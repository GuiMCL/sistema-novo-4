"""Configurações de processo (lidas de variáveis de ambiente).

Tudo que muda entre ambientes (caminho do banco, credenciais do painel,
telefone do dono, fuso) vem daqui. No Docker são injetadas pelo compose.
"""

from __future__ import annotations

import hashlib
import os


class Settings:
    # SQLite: caminho do arquivo. No container aponta para um volume (/data).
    db_path: str = os.getenv("MCP_DB_PATH", "agendamentos.db")

    # Painel /admin (HTTP Basic). Trocar em produção.
    admin_user: str = os.getenv("ADMIN_USER", "admin")
    admin_pass: str = os.getenv("ADMIN_PASS", "admin123")

    # Telefone do dono — autoriza ações restritas. Usado como seed da Config.
    owner_phone: str = os.getenv("OWNER_PHONE", "5545999990000")

    # Fuso usado nas conversões de data/hora (validação de passado, slots).
    timezone: str = os.getenv("MCP_TZ", "America/Sao_Paulo")

    # Evolution API — pipeline do agente + pareamento pelo painel (rede docker).
    evolution_api_url: str = os.getenv("EVOLUTION_API_URL", "http://evolution_api:9090")
    evolution_api_key: str = os.getenv("EVOLUTION_API_KEY", "")
    evolution_instance: str = os.getenv("EVOLUTION_INSTANCE", "evo_bot")

    # URL deste serviço VISTA PELA EVOLUTION (rede docker) — destino do webhook.
    webhook_url: str = os.getenv(
        "MCP_WEBHOOK_URL",
        "http://mcp_agendamentos:8000/webhook/whatsapp/receberMensagem",
    )

    # Token do webhook: impede forja local de eventos (remoteJid do dono).
    # Derivado da SENHA por hash — URLs vazam em logs; o hash não devolve a
    # senha. A Evolution entrega com ?token=...; o endpoint exige igualdade.
    webhook_token: str = hashlib.sha256(
        f"webhook:{admin_pass}".encode()
    ).hexdigest()[:32]

    # Seed legado da instrução geral (1º acesso ao card do painel). Vazio →
    # vale o padrão em app/agente.py; depois do 1º save, SQLite (tabela Prompt).
    agent_system_prompt: str = os.getenv("AGENT_SYSTEM_PROMPT", "")

    # URL externa (browser do host) p/ atalho no painel.
    evolution_external_url: str = os.getenv(
        "EVOLUTION_EXTERNAL_URL", "http://localhost:9090"
    )

    # Segredo para assinar cookies de sessão
    session_secret: str = os.getenv("SESSION_SECRET", "")


settings = Settings()

# Fallback do session_secret (derivado da senha p/ ser determinístico)
if not settings.session_secret:
    settings.session_secret = hashlib.sha256(
        f"session:{settings.admin_pass}".encode()
    ).hexdigest()[:32]
