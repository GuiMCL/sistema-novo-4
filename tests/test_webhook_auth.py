"""Auth do webhook WaiaConnect: header X-Connect-Token, ?token= e assinatura HMAC.

O painel WaiaConnect manda um token fixo no header X-Connect-Token. O endpoint
precisa aceitá-lo sem exigir assinatura HMAC válida (que usa outra chave) nem
o ?token= derivado da senha do admin.
"""

import pytest
from fastapi.testclient import TestClient

from app import whatsapp
from app.config import settings
from app.main import app

TOKEN_PAINEL = "wct_92756a083c636389e84b481b3dc8ac5337f5a40b00ec42f05ca2803c9e97cd99"


@pytest.fixture
def client(monkeypatch):
    """TestClient sem lifespan (não sobe o worker de tarefas) e sem pipeline."""

    class _FakeTask:
        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(whatsapp.settings, "waiaconnect_connect_token", TOKEN_PAINEL)
    monkeypatch.setattr(whatsapp.settings, "webhook_token", "tokenseco-do-admin")
    # Sem `with`: o lifespan sobe o worker de tarefas e o session manager do MCP,
    # que só pode ser iniciado uma vez por instancia.
    yield TestClient(app)


def test_aceita_token_estatico_do_header(client):
    r = client.post("/webhook/waiaconnect", json={}, headers={"X-Connect-Token": TOKEN_PAINEL})
    assert r.status_code == 200, r.text


def test_header_com_token_errado_responde_403(client):
    r = client.post("/webhook/waiaconnect", json={}, headers={"X-Connect-Token": "wct_errado"})
    assert r.status_code == 403
    assert r.json() == {"erro": "token inválido"}


def test_sem_nenhuma_credencial_responde_403(client):
    r = client.post("/webhook/waiaconnect", json={})
    assert r.status_code == 403


def test_header_valido_ignora_query_token_errado(client):
    """Header válido não pode ser derrubado por um ?token= errado na URL."""
    r = client.post(
        "/webhook/waiaconnect?token=errado",
        json={},
        headers={"X-Connect-Token": TOKEN_PAINEL},
    )
    assert r.status_code == 200, r.text


def test_query_param_legado_continua_valendo(client):
    r = client.post("/webhook/waiaconnect?token=tokenseco-do-admin", json={})
    assert r.status_code == 200, r.text


def test_assinatura_hmac_valida_continua_valendo(client, monkeypatch):
    import hashlib
    import hmac

    corpo = b'{"evento":"x"}'
    esperado = hmac.new(settings.webhook_token.encode(), corpo, hashlib.sha256).hexdigest()
    monkeypatch.setattr(whatsapp, "_processar_evento_waiaconnect", _nao_processa)
    r = client.post(
        "/webhook/waiaconnect",
        content=corpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={esperado}"},
    )
    assert r.status_code == 200, r.text


async def _nao_processa(body):
    return None


def test_sem_token_configurado_header_e_ignorado(monkeypatch):
    """Sem WAIACONNECT_CONNECT_TOKEN, o header não vale como credencial.

    Comparar contra string vazia aceitaria qualquer requisição — o header
    precisa ser recusado quando não há token configurado.
    """
    monkeypatch.setattr(whatsapp.settings, "waiaconnect_connect_token", "")
    monkeypatch.setattr(whatsapp.settings, "webhook_token", "tokenseco-do-admin")
    monkeypatch.setattr(whatsapp.asyncio, "create_task", lambda coro: coro.close())

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    assert whatsapp._verificar_token_estatico(_Req({"x-connect-token": TOKEN_PAINEL})) is False
    assert whatsapp._verificar_token_estatico(_Req({"x-connect-token": ""})) is False


def test_header_vazio_nao_passa_quando_token_configurado(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "waiaconnect_connect_token", TOKEN_PAINEL)

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    assert whatsapp._verificar_token_estatico(_Req({})) is False
    assert whatsapp._verificar_token_estatico(_Req({"x-connect-token": ""})) is False
    assert whatsapp._verificar_token_estatico(_Req({"x-connect-token": TOKEN_PAINEL})) is True
