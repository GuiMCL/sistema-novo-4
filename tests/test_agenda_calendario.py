"""Calendário e agendamento manual em /admin/agenda.

O bug que these tests pegam: o template `partials/agendamentos.html` renderiza
`{{ ag_detalhe | tojson }}`. Se o backend não supplies `ag_detalhe` no contexto,
o Jinja passa `Undefined` pro json.dumps e a página INTEIRA quebra com 500
("Object of type Undefined is not JSON serializable"). Como o template e o
contexto vivem em arquivos diferentes, dá pra deployar um sem o outro — e foi
exatamente o que aconteceu. Estes testes falham se os dois ficarem dessincronizados.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import admin as admin_mod, auth, db
from app import main
from app.main import app

# Segunda-feira futuro: quase toda segunda tem expediente no setup padrão.
SEGUNDA = (date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)).isoformat()


@pytest.fixture
def painel():
    app.dependency_overrides[admin_mod.autenticar_pagina] = lambda: "admin"
    app.dependency_overrides[admin_mod.autenticar] = lambda: "admin"
    app.dependency_overrides[auth.admin_required] = lambda: db.Usuario(
        nome="Teste", email="t@t", papel="admin"
    )
    app.dependency_overrides[auth.login_required] = lambda: db.Usuario(
        nome="Teste", email="t@t", papel="admin"
    )
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def expediente():
    """Um dia de expediente com uma vaga, para o agendamento ser aceito.

    `banco_limpo` do conftest não limpa HorarioFuncionamento, então o fixture
    restaura os horários no fim — sem isso este fixture vaza expediente de
    segunda a sexta para `test_expediente.py` e quebra o cálculo de "hoje".
    """
    antes = [(h.dia_semana, h.inicio, h.fim) for h in db.listar_horarios()]
    db.criar_vaga(nome="Box 1")
    db.substituir_horarios([(d, "08:00", "17:00") for d in range(5)])
    yield
    db.substituir_horarios(antes)


# ---------------------------------------------------------------------------
# A página renderiza (o contexto bate com o template)
# ---------------------------------------------------------------------------


def test_agenda_renderiza_sem_erro(painel):
    r = painel.get("/admin/agenda")
    assert r.status_code == 200, r.text[:2000]


def test_agenda_tem_o_calendario(painel):
    """A grade do mês é o que o dono espera ver."""
    h = painel.get("/admin/agenda").text
    assert 'class="cal-grid"' in h
    assert "cal-toolbar" in h
    assert "cal-nav" in h
    assert "cal-dia" in h


def test_agenda_nao_usa_o_form_antigo_de_horario(painel):
    """O atendimento é por DIA INTEIRO: nada de seletor de slot/horário."""
    h = painel.get("/admin/agenda").text
    assert 'id="agm-inicio"' not in h
    assert 'id="agm-slots"' not in h
    assert 'id="agm-servico"' not in h


def test_agenda_tem_o_formulario_manual_por_dia(painel):
    h = painel.get("/admin/agenda").text
    assert 'id="agm-descricao"' in h
    assert 'id="agm-data"' in h
    assert 'name="servico_nome"' in h


def test_payload_do_calendario_js_e_json_valido(painel, expediente):
    """`window.__CAL__` precisa ter o `detalhe` em JSON de verdade.

    O bloco é um literal JS (`{ detalhe: ..., apenas_ativos: ... }`), então as
    chaves externa não são JSON. Mas o valor de `detalhe` vem de `| tojson` e
    tem que ser JSON de verdade — é o que o calendario.js consome.
    """
    h = painel.get("/admin/agenda").text
    bloco = re.search(r"window\.__CAL__ = \{(.*?)\};", h, re.S)
    assert bloco, "window.__CAL__ ausente"

    detalhe = re.search(r"detalhe:\s*(\{.*?\}),\s*apenas_ativos:", bloco.group(1), re.S)
    assert detalhe, "campo `detalhe` ausente ou malformado"
    assert isinstance(json.loads(detalhe.group(1)), dict)

    assert re.search(r"apenas_ativos:\s*(true|false)", bloco.group(1))


def test_agendamento_aparece_como_chip_no_calendario(painel, expediente):
    db.criar_agendamento(
        nome_cliente="Joao", telefone_cliente="5545999990001",
        inicio=f"{SEGUNDA}T08:00", fim=f"{SEGUNDA}T17:00",
    )
    h = painel.get("/admin/agenda", params={"mes": date.fromisoformat(SEGUNDA).month,
                                           "ano": date.fromisoformat(SEGUNDA).year}).text
    assert "cal-chip" in h
    assert "Joao" in h


def test_navegacao_de_mes_nao_quebra(painel):
    for params in ({"mes": 12, "ano": 2026}, {"mes": 1, "ano": 2026}, {"mes": "x", "ano": "y"}):
        r = painel.get("/admin/agenda", params=params)
        assert r.status_code == 200, f"{params} -> {r.status_code}"


def test_ativos_preserva_o_calendario(painel):
    r = painel.get("/admin/agenda", params={"ativos": "1"})
    assert r.status_code == 200
    assert 'class="cal-grid"' in r.text


# ---------------------------------------------------------------------------
# Detalhe do agendamento no modal
# ---------------------------------------------------------------------------


def test_ag_detalhe_traz_o_appointment_completo(painel, expediente):
    ag = db.criar_agendamento(
        nome_cliente="Maria", telefone_cliente="5545999990002",
        inicio=f"{SEGUNDA}T08:00", fim=f"{SEGUNDA}T17:00",
        descricao="ruido no pneu dianteiro", veiculo="Gol", placa="abc1d23",
    )
    h = painel.get("/admin/agenda").text
    bloco = re.search(r"window\.__CAL__ = \{(.*?)\};", h, re.S).group(1)
    detalhe = json.loads(
        re.search(r"detalhe:\s*(\{.*?\}),\s*apenas_ativos:", bloco, re.S).group(1)
    )
    d = detalhe[str(ag.id)]
    assert d["nome"] == "Maria"
    assert d["descricao"] == "ruido no pneu dianteiro"
    assert d["veiculo"] == "Gol"
    # `db.criar_agendamento` não normaliza: o .upper() da placa é da rota.
    assert d["placa"] == "abc1d23"
    assert d["vaga"] == "Box 1"


def test_agendamento_sem_descricao_nao_quebra_o_json(painel, expediente):
    db.criar_agendamento(
        nome_cliente="Sem descricao", telefone_cliente="5545999990003",
        inicio=f"{SEGUNDA}T08:00", fim=f"{SEGUNDA}T17:00",
    )
    assert painel.get("/admin/agenda").status_code == 200


# ---------------------------------------------------------------------------
# Agendamento manual: fluxo por dia
# ---------------------------------------------------------------------------


def test_agendar_manualmente_por_dia(painel, expediente):
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "Carlos",
        "telefone_cliente": "5545999990004",
        "data": SEGUNDA,
        "descricao": "vai batendo na hora de frear",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]

    lista = db.listar_agendamentos()
    assert len(lista) == 1
    ag = lista[0]
    # Dia inteiro: início e fim vêm do expediente, não de um horário escolhido.
    assert ag.inicio == f"{SEGUNDA}T08:00"
    assert ag.fim == f"{SEGUNDA}T17:00"
    assert ag.descricao == "vai batendo na hora de frear"
    assert ag.vaga_id is not None


def test_agendar_por_dia_auto_atribui_vaga(painel, expediente):
    painel.post("/admin/agendamento", data={
        "nome_cliente": "Auto Vaga",
        "telefone_cliente": "5545999990005",
        "data": SEGUNDA,
        "descricao": "teste",
    })
    assert db.listar_agendamentos()[0].vaga_id is not None


def test_agendar_com_servico_e_descricao(painel, expediente):
    srv = db.criar_servico(nome="Troca de oleo", descricao="", valor=80.0, duracao_min=60)
    painel.post("/admin/agendamento", data={
        "nome_cliente": "Com Servico",
        "telefone_cliente": "5545999990006",
        "data": SEGUNDA,
        "descricao": "quer oleo 5w30",
        "servico_id": srv.id,
        "servico_nome": "Troca de oleo",
    })
    ag = db.listar_agendamentos()[0]
    assert ag.servico_id == srv.id
    assert ag.servico_nome == "Troca de oleo"


def test_agendar_sem_servico_nem_descricao_rejeita(painel, expediente):
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "Sem Info",
        "telefone_cliente": "5545999990007",
        "data": SEGUNDA,
    })
    assert r.status_code == 400
    assert db.listar_agendamentos() == []


def test_agendar_sem_nome_rejeita(painel, expediente):
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "   ",
        "telefone_cliente": "5545999990008",
        "data": SEGUNDA,
        "descricao": "x",
    })
    assert r.status_code == 400


def test_agendar_data_invalida_rejeita(painel, expediente):
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "Data Ruim",
        "telefone_cliente": "5545999990009",
        "data": "32/13/2026",
        "descricao": "x",
    })
    assert r.status_code == 400
    assert "Data" in r.json()["detail"]


def test_agendar_dia_sem_expediente_rejeita(painel):
    domingo = (date.today() + timedelta(days=(6 - date.today().weekday()) % 7 or 7)).isoformat()
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "Sem Expediente",
        "telefone_cliente": "5545999990010",
        "data": domingo,
        "descricao": "x",
    })
    assert r.status_code == 400


def test_agendar_sem_vaga_retorna_409(painel, expediente):
    """Duas reservas no mesmo dia competem pela mesma vaga."""
    painel.post("/admin/agendamento", data={
        "nome_cliente": "Primeiro", "telefone_cliente": "5545999990011",
        "data": SEGUNDA, "descricao": "x",
    })
    r = painel.post("/admin/agendamento", data={
        "nome_cliente": "Segundo", "telefone_cliente": "5545999990012",
        "data": SEGUNDA, "descricao": "x",
    })
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Persistência do novo campo
# ---------------------------------------------------------------------------


def test_coluna_descricao_existe_no_banco():
    from sqlmodel import Session, inspect

    with db._session() as s:
        cols = {c["name"] for c in inspect(Session.get_bind(s, db.Agendamento)).get_columns("agendamento")}
    assert "descricao" in cols


# ---------------------------------------------------------------------------
# Grafo de módulos do front
# ---------------------------------------------------------------------------


def test_todo_import_de_admin_js_existe_em_disco():
    """Um `import` de arquivo ausente quebra o grafo INTEIRO de ES modules:
    o navegador não executa nenhum outro import do admin.js e o painel morre
    sem erro visível — só o 404 do módulo faltando no log. Já aconteceu com
    lembretes.js."""
    import pathlib
    import re

    raiz = pathlib.Path(main.__file__).parent / "static" / "admin" / "js"
    fonte = (raiz / "admin.js").read_text(encoding="utf-8")
    faltando = [
        m for m in re.findall(r"import\s+'\./([^']+)'", fonte)
        if not (raiz / m).is_file()
    ]
    assert faltando == [], f"admin.js importa módulo inexistente: {faltando}"


def test_calendario_e_agendamento_entram_no_admin_js():
    """O calendário e o agendamento manual são carregados por admin.js."""
    import pathlib

    raiz = pathlib.Path(main.__file__).parent / "static" / "admin" / "js"
    fonte = (raiz / "admin.js").read_text(encoding="utf-8")
    assert "import './calendario.js';" in fonte
    assert "import './agendamento.js';" in fonte


def test_ids_usados_pelo_js_existem_no_html_da_agenda(painel):
    """Cada getElementById do JS precisa achar o elemento no HTML renderizado.
    Sem optional chaining, um id faltando joga o módulo inteiro fora."""
    import pathlib
    import re

    h = painel.get("/admin/agenda").text
    ids_html = set(re.findall(r'id="([^"]+)"', h))
    raiz = pathlib.Path(main.__file__).parent / "static" / "admin" / "js"

    for nome in ("agendamento.js", "calendario.js"):
        fonte = (raiz / nome).read_text(encoding="utf-8")
        for elemento in set(re.findall(r"getElementById\('([^']+)'\)", fonte)):
            assert elemento in ids_html, f"{nome} procura #{elemento}, que não existe no HTML"