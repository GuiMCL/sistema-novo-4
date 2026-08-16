"""Worker de ações proativas + lembretes automáticos.

Consome a tabela `Tarefa` (fila persistente) e também dispara lembretes
de confirmação de agendamentos.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

from . import agente, db, ia, whatsapp
from .notificacoes import data_e_hora_br
from .tools import _agora_local

log = logging.getLogger("tarefas")

TICK_S = 30.0
JANELA_INICIO = "08:00"
JANELA_FIM = "20:00"
MAX_TENTATIVAS = 3
PAUSA_ENTRE_TAREFAS_S = 3.0


async def worker() -> None:
    presas = db.resetar_tarefas_executando()
    if presas:
        log.warning("%d tarefa(s) presas voltaram a pendente", presas)
    log.info("Worker iniciado (tick %.0fs)", TICK_S)
    while True:
        try:
            await _tick()
        except Exception:
            log.exception("Erro no tick do worker")
        await asyncio.sleep(TICK_S)


async def _tick() -> None:
    agora = _agora_local()
    agora_str = agora.strftime("%H:%M")
    if not (JANELA_INICIO <= agora_str < JANELA_FIM):
        return

    # Tarefas proativas
    for t in db.tarefas_vencidas(agora.isoformat(timespec="minutes")):
        if whatsapp.contato_ocupado(t.telefone_alvo):
            log.info("Tarefa %d adiada: contato %s ocupado", t.id, t.telefone_alvo)
            continue
        await _executar(t)
        await asyncio.sleep(PAUSA_ENTRE_TAREFAS_S + random.random() * 2)

    # Lembretes automáticos
    await _disparar_lembretes(agora)


async def _disparar_lembretes(agora: datetime) -> None:
    """Dispara lembretes em dois estagios: D-2 (aviso) e D-1 (confirmacao + dados)."""
    try:
        config = db.get_lembrete_config()
        if not config.ativo:
            return

        agora_iso = agora.isoformat(timespec="minutes")

        # Estagio 1: primeiro aviso (ex: 48h antes)
        stage1 = config.horas_antes > 0
        if stage1:
            for ag in db.agendamentos_precisando_lembrete(agora_iso, config.horas_antes, stage=0):
                await _enviar_lembrete(ag, 0, agora)
                await asyncio.sleep(PAUSA_ENTRE_TAREFAS_S + random.random() * 1)

        # Estagio 2: segundo aviso (ex: 24h antes) com pedido de dados
        stage2 = config.ativo2 and config.horas_antes2 > 0
        if stage2:
            for ag in db.agendamentos_precisando_lembrete(agora_iso, config.horas_antes2, stage=1):
                await _enviar_lembrete(ag, 1, agora)
                await asyncio.sleep(PAUSA_ENTRE_TAREFAS_S + random.random() * 1)

    except Exception as e:
        log.error("Erro disparando lembretes: %s", e)


async def _enviar_lembrete(ag: db.Agendamento, stage: int, agora: datetime) -> None:
    """Envia um lembrete e incrementa o contador.

    Usa o modelo de mensagem configurado no painel (com as variaveis {nome},
    {servico} e {data} preenchidas com os dados do agendamento). Sem
    modelo configurado, cai para a geracao por IA (comportamento legado).
    """
    try:
        servico = db.nome_servico(ag)
        nome_servico = servico or "atendimento"
        data, _ = data_e_hora_br(ag.inicio)

        config = db.get_lembrete_config()
        modelo = config.mensagem if stage == 0 else config.mensagem2

        if modelo and modelo.strip():
            # Preenche as variaveis conhecidas sem quebrar outras chaves no texto.
            mensagem = modelo
            for chave, valor in {
                "nome": ag.nome_cliente,
                "servico": nome_servico,
                "data": data,
            }.items():
                mensagem = mensagem.replace("{" + chave + "}", str(valor))
        else:
            mensagem = None

        if mensagem is None:
            system = (
                "Voce e um assistente de uma oficina mecânica enviando lembretes "
                "aos clientes sobre agendamentos. Seja cordial e profissional. "
                "Nao use emojis. Nao invente informacoes."
            )
            if stage == 0:
                user = (
                    f"Cliente: {ag.nome_cliente}\n"
                    f"Servico: {nome_servico}\n"
                    f"Data: {data}\n\n"
                    f"Escreva uma mensagem curta e cordial para recordar o cliente "
                    f"sobre este agendamento (o atendimento e por dia inteiro, sem horario), pedindo confirmacao."
                )
            else:
                user = (
                    f"Cliente: {ag.nome_cliente}\n"
                    f"Servico: {nome_servico}\n"
                    f"Data: {data}\n\n"
                    f"Escreva uma mensagem curta e cordial reforçando o agendamento "
                    f"de amanha (o atendimento e por dia inteiro, sem horario), pedindo confirmacao."
                )

            try:
                mensagem = await ia.completar(system, user)
            except ia.IANaoConfigurada:
                log.warning("IA nao configurada — lembrete do ag #%d suprimido", ag.id)
                return

        alvo = db.resolver_chave_conversa(ag.telefone_cliente)
        instancia = whatsapp.get_instancia_do_contato(alvo)
        await whatsapp.enviar_bolhas(alvo.split("@")[0], mensagem, instancia=instancia)

        with db._lock, db._session() as s:
            a = s.get(db.Agendamento, ag.id)
            if a:
                a.lembretes_enviados = (a.lembretes_enviados or 0) + 1
                a.ultimo_lembrete = agora.isoformat(timespec="seconds")
                s.add(a)
                s.commit()

        log.info("Lembrete (estagio %d) enviado p/ %s (ag #%d)", stage, ag.nome_cliente, ag.id)
    except Exception as e:
        log.error("Erro no lembrete do ag #%d: %s", ag.id, e)


async def _executar(t: db.Tarefa) -> None:
    db.atualizar_tarefa(t.id, status="executando", tentativas=t.tentativas + 1)
    try:
        instrucao = _montar_instrucao(t)
        if instrucao is None:
            db.atualizar_tarefa(t.id, status="concluida", resultado="Tarefa obsoleta.")
            return
        alvo = db.resolver_chave_conversa(t.telefone_alvo)
        resposta = await agente.executar_tarefa(alvo, instrucao)
        instancia = whatsapp.get_instancia_do_contato(alvo)
        await whatsapp.enviar_bolhas(alvo.split("@")[0], resposta, instancia=instancia)
        db.atualizar_tarefa(t.id, status="concluida", resultado=resposta[:500])
        log.info("Tarefa %d concluída (%s → %s)", t.id, t.tipo, t.telefone_alvo)
    except ia.IANaoConfigurada:
        db.atualizar_tarefa(t.id, status="pendente", tentativas=t.tentativas)
        log.warning("Tarefa %d adiada: IA não configurada", t.id)
    except Exception as e:
        if t.tentativas + 1 >= MAX_TENTATIVAS:
            db.atualizar_tarefa(t.id, status="falhou", resultado=str(e)[:500])
        else:
            db.atualizar_tarefa(t.id, status="pendente")
        log.exception("Tarefa %d falhou (tentativa %d)", t.id, t.tentativas + 1)


def _montar_instrucao(t: db.Tarefa) -> str | None:
    payload = json.loads(t.payload or "{}")
    if t.tipo == "contatar_cliente":
        return _instrucao_contatar_cliente(payload)
    log.warning("Tarefa %d tipo desconhecido: %s", t.id, t.tipo)
    return None


_RESUMO_ACAO = {
    "reagendado": "Avisar remarcação",
    "cancelado": "Avisar cancelamento",
    "remarcar": "Oferecer remarcação (dia fechado)",
    "cancelar": "Avisar cancelamento (dia fechado)",
}


def descrever_tarefa(t: db.Tarefa) -> dict:
    payload = json.loads(t.payload or "{}")
    acao = payload.get("acao", "")
    nome_cliente = ""
    servico = ""
    quando = ""
    ag = db.get_agendamento(payload.get("agendamento_id") or 0)
    if ag:
        nome_cliente = ag.nome_cliente
        servico = db.nome_servico(ag)
        quando = ag.inicio
    titulo = _RESUMO_ACAO.get(acao, t.tipo)
    return {
        "id": t.id, "status": t.status, "tipo": t.tipo, "acao": acao,
        "titulo": titulo, "alvo": t.telefone_alvo, "nome_cliente": nome_cliente,
        "servico": servico, "quando_agendamento": quando,
        "agendado_para": t.agendado_para, "tentativas": t.tentativas,
        "max_tentativas": MAX_TENTATIVAS, "criado_em": t.criado_em,
        "resultado": t.resultado, "janela_inicio": JANELA_INICIO, "janela_fim": JANELA_FIM,
    }


def _instrucao_contatar_cliente(payload: dict) -> str | None:
    ag = db.get_agendamento(payload.get("agendamento_id") or 0)
    if not ag:
        return None
    acao = payload.get("acao", "remarcar")
    if acao in ("remarcar", "reagendado") and ag.status not in ("ativo", "confirmado"):
        return None
    servico = db.nome_servico(ag)
    nome_servico = servico or f"serviço #{ag.servico_id}"
    data, _ = data_e_hora_br(ag.inicio)

    if acao in ("remarcar", "cancelar"):
        motivo = payload.get("motivo") or "um imprevisto"
        base = f"O dono teve um imprevisto ({motivo}) e não poderá atender no dia {data}. O cliente {ag.nome_cliente} tem \"{nome_servico}\" (agendamento #{ag.id}). "
        if acao == "cancelar":
            return base + "Esse agendamento JÁ FOI CANCELADO. Avise o cliente com delicadeza, peça desculpas e ofereça ajuda para marcar uma nova data."
        return base + "Avise o cliente, peça desculpas e ofereça remarcação para outra data."
    if acao == "cancelado":
        return f"O dono cancelou o agendamento #{ag.id} de {ag.nome_cliente}: \"{nome_servico}\" do dia {data}. JÁ CANCELADO. Avise o cliente com delicadeza."
    if acao == "reagendado":
        antes = payload.get("inicio_anterior") or ""
        data_ant, _ = data_e_hora_br(antes)
        de = f"que era no dia {data_ant} " if antes else ""
        return f"O dono remarcou o agendamento #{ag.id} de {ag.nome_cliente} (\"{nome_servico}\") {de}para o dia {data}. JÁ REMARCADO. Avise o cliente."
    return None
