"""Parser do webhook WaiaConnect.

O envelope da WaiaConnect é versionado e não é o payload cru da Meta:
    {id, type, version, createdAt, connection, sequence,
     data: {message: {...}, contacts: [{wa_id, profile: {name}}]}}

Regressão: o parser exigia `direction`/`type` numa lista de valores de direção
("inbound", "message_received", "messages.upsert"). O `type` do topo é o TIPO DO
EVENTO ("message.received") e nunca casou — toda mensagem era descartada em
silêncio, com 200 OK no endpoint.
"""

import asyncio

import pytest

from app import waiaconnect

JID = "5493511234567"


def envelope(data, tipo="message.received", evento_id="evt_abc123"):
    """Envelope da WaiaConnect com os campos do exemplo da doc."""
    return {
        "id": evento_id,
        "type": tipo,
        "version": "1",
        "createdAt": "2026-08-06T00:31:22.461Z",
        "connection": {
            "id": "conn_4eede070e5a84d1590bdce2ea1d837bc",
            "phoneNumber": "+5493510000000",
            "externalId": "farmacia-lopez",
        },
        "sequence": 12345,
        "data": data,
    }


MESSAGE_RECEIVED = envelope(
    {
        "message": {
            "id": "wamid.HBgL",
            "from": JID,
            "type": "text",
            "text": {"body": "hola"},
        },
        "contacts": [{"wa_id": JID, "profile": {"name": "Ana"}}],
    }
)


@pytest.fixture(autouse=True)
def limpa_janela():
    waiaconnect._ultima_msg_inbound.clear()
    yield
    waiaconnect._ultima_msg_inbound.clear()


def _processa(body):
    return asyncio.run(waiaconnect.processar_webhook(body))


def test_message_received_e_aceito():
    r = _processa(MESSAGE_RECEIVED)
    assert r is not None
    assert r["texto"] == "hola"
    assert r["remote_jid"] == JID
    assert r["push_name"] == "Ana"
    assert r["message_id"] == "wamid.HBgL"


def test_message_received_preserva_janela_de_24h():
    _processa(MESSAGE_RECEIVED)
    assert waiaconnect.dentro_da_janela_24h(JID) is True


def test_body_em_array():
    r = _processa([MESSAGE_RECEIVED])
    assert r["texto"] == "hola"


def test_message_echo_do_dono_e_descartado():
    """Dono mandou do celular: from é o número do negócio, não do cliente."""
    body = envelope(
        {
            "origin": "device",
            "message": {
                "id": "wamid.ECHO",
                "from": "5493516516690",
                "to": JID,
                "type": "text",
                "timestamp": "1754531482",
                "text": {"body": "te confirmo el turno"},
            },
        },
        tipo="message.echo",
    )
    assert _processa(body) is None
    assert waiaconnect._ultima_msg_inbound == {}


def test_message_status_descartado():
    body = envelope(
        {
            "messageId": "wamid.HBgL",
            "status": "read",
            "origin": "api",
            "recipient": JID,
            "timestamp": "2026-08-06T00:31:25.000Z",
        },
        tipo="message.status",
    )
    assert _processa(body) is None


def test_history_batch_nao_e_processado():
    """history.batch traz meses de conversa — responderia mensagens do passado."""
    body = envelope(
        {
            "batchId": "hb_3c1f",
            "count": 1,
            "messages": [
                {
                    "contact": JID,
                    "direction": "in",
                    "message": {
                        "id": "wamid.H1",
                        "from": JID,
                        "type": "text",
                        "text": {"body": "¿Tienen turno el lunes?"},
                    },
                }
            ],
        },
        tipo="history.batch",
    )
    assert _processa(body) is None
    assert waiaconnect._ultima_msg_inbound == {}


def test_webhook_test_descartado():
    body = envelope(
        {"type": "test", "test": True, "message": "WAIA Connect test event"},
        tipo="webhook.test",
    )
    assert _processa(body) is None


def test_billing_descartado():
    assert _processa(envelope({"planCode": "growth"}, tipo="billing.payment_recovered")) is None


def test_interactive_button_reply():
    body = envelope(
        {
            "message": {
                "id": "wamid.B",
                "from": JID,
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "1", "title": "Sim"},
                },
            },
            "contacts": [{"wa_id": JID, "profile": {"name": "Ana"}}],
        }
    )
    assert _processa(body)["texto"] == "Sim"


def test_localizacao():
    body = envelope(
        {
            "message": {
                "id": "wamid.L",
                "from": JID,
                "type": "location",
                "location": {"latitude": -31.4, "longitude": -64.1},
            }
        }
    )
    assert "Localização" in _processa(body)["texto"]


def test_push_name_ausente_nao_quebra():
    body = envelope(
        {"message": {"id": "wamid.S", "from": JID, "type": "text", "text": {"body": "oi"}}}
    )
    assert _processa(body)["push_name"] == ""


def test_contacts_de_outro_numero_nao_vaza_nome():
    body = envelope(
        {
            "message": {"id": "wamid.X", "from": JID, "type": "text", "text": {"body": "oi"}},
            "contacts": [{"wa_id": "999", "profile": {"name": "Outro"}}],
        }
    )
    assert _processa(body)["push_name"] == ""


def test_mensagem_sem_texto_e_sem_midia_e_descartada():
    """types que não são mídia nem texto (ex.: reaction, protocol) não viram conversa."""
    body = envelope(
        {
            "message": {
                "id": "wamid.R",
                "from": JID,
                "type": "reaction",
                "reaction": {"emoji": "\U0001f44d", "message_id": "wamid.X"},
            }
        }
    )
    assert _processa(body) is None


def test_midia_sem_id_vira_marcador():
    """Sem id não há o que baixar, mas a mensagem continua visível no painel."""
    r = _processa(envelope_midia("image", {}))
    assert r is not None
    assert "não foi possível processar" in r["texto"]


def test_data_sem_message_nao_levanta():
    assert _processa(envelope({"outro": 1})) is None


def test_envelope_sem_data_nao_levanta():
    assert _processa({"id": "evt_x", "type": "message.received"}) is None


def test_body_invalido_nao_levanta():
    assert _processa({"foo": "bar"}) is None
    assert _processa([]) is None
    assert _processa("lixo") is None
    assert _processa(None) is None


def test_envelope_cru_da_meta_e_recusado():
    """A WaiaConnect nunca envia payload cru da Meta — não vale aceitar."""
    meta = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"profile": {"name": "Ana"}, "wa_id": JID}],
                            "messages": [
                                {
                                    "from": JID,
                                    "id": "wamid.META",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    assert _processa(meta) is None


def test_endpoint_grava_cliente_a_partir_do_envelope_real(monkeypatch):
    """Integra: envelope da doc no endpoint precisa gravar o cliente."""
    from fastapi.testclient import TestClient

    from app import db, whatsapp
    from app.config import settings
    from app.main import app

    class _FakeTask:
        """Não agenda o debounce de 6s."""

        def __init__(self, coro):
            coro.close()

    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    monkeypatch.setattr(settings, "waiaconnect_connect_token", "wct_teste")

    client = TestClient(app)
    r = client.post(
        "/webhook/waiaconnect",
        json=MESSAGE_RECEIVED,
        headers={"X-Connect-Token": "wct_teste"},
    )
    assert r.status_code == 200, r.text

    # O handler despacha em background task; o fake acima impede a execução,
    # então rodamos direto para conferir o que foi persistido.
    asyncio.run(whatsapp._processar_evento_waiaconnect(MESSAGE_RECEIVED))

    assert db.get_cliente(JID) is not None
    assert waiaconnect.dentro_da_janela_24h(JID) is True


def envelope_midia(tipo, detalhe, extra=None):
    msg = {"id": f"wamid.{tipo}", "from": JID, "type": tipo, tipo: detalhe}
    if extra:
        msg.update(extra)
    return envelope({"message": msg, "contacts": [{"wa_id": JID, "profile": {"name": "Ana"}}]})


class _FakeResp:
    def __init__(self, status=200, content=b"", ctype="application/octet-stream"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}
        self.text = ""


class _FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, path):
        self.calls.append(path)
        return self.resp


@pytest.fixture
def ia_falsa(monkeypatch):
    """Substitui download + IA por dublês; nada de rede."""
    from app import ia, waiaconnect

    calls = {}

    async def fake_baixar(media_id):
        calls["download"] = media_id
        return b"BYTES", calls.get("mime", "audio/ogg")

    async def fake_transcrever(b64, mimetype="audio/ogg"):
        calls["transcrever"] = (b64, mimetype)
        return "quero marcar um servico"

    async def fake_descrever(b64, mimetype="image/jpeg", legenda=""):
        calls["descrever"] = (b64, mimetype, legenda)
        return "uma foto de um carro quebrado"

    monkeypatch.setattr(waiaconnect, "baixar_midia", fake_baixar)
    monkeypatch.setattr(ia, "transcrever_audio", fake_transcrever)
    monkeypatch.setattr(ia, "descrever_imagem", fake_descrever)
    return calls


def test_audio_e_transcrito(ia_falsa):
    body = envelope_midia("audio", {"id": "media_a1", "mime_type": "audio/ogg; codecs=opus", "voice": True})
    r = _processa(body)
    assert "quero marcar um servico" in r["texto"]
    assert r["texto"].startswith("[Áudio do cliente]")
    assert ia_falsa["download"] == "media_a1"


def test_audio_nota_de_voz_usa_mime_do_download(ia_falsa):
    ia_falsa["mime"] = "audio/mpeg"
    body = envelope_midia("audio", {"id": "media_a2", "voice": True})
    r = _processa(body)
    assert ia_falsa["transcrever"][1] == "audio/mpeg"
    assert "quero marcar" in r["texto"]


def test_imagem_e_descrita_com_legenda(ia_falsa):
    body = envelope_midia(
        "image",
        {"id": "media_i1", "mime_type": "image/jpeg", "caption": "meu carro"},
    )
    r = _processa(body)
    assert "uma foto de um carro quebrado" in r["texto"]
    assert "Legenda: meu carro" in r["texto"]
    assert ia_falsa["descrever"][2] == "meu carro"


def test_midia_abre_janela_de_24h(ia_falsa):
    body = envelope_midia("audio", {"id": "media_a3"})
    _processa(body)
    assert waiaconnect.dentro_da_janela_24h(JID) is True


def test_texto_tem_precedencia_sobre_midia(ia_falsa):
    body = envelope_midia(
        "image", {"id": "media_i2", "caption": "olha"}, extra={"text": {"body": "olha isso"}}
    )
    r = _processa(body)
    assert r["texto"] == "olha isso"
    assert "download" not in ia_falsa


def test_falha_na_transcricao_nao_perde_a_mensagem(ia_falsa, monkeypatch):
    from app import ia

    async def boom(*a, **k):
        raise RuntimeError("provedor sem chave")

    monkeypatch.setattr(ia, "transcrever_audio", boom)
    body = envelope_midia("audio", {"id": "media_a4"})
    r = _processa(body)
    assert r is not None
    assert "não foi possível processar" in r["texto"]


def test_download_404_nao_perde_a_mensagem(monkeypatch):
    from app import waiaconnect as w

    async def fake_baixar(media_id):
        raise RuntimeError("mídia não encontrada (404)")

    monkeypatch.setattr(w, "baixar_midia", fake_baixar)
    r = _processa(envelope_midia("image", {"id": "sumiu"}))
    assert "não foi possível processar" in r["texto"]


def test_document_vira_marcador_com_nome(monkeypatch):
    r = _processa(envelope_midia("document", {"id": "d1", "filename": "orcamento.pdf"}))
    assert r["texto"] == "[Documento do cliente: orcamento.pdf]"


def test_sticker_vira_marcador():
    r = _processa(envelope_midia("sticker", {"id": "s1"}))
    assert "Figurinha" in r["texto"]


def test_video_vira_marcador():
    r = _processa(envelope_midia("video", {"id": "v1"}))
    assert "Video" in r["texto"]


def test_baixar_midia_extrai_bytes_e_mime(monkeypatch):
    resp = _FakeResp(200, b"\x00\x01OPUS", "audio/ogg; codecs=opus")
    monkeypatch.setattr(waiaconnect, "_async_client", lambda: _FakeClient(resp))
    conteudo, mime = asyncio.run(waiaconnect.baixar_midia("media_x"))
    assert conteudo == b"\x00\x01OPUS"
    assert mime == "audio/ogg"


def test_baixar_midia_404_levanta(monkeypatch):
    monkeypatch.setattr(waiaconnect, "_async_client", lambda: _FakeClient(_FakeResp(404)))
    try:
        asyncio.run(waiaconnect.baixar_midia("sumiu"))
        assert False, "deveria levantar"
    except RuntimeError as e:
        assert "404" in str(e)


def test_baixar_midia_acima_do_limite_levanta(monkeypatch):
    grande = _FakeResp(200, b"x" * (waiaconnect.MEDIA_MAX_BYTES + 1), "audio/ogg")
    monkeypatch.setattr(waiaconnect, "_async_client", lambda: _FakeClient(grande))
    try:
        asyncio.run(waiaconnect.baixar_midia("gigante"))
        assert False, "deveria levantar"
    except RuntimeError as e:
        assert "excede o limite" in str(e)


def test_baixar_midia_sem_id_levanta():
    try:
        asyncio.run(waiaconnect.baixar_midia(""))
        assert False, "deveria levantar"
    except ValueError:
        pass
