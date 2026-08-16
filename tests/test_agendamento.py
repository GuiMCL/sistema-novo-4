"""Disponibilidade e agendamento — regressões de expediente, duplicidade e contexto."""

from datetime import datetime

import pytest

from app import auth, db, tools
from tests.conftest import criar_servico, criar_vaga

# Segunda-feira 17/08/2026, 18:01 — expediente padrão encerra às 18:00.
DEPOIS_DE_FECHAR = datetime(2026, 8, 17, 18, 1)
MANHA = datetime(2026, 8, 17, 9, 0)


@pytest.fixture
def seg_18h(monkeypatch):
    monkeypatch.setattr(tools, "_agora_local", lambda: DEPOIS_DE_FECHAR)


@pytest.fixture
def seg_manha(monkeypatch):
    monkeypatch.setattr(tools, "_agora_local", lambda: MANHA)


def test_disponibilidade_omite_hoje_apos_fechamento(seg_18h):
    r = tools.consultar_horarios_disponiveis()
    datas = [d["data"] for d in r["dias_disponiveis"]]
    assert "2026-08-17" not in datas  # hoje encerrado
    assert "2026-08-18" in datas      # amanhã (terça) é válido
    assert "2026-08-22" not in datas  # sábado fechado
    assert "2026-08-23" not in datas  # domingo fechado


def test_disponibilidade_com_data_marca_pode_agendar_falso_hoje_fechado(seg_18h):
    r = tools.consultar_horarios_disponiveis(data="2026-08-17")
    assert r["pode_agendar"] is False
    assert "expediente" in r.get("aviso", "").lower()


def test_disponibilidade_com_data_futura_marca_pode_agendar_true(seg_18h):
    r = tools.consultar_horarios_disponiveis(data="2026-08-18")
    assert r["pode_agendar"] is True


def test_agendar_hoje_depois_do_fechamento_rejeita(seg_18h, cliente):
    servico = criar_servico()
    r = tools.agendar(
        servico_id=servico.id,
        nome_cliente="Adrieli",
        data="2026-08-17",
        veiculo="VW Gol",
        placa="ALX5946",
    )
    assert "erro" in r
    assert "expediente" in r["erro"].lower()
    assert db.listar_agendamentos() == []


def test_agendar_hoje_de_manha_aceita(seg_manha, cliente):
    servico = criar_servico()
    r = tools.agendar(
        servico_id=servico.id,
        nome_cliente="Adrieli",
        data="2026-08-17",
        veiculo="VW Gol",
        placa="ALX5946",
    )
    assert r.get("ok") is True
    assert len(db.listar_agendamentos()) == 1


def test_agendar_data_passada_rejeita(cliente):
    servico = criar_servico()
    r = tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-10")
    assert "passada" in r.get("erro", "").lower()


def test_agendar_dia_fechado_bloqueio_rejeita(seg_manha, cliente):
    servico = criar_servico()
    db.criar_bloqueio(data="2026-08-17", inicio=None, fim=None, motivo="imprevisto")
    r = tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-17")
    assert "fechada" in r.get("erro", "").lower()
    assert db.listar_agendamentos() == []


def test_agendar_dia_sem_expediente_rejeita(seg_manha, cliente):
    servico = criar_servico()
    r = tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-22")
    assert "erro" in r
    assert db.listar_agendamentos() == []


def test_agendar_lotado_rejeita(seg_manha, cliente):
    criar_vaga("Box 1")
    servico = criar_servico()
    r1 = tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")
    assert r1.get("ok") is True
    # segunda pessoa tenta o mesmo dia
    token = auth.solicitante_ctx.set("5545999990002@s.whatsapp.net")
    try:
        r2 = tools.agendar(servico_id=servico.id, nome_cliente="Outro", data="2026-08-18")
    finally:
        auth.solicitante_ctx.reset(token)
    assert "vagas" in r2.get("erro", "").lower()


def test_agendar_nome_generico_rejeita(cliente):
    servico = criar_servico()
    r = tools.agendar(servico_id=servico.id, nome_cliente="cliente", data="2026-08-18")
    assert "nome" in r.get("erro", "").lower()


def test_agendar_avisa_quando_ja_existe_agendamento(seg_manha, cliente):
    servico = criar_servico()
    r1 = tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")
    assert r1.get("ok") is True

    # cliente traz sintoma novo → tool NÃO cria outro agendamento
    r2 = tools.agendar(
        servico_id=servico.id,
        nome_cliente="Adrieli",
        data="2026-08-19",
        observacoes="vazando óleo",
    )
    assert "aviso" in r2
    assert r2["agendamentos_existentes"]
    assert len(db.listar_agendamentos()) == 1  # nada novo criado


def test_agendar_confirmar_existente_cria_novo(seg_manha, cliente):
    servico = criar_servico()
    tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")
    r = tools.agendar(
        servico_id=servico.id,
        nome_cliente="Adrieli",
        data="2026-08-19",
        confirmar_existente=True,
    )
    assert r.get("ok") is True
    assert len(db.listar_agendamentos()) == 2


def test_agendar_rejeita_mesmo_dia_do_agendamento_existente(seg_manha, cliente):
    servico = criar_servico()
    tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")
    r = tools.agendar(
        servico_id=servico.id,
        nome_cliente="Adrieli",
        data="2026-08-18",
        confirmar_existente=True,
    )
    assert "mesma data" in r.get("erro", "").lower()
    assert len(db.listar_agendamentos()) == 1


def test_disponibilidade_com_data_nao_lota_dia_proprio(seg_manha, cliente):
    """O dia que o cliente JÁ TEM reservado não aparece lotado para ele."""
    criar_vaga("Box 1")
    servico = criar_servico()
    tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")

    r = tools.consultar_horarios_disponiveis(data="2026-08-18")
    assert r["dia_ja_reservado"] is True
    assert r["pode_agendar"] is True
    assert r["vagas_livres"] >= 1  # a vaga do próprio dia conta como livre p/ ele
    assert r["meus_agendamentos_no_dia"]


def test_disponibilidade_sem_data_omite_dia_proprio(seg_manha, cliente):
    criar_vaga("Box 1")
    servico = criar_servico()
    tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")

    r = tools.consultar_horarios_disponiveis()
    datas = [d["data"] for d in r["dias_disponiveis"]]
    assert "2026-08-18" not in datas  # já é o dia do próprio cliente


def test_dia_proprio_nao_lota_para_outro_cliente(seg_manha, cliente):
    """Para OUTRO cliente, o dia continua lotado (vaga única ocupada)."""
    criar_vaga("Box 1")
    servico = criar_servico()
    tools.agendar(servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18")

    token = auth.solicitante_ctx.set("5545999990002@s.whatsapp.net")
    try:
        r = tools.consultar_horarios_disponiveis(data="2026-08-18")
    finally:
        auth.solicitante_ctx.reset(token)
    assert r["dia_ja_reservado"] is False
    assert r["vagas_livres"] == 0
    assert r["dia_livre"] is False


def test_atualizar_observacoes_anexa_sintomas(cliente):
    servico = criar_servico()
    r = tools.agendar(
        servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18", observacoes="inicial"
    )
    ag_id = r["agendamento"]["id"]

    r1 = tools.atualizar_observacoes(ag_id, "Está vazando óleo")
    assert r1.get("ok") is True
    r2 = tools.atualizar_observacoes(ag_id, "Barulho alto como correia dentada")
    obs = r2["agendamento"]["observacoes"]
    assert "vazando óleo" in obs
    assert "correia dentada" in obs


def test_atualizar_observacoes_negado_para_estranho(dono):
    servico = criar_servico()
    token = auth.solicitante_ctx.set("5545999990001@s.whatsapp.net")
    try:
        r = tools.agendar(
            servico_id=servico.id, nome_cliente="Adrieli", data="2026-08-18"
        )
    finally:
        auth.solicitante_ctx.reset(token)
    ag_id = r["agendamento"]["id"]
    # dono pode (pode_mexer_no_agendamento libera o dono)
    r_ok = tools.atualizar_observacoes(ag_id, "sintoma do dono")
    assert r_ok.get("ok") is True
    # terceiro (nem dono, nem cliente) → negado
    token = auth.solicitante_ctx.set("5545999990009@s.whatsapp.net")
    try:
        r_neg = tools.atualizar_observacoes(ag_id, "sintoma de estranho")
    finally:
        auth.solicitante_ctx.reset(token)
    assert "erro" in r_neg
