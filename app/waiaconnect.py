"""Cliente HTTP para a WaiaConnect API (Meta WhatsApp Business Provider).

Funcionalidades:
- Envio de texto livre (dentro da janela de 24h)
- Envio de template aprovado (fora da janela de 24h)
- Controle de janela de 24h via webhook inbound
- Idempotency-Key por requisição
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

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
    """Processa webhook da WaiaConnect e extrai mensagem do cliente.

    Retorna dict com: remote_jid, texto, push_name, message_id, timestamp
    ou None se não for mensagem de cliente processável.
    """
    try:
        eventos = _achatar_eventos(body)
        log.info("Webhook WaiaConnect: %d evento(s) — chaves=%s", len(eventos), _resumo(body))

        for evento in eventos:
            resultado = _mensagem_de_evento(evento)
            if not resultado:
                continue
            registrar_mensagem_inbound(resultado["remote_jid"])
            log.info(
                "Mensagem de %s: %r", resultado["remote_jid"], resultado["texto"][:120]
            )
            return resultado

        log.info("Webhook WaiaConnect: nenhum evento de cliente reconhecido")

    except Exception:
        log.exception("Erro processando webhook WaiaConnect")

    return None


def _resumo(body: Any) -> list[str]:
    """Chaves do topo do body — suficiente para diagnosticar formato novo."""
    return sorted(body) if isinstance(body, dict) else [type(body).__name__]


def _achatar_eventos(body: Any) -> list[dict]:
    """Achata o body numa lista de eventos.

    Aceita evento único, lista de eventos e o envelope do WhatsApp Cloud API
    (entry[].changes[].value), que é o formato de um provider Meta. Só entram
    `changes` com field="messages"; `statuses` (entrega/sentido) e os demais
    fields não são mensagens de cliente.
    """
    if isinstance(body, list):
        return [e for e in body if isinstance(e, dict)]

    if not isinstance(body, dict):
        return []

    entry = body.get("entry")
    if not isinstance(entry, list):
        return [body]

    achatados: list[dict] = []
    for e in entry:
        if not isinstance(e, dict):
            continue
        changes = e.get("changes")
        if not isinstance(changes, list):
            achatados.append(e)
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if isinstance(value, dict):
                achatados.append(value)
    return achatados


_DIRECOES_INBOUND = ("inbound", "message_received", "messages.upsert")


def _mensagem_de_evento(data: dict) -> dict[str, Any] | None:
    """Extrai a mensagem de um evento já achatado."""
    # Envelope Meta: value.messages[]. Statuses e fromMe não são inbound.
    if isinstance(data.get("messages"), list):
        nomes = {
            c.get("wa_id"): (c.get("profile") or {}).get("name", "")
            for c in data.get("contacts", [])
            if isinstance(c, dict)
        }
        for msg in data["messages"]:
            if not isinstance(msg, dict) or msg.get("fromMe"):
                continue
            texto = _extrair_texto_waiaconnect(msg)
            remote_jid = msg.get("from") or ""
            if not texto or not remote_jid:
                continue
            return {
                "remote_jid": remote_jid,
                "texto": texto,
                "push_name": nomes.get(remote_jid, "") or "",
                "message_id": msg.get("id") or "",
                "timestamp": msg.get("timestamp"),
            }
        return None

    return _mensagem_do_formato_direto(data)


def _mensagem_do_formato_direto(data: dict) -> dict[str, Any] | None:
    """Payload sem envelope: o próprio evento é a mensagem.

    `type` NÃO é lido como direção. Na API de envio da WaiaConnect `type` é o
    tipo do conteúdo ("text", "template"), então usá-lo como filtro descartava
    toda mensagem de texto. A direção, quando presente, é `direction`.
    """
    direction = data.get("direction")
    if direction is not None and direction not in _DIRECOES_INBOUND:
        log.info("Evento ignorado: direction=%r fora de %s", direction, _DIRECOES_INBOUND)
        return None

    aninhado = data.get("data")
    if isinstance(aninhado, dict):
        data = aninhado

    remote_jid = (
        data.get("from")
        or data.get("from_number")
        or data.get("remoteJid")
        or (data.get("contact") or {}).get("wa_id")
    )
    if not remote_jid:
        log.info("Evento ignorado: sem remetente — chaves=%s", sorted(data))
        return None

    texto = _extrair_texto_waiaconnect(data)
    if not texto:
        log.info("Evento ignorado: sem texto extraível — chaves=%s", sorted(data))
        return None

    return {
        "remote_jid": remote_jid,
        "texto": texto,
        "push_name": (
            data.get("pushName")
            or (data.get("contact") or {}).get("profile", {}).get("name")
            or ""
        ),
        "message_id": data.get("id")
        or data.get("message_id")
        or (data.get("key") or {}).get("id"),
        "timestamp": data.get("timestamp") or data.get("time"),
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

    # Mídia (não processa aqui — só texto)
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