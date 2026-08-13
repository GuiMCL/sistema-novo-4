"""Contexto estruturado do cliente injetado no system prompt (anti perda de contexto)."""

from app import agente, auth, db, tools
from tests.conftest import JID_CLIENTE, TEL_CLIENTE, criar_servico


def _solicitante_cliente():
    return auth.solicitante_ctx.set(JID_CLIENTE)


def test_contexto_sem_agendamento():
    token = agente._CONTATO_CTX.set(JID_CLIENTE)
    try:
        bloco = agente._contexto_cliente()
    finally:
        agente._CONTATO_CTX.reset(token)
    assert "Agendamento ativo: NÃO" in bloco
    assert "Nome cadastrado" in bloco


def test_contexto_traz_agendamento_existente():
    servico = criar_servico("Troca do condensador do ar-condicionado")
    token_auth = _solicitante_cliente()
    try:
        r = tools.agendar(
            servico_id=servico.id,
            nome_cliente="Adrieli",
            data="2026-08-18",
            veiculo="Volkswagen Gol",
            placa="ALX5946",
        )
    finally:
        auth.solicitante_ctx.reset(token_auth)
    assert r.get("ok") is True

    token = agente._CONTATO_CTX.set(JID_CLIENTE)
    try:
        bloco = agente._contexto_cliente()
    finally:
        agente._CONTATO_CTX.reset(token)

    assert "Adrieli" in bloco
    assert "Troca do condensador" in bloco
    assert "2026-08-18" in bloco
    assert "Volkswagen Gol" in bloco
    assert "ALX5946" in bloco
    assert "Status: ativo" in bloco
    assert "novo agendamento" in bloco.lower()


def test_system_prompt_inclui_contexto_e_regras():
    token_auth = _solicitante_cliente()
    try:
        db.upsert_cliente(TEL_CLIENTE, "Adrieli")
        token = agente._CONTATO_CTX.set(JID_CLIENTE)
        try:
            prompt = agente._system_prompt()
        finally:
            agente._CONTATO_CTX.reset(token)
    finally:
        auth.solicitante_ctx.reset(token_auth)

    assert "Contexto do cliente" in prompt
    assert "Adrieli" in prompt
    assert "perguntadas de novo" in prompt
    assert "Mensagens curtas" in prompt
    assert "atualizar_observacoes" in prompt
