"""Definição das ferramentas (tools) MCP — adaptadas para vagas + multi-instância.

Agrupadas por nível de permissão:
  - Abertas:           qualquer cliente
  - Dono ou próprio:   remarcar / cancelar
  - Dono:              gestão e visão completa
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import auth, db, notificacoes
from .phone import mesmo_numero, normalizar

mcp = FastMCP(
    "agendamentos",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _agora_local() -> datetime:
    cfg = db.get_config()
    try:
        tz = ZoneInfo(cfg.fuso)
    except Exception:
        tz = ZoneInfo("America/Sao_Paulo")
    return datetime.now(tz).replace(tzinfo=None)


_NOMES_GENERICOS = {
    "cliente", "o cliente", "a cliente", "cliente novo", "novo cliente",
    "sem nome", "desconhecido", "desconhecida", "nome", "usuario", "usuário",
    "n/a", "na", "-", "?", "x", "teste", "test",
}


def _nome_generico(nome: str | None) -> bool:
    return not nome or nome.strip().lower() in _NOMES_GENERICOS


def _data_sem_horario(iso: str) -> str:
    """Extrai apenas a data (YYYY-MM-DD) de um início ISO, sem hora."""
    if not iso:
        return ""
    return iso.split("T")[0]


def _agendamento_dict(a) -> dict:
    """Dict do agendamento para o modelo — SEMPRE sem horário de relógio."""
    d = db.como_dict(a)
    d["inicio"] = _data_sem_horario(a.inicio)
    d["fim"] = _data_sem_horario(a.fim)
    return d


# ---------------------------------------------------------------------------
# Tools ABERTAS
# ---------------------------------------------------------------------------

# Duração usada quando o agendamento é feito por sintoma/descrição, sem serviço
# cadastrado (o serviço define a duração; sem ele, assume-se 1h por padrão).
DURACAO_PADRAO = 60


@mcp.tool()
def listar_servicos() -> list[dict]:
    """Lista os serviços disponíveis com descrição, valor e duração."""
    return [db.como_dict(s) for s in db.listar_servicos_ativos()]


@mcp.tool()
def listar_vagas() -> list[dict]:
    """Lista as vagas/boxes de atendimento disponíveis."""
    return [db.como_dict(v) for v in db.listar_vagas()]


def _vagas_ocupadas_em(dia: date) -> int:
    """Nº de agendamentos vigentes que ocupam vaga em `dia` (agendamento é por DIA INTEIRO)."""
    prefixo = dia.isoformat()
    n = 0
    for a in db.listar_agendamentos():
        if a.inicio[:10] == prefixo and a.status in db.STATUS_VIGENTES:
            n += 1
    return n


@mcp.tool()
def consultar_horarios_disponiveis(
    data: str = "",
    servico_id: int | None = None,
    quantidade_dias: int = 14,
) -> dict:
    """Verifica disponibilidade de agendamento.

    O agendamento é por DIA INTEIRO (o cliente ocupa uma vaga/box o dia todo),
    então a disponibilidade é por dia, não por horário de relógio.

    - SEM `data`: retorna APENAS a PRÓXIMA data com expediente e vaga livre
      (a vaga mais próxima, até `quantidade_dias` dias à frente) — use para
      SUGERIR ao cliente apenas essa data. Se o cliente recusar, cheque o dia
      que ele quiser passando `data`.
    - COM `data` (formato YYYY-MM-DD): retorna se o dia tem vaga livre e
      quantas vagas/boxes sobram.

    `servico_id` é opcional e NÃO muda a capacidade (definida pelo nº de
    vagas/boxes).
    """
    servico = None
    if servico_id is not None:
        servico = db.get_servico(servico_id)
        if not servico:
            return {"erro": "Serviço não encontrado."}

    vagas = db.listar_vagas()
    total_vagas = len(vagas) or 1
    nome_servico = servico.nome if servico else ""

    # Modo 1: sem data → retorna APENAS a PRÓXIMA data com vaga livre (a vaga
    # mais próxima). O agente deve oferecer somente essa data; se o cliente
    # recusar, ele checa o dia específico com `data`.
    if not data.strip():
        agora = _agora_local()
        dias_disponiveis: list[dict] = []
        for i in range(max(1, quantidade_dias)):
            candidato = (agora.date() + timedelta(days=i)).isoformat()
            intervalos = db.horarios_do_dia(date.fromisoformat(candidato).weekday())
            if not intervalos:
                continue
            ini = f"{candidato}T{intervalos[0].inicio}"
            fim = f"{candidato}T{intervalos[-1].fim}"
            if db.horario_disponivel(ini, fim):
                ocupadas = _vagas_ocupadas_em(date.fromisoformat(candidato))
                dias_disponiveis.append(
                    {
                        "data": candidato,
                        "dia_semana": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")[date.fromisoformat(candidato).weekday()],
                        "vagas_livres": max(0, total_vagas - ocupadas),
                    }
                )
                break  # só a vaga mais próxima
        return {"data": "", "servico": nome_servico, "dias_disponiveis": dias_disponiveis, "total_vagas": total_vagas}

    try:
        dia = date.fromisoformat(data)
    except ValueError:
        return {"erro": "Data inválida. Use o formato YYYY-MM-DD."}

    intervalos = db.horarios_do_dia(dia.weekday())
    if not intervalos:
        return {"data": data, "servico": nome_servico, "vagas_livres": 0, "total_vagas": total_vagas, "aviso": "Sem expediente neste dia (fechado)."}

    vagas_livres = max(0, total_vagas - _vagas_ocupadas_em(dia))
    return {
        "data": data,
        "servico": nome_servico,
        "dia_livre": vagas_livres > 0,
        "vagas_livres": vagas_livres,
        "total_vagas": total_vagas,
    }


@mcp.tool()
def agendar(
    servico_id: int | None = None,
    servico_nome: str = "",
    descricao: str = "",
    nome_cliente: str = "",
    data: str = "",
    veiculo: str = "",
    placa: str = "",
    observacoes: str = "",
    telefone_solicitante: str | None = None,
) -> dict:
    """Agenda um atendimento para uma data. `data` no formato YYYY-MM-DD.

    Pode ser com serviço (`servico_id`, se existir serviço compatível na lista)
    ou apenas pelo sintoma/problema descrito pelo cliente em `descricao`
    (ex.: "ruído no pneu dianteiro direito", "trocar o cabeçote"). Pelo menos
    um dos dois deve ser informado. Se o cliente citar o nome exato do serviço
    e ele NÃO estiver no catálogo, passe em `servico_nome` esse texto solto
    (sem criar serviço novo). A vaga é auto-atribuída e o cliente ocupa
    uma vaga (box) no DIA inteiro.

    Para oficina: informe `veiculo` (modelo do carro) e `placa` se o cliente
    mencionar. O telefone do cliente é o do solicitante (injetado pelo pipeline).
    """
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return auth.NEGADO_SEM_SOLICITANTE
    if _nome_generico(nome_cliente):
        return {"erro": "Nome ausente ou genérico. Pergunte o nome real do cliente antes de agendar."}
    if not descricao.strip():
        return {"erro": "Descreva o problema/sintoma do carro em `descricao` antes de agendar."}
    servico = None
    if servico_id is not None:
        servico = db.get_servico(servico_id)
        if not servico:
            return {"erro": "Serviço não encontrado."}
    try:
        dia = date.fromisoformat(data)
    except ValueError:
        return {"erro": "Data inválida. Use YYYY-MM-DD."}
    if dia < _agora_local().date():
        return {"erro": "Não é possível agendar para uma data passada."}
    # Usa o horário de funcionamento do dia para definir inicio/fim
    horarios = db.horarios_do_dia(dia.weekday())
    if not horarios:
        return {"erro": "Sem expediente nesta data."}
    primeiro = horarios[0]
    ultimo = horarios[-1]
    dt_inicio = datetime.combine(dia, time.fromisoformat(primeiro.inicio))
    dt_fim = datetime.combine(dia, time.fromisoformat(ultimo.fim))

    ag = db.criar_agendamento(
        servico_id=servico.id if servico else None,
        servico_nome=servico_nome,
        telefone_cliente=normalizar(tel) or tel,
        nome_cliente=nome_cliente,
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=dt_fim.isoformat(timespec="minutes"),
        veiculo=veiculo,
        placa=placa.upper(),
        observacoes=observacoes,
        origem="bot",
        descricao=descricao,
    )
    if not ag:
        return {"erro": "Todas as vagas ocupadas nesta data. Escolha outro dia."}
    notificacoes.notificar_dono("agendado", ag, tel)

    vaga_nome = ""
    if ag.vaga_id:
        v = db.get_vaga(ag.vaga_id)
        vaga_nome = v.nome if v else ""
    resultado = _agendamento_dict(ag)
    resultado["vaga_nome"] = vaga_nome
    return {"ok": True, "agendamento": resultado}


@mcp.tool()
def meus_agendamentos(telefone_solicitante: str | None = None) -> list[dict]:
    """Lista os agendamentos ativos do próprio solicitante."""
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return [auth.NEGADO_SEM_SOLICITANTE]
    return [_agendamento_dict(a) for a in db.agendamentos_do_telefone(tel)]


# ---------------------------------------------------------------------------
# Tools DONO ou PRÓPRIO CLIENTE
# ---------------------------------------------------------------------------


def _enfileirar_aviso_cliente(ag, acao: str, telefone_solicitante: str | None, inicio_anterior: str | None = None) -> bool:
    if not auth.eh_dono(telefone_solicitante):
        return False
    if mesmo_numero(ag.telefone_cliente, db.get_config().telefone_dono):
        return False
    db.criar_aviso_cliente(ag, acao, _agora_local().isoformat(timespec="minutes"), inicio_anterior=inicio_anterior)
    return True


@mcp.tool()
def reagendar(
    agendamento_id: int,
    nova_data: str,
    avisar_cliente: bool = False,
    telefone_solicitante: str | None = None,
) -> dict:
    """Remarca um agendamento para outro DIA. `nova_data` no formato YYYY-MM-DD (sem horário)."""
    if not auth.pode_mexer_no_agendamento(telefone_solicitante, agendamento_id):
        return auth.NEGADO_PROPRIO
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        return {"erro": "Agendamento não encontrado."}
    inicio_anterior = ag.inicio
    try:
        dia = date.fromisoformat(nova_data)
    except ValueError:
        return {"erro": "Data inválida. Use YYYY-MM-DD."}
    if dia < _agora_local().date():
        return {"erro": "Não é possível remarcar para uma data passada."}
    horarios = db.horarios_do_dia(dia.weekday())
    if not horarios:
        return {"erro": "Sem expediente nesta data."}
    dt_inicio = datetime.combine(dia, time.fromisoformat(horarios[0].inicio))
    dt_fim = datetime.combine(dia, time.fromisoformat(horarios[-1].fim))
    novo_inicio = dt_inicio.isoformat(timespec="minutes")
    novo_fim = dt_fim.isoformat(timespec="minutes")
    if not db.reagendar_agendamento(agendamento_id, novo_inicio, novo_fim):
        return {"erro": "Nova data indisponível (todas as vagas ocupadas)."}
    atualizado = db.get_agendamento(agendamento_id)
    notificacoes.notificar_dono("reagendado", atualizado, auth.requester(telefone_solicitante))
    resultado = {"ok": True, "agendamento": _agendamento_dict(atualizado)}
    if avisar_cliente and _enfileirar_aviso_cliente(atualizado, "reagendado", telefone_solicitante, inicio_anterior=inicio_anterior):
        resultado["cliente_sera_avisado"] = True
    return resultado


@mcp.tool()
def cancelar(
    agendamento_id: int,
    avisar_cliente: bool = False,
    telefone_solicitante: str | None = None,
) -> dict:
    """Cancela um agendamento."""
    if not auth.pode_mexer_no_agendamento(telefone_solicitante, agendamento_id):
        return auth.NEGADO_PROPRIO
    ag = db.get_agendamento(agendamento_id)
    if not db.cancelar_agendamento(agendamento_id):
        return {"erro": "Agendamento não encontrado ou já cancelado."}
    notificacoes.notificar_dono("cancelado", ag, auth.requester(telefone_solicitante))
    resultado = {"ok": True}
    if avisar_cliente and _enfileirar_aviso_cliente(ag, "cancelado", telefone_solicitante):
        resultado["cliente_sera_avisado"] = True
    return resultado


# ---------------------------------------------------------------------------
# Tools DONO
# ---------------------------------------------------------------------------


def _validar_periodo(data: str, data_fim: str | None) -> dict | None:
    try:
        ini = date.fromisoformat(data)
        fim = date.fromisoformat(data_fim) if data_fim else ini
    except ValueError:
        return {"erro": "Data inválida. Use o formato YYYY-MM-DD."}
    if fim < ini:
        return {"erro": "A data final é anterior à inicial."}
    return None


@mcp.tool()
def fechar_data(data: str, data_fim: str | None = None, motivo: str = "", telefone_solicitante: str | None = None) -> dict:
    """[DONO] Fecha um dia ou período inteiro."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    b = db.criar_bloqueio(data=data, inicio=None, fim=None, motivo=motivo, data_fim=data_fim)
    return {"ok": True, "bloqueio": db.como_dict(b)}


@mcp.tool()
def abrir_data(data: str, data_fim: str | None = None, telefone_solicitante: str | None = None) -> dict:
    """[DONO] Reabre dia ou período."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    removidos = db.remover_bloqueio_por_data(data, data_fim)
    return {"ok": True, "removidos": removidos}


@mcp.tool()
def remanejar_dia(data: str, acao: str = "remarcar", motivo: str = "", telefone_solicitante: str | None = None) -> dict:
    """[DONO] Fecha o dia e contata clientes."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if acao not in ("remarcar", "cancelar"):
        return {"erro": 'Ação inválida: use "remarcar" ou "cancelar".'}
    if erro := _validar_periodo(data, None):
        return erro
    db.criar_bloqueio(data=data, inicio=None, fim=None, motivo=motivo or "imprevisto do dono", data_fim=None)
    dono = db.get_config().telefone_dono
    afetados = [a for a in db.listar_agendamentos() if a.inicio.startswith(data)]
    agora = _agora_local().isoformat(timespec="minutes")
    contatados = 0
    for a in afetados:
        if acao == "cancelar":
            db.cancelar_agendamento(a.id)
        if mesmo_numero(a.telefone_cliente, dono):
            continue
        db.criar_tarefa(tipo="contatar_cliente", telefone_alvo=a.telefone_cliente, payload={"agendamento_id": a.id, "acao": acao, "motivo": motivo}, agendado_para=agora)
        contatados += 1
    return {"ok": True, "data": data, "acao": acao, "dia_fechado": True, "agendamentos_afetados": len(afetados), "clientes_a_contatar": contatados}


@mcp.tool()
def transferir_atendimento(destino: str, telefone_solicitante: str | None = None) -> dict:
    """Transfere o atendimento para outro setor/departamento (ex.: financeiro, suporte).

    Envia um aviso ao setor de destino, pausa o bot para o cliente e registra
    a transferencia. Use quando o cliente pedir para falar com outro setor.
    Destinos disponiveis: use listar_destinos_transferencia para ver.
    """
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return auth.NEGADO_SEM_SOLICITANTE
    d = db.get_destino_transferencia(destino.strip().lower())
    if not d:
        return {"erro": f"Destino '{destino}' nao encontrado. Destinos disponiveis: consulte listar_destinos_transferencia."}
    from .agente import registrar_na_memoria
    from .evolution import enviar_texto_sync as enviar_sync
    from .phone import formatar_internacional

    # Informacoes do cliente
    cli = db.get_cliente(tel)
    nome_cliente = cli.nome if cli and cli.nome else tel
    agendamentos = db.agendamentos_do_telefone(tel)
    info_ag = ""
    if agendamentos:
        ultimo = agendamentos[-1]
        nome_serv = db.nome_servico(ultimo)
        dt = (ultimo.inicio or "").split("T")[0] if ultimo.inicio else ""
        info_ag = f" | {nome_serv} {dt}" if nome_serv else ""
    tel_fmt = formatar_internacional(tel) or tel

    # Envia notificacao para o setor de destino
    notificacao = (
        f"Cliente {nome_cliente} ({tel_fmt}) pediu transferencia "
        f"para {d.nome}.{info_ag}"
    )
    if d.instancia_id:
        inst = db.get_instancia(d.instancia_id)
        if inst and inst.numero:
            try:
                enviar_sync(inst.numero, notificacao, instancia=inst.nome)
            except Exception:
                pass
    elif d.telefone:
        try:
            enviar_sync(d.telefone, notificacao)
        except Exception:
            pass

    # Pausa o bot para o cliente
    try:
        db.set_pausa_cliente(tel, True)
    except Exception:
        pass

    # Registra na memoria que houve transferencia
    registrar_na_memoria(tel, f"[SISTEMA] Atendimento transferido para {d.nome}", "sistema")

    # Envia confirmacao pro cliente
    try:
        aviso = d.mensagem.format(nome=d.nome, cliente=nome_cliente)
        enviar_sync(tel, aviso)
    except Exception:
        pass

    return {
        "ok": True,
        "destino": d.nome,
        "mensagem": d.mensagem.format(nome=d.nome, cliente=nome_cliente),
        "telefone_contato": d.telefone or "",
        "bot_pausado": True,
    }


@mcp.tool()
def listar_destinos_transferencia(telefone_solicitante: str | None = None) -> list[dict]:
    """Lista os destinos disponiveis para transferencia de atendimento."""
    return [{"nome": d.nome, "mensagem": d.mensagem} for d in db.listar_destinos_transferencia()]


@mcp.tool()
def pausar_bot(telefone: str, pausar: bool = True, telefone_solicitante: str | None = None) -> dict:
    """[DONO] Silencia ou retoma o bot para um contato."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    alvo = normalizar(telefone) or (telefone or "").strip()
    if not alvo:
        return {"erro": "Informe o telefone do contato."}
    if mesmo_numero(alvo, db.get_config().telefone_dono):
        return {"erro": "O dono não pode ser pausado."}
    c = db.set_pausa_cliente(alvo, pausar)
    return {"ok": True, "telefone": c.telefone, "bot_pausado": c.bot_pausado}


@mcp.tool()
def criar_servico(nome: str, descricao: str, valor: float, duracao_min: int, telefone_solicitante: str | None = None) -> dict:
    """[DONO] Cria um novo serviço."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    s = db.criar_servico(nome, descricao, valor, duracao_min)
    return {"ok": True, "servico": db.como_dict(s)}


@mcp.tool()
def editar_servico(servico_id: int, nome: str | None = None, descricao: str | None = None, valor: float | None = None, duracao_min: int | None = None, ativo: bool | None = None, telefone_solicitante: str | None = None) -> dict:
    """[DONO] Edita um serviço."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    s = db.editar_servico(servico_id, nome=nome, descricao=descricao, valor=valor, duracao_min=duracao_min, ativo=ativo)
    if not s:
        return {"erro": "Serviço não encontrado."}
    return {"ok": True, "servico": db.como_dict(s)}


@mcp.tool()
def ver_agenda_completa(telefone_solicitante: str | None = None) -> dict:
    """[DONO] Retorna todos os agendamentos ativos e bloqueios com vagas."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    ags = []
    for a in db.listar_agendamentos():
        d = _agendamento_dict(a)
        if a.vaga_id:
            v = db.get_vaga(a.vaga_id)
            d["vaga_nome"] = v.nome if v else ""
        ags.append(d)
    return {"agendamentos": ags, "bloqueios": [db.como_dict(b) for b in db.listar_bloqueios()]}
