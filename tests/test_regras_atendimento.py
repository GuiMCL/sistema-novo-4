import asyncio
from datetime import datetime
from types import SimpleNamespace

from app import db, tools


def test_feriado_bloqueado_nao_aparece_nem_pode_ser_consultado(monkeypatch, cliente):
    monkeypatch.setattr(tools, "_agora_local", lambda: datetime(2026, 8, 17, 9, 0))
    db.criar_bloqueio("2026-08-18", inicio=None, fim=None, motivo="feriado")

    sugestao = tools.consultar_horarios_disponiveis()
    assert "2026-08-18" not in [d["data"] for d in sugestao["dias_disponiveis"]]

    consulta = tools.consultar_horarios_disponiveis(data="2026-08-18")
    assert consulta["pode_agendar"] is False
    assert "feriado" in consulta["aviso"].lower()


def test_destinos_oficiais_exibem_apenas_contatos_cadastrados():
    destinos = {d["nome"]: d for d in tools.listar_destinos_transferencia()}
    assert destinos["financeiro"]["telefone"] == "5544888574708"
    assert destinos["patio_oficina"]["telefone"] == "5544888335636"
    assert destinos["financeiro"]["mensagem"] == "Contato: 5544888574708"


def test_contato_financeiro_apenas_informa_o_numero(cliente):
    resultado = tools.transferir_atendimento("financeiro")

    assert resultado["ok"] is True
    assert resultado["telefone_contato"] == "5544888574708"
    assert resultado["mensagem"] == "Contato: 5544888574708"


def test_worker_nao_dispara_lembrete_de_24_horas(monkeypatch):
    from app import tarefas

    chamadas = []
    monkeypatch.setattr(
        tarefas.db,
        "get_lembrete_config",
        lambda: SimpleNamespace(ativo=True, horas_antes=48, ativo2=True, horas_antes2=24),
    )
    monkeypatch.setattr(
        tarefas.db,
        "agendamentos_precisando_lembrete",
        lambda agora, horas, stage=0: chamadas.append((horas, stage)) or [],
    )

    async def sem_espera(_segundos):
        return None

    monkeypatch.setattr(tarefas.asyncio, "sleep", sem_espera)
    asyncio.run(tarefas._disparar_lembretes(datetime(2026, 11, 5, 8, 0)))

    assert chamadas == [(24, 0)]


def test_lembrete_considera_apenas_o_dia_seguinte():
    amanha = db.Agendamento(
        status="ativo",
        telefone_cliente="5511999999999",
        nome_cliente="Cliente",
        inicio="2026-11-06T17:00",
        fim="2026-11-06T18:00",
        lembretes_enviados=0,
        lembrete_em_processamento=0,
    )
    dois_dias = db.Agendamento(
        status="ativo",
        telefone_cliente="5511888888888",
        nome_cliente="Outro cliente",
        inicio="2026-11-07T09:00",
        fim="2026-11-07T10:00",
        lembretes_enviados=0,
        lembrete_em_processamento=0,
    )

    with db._lock, db._session() as s:
        s.add(amanha)
        s.add(dois_dias)
        s.commit()

    encontrados = db.agendamentos_precisando_lembrete(
        "2026-11-05T08:00", horas_antes=96, stage=0
    )

    assert encontrados == [amanha]
    assert dois_dias not in encontrados
