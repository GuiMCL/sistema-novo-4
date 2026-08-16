"""Confirmação de agendamento (resposta ao lembrete) — flag e permissões."""

from datetime import datetime

import pytest

from app import auth, db, tools
from tests.conftest import criar_servico

MANHA = datetime(2026, 8, 17, 9, 0)


@pytest.fixture
def seg_manha(monkeypatch):
    monkeypatch.setattr(tools, "_agora_local", lambda: MANHA)


def _agendar(cliente):
    servico = criar_servico()
    return tools.agendar(
        servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18"
    )["agendamento"]


def test_confirmar_agendamento_do_proprio_cliente(seg_manha, cliente):
    ag = _agendar(cliente)
    r = tools.confirmar_agendamento(ag["id"])
    assert r.get("ok") is True
    assert db.get_agendamento(ag["id"]).status == "confirmado"

    # confirmar de novo → já confirmado (sem erro)
    r2 = tools.confirmar_agendamento(ag["id"])
    assert r2.get("ja_confirmado") is True


def test_confirmar_agendamento_negado_para_estranho(seg_manha, cliente):
    ag = _agendar(cliente)
    token = auth.solicitante_ctx.set("5545999990009@s.whatsapp.net")
    try:
        r = tools.confirmar_agendamento(ag["id"])
    finally:
        auth.solicitante_ctx.reset(token)
    assert "erro" in r
    assert db.get_agendamento(ag["id"]).status == "ativo"


def test_confirmar_agendamento_negado_apos_cancelado(seg_manha, cliente):
    ag = _agendar(cliente)
    tools.cancelar(ag["id"])
    r = tools.confirmar_agendamento(ag["id"])
    assert "erro" in r


def test_confirmar_limpa_aguardando_confirmacao(seg_manha, cliente):
    ag = _agendar(cliente)
    with db._lock, db._session() as s:
        a = s.get(db.Agendamento, ag["id"])
        a.aguardando_confirmacao = 1
        s.add(a)
        s.commit()
    tools.confirmar_agendamento(ag["id"])
    assert db.get_agendamento(ag["id"]).aguardando_confirmacao == 0


def test_reagendar_limpa_aguardando_confirmacao(seg_manha, cliente):
    ag = _agendar(cliente)
    with db._lock, db._session() as s:
        a = s.get(db.Agendamento, ag["id"])
        a.aguardando_confirmacao = 1
        s.add(a)
        s.commit()
    r = tools.reagendar(ag["id"], "2026-08-19T09:00")
    assert r.get("ok") is True
    assert db.get_agendamento(ag["id"]).aguardando_confirmacao == 0
