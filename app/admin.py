"""Painel web de configuração (/admin) + login + gestão de usuários/instâncias/vagas.

Autenticação:
  - Páginas HTML (/admin, /admin/...): sessão (tabela Usuario)
  - APIs JS (/admin/whatsapp/*, /admin/ia/*, etc): HTTP Basic legado
  - /admin/login: formulário de login
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import agente, auth, db, evolution, ia, tarefas
from .config import settings
from .phone import formatar_internacional, mesmo_numero, normalizar
from .tools import _agora_local

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()


# ---------------------------------------------------------------------------
# Sessão: redireciona para login se não autenticado (para páginas HTML)
# ---------------------------------------------------------------------------

def sessao_ou_redirect(request: Request) -> db.Usuario:
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    u = db.get_usuario(usuario_id)
    if not u or not u.ativo:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return u


# ---------------------------------------------------------------------------
# Auth para páginas HTML (sessão → redireciona p/ login se não tiver)
# ---------------------------------------------------------------------------

def autenticar_pagina(request: Request) -> str:
    """Redireciona para /admin/login se não estiver logado via sessão."""
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    u = db.get_usuario(usuario_id)
    if not u or not u.ativo:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return u.nome


# ---------------------------------------------------------------------------
# Auth para APIs JS (sessão + fallback HTTP Basic)
# ---------------------------------------------------------------------------

def autenticar(request: Request, cred: HTTPBasicCredentials = Depends(security)) -> str:
    """Tenta sessão primeiro; fallback HTTP Basic para chamadas JS/curl."""
    usuario_id = request.session.get("usuario_id")
    if usuario_id:
        u = db.get_usuario(usuario_id)
        if u and u.ativo:
            return u.nome
    ok_user = secrets.compare_digest(cred.username, settings.admin_user)
    ok_pass = secrets.compare_digest(cred.password, settings.admin_pass)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return cred.username


# ---------------------------------------------------------------------------
# Login / Logout (sessão)
# ---------------------------------------------------------------------------


@router.get("/admin/login", response_class=HTMLResponse)
def pagina_login(request: Request):
    usuario_id = request.session.get("usuario_id")
    if usuario_id and db.get_usuario(usuario_id):
        return RedirectResponse("/admin")
    return templates.TemplateResponse(request, "login.html", {"erro": ""})


@router.post("/admin/login")
def fazer_login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
):
    u = auth.autenticar_usuario(email, senha)
    if not u:
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": "E-mail ou senha inválidos."},
            status_code=401,
        )
    request.session["usuario_id"] = u.id
    request.session["usuario_nome"] = u.nome
    request.session["usuario_papel"] = u.papel
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/logout")
def fazer_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login")


# ---------------------------------------------------------------------------
# Painel principal (paginas)
# ---------------------------------------------------------------------------


def _contexto_base() -> dict:
    """Dados comuns a todas as paginas do admin."""
    return {
        "config": db.get_config(),
        "usuarios": db.listar_usuarios(),
        "instancias": db.listar_instancias(),
        "provedores_ia": ia.PROVEDORES,
    }


@router.get("/admin", response_class=HTMLResponse)
def pagina_dashboard(request: Request, _: str = Depends(autenticar_pagina)):
    return templates.TemplateResponse(
        request, "admin_dashboard.html",
        {"active_page": "dashboard", **_contexto_base()},
    )


@router.get("/admin/agenda", response_class=HTMLResponse)
def pagina_agenda(request: Request, ativos: str = "", _: str = Depends(autenticar_pagina)):
    servicos = db.listar_todos_servicos()
    nome_por_id = {s.id: s.nome for s in servicos}
    agendamentos = sorted(db.listar_agendamentos(), key=lambda a: a.inicio)
    bloqueios = sorted(db.listar_bloqueios(), key=lambda b: (b.data, b.inicio or ""))
    horarios = db.listar_horarios()
    horarios_por_dia: dict[int, list] = {d: [] for d in range(7)}
    for h in horarios:
        if 0 <= h.dia_semana <= 6:
            horarios_por_dia[h.dia_semana].append(h)
    vagas = db.listar_vagas()
    lembrete = db.get_lembrete_config()

    # Atendente: filtra apenas ativos + instancia designada
    usuario_id = request.session.get("usuario_id")
    usuario_papel = request.session.get("usuario_papel", "admin")
    if ativos or usuario_papel != "admin":
        ativos_telefones: set[str] = set()
        for i in db.listar_instancias():
            if i.usuario_id == usuario_id:
                if i.numero:
                    ativos_telefones.add(i.numero)
        agendamentos = [
            a for a in agendamentos
            if a.status not in ("cancelado", "finalizado")
            and (not ativos_telefones or a.telefone_cliente in ativos_telefones
                 or any(db.get_conversa(a.telefone_cliente) for _ in [1]))
        ]

    return templates.TemplateResponse(
        request, "admin_agenda.html",
        {
            "active_page": "agenda",
            "servicos": servicos,
            "servico_nome": nome_por_id,
            "bloqueios": bloqueios,
            "agendamentos": agendamentos,
            "n_ativos": sum(1 for s in servicos if s.ativo),
            "horarios_por_dia": horarios_por_dia,
            "n_horarios": len(horarios),
            "evolution_url": settings.evolution_external_url,
            "vagas": vagas,
            "lembrete": lembrete,
            "apenas_ativos": True,
            **_contexto_base(),
        },
    )


@router.get("/admin/servicos", response_class=HTMLResponse)
def pagina_servicos(request: Request, _: str = Depends(autenticar_pagina)):
    servicos = db.listar_todos_servicos()
    bloqueios = sorted(db.listar_bloqueios(), key=lambda b: (b.data, b.inicio or ""))
    return templates.TemplateResponse(
        request, "admin_servicos.html",
        {
            "active_page": "servicos",
            "servicos": servicos,
            "bloqueios": bloqueios,
            "n_ativos": sum(1 for s in servicos if s.ativo),
            **_contexto_base(),
        },
    )


@router.get("/admin/config", response_class=HTMLResponse)
def pagina_config(request: Request, _: str = Depends(autenticar_pagina)):
    lembrete = db.get_lembrete_config()
    destinos = db.listar_destinos_transferencia()
    return templates.TemplateResponse(
        request, "admin_config.html",
        {
            "active_page": "config",
            "lembrete": lembrete,
            "destinos": destinos,
            "instancias": db.listar_instancias(),
            **_contexto_base(),
        },
    )


# ---------------------------------------------------------------------------
# Usuários (multi-usuário) — sessão + papel admin
# ---------------------------------------------------------------------------


@router.post("/admin/usuario")
def novo_usuario(
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    nome: str = Form(...),
    email: str = Form(...),
    senha: str = Form(...),
    papel: str = Form("atendente"),
    telefone: str = Form(""),
):
    if db.get_usuario_por_email(email):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")
    db.criar_usuario(
        nome=nome.strip(),
        email=email.strip().lower(),
        senha_hash=auth.hash_senha(senha),
        papel=papel,
        telefone=telefone.strip(),
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/usuario/{usuario_id}/editar")
def editar_usuario(
    usuario_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    nome: str = Form(...),
    papel: str = Form(...),
    telefone: str = Form(""),
    ativo: bool = Form(False),
):
    db.editar_usuario(usuario_id, nome=nome.strip(), papel=papel, telefone=telefone.strip(), ativo=ativo)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/usuario/{usuario_id}/resetar-senha")
def resetar_senha(
    usuario_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    senha: str = Form(...),
):
    db.editar_usuario(usuario_id, senha_hash=auth.hash_senha(senha))
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Instâncias WhatsApp (multi-instância)
# ---------------------------------------------------------------------------


@router.post("/admin/instancia")
def nova_instancia(
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    nome: str = Form(...),
    usuario_id: int = Form(0),
):
    if db.get_instancia_por_nome(nome.strip()):
        raise HTTPException(status_code=409, detail="Instância já existe.")
    inst = db.criar_instancia(
        nome=nome.strip(),
        usuario_id=usuario_id or None,
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/instancia/{instancia_id}/editar")
def editar_instancia(
    instancia_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    numero: str = Form(""),
    usuario_id: int = Form(0),
    ativo: bool = Form(False),
):
    db.editar_instancia(instancia_id, numero=numero.strip(), usuario_id=usuario_id or None, ativo=ativo)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/instancia/{instancia_id}/excluir")
def excluir_instancia(
    instancia_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
):
    inst = db.get_instancia(instancia_id)
    if inst:
        db.editar_instancia(instancia_id, ativo=False)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Vagas (boxes de atendimento)
# ---------------------------------------------------------------------------


@router.post("/admin/vaga")
def nova_vaga(
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    nome: str = Form(...),
    descricao: str = Form(""),
    ordem: int = Form(0),
):
    db.criar_vaga(nome=nome.strip(), descricao=descricao.strip(), ordem=ordem)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/vaga/{vaga_id}/editar")
def editar_vaga(
    vaga_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    nome: str = Form(...),
    descricao: str = Form(""),
    ativo: bool = Form(False),
    ordem: int = Form(0),
):
    db.editar_vaga(vaga_id, nome=nome.strip(), descricao=descricao.strip(), ativo=ativo, ordem=ordem)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/vaga/{vaga_id}/excluir")
def excluir_vaga(
    vaga_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
):
    db.deletar_vaga(vaga_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Lembretes
# ---------------------------------------------------------------------------


@router.post("/admin/lembrete")
def salvar_lembrete(
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    ativo: bool = Form(False),
    horas_antes: int = Form(48),
    mensagem: str = Form(""),
    horas_antes2: int = Form(24),
    mensagem2: str = Form(""),
    ativo2: bool = Form(False),
    confirmar_automatically: bool = Form(False),
    cancelar_se_nao_confirmar: bool = Form(False),
    timeout_horas: int = Form(6),
):
    db.update_lembrete_config(
        ativo=ativo,
        horas_antes=horas_antes,
        mensagem=mensagem.strip(),
        horas_antes2=horas_antes2,
        mensagem2=mensagem2.strip(),
        ativo2=ativo2,
        confirmar_automatically=confirmar_automatically,
        cancelar_se_nao_confirmar=cancelar_se_nao_confirmar,
        timeout_horas=timeout_horas,
    )
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# WhatsApp (painel — compatível com multi-instância)
# ---------------------------------------------------------------------------


@router.get("/admin/whatsapp/estado")
def whatsapp_estado(_: str = Depends(autenticar)):
    try:
        return evolution.estado()
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/qr")
def whatsapp_qr(_: str = Depends(autenticar)):
    try:
        return evolution.conectar()
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/whatsapp/desconectar")
def whatsapp_desconectar(_: str = Depends(autenticar)):
    try:
        return evolution.desconectar()
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/foto")
def whatsapp_foto(numero: str, _: str = Depends(autenticar), instancia: str = ""):
    try:
        return {"url": evolution.foto_perfil(numero, instancia or None)}
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/checar")
def whatsapp_checar(numero: str, _: str = Depends(autenticar), instancia: str = ""):
    try:
        item = evolution.checar_numero(numero, instancia or None)
    except Exception as e:
        return JSONResponse({"erro": "Não foi possível checar o número."}, status_code=502)
    existe = bool(item and item.get("exists"))
    jid = (item or {}).get("jid") or ""
    canon = normalizar(jid) or normalizar(numero)
    foto = None
    if existe and canon:
        try:
            foto = evolution.foto_perfil(canon)
        except Exception:
            foto = None
    return {
        "existe": existe,
        "numero": canon,
        "numero_fmt": formatar_internacional(canon or numero),
        "foto": foto,
    }


# ---------------------------------------------------------------------------
# Multi-instância — estado individual
# ---------------------------------------------------------------------------


@router.get("/admin/instancia/{instancia_id}/estado")
def instancia_estado(instancia_id: int, _: db.Usuario = Depends(auth.login_required)):
    inst = db.get_instancia(instancia_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    try:
        return evolution.estado_instancia(inst.nome)
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/instancia/{instancia_id}/qr")
def instancia_qr(instancia_id: int, _: db.Usuario = Depends(auth.login_required)):
    inst = db.get_instancia(instancia_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    try:
        return evolution.conectar_instancia(inst.nome)
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/instancia/{instancia_id}/desconectar")
def instancia_desconectar(instancia_id: int, _: db.Usuario = Depends(auth.login_required)):
    inst = db.get_instancia(instancia_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instância não encontrada.")
    try:
        return evolution.desconectar_instancia(inst.nome)
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


# ---------------------------------------------------------------------------
# IA, Prompts, Config, Horários, Agendamentos, Conversas, Tarefas, Bloqueios
# (mantidos iguais, apenas com o _: str = Depends(autenticar) legado)
# ---------------------------------------------------------------------------


@router.get("/admin/ia/estado")
def ia_estado(_: str = Depends(autenticar)):
    try:
        return ia.estado()
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/ia/modelos")
def ia_modelos(alvo: str, _: str = Depends(autenticar)):
    if alvo not in ia.ALVOS_COM_MODELO:
        raise HTTPException(status_code=400, detail="Alvo inválido.")
    try:
        return {"modelos": ia.listar_modelos(alvo)}
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/modelos-preview")
def ia_modelos_preview(
    _: str = Depends(autenticar),
    provedor: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form(""),
):
    preset = ia.PROVEDORES.get(provedor)
    if not preset:
        raise HTTPException(status_code=400, detail="Provedor desconhecido.")
    url = (preset["base_url"] or base_url).strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Provedor personalizado exige a base URL.")
    chave = api_key.strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Informe a chave de API.")
    try:
        return {"modelos": ia.listar_modelos_do_provedor(url, chave)}
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/credencial")
def ia_credencial(
    _: str = Depends(autenticar),
    alvo: str = Form(...),
    provedor: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form(""),
):
    if alvo not in ia.CRED_POR_ALVO:
        raise HTTPException(status_code=400, detail="Alvo inválido.")
    preset = ia.PROVEDORES.get(provedor)
    if not preset:
        raise HTTPException(status_code=400, detail="Provedor desconhecido.")
    if not preset[alvo]:
        raise HTTPException(status_code=400, detail=f"{preset['nome']} não suporta o uso '{alvo}'.")
    url = (preset["base_url"] or base_url).strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Provedor personalizado exige a base URL.")
    chave = api_key.strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Informe a chave de API.")
    try:
        return ia.atualizar_chave(alvo, chave, url)
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/modelo")
def ia_modelo(
    _: str = Depends(autenticar),
    alvo: str = Form(...),
    modelo: str = Form(...),
):
    if alvo not in ia.ALVOS_COM_MODELO:
        raise HTTPException(status_code=400, detail="Alvo inválido.")
    if not modelo.strip():
        raise HTTPException(status_code=400, detail="Informe o nome do modelo.")
    try:
        return ia.atualizar_modelo(alvo, modelo.strip())
    except Exception as e:
        return JSONResponse({"erro": str(e)}, status_code=502)


# ---------------------------------------------------------------------------
# Prompts do agente
# ---------------------------------------------------------------------------


@router.get("/admin/agente/prompt")
def agente_prompt(_: str = Depends(autenticar)):
    geral = db.get_prompt("geral")
    mcp_dono = db.get_prompt("mcp_dono")
    mcp_cliente = db.get_prompt("mcp_cliente")
    return {
        "fonte": "painel" if geral is not None else "padrao",
        "geral": geral if geral is not None else agente.seed_prompt_geral(settings.agent_system_prompt),
        "mcp_dono": mcp_dono if mcp_dono is not None else agente.PROMPT_MCP_DONO_PADRAO,
        "mcp_cliente": mcp_cliente if mcp_cliente is not None else agente.PROMPT_MCP_CLIENTE_PADRAO,
        "mcp_dono_padrao": agente.PROMPT_MCP_DONO_PADRAO,
        "mcp_cliente_padrao": agente.PROMPT_MCP_CLIENTE_PADRAO,
    }


@router.post("/admin/agente/prompt")
def agente_prompt_salvar(
    _: str = Depends(autenticar),
    geral: str = Form(...),
    mcp_dono: str = Form(...),
    mcp_cliente: str = Form(...),
):
    if not geral.strip():
        raise HTTPException(status_code=400, detail="A instrução geral não pode ficar vazia.")
    db.set_prompt("geral", geral.strip())
    db.set_prompt("mcp_dono", mcp_dono.strip())
    db.set_prompt("mcp_cliente", mcp_cliente.strip())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Config geral
# ---------------------------------------------------------------------------


@router.post("/admin/config")
def salvar_config(
    _: str = Depends(autenticar),  # autenticar ok — config admin
    telefone_dono: str = Form(...),
    fuso: str = Form(...),
    avisar_dono: bool = Form(False),
):
    db.update_config(telefone_dono=telefone_dono, fuso=fuso, avisar_dono=avisar_dono)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Horários
# ---------------------------------------------------------------------------


@router.post("/admin/horarios")
def salvar_horarios(
    _: str = Depends(autenticar),
    dia: list[int] = Form([]),
    inicio: list[str] = Form([]),
    fim: list[str] = Form([]),
):
    if not (len(dia) == len(inicio) == len(fim)):
        raise HTTPException(status_code=400, detail="Linhas de horário inconsistentes.")
    intervalos: list[tuple[int, str, str]] = []
    for d, ini, f in zip(dia, inicio, fim):
        if not 0 <= d <= 6:
            raise HTTPException(status_code=400, detail="Dia da semana inválido.")
        try:
            time.fromisoformat(ini), time.fromisoformat(f)
        except ValueError:
            raise HTTPException(status_code=400, detail="Horário inválido (use HH:MM).")
        if f <= ini:
            raise HTTPException(status_code=400, detail=f"Intervalo termina antes de começar ({ini}–{f}).")
        intervalos.append((d, ini, f))
    por_dia: dict[int, list[tuple[str, str]]] = {}
    for d, ini, f in intervalos:
        por_dia.setdefault(d, []).append((ini, f))
    for faixas in por_dia.values():
        faixas.sort()
        for (_i1, f1), (i2, _f2) in zip(faixas, faixas[1:]):
            if i2 < f1:
                raise HTTPException(status_code=400, detail=f"Intervalos sobrepostos no mesmo dia ({f1} × {i2}).")
    db.substituir_horarios(intervalos)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/horarios/restaurar")
def restaurar_horarios(_: str = Depends(autenticar)):
    db.restaurar_horarios_padrao()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/horarios/limpar")
def limpar_horarios(_: str = Depends(autenticar)):
    db.limpar_horarios()
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Serviços
# ---------------------------------------------------------------------------


@router.post("/admin/servico")
def novo_servico(
    _: str = Depends(autenticar),
    nome: str = Form(...),
    descricao: str = Form(...),
    valor: float = Form(...),
    duracao_min: int = Form(...),
):
    db.criar_servico(nome, descricao, valor, duracao_min)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/servico/{servico_id}/toggle")
def alternar_servico(servico_id: int, _: str = Depends(autenticar)):
    srv = db.get_servico(servico_id)
    if srv:
        db.editar_servico(servico_id, ativo=not srv.ativo)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/servico/{servico_id}/excluir")
def excluir_servico(servico_id: int, _: str = Depends(autenticar)):
    db.deletar_servico(servico_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Agendamentos
# ---------------------------------------------------------------------------


@router.get("/admin/agenda/slots")
def agenda_slots(data: str, servico_id: int, _: str = Depends(autenticar)):
    servico = db.get_servico(servico_id)
    if not servico:
        raise HTTPException(status_code=404, detail="Serviço não encontrado.")
    try:
        dia = date.fromisoformat(data)
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")
    intervalos = db.horarios_do_dia(dia.weekday())
    if not intervalos:
        return {"slots": [], "fechado": True}
    agora = _agora_local()
    passo = timedelta(minutes=servico.duracao_min)
    slots: list[dict] = []
    for janela in intervalos:
        atual = datetime.fromisoformat(f"{data}T{janela.inicio}")
        limite = datetime.fromisoformat(f"{data}T{janela.fim}")
        while atual + passo <= limite:
            ini = atual.isoformat(timespec="minutes")
            fim = (atual + passo).isoformat(timespec="minutes")
            livre = atual >= agora and db.horario_disponivel(ini, fim)
            slots.append({"inicio": atual.strftime("%H:%M"), "estado": "livre" if livre else "ocupado"})
            atual += passo
    return {"slots": slots, "fechado": False}


@router.post("/admin/agendamento")
def novo_agendamento(
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    servico_id: int = Form(...),
    nome_cliente: str = Form(...),
    telefone_cliente: str = Form(...),
    inicio: str = Form(...),
    observacoes: str = Form(""),
    veiculo: str = Form(""),
    placa: str = Form(""),
    modelo: str = Form(""),
    ano: str = Form(""),
):
    servico = db.get_servico(servico_id)
    if not servico:
        raise HTTPException(status_code=404, detail="Serviço não encontrado.")
    nome = nome_cliente.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
    tel = telefone_cliente.strip()
    if not tel:
        raise HTTPException(status_code=400, detail="Informe o telefone do cliente.")
    try:
        dt_inicio = datetime.fromisoformat(inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    fim = (dt_inicio + timedelta(minutes=servico.duracao_min)).isoformat(timespec="minutes")
    ag = db.criar_agendamento(
        servico_id=servico_id,
        telefone_cliente=normalizar(tel) or tel,
        nome_cliente=nome,
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=fim,
        observacoes=observacoes.strip(),
        veiculo=veiculo.strip(),
        placa=placa.strip().upper(),
        modelo=modelo.strip(),
        ano=ano.strip(),
    )
    if not ag:
        raise HTTPException(status_code=409, detail="Sem vagas disponíveis no horário.")
    return RedirectResponse("/admin", status_code=303)


def _avisar_cliente_permitido(ag: db.Agendamento) -> bool:
    return not mesmo_numero(ag.telefone_cliente, db.get_config().telefone_dono)


@router.post("/admin/agendamento/{agendamento_id}/cancelar")
def cancelar_agendamento(
    agendamento_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    avisar_cliente: str = Form(""),
):
    ag = db.get_agendamento(agendamento_id)
    cancelou = db.cancelar_agendamento(agendamento_id)
    if cancelou and avisar_cliente and ag and _avisar_cliente_permitido(ag):
        db.criar_aviso_cliente(ag, "cancelado", _agora_local().isoformat(timespec="minutes"))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/agendamento/{agendamento_id}/reagendar")
def reagendar_agendamento(
    agendamento_id: int,
    request: Request,
    _: db.Usuario = Depends(auth.admin_required),
    novo_inicio: str = Form(...),
    avisar_cliente: str = Form(""),
):
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    servico = db.get_servico(ag.servico_id)
    try:
        dt_inicio = datetime.fromisoformat(novo_inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    inicio_anterior = ag.inicio
    dur = servico.duracao_min if servico else 30
    novo_fim = (dt_inicio + timedelta(minutes=dur)).isoformat(timespec="minutes")
    ok = db.reagendar_agendamento(agendamento_id, dt_inicio.isoformat(timespec="minutes"), novo_fim)
    if ok and avisar_cliente and _avisar_cliente_permitido(ag):
        db.criar_aviso_cliente(
            db.get_agendamento(agendamento_id), "reagendado",
            _agora_local().isoformat(timespec="minutes"),
            inicio_anterior=inicio_anterior,
        )
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Conversas
# ---------------------------------------------------------------------------


def _resumo_conversas() -> list[dict]:
    clientes = {c.telefone: c for c in db.listar_clientes()}
    itens: list[dict] = []
    vistos: set[str] = set()
    for conv in db.listar_conversas():
        norm = normalizar(conv.telefone) or conv.telefone
        vistos.add(norm)
        bolhas = agente.historico_para_bolhas(conv.historico)
        ultima = bolhas[-1] if bolhas else None
        cli = clientes.get(norm)
        itens.append({
            "telefone": norm,
            "nome": cli.nome if cli and cli.nome else "",
            "pausado": bool(cli and cli.bot_pausado),
            "preview": (ultima["texto"][:90] if ultima else ""),
            "quem": ultima["quem"] if ultima else "",
            "hora": ultima["hora"] if ultima else "",
            "_ordem": conv.atualizado_em or "",
        })
    for tel, cli in clientes.items():
        if tel in vistos:
            continue
        itens.append({
            "telefone": tel, "nome": cli.nome or "", "pausado": bool(cli.bot_pausado),
            "preview": "", "quem": "", "hora": "", "_ordem": "",
        })
    itens.sort(key=lambda x: x["_ordem"], reverse=True)
    for it in itens:
        it.pop("_ordem", None)
    return itens


@router.get("/admin/conversas")
def listar_conversas(_: str = Depends(autenticar)):
    return {"conversas": _resumo_conversas()}


@router.get("/admin/conversas/{telefone}")
def conversa_detalhe(telefone: str, _: str = Depends(autenticar)):
    norm = normalizar(telefone) or telefone
    cli = db.get_cliente(norm)
    bruto = db.get_conversa(db.resolver_chave_conversa(telefone))
    return {
        "telefone": norm,
        "nome": cli.nome if cli and cli.nome else "",
        "pausado": bool(cli and cli.bot_pausado),
        "mensagens": agente.historico_para_bolhas(bruto),
    }


@router.post("/admin/conversas/{telefone}/pausa")
def conversa_pausa(telefone: str, _: str = Depends(autenticar), pausar: bool = Form(...)):
    if mesmo_numero(telefone, db.get_config().telefone_dono):
        raise HTTPException(status_code=400, detail="O dono não pode ser pausado.")
    c = db.set_pausa_cliente(telefone, pausar)
    return {"ok": True, "telefone": c.telefone, "bot_pausado": c.bot_pausado}


@router.post("/admin/conversas/{telefone}/enviar")
async def conversa_enviar(
    telefone: str,
    request: Request,
    _: str = Depends(autenticar),
    texto: str = Form(...),
):
    from .whatsapp import get_instancia_do_contato
    msg = texto.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Escreva uma mensagem antes de enviar.")
    numero = re.sub(r"\D", "", telefone)
    if not numero:
        raise HTTPException(status_code=400, detail="Telefone inválido.")
    digitando_ms = int(min(0.3 + len(msg) * 0.012, 1.8) * 1000)
    instancia = get_instancia_do_contato(telefone)
    try:
        await evolution.enviar_texto(numero, msg, digitando_ms=digitando_ms, timeout=8.0, instancia=instancia)
    except Exception as e:
        raise HTTPException(status_code=502, detail="Não foi possível enviar pelo WhatsApp.") from e
    usuario_id = request.session.get("usuario_id")
    usuario = db.get_usuario(usuario_id) if usuario_id else None
    nome_atendente = usuario.nome if usuario else "admin"
    agente.registrar_na_memoria(db.resolver_chave_conversa(telefone), msg, f"{nome_atendente} (admin)")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Proatividade
# ---------------------------------------------------------------------------


@router.get("/admin/tarefas/estado")
def tarefas_estado(_: str = Depends(autenticar)):
    return {"tarefas": [tarefas.descrever_tarefa(t) for t in db.listar_tarefas_painel()]}


@router.post("/admin/tarefas/{tarefa_id}/cancelar")
def tarefa_cancelar(tarefa_id: int, _: str = Depends(autenticar)):
    if not db.cancelar_tarefa(tarefa_id):
        raise HTTPException(status_code=409, detail="Tarefa não está mais pendente na fila.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bloqueios
# ---------------------------------------------------------------------------


@router.post("/admin/bloqueio")
def novo_bloqueio(
    _: str = Depends(autenticar),
    data: str = Form(...),
    data_fim: str = Form(""),
    inicio: str = Form(""),
    fim: str = Form(""),
    motivo: str = Form(""),
):
    if data_fim and data_fim < data:
        raise HTTPException(status_code=400, detail="Data final anterior à inicial.")
    db.criar_bloqueio(data=data, inicio=inicio or None, fim=fim or None, motivo=motivo, data_fim=data_fim or None)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/bloqueio/{bloqueio_id}/excluir")
def excluir_bloqueio(bloqueio_id: int, _: str = Depends(autenticar)):
    db.remover_bloqueio(bloqueio_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Destinos de Transferencia
# ---------------------------------------------------------------------------

@router.get("/admin/transferencia/destinos")
def listar_destinos_api(_: str = Depends(autenticar)):
    return {"destinos": [{"id": d.id, "nome": d.nome, "telefone": d.telefone, "instancia_id": d.instancia_id, "mensagem": d.mensagem, "ativo": d.ativo} for d in db.listar_destinos_transferencia()]}


@router.post("/admin/transferencia/destino")
def criar_destino_api(
    _: str = Depends(autenticar),
    nome: str = Form(...),
    mensagem: str = Form(""),
    telefone: str = Form(""),
    instancia_id: int = Form(0),
):
    inst_id = instancia_id or None
    db.criar_destino_transferencia(nome=nome.strip().lower(), mensagem=mensagem.strip(), telefone=telefone.strip(), instancia_id=inst_id)
    return {"ok": True}


@router.post("/admin/transferencia/destino/{destino_id}/excluir")
def excluir_destino_api(destino_id: int, _: db.Usuario = Depends(auth.admin_required)):
    db.deletar_destino_transferencia(destino_id)
    return {"ok": True}
