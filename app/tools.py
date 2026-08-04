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


# ---------------------------------------------------------------------------
# Tools ABERTAS
# ---------------------------------------------------------------------------


@mcp.tool()
def listar_servicos() -> list[dict]:
    """Lista os serviços disponíveis com descrição, valor e duração."""
    return [db.como_dict(s) for s in db.listar_servicos_ativos()]


@mcp.tool()
def listar_vagas() -> list[dict]:
    """Lista as vagas/boxes de atendimento disponíveis."""
    return [db.como_dict(v) for v in db.listar_vagas()]


@mcp.tool()
def consultar_horarios_disponiveis(data: str, servico_id: int) -> dict:
    """Lista horários livres em uma data (formato YYYY-MM-DD) para um serviço.

    A capacidade é definida pelo número de vagas/boxes: vários horários podem
    estar ocupados no mesmo momento se todas as vagas estiverem preenchidas.
    Retorna também quantas vagas estão livres em cada horário.
    """
    servico = db.get_servico(servico_id)
    if not servico:
        return {"erro": "Serviço não encontrado."}
    try:
        dia = date.fromisoformat(data)
    except ValueError:
        return {"erro": "Data inválida. Use o formato YYYY-MM-DD."}

    intervalos = db.horarios_do_dia(dia.weekday())
    if not intervalos:
        return {"data": data, "servico": servico.nome, "horarios": [], "aviso": "Sem expediente neste dia (fechado)."}

    vagas = db.listar_vagas()
    total_vagas = len(vagas) or 1

    agora = _agora_local()
    livres: list[dict] = []
    passo = timedelta(minutes=servico.duracao_min)
    for janela in intervalos:
        atual = datetime.fromisoformat(f"{data}T{janela.inicio}")
        limite = datetime.fromisoformat(f"{data}T{janela.fim}")
        while atual + passo <= limite:
            ini = atual.isoformat(timespec="minutes")
            fim = (atual + passo).isoformat(timespec="minutes")
            if atual >= agora:
                # Conta ocupados no período
                ocupados = 0
                for a in db.listar_agendamentos():
                    if a.status not in db.STATUS_VIGENTES:
                        continue
                    a_ini = datetime.fromisoformat(a.inicio)
                    a_fim = datetime.fromisoformat(a.fim)
                    if a_ini < datetime.fromisoformat(fim) and datetime.fromisoformat(ini) < a_fim:
                        ocupados += 1
                vagas_livres = max(0, total_vagas - ocupados)
                if vagas_livres > 0:
                    livres.append({"horario": atual.strftime("%H:%M"), "vagas": vagas_livres})
            atual += passo

    return {"data": data, "servico": servico.nome, "horarios": livres, "total_vagas": total_vagas}


@mcp.tool()
def agendar(
    servico_id: int,
    nome_cliente: str,
    data: str,
    veiculo: str = "",
    placa: str = "",
    observacoes: str = "",
    telefone_solicitante: str | None = None,
) -> dict:
    """Agenda um serviço para uma data. `data` no formato YYYY-MM-DD.
    A vaga é auto-atribuída. O cliente ocupa uma vaga (box) no DIA inteiro.

    Para oficina: informe `veiculo` (modelo do carro) e `placa` se o cliente
    mencionar. O telefone do cliente é o do solicitante (injetado pelo pipeline).
    """
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return auth.NEGADO_SEM_SOLICITANTE
    if _nome_generico(nome_cliente):
        return {"erro": "Nome ausente ou genérico. Pergunte o nome real do cliente antes de agendar."}
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
        servico_id=servico_id,
        telefone_cliente=normalizar(tel) or tel,
        nome_cliente=nome_cliente,
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=dt_fim.isoformat(timespec="minutes"),
        veiculo=veiculo,
        placa=placa.upper(),
        observacoes=observacoes,
        origem="bot",
    )
    if not ag:
        return {"erro": "Todas as vagas ocupadas nesta data. Escolha outro dia."}
    notificacoes.notificar_dono("agendado", ag, tel)

    vaga_nome = ""
    if ag.vaga_id:
        v = db.get_vaga(ag.vaga_id)
        vaga_nome = v.nome if v else ""
    resultado = db.como_dict(ag)
    resultado["vaga_nome"] = vaga_nome
    return {"ok": True, "agendamento": resultado}


@mcp.tool()
def meus_agendamentos(telefone_solicitante: str | None = None) -> list[dict]:
    """Lista os agendamentos ativos do próprio solicitante."""
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return [auth.NEGADO_SEM_SOLICITANTE]
    return [db.como_dict(a) for a in db.agendamentos_do_telefone(tel)]


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
    novo_inicio: str,
    avisar_cliente: bool = False,
    telefone_solicitante: str | None = None,
) -> dict:
    """Remarca um agendamento."""
    if not auth.pode_mexer_no_agendamento(telefone_solicitante, agendamento_id):
        return auth.NEGADO_PROPRIO
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        return {"erro": "Agendamento não encontrado."}
    inicio_anterior = ag.inicio
    servico = db.get_servico(ag.servico_id)
    try:
        dt_inicio = datetime.fromisoformat(novo_inicio)
    except ValueError:
        return {"erro": "Horário inválido. Use YYYY-MM-DDTHH:MM."}
    if dt_inicio < _agora_local():
        return {"erro": "Não é possível remarcar para um horário no passado."}
    dt_fim = dt_inicio + timedelta(minutes=servico.duracao_min)
    if not db.dentro_do_funcionamento(dt_inicio, dt_fim):
        return {"erro": "Fora do horário de funcionamento."}
    novo_fim = dt_fim.isoformat(timespec="minutes")
    if not db.reagendar_agendamento(agendamento_id, dt_inicio.isoformat(timespec="minutes"), novo_fim):
        return {"erro": "Novo horário indisponível (todas as vagas ocupadas)."}
    atualizado = db.get_agendamento(agendamento_id)
    notificacoes.notificar_dono("reagendado", atualizado, auth.requester(telefone_solicitante))
    resultado = {"ok": True, "agendamento": db.como_dict(atualizado)}
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
def bloquear_horario(data: str, inicio: str, fim: str, motivo: str = "", data_fim: str | None = None, telefone_solicitante: str | None = None) -> dict:
    """[DONO] Bloqueia um intervalo de horas."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    b = db.criar_bloqueio(data=data, inicio=inicio, fim=fim, motivo=motivo, data_fim=data_fim)
    return {"ok": True, "bloqueio": db.como_dict(b)}


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
        serv = db.get_servico(ultimo.servico_id)
        nome_serv = serv.nome if serv else ""
        dt = (ultimo.inicio or "").replace("T", " ") if ultimo.inicio else ""
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
        d = db.como_dict(a)
        if a.vaga_id:
            v = db.get_vaga(a.vaga_id)
            d["vaga_nome"] = v.nome if v else ""
        ags.append(d)
    return {"agendamentos": ags, "bloqueios": [db.como_dict(b) for b in db.listar_bloqueios()]}
