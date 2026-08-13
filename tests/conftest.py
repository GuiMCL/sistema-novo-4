"""Setup dos testes: banco SQLite em diretório temporário + limpeza por teste.

As variáveis de ambiente precisam ser setadas ANTES de qualquer import do
pacote `app` (o engine e o init_db rodam na importação). O `autouse` limpa as
tabelas e desliga o aviso ao dono (sem chamada de rede nos testes).
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="mcp_test_")
os.environ["MCP_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ.setdefault("MCP_TZ", "America/Sao_Paulo")
os.environ.setdefault("ADMIN_PASS", "teste")
os.environ.setdefault("OWNER_PHONE", "5545999990000")

import pytest
from sqlmodel import select

from app import auth, db

TEL_CLIENTE = "5545999990001"
JID_CLIENTE = f"{TEL_CLIENTE}@s.whatsapp.net"


@pytest.fixture(autouse=True)
def banco_limpo():
    with db._lock, db._session() as s:
        for t in (db.Agendamento, db.Bloqueio, db.Tarefa, db.Servico, db.Vaga, db.Cliente, db.Conversa):
            for o in s.exec(select(t)).all():
                s.delete(o)
        s.commit()
    db.update_config(avisar_dono=False)
    yield


@pytest.fixture
def cliente():
    """Remetente = cliente (não-dono) no contextvar, como faz o pipeline."""
    token = auth.solicitante_ctx.set(JID_CLIENTE)
    try:
        yield TEL_CLIENTE
    finally:
        auth.solicitante_ctx.reset(token)


@pytest.fixture
def dono():
    token = auth.solicitante_ctx.set("5545999990000@s.whatsapp.net")
    try:
        yield "5545999990000"
    finally:
        auth.solicitante_ctx.reset(token)


def criar_servico(nome="Troca do condensador", duracao=60):
    return db.criar_servico(nome=nome, descricao=nome, valor=100.0, duracao_min=duracao)


def criar_vaga(nome="Box 1"):
    return db.criar_vaga(nome=nome)
