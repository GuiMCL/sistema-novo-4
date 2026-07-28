"""Camada de autorização — multi-usuário + dono legado.

Dois sistemas coexistem:
  1. Usuários do sistema (tabela Usuario) — autenticação por sessão web.
  2. Dono do WhatsApp (telefone) — autenticação por contextvar (pipeline).

Regra de ouro: quem é o solicitante NÃO é decidido pelo modelo. O pipeline do
WhatsApp grava o remoteJid do remetente no contextvar antes de rodar o agente.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
from typing import Callable

from fastapi import HTTPException, Request, status

from . import db

# Preenchido por requisição (middleware em main.py ou pipeline em whatsapp.py).
solicitante_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "solicitante", default=None
)

# Usuário logado na sessão web.
_usuario_ctx: contextvars.ContextVar[db.Usuario | None] = contextvars.ContextVar(
    "usuario", default=None
)


def requester(telefone_arg: str | None = None) -> str | None:
    """Telefone efetivo do solicitante: contextvar tem prioridade."""
    return solicitante_ctx.get() or telefone_arg


def eh_dono(telefone_solicitante: str | None = None) -> bool:
    from .phone import mesmo_numero
    return mesmo_numero(requester(telefone_solicitante), db.get_config().telefone_dono)


def pode_mexer_no_agendamento(telefone_solicitante: str | None, agendamento_id: int) -> bool:
    from .phone import mesmo_numero
    tel = requester(telefone_solicitante)
    if mesmo_numero(tel, db.get_config().telefone_dono):
        return True
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        return False
    return mesmo_numero(ag.telefone_cliente, tel)


# ---------------------------------------------------------------------------
# Hash de senha (sem passlib — hashlib + salt p/ evitar dependência extra)
# ---------------------------------------------------------------------------

_SALT = os.getenv("PASSWORD_SALT", "myautoatend2024")


def hash_senha(senha: str) -> str:
    return hashlib.sha256(f"{_SALT}:{senha}".encode()).hexdigest()


def verificar_senha(senha: str, hash_: str) -> bool:
    return hash_senha(senha) == hash_


# ---------------------------------------------------------------------------
# Login / sessão
# ---------------------------------------------------------------------------


def autenticar_usuario(email: str, senha: str) -> db.Usuario | None:
    u = db.get_usuario_por_email(email)
    if not u or not u.ativo:
        return None
    if not verificar_senha(senha, u.senha_hash):
        return None
    db.editar_usuario(u.id, ultimo_login=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
    return u


def login_required(request: Request) -> db.Usuario:
    """Dependência FastAPI: exige sessão ativa. Redireciona p/ login em páginas HTML."""
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Faça login primeiro.")
    u = db.get_usuario(usuario_id)
    if not u or not u.ativo:
        request.session.clear()
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inativo ou inexistente.")
    return u


def admin_required(request: Request) -> db.Usuario:
    """Apenas admins."""
    u = login_required(request)
    if u.papel != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Apenas administradores.")
    return u


def papel_required(*papeis: str) -> Callable:
    """Decorator de permissão por papel."""
    def checker(request: Request) -> db.Usuario:
        u = login_required(request)
        if u.papel not in papeis:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Acesso restrito a: {', '.join(papeis)}")
        return u
    return checker


# ---------------------------------------------------------------------------
# Respostas padronizadas de negação (para ferramentas MCP)
# ---------------------------------------------------------------------------

NEGADO_DONO = {"erro": "Apenas o dono pode executar esta ação."}
NEGADO_PROPRIO = {"erro": "Você só pode alterar agendamentos feitos com o seu próprio número."}
NEGADO_SEM_SOLICITANTE = {"erro": "Não foi possível identificar o telefone do solicitante."}
