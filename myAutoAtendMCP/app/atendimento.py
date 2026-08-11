"""Interface de atendimento tipo Chatwoot — /atendimento.

Inbox de conversas com:
  - Sidebar esquerda: lista de conversas com busca e filtros
  - Centro: chat com bolhas
  - Painel direito: dados do cliente + agendamento
  - Atribuição de atendente
  - Envio de mensagens rápidas
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import agente, auth, db, evolution

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Página principal do atendimento
# ---------------------------------------------------------------------------


@router.get("/atendimento", response_class=HTMLResponse)
def pagina_atendimento(request: Request, _: db.Usuario = Depends(auth.login_required)):
    usuario = auth.login_required(request)
    return templates.TemplateResponse(
        request,
        "atendimento.html",
        {
            "config": db.get_config(),
            "instancias": db.listar_instancias(),
            "vagas": db.listar_vagas(),
            "usuarios": db.listar_usuarios(),
            "servicos": db.listar_servicos_ativos(),
            "usuario": usuario,
            "usuario_atual_id": usuario.id,
        },
    )


# ---------------------------------------------------------------------------
# API de conversas para o frontend
# ---------------------------------------------------------------------------


@router.get("/atendimento/api/conversas")
def api_conversas(
    request: Request,
    _: db.Usuario = Depends(auth.login_required),
    busca: str = "",
    filtro: str = "",  # "minhas" | "pendentes" | "todas"
):
    """Lista de conversas para a sidebar."""
    usuario = auth.login_required(request)
    from .whatsapp import get_instancia_do_contato

    # Atendente: só ve conversas das suas instancias designadas
    instancias_usuario: set[str] = set()
    if usuario.papel != "admin":
        for inst in db.instancias_do_usuario(usuario.id):
            instancias_usuario.add(inst.nome)

    clientes = {c.telefone: c for c in db.listar_clientes()}
    itens: list[dict] = []
    vistos: set[str] = set()

    from .phone import normalizar

    for conv in db.listar_conversas():
        norm = normalizar(conv.telefone) or conv.telefone

        # Filtro por instancia do atendente
        if instancias_usuario:
            inst_conv = get_instancia_do_contato(norm)
            if inst_conv not in instancias_usuario:
                continue

        vistos.add(norm)
        cli = clientes.get(norm)
        ags = db.agendamentos_do_telefone(norm)
        ultimo_ag = ags[-1] if ags else None

        bolhas = agente.historico_para_bolhas(conv.historico)
        ultima = bolhas[-1] if bolhas else None
        preview = ultima["texto"][:120] if ultima else ""

        # Filtro de busca
        nome_cli = cli.nome if cli and cli.nome else ""
        if busca and busca.lower() not in nome_cli.lower() and busca not in norm:
            continue

        item = {
            "telefone": norm,
            "nome": nome_cli or norm,
            "pausado": bool(cli and cli.bot_pausado),
            "preview": preview,
            "quem": ultima["quem"] if ultima else "",
            "hora": ultima["hora"] if ultima else "",
            "nao_lido": 0,
            "agendamento": {
                "id": ultimo_ag.id,
                "servico": db.get_servico(ultimo_ag.servico_id).nome if ultimo_ag and ultimo_ag.servico_id else (ultimo_ag.descricao or ""),
                "inicio": ultimo_ag.inicio if ultimo_ag else "",
                "status": ultimo_ag.status if ultimo_ag else "",
                "placa": ultimo_ag.placa if ultimo_ag else "",
                "veiculo": ultimo_ag.veiculo if ultimo_ag else "",
                "vaga_id": ultimo_ag.vaga_id if ultimo_ag else None,
            } if ultimo_ag else None,
            "_ordem": conv.atualizado_em or "",
        }

        if filtro == "minhas" and item.get("agendamento", {}).get("atendente_id"):
            pass  # TODO: filtro por atendente

        itens.append(item)

    # Contatos sem conversa
    for tel, cli in clientes.items():
        if tel in vistos:
            continue
        if busca and busca.lower() not in (cli.nome or "").lower():
            continue
        itens.append({
            "telefone": tel,
            "nome": cli.nome or tel,
            "pausado": bool(cli.bot_pausado),
            "preview": "",
            "quem": "",
            "hora": "",
            "nao_lido": 0,
            "agendamento": None,
            "_ordem": "",
        })

    itens.sort(key=lambda x: x["_ordem"], reverse=True)
    for it in itens:
        it.pop("_ordem", None)
    return {"conversas": itens}


@router.get("/atendimento/api/conversas/{telefone}")
def api_conversa_detalhe(telefone: str, _: db.Usuario = Depends(auth.login_required)):
    """Detalhe de uma conversa: mensagens + dados do cliente + agendamentos."""
    from .phone import normalizar

    norm = normalizar(telefone) or telefone
    cli = db.get_cliente(norm)
    bruto = db.get_conversa(db.resolver_chave_conversa(telefone))
    agendamentos = [db.como_dict(a) for a in db.agendamentos_do_telefone(norm)]
    servicos = {s.id: s.nome for s in db.listar_todos_servicos()}

    return {
        "telefone": norm,
        "nome": cli.nome if cli and cli.nome else norm,
        "pausado": bool(cli and cli.bot_pausado),
        "mensagens": agente.historico_para_bolhas(bruto),
        "agendamentos": [
            {
                **a,
                "servico_nome": servicos.get(a.get("servico_id")) or a.get("descricao") or "",
                "vaga_nome": db.get_vaga(a.get("vaga_id")).nome if a.get("vaga_id") and db.get_vaga(a["vaga_id"]) else "",
            }
            for a in agendamentos
        ],
    }


@router.post("/atendimento/api/conversas/{telefone}/enviar")
async def api_conversa_enviar(
    telefone: str,
    request: Request,
    _: db.Usuario = Depends(auth.login_required),
    texto: str = Form(...),
):
    """Envia mensagem manual pelo WhatsApp."""
    from .whatsapp import enviar_bolhas, get_instancia_do_contato

    msg = texto.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    numero = re.sub(r"\D", "", telefone)
    if not numero:
        raise HTTPException(status_code=400, detail="Telefone inválido.")

    instancia = get_instancia_do_contato(telefone)
    try:
        await enviar_bolhas(numero, msg, instancia=instancia)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    usuario = auth.login_required(request)
    nome_atendente = usuario.nome if usuario else "bot"
    agente.registrar_na_memoria(
        db.resolver_chave_conversa(telefone), msg, f"{nome_atendente} (atendente)"
    )
    return {"ok": True}


@router.post("/atendimento/api/conversas/{telefone}/pausa")
def api_conversa_pausa(
    telefone: str,
    _: db.Usuario = Depends(auth.login_required),
    pausar: bool = Form(...),
):
    from .phone import mesmo_numero
    if mesmo_numero(telefone, db.get_config().telefone_dono):
        raise HTTPException(status_code=400, detail="O dono não pode ser pausado.")
    c = db.set_pausa_cliente(telefone, pausar)
    return {"ok": True, "telefone": c.telefone, "bot_pausado": c.bot_pausado}


# ---------------------------------------------------------------------------
# API de agendamentos rápidos
# ---------------------------------------------------------------------------


@router.post("/atendimento/api/agendamento")
def api_criar_agendamento(
    request: Request,
    _: db.Usuario = Depends(auth.login_required),
    servico_id: int | None = Form(None),
    descricao: str = Form(""),
    telefone_cliente: str = Form(...),
    nome_cliente: str = Form(...),
    data: str = Form(...),
    veiculo: str = Form(""),
    placa: str = Form(""),
    observacoes: str = Form(""),
):
    from datetime import date, datetime, time
    from .phone import normalizar

    usuario = auth.login_required(request)
    servico = db.get_servico(servico_id) if servico_id else None
    if not servico and not descricao.strip():
        raise HTTPException(status_code=400, detail="Informe o sintoma/descrição ou selecione um serviço.")

    try:
        dia = date.fromisoformat(data)
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")

    horarios = db.horarios_do_dia(dia.weekday())
    if not horarios:
        raise HTTPException(status_code=400, detail="Sem expediente nesta data.")
    dt_inicio = datetime.combine(dia, time.fromisoformat(horarios[0].inicio))
    dt_fim = datetime.combine(dia, time.fromisoformat(horarios[-1].fim))

    ag = db.criar_agendamento(
        servico_id=servico.id if servico else None,
        telefone_cliente=normalizar(telefone_cliente) or telefone_cliente,
        nome_cliente=nome_cliente.strip(),
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=dt_fim.isoformat(timespec="minutes"),
        observacoes=observacoes.strip(),
        veiculo=veiculo.strip(),
        placa=placa.strip().upper(),
        usuario_id=usuario.id,
        descricao=descricao.strip(),
    )
    if not ag:
        raise HTTPException(status_code=409, detail="Sem vagas disponíveis nesta data.")
    return {"ok": True, "agendamento": db.como_dict(ag)}


@router.post("/atendimento/api/agendamento/{agendamento_id}/status")
def api_alterar_status(
    agendamento_id: int,
    _: db.Usuario = Depends(auth.login_required),
    status: str = Form(...),
):
    if status == "confirmado":
        ok = db.confirmar_agendamento(agendamento_id)
    elif status == "cancelado":
        ok = db.cancelar_agendamento(agendamento_id)
    else:
        raise HTTPException(status_code=400, detail="Status inválido.")
    if not ok:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API de vagas
# ---------------------------------------------------------------------------


@router.get("/atendimento/api/vagas")
def api_vagas(_: db.Usuario = Depends(auth.login_required), data: str = ""):
    """Estado das vagas para um dia — quais ocupadas/livres com detalhes."""
    vagas = db.listar_vagas()
    resultado = []
    for v in vagas:
        ocupantes = []
        if data:
            ags = db.listar_agendamentos()
            for a in ags:
                if a.vaga_id == v.id and a.inicio.startswith(data):
                    ocupantes.append({
                        "id": a.id,
                        "nome": a.nome_cliente,
                        "placa": a.placa,
                        "servico": db.get_servico(a.servico_id).nome if a.servico_id else (a.descricao or ""),
                        "inicio": a.inicio,
                        "fim": a.fim,
                        "status": a.status,
                    })
        resultado.append({
            "id": v.id,
            "nome": v.nome,
            "descricao": v.descricao,
            "ocupada": len(ocupantes) > 0,
            "ocupantes": ocupantes,
        })
    return {"vagas": resultado}
