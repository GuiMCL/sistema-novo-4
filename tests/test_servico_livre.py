"""Serviço digitado livre no painel: fica salvo no agendamento sem vínculo
com o catálogo (servico_id NULL + servico_nome)."""

from app import db
from tests.conftest import criar_servico, criar_vaga


def _novo(**extra):
    dados = dict(
        servico_nome="Troca de óleo do motor",
        telefone_cliente="5545999990001",
        nome_cliente="Luiz",
        inicio="2026-08-17T08:00",
        fim="2026-08-17T09:00",
    )
    dados.update(extra)
    return db.criar_agendamento(**dados)


def test_criar_agendamento_com_servico_livre():
    criar_vaga("Box 1")
    ag = _novo(servico_id=None)
    assert ag is not None
    assert ag.servico_id is None
    assert ag.servico_nome == "Troca de óleo do motor"
    assert db.nome_servico(ag) == "Troca de óleo do motor"


def test_nome_servico_cai_no_catalogo_quando_ha_id():
    s = criar_servico(nome="Suspensão")
    criar_vaga("Box 1")
    ag = _novo(servico_id=s.id, servico_nome="")
    assert ag.servico_id == s.id
    assert db.nome_servico(ag) == "Suspensão"


def test_migracao_servico_id_vira_anulavel():
    with db.engine.connect() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS agendamento")
        conn.exec_driver_sql(
            """
            CREATE TABLE agendamento (
                id INTEGER PRIMARY KEY,
                servico_id INTEGER NOT NULL,
                telefone_cliente VARCHAR,
                nome_cliente VARCHAR,
                inicio VARCHAR,
                fim VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'ativo'
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO agendamento (servico_id, telefone_cliente, nome_cliente, "
            "inicio, fim) VALUES (7, '5545999990001', 'Maria', "
            "'2026-08-17T08:00', '2026-08-17T09:00')"
        )
        conn.commit()

    db._migrar()

    with db.engine.connect() as conn:
        cols = {r[1]: r for r in conn.exec_driver_sql("PRAGMA table_info(agendamento)")}
        assert "servico_nome" in cols
        assert cols["servico_id"][3] == 0  # notnull agora falso
        assert conn.exec_driver_sql(
            "SELECT servico_id, nome_cliente FROM agendamento"
        ).fetchall() == [(7, "Maria")]

    criar_vaga("Box 1")
    ag = _novo(servico_id=None, inicio="2026-08-18T08:00", fim="2026-08-18T09:00")
    assert ag is not None and ag.servico_id is None
