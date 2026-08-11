# -*- coding: utf-8 -*-
"""Atualiza os prompts salvos no banco para as versões sem listagem de horários.

RODA DENTRO DO CONTAINER (precisa do app instalado para pegar os defaults):

    docker compose up -d --build mcp-agendamentos
    docker cp scripts/atualizar_prompts.py mcp_agendamentos:/tmp/atualizar_prompts.py
    docker exec -e PYTHONPATH=/srv -w /srv mcp_agendamentos python /tmp/atualizar_prompts.py

Idempotente: pode rodar quantas vezes quiser.
"""
from __future__ import annotations

import os
import sqlite3
import sys

try:
    from app import agente
except ModuleNotFoundError:
    print("ERRO: rode dentro do container (PYTHONPATH=/srv -w /srv), com o app instalado.")
    sys.exit(1)

DB = os.environ.get("MCP_DB_PATH", "/data/agendamentos.db")

REGRA_ANTI_HORARIO = (
    "* **NUNCA Liste Horarios de Relogio (INVIOLAVEL):** O atendimento e por DIA INTEIRO, "
    "o cliente ocupa uma vaga/box o dia todo. Jamais escreva horarios como 08:00, 09:00, "
    "'13h' nem liste 'horarios disponiveis'. Ao falar de agenda, diga apenas se o dia TEM "
    "VAGA ou NAO e pergunte qual dia o cliente prefere. Se perguntarem 'que horas', "
    "responda que o atendimento e por dia inteiro (sem horario marcado) e pergunte o dia."
)


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # 1) Blocos MCP -> defaults atuais (já proíbem listar horários e usam nova_data)
    for chave, padrao in (("mcp_dono", agente.PROMPT_MCP_DONO_PADRAO),
                          ("mcp_cliente", agente.PROMPT_MCP_CLIENTE_PADRAO)):
        if cur.execute("SELECT 1 FROM prompt WHERE chave=?", (chave,)).fetchone():
            cur.execute("UPDATE prompt SET texto=? WHERE chave=?", (padrao, chave))
            print(f"{chave}: atualizado")
        else:
            cur.execute("INSERT INTO prompt (chave, texto) VALUES (?, ?)", (chave, padrao))
            print(f"{chave}: criado")

    # 2) Prompt geral: preserva o texto customizado, só insere a regra anti-horário.
    geral = cur.execute("SELECT texto FROM prompt WHERE chave='geral'").fetchone()
    if geral:
        texto = geral[0]
        if "NUNCA Liste Horarios" in texto:
            print("geral: regra já presente (nada a fazer)")
        else:
            ancora = "* **Fidelidade às Informações:**"
            if ancora in texto:
                texto = texto.replace(ancora, REGRA_ANTI_HORARIO + "\n" + ancora, 1)
            else:
                texto = REGRA_ANTI_HORARIO + "\n" + texto
            cur.execute("UPDATE prompt SET texto=? WHERE chave='geral'", (texto,))
            print("geral: regra anti-horário inserida")
    else:
        print("geral: sem prompt salvo (usa o default do código — ok, já é a versão nova)")

    con.commit()
    con.close()
    print("Pronto. As próximas respostas do bot não devem mais listar horários.")


if __name__ == "__main__":
    main()
