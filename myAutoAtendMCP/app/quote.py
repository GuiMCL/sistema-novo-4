"""Central de Cotações — módulo independente de cotação de peças com fornecedores.

Este módulo é totalmente desacoplado do módulo de atendimento:
  - Sessões WhatsApp do tipo 'cotacao' (nunca 'atendimento')
  - Banco de dados próprio (tabelas quote_*)
  - Fornecedores vs Clientes (nunca se misturam)
  - Roteiro próprio sem interferência no fluxo de atendimento
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import auth, db, evolution
from .consultar_placa import PlacaError, consultar_placa

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Auth helper (sessão — mesma do admin)
# ---------------------------------------------------------------------------

def _usuario(request: Request) -> db.Usuario:
    """Retorna usuário autenticado ou redireciona."""
    return auth.login_required(request)


def _admin(request: Request) -> db.Usuario:
    """Apenas admin."""
    return auth.admin_required(request)


# ---------------------------------------------------------------------------
# Páginas HTML
# ---------------------------------------------------------------------------


@router.get("/admin/quote/dashboard", response_class=HTMLResponse)
def quote_dashboard(request: Request, u: db.Usuario = Depends(_usuario)):
    stats = db.get_quote_dashboard_stats()
    return templates.TemplateResponse(
        request, "quote/dashboard.html",
        {"request": request, "stats": stats, "active_page": "quote-dashboard"},
    )


@router.get("/admin/quote/categorias", response_class=HTMLResponse)
def quote_categorias(request: Request, u: db.Usuario = Depends(_usuario)):
    categorias = db.listar_quote_categorias()
    return templates.TemplateResponse(
        request, "quote/categorias.html",
        {
            "request": request,
            "categorias": categorias,
            "active_page": "quote-categorias",
        },
    )


@router.get("/admin/quote/pecas", response_class=HTMLResponse)
def quote_pecas(request: Request, u: db.Usuario = Depends(_usuario)):
    categorias = db.listar_quote_categorias(apenas_ativas=True)
    pecas = db.listar_quote_pecas()
    return templates.TemplateResponse(
        request, "quote/pecas.html",
        {
            "request": request,
            "categorias": categorias,
            "pecas": pecas,
            "active_page": "quote-pecas",
        },
    )


@router.get("/admin/quote/fornecedores", response_class=HTMLResponse)
def quote_fornecedores(request: Request, u: db.Usuario = Depends(_usuario)):
    fornecedores_raw = db.listar_quote_fornecedores()
    categorias = db.listar_quote_categorias(apenas_ativas=True)
    fornecedores = []
    for f in fornecedores_raw:
        d = f.model_dump()
        cats = db.listar_categorias_do_fornecedor(f.id)
        d["categorias"] = [{"id": c.id, "nome": c.nome} for c in cats]
        fornecedores.append(d)
    return templates.TemplateResponse(
        request, "quote/fornecedores.html",
        {
            "request": request,
            "fornecedores": fornecedores,
            "categorias": categorias,
            "active_page": "quote-fornecedores",
        },
    )


@router.get("/admin/quote/contatos", response_class=HTMLResponse)
def quote_contatos(request: Request, u: db.Usuario = Depends(_usuario)):
    fornecedores = db.listar_quote_fornecedores(apenas_ativos=True)
    contatos_raw = db.listar_quote_contatos()
    forn_map = {f.id: f.nome for f in fornecedores}
    contatos = []
    for c in contatos_raw:
        d = c.model_dump()
        d["fornecedor_nome"] = forn_map.get(c.supplier_id, "")
        contatos.append(d)
    return templates.TemplateResponse(
        request, "quote/contatos.html",
        {
            "request": request,
            "fornecedores": fornecedores,
            "contatos": contatos,
            "active_page": "quote-contatos",
        },
    )


@router.get("/admin/quote/sessoes", response_class=HTMLResponse)
def quote_sessoes(request: Request, u: db.Usuario = Depends(_usuario)):
    sessoes = db.listar_quote_sessoes_todas()
    return templates.TemplateResponse(
        request, "quote/sessoes.html",
        {
            "request": request,
            "sessoes": sessoes,
            "active_page": "quote-sessoes",
        },
    )


@router.get("/admin/quote/nova", response_class=HTMLResponse)
def quote_nova(request: Request, u: db.Usuario = Depends(_usuario)):
    categorias = db.listar_quote_categorias(apenas_ativas=True)
    sessoes = db.listar_quote_sessoes()
    template = db.get_quote_template()
    return templates.TemplateResponse(
        request, "quote/nova_cotacao.html",
        {
            "request": request,
            "categorias": categorias,
            "sessoes": sessoes,
            "template": template,
            "active_page": "quote-nova",
        },
    )


@router.get("/admin/quote/historico", response_class=HTMLResponse)
def quote_historico(request: Request, status_filtro: str = "", u: db.Usuario = Depends(_usuario)):
    requests_raw = db.listar_quote_requests(status=status_filtro or None)
    categorias = {c.id: c.nome for c in db.listar_quote_categorias()}
    requests = []
    for r in requests_raw:
        d = r.model_dump()
        d["categoria_nome"] = categorias.get(r.categoria_id, "")
        usuario = db.get_usuario(r.usuario_id)
        d["usuario_nome"] = usuario.nome if usuario else ""
        requests.append(d)
    return templates.TemplateResponse(
        request, "quote/historico.html",
        {
            "request": request,
            "requests": requests,
            "status_filtro": status_filtro,
            "active_page": "quote-historico",
        },
    )


@router.get("/admin/quote/configuracoes", response_class=HTMLResponse)
def quote_configuracoes(request: Request, u: db.Usuario = Depends(_usuario)):
    template = db.get_quote_template()
    templates_list = db.listar_quote_templates()
    config = db.get_config()
    return templates.TemplateResponse(
        request, "quote/configuracoes.html",
        {
            "request": request,
            "template": template,
            "templates": templates_list,
            "config": config,
            "active_page": "quote-config",
        },
    )


@router.get("/admin/quote/{request_id}", response_class=HTMLResponse)
def quote_visualizar(request: Request, request_id: int, u: db.Usuario = Depends(_usuario)):
    req = db.get_quote_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    itens = list(db.listar_quote_pecas())
    request_itens = []  # will fetch via API
    historico = db.listar_historico_cotacao(request_id)
    return templates.TemplateResponse(
        request, "quote/visualizar.html",
        {
            "request": request,
            "req": req,
            "itens": itens,
            "historico": historico,
            "active_page": "quote-historico",
        },
    )


# ---------------------------------------------------------------------------
# API: Categorias
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/categorias")
def api_listar_categorias(u: db.Usuario = Depends(_usuario)):
    cats = db.listar_quote_categorias()
    return [c.model_dump() for c in cats]


@router.post("/admin/quote/api/categorias")
def api_criar_categoria(
    nome: str = Form(...), descricao: str = Form(""), ordem: int = Form(0),
    u: db.Usuario = Depends(_admin),
):
    cat = db.criar_quote_categoria(nome=nome, descricao=descricao, ordem=ordem)
    return cat.model_dump()


@router.put("/admin/quote/api/categorias/{categoria_id}")
def api_editar_categoria(
    categoria_id: int, nome: str = Form(...), descricao: str = Form(""),
    ordem: int = Form(0), ativo: bool = Form(True),
    u: db.Usuario = Depends(_admin),
):
    cat = db.editar_quote_categoria(categoria_id, nome=nome, descricao=descricao, ordem=ordem, ativo=ativo)
    if not cat:
        raise HTTPException(status_code=404, detail="Categoria não encontrada")
    return cat.model_dump()


@router.delete("/admin/quote/api/categorias/{categoria_id}")
def api_deletar_categoria(categoria_id: int, u: db.Usuario = Depends(_admin)):
    if not db.deletar_quote_categoria(categoria_id):
        raise HTTPException(status_code=404, detail="Categoria não encontrada")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Peças
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/pecas")
def api_listar_pecas(categoria_id: int = 0, u: db.Usuario = Depends(_usuario)):
    cat_id = categoria_id if categoria_id else None
    pecas = db.listar_quote_pecas(categoria_id=cat_id)
    result = []
    for p in pecas:
        d = p.model_dump()
        cat = db.get_quote_categoria(p.categoria_id)
        d["categoria_nome"] = cat.nome if cat else ""
        result.append(d)
    return result


@router.get("/admin/quote/api/pecas-por-categoria/{categoria_id}")
def api_pecas_por_categoria(categoria_id: int, u: db.Usuario = Depends(_usuario)):
    pecas = db.listar_quote_pecas(categoria_id=categoria_id, apenas_ativas=True)
    return [{"id": p.id, "nome": p.nome, "codigo_interno": p.codigo_interno, "marca": p.marca} for p in pecas]


@router.post("/admin/quote/api/pecas")
def api_criar_peca(
    nome: str = Form(...), categoria_id: int = Form(...),
    codigo_interno: str = Form(""), codigo_fabricante: str = Form(""),
    marca: str = Form(""), descricao: str = Form(""),
    observacoes: str = Form(""), imagem: str = Form(""),
    u: db.Usuario = Depends(_admin),
):
    p = db.criar_quote_peca(
        nome=nome, categoria_id=categoria_id,
        codigo_interno=codigo_interno, codigo_fabricante=codigo_fabricante,
        marca=marca, descricao=descricao, observacoes=observacoes, imagem=imagem,
    )
    return p.model_dump()


@router.put("/admin/quote/api/pecas/{peca_id}")
def api_editar_peca(
    peca_id: int, nome: str = Form(...), categoria_id: int = Form(...),
    codigo_interno: str = Form(""), codigo_fabricante: str = Form(""),
    marca: str = Form(""), descricao: str = Form(""),
    observacoes: str = Form(""), imagem: str = Form(""), ativo: bool = Form(True),
    u: db.Usuario = Depends(_admin),
):
    p = db.editar_quote_peca(
        peca_id, nome=nome, categoria_id=categoria_id,
        codigo_interno=codigo_interno, codigo_fabricante=codigo_fabricante,
        marca=marca, descricao=descricao, observacoes=observacoes,
        imagem=imagem, ativo=ativo,
    )
    if not p:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    return p.model_dump()


@router.delete("/admin/quote/api/pecas/{peca_id}")
def api_deletar_peca(peca_id: int, u: db.Usuario = Depends(_admin)):
    if not db.deletar_quote_peca(peca_id):
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Fornecedores
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/fornecedores")
def api_listar_fornecedores(u: db.Usuario = Depends(_usuario)):
    fornecedores = db.listar_quote_fornecedores()
    result = []
    for f in fornecedores:
        d = f.model_dump()
        cats = db.listar_categorias_do_fornecedor(f.id)
        d["categorias"] = [{"id": c.id, "nome": c.nome} for c in cats]
        result.append(d)
    return result


@router.get("/admin/quote/api/fornecedores/por-categoria/{categoria_id}")
def api_fornecedores_por_categoria(categoria_id: int, u: db.Usuario = Depends(_usuario)):
    fornecedores = db.fornecedores_por_categoria(categoria_id)
    return [{"id": f.id, "nome": f.nome, "empresa": f.empresa, "whatsapp": f.whatsapp} for f in fornecedores]


@router.get("/admin/quote/api/fornecedores/{fornecedor_id}")
def api_get_fornecedor(fornecedor_id: int, u: db.Usuario = Depends(_usuario)):
    f = db.get_quote_fornecedor(fornecedor_id)
    if not f:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
    d = f.model_dump()
    cats = db.listar_categorias_do_fornecedor(f.id)
    d["categorias"] = [c.id for c in cats]
    return d


@router.post("/admin/quote/api/fornecedores")
def api_criar_fornecedor(
    nome: str = Form(...), empresa: str = Form(""), whatsapp: str = Form(""),
    telefone: str = Form(""), email: str = Form(""), cidade: str = Form(""),
    estado: str = Form(""), observacoes: str = Form(""),
    categorias: str = Form("[]"),
    u: db.Usuario = Depends(_admin),
):
    f = db.criar_quote_fornecedor(
        nome=nome, empresa=empresa, whatsapp=whatsapp,
        telefone=telefone, email=email, cidade=cidade,
        estado=estado, observacoes=observacoes,
    )
    cat_ids = json.loads(categorias)
    if cat_ids:
        db.salvar_categorias_do_fornecedor(f.id, cat_ids)
    return f.model_dump()


@router.put("/admin/quote/api/fornecedores/{fornecedor_id}")
def api_editar_fornecedor(
    fornecedor_id: int, nome: str = Form(...), empresa: str = Form(""),
    whatsapp: str = Form(""), telefone: str = Form(""), email: str = Form(""),
    cidade: str = Form(""), estado: str = Form(""), observacoes: str = Form(""),
    ativo: bool = Form(True), categorias: str = Form("[]"),
    u: db.Usuario = Depends(_admin),
):
    f = db.editar_quote_fornecedor(
        fornecedor_id, nome=nome, empresa=empresa, whatsapp=whatsapp,
        telefone=telefone, email=email, cidade=cidade,
        estado=estado, observacoes=observacoes, ativo=ativo,
    )
    if not f:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
    cat_ids = json.loads(categorias)
    db.salvar_categorias_do_fornecedor(fornecedor_id, cat_ids)
    return f.model_dump()


@router.delete("/admin/quote/api/fornecedores/{fornecedor_id}")
def api_deletar_fornecedor(fornecedor_id: int, u: db.Usuario = Depends(_admin)):
    if not db.deletar_quote_fornecedor(fornecedor_id):
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Contatos
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/contatos")
def api_listar_contatos(fornecedor_id: int = 0, u: db.Usuario = Depends(_usuario)):
    f_id = fornecedor_id if fornecedor_id else None
    contatos = db.listar_quote_contatos(fornecedor_id=f_id)
    result = []
    for c in contatos:
        d = c.model_dump()
        f = db.get_quote_fornecedor(c.supplier_id)
        d["fornecedor_nome"] = f.nome if f else ""
        result.append(d)
    return result


@router.post("/admin/quote/api/contatos")
def api_criar_contato(
    supplier_id: int = Form(...), nome: str = Form(...),
    whatsapp: str = Form(""), email: str = Form(""), observacoes: str = Form(""),
    u: db.Usuario = Depends(_admin),
):
    c = db.criar_quote_contato(
        supplier_id=supplier_id, nome=nome, whatsapp=whatsapp,
        email=email, observacoes=observacoes,
    )
    return c.model_dump()


@router.put("/admin/quote/api/contatos/{contato_id}")
def api_editar_contato(
    contato_id: int, nome: str = Form(...), supplier_id: int = Form(...),
    whatsapp: str = Form(""), email: str = Form(""), observacoes: str = Form(""),
    ativo: bool = Form(True),
    u: db.Usuario = Depends(_admin),
):
    c = db.editar_quote_contato(
        contato_id, nome=nome, supplier_id=supplier_id,
        whatsapp=whatsapp, email=email, observacoes=observacoes, ativo=ativo,
    )
    if not c:
        raise HTTPException(status_code=404, detail="Contato não encontrado")
    return c.model_dump()


@router.delete("/admin/quote/api/contatos/{contato_id}")
def api_deletar_contato(contato_id: int, u: db.Usuario = Depends(_admin)):
    if not db.deletar_quote_contato(contato_id):
        raise HTTPException(status_code=404, detail="Contato não encontrado")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Sessões WhatsApp (tipo cotacao)
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/sessoes")
def api_listar_sessoes(u: db.Usuario = Depends(_usuario)):
    sessoes = db.listar_quote_sessoes_todas()
    return [s.model_dump() for s in sessoes]


@router.post("/admin/quote/api/sessoes")
async def api_criar_sessao(
    nome: str = Form(...), numero: str = Form(""),
    u: db.Usuario = Depends(_admin),
):
    with db._lock, db._session() as s:
        nova = db.InstanciaWhatsApp(
            nome=nome, numero=numero, tipo="cotacao",
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(nova)
        s.commit()
        s.refresh(nova)
    try:
        await evolution.criar_instancia_evolution(nome)
    except Exception:
        pass
    return nova.model_dump()


@router.get("/admin/quote/api/sessoes/{sessao_id}/estado")
def api_estado_sessao(sessao_id: int, u: db.Usuario = Depends(_usuario)):
    sessao = db.get_instancia(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    try:
        est = evolution.estado_instancia(sessao.nome)
        return est
    except Exception as e:
        return {"erro": str(e), "instance": {"state": "disconnected"}}


@router.get("/admin/quote/api/sessoes/{sessao_id}/qr")
def api_qr_sessao(sessao_id: int, u: db.Usuario = Depends(_usuario)):
    sessao = db.get_instancia(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    try:
        qr = evolution.conectar_instancia(sessao.nome)
        return qr
    except Exception as e:
        return {"erro": str(e)}


@router.post("/admin/quote/api/sessoes/{sessao_id}/desconectar")
def api_desconectar_sessao(sessao_id: int, u: db.Usuario = Depends(_admin)):
    sessao = db.get_instancia(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    try:
        res = evolution.desconectar_instancia(sessao.nome)
        return res
    except Exception as e:
        return {"erro": str(e)}


@router.delete("/admin/quote/api/sessoes/{sessao_id}")
def api_deletar_sessao(sessao_id: int, u: db.Usuario = Depends(_admin)):
    with db._lock, db._session() as s:
        sessao = s.get(db.InstanciaWhatsApp, sessao_id)
        if not sessao:
            raise HTTPException(status_code=404, detail="Sessão não encontrada")
        nome = sessao.nome
        s.delete(sessao)
        s.commit()
    evolution.deletar_instancia_evolution(nome)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Templates de mensagem
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/templates")
def api_listar_templates(u: db.Usuario = Depends(_usuario)):
    templates = db.listar_quote_templates()
    return [t.model_dump() for t in templates]


@router.put("/admin/quote/api/templates/{template_id}")
def api_editar_template(
    template_id: int, nome: str = Form(...), template: str = Form(...),
    ativo: bool = Form(True),
    u: db.Usuario = Depends(_admin),
):
    t = db.editar_quote_template(template_id, nome=nome, template=template, ativo=ativo)
    if not t:
        raise HTTPException(status_code=404, detail="Template não encontrado")
    return t.model_dump()


@router.post("/admin/quote/api/templates")
def api_criar_template(
    nome: str = Form(...), template: str = Form(...),
    u: db.Usuario = Depends(_admin),
):
    t = db.criar_quote_template(nome=nome, template=template)
    return t.model_dump()


# ---------------------------------------------------------------------------
# API: Dashboard stats
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/stats")
def api_stats(u: db.Usuario = Depends(_usuario)):
    return db.get_quote_dashboard_stats()


# ---------------------------------------------------------------------------
# API: Consulta de placa veicular
# ---------------------------------------------------------------------------


@router.post("/admin/quote/api/consultar-placa")
def api_consultar_placa(placa: str = Form(...), u: db.Usuario = Depends(_usuario)):
    cfg = db.get_config()
    if not cfg:
        raise HTTPException(status_code=400, detail="Configuração não encontrada")
    bearer = (cfg.quote_placa_token or "").strip()
    device = (cfg.quote_placa_device_token or "").strip()
    if not bearer or not device:
        raise HTTPException(
            status_code=400,
            detail="API de consulta de placa não configurada. Configure os tokens no menu Configurações.",
        )
    try:
        return consultar_placa(placa, bearer, device)
    except PlacaError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/quote/api/config-placa")
def api_salvar_config_placa(
    token: str = Form(""), device_token: str = Form(""),
    u: db.Usuario = Depends(_admin),
):
    cfg = db.update_config(
        quote_placa_token=token.strip(),
        quote_placa_device_token=device_token.strip(),
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: Solicitações (fluxo principal)
# ---------------------------------------------------------------------------


@router.get("/admin/quote/api/solicitacoes")
def api_listar_solicitacoes(status: str = "", u: db.Usuario = Depends(_usuario)):
    requests = db.listar_quote_requests(status=status or None)
    result = []
    for r in requests:
        d = r.model_dump()
        cat = db.get_quote_categoria(r.categoria_id)
        d["categoria_nome"] = cat.nome if cat else ""
        usuario = db.get_usuario(r.usuario_id)
        d["usuario_nome"] = usuario.nome if usuario else ""
        itens = []
        with db._session() as s:
            for item in s.exec(
                db.select(db.QuoteRequestItem).where(db.QuoteRequestItem.request_id == r.id)
            ).all():
                p = db.get_quote_peca(item.part_id)
                itens.append({
                    "id": item.id,
                    "part_id": item.part_id,
                    "part_nome": p.nome if p else "",
                    "quantidade": item.quantidade,
                })
        d["itens"] = itens
        result.append(d)
    return result


@router.get("/admin/quote/api/solicitacoes/{request_id}")
def api_get_solicitacao(request_id: int, u: db.Usuario = Depends(_usuario)):
    r = db.get_quote_request(request_id)
    if not r:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    d = r.model_dump()
    cat = db.get_quote_categoria(r.categoria_id)
    d["categoria_nome"] = cat.nome if cat else ""
    usuario = db.get_usuario(r.usuario_id)
    d["usuario_nome"] = usuario.nome if usuario else ""
    # itens
    itens = []
    with db._session() as s:
        for item in s.exec(
            db.select(db.QuoteRequestItem).where(db.QuoteRequestItem.request_id == r.id)
        ).all():
            p = db.get_quote_peca(item.part_id)
            itens.append({
                "id": item.id,
                "part_id": item.part_id,
                "part_nome": p.nome if p else "",
                "codigo_interno": p.codigo_interno if p else "",
                "marca": p.marca if p else "",
                "quantidade": item.quantidade,
                "observacoes": item.observacoes,
            })
    d["itens"] = itens
    # fornecedores
    fornecedores = []
    with db._session() as s:
        for rs in s.exec(
            db.select(db.QuoteRequestSupplier).where(db.QuoteRequestSupplier.request_id == r.id)
        ).all():
            f = db.get_quote_fornecedor(rs.supplier_id)
            if f:
                fornecedores.append({"id": f.id, "nome": f.nome, "whatsapp": f.whatsapp})
    d["fornecedores"] = fornecedores
    # mensagens
    mensagens = db.listar_mensagens_cotacao(request_id)
    msgs_json = []
    for m in mensagens:
        md = m.model_dump()
        f = db.get_quote_fornecedor(m.supplier_id)
        md["fornecedor_nome"] = f.nome if f else ""
        msgs_json.append(md)
    d["mensagens"] = msgs_json
    # preços
    precos = db.listar_precos_cotacao(request_id)
    d["precos"] = [p.model_dump() for p in precos]
    # histórico
    historico = db.listar_historico_cotacao(request_id)
    d["historico"] = [h.model_dump() for h in historico]
    return d


@router.post("/admin/quote/api/solicitacoes/{request_id}/precos")
def api_salvar_precos(
    request_id: int, data: str = Form(...),
    u: db.Usuario = Depends(_usuario),
):
    r = db.get_quote_request(request_id)
    if not r:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    precos_data = json.loads(data)
    salvos = 0
    for p in precos_data:
        db.salvar_preco_cotacao(
            request_id=request_id,
            supplier_id=p["supplier_id"],
            part_id=p["part_id"],
            valor=float(p.get("valor", 0)),
            prazo_entrega=p.get("prazo_entrega", ""),
            observacoes=p.get("observacoes", ""),
        )
        salvos += 1
    db.registrar_historico_cotacao(
        request_id=request_id, acao="precos_atualizados",
        descricao=f"Preços atualizados ({salvos} itens)",
        usuario_id=u.id,
    )
    return {"ok": True, "salvos": salvos}


@router.post("/admin/quote/api/solicitacoes")
def api_criar_solicitacao(
    categoria_id: int = Form(...),
    pecas: str = Form(...),  # JSON: [{"part_id": 1, "quantidade": 2}, ...]
    fornecedores: str = Form(...),  # JSON: [1, 2, 3]
    sessao_id: int = Form(0),
    observacoes: str = Form(""),
    template_id: int = Form(0),
    placa: str = Form(""),
    veiculo_marca: str = Form(""),
    veiculo_modelo: str = Form(""),
    veiculo_ano: str = Form(""),
    u: db.Usuario = Depends(_usuario),
):
    pecas_list = json.loads(pecas)
    fornecedores_list = json.loads(fornecedores)
    sessao = sessao_id if sessao_id else None
    tmpl = template_id if template_id else None

    r = db.criar_quote_request(
        categoria_id=categoria_id,
        usuario_id=u.id,
        sessao_id=sessao,
        observacoes=observacoes,
        template_id=tmpl,
        placa=placa,
        veiculo_marca=veiculo_marca,
        veiculo_modelo=veiculo_modelo,
        veiculo_ano=veiculo_ano,
    )

    for item in pecas_list:
        db.adicionar_item_cotacao(
            request_id=r.id,
            part_id=item["part_id"],
            quantidade=item.get("quantidade", 1),
            observacoes=item.get("observacoes", ""),
        )

    for f_id in fornecedores_list:
        db.adicionar_fornecedor_cotacao(request_id=r.id, supplier_id=f_id)

    db.registrar_historico_cotacao(
        request_id=r.id, acao="criada",
        descricao=f"Cotação criada por {u.nome}",
        usuario_id=u.id,
    )

    return r.model_dump()


@router.post("/admin/quote/api/solicitacoes/{request_id}/enviar")
async def api_enviar_solicitacao(request_id: int, u: db.Usuario = Depends(_usuario)):
    r = db.get_quote_request(request_id)
    if not r:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    if r.status not in ("aberta", "enviada", "aguardando", "respondida_parcial"):
        raise HTTPException(status_code=400, detail=f"Status inválido para envio: {r.status}")

    if not r.sessao_id:
        raise HTTPException(status_code=400, detail="Selecione uma sessão WhatsApp antes de enviar")

    with db._session() as s:
        inst = s.get(db.InstanciaWhatsApp, r.sessao_id)
        if not inst or not inst.ativo:
            raise HTTPException(status_code=400, detail="Sessão WhatsApp não encontrada ou inativa")

    nome_instancia = inst.nome

    cat = db.get_quote_categoria(r.categoria_id)
    template = db.get_quote_template(r.template_id)

    # Monta lista de peças
    pecas_texto = []
    with db._session() as s:
        for item in s.exec(
            db.select(db.QuoteRequestItem).where(db.QuoteRequestItem.request_id == r.id)
        ).all():
            p = db.get_quote_peca(item.part_id)
            nome = p.nome if p else f"Peça #{item.part_id}"
            pecas_texto.append(f"  - {nome} ({item.quantidade}x)")

    pecas_str = "\n".join(pecas_texto)

    # Envia para cada fornecedor
    erros = []
    with db._session() as s:
        fornecedores = s.exec(
            db.select(db.QuoteRequestSupplier).where(db.QuoteRequestSupplier.request_id == r.id)
        ).all()

    if not fornecedores:
        raise HTTPException(status_code=400, detail="Nenhum fornecedor selecionado")

    for rs in fornecedores:
        f = db.get_quote_fornecedor(rs.supplier_id)
        if not f or not f.whatsapp:
            erros.append(f"Fornecedor #{rs.supplier_id} sem WhatsApp")
            continue

        # Monta mensagem do template
        msg = template.template
        msg = msg.replace("{fornecedor}", f.nome)
        msg = msg.replace("{fornecedor_empresa}", f.empresa)
        msg = msg.replace("{pecas}", pecas_str)
        msg = msg.replace("{categoria}", cat.nome if cat else "")
        msg = msg.replace("{numero_cotacao}", r.numero)
        msg = msg.replace("{observacoes}", r.observacoes)
        msg = msg.replace("{data}", datetime.now().strftime("%d/%m/%Y"))
        msg = msg.replace("{placa}", r.placa)
        msg = msg.replace("{marca}", r.veiculo_marca)
        msg = msg.replace("{modelo}", r.veiculo_modelo)
        msg = msg.replace("{ano}", r.veiculo_ano)
        veiculo = f"{r.veiculo_marca} {r.veiculo_modelo} {r.veiculo_ano}".strip()
        msg = msg.replace("{veiculo}", veiculo)

        # Remove linhas de variáveis não substituídas
        msg = re.sub(r'\{[^}]+\}', '', msg).strip()

        try:
            numero_whatsapp = re.sub(r'[^0-9]', '', f.whatsapp)
            if not numero_whatsapp.endswith("@s.whatsapp.net"):
                remote_jid = f"{numero_whatsapp}@s.whatsapp.net"
            else:
                remote_jid = numero_whatsapp

            await evolution.enviar_texto(remote_jid, msg, instancia=nome_instancia)

            db.registrar_mensagem_cotacao(
                request_id=r.id, supplier_id=rs.supplier_id,
                mensagem=msg, tipo="enviada", remote_jid=remote_jid,
            )
        except Exception as e:
            erros.append(f"{f.nome}: {str(e)}")

    db.atualizar_quote_request(r.id, status="enviada")
    db.registrar_historico_cotacao(
        request_id=r.id, acao="enviada",
        descricao=f"Cotação enviada para {len(fornecedores)} fornecedor(es)",
        usuario_id=u.id,
    )

    return {"ok": True, "enviados": len(fornecedores) - len(erros), "erros": erros}


@router.post("/admin/quote/api/solicitacoes/{request_id}/status")
def api_alterar_status(
    request_id: int, status: str = Form(...),
    u: db.Usuario = Depends(_usuario),
):
    r = db.get_quote_request(request_id)
    if not r:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    status_antigo = r.status
    db.atualizar_quote_request(r.id, status=status)
    db.registrar_historico_cotacao(
        request_id=r.id, acao="status",
        descricao=f"Status alterado: {status_antigo} → {status}",
        usuario_id=u.id,
    )
    return {"ok": True}


@router.post("/admin/quote/api/solicitacoes/{request_id}/receber")
def api_receber_resposta(
    request_id: int, supplier_id: int = Form(...),
    mensagem: str = Form(...), remote_jid: str = Form(""),
    u: db.Usuario = Depends(_usuario),
):
    m = db.registrar_mensagem_cotacao(
        request_id=request_id, supplier_id=supplier_id,
        mensagem=mensagem, tipo="recebida", remote_jid=remote_jid,
    )
    # Atualiza status para aguardando se estiver enviada
    r = db.get_quote_request(request_id)
    if r and r.status == "enviada":
        db.atualizar_quote_request(request_id, status="aguardando")
    return m.model_dump()


@router.get("/admin/quote/api/solicitacoes/{request_id}/mensagens")
def api_mensagens_cotacao(request_id: int, u: db.Usuario = Depends(_usuario)):
    msgs = db.listar_mensagens_cotacao(request_id)
    result = []
    for m in msgs:
        d = m.model_dump()
        f = db.get_quote_fornecedor(m.supplier_id)
        d["fornecedor_nome"] = f.nome if f else ""
        result.append(d)
    return result


@router.get("/admin/quote/api/solicitacoes/{request_id}/historico")
def api_historico_cotacao(request_id: int, u: db.Usuario = Depends(_usuario)):
    historico = db.listar_historico_cotacao(request_id)
    result = []
    for h in historico:
        d = h.model_dump()
        if h.usuario_id:
            usr = db.get_usuario(h.usuario_id)
            d["usuario_nome"] = usr.nome if usr else ""
        result.append(d)
    return result
