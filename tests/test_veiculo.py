"""Dados de veículo em agendamento existente + dias do próprio cliente na sugestão."""

from datetime import datetime

import pytest

from app import auth, db, tools
from tests.conftest import criar_servico

MANHA = datetime(2026, 8, 15, 9, 0)


@pytest.fixture
def sab_manha(monkeypatch):
    monkeypatch.setattr(tools, "_agora_local", lambda: MANHA)


def _agendar(cliente):
    servico = criar_servico()
    return tools.agendar(
        servico_id=servico.id, nome_cliente="Luiz Carlos Regina", data="2026-08-19"
    )["agendamento"]


def test_atualizar_dados_veiculo_anexa_ao_existente(sab_manha, cliente):
    ag = _agendar(cliente)
    r1 = tools.atualizar_dados_veiculo(ag["id"], veiculo="Onix 2012/2013")
    assert r1.get("ok") is True
    r2 = tools.atualizar_dados_veiculo(ag["id"], placa="awg4f79")
    final = r2["agendamento"]
    assert final["veiculo"] == "Onix 2012/2013"
    assert final["placa"] == "AWG4F79"
    # nada de novo criado
    assert len(db.listar_agendamentos()) == 1


def test_atualizar_dados_veiculo_negado_estranho(sab_manha, cliente):
    ag = _agendar(cliente)
    token = auth.solicitante_ctx.set("5545999990009@s.whatsapp.net")
    try:
        r = tools.atualizar_dados_veiculo(ag["id"], placa="AWG4F79")
    finally:
        auth.solicitante_ctx.reset(token)
    assert "erro" in r


def test_atualizar_dados_veiculo_exige_algum_campo(sab_manha, cliente):
    ag = _agendar(cliente)
    r = tools.atualizar_dados_veiculo(ag["id"])
    assert "erro" in r


def test_atualizar_dados_veiculo_negado_apos_cancelado(sab_manha, cliente):
    ag = _agendar(cliente)
    tools.cancelar(ag["id"])
    r = tools.atualizar_dados_veiculo(ag["id"], placa="AWG4F79")
    assert "erro" in r


def test_sugestao_separa_dias_do_cliente(sab_manha, cliente):
    criar_servico()
    from tests.conftest import criar_vaga

    criar_vaga("Box 1")
    tools.agendar(
        servico_id=1, nome_cliente="Luiz Carlos Regina", data="2026-08-19"
    )
    r = tools.consultar_horarios_disponiveis()
    livres = [d["data"] for d in r["dias_disponiveis"]]
    proprios = [d["data"] for d in r["dias_do_cliente"]]
    assert "2026-08-19" not in livres          # não vira vaga nova
    assert "2026-08-19" in proprios            # aparece como reserva do cliente
    assert r["dias_do_cliente"][0]["id"] >= 1


def test_consulta_do_proprio_dia_avisa_reservado(sab_manha, cliente):
    criar_servico()
    from tests.conftest import criar_vaga

    criar_vaga("Box 1")
    tools.agendar(
        servico_id=1, nome_cliente="Luiz Carlos Regina", data="2026-08-19"
    )
    r = tools.consultar_horarios_disponiveis(data="2026-08-19")
    assert r["dia_ja_reservado"] is True
    assert "reservado" in r["aviso"].lower()
