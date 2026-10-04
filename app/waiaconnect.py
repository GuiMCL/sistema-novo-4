"""Cliente HTTP para a WaiaConnect API (Meta WhatsApp Business Provider).

Funcionalidades:
- Envio de texto livre (dentro da janela de 24h)
- Envio de template aprovado (fora da janela de 24h)
- Controle de janela de 24h via webhook inbound
- Idempotency-Key por requisição
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from .config import settings
from .phone import normalizar

log = logging.getLogger("waiaconnect")

# Janela de 24h em milissegundos
JANELA_24H_MS = 24 * 60 * 60 * 1000

# Cache em memória da última mensagem inbound por telefone (E.164)
# Em produção com múltiplas instâncias, usar Redis/DB
_ultima_msg_inbound: dict[str, float] = {}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.waiaconnect_base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {settings.waiaconnect_api_key}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


def _async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.waiaconnect_base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {settings.waiaconnect_api_key}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )


def _gerar_idempotency_key() -> str:
    return uuid.uuid4().hex


def _normalizar_para_envio(telefone: str) -> str:
    """Normaliza para E.164 sem '+' (ex.: 5545999990000)."""
    n = normalizar(telefone)
    if n.startswith("+"):
        n = n[1:]
    return n


# ---------------------------------------------------------------------------
# Controle de janela de 24h
# ---------------------------------------------------------------------------


def registrar_mensagem_inbound(telefone: str) -> None:
    """Marca que o cliente enviou mensagem (abre/renova janela de 24h)."""
    telefone_norm = _normalizar_para_envio(telefone)
    if telefone_norm:
        _ultima_msg_inbound[telefone_norm] = time.time() * 1000  # ms


def obter_ultima_msg_inbound(telefone: str) -> float | None:
    """Retorna timestamp (ms) da última mensagem inbound ou None."""
    telefone_norm = _normalizar_para_envio(telefone)
    return _ultima_msg_inbound.get(telefone_norm)


def dentro_da_janela_24h(telefone: str) -> bool:
    """True se a última mensagem do cliente foi há menos de 24h."""
    ultima = obter_ultima_msg_inbound(telefone)
    if ultima is None:
        return False
    return (time.time() * 1000) - ultima < JANELA_24H_MS


def limpar_cache_janela() -> None:
    """Limpa entradas antigas (>48h) para não vazar memória."""
    agora = time.time() * 1000
    limite = 48 * 60 * 60 * 1000
    for tel, ts in list(_ultima_msg_inbound.items()):
        if agora - ts > limite:
            del _ultima_msg_inbound[tel]


# ---------------------------------------------------------------------------
# Envio de mensagens
# ---------------------------------------------------------------------------


async def enviar_texto(
    numero: str,
    texto: str,
    connection_id: str | None = None,
) -> dict[str, Any]:
    """Envia mensagem de texto livre (type: text).

    Só funciona dentro da janela de 24h da última mensagem do cliente.
    Fora da janela, a Meta retorna erro 131047.
    """
    connection_id = connection_id or settings.waiaconnect_connection_id
    if not connection_id:
        raise ValueError("connection_id não configurado (WAIACONNECT_CONNECTION_ID)")

    telefone = _normalizar_para_envio(numero)
    payload = {
        "connectionId": connection_id,
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }

    async with _async_client() as c:
        r = await c.post(
            "/v1/messages",
            json=payload,
            headers={"Idempotency-Key": _gerar_idempotency_key()},
        )

    if r.status_code >= 400:
        erro = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        log.error("Erro enviando texto p/ %s: %s %s", telefone, r.status_code, erro)
        r.raise_for_status()

    return r.json()


async def enviar_template(
    numero: str,
    template_name: str,
    language: str = "pt_BR",
    components: list[dict] | None = None,
    connection_id: str | None = None,
) -> dict[str, Any]:
    """Envia template aprovado no Meta (type: template).

    Obrigatório fora da janela de 24h.
    Template deve estar com status APPROVED no Meta.
    """
    connection_id = connection_id or settings.waiaconnect_connection_id
    if not connection_id:
        raise ValueError("connection_id não configurado (WAIACONNECT_CONNECTION_ID)")

    telefone = _normalizar_para_envio(numero)
    payload = {
        "connectionId": connection_id,
        "to": telefone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": language,
            "components": components or [],
        },
    }

    async with _async_client() as c:
        r = await c.post(
            "/v1/messages",
            json=payload,
            headers={"Idempotency-Key": _gerar_idempotency_key()},
        )

    if r.status_code >= 400:
        erro = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        log.error("Erro enviando template p/ %s: %s %s", telefone, r.status_code, erro)
        r.raise_for_status()

    return r.json()


async def enviar_inteligente(
    numero: str,
    texto: str,
    connection_id: str | None = None,
    template_fallback: str | None = None,
    language: str = "pt_BR",
    components: list[dict] | None = None,
) -> dict[str, Any]:
    """Envia mensagem decidindo automaticamente entre texto livre ou template.

    - Se dentro da janela de 24h: envia texto livre (type: text)
    - Se fora da janela de 24h: envia template (type: template) — requer template_fallback
    """
    telefone = _normalizar_para_envio(numero)

    if dentro_da_janela_24h(telefone):
        return await enviar_texto(numero, texto, connection_id)

    # Fora da janela — precisa de template aprovado
    if not template_fallback:
        raise ValueError(
            f"Fora da janela de 24h para {telefone}. "
            "Forneça template_fallback (nome do template aprovado no Meta)."
        )

    return await enviar_template(
        numero,
        template_name=template_fallback,
        language=language,
        components=components,
        connection_id=connection_id,
    )


# ---------------------------------------------------------------------------
# Webhook processing (receber mensagens do cliente)
# ---------------------------------------------------------------------------


async def processar_webhook(body: dict) -> dict[str, Any] | None:
    """Processa webhook da WaiaConnect e extrai mensagem de cliente.

    O envelope da WaiaConnect é versionado e NÃO é o payload cru da Meta:

        {id, type, version, createdAt, connection, sequence,
         data: {message: {...}, contacts: [{wa_id, profile: {name}}]}}

    Só `type == "message.received"` entra no agente. `message.echo` é o dono
    mandando do celular (from = seu número) e `message.status` é recibo de
    entrega — nenhum dos dois pode virar conversa de cliente.

    Retorna dict com: remote_jid, texto, midia, push_name, message_id, timestamp
    ou None se não for mensagem de cliente processável.
    """
    try:
        for evento in _eventos_do_body(body):
            tipo = evento.get("type")
            if tipo != EVENTO_MENSAGEM_RECEBIDA:
                _logar_evento_ignorado(tipo, evento.get("id"))
                continue

            resultado = _mensagem_de_cliente(evento)
            if resultado is None:
                continue

            texto = resultado["texto"]
            if not texto:
                texto = await _texto_da_midia(resultado["midia"])

            registrar_mensagem_inbound(resultado["remote_jid"])
            resultado["texto"] = texto
            log.info(
                "Mensagem de %s: %r", resultado["remote_jid"], texto[:120]
            )
            return resultado

        log.info("Webhook WaiaConnect: nenhum evento %s", EVENTO_MENSAGEM_RECEBIDA)

    except Exception:
        log.exception("Erro processando webhook WaiaConnect")

    return None


# ---------------------------------------------------------------------------
# Mídia: download via proxy + transcrição/visão
# ---------------------------------------------------------------------------

# Teto de download. A Meta aceita áudio de até 16 MB e imagem de até 5 MB;
# acima disso a transcrição estoura memória sem trazer informação útil.
MEDIA_MAX_BYTES = 16 * 1024 * 1024


async def baixar_midia(media_id: str) -> tuple[bytes, str]:
    """Baixa a mídia em GET /v1/media/:id → (bytes, mimetype).

    A WaiaConnect faz proxy dos bytes: a Meta manda id, não arquivo. Um id que
    não pertence à sua conta (ou que expirou na Meta) responde 404.
    """
    if not media_id:
        raise ValueError("media_id vazio")

    async with _async_client() as c:
        r = await c.get("/v1/media/" + quote(media_id, safe=""))

    if r.status_code == 404:
        raise RuntimeError(f"mídia {media_id} não encontrada (404) ou expirada na Meta")
    if r.status_code >= 400:
        raise RuntimeError(f"download da mídia falhou (HTTP {r.status_code}): {r.text[:200]}")
    if len(r.content) > MEDIA_MAX_BYTES:
        raise RuntimeError(
            f"mídia de {len(r.content)} bytes excede o limite de {MEDIA_MAX_BYTES}"
        )

    return r.content, (r.headers.get("content-type") or "").split(";")[0].strip()


async def _texto_da_midia(midia: dict[str, Any] | None) -> str:
    """Converte a mídia em texto: áudio transcreve, imagem descreve.

    Nunca levanta. Falhar aqui não pode custar a mensagem inteira — o cliente
    ficaria sem resposta e o painel sem registro. O marcador garante que a
    mensagem ainda chegue ao atendente.
    """
    if midia is None:
        return ""

    tipo = midia["tipo"]
    legenda = midia["legenda"]
    rotulo = "áudio" if tipo == "audio" else tipo

    if tipo in ("audio", "image"):
        try:
            from . import ia

            conteudo, mime = await baixar_midia(midia["id"])
            b64 = base64.b64encode(conteudo).decode()

            if tipo == "audio":
                transcricao = await ia.transcrever_audio(b64, mime or "audio/ogg")
                return _com_legenda(f"[Áudio do cliente] {transcricao}", legenda)

            descricao = await ia.descrever_imagem(b64, mime or "image/jpeg", legenda)
            return _com_legenda(f"[Imagem do cliente] {descricao}", legenda)

        except Exception:
            log.exception("Falha processando %s %s", rotulo, midia["id"])
            return f"[{rotulo.capitalize()} recebido — não foi possível processar]"

    # Tipos sem processamento de IA: mantém o registro visível no painel.
    if tipo == "document":
        nome = midia["filename"] or "sem nome"
        return _com_legenda(f"[Documento do cliente: {nome}]", legenda)
    if tipo == "sticker":
        return "[Figurinha do cliente]"
    return f"[{tipo.capitalize()} do cliente]"


def _com_legenda(texto: str, legenda: str) -> str:
    return f"{texto}\nLegenda: {legenda}" if legenda else texto


# Evento cujo data.message é uma mensagem enviada por cliente.
EVENTO_MENSAGEM_RECEBIDA = "message.received"

# Os 21 eventos da WaiaConnect que NÃO são mensagem de cliente. Sem esta lista
# o parser aceitaria qualquer coisa; `history.batch` em particular traria meses
# de conversa antiga e faria o bot responder mensagens do passado.
_EVENTOS_IGNORADOS = frozenset(
    {
        "message.echo",
        "message.status",
        "connection.created",
        "connection.status_changed",
        "connection.usage_threshold_reached",
        "usage.threshold_reached",
        "history.synced",
        "history.batch",
        "history.completed",
        "history.dropped",
        "template.status_changed",
        "template.draft_submitted",
        "account.trial_warning",
        "account.trial_expired",
        "subscription.activated",
        "subscription.cancelled",
        "billing.payment_failed",
        "billing.payment_recovered",
        "billing.amount_updated",
        "webhook.test",
    }
)


def _logar_evento_ignorado(tipo: Any, evento_id: Any) -> None:
    if tipo == "message.echo":
        log.info("Ignorando message.echo (dono mandou do celular) — evento %s", evento_id)
    elif tipo in _EVENTOS_IGNORADOS:
        log.info("Evento %s ignorado — evento %s", tipo, evento_id)
    else:
        log.warning("Evento desconhecido: type=%r — evento %s", tipo, evento_id)


def _eventos_do_body(body: Any) -> list[dict]:
    """Envelopes do body: array de envelopes ou um envelope único.

    Não há tentativa de ler o payload cru da Meta: a WaiaConnect Embrulha
    (docs: "the envelope is not Meta's raw payload") e o envelope Meta traz
    várias mensagens por evento, o que não cabe no retorno único daqui.
    """
    if isinstance(body, list):
        return [e for e in body if isinstance(e, dict)]
    if isinstance(body, dict):
        return [body]
    return []


def _mensagem_de_cliente(evento: dict) -> dict[str, Any] | None:
    """Extrai a mensagem do data.message de um envelope message.received."""
    data = evento.get("data")
    if not isinstance(data, dict):
        log.info("Evento %s sem data utilizável — chaves=%s", evento.get("id"), sorted(evento))
        return None

    msg = data.get("message")
    if not isinstance(msg, dict):
        log.info("Evento %s sem data.message — chaves=%s", evento.get("id"), sorted(data))
        return None

    remote_jid = msg.get("from") or ""
    if not remote_jid:
        log.info("Evento %s sem data.message.from", evento.get("id"))
        return None

    texto = _extrair_texto_waiaconnect(msg)
    midia = _midia_da_mensagem(msg)

    if not texto and midia is None:
        log.info(
            "Mensagem %s sem texto nem mídia — tipo=%s chaves=%s",
            evento.get("id"),
            msg.get("type"),
            sorted(msg),
        )
        return None

    return {
        "remote_jid": remote_jid,
        "texto": texto,
        "midia": midia,
        "push_name": _nome_do_contato(data, remote_jid),
        "message_id": msg.get("id") or evento.get("id"),
        "timestamp": msg.get("timestamp") or evento.get("createdAt"),
    }


def _nome_do_contato(data: dict, remote_jid: str) -> str:
    """Nome do pushName via data.contacts[].profile.name."""
    for c in data.get("contacts") or []:
        if isinstance(c, dict) and c.get("wa_id") == remote_jid:
            return (c.get("profile") or {}).get("name") or ""
    return ""


# Tipos de mídia que a Meta entrega dentro de data.message.<tipo>.
# `audio` costuma vir com voice=true (nota de voz) — mesmo tipo, mesmo
# tratamento. `sticker` não tem id de mídia.
_TIPOS_DE_MIDIA = ("audio", "image", "video", "document", "sticker")


def _midia_da_mensagem(msg: dict) -> dict[str, Any] | None:
    """Descritor da mídia de data.message, ou None se a mensagem não é mídia."""
    tipo = msg.get("type")
    if tipo not in _TIPOS_DE_MIDIA:
        return None

    detalhe = msg.get(tipo)
    if not isinstance(detalhe, dict):
        return None

    return {
        "tipo": tipo,
        "id": detalhe.get("id") or "",
        "mime": detalhe.get("mime_type") or "",
        "legenda": detalhe.get("caption") or "",
        "voice": bool(detalhe.get("voice")),
        "filename": detalhe.get("filename") or "",
    }


def _extrair_texto_waiaconnect(data: dict) -> str | None:
    """Extrai texto da mensagem conforme formato WaiaConnect."""
    # Texto simples — `text` como objeto (formato de envio) ou como string
    texto = data.get("text")
    if isinstance(texto, dict):
        body = texto.get("body")
        return body if isinstance(body, str) and body else None
    if isinstance(texto, str) and texto:
        return texto

    # Tipo messageType estilo Evolution
    msg_type = data.get("type") or data.get("messageType")
    message = data.get("message") or data

    if msg_type in ("text", "conversation", "extendedTextMessage"):
        if isinstance(message, dict):
            return (
                message.get("conversation")
                or message.get("text", {}).get("body")
                or message.get("extendedTextMessage", {}).get("text")
            )

    # Interativo (botão, lista, reply)
    if msg_type in ("interactive", "buttonResponse", "listResponse"):
        interactive = message.get("interactive") or message
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("title")
        if interactive.get("type") == "list_reply":
            return interactive.get("list_reply", {}).get("title")
        if interactive.get("type") == "nfm_reply":
            return interactive.get("nfm_reply", {}).get("body")

    # Localização
    if msg_type == "location":
        loc = message.get("location") or message
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat and lng:
            return f"[Localização] {lat}, {lng}"

    # Mídia não é tratada aqui — o id vai em `midia` e `_texto_da_midia` baixa,
    # transcreve ou descreve (exige I/O, por isso é outra função).
    return None


# ---------------------------------------------------------------------------
# Health check / status
# ---------------------------------------------------------------------------


async def verificar_conexao(connection_id: str | None = None) -> dict[str, Any]:
    """Verifica status da conexão na WaiaConnect."""
    connection_id = connection_id or settings.waiaconnect_connection_id
    if not connection_id:
        return {"erro": "connection_id não configurado"}

    async with _async_client() as c:
        r = await c.get(f"/v1/connections/{connection_id}")
        if r.status_code >= 400:
            return {"erro": f"HTTP {r.status_code}", "detalhes": r.text}
        return r.json()


async def listar_templates(connection_id: str | None = None) -> list[dict]:
    """Lista templates disponíveis (aprovados) na WaiaConnect."""
    connection_id = connection_id or settings.waiaconnect_connection_id
    if not connection_id:
        return []

    async with _async_client() as c:
        r = await c.get(f"/v1/templates?connectionId={connection_id}")
        if r.status_code >= 400:
            log.warning("Erro listando templates: %s", r.text)
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("templates", [])


# ---------------------------------------------------------------------------
# Versão síncrona para uso em notificações (threadpool)
# ---------------------------------------------------------------------------


def enviar_texto_sync(numero: str, texto: str, connection_id: str | None = None) -> dict[str, Any]:
    """Versão síncrona de enviar_texto para uso em threadpool/notificações."""
    connection_id = connection_id or settings.waiaconnect_connection_id
    if not connection_id:
        raise ValueError("connection_id não configurado (WAIACONNECT_CONNECTION_ID)")

    telefone = _normalizar_para_envio(numero)
    payload = {
        "connectionId": connection_id,
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }

    with _client() as c:
        r = c.post(
            "/v1/messages",
            json=payload,
            headers={"Idempotency-Key": _gerar_idempotency_key()},
        )

    if r.status_code >= 400:
        erro = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        log.error("Erro enviando texto sync p/ %s: %s %s", telefone, r.status_code, erro)
        r.raise_for_status()

    return r.json()