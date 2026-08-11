"""Avisos ao dono no WhatsApp (fase 1 do plano de ações proativas).

Template fixo em Python — não passa pela IA (custo zero, sem latência, sem
alucinação). Envio síncrono com timeout curto: as tools rodam fora do event
loop (threadpool), então o client sync da Evolution serve; falha de aviso
NUNCA derruba a ação principal (try/except + log).

Só notifica ações vindas do bot (cliente). Ação do próprio dono — pelo
painel ou conversando com o bot pelo número dele — não gera aviso (eco).
Liga/desliga: checkbox "Aviso ao dono" no card Configuração geral do painel
(coluna `Config.avisar_dono`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from . import db, evolution
from .phone import mesmo_numero, normalizar

log = logging.getLogger("notificacoes")


def data_e_hora_br(inicio: str) -> tuple[str, str]:
    """(dd/mm/aaaa, HH:MM) a partir do inicio ISO YYYY-MM-DDTHH:MM.

    Tolerante a início vazio ou sem horário — nunca quebra o template."""
    if not inicio:
        return "", ""
    try:
        dt = datetime.fromisoformat(inicio)
        return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")
    except ValueError:
        parte = inicio.split("T")[0]
        try:
            return datetime.fromisoformat(parte).strftime("%d/%m/%Y"), ""
        except ValueError:
            return parte, ""

# Placeholder do compose enquanto o dono não configura o telefone no painel.
_TELEFONE_PLACEHOLDER = "5500000000000"

_TEMPLATES = {
    "agendado": "*Novo agendamento*\n{cliente} ({tel})\n{servico} - {data} as {hora}",
    "reagendado": "*Reagendamento*\n{cliente} ({tel})\n{servico} - agora {data} as {hora}",
    "cancelado": "*Cancelamento*\n{cliente} ({tel})\n{servico} - era {data} as {hora}",
}


def notificar_dono(evento: str, agendamento, solicitante: str | None) -> None:
    """Avisa o dono sobre um evento de agendamento. Nunca levanta exceção."""
    try:
        cfg = db.get_config()
        if not cfg.avisar_dono:
            return
        dono = normalizar(cfg.telefone_dono)
        if not dono or mesmo_numero(cfg.telefone_dono, _TELEFONE_PLACEHOLDER):
            return  # telefone do dono ainda não configurado no painel
        if mesmo_numero(solicitante, cfg.telefone_dono):
            return  # ação do próprio dono — sem eco

        servico = db.get_servico(agendamento.servico_id)
        data, hora = data_e_hora_br(agendamento.inicio)
        texto = _TEMPLATES[evento].format(
            cliente=agendamento.nome_cliente,
            tel=agendamento.telefone_cliente,
            servico=servico.nome if servico else f"serviço #{agendamento.servico_id}",
            data=data,
            hora=hora,
        )
        evolution.enviar_texto_sync(re.sub(r"\D", "", dono), texto)
    except Exception:  # noqa: BLE001 — aviso é melhor-esforço
        log.exception("Falha ao notificar o dono (%s)", evento)
