"""Cliente HTTP para a Evolution API — suporte a múltiplas instâncias.

Duas frentes:
  1. Instância legada (settings.evolution_instance "evo_bot") — compatibilidade.
  2. Multi-instâncias (tabela InstanciaWhatsApp) — N instâncias simultâneas.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger("evolution")

_FOTO_TTL_S = 3600.0
_FOTO_TTL_VAZIO_S = 300.0
_foto_cache: dict[str, tuple[float, str | None]] = {}
_NUMERO_TTL_S = 600.0
_numero_cache: dict[str, tuple[float, dict | None]] = {}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.evolution_api_url.rstrip("/"),
        headers={"apikey": settings.evolution_api_key},
        timeout=15.0,
    )


def _async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.evolution_api_url.rstrip("/"),
        headers={"apikey": settings.evolution_api_key},
        timeout=30.0,
    )


# ---------------------------------------------------------------------------
# Instância legada (evo_bot)
# ---------------------------------------------------------------------------


def estado() -> dict:
    """Estado da instância legada."""
    with _client() as c:
        r = c.get(f"/instance/connectionState/{settings.evolution_instance}")
        r.raise_for_status()
        est = r.json()
    if (est.get("instance") or {}).get("state") == "open":
        try:
            est["perfil"] = _perfil_instancia(settings.evolution_instance)
        except Exception:
            est["perfil"] = None
    return est


def conectar() -> dict:
    with _client() as c:
        r = c.get(f"/instance/connect/{settings.evolution_instance}")
        r.raise_for_status()
        return r.json()


def desconectar() -> dict:
    with _client() as c:
        r = c.delete(f"/instance/logout/{settings.evolution_instance}")
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Multi-instância: operações em qualquer instância
# ---------------------------------------------------------------------------


def _extract_instance_name(item: dict) -> str:
    """Extrai o nome da instância de um item do fetchInstances (lida com formatos diferentes da API)."""
    info = item.get("instance", item) if isinstance(item, dict) else {}
    return info.get("name") or info.get("instanceName") or ""


def _perfil_instancia(nome_instancia: str) -> dict | None:
    """Perfil (número, nome, foto) de uma instância específica."""
    from .phone import formatar_internacional, normalizar

    with _client() as c:
        r = c.get("/instance/fetchInstances")
        r.raise_for_status()
        dados = r.json()
    itens = dados if isinstance(dados, list) else [dados]
    for item in itens:
        nome_inst = _extract_instance_name(item)
        if nome_inst != nome_instancia:
            continue
        info = item.get("instance", item) if isinstance(item, dict) else {}
        jid = info.get("ownerJid") or info.get("owner") or ""
        nome = (info.get("profileName") or "").strip()
        return {
            "numero": normalizar(jid),
            "numero_fmt": formatar_internacional(jid),
            "nome": "" if nome in {".", "-"} else nome,
            "foto": info.get("profilePicUrl") or info.get("profilePictureUrl"),
        }
    return None


def estado_instancia(nome: str) -> dict:
    """Estado de uma instância específica pelo nome."""
    with _client() as c:
        r = c.get(f"/instance/connectionState/{nome}")
        r.raise_for_status()
        est = r.json()
    if (est.get("instance") or {}).get("state") == "open":
        try:
            est["perfil"] = _perfil_instancia(nome)
        except Exception:
            est["perfil"] = None
    return est


def conectar_instancia(nome: str) -> dict:
    """Conecta/obtem QR Code de uma instancia. Cria na Evolution se nao existir."""
    from . import db
    # Verifica se a instancia existe na Evolution
    with _client() as c:
        r = c.get("/instance/fetchInstances")
        if r.status_code == 200:
            dados = r.json()
            itens = dados if isinstance(dados, list) else [dados]
            existe = any(_extract_instance_name(i) == nome for i in itens)
            if not existe:
                # Cria a instancia sincronamente
                cr = c.post(
                    "/instance/create",
                    json={"instanceName": nome, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
                )
                if cr.status_code not in (200, 201):
                    return {"erro": f"Falha ao criar instancia: {cr.text[:200]}"}
                _configurar_instancia_sync(nome)
        r = c.get(f"/instance/connect/{nome}")
        if r.status_code == 404:
            return {"erro": "Instancia nao encontrada na Evolution mesmo apos criacao."}
        r.raise_for_status()
        return r.json()


def desconectar_instancia(nome: str) -> dict:
    with _client() as c:
        r = c.delete(f"/instance/logout/{nome}")
        r.raise_for_status()
        return r.json()


def deletar_instancia_evolution(nome: str) -> bool:
    """Deleta uma instância da Evolution API."""
    try:
        with _client() as c:
            r = c.delete(f"/instance/delete/{nome}")
            return r.status_code in (200, 201, 204)
    except Exception:
        return False


async def criar_instancia_evolution(nome: str) -> bool:
    """Cria uma instância na Evolution API. Retorna True se criou, False se já existe."""
    async with _async_client() as c:
        try:
            r = await c.get("/instance/fetchInstances")
            if r.status_code != 200:
                return False
            dados = r.json()
            itens = dados if isinstance(dados, list) else [dados]
            existe = any(
                _extract_instance_name(i) == nome
                for i in itens
            )
            if existe:
                log.info("Instância %s já existe na Evolution", nome)
                await _configurar_instancia(nome, c)
                return True

            cr = await c.post(
                "/instance/create",
                json={"instanceName": nome, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
            if cr.status_code not in (200, 201):
                log.error("Falha ao criar instância %s: %s", nome, cr.text[:300])
                return False
            log.info("Instância %s criada", nome)
            await _configurar_instancia(nome, c)
            return True
        except Exception as e:
            log.error("Erro criando instância %s: %s", nome, e)
            return False


async def _configurar_instancia(nome: str, c: httpx.AsyncClient) -> None:
    """Aplica settings + webhook em uma instância."""
    await c.post(
        f"/settings/set/{nome}",
        json={
            "rejectCall": False, "msgCall": "", "groupsIgnore": True,
            "alwaysOnline": False, "readMessages": False, "readStatus": False,
            "syncFullHistory": False,
        },
    )
    wh_url = f"{settings.webhook_url}?token={settings.webhook_token}"
    await c.post(
        f"/webhook/set/{nome}",
        json={
            "webhook": {
                "url": wh_url,
                "enabled": True,
                "webhookByEvents": False,
                "base64": True,
                "events": ["MESSAGES_UPSERT"],
            }
        },
    )


def _configurar_instancia_sync(nome: str) -> None:
    """Versao sync de _configurar_instancia."""
    with _client() as c:
        c.post(
            f"/settings/set/{nome}",
            json={
                "rejectCall": False, "msgCall": "", "groupsIgnore": True,
                "alwaysOnline": False, "readMessages": False, "readStatus": False,
                "syncFullHistory": False,
            },
        )
        wh_url = f"{settings.webhook_url}?token={settings.webhook_token}"
        c.post(
            f"/webhook/set/{nome}",
            json={
                "webhook": {
                    "url": wh_url,
                    "enabled": True,
                    "webhookByEvents": False,
                    "base64": True,
                    "events": ["MESSAGES_UPSERT"],
                }
            },
        )


async def garantir_multi_instancias(webhook_url: str) -> None:
    """Garante que todas as instâncias registradas no banco existem na Evolution.
    Tambem importa automaticamente a instancia legada evo_bot se existir na Evolution."""
    from . import db
    await asyncio.sleep(5)
    # Importa evo_bot legado se existir na Evolution mas nao no banco
    try:
        async with _async_client() as c:
            r = await c.get("/instance/fetchInstances")
            if r.status_code == 200:
                dados = r.json()
                itens = dados if isinstance(dados, list) else []
                for item in itens:
                    nome_item = _extract_instance_name(item)
                    if nome_item == settings.evolution_instance:
                        existente = db.get_instancia_por_nome(nome_item)
                        if not existente:
                            db.criar_instancia(nome_item, f"Importada (legada {nome_item})")
                            log.info("Instancia legada %s importada para o banco", nome_item)
    except Exception:
        log.warning("Nao foi possivel verificar/importar instancia legada", exc_info=True)
    # Garante que todas as instancias ativas do banco existem na Evolution
    instancias = db.listar_instancias()
    for inst in instancias:
        if inst.ativo:
            await criar_instancia_evolution(inst.nome)


# ---------------------------------------------------------------------------
# Operações de mídia (funcionam com qualquer instância)
# ---------------------------------------------------------------------------


async def obter_midia_base64(message_id: str, instancia: str | None = None) -> dict:
    nome = instancia or settings.evolution_instance
    async with _async_client() as c:
        r = await c.post(
            f"/chat/getBase64FromMediaMessage/{nome}",
            json={"message": {"key": {"id": message_id}}, "convertToMp4": False},
        )
        r.raise_for_status()
        return r.json()


async def marcar_como_lida(remote_jid: str, from_me: bool, message_id: str, instancia: str | None = None) -> None:
    nome = instancia or settings.evolution_instance
    try:
        async with _async_client() as c:
            await c.post(
                f"/chat/markMessageAsRead/{nome}",
                json={"readMessages": [{"remoteJid": remote_jid, "fromMe": from_me, "id": message_id}]},
            )
    except Exception as e:
        log.warning("markMessageAsRead falhou: %s", e)


def enviar_texto_sync(numero: str, texto: str, instancia: str | None = None) -> None:
    nome = instancia or settings.evolution_instance
    with _client() as c:
        r = c.post(
            f"/message/sendText/{nome}",
            json={"number": numero, "text": texto},
            timeout=5.0,
        )
        r.raise_for_status()


async def enviar_texto(
    numero: str, texto: str, digitando_ms: int = 0,
    timeout: float | None = None, instancia: str | None = None,
) -> None:
    nome = instancia or settings.evolution_instance
    corpo: dict = {"number": numero, "text": texto}
    if digitando_ms > 0:
        corpo["delay"] = digitando_ms
    async with _async_client() as c:
        kwargs = {"timeout": timeout} if timeout is not None else {}
        r = await c.post(f"/message/sendText/{nome}", json=corpo, **kwargs)
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Bootstrap da instância principal
# ---------------------------------------------------------------------------


async def garantir_instancia(webhook_url: str) -> None:
    async with _async_client() as c:
        for tentativa in range(30):
            try:
                r = await c.get("/instance/fetchInstances")
                if r.status_code == 200:
                    break
            except Exception:
                pass
            log.info("Aguardando Evolution API... tentativa %d", tentativa + 1)
            await asyncio.sleep(3)
        else:
            log.error("Evolution API não respondeu — instância não foi criada.")
            return

        nome = settings.evolution_instance
        existe = any(i.get("name") == nome for i in r.json())
        if not existe:
            cr = await c.post(
                "/instance/create",
                json={"instanceName": nome, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
            log.info("Criar instância %s → HTTP %s", nome, cr.status_code)
            if cr.status_code not in (200, 201):
                log.error("Falha ao criar instância: %s", cr.text[:300])
                return

        await c.post(
            f"/settings/set/{nome}",
            json={
                "rejectCall": False, "msgCall": "", "groupsIgnore": True,
                "alwaysOnline": False, "readMessages": False, "readStatus": False,
                "syncFullHistory": False,
            },
        )
        wh = await c.post(
            f"/webhook/set/{nome}",
            json={
                "webhook": {
                    "url": webhook_url, "enabled": True,
                    "webhookByEvents": False, "base64": True,
                    "events": ["MESSAGES_UPSERT"],
                }
            },
        )
        log.info("Webhook → HTTP %s (%s)", wh.status_code, webhook_url)
        if not existe:
            log.info("AÇÃO NECESSÁRIA: conecte o WhatsApp — QR Code no painel /admin.")


# ---------------------------------------------------------------------------
# Cache de foto e checagem de número
# ---------------------------------------------------------------------------


def foto_perfil(numero: str, instancia: str | None = None) -> str | None:
    digitos = re.sub(r"\D", "", numero or "")
    if not digitos:
        return None
    nome = instancia or settings.evolution_instance
    agora = time.monotonic()
    em_cache = _foto_cache.get(digitos)
    if em_cache and em_cache[0] > agora:
        return em_cache[1]
    with _client() as c:
        r = c.post(
            f"/chat/fetchProfilePictureUrl/{nome}",
            json={"number": digitos},
            timeout=5.0,
        )
        url = None
        if r.status_code < 400:
            url = r.json().get("profilePictureUrl")
        elif r.status_code >= 500:
            r.raise_for_status()
    ttl = _FOTO_TTL_S if url else _FOTO_TTL_VAZIO_S
    _foto_cache[digitos] = (agora + ttl, url)
    return url


def checar_numero(numero: str, instancia: str | None = None) -> dict | None:
    digitos = re.sub(r"\D", "", numero or "")
    if not digitos:
        return None
    nome = instancia or settings.evolution_instance
    agora = time.monotonic()
    em_cache = _numero_cache.get(digitos)
    if em_cache and em_cache[0] > agora:
        return em_cache[1]
    with _client() as c:
        r = c.post(
            f"/chat/whatsappNumbers/{nome}",
            json={"numbers": [digitos]},
            timeout=8.0,
        )
        r.raise_for_status()
        dados = r.json()
    item = None
    if isinstance(dados, list):
        item = next((d for d in dados if isinstance(d, dict)), None)
    _numero_cache[digitos] = (agora + _NUMERO_TTL_S, item)
    return item
