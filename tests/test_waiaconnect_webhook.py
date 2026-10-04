"""Parser do webhook WaiaConnect: envelope Meta Cloud API e payload direto.

Regressão: o parser descartava toda mensagem porque exigia `direction` ou
`type` numa lista de valores de direção. `type` na API da WaiaConnect é o tipo
do conteúdo ("text"), não a direção — logo toda mensagem de texto era perdida.
"""

import asyncio

import pytest

from app import waiaconnect

JID = "5545888887777"

META_ENVELOPE = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WABA_ID",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "5545999990000",
                            "phone_number_id": "123",
                        },
                        "contacts": [{"profile": {"name": "Joao"}, "wa_id": JID}],
                        "messages": [
                            {
                                "from": JID,
                                "id": "wamid.X",
                                "timestamp": "1700000000",
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


@pytest.fixture(autouse=True)
def limpa_janela():
    waiaconnect._ultima_msg_inbound.clear()
    yield
    waiaconnect._ultima_msg_inbound.clear()


def _processa(body):
    return asyncio.run(waiaconnect.processar_webhook(body))


def test_envelope_meta_cloud_api():
    r = _processa(META_ENVELOPE)
    assert r is not None
    assert r["texto"] == "oi"
    assert r["remote_jid"] == JID
    assert r["push_name"] == "Joao"
    assert r["message_id"] == "wamid.X"


def test_meta_preserva_janela_de_24h():
    _processa(META_ENVELOPE)
    assert waiaconnect.dentro_da_janela_24h(JID) is True


def test_meta_interativo_button_reply():
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"profile": {"name": "Joao"}, "wa_id": JID}],
                            "messages": [
                                {
                                    "from": JID,
                                    "id": "wamid.B",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "1", "title": "Sim"},
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }
    r = _processa(body)
    assert r["texto"] == "Sim"


def test_meta_ignora_status_de_entrega():
    """changes[].field=statuses não é mensagem de cliente."""
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "statuses",
                        "value": {
                            "statuses": [
                                {"id": "wamid.X", "status": "delivered", "recipient_id": JID}
                            ]
                        },
                    }
                ]
            }
        ],
    }
    assert _processa(body) is None


def test_meta_ignora_mensagem_fromMe():
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": JID,
                                    "fromMe": True,
                                    "id": "wamid.Z",
                                    "type": "text",
                                    "text": {"body": "eco"},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    assert _processa(body) is None


def test_formato_direto_sem_direction_aceita():
    """Regressão do bug: type='text' era lido como direção e descartado."""
    body = {"connectionId": "c1", "from": JID, "type": "text", "text": {"body": "oi"}}
    r = _processa(body)
    assert r is not None
    assert r["texto"] == "oi"


def test_formato_direto_com_direction_inbound():
    body = {
        "direction": "inbound",
        "data": {"from": JID, "pushName": "Joao", "id": "m1", "type": "text", "text": {"body": "oi"}},
    }
    r = _processa(body)
    assert r["texto"] == "oi"
    assert r["push_name"] == "Joao"


def test_formato_direto_direction_outbound_descartado():
    body = {"direction": "outbound", "from": JID, "type": "text", "text": {"body": "eco"}}
    assert _processa(body) is None


def test_text_como_string_simples():
    body = {"from": JID, "type": "text", "text": "oi"}
    assert (_processa(body))["texto"] == "oi"


def test_body_invalido_nao_levanta():
    assert _processa({"foo": "bar"}) is None
    assert _processa([]) is None


def test_body_nao_dict_nao_levanta():
    assert _processa("lixo") is None


def test_localizacao_vira_texto():
    body = {
        "from": JID,
        "type": "location",
        "location": {"latitude": -25.4, "longitude": -54.6},
    }
    assert "Localização" in (_processa(body))["texto"]


def test_endpoint_grava_cliente_a_partir_do_envelope_meta(monkeypatch):
    """Integra: envelope Meta no endpoint precisa gravar o cliente.

    Cobre a cadeia inteira — auth, parse e upsert — que é onde a mensagem
    sumia antes.
    """
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
        json=META_ENVELOPE,
        headers={"X-Connect-Token": "wct_teste"},
    )
    assert r.status_code == 200, r.text

    # O handler despacha em background task; o fake acima impede a execução,
    # então rodamos direto para conferir o que foi persistido.
    asyncio.run(whatsapp._processar_evento_waiaconnect(META_ENVELOPE))

    assert db.get_cliente(JID) is not None
    assert waiaconnect.dentro_da_janela_24h(JID) is True
