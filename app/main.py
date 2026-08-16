"""Aplicação principal — agente WhatsApp + painel /admin + atendimento Chatwoot-like + servidor MCP.

  /webhook/whatsapp/receberMensagem — pipeline do agente
  /admin — painel de configuração
  /atendimento — interface de atendimento tipo Chatwoot
  /mcp — endpoint MCP (streamable-http)
  /login / /logout — autenticação de usuários
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth, evolution, tarefas
from .admin import router as admin_router
from .atendimento import router as atendimento_router
from .config import settings
from .tools import mcp
from .whatsapp import router as whatsapp_router

log = logging.getLogger("main")

# App ASGI do MCP
mcp_app = mcp.streamable_http_app()


class SolicitanteMiddleware:
    """Middleware ASGI puro: extrai o telefone do solicitante e injeta no contextvar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        sol = None
        qs = scope.get("query_string", b"").decode()
        if qs:
            sol = parse_qs(qs).get("solicitante", [None])[0]
        if not sol:
            headers = dict(scope.get("headers") or [])
            sol = headers.get(b"x-solicitante-telefone", b"").decode() or None

        token = auth.solicitante_ctx.set(sol)
        try:
            await self.app(scope, receive, send)
        finally:
            auth.solicitante_ctx.reset(token)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Garante todas as instâncias registradas no banco na Evolution
    bootstrap = asyncio.create_task(
        evolution.garantir_multi_instancias(
            f"{settings.webhook_url}?token={settings.webhook_token}"
        )
    )
    worker = asyncio.create_task(tarefas.worker())  # ações proativas + lembretes
    async with mcp.session_manager.run():
        yield
    worker.cancel()
    bootstrap.cancel()


app = FastAPI(title="Revi Atende — Sistema de Agendamentos", lifespan=lifespan)

# Sessão criptografada para login de usuários
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=86400 * 7)

app.include_router(admin_router)
app.include_router(atendimento_router)
app.include_router(whatsapp_router)
app.mount("/mcp", SolicitanteMiddleware(mcp_app))


class _StaticNoCache(StaticFiles):
    """JS/CSS com Cache-Control: no-cache — o navegador revalida a cada carga,
    então mudanças de front end aparecem sem Ctrl+F5 nem troca de cache."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def file_response(self, *args, **kwargs):
        resposta = super().file_response(*args, **kwargs)
        if resposta.path.lower().endswith((".js", ".css")):
            resposta.headers["Cache-Control"] = "no-cache"
        return resposta


app.mount("/static", _StaticNoCache(directory="app/static"), name="static")


@app.get("/")
def home():
    return RedirectResponse("/admin")


@app.get("/health")
def health():
    return {"status": "ok"}
