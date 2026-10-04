"""Regressões de entrega e de autoria das mensagens.

Cadeia que derrubava a confirmação de agendamento:

1. `enviar_bolhas` só capturava `ValueError`, mas o Meta rejeita template
   inexistente/não aprovado com HTTP 4xx — `raise_for_status()` levanta
   `HTTPStatusError`. A exceção subia e matava o laço: o cliente recebia só a
   primeira bolha.
2. `/atendimento` gravava a mensagem do atendente DEPOIS do envio. Se o envio
   falhasse (502), a mensagem nunca entrava no histórico — o painel mostrava só
   o que já estava no banco de antes.
3. `registrar_na_memoria` decidia user-part vs response por `papel == "bot"`.
   Os chamadores do painel passam "Admin (atendente)", que não é "bot", então a
   fala da empresa era gravada como UserPromptPart: aparecia do lado do cliente
   no /atendimento e poluía o contexto do modelo.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agente, atendimento as at_mod, auth, db, whatsapp
from app.main import app

TEL = "5545999900001"


def _http_erro(mensagem="template não aprovado"):
    req = httpx.Request("POST", "https://api.waiaconnect.com/v1/messages")
    resp = httpx.Response(400, request=req, json={"error": mensagem})
    return httpx.HTTPStatusError(mensagem, request=req, response=resp)


@pytest.fixture
def painel():
    app.dependency_overrides[at_mod.auth.login_required] = lambda: db.Usuario(
        nome="Atendente", email="a@a", papel="admin"
    )
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Uma bolha falhando não derruba as outras
# ---------------------------------------------------------------------------


def test_erro_de_status_nao_mata_o_resto_das_bolhas(monkeypatch):
    """O bug: HTTPStatusError (não ValueError) escapava do except."""
    enviados = []

    async def fake(numero, texto, template_fallback=None, language="pt_BR", connection_id=None):
        enviados.append(texto)
        if len(enviados) == 1:
            raise _http_erro()
        return {"ok": True}

    monkeypatch.setattr(whatsapp, "enviar_inteligente", fake)
    monkeypatch.setattr(whatsapp.asyncio, "sleep", lambda s: asyncio.sleep(0))

    asyncio.run(whatsapp.enviar_bolhas(TEL, "primeira[quebrar]segunda[quebrar]terceira"))
    assert enviados == ["primeira", "segunda", "terceira"]


def test_enviar_bolhas_reporta_que_nada_saiu(monkeypatch):
    async def sempre_falha(*a, **k):
        raise _http_erro()

    monkeypatch.setattr(whatsapp, "enviar_inteligente", sempre_falha)
    monkeypatch.setattr(whatsapp.asyncio, "sleep", lambda s: asyncio.sleep(0))
    assert asyncio.run(whatsapp.enviar_bolhas(TEL, "oi")) is False


def test_enviar_bolhas_reporta_que_saiu(monkeypatch):
    async def ok(*a, **k):
        return {"ok": True}

    monkeypatch.setattr(whatsapp, "enviar_inteligente", ok)
    monkeypatch.setattr(whatsapp.asyncio, "sleep", lambda s: asyncio.sleep(0))
    assert asyncio.run(whatsapp.enviar_bolhas(TEL, "oi")) is True


def test_value_error_fora_da_janela_tambem_e_engolido(monkeypatch):
    async def sem_template(*a, **k):
        raise ValueError("Fora da janela de 24h")

    monkeypatch.setattr(whatsapp, "enviar_inteligente", sem_template)
    monkeypatch.setattr(whatsapp.asyncio, "sleep", lambda s: asyncio.sleep(0))
    assert asyncio.run(whatsapp.enviar_bolhas(TEL, "oi")) is False


# ---------------------------------------------------------------------------
# 2 + 3. O painel grava o que o atendente mandou, do lado certo
# ---------------------------------------------------------------------------


def test_mensagem_do_atendente_fica_do_lado_do_bot():
    """Bug 3: papel "Admin (atendente)" caía no else e virava fala do cliente."""
    agente.registrar_na_memoria(TEL, "sou o atendente", "Atendente (atendente)")
    bolhas = agente.historico_para_bolhas(db.get_conversa(TEL))
    assert bolhas[-1]["quem"] == "bot"
    assert bolhas[-1]["texto"] == "sou o atendente"


def test_fala_do_cliente_continua_do_lado_do_cliente():
    agente.registrar_na_memoria(TEL, "quero agendar", "cliente")
    bolhas = agente.historico_para_bolhas(db.get_conversa(TEL))
    assert bolhas[-1]["quem"] == "cliente"


def test_painel_grava_mesmo_quando_o_envio_falha(painel, monkeypatch):
    """Bug 2: o 502 fazia a mensagem sumir do histórico."""
    async def falha(*a, **k):
        raise _http_erro()

    monkeypatch.setattr(whatsapp, "enviar_bolhas", falha)

    r = painel.post(f"/atendimento/api/conversas/{TEL}/enviar", data={"texto": "sua OS está pronta"})
    assert r.status_code == 502

    bolhas = agente.historico_para_bolhas(db.get_conversa(TEL))
    assert bolhas, "a mensagem do atendente não foi gravada"
    assert bolhas[-1]["quem"] == "bot"
    assert bolhas[-1]["texto"] == "sua OS está pronta"


def test_painel_grava_e_manda_quando_o_envio_da_certo(painel, monkeypatch):
    enviados = []

    async def ok(numero, texto):
        enviados.append((numero, texto))
        return True

    monkeypatch.setattr(whatsapp, "enviar_bolhas", ok)
    r = painel.post(f"/atendimento/api/conversas/{TEL}/enviar", data={"texto": "passando ai"})
    assert r.status_code == 200
    assert enviados == [(TEL, "passando ai")]

    bolhas = agente.historico_para_bolhas(db.get_conversa(TEL))
    assert bolhas[-1]["quem"] == "bot"


def test_painel_recusa_mensagem_vazia(painel):
    r = painel.post(f"/atendimento/api/conversas/{TEL}/enviar", data={"texto": "   "})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Lembrete: só marca aguardando_confirmacao se o cliente recebeu
# ---------------------------------------------------------------------------


def test_lembrete_nao_marca_confirmacao_sem_entrega(monkeypatch):
    """Sem isso o agente trataria um 'sim' solto como resposta a um lembrete
    que o cliente nunca recebeu."""
    from app import tarefas

    db.criar_vaga(nome="Box 1")
    db.substituir_horarios([(d, "08:00", "17:00") for d in range(7)])
    ag = db.criar_agendamento(
        nome_cliente="Cli", telefone_cliente=TEL,
        inicio="2026-01-05T08:00", fim="2026-01-05T17:00",
    )

    async def nao_entrega(*a, **k):
        return False

    monkeypatch.setattr(whatsapp, "enviar_bolhas", nao_entrega)
    monkeypatch.setattr(tarefas.ia, "completar", _completar_fake("Lembra de confirmar?"))

    asyncio.run(tarefas._disparar_lembrete(ag, 0))

    assert db.get_agendamento(ag.id).aguardando_confirmacao == 0
    assert (db.get_agendamento(ag.id).lembretes_enviados or 0) == 0


def test_lembrete_marca_confirmacao_quando_entrega(monkeypatch):
    from app import tarefas

    db.criar_vaga(nome="Box 1")
    db.substituir_horarios([(d, "08:00", "17:00") for d in range(7)])
    ag = db.criar_agendamento(
        nome_cliente="Cli", telefone_cliente=TEL,
        inicio="2026-01-05T08:00", fim="2026-01-05T17:00",
    )

    async def entrega(*a, **k):
        return True

    monkeypatch.setattr(whatsapp, "enviar_bolhas", entrega)
    monkeypatch.setattr(tarefas.ia, "completar", _completar_fake("Lembra de confirmar?"))

    asyncio.run(tarefas._disparar_lembrete(ag, 0))

    atualizado = db.get_agendamento(ag.id)
    assert atualizado.aguardando_confirmacao == 1
    assert atualizado.lembretes_enviados == 1

    bolhas = agente.historico_para_bolhas(db.get_conversa(db.resolver_chave_conversa(TEL)))
    assert bolhas[-1]["quem"] == "bot"
    assert "confirmar" in bolhas[-1]["texto"]


def _completar_fake(resposta):
    async def fake(system, user, **k):
        return resposta
    return fake


# ---------------------------------------------------------------------------
# Confirmação propriamente dita
# ---------------------------------------------------------------------------


def test_confirmar_marca_status_e_horario():
    ag = db.criar_agendamento(
        nome_cliente="Cli", telefone_cliente=TEL,
        inicio="2026-01-05T08:00", fim="2026-01-05T17:00",
    )
    assert db.confirmar_agendamento(ag.id)
    at = db.get_agendamento(ag.id)
    assert at.status == "confirmado"
    assert at.confirmado_em
    assert at.aguardando_confirmacao == 0


def test_tool_confirmar_usa_o_dono():
    from app import tools

    ag = db.criar_agendamento(
        nome_cliente="Cli", telefone_cliente=TEL,
        inicio="2026-01-05T08:00", fim="2026-01-05T17:00",
    )
    dono = db.get_config().telefone_dono
    r = tools.confirmar_agendamento(ag.id, telefone_solicitante=dono)
    assert r.get("ok")
    assert db.get_agendamento(ag.id).status == "confirmado"


def test_tool_confirmar_recusa_terceiro():
    from app import tools

    ag = db.criar_agendamento(
        nome_cliente="Cli", telefone_cliente=TEL,
        inicio="2026-01-05T08:00", fim="2026-01-05T17:00",
    )
    r = tools.confirmar_agendamento(ag.id, telefone_solicitante="5511999999999")
    assert r == auth.NEGADO_PROPRIO
    assert db.get_agendamento(ag.id).status == "ativo"