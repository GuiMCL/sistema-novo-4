"""Cliente da API oficial WhatsApp Cloud (Meta).

Cada ``instancia`` usada pela aplicação é o ``PHONE_NUMBER_ID`` da Meta. Não
há sessão, QR Code ou pareamento: o número é cadastrado no WhatsApp Manager.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("whatsapp_cloud")
GRAPH_URL = "https://graph.facebook.com"


class WhatsAppCloudError(RuntimeError):
    pass


def _phone_number_id(instancia: str | None = None) -> str:
    return (instancia or settings.whatsapp_phone_number_id).strip()


def _headers() -> dict[str, str]:
    if not settings.whatsapp_access_token:
        raise WhatsAppCloudError("WHATSAPP_ACCESS_TOKEN não configurado.")
    return {"Authorization": f"Bearer {settings.whatsapp_access_token}"}


def _url(path: str) -> str:
    return f"{GRAPH_URL}/{settings.whatsapp_graph_api_version}/{path.lstrip('/')}"


def configurada() -> bool:
    return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)


def validar_assinatura(corpo: bytes, assinatura: str | None) -> bool:
    """Valida X-Hub-Signature-256. Sem APP_SECRET, o webhook é recusado."""
    if not settings.whatsapp_app_secret or not assinatura:
        return False
    esperado = "sha256=" + hmac.new(
        settings.whatsapp_app_secret.encode(), corpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, assinatura)


def estado(instancia: str | None = None) -> dict[str, Any]:
    phone_id = _phone_number_id(instancia)
    if not configurada() or not phone_id:
        return {"state": "not_configured", "perfil": None}
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(_url(phone_id), headers=_headers(), params={"fields": "display_phone_number,verified_name,quality_rating"})
            r.raise_for_status()
        d = r.json()
        return {"state": "connected", "perfil": {"numero": d.get("display_phone_number", ""), "numero_fmt": d.get("display_phone_number", ""), "nome": d.get("verified_name", ""), "qualidade": d.get("quality_rating", "")}}
    except Exception as exc:
        log.warning("Falha ao consultar número Cloud API: %s", exc)
        return {"state": "error", "erro": str(exc), "perfil": None}


async def enviar_texto(numero: str, texto: str, digitando_ms: int = 0, timeout: float | None = None, instancia: str | None = None) -> None:
    phone_id = _phone_number_id(instancia)
    if not phone_id:
        raise WhatsAppCloudError("WHATSAPP_PHONE_NUMBER_ID não configurado.")
    corpo = {"messaging_product": "whatsapp", "to": re.sub(r"\D", "", numero), "type": "text", "text": {"preview_url": False, "body": texto}}
    async with httpx.AsyncClient(timeout=timeout or 30.0) as client:
        r = await client.post(_url(f"{phone_id}/messages"), headers=_headers(), json=corpo)
    if r.is_error:
        raise WhatsAppCloudError(f"Meta HTTP {r.status_code}: {r.text[:500]}")


async def enviar_template(
    numero: str, nome_template: str, parametros: list[str],
    instancia: str | None = None, idioma: str | None = None,
) -> None:
    """Envia template aprovado; parâmetros correspondem às variáveis do body."""
    phone_id = _phone_number_id(instancia)
    if not phone_id:
        raise WhatsAppCloudError("WHATSAPP_PHONE_NUMBER_ID não configurado.")
    if not nome_template:
        raise WhatsAppCloudError("Template de WhatsApp não configurado.")
    corpo = {
        "messaging_product": "whatsapp",
        "to": re.sub(r"\D", "", numero),
        "type": "template",
        "template": {
            "name": nome_template,
            "language": {"code": idioma or settings.whatsapp_template_language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": str(valor)} for valor in parametros]}],
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_url(f"{phone_id}/messages"), headers=_headers(), json=corpo)
    if r.is_error:
        raise WhatsAppCloudError(f"Meta HTTP {r.status_code}: {r.text[:500]}")


def enviar_texto_sync(numero: str, texto: str, instancia: str | None = None) -> None:
    import asyncio
    asyncio.run(enviar_texto(numero, texto, instancia=instancia))


async def marcar_como_lida(remote_jid: str, from_me: bool, message_id: str, instancia: str | None = None) -> None:
    phone_id = _phone_number_id(instancia)
    if not phone_id or not message_id:
        return
    corpo = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(_url(f"{phone_id}/messages"), headers=_headers(), json=corpo)
    except Exception as exc:
        log.warning("Não foi possível marcar a mensagem como lida: %s", exc)


async def obter_midia_base64(media_id: str, instancia: str | None = None) -> dict[str, str]:
    if not media_id:
        return {}
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        meta = await client.get(_url(media_id), headers=_headers())
        meta.raise_for_status()
        dados = meta.json()
        arquivo = await client.get(dados["url"], headers=_headers())
        arquivo.raise_for_status()
    return {"base64": base64.b64encode(arquivo.content).decode(), "mimetype": dados.get("mime_type", "")}


def foto_perfil(numero: str, instancia: str | None = None) -> None:
    # A Cloud API não disponibiliza fotos de perfil de clientes.
    return None


def checar_numero(numero: str, instancia: str | None = None) -> None:
    # A Cloud API não oferece endpoint público de existência de número.
    return None
