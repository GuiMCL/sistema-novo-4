"""Pipeline do WhatsApp — suporte a múltiplas instâncias.

O webhook recebe eventos de N instâncias Evolution. Cada evento carrega o
nome da instância no campo `instance`. O pipeline identifica a instância,
atribui o contato a ela e roteia a resposta pela instância correta.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import agente, auth, db, evolution, ia
from .config import settings
from .phone import mesmo_numero

log = logging.getLogger("whatsapp")

router = APIRouter()

DEBOUNCE_S = 6.0

_buffers: dict[str, list[str]] = {}
_timers: dict[str, asyncio.Task] = {}

# Instância associada a cada remoteJid (cache do debounce)
_instance_map: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Webhook (multi-instância)
# ---------------------------------------------------------------------------


@router.post("/webhook/whatsapp/receberMensagem")
async def receber_mensagem(request: Request, token: str = ""):
    if not secrets.compare_digest(token, settings.webhook_token):
        return JSONResponse({"erro": "token inválido"}, status_code=403)
    body = await request.json()
    asyncio.create_task(_processar_evento(body))
    return {"ok": True}


async def _processar_evento(body: dict) -> None:
    try:
        data = body.get("data") or {}
        key = data.get("key") or {}
        remote_jid = key.get("remoteJid") or ""
        if not remote_jid or key.get("fromMe"):
            return

        # Identifica a instância que recebeu a mensagem
        instancia_nome = body.get("instance", "")
        instancia_db = None
        if instancia_nome:
            instancia_db = db.get_instancia_por_nome(instancia_nome)
        instancia_id = instancia_db.id if instancia_db else None

        texto = await _extrair_texto(data)
        if texto is None:
            return

        # --- ROTEAMENTO: sessão do tipo "cotacao" → módulo de cotações ---
        if instancia_db and instancia_db.tipo == "cotacao":
            from .phone import normalizar as _norm
            import phonenumbers as _pn
            tel_clean = _norm(remote_jid) or remote_jid
            tel_digits = re.sub(r"\D", "", remote_jid.split("@")[0])
            log.info("Cotação webhook — número recebido: %s (E164: %s)", remote_jid, tel_clean)
            supplier_id = None
            supplier_nome = ""
            for f in db.listar_quote_fornecedores(apenas_ativos=True):
                f_tel = _norm(f.whatsapp) or f.whatsapp
                if not f_tel:
                    continue
                # Tenta match exato primeiro
                if tel_clean == f_tel:
                    supplier_id = f.id
                    supplier_nome = f.nome
                    log.info("Cotação — matched exato: #%s %s", f.id, f.nome)
                    break
                # Tenta match fuzzy via phonenumbers
                try:
                    a = _pn.parse("+" + tel_digits, None)
                    b = _pn.parse("+" + re.sub(r"\D", "", f.whatsapp.split("@")[0]), None)
                    if _pn.is_valid_number(a) and _pn.is_valid_number(b):
                        if _pn.phonenumberutil.number_type(a) == _pn.phonenumberutil.number_type(b):
                            na = _pn.format_number(a, _pn.PhoneNumberFormat.E164)
                            nb = _pn.format_number(b, _pn.PhoneNumberFormat.E164)
                            if na == nb:
                                supplier_id = f.id
                                supplier_nome = f.nome
                                log.info("Cotação — matched fuzzy: #%s %s (E164: %s)", f.id, f.nome, na)
                                break
                except Exception:
                    pass
                # Match por dígitos (últimos 8+)
                f_digits = re.sub(r"\D", "", f.whatsapp.split("@")[0])
                if len(tel_digits) >= 10 and len(f_digits) >= 10:
                    if tel_digits[-8:] == f_digits[-8:] or tel_digits[-9:] == f_digits[-9:]:
                        supplier_id = f.id
                        supplier_nome = f.nome
                        log.info("Cotação — matched dígitos: #%s %s", f.id, f.nome)
                        break
            if not supplier_id:
                log.info("Cotação — nenhum fornecedor matched para %s (digits: %s)", tel_clean, tel_digits)
                return
            req_id = db.find_open_quote_for_supplier(supplier_id)
            if not req_id:
                log.info("Cotação — nenhum pedido aberto para supplier #%s", supplier_id)
                return
            db.registrar_mensagem_cotacao(
                request_id=req_id, supplier_id=supplier_id,
                mensagem=texto, tipo="recebida", remote_jid=remote_jid,
            )
            r = db.get_quote_request(req_id)
            if r and r.status == "enviada":
                db.atualizar_quote_request(req_id, status="aguardando")
            _extrair_preco_cotacao(texto, req_id, supplier_id)
            log.info("Cotação recebida — request #%s supplier #%s (%s)", req_id, supplier_id, supplier_nome)
            return

        texto = _sanitizar_entrada(texto)

        db.upsert_cliente(remote_jid, data.get("pushName") or "")

        # Marca instância na memória do contato (cache)
        _instance_map[remote_jid] = instancia_nome

        dono = mesmo_numero(remote_jid, db.get_config().telefone_dono)
        if not dono and db.cliente_pausado(remote_jid):
            agente.registrar_na_memoria(remote_jid, texto, "cliente")
            log.info("Bot pausado p/ %s — mensagem só gravada", remote_jid)
            return

        await evolution.marcar_como_lida(
            remote_jid, False, key.get("id") or "", instancia=instancia_nome or None
        )
        _agendar_lote(remote_jid, texto)
    except Exception:
        log.exception("Erro processando evento do webhook")


_RE_MARCADOR_FORJADO = re.compile(r"\[\s*tarefa\s*interna[^\]]*\]", re.IGNORECASE)


def _sanitizar_entrada(texto: str) -> str:
    return _RE_MARCADOR_FORJADO.sub("[conteúdo removido]", texto)


async def _extrair_texto(data: dict) -> str | None:
    tipo = data.get("messageType") or ""
    msg = data.get("message") or {}
    if tipo == "conversation":
        return msg.get("conversation") or None
    if tipo == "extendedTextMessage":
        return (msg.get("extendedTextMessage") or {}).get("text") or None
    if tipo in ("audioMessage", "imageMessage"):
        b64, mime = await _base64_da_mensagem(data, tipo)
        if not b64:
            return None
        if tipo == "audioMessage":
            transcricao = await ia.transcrever_audio(b64, mime or "audio/ogg")
            return f"[Áudio transcrito] {transcricao}" if transcricao else None
        legenda = (msg.get("imageMessage") or {}).get("caption") or ""
        descricao = await ia.descrever_imagem(b64, mime or "image/jpeg", legenda)
        return f"[Imagem enviada pelo cliente] {descricao}\nLegenda: {legenda}" if legenda else f"[Imagem enviada pelo cliente] {descricao}"
    return None


async def _base64_da_mensagem(data: dict, tipo: str) -> tuple[str | None, str | None]:
    msg = data.get("message") or {}
    detalhe = msg.get(tipo) or {}
    b64 = msg.get("base64")
    mime = detalhe.get("mimetype")
    if b64:
        return b64, mime
    midia = await evolution.obter_midia_base64(
        (data.get("key") or {}).get("id") or "",
        instancia=_instance_map.get(data.get("key", {}).get("remoteJid", "")),
    )
    return midia.get("base64"), midia.get("mimetype") or mime


# ---------------------------------------------------------------------------
# Debounce por contato
# ---------------------------------------------------------------------------


def _agendar_lote(remote_jid: str, texto: str) -> None:
    _buffers.setdefault(remote_jid, []).append(texto)
    timer = _timers.get(remote_jid)
    if timer and not timer.done():
        timer.cancel()
    _timers[remote_jid] = asyncio.create_task(_esperar_e_responder(remote_jid))


async def _esperar_e_responder(remote_jid: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_S)
    except asyncio.CancelledError:
        return

    mensagens = _buffers.pop(remote_jid, [])
    _timers.pop(remote_jid, None)
    if not mensagens:
        return

    lote = "[quebrar]".join(mensagens)
    try:
        await _responder_contato(remote_jid, lote)
    except ia.IANaoConfigurada as e:
        log.warning("%s", e)
    except Exception:
        log.exception("Erro respondendo %s", remote_jid)


async def _responder_contato(remote_jid: str, mensagem: str) -> None:
    token = auth.solicitante_ctx.set(remote_jid)
    try:
        resposta = await agente.responder(remote_jid, mensagem)
    finally:
        auth.solicitante_ctx.reset(token)

    instancia = _instance_map.get(remote_jid)
    await enviar_bolhas(remote_jid.split("@")[0], resposta, instancia=instancia)


def get_instancia_do_contato(telefone: str) -> str | None:
    """Resolve a instância Evolution de um contato pelo telefone."""
    from .phone import normalizar
    norm = normalizar(telefone) or telefone
    for jid, inst in _instance_map.items():
        if norm in jid or jid.startswith(norm):
            return inst
    return None


async def enviar_bolhas(numero: str, resposta: str, instancia: str | None = None) -> None:
    """Divide em bolhas e envia — usado pelo pipeline reativo e ações proativas."""
    for bolha in dividir_bolhas(resposta):
        segundos = min(0.4 + len(bolha) * 0.02, 4.0) + random.random() * 0.7
        await evolution.enviar_texto(
            numero, bolha, digitando_ms=int(segundos * 1000), instancia=instancia
        )


def contato_ocupado(telefone: str) -> bool:
    pendentes = set(_buffers) | set(_timers)
    return any(mesmo_numero(telefone, jid) for jid in pendentes)


# ---------------------------------------------------------------------------
# Divisão em bolhas
# ---------------------------------------------------------------------------


def dividir_bolhas(texto: str) -> list[str]:
    normalizado = re.sub(r"\[quebra\]", "[quebrar]", texto or "", flags=re.IGNORECASE)
    normalizado = re.sub(r"\n+", "[quebrar]", normalizado)
    bolhas = [p.strip() for p in normalizado.split("[quebrar]") if p.strip()]
    sem_repeticao: list[str] = []
    for b in bolhas:
        if not sem_repeticao or sem_repeticao[-1] != b:
            sem_repeticao.append(b)
    return sem_repeticao


_RE_PRECO = re.compile(
    r'(?:R\s*\$|R\$)?\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})|\d+(?:,\d{2}))',
    re.IGNORECASE,
)


def _extrair_preco_cotacao(texto: str, request_id: int, supplier_id: int) -> None:
    import json
    matches = _RE_PRECO.findall(texto)
    if not matches:
        return
    with db._session() as s:
        itens = list(
            s.exec(
                db.select(db.QuoteRequestItem)
                .where(db.QuoteRequestItem.request_id == request_id)
            ).all()
        )
    if not itens:
        return
    count = 0
    for i, raw in enumerate(matches):
        if i >= len(itens):
            break
        valor_str = raw.replace('.', '').replace(',', '.')
        try:
            valor = float(valor_str)
        except ValueError:
            continue
        if valor <= 0 or valor > 999999:
            continue
        db.salvar_preco_cotacao(
            request_id=request_id, supplier_id=supplier_id,
            part_id=itens[i].part_id, valor=valor,
        )
        count += 1
    log.info("Preços extraídos: %d valor(es) — request #%s supplier #%s",
             count, request_id, supplier_id)
