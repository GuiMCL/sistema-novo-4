"""Auth do webhook WaiaConnect: header X-Connect-Token, ?token= e assinatura HMAC.

O painel WaiaConnect manda um token fixo no header X-Connect-Token. O endpoint
precisa aceitá-lo sem exigir assinatura HMAC válida (que usa outra chave) nem
o ?token= derivado da senha do admin.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app import whatsapp
from app.config import settings
from app.main import app

TOKEN_PAINEL = "wct_92756a083c636389e84b481b3dc8ac5337f5a40b00ec42f05ca2803c9e97cd99"
SEGREDO = "whsec_endpoint_secret"


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


def _assina(secret: str, corpo: bytes, timestamp: str) -> str:
    """X-Connect-Signature-256 conforme a spec: sha256=HMAC(secret, "ts.body")."""
    import hashlib
    import hmac

    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + corpo, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _req_headers(secret: str, corpo: bytes, timestamp: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Connect-Timestamp": timestamp,
        "X-Connect-Signature-256": _assina(secret, corpo, timestamp),
    }


def test_assinatura_valida_autoriza_sem_token_estatico(monkeypatch):
    """Com WAIACONNECT_WEBHOOK_SECRET, a assinatura sozinha basta."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    class _FakeTask:
        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(settings, "waiaconnect_webhook_secret", SEGREDO)
    monkeypatch.setattr(settings, "waiaconnect_connect_token", "")

    corpo = b'{"id":"evt_1","type":"message.received"}'
    ts = str(int(time.time()))
    client = TestClient(app)
    assert client.post("/webhook/waiaconnect", content=corpo, headers=_req_headers(SEGREDO, corpo, ts)).status_code == 200

    # Sem assinatura nem token: recusado.
    assert client.post("/webhook/waiaconnect", json={}).status_code == 403


def test_assinatura_com_corpo_adulterado_e_recusada(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    class _FakeTask:
        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(settings, "waiaconnect_webhook_secret", SEGREDO)
    monkeypatch.setattr(settings, "waiaconnect_connect_token", "")

    ts = str(int(time.time()))
    headers = _req_headers(SEGREDO, b'{"original":true}', ts)
    client = TestClient(app)
    # Mesmos headers, corpo diferente: o hash não bate mais.
    r = client.post("/webhook/waiaconnect", content=b'{"adulterado":true}', headers=headers)
    assert r.status_code == 403


def test_assinatura_fora_da_janela_de_replay_e_recusada(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    class _FakeTask:
        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(settings, "waiaconnect_webhook_secret", SEGREDO)
    monkeypatch.setattr(settings, "waiaconnect_connect_token", "")

    corpo = b'{"id":"evt_1"}'
    ts = str(int(time.time()) - 3600)  # 1h de idade
    client = TestClient(app)
    r = client.post("/webhook/waiaconnect", content=corpo, headers=_req_headers(SEGREDO, corpo, ts))
    assert r.status_code == 403


def test_assinatura_com_segredo_errado_e_recusada(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    class _FakeTask:
        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(settings, "waiaconnect_webhook_secret", SEGREDO)
    monkeypatch.setattr(settings, "waiaconnect_connect_token", "")

    corpo = b'{"id":"evt_1"}'
    ts = str(int(time.time()))
    client = TestClient(app)
    headers = _req_headers("whsec_outro", corpo, ts)
    assert client.post("/webhook/waiaconnect", content=corpo, headers=headers).status_code == 403


def test_assinatura_ignorada_quando_secret_nao_configurado(monkeypatch):
    """Sem WAIACONNECT_WEBHOOK_SECRET a assinatura não vale como credencial."""
    monkeypatch.setattr(settings, "waiaconnect_webhook_secret", "")

    class _Req:
        headers = {
            "x-connect-timestamp": str(int(time.time())),
            "x-connect-signature-256": _assina(SEGREDO, b"{}", str(int(time.time()))),
        }

    assert whatsapp._verificar_assinatura_waiaconnect(_Req(), b"{}") is False


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
