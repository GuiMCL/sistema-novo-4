"""Provedores de IA — config local (SQLite) e chamadas diretas às APIs.

Substitui o app/n8n.py: a chave/base URL/modelo de cada uso (texto, áudio,
imagem) agora vivem na tabela ProvedorIA, e as chamadas (transcrição, visão,
listagem de modelos) são feitas daqui, sem intermediário.

Fluxo unidirecional preservado: a chave ENTRA pelo painel e é gravada no
SQLite; nenhuma rota devolve a chave (nem mascarada) — o painel só lê
provedor (deduzido da base URL) e modelo.

Compatibilidade por uso:
  - texto/imagem: qualquer API compatível com chat completions (OpenAI,
    Anthropic, OpenRouter, Groq, Gemini, Mistral...). A Anthropic atende pela
    camada compatível em /v1/chat/completions (Bearer); só o GET /models
    exige headers nativos (x-api-key + anthropic-version).
  - áudio: exige `POST /audio/transcriptions` multipart estilo OpenAI com
    `model=whisper-1` → só OpenAI ou proxy compatível (OpenRouter usa outro
    formato, JSON base64 — fica p/ depois).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from . import db

# Defaults de modelo (paridade com o workflow n8n original).
MODELO_PADRAO = {"texto": "gpt-5.1", "audio": "whisper-1", "imagem": "gpt-4o"}

CRED_POR_ALVO = {"texto": "IA - Texto", "audio": "IA - Áudio", "imagem": "IA - Imagem"}
ALVOS = ("texto", "audio", "imagem")
# Áudio fixa whisper-1 (formato multipart OpenAI) — sem modelo configurável.
ALVOS_COM_MODELO = ("texto", "imagem")

# Provedores com API compatível OpenAI, com capacidade por uso.
PROVEDORES: dict[str, dict[str, Any]] = {
    "openai": {"nome": "OpenAI", "base_url": "https://api.openai.com/v1", "texto": True, "audio": True, "imagem": True},
    "anthropic": {"nome": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1", "texto": True, "audio": False, "imagem": True},
    "groq": {"nome": "Groq", "base_url": "https://api.groq.com/openai/v1", "texto": True, "audio": False, "imagem": True},
    "openrouter": {"nome": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "texto": True, "audio": False, "imagem": True},
    "mistral": {"nome": "Mistral", "base_url": "https://api.mistral.ai/v1", "texto": True, "audio": False, "imagem": True},
    "xai": {"nome": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "texto": True, "audio": False, "imagem": True},
    "gemini": {"nome": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "texto": True, "audio": False, "imagem": True},
    "deepseek": {"nome": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "texto": True, "audio": False, "imagem": False},
    "custom": {"nome": "Personalizado (URL própria)", "base_url": "", "texto": True, "audio": True, "imagem": True},
}


class IANaoConfigurada(RuntimeError):
    """Uso ainda sem chave salva — o painel orienta o usuário a configurar."""


def _config(alvo: str) -> db.ProvedorIA:
    p = db.get_provedor_ia(alvo)
    if not p or not p.api_key:
        raise IANaoConfigurada(
            f"Provedor de IA ({alvo}) não configurado — salve a chave no painel /admin."
        )
    return p


def _provedor_da_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.rstrip("/")
    for chave, preset in PROVEDORES.items():
        if preset["base_url"] and preset["base_url"].rstrip("/") == u:
            return chave
    return "custom"


# ---------------------------------------------------------------------------
# Estado / escrita (consumidos pelas rotas do painel)
# ---------------------------------------------------------------------------


def estado() -> dict:
    """Snapshot p/ o painel: provedor (deduzido da URL), modelo, última troca."""
    out: dict[str, Any] = {}
    for alvo in ALVOS:
        p = db.get_provedor_ia(alvo)
        out[alvo] = {
            "provedor": _provedor_da_url(p.base_url) if p and p.api_key else None,
            "modelo": (p.modelo or None) if p and alvo in ALVOS_COM_MODELO else None,
            "atualizado_em": p.atualizado_em if p else None,
        }
    return out


def atualizar_chave(alvo: str, api_key: str, base_url: str) -> dict:
    """Grava chave + base URL do alvo. Não retorna segredo algum."""
    atual = db.get_provedor_ia(alvo)
    modelo = atual.modelo if atual and atual.modelo else MODELO_PADRAO[alvo]
    db.set_provedor_ia(alvo, api_key=api_key, base_url=base_url.rstrip("/"), modelo=modelo)
    return {"ok": True, "credencial": CRED_POR_ALVO[alvo]}


def atualizar_modelo(alvo: str, modelo: str) -> dict:
    if alvo not in ALVOS_COM_MODELO:
        raise RuntimeError("Este uso não tem modelo configurável (áudio fixa whisper-1).")
    _config(alvo)  # exige chave antes
    db.set_provedor_ia(alvo, modelo=modelo)
    return {"ok": True, "modelo": modelo}


# ---------------------------------------------------------------------------
# Listagem de modelos
# ---------------------------------------------------------------------------


def listar_modelos_do_provedor(base_url: str, api_key: str) -> list[dict]:
    """GET /models no provedor. Também usada no preview (chave transiente)."""
    if "api.anthropic.com" in base_url:
        # GET /models da Anthropic não aceita Bearer — só x-api-key + versão.
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        params: dict[str, Any] | None = {"limit": 100}
    else:
        headers = {"Authorization": f"Bearer {api_key}"}
        params = None
    r = httpx.get(
        base_url.rstrip("/") + "/models",
        headers=headers,
        params=params,
        timeout=20.0,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Provedor respondeu HTTP {r.status_code} ao listar modelos — confira a chave."
        )
    data = r.json().get("data", [])
    modelos = sorted(m["id"] for m in data if m.get("id"))
    return [{"valor": m} for m in modelos]


def listar_modelos(alvo: str) -> list[dict]:
    """Modelos do provedor configurado p/ o alvo (chave salva, nunca exposta)."""
    p = _config(alvo)
    return listar_modelos_do_provedor(p.base_url, p.api_key)


# ---------------------------------------------------------------------------
# Mídia (pipeline do WhatsApp)
# ---------------------------------------------------------------------------


async def completar(system: str, user: str, temperature: float = 0.7) -> str:
    """Gera texto usando o provedor de texto configurado. Retorna o conteudo da resposta."""
    p = _config("texto")
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            p.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {p.api_key}"},
            json={
                "model": p.modelo or MODELO_PADRAO["texto"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Completions falhou (HTTP {r.status_code}): {r.text[:200]}")
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def completar_sync(system: str, user: str, temperature: float = 0.7) -> str:
    """Versao sincrona de `completar` (para rotas do painel fora do event loop)."""
    p = _config("texto")
    with httpx.Client(timeout=60.0) as c:
        r = c.post(
            p.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {p.api_key}"},
            json={
                "model": p.modelo or MODELO_PADRAO["texto"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Completions falhou (HTTP {r.status_code}): {r.text[:200]}")
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


async def transcrever_audio(audio_b64: str, mimetype: str = "audio/ogg") -> str:
    """Transcreve áudio (multipart estilo OpenAI, model=whisper-1)."""
    p = _config("audio")
    ext = "ogg" if "ogg" in mimetype else mimetype.split("/")[-1] or "ogg"
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            p.base_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {p.api_key}"},
            files={"file": (f"audio.{ext}", base64.b64decode(audio_b64), mimetype)},
            data={"model": p.modelo or MODELO_PADRAO["audio"]},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Transcrição falhou (HTTP {r.status_code}): {r.text[:200]}")
    return r.json().get("text", "")


async def descrever_imagem(imagem_b64: str, mimetype: str = "image/jpeg", legenda: str = "") -> str:
    """Descreve/transcreve uma imagem via chat completions com visão."""
    p = _config("imagem")
    instrucao = (
        "Descreva o conteúdo desta imagem de forma objetiva, transcrevendo "
        "qualquer texto visível. A descrição será usada por um atendente "
        "virtual para entender o que o cliente enviou."
    )
    if legenda:
        instrucao += f"\nLegenda enviada pelo cliente: {legenda}"
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            p.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {p.api_key}"},
            json={
                "model": p.modelo or MODELO_PADRAO["imagem"],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instrucao},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mimetype};base64,{imagem_b64}"},
                            },
                        ],
                    }
                ],
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Visão falhou (HTTP {r.status_code}): {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"] or ""
