"""Pipeline do WhatsApp via WaiaConnect (Meta WhatsApp Business Provider).

Substitui o pipeline Evolution API. Principais diferenças:
- WaiaConnect usa connectionId + telefone E.164 sem '+' (ex.: 5545999990000)
- Janela de 24h: texto livre só dentro de 24h da última msg do cliente; fora, template aprovado
- Webhook único (não multi-instância como Evolution)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import random
import re
import secrets
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import agente, auth, db, ia
from .config import settings
from .phone import mesmo_numero
from .waiaconnect import (
    enviar_inteligente,
    enviar_texto,
    enviar_template,
    processar_webhook,
    registrar_mensagem_inbound,
)

log = logging.getLogger("whatsapp")

router = APIRouter()

DEBOUNCE_S = 6.0

_buffers: dict[str, list[str]] = {}
_timers: dict[str, asyncio.Task] = {}

# Template padrão para fallback fora da janela de 24h
# Configure no painel da WaiaConnect/Meta e coloque o nome exato aqui
TEMPLATE_FALLBACK_PADRAO = "fora_horario_atendimento"

# Falhas de envio que NÃO podem derrubar o restante da resposta: falta de
# template fora da janela (ValueError) e rejeição do Meta na hora de enviar
# (HTTPStatusError de raise_for_status — template inexistente, não aprovado,
# fora da janela mesmo assim). Uma bolha falha; as próximas ainda saem.
_ERROS_DE_ENVIO = (ValueError, httpx.HTTPStatusError, httpx.HTTPError)

# Tolerância de replay da assinatura do webhook (spec WaiaConnect: 5 min)
ASSINATURA_TOLERANCIA_S = 300


# ---------------------------------------------------------------------------
# Webhook WaiaConnect
# ---------------------------------------------------------------------------


def _verificar_token_estatico(request: Request) -> bool:
    """Valida o header X-Connect-Token (token fixo definido no painel WaiaConnect).

    Sem WAIACONNECT_CONNECT_TOKEN configurado, o header é ignorado — nunca
    comparado contra string vazia, que aceitaria qualquer requisição.
    """
    esperado = settings.waiaconnect_connect_token
    if not esperado:
        return False
    recebido = request.headers.get("x-connect-token", "")
    return bool(recebido) and secrets.compare_digest(recebido, esperado)


def _verificar_assinatura_waiaconnect(request: Request, body: bytes) -> bool:
    """Valida X-Connect-Signature-256 conforme a spec da WaiaConnect.

    O header é `sha256=HMAC_SHA256(secret, "<X-Connect-Timestamp>.<raw body>")`.
    O timestamp está dentro do HMAC, então uma entrega capturada não pode ser
    replayada; entregas com mais de 5 min são recusadas.

    O HMAC é calculado sobre os bytes crus — reserializar o JSON muda o hash.
    """
    secret = settings.waiaconnect_webhook_secret
    if not secret:
        return False

    timestamp = request.headers.get("x-connect-timestamp", "")
    assinatura = request.headers.get("x-connect-signature-256", "")
    if not timestamp or not assinatura:
        return False

    try:
        idade = abs(time.time() - int(timestamp))
    except ValueError:
        log.warning("X-Connect-Timestamp inválido: %r", timestamp)
        return False

    if idade > ASSINATURA_TOLERANCIA_S:
        log.warning(
            "Entrega WaiaConnect recusada: %ds de idade (tolerância %ds)",
            int(idade),
            ASSINATURA_TOLERANCIA_S,
        )
        return False

    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return secrets.compare_digest(f"sha256={mac.hexdigest()}", assinatura)


@router.post("/webhook/waiaconnect")
async def receber_mensagem_waiaconnect(request: Request, token: str = ""):
    """Endpoint do webhook da WaiaConnect.

    Aceita autenticação por:
    1. Header `X-Connect-Token` (token fixo, padrão do painel)
    2. Query param `token` (compatibilidade)
    3. Header HMAC signature (padrão WaiaConnect/Meta)
    """
    body = await request.body()

    autenticado = (
        _verificar_token_estatico(request)
        or _verificar_assinatura_waiaconnect(request, body)
        or secrets.compare_digest(token, settings.webhook_token)
    )
    if not autenticado:
        return JSONResponse({"erro": "token inválido"}, status_code=403)
    
    import json
    try:
        body_json = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"erro": "body inválido"}, status_code=400)
    
    asyncio.create_task(_processar_evento_waiaconnect(body_json))
    return {"ok": True}


async def _processar_evento_waiaconnect(body: dict) -> None:
    try:
        resultado = await processar_webhook(body)
        if not resultado:
            return

        remote_jid = resultado["remote_jid"]
        texto = resultado["texto"]
        push_name = resultado["push_name"]
        message_id = resultado["message_id"]

        # Normaliza telefone para E.164 (chave do banco)
        from .phone import normalizar
        telefone_norm = normalizar(remote_jid)

        # Upsert cliente (aproveita pushName)
        db.upsert_cliente(telefone_norm, push_name)

        # Se for o dono, não pausa
        dono = mesmo_numero(telefone_norm, db.get_config().telefone_dono)
        if not dono and db.cliente_pausado(telefone_norm):
            agente.registrar_na_memoria(telefone_norm, texto, "cliente")
            log.info("Bot pausado p/ %s — mensagem só gravada", telefone_norm)
            return

        # Sanitiza
        texto = _sanitizar_entrada(texto)

        # Agenda resposta com debounce
        _agendar_lote(telefone_norm, texto)

    except Exception:
        log.exception("Erro processando evento do webhook WaiaConnect")


_RE_MARCADOR_FORJADO = re.compile(r"\[\s*tarefa\s*interna[^\]]*\]", re.IGNORECASE)


def _sanitizar_entrada(texto: str) -> str:
    return _RE_MARCADOR_FORJADO.sub("[conteúdo removido]", texto)


# ---------------------------------------------------------------------------
# Debounce por contato (igual ao Evolution)
# ---------------------------------------------------------------------------


def _agendar_lote(remote_jid: str, texto: str) -> None:
    _buffers.setdefault(remote_jid, [])
    # Entrega duplicada do webhook (retry/duplicidade) manda a MESMA mensagem
    # duas vezes — dentro da janela de debounce a cópia exata é ignorada para
    # não gerar resposta duplicada nem agendamento duplicado.
    if _buffers[remote_jid] and _buffers[remote_jid][-1] == texto:
        return
    _buffers[remote_jid].append(texto)
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

    await enviar_bolhas(remote_jid, resposta)


def contato_ocupado(telefone: str) -> bool:
    pendentes = set(_buffers) | set(_timers)
    return any(mesmo_numero(telefone, jid) for jid in pendentes)


# ---------------------------------------------------------------------------
# Envio de mensagens via WaiaConnect
# ---------------------------------------------------------------------------


async def enviar_bolhas(numero: str, resposta: str) -> bool:
    """Divide em bolhas e envia via WaiaConnect (texto ou template se >24h).

    Devolve True se pelo menos uma bolha foi aceita. Quem depende da entrega
    checar isso (ex.: o lembrete de confirmação, que só marca
    `aguardando_confirmacao` se o cliente realmente recebeu o pedido).
    """
    aceitou = False
    for bolha in dividir_bolhas(resposta):
        segundos = min(0.4 + len(bolha) * 0.02, 4.0) + random.random() * 0.7
        try:
            await enviar_inteligente(
                numero,
                bolha,
                template_fallback=TEMPLATE_FALLBACK_PADRAO,
                language="pt_BR",
            )
            aceitou = True
        except _ERROS_DE_ENVIO as e:
            # Fora da janela sem template, ou template rejeitado pelo Meta: a
            # bolha se perde, mas o resto da resposta continua indo. Sem isto o
            # HTTPStatusError de `raise_for_status` derrubava o laço inteiro e o
            # cliente recebia só a primeira parte.
            log.warning("Não conseguiu enviar para %s: %s", numero, e)
        await asyncio.sleep(segundos / 1000)  # converte ms para segundos
    return aceitou


async def enviar_texto_simples(numero: str, texto: str) -> None:
    """Envia uma única bolha de texto (usado por ações proativas/tarefas)."""
    try:
        await enviar_inteligente(
            numero,
            texto,
            template_fallback=TEMPLATE_FALLBACK_PADRAO,
            language="pt_BR",
        )
    except _ERROS_DE_ENVIO as e:
        log.warning("Não conseguiu enviar texto simples para %s: %s", numero, e)


# ---------------------------------------------------------------------------
# Divisão em bolhas (igual ao Evolution)
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


# ---------------------------------------------------------------------------
# Compatibilidade: funções que o resto do sistema espera
# ---------------------------------------------------------------------------


def get_instancia_do_contato(telefone: str) -> str | None:
    """WaiaConnect não usa multi-instância como Evolution. Retorna connection_id fixo."""
    return settings.waiaconnect_connection_id or None