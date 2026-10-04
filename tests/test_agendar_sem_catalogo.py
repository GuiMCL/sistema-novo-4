"""A IA parava de agendar porque `servico_id` era obrigatório na tool.

Com o catálogo de serviços vazio — o estado real de quem nunca cadastrou
serviço no /admin — `listar_servicos` devolvia `[]` e `agendar` exigia
`servico_id`. O modelo então perguntava ao cliente qual serviço era:

    "Não consegui concluir porque preciso identificar o serviço corretamente.
     Pode me dizer se é uma avaliação de suspensão ou outro serviço?"

O cliente não conhece o catálogo, então a conversa travava e nada era
registrado. `db.criar_agendamento` já aceitou `servico_id=None` com texto livre
(o caminho do formulário manual); a tool é que não aceitava.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import auth, db, notificacoes, tools

TEL = "554491151238"


@pytest.fixture
def oficina(monkeypatch):
    """Expediente + uma vaga, catálogo de serviços VAZIO (como em produção)."""
    db.criar_vaga(nome="Box 1")
    db.substituir_horarios([(d, "08:00", "17:00") for d in range(7)])
    db.update_config(telefone_dono="5544999999999")
    monkeypatch.setattr(notificacoes, "notificar_dono", lambda *a, **k: None)
    token = auth.solicitante_ctx.set(TEL)
    yield
    auth.solicitante_ctx.reset(token)


def _dia():
    return (date.today() + timedelta(days=2)).isoformat()


def test_catalogo_vazio_agenda_com_texto_livre(oficina):
    assert db.listar_servicos_ativos() == [], "o cenário depende do catálogo vazio"

    r = tools.agendar("Joao Silva", _dia(), servico_nome="avaliacao de suspensao")

    assert r.get("ok"), r
    ag = r["agendamento"]
    assert ag["status"] == "ativo"
    assert ag["servico_id"] is None
    assert ag["servico_nome"] == "avaliacao de suspensao"
    assert ag["nome_cliente"] == "Joao Silva"


def test_lembrete_nao_sai_com_servico_em_branco(oficina):
    """`nome_servico` precisa cair no texto livre, senão o cliente recebe
    "confirmar seu horário ... para ." """
    tools.agendar("Joao Silva", _dia(), servico_nome="avaliacao de suspensao")
    ag = db.agendamentos_do_telefone(TEL)[-1]
    assert db.nome_servico(ag) == "avaliacao de suspensao"


def test_com_catalogo_o_servico_escolhido_vence(oficina):
    s = db.criar_servico(nome="Suspensao", descricao="", valor=150, duracao_min=60)
    outra = db.criar_servico(nome="Freios", descricao="", valor=200, duracao_min=90)

    r = tools.agendar("Joao Silva", _dia(), servico_id=s.id, servico_nome="avaliacao de suspensao")

    assert r.get("ok"), r
    ag = r["agendamento"]
    # com catálogo, o id escolhe; o nome fica preenchido e o texto do cliente
    # vira descrição (o que o cliente pediu fica registrado mesmo com catálogo)
    assert ag["servico_id"] == s.id
    assert ag["servico_nome"] == "Suspensao"
    assert ag["descricao"] == "avaliacao de suspensao"
    assert db.nome_servico(ag) == "Suspensao"
    assert db.get_servico(outra.id) is not None


def test_servico_id_invalido_ainda_avisa(oficina):
    r = tools.agendar("Joao Silva", _dia(), servico_id=9999)
    assert "erro" in r
    assert "9999" not in r["erro"]  # mensagem é pro cliente, nãoTechnical


def test_sem_nome_do_cliente_ainda_bloqueia(oficina):
    r = tools.agendar("", _dia(), servico_nome="qualquer coisa")
    assert "Nome" in r["erro"]


def test_a_tool_nao_exige_servico_id_no_schema():
    """A assinatura é o que o modelo enxerga: `servico_id` como obrigatório
    faz o modelo inventar pergunta sobre serviço antes de agendar."""
    import inspect

    p = inspect.signature(tools.agendar).parameters
    assert p["servico_id"].default is None
    assert p["servico_nome"].default == ""
    # obrigatórios antes dos opcionais, senão nem compila
    obrigatorios = [n for n, v in p.items() if v.default is inspect.Parameter.empty]
    assert obrigatorios == ["nome_cliente", "data"]


def test_docstring_manda_agendar_sem_perguntar():
    doc = tools.agendar.__doc__ or ""
    assert "NÃO pergunte ao cliente qual" in doc
    assert "servico_id` é OPCIONAL" in doc