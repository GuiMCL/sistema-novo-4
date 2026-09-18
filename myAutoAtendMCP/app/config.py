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

    # WhatsApp Cloud API (Meta). O token nunca deve ser exposto ao navegador.
    whatsapp_graph_api_version: str = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    # Templates aprovados na Meta, usados para mensagens iniciadas pelo negócio.
    whatsapp_appointment_template: str = os.getenv("WHATSAPP_APPOINTMENT_TEMPLATE", "")
    whatsapp_appointment_template_2: str = os.getenv("WHATSAPP_APPOINTMENT_TEMPLATE_2", "")
    whatsapp_template_language: str = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "pt_BR")

    # Seed legado da instrução geral (1º acesso ao card do painel). Vazio →
    # vale o padrão em app/agente.py; depois do 1º save, SQLite (tabela Prompt).
    agent_system_prompt: str = os.getenv("AGENT_SYSTEM_PROMPT", "")

    # Segredo para assinar cookies de sessão
    session_secret: str = os.getenv("SESSION_SECRET", "")


settings = Settings()

# Fallback do session_secret (derivado da senha p/ ser determinístico)
if not settings.session_secret:
    settings.session_secret = hashlib.sha256(
        f"session:{settings.admin_pass}".encode()
    ).hexdigest()[:32]
