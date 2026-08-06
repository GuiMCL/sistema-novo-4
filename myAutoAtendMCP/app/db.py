"""Persistência real em SQLite via SQLModel.

Mantém as MESMAS assinaturas de função do esqueleto mockado — as tools e o
painel não precisam saber que houve troca de backend.

Concorrência: `criar_agendamento`/`reagendar_agendamento` rodam o par
"checar conflito + gravar" sob um `Lock` de processo. SQLite serializa
escritas por natureza; o lock fecha a janela de corrida entre o SELECT de
conflito e o INSERT dentro do mesmo processo (instância única).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from threading import Lock
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import settings
from .phone import mesmo_numero, normalizar

# Status que mantêm o agendamento válido (ocupa vaga, aparece no painel e na
# consulta do cliente). "confirmado" é o "ativo" depois de confirmado pelo
# painel — tudo que filtrava só por "ativo" perdia esses agendamentos.
STATUS_VIGENTES = ("ativo", "confirmado")

# ---------------------------------------------------------------------------
# Modelos (tabelas)
# ---------------------------------------------------------------------------


class Config(SQLModel, table=True):
    # Nota de migração: bancos antigos têm colunas órfãs (`instrucoes_gerais`,
    # `abertura`, `fechamento`, `duracao_slot_min` — funcionamento migrou para
    # a tabela HorarioFuncionamento; slot nunca foi lido). SQLite ignora
    # colunas fora do modelo — sem migração.
    id: int = Field(default=1, primary_key=True)
    telefone_dono: str = settings.owner_phone
    fuso: str = settings.timezone
    avisar_dono: bool = True  # aviso no WhatsApp do dono a cada ação do bot
    quote_placa_token: str = ""
    quote_placa_device_token: str = ""


class HorarioFuncionamento(SQLModel, table=True):
    """Intervalo de atendimento de um dia da semana (0=segunda … 6=domingo).

    Várias linhas por dia = vários intervalos (ex.: manhã e tarde).
    Dia sem linha nenhuma = fechado. Tabela vazia = tudo fechado (estado
    legítimo via "Apagar tudo" no painel — por isso o seed do padrão só
    acontece quando a tabela é criada, nunca quando está vazia).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    dia_semana: int  # 0=segunda … 6=domingo (convenção do datetime.weekday())
    inicio: str  # "HH:MM"
    fim: str  # "HH:MM"


class Prompt(SQLModel, table=True):
    """Partes do system prompt do agente editadas pelo painel.

    Chaves usadas: "geral" (instrução principal), "mcp_dono" e "mcp_cliente"
    (bloco de ferramentas — uma versão por perfil de remetente). A chave antiga
    "mcp" (bloco único) foi aposentada: `_migrar_prompts` copia um eventual
    texto customizado dela para as duas novas na primeira subida. Fonte de
    verdade do painel — o agente lê daqui a cada mensagem (app/agente.py),
    então salvar aplica na hora. Tabela separada da Config: ambientes antigos
    ganham a tabela nova no create_all sem precisar de migração de coluna.
    """

    chave: str = Field(primary_key=True)
    texto: str


class ProvedorIA(SQLModel, table=True):
    """Config de provedor de IA por uso (texto / audio / imagem).

    Substitui as credenciais que viviam no n8n. A chave fica no SQLite local
    (stack 127.0.0.1); nenhuma rota do painel devolve a chave de volta.
    """

    alvo: str = Field(primary_key=True)  # "texto" | "audio" | "imagem"
    api_key: str
    base_url: str
    modelo: str = ""
    atualizado_em: str = ""


class Conversa(SQLModel, table=True):
    """Histórico de conversa do agente por contato (remoteJid normalizado).

    `historico` é o JSON serializado das mensagens do pydantic-ai
    (ModelMessagesTypeAdapter), já aparado na janela — tamanho limitado.
    """

    telefone: str = Field(primary_key=True)
    historico: str
    atualizado_em: str = ""


class Cliente(SQLModel, table=True):
    """Contato que já apareceu no WhatsApp do bot (telefone E.164 como PK).

    Criada sob demanda: upsert no pipeline do webhook (aproveita o pushName
    para o nome) e ao pausar pelo painel. Nasce só com nome + estado de pausa,
    mas vai crescer (ficha de cadastro, memória por cliente) — por isso os
    acessos passam pelos helpers get/upsert. Tabela nova: o create_all cobre,
    sem ALTER. A chave é o E.164 normalizado; a memória (Conversa) continua
    indexada pelo remoteJid bruto — `resolver_chave_conversa` faz a ponte.
    """

    telefone: str = Field(primary_key=True)  # E.164 normalizado
    nome: str = ""
    bot_pausado: bool = False


class Servico(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    descricao: str
    valor: float
    duracao_min: int
    ativo: bool = True


class Bloqueio(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    data: str  # "YYYY-MM-DD"
    data_fim: Optional[str] = None  # "YYYY-MM-DD" ou None p/ um dia só
    inicio: Optional[str] = None  # "HH:MM" ou None para o dia inteiro
    fim: Optional[str] = None
    motivo: str = ""


class Tarefa(SQLModel, table=True):
    """Fila persistente de ações proativas do bot (worker em app/tarefas.py).

    Qualquer feature que precise do bot iniciando conversa agenda uma linha
    aqui; o worker do lifespan consome respeitando janela de cortesia, rate
    limit e debounce ativo do contato. `telefone_alvo` em formato livre
    (E.164 ou jid) — o worker resolve a chave de memória do contato.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tipo: str  # "contatar_cliente" (remanejo de dia / aviso de ação do dono)
    telefone_alvo: str
    payload: str = "{}"  # JSON com os dados do tipo
    status: str = "pendente"  # pendente | executando | concluida | falhou
    agendado_para: str  # ISO local "YYYY-MM-DDTHH:MM" (fuso da Config)
    tentativas: int = 0
    criado_em: str = ""
    resultado: str = ""  # última resposta enviada ou erro


class Usuario(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    email: str = Field(unique=True)
    senha_hash: str
    telefone: str = ""
    papel: str = "atendente"  # admin | atendente | mecanico
    avatar_url: str = ""
    ativo: bool = True
    criado_em: str = ""
    ultimo_login: str = ""


class InstanciaWhatsApp(SQLModel, table=True):
    """Múltiplas instâncias Evolution conectadas simultaneamente."""
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str  # nome da instância na Evolution
    numero: str = ""  # E.164
    numero_fmt: str = ""  # formatado para exibição
    provedor: str = "evolution"
    tipo: str = "atendimento"  # "atendimento" | "cotacao"
    ativo: bool = True
    usuario_id: Optional[int] = None  # atendente designado
    ultima_conexao: str = ""
    criado_em: str = ""


class Vaga(SQLModel, table=True):
    """Vagas/boxes de atendimento (ex.: Box 1, Box 2, …)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    descricao: str = ""
    ativo: bool = True
    ordem: int = 0


class TransferenciaDestino(SQLModel, table=True):
    """Destinos para transferencia de atendimento (ex.: financeiro, suporte)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str  # identificador usado pelo agente: "financeiro"
    instancia_id: Optional[int] = None  # qual instancia WhatsApp recebe
    telefone: str = ""  # numero alternativo se nao tiver instancia
    mensagem: str = "{cliente}, voce foi transferido para o {nome}. Em breve alguem do setor vai te chamar."
    ativo: bool = True


class LembreteConfig(SQLModel, table=True):
    """Configuração de lembretes automáticos de confirmação (dois estágios)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ativo: bool = True

    # Primeiro estágio (DISPARO 1): exemplo 48h antes
    horas_antes: int = 48
    mensagem: str = (
        "Ola {nome}! So passando para lembrar do nosso agendamento para depois de amanha. "
        "Ate la! Tenha um excelente dia."
    )

    # Segundo estágio (DISPARO 2): exemplo 24h antes
    horas_antes2: int = 24
    mensagem2: str = (
        "Ola {nome}! Passando para confirmar seu horario de amanha as {hora} para {servico}. "
        "Para adiantar seu cadastro, poderia me informar seu Nome Completo, CPF e Telefone? "
        "Fico no aguardo!"
    )
    ativo2: bool = True

    confirmar_automatically: bool = True  # confirma se responder "sim"
    cancelar_se_nao_confirmar: bool = False  # cancela se não responder em X horas
    timeout_horas: int = 6


class Agendamento(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    servico_id: Optional[int] = None  # opcional — agendamento pode ser só pelo sintoma (descricao)
    telefone_cliente: str
    nome_cliente: str
    inicio: str  # ISO "YYYY-MM-DDTHH:MM"
    fim: str
    status: str = "ativo"  # ativo | cancelado | confirmado | remarcado
    observacoes: str = ""  # campo livre, opcional
    descricao: str = ""  # sintoma/problema descrito pelo cliente (ex.: "ruído no pneu dianteiro direito")
    # Novos campos p/ multi-vaga + multi-instância + multi-usuário
    vaga_id: Optional[int] = None
    veiculo: str = ""
    placa: str = ""
    modelo: str = ""
    ano: str = ""
    usuario_id: Optional[int] = None  # quem criou/está atendendo
    instancia_id: Optional[int] = None  # qual WhatsApp recebeu
    lembretes_enviados: int = 0
    ultimo_lembrete: str = ""
    confirmado_em: str = ""
    origem: str = "bot"  # bot | painel | api


# ---------------------------------------------------------------------------
# Modelos do módulo Central de Cotações
# ---------------------------------------------------------------------------


class QuoteCategory(SQLModel, table=True):
    """Categoria de peças para cotação."""
    __tablename__ = "quote_categories"
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    descricao: str = ""
    ativo: bool = True
    ordem: int = 0
    criado_em: str = ""


class QuotePart(SQLModel, table=True):
    """Peça cadastrada para cotação."""
    __tablename__ = "quote_parts"
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    codigo_interno: str = ""
    codigo_fabricante: str = ""
    marca: str = ""
    categoria_id: int = Field(foreign_key="quote_categories.id")
    descricao: str = ""
    observacoes: str = ""
    imagem: str = ""
    ativo: bool = True
    criado_em: str = ""


class QuoteSupplier(SQLModel, table=True):
    """Fornecedor para cotação."""
    __tablename__ = "quote_suppliers"
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    empresa: str = ""
    whatsapp: str = ""
    telefone: str = ""
    email: str = ""
    cidade: str = ""
    estado: str = ""
    observacoes: str = ""
    ativo: bool = True
    criado_em: str = ""


class QuoteSupplierCategory(SQLModel, table=True):
    """Relacionamento M2M entre fornecedores e categorias."""
    __tablename__ = "quote_supplier_categories"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="quote_suppliers.id")
    category_id: int = Field(foreign_key="quote_categories.id")


class QuoteContact(SQLModel, table=True):
    """Contato de fornecedor (agenda exclusiva de cotação)."""
    __tablename__ = "quote_contacts"
    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="quote_suppliers.id")
    nome: str
    whatsapp: str = ""
    email: str = ""
    observacoes: str = ""
    ativo: bool = True
    criado_em: str = ""


class QuoteMessageTemplate(SQLModel, table=True):
    """Template de mensagem para envio de cotação."""
    __tablename__ = "quote_message_templates"
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str = "Padrão"
    template: str = "Olá {fornecedor}, gostaria de uma cotação para as seguintes peças:\n\n{pecas}\n\nAguardo retorno. Obrigado."
    ativo: bool = True
    criado_em: str = ""


class QuoteRequest(SQLModel, table=True):
    """Solicitação de cotação."""
    __tablename__ = "quote_requests"
    id: Optional[int] = Field(default=None, primary_key=True)
    numero: str = ""
    categoria_id: int = Field(foreign_key="quote_categories.id")
    usuario_id: int = Field(foreign_key="usuario.id")
    sessao_id: Optional[int] = Field(default=None, foreign_key="instanciawhatsapp.id")
    status: str = "aberta"  # aberta | enviada | aguardando | respondida_parcial | respondida | finalizada | cancelada
    observacoes: str = ""
    template_id: Optional[int] = Field(default=None, foreign_key="quote_message_templates.id")
    placa: str = ""
    veiculo_marca: str = ""
    veiculo_modelo: str = ""
    veiculo_ano: str = ""
    criado_em: str = ""
    atualizado_em: str = ""


class QuoteRequestItem(SQLModel, table=True):
    """Item de uma solicitação de cotação."""
    __tablename__ = "quote_request_items"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="quote_requests.id")
    part_id: int = Field(foreign_key="quote_parts.id")
    quantidade: int = 1
    observacoes: str = ""


class QuoteRequestSupplier(SQLModel, table=True):
    """Fornecedor selecionado em uma solicitação."""
    __tablename__ = "quote_request_suppliers"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="quote_requests.id")
    supplier_id: int = Field(foreign_key="quote_suppliers.id")


class QuoteMessage(SQLModel, table=True):
    """Mensagem trocada em uma cotação."""
    __tablename__ = "quote_messages"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="quote_requests.id")
    supplier_id: int = Field(foreign_key="quote_suppliers.id")
    remote_jid: str = ""
    mensagem: str
    tipo: str = "enviada"  # enviada | recebida
    data_hora: str = ""
    criado_em: str = ""


class QuotePrice(SQLModel, table=True):
    __tablename__ = "quote_prices"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="quote_requests.id")
    supplier_id: int = Field(foreign_key="quote_suppliers.id")
    part_id: int = Field(foreign_key="quote_parts.id")
    valor: float = 0.0
    prazo_entrega: str = ""
    observacoes: str = ""
    criado_em: str = ""


class QuoteHistory(SQLModel, table=True):
    """Histórico de ações em uma cotação."""
    __tablename__ = "quote_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="quote_requests.id")
    acao: str
    descricao: str = ""
    usuario_id: Optional[int] = Field(default=None, foreign_key="usuario.id")
    data_hora: str = ""
    payload: str = "{}"


# ---------------------------------------------------------------------------
# Helpers do módulo Central de Cotações
# ---------------------------------------------------------------------------


def listar_quote_categorias(apenas_ativas: bool = False) -> list[QuoteCategory]:
    with _session() as s:
        stmt = select(QuoteCategory).order_by(QuoteCategory.ordem, QuoteCategory.nome)
        if apenas_ativas:
            stmt = stmt.where(QuoteCategory.ativo == True)  # noqa: E712
        return list(s.exec(stmt).all())


def get_quote_categoria(categoria_id: int) -> QuoteCategory | None:
    with _session() as s:
        return s.get(QuoteCategory, categoria_id)


def criar_quote_categoria(nome: str, descricao: str = "", ordem: int = 0) -> QuoteCategory:
    with _lock, _session() as s:
        nova = QuoteCategory(nome=nome, descricao=descricao, ordem=ordem, criado_em=datetime.now().isoformat(timespec="seconds"))
        s.add(nova)
        s.commit()
        return nova


def editar_quote_categoria(categoria_id: int, **campos) -> QuoteCategory | None:
    with _lock, _session() as s:
        cat = s.get(QuoteCategory, categoria_id)
        if not cat:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(cat, k):
                setattr(cat, k, v)
        s.add(cat)
        s.commit()
        return cat


def deletar_quote_categoria(categoria_id: int) -> bool:
    with _lock, _session() as s:
        cat = s.get(QuoteCategory, categoria_id)
        if not cat:
            return False
        s.delete(cat)
        s.commit()
    return True


def listar_quote_pecas(categoria_id: int | None = None, apenas_ativas: bool = False) -> list[QuotePart]:
    with _session() as s:
        stmt = select(QuotePart)
        if categoria_id:
            stmt = stmt.where(QuotePart.categoria_id == categoria_id)
        if apenas_ativas:
            stmt = stmt.where(QuotePart.ativo == True)  # noqa: E712
        stmt = stmt.order_by(QuotePart.nome)
        return list(s.exec(stmt).all())


def get_quote_peca(peca_id: int) -> QuotePart | None:
    with _session() as s:
        return s.get(QuotePart, peca_id)


def criar_quote_peca(
    nome: str, categoria_id: int, codigo_interno: str = "", codigo_fabricante: str = "",
    marca: str = "", descricao: str = "", observacoes: str = "", imagem: str = "",
) -> QuotePart:
    with _lock, _session() as s:
        nova = QuotePart(
            nome=nome, categoria_id=categoria_id, codigo_interno=codigo_interno,
            codigo_fabricante=codigo_fabricante, marca=marca, descricao=descricao,
            observacoes=observacoes, imagem=imagem, criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(nova)
        s.commit()
        return nova


def editar_quote_peca(peca_id: int, **campos) -> QuotePart | None:
    with _lock, _session() as s:
        p = s.get(QuotePart, peca_id)
        if not p:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(p, k):
                setattr(p, k, v)
        s.add(p)
        s.commit()
        return p


def deletar_quote_peca(peca_id: int) -> bool:
    with _lock, _session() as s:
        p = s.get(QuotePart, peca_id)
        if not p:
            return False
        s.delete(p)
        s.commit()
    return True


def listar_quote_fornecedores(apenas_ativos: bool = False) -> list[QuoteSupplier]:
    with _session() as s:
        stmt = select(QuoteSupplier).order_by(QuoteSupplier.nome)
        if apenas_ativos:
            stmt = stmt.where(QuoteSupplier.ativo == True)  # noqa: E712
        return list(s.exec(stmt).all())


def get_quote_fornecedor(fornecedor_id: int) -> QuoteSupplier | None:
    with _session() as s:
        return s.get(QuoteSupplier, fornecedor_id)


def criar_quote_fornecedor(
    nome: str, empresa: str = "", whatsapp: str = "", telefone: str = "",
    email: str = "", cidade: str = "", estado: str = "", observacoes: str = "",
) -> QuoteSupplier:
    with _lock, _session() as s:
        f = QuoteSupplier(
            nome=nome, empresa=empresa, whatsapp=whatsapp, telefone=telefone,
            email=email, cidade=cidade, estado=estado, observacoes=observacoes,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(f)
        s.commit()
        return f


def editar_quote_fornecedor(fornecedor_id: int, **campos) -> QuoteSupplier | None:
    with _lock, _session() as s:
        f = s.get(QuoteSupplier, fornecedor_id)
        if not f:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(f, k):
                setattr(f, k, v)
        s.add(f)
        s.commit()
        return f


def deletar_quote_fornecedor(fornecedor_id: int) -> bool:
    with _lock, _session() as s:
        f = s.get(QuoteSupplier, fornecedor_id)
        if not f:
            return False
        s.delete(f)
        s.commit()
    return True


def listar_categorias_do_fornecedor(fornecedor_id: int) -> list[QuoteCategory]:
    with _session() as s:
        cat_ids = [
            sc.category_id
            for sc in s.exec(
                select(QuoteSupplierCategory).where(QuoteSupplierCategory.supplier_id == fornecedor_id)
            ).all()
        ]
        if not cat_ids:
            return []
        stmt = select(QuoteCategory).where(QuoteCategory.id.in_(cat_ids))
        return list(s.exec(stmt).all())


def salvar_categorias_do_fornecedor(fornecedor_id: int, categoria_ids: list[int]) -> None:
    with _lock, _session() as s:
        existing = s.exec(
            select(QuoteSupplierCategory).where(QuoteSupplierCategory.supplier_id == fornecedor_id)
        ).all()
        for sc in existing:
            s.delete(sc)
        for cat_id in categoria_ids:
            s.add(QuoteSupplierCategory(supplier_id=fornecedor_id, category_id=cat_id))
        s.commit()


def fornecedores_por_categoria(categoria_id: int) -> list[QuoteSupplier]:
    """Retorna fornecedores ativos que atendem uma categoria."""
    with _session() as s:
        sc_ids = [
            sc.supplier_id
            for sc in s.exec(
                select(QuoteSupplierCategory).where(QuoteSupplierCategory.category_id == categoria_id)
            ).all()
        ]
        if not sc_ids:
            return []
        return list(
            s.exec(
                select(QuoteSupplier)
                .where(QuoteSupplier.id.in_(sc_ids), QuoteSupplier.ativo == True)  # noqa: E712
                .order_by(QuoteSupplier.nome)
            ).all()
        )


def listar_quote_contatos(fornecedor_id: int | None = None) -> list[QuoteContact]:
    with _session() as s:
        stmt = select(QuoteContact)
        if fornecedor_id:
            stmt = stmt.where(QuoteContact.supplier_id == fornecedor_id)
        stmt = stmt.order_by(QuoteContact.nome)
        return list(s.exec(stmt).all())


def criar_quote_contato(
    supplier_id: int, nome: str, whatsapp: str = "",
    email: str = "", observacoes: str = "",
) -> QuoteContact:
    with _lock, _session() as s:
        c = QuoteContact(
            supplier_id=supplier_id, nome=nome, whatsapp=whatsapp,
            email=email, observacoes=observacoes,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(c)
        s.commit()
        return c


def editar_quote_contato(contato_id: int, **campos) -> QuoteContact | None:
    with _lock, _session() as s:
        c = s.get(QuoteContact, contato_id)
        if not c:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(c, k):
                setattr(c, k, v)
        s.add(c)
        s.commit()
        return c


def deletar_quote_contato(contato_id: int) -> bool:
    with _lock, _session() as s:
        c = s.get(QuoteContact, contato_id)
        if not c:
            return False
        s.delete(c)
        s.commit()
    return True


def listar_quote_sessoes() -> list[InstanciaWhatsApp]:
    """Retorna instâncias do tipo 'cotacao'."""
    with _session() as s:
        return list(
            s.exec(
                select(InstanciaWhatsApp)
                .where(InstanciaWhatsApp.tipo == "cotacao", InstanciaWhatsApp.ativo == True)  # noqa: E712
                .order_by(InstanciaWhatsApp.nome)
            ).all()
        )


def listar_quote_sessoes_todas() -> list[InstanciaWhatsApp]:
    with _session() as s:
        return list(
            s.exec(
                select(InstanciaWhatsApp)
                .where(InstanciaWhatsApp.tipo == "cotacao")
                .order_by(InstanciaWhatsApp.nome)
            ).all()
        )


def get_quote_template(template_id: int | None = None) -> QuoteMessageTemplate:
    with _session() as s:
        if template_id:
            t = s.get(QuoteMessageTemplate, template_id)
            if t:
                return t
        t = s.exec(select(QuoteMessageTemplate).where(QuoteMessageTemplate.ativo == True)).first()
        if t:
            return t
        t = QuoteMessageTemplate(id=1)
        s.add(t)
        s.commit()
        return t


def listar_quote_templates() -> list[QuoteMessageTemplate]:
    with _session() as s:
        return list(s.exec(select(QuoteMessageTemplate)).all())


def criar_quote_template(nome: str, template: str) -> QuoteMessageTemplate:
    with _lock, _session() as s:
        t = QuoteMessageTemplate(nome=nome, template=template, criado_em=datetime.now().isoformat(timespec="seconds"))
        s.add(t)
        s.commit()
        return t


def editar_quote_template(template_id: int, **campos) -> QuoteMessageTemplate | None:
    with _lock, _session() as s:
        t = s.get(QuoteMessageTemplate, template_id)
        if not t:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(t, k):
                setattr(t, k, v)
        s.add(t)
        s.commit()
        return t


def listar_quote_requests(
    status: str | None = None, limite: int = 100
) -> list[QuoteRequest]:
    with _session() as s:
        stmt = select(QuoteRequest).order_by(QuoteRequest.id.desc())
        if status:
            stmt = stmt.where(QuoteRequest.status == status)
        return list(s.exec(stmt.limit(limite)).all())


def get_quote_request(request_id: int) -> QuoteRequest | None:
    with _session() as s:
        return s.get(QuoteRequest, request_id)


def criar_quote_request(
    categoria_id: int, usuario_id: int, sessao_id: int | None = None,
    observacoes: str = "", template_id: int | None = None,
    placa: str = "", veiculo_marca: str = "",
    veiculo_modelo: str = "", veiculo_ano: str = "",
) -> QuoteRequest:
    with _lock, _session() as s:
        r = QuoteRequest(
            categoria_id=categoria_id, usuario_id=usuario_id,
            sessao_id=sessao_id, observacoes=observacoes,
            template_id=template_id,
            placa=placa.upper().strip(), veiculo_marca=veiculo_marca.strip(),
            veiculo_modelo=veiculo_modelo.strip(), veiculo_ano=veiculo_ano.strip(),
            criado_em=datetime.now().isoformat(timespec="seconds"),
            atualizado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        r.numero = f"COT-{r.id:05d}"
        s.add(r)
        s.commit()
        return r


def atualizar_quote_request(request_id: int, **campos) -> QuoteRequest | None:
    with _lock, _session() as s:
        r = s.get(QuoteRequest, request_id)
        if not r:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(r, k):
                setattr(r, k, v)
        r.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(r)
        s.commit()
        return r


def adicionar_item_cotacao(request_id: int, part_id: int, quantidade: int = 1, observacoes: str = "") -> QuoteRequestItem:
    with _lock, _session() as s:
        item = QuoteRequestItem(
            request_id=request_id, part_id=part_id,
            quantidade=quantidade, observacoes=observacoes,
        )
        s.add(item)
        s.commit()
        return item


def adicionar_fornecedor_cotacao(request_id: int, supplier_id: int) -> QuoteRequestSupplier:
    with _lock, _session() as s:
        rs = QuoteRequestSupplier(request_id=request_id, supplier_id=supplier_id)
        s.add(rs)
        s.commit()
        return rs


def registrar_mensagem_cotacao(
    request_id: int, supplier_id: int, mensagem: str,
    tipo: str = "enviada", remote_jid: str = "", data_hora: str = "",
) -> QuoteMessage:
    with _lock, _session() as s:
        m = QuoteMessage(
            request_id=request_id, supplier_id=supplier_id,
            mensagem=mensagem, tipo=tipo, remote_jid=remote_jid,
            data_hora=data_hora or datetime.now().isoformat(timespec="seconds"),
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(m)
        s.commit()
        return m


def listar_mensagens_cotacao(request_id: int) -> list[QuoteMessage]:
    with _session() as s:
        return list(
            s.exec(
                select(QuoteMessage)
                .where(QuoteMessage.request_id == request_id)
                .order_by(QuoteMessage.criado_em)
            ).all()
        )


def listar_precos_cotacao(request_id: int) -> list[QuotePrice]:
    with _session() as s:
        return list(
            s.exec(
                select(QuotePrice)
                .where(QuotePrice.request_id == request_id)
                .order_by(QuotePrice.supplier_id, QuotePrice.part_id)
            ).all()
        )


def salvar_preco_cotacao(
    request_id: int, supplier_id: int, part_id: int,
    valor: float, prazo_entrega: str = "", observacoes: str = "",
) -> QuotePrice | None:
    with _lock, _session() as s:
        existing = s.exec(
            select(QuotePrice)
            .where(
                QuotePrice.request_id == request_id,
                QuotePrice.supplier_id == supplier_id,
                QuotePrice.part_id == part_id,
            )
        ).first()
        if existing:
            existing.valor = valor
            existing.prazo_entrega = prazo_entrega
            existing.observacoes = observacoes
            s.add(existing)
            s.commit()
            return existing
        p = QuotePrice(
            request_id=request_id, supplier_id=supplier_id, part_id=part_id,
            valor=valor, prazo_entrega=prazo_entrega, observacoes=observacoes,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(p)
        s.commit()
        return p


def registrar_historico_cotacao(
    request_id: int, acao: str, descricao: str = "",
    usuario_id: int | None = None, payload: dict | None = None,
) -> QuoteHistory:
    with _lock, _session() as s:
        h = QuoteHistory(
            request_id=request_id, acao=acao, descricao=descricao,
            usuario_id=usuario_id, payload=json.dumps(payload or {}, ensure_ascii=False),
            data_hora=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(h)
        s.commit()
        return h


def listar_historico_cotacao(request_id: int) -> list[QuoteHistory]:
    with _session() as s:
        return list(
            s.exec(
                select(QuoteHistory)
                .where(QuoteHistory.request_id == request_id)
                .order_by(QuoteHistory.data_hora)
            ).all()
        )


def get_quote_dashboard_stats() -> dict:
    with _session() as s:
        total = s.exec(select(QuoteRequest)).all()
        abertas = [r for r in total if r.status == "aberta"]
        enviadas = [r for r in total if r.status == "enviada"]
        aguardando = [r for r in total if r.status == "aguardando"]
        respondidas = [r for r in total if r.status in ("respondida_parcial", "respondida")]
        finalizadas = [r for r in total if r.status == "finalizada"]
        canceladas = [r for r in total if r.status == "cancelada"]
        categorias = s.exec(select(QuoteCategory).where(QuoteCategory.ativo == True)).all()
        pecas = s.exec(select(QuotePart).where(QuotePart.ativo == True)).all()
        fornecedores = s.exec(select(QuoteSupplier).where(QuoteSupplier.ativo == True)).all()
        return {
            "total": len(total),
            "abertas": len(abertas),
            "enviadas": len(enviadas),
            "aguardando": len(aguardando),
            "respondidas": len(respondidas),
            "finalizadas": len(finalizadas),
            "canceladas": len(canceladas),
            "categorias": len(categorias),
            "pecas": len(pecas),
            "fornecedores": len(fornecedores),
        }


def find_quote_request_by_supplier_whatsapp(numero: str) -> int | None:
    """Encontra o ID da solicitação de cotação ativa para um número de WhatsApp de fornecedor."""
    from .phone import normalizar as _norm
    tel_clean = _norm(numero) or numero
    tel_clean = tel_clean.replace("@s.whatsapp.net", "").replace("@g.us", "").strip()
    with _session() as s:
        fornecedores = s.exec(
            select(QuoteSupplier).where(QuoteSupplier.ativo == True)
        ).all()
        matched = []
        for f in fornecedores:
            f_tel = _norm(f.whatsapp) or f.whatsapp
            if f_tel:
                f_clean = f_tel.replace("@s.whatsapp.net", "").replace("@g.us", "").strip()
                if tel_clean == f_clean or tel_clean in f_clean or f_clean in tel_clean:
                    matched.append(f.id)
        if not matched:
            return None
        rs = s.exec(
            select(QuoteRequestSupplier)
            .where(QuoteRequestSupplier.supplier_id.in_(matched))
        ).all()
        if not rs:
            return None
        r_ids = list(set(r.request_id for r in rs))
        req = s.exec(
            select(QuoteRequest)
            .where(
                QuoteRequest.id.in_(r_ids),
                QuoteRequest.status.not_in(["finalizada", "cancelada"]),
            )
            .order_by(QuoteRequest.id.desc())
        ).first()
        if not req:
            req = s.exec(
                select(QuoteRequest)
                .where(QuoteRequest.id.in_(r_ids))
                .order_by(QuoteRequest.id.desc())
            ).first()
        return req.id if req else None


def find_open_quote_for_supplier(supplier_id: int) -> int | None:
    """Encontra o ID da solicitação de cotação ativa mais recente para um fornecedor."""
    with _session() as s:
        rs = s.exec(
            select(QuoteRequestSupplier)
            .where(QuoteRequestSupplier.supplier_id == supplier_id)
        ).all()
        if not rs:
            return None
        r_ids = list(set(r.request_id for r in rs))
        req = s.exec(
            select(QuoteRequest)
            .where(
                QuoteRequest.id.in_(r_ids),
                QuoteRequest.status.not_in(["finalizada", "cancelada"]),
            )
            .order_by(QuoteRequest.id.desc())
        ).first()
        if not req:
            req = s.exec(
                select(QuoteRequest)
                .where(QuoteRequest.id.in_(r_ids))
                .order_by(QuoteRequest.id.desc())
            ).first()
        return req.id if req else None


# ---------------------------------------------------------------------------
# Engine + sessão
# ---------------------------------------------------------------------------

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

# Serializa o par checar-conflito + gravar.
_lock = Lock()


def _session() -> Session:
    # expire_on_commit=False: objetos seguem legíveis após o commit/close.
    return Session(engine, expire_on_commit=False)


def init_db() -> None:
    # Seed do funcionamento: só quando a tabela ainda não existe (primeiro
    # boot ou upgrade). Tabela vazia ≠ tabela nova — "Apagar tudo" no painel
    # esvazia de propósito e não pode ser revertido por um restart.
    with engine.connect() as conn:
        tinha_horarios = bool(
            conn.exec_driver_sql("PRAGMA table_info(horariofuncionamento)").fetchall()
        )
    SQLModel.metadata.create_all(engine)
    _migrar()
    _migrar_prompts()
    with _session() as s:
        if s.get(Config, 1) is None:
            s.add(Config(id=1))
            s.commit()
    if not tinha_horarios:
        restaurar_horarios_padrao()
    # Seed do template padrão de cotação
    with _session() as s:
        if not s.exec(select(QuoteMessageTemplate)).first():
            s.add(QuoteMessageTemplate(id=1))
            s.commit()
    # Seed do admin: se não há nenhum usuário, cria admin com LOGIN/SENHA do .env
    with _session() as s:
        if not s.exec(select(Usuario)).first():
            import hashlib, os
            _salt = os.getenv("PASSWORD_SALT", "myautoatend2024")
            _hash = hashlib.sha256(f"{_salt}:{settings.admin_pass}".encode()).hexdigest()
            criar_usuario(
                nome="Administrador",
                email=settings.admin_user,
                senha_hash=_hash,
                papel="admin",
                telefone=settings.owner_phone,
            )


def _migrar() -> None:
    """Colunas adicionadas após a criação da tabela — o create_all não altera
    tabela existente, então o ALTER é manual."""
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(bloqueio)")}
        if cols and "data_fim" not in cols:
            conn.exec_driver_sql("ALTER TABLE bloqueio ADD COLUMN data_fim VARCHAR")
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(agendamento)")}
        if cols:
            for col, tipo in [
                ("observacoes", "VARCHAR NOT NULL DEFAULT ''"),
                ("vaga_id", "INTEGER"),
                ("veiculo", "VARCHAR NOT NULL DEFAULT ''"),
                ("placa", "VARCHAR NOT NULL DEFAULT ''"),
                ("modelo", "VARCHAR NOT NULL DEFAULT ''"),
                ("ano", "VARCHAR NOT NULL DEFAULT ''"),
                ("usuario_id", "INTEGER"),
                ("instancia_id", "INTEGER"),
                ("lembretes_enviados", "INTEGER NOT NULL DEFAULT 0"),
                ("ultimo_lembrete", "VARCHAR NOT NULL DEFAULT ''"),
                ("confirmado_em", "VARCHAR NOT NULL DEFAULT ''"),
                ("origem", "VARCHAR NOT NULL DEFAULT 'bot'"),
            ]:
                if col not in cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE agendamento ADD COLUMN {col} {tipo}"
                    )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(config)")}
        if cols and "avisar_dono" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE config ADD COLUMN avisar_dono BOOLEAN NOT NULL DEFAULT 1"
            )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(lembreteconfig)")}
        if cols:
            for col, tipo in [
                ("horas_antes2", "INTEGER NOT NULL DEFAULT 24"),
                ("mensagem2", "VARCHAR NOT NULL DEFAULT ''"),
                ("ativo2", "BOOLEAN NOT NULL DEFAULT 1"),
            ]:
                if col not in cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE lembreteconfig ADD COLUMN {col} {tipo}"
                    )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(instanciawhatsapp)")}
        if cols and "tipo" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE instanciawhatsapp ADD COLUMN tipo VARCHAR NOT NULL DEFAULT 'atendimento'"
            )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(config)")}
        if cols:
            for col, tipo in [
                ("quote_placa_token", "VARCHAR NOT NULL DEFAULT ''"),
                ("quote_placa_device_token", "VARCHAR NOT NULL DEFAULT ''"),
            ]:
                if col not in cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE config ADD COLUMN {col} {tipo}"
                    )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(quote_requests)")}
        if cols:
            for col, tipo in [
                ("placa", "VARCHAR NOT NULL DEFAULT ''"),
                ("veiculo_marca", "VARCHAR NOT NULL DEFAULT ''"),
                ("veiculo_modelo", "VARCHAR NOT NULL DEFAULT ''"),
                ("veiculo_ano", "VARCHAR NOT NULL DEFAULT ''"),
            ]:
                if col not in cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE quote_requests ADD COLUMN {col} {tipo}"
                    )
            conn.commit()
        # Agendamento agora pode ser feito por sintoma/descricao, sem serviço:
        # 1) coluna descricao 2) servico_id passa a ser opcional (nullable).
        cols = {r[1]: r for r in conn.exec_driver_sql("PRAGMA table_info(agendamento)")}
        if cols:
            if "descricao" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE agendamento ADD COLUMN descricao VARCHAR NOT NULL DEFAULT ''"
                )
            if cols["servico_id"][3]:  # notnull
                _reconstruir_agendamento(conn)
            conn.commit()


def _reconstruir_agendamento(conn) -> None:
    """SQLite não deixa dropar NOT NULL via ALTER — recria a tabela preservando
    os dados existentes e mantendo o servico_id opcional."""
    cols = conn.exec_driver_sql("PRAGMA table_info(agendamento)").fetchall()
    defs, nomes = [], []
    for cid, name, ctype, notnull, dflt, pk in cols:
        if name == "servico_id":
            notnull = 0
        d = f'"{name}" {ctype}'
        if notnull:
            d += " NOT NULL"
        if dflt is not None:
            d += f" DEFAULT {dflt}"
        if pk:
            d += " PRIMARY KEY"
        defs.append(d)
        nomes.append(f'"{name}"')
    conn.exec_driver_sql(f"CREATE TABLE agendamento_novo ({', '.join(defs)})")
    conn.exec_driver_sql(
        f"INSERT INTO agendamento_novo ({', '.join(nomes)}) SELECT {', '.join(nomes)} FROM agendamento"
    )
    conn.exec_driver_sql("DROP TABLE agendamento")
    conn.exec_driver_sql("ALTER TABLE agendamento_novo RENAME TO agendamento")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_config() -> Config:
    with _session() as s:
        return s.get(Config, 1)


def update_config(**campos) -> Config:
    with _lock, _session() as s:
        cfg = s.get(Config, 1)
        for k, v in campos.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        s.add(cfg)
        s.commit()
        return cfg


# ---------------------------------------------------------------------------
# Horários de funcionamento (grade semanal de atendimento)
# ---------------------------------------------------------------------------

# Padrão: segunda a sexta, 08:00–12:00 e 13:30–18:00.
HORARIOS_PADRAO: list[tuple[int, str, str]] = [
    (dia, inicio, fim)
    for dia in range(5)
    for inicio, fim in (("08:00", "12:00"), ("13:30", "18:00"))
]


def listar_horarios() -> list[HorarioFuncionamento]:
    with _session() as s:
        stmt = select(HorarioFuncionamento).order_by(
            HorarioFuncionamento.dia_semana, HorarioFuncionamento.inicio
        )
        return list(s.exec(stmt).all())


def horarios_do_dia(dia_semana: int) -> list[HorarioFuncionamento]:
    with _session() as s:
        stmt = (
            select(HorarioFuncionamento)
            .where(HorarioFuncionamento.dia_semana == dia_semana)
            .order_by(HorarioFuncionamento.inicio)
        )
        return list(s.exec(stmt).all())


def substituir_horarios(intervalos: list[tuple[int, str, str]]) -> None:
    """Troca a grade inteira (apaga tudo + grava) numa única transação."""
    with _lock, _session() as s:
        for h in s.exec(select(HorarioFuncionamento)).all():
            s.delete(h)
        for dia, inicio, fim in intervalos:
            s.add(HorarioFuncionamento(dia_semana=dia, inicio=inicio, fim=fim))
        s.commit()


def restaurar_horarios_padrao() -> None:
    substituir_horarios(HORARIOS_PADRAO)


def limpar_horarios() -> None:
    substituir_horarios([])


def dentro_do_funcionamento(inicio: datetime, fim: datetime) -> bool:
    """True se [inicio, fim] cabe inteiro num intervalo de funcionamento do dia."""
    dia = inicio.date()
    for h in horarios_do_dia(dia.weekday()):
        h_ini = datetime.fromisoformat(f"{dia}T{h.inicio}")
        h_fim = datetime.fromisoformat(f"{dia}T{h.fim}")
        if inicio >= h_ini and fim <= h_fim:
            return True
    return False


# ---------------------------------------------------------------------------
# Prompts do agente (system prompt editado pelo painel)
# ---------------------------------------------------------------------------


def get_prompt(chave: str) -> str | None:
    with _session() as s:
        p = s.get(Prompt, chave)
        return p.texto if p else None


def set_prompt(chave: str, texto: str) -> None:
    with _lock, _session() as s:
        p = s.get(Prompt, chave)
        if p:
            p.texto = texto
        else:
            p = Prompt(chave=chave, texto=texto)
        s.add(p)
        s.commit()


def _migrar_prompts() -> None:
    """Migração suave da chave `mcp` (bloco único) → `mcp_dono` + `mcp_cliente`.

    Roda só enquanto nenhuma das duas novas chaves existe. Se o dono tinha um
    texto customizado no `mcp` legado, semeia AMBAS com ele (uma vez) para não
    perder a personalização — cabe ao dono depois enxugar a versão do cliente
    no painel. Sem valor customizado, não cria linha: o agente usa os defaults
    de app/agente.py. A chave `mcp` deixa de ser lida."""
    if get_prompt("mcp_dono") is not None or get_prompt("mcp_cliente") is not None:
        return
    legado = get_prompt("mcp")
    if legado is None:
        return
    set_prompt("mcp_dono", legado)
    set_prompt("mcp_cliente", legado)


# ---------------------------------------------------------------------------
# Provedores de IA (config local — antes vivia nas credenciais do n8n)
# ---------------------------------------------------------------------------


def get_provedor_ia(alvo: str) -> ProvedorIA | None:
    with _session() as s:
        return s.get(ProvedorIA, alvo)


def set_provedor_ia(
    alvo: str,
    api_key: str | None = None,
    base_url: str | None = None,
    modelo: str | None = None,
) -> ProvedorIA:
    """Cria/atualiza só os campos passados (None = mantém)."""
    with _lock, _session() as s:
        p = s.get(ProvedorIA, alvo)
        if p is None:
            p = ProvedorIA(alvo=alvo, api_key="", base_url="")
        if api_key is not None:
            p.api_key = api_key
        if base_url is not None:
            p.base_url = base_url
        if modelo is not None:
            p.modelo = modelo
        p.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(p)
        s.commit()
        return p


# ---------------------------------------------------------------------------
# Tarefas (fila de ações proativas — consumida pelo worker de app/tarefas.py)
# ---------------------------------------------------------------------------


def criar_tarefa(
    tipo: str, telefone_alvo: str, payload: dict, agendado_para: str
) -> Tarefa:
    with _lock, _session() as s:
        t = Tarefa(
            tipo=tipo,
            telefone_alvo=telefone_alvo,
            payload=json.dumps(payload, ensure_ascii=False),
            agendado_para=agendado_para,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(t)
        s.commit()
        return t


def criar_aviso_cliente(
    ag: Agendamento,
    acao: str,
    agendado_para: str,
    inicio_anterior: str | None = None,
) -> Tarefa:
    """Enfileira aviso proativo da IA ao cliente sobre ação do dono/admin no
    agendamento — `acao` "reagendado" ou "cancelado" (instruções por ação em
    app/tarefas.py).

    Avisos pendentes do mesmo agendamento são substituídos: ao cliente só
    interessa o estado final. Reagendamento em cima de outro ainda não avisado
    herda o `inicio_anterior` do aviso substituído — o único horário que o
    cliente conhece.
    """
    payload: dict = {"agendamento_id": ag.id, "acao": acao}
    if inicio_anterior:
        payload["inicio_anterior"] = inicio_anterior
    for antigo in _obsoletar_avisos_pendentes(ag.id):
        if (
            acao == "reagendado"
            and antigo.get("acao") == "reagendado"
            and antigo.get("inicio_anterior")
        ):
            payload["inicio_anterior"] = antigo["inicio_anterior"]
            break
    return criar_tarefa("contatar_cliente", ag.telefone_cliente, payload, agendado_para)


def _obsoletar_avisos_pendentes(agendamento_id: int) -> list[dict]:
    """Conclui os `contatar_cliente` pendentes do agendamento; retorna os
    payloads substituídos na ordem de criação."""
    with _lock, _session() as s:
        stmt = (
            select(Tarefa)
            .where(Tarefa.tipo == "contatar_cliente", Tarefa.status == "pendente")
            .order_by(Tarefa.id)
        )
        substituidos: list[dict] = []
        for t in s.exec(stmt):
            payload = json.loads(t.payload or "{}")
            if payload.get("agendamento_id") != agendamento_id:
                continue
            t.status = "concluida"
            t.resultado = "Substituída por aviso mais recente."
            s.add(t)
            substituidos.append(payload)
        s.commit()
        return substituidos


def tarefas_vencidas(agora: str) -> list[Tarefa]:
    """Pendentes com hora de disparo alcançada, na ordem de criação."""
    with _session() as s:
        stmt = (
            select(Tarefa)
            .where(Tarefa.status == "pendente", Tarefa.agendado_para <= agora)
            .order_by(Tarefa.id)
        )
        return list(s.exec(stmt).all())


def atualizar_tarefa(tarefa_id: int, **campos) -> None:
    with _lock, _session() as s:
        t = s.get(Tarefa, tarefa_id)
        if not t:
            return
        for k, v in campos.items():
            if hasattr(t, k):
                setattr(t, k, v)
        s.add(t)
        s.commit()


def resetar_tarefas_executando() -> int:
    """Volta `executando` → `pendente` (retomada após crash/restart).
    Pode causar um reenvio — aceitável, tentativas são limitadas."""
    with _lock, _session() as s:
        presas = s.exec(select(Tarefa).where(Tarefa.status == "executando")).all()
        for t in presas:
            t.status = "pendente"
            s.add(t)
        s.commit()
        return len(presas)


def listar_tarefas_painel(limite_falhadas: int = 20) -> list[Tarefa]:
    """Fila visível no painel: todas pendente/executando + as últimas N falhadas.
    Concluídas e canceladas ficam de fora. Ordem: ativas por `agendado_para`
    (próxima a disparar primeiro), falhadas por id desc (mais recente no topo)."""
    with _session() as s:
        ativas = list(
            s.exec(
                select(Tarefa)
                .where(Tarefa.status.in_(["pendente", "executando"]))
                .order_by(Tarefa.agendado_para, Tarefa.id)
            ).all()
        )
        falhadas = list(
            s.exec(
                select(Tarefa)
                .where(Tarefa.status == "falhou")
                .order_by(Tarefa.id.desc())
                .limit(limite_falhadas)
            ).all()
        )
        return ativas + falhadas


def cancelar_tarefa(tarefa_id: int) -> bool:
    """Remove uma tarefa da fila (marca `cancelada`). Só pendente — `executando`
    está em voo e `tarefas_vencidas` só pega `pendente`, então o worker ignora."""
    with _lock, _session() as s:
        t = s.get(Tarefa, tarefa_id)
        if not t or t.status != "pendente":
            return False
        t.status = "cancelada"
        s.add(t)
        s.commit()
    return True


def excluir_tarefa(tarefa_id: int) -> bool:
    """Remove a tarefa da fila de forma definitiva (hard delete, qualquer status)."""
    with _lock, _session() as s:
        t = s.get(Tarefa, tarefa_id)
        if not t:
            return False
        s.delete(t)
        s.commit()
    return True


def chaves_conversas() -> list[str]:
    """Chaves (remoteJid) com memória existente — p/ o worker reusar a chave
    do contato em vez de inventar outra (nono dígito muda o jid)."""
    with _session() as s:
        return [c.telefone for c in s.exec(select(Conversa)).all()]


# ---------------------------------------------------------------------------
# Conversas do agente (memória por contato)
# ---------------------------------------------------------------------------


def get_conversa(telefone: str) -> str | None:
    with _session() as s:
        c = s.get(Conversa, telefone)
        return c.historico if c else None


def set_conversa(telefone: str, historico: str) -> None:
    with _lock, _session() as s:
        c = s.get(Conversa, telefone)
        if c:
            c.historico = historico
        else:
            c = Conversa(telefone=telefone, historico=historico)
        c.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(c)
        s.commit()


def listar_conversas() -> list[Conversa]:
    """Todas as conversas com memória (fonte da lista de conversas do painel)."""
    with _session() as s:
        return list(s.exec(select(Conversa)).all())


def resolver_chave_conversa(telefone: str) -> str:
    """remoteJid da memória de um contato a partir de um telefone qualquer.

    Reusa a chave existente se o número bater (o jid real pode diferir do
    E.164 pelo nono dígito, e inventar outra chave racharia o histórico);
    sem conversa ainda, constrói o jid a partir dos dígitos.
    """
    for chave in chaves_conversas():
        if mesmo_numero(chave, telefone):
            return chave
    return f"{re.sub(r'[^0-9]', '', telefone or '')}@s.whatsapp.net"


# ---------------------------------------------------------------------------
# Clientes (contatos conhecidos: nome + pausa do bot — cresce depois)
# ---------------------------------------------------------------------------


def get_cliente(telefone: str) -> Cliente | None:
    """Contato pelo telefone (normalizado para E.164). None se nunca falou."""
    with _session() as s:
        return s.get(Cliente, normalizar(telefone) or telefone)


def listar_clientes() -> list[Cliente]:
    with _session() as s:
        return list(s.exec(select(Cliente)).all())


def upsert_cliente(telefone: str, nome: str | None = None) -> Cliente:
    """Cria ou atualiza um contato. `nome` só sobrescreve quando vem
    preenchido — um pushName vazio não apaga o nome já salvo."""
    chave = normalizar(telefone) or telefone
    with _lock, _session() as s:
        c = s.get(Cliente, chave)
        if c is None:
            c = Cliente(telefone=chave)
        if nome and nome.strip():
            c.nome = nome.strip()
        s.add(c)
        s.commit()
        return c


def set_pausa_cliente(telefone: str, pausado: bool) -> Cliente:
    """Liga/desliga a pausa do bot para um contato (upsert)."""
    chave = normalizar(telefone) or telefone
    with _lock, _session() as s:
        c = s.get(Cliente, chave)
        if c is None:
            c = Cliente(telefone=chave)
        c.bot_pausado = pausado
        s.add(c)
        s.commit()
        return c


def cliente_pausado(telefone: str) -> bool:
    """True se o bot está pausado para este contato."""
    c = get_cliente(telefone)
    return bool(c and c.bot_pausado)


# ---------------------------------------------------------------------------
# Serviços
# ---------------------------------------------------------------------------


def listar_servicos_ativos() -> list[Servico]:
    with _session() as s:
        return list(s.exec(select(Servico).where(Servico.ativo == True)).all())  # noqa: E712


def listar_todos_servicos() -> list[Servico]:
    with _session() as s:
        return list(s.exec(select(Servico)).all())


def get_servico(servico_id: int) -> Servico | None:
    with _session() as s:
        return s.get(Servico, servico_id)


def criar_servico(nome: str, descricao: str, valor: float, duracao_min: int) -> Servico:
    with _lock, _session() as s:
        novo = Servico(nome=nome, descricao=descricao, valor=valor, duracao_min=duracao_min)
        s.add(novo)
        s.commit()
        return novo


def editar_servico(servico_id: int, **campos) -> Servico | None:
    with _lock, _session() as s:
        srv = s.get(Servico, servico_id)
        if not srv:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(srv, k):
                setattr(srv, k, v)
        s.add(srv)
        s.commit()
        return srv


def deletar_servico(servico_id: int) -> bool:
    with _lock, _session() as s:
        srv = s.get(Servico, servico_id)
        if not srv:
            return False
        s.delete(srv)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Bloqueios
# ---------------------------------------------------------------------------


def listar_bloqueios() -> list[Bloqueio]:
    with _session() as s:
        return list(s.exec(select(Bloqueio)).all())


def criar_bloqueio(
    data: str,
    inicio: str | None,
    fim: str | None,
    motivo: str = "",
    data_fim: str | None = None,
) -> Bloqueio:
    """`data_fim` (exclusivo p/ período) cobre todos os dias de data até data_fim."""
    with _lock, _session() as s:
        if data_fim == data:
            data_fim = None
        b = Bloqueio(data=data, data_fim=data_fim, inicio=inicio, fim=fim, motivo=motivo)
        s.add(b)
        s.commit()
        return b


def remover_bloqueio(bloqueio_id: int) -> bool:
    with _lock, _session() as s:
        b = s.get(Bloqueio, bloqueio_id)
        if not b:
            return False
        s.delete(b)
        s.commit()
    return True


def remover_bloqueio_por_data(data: str, data_fim: str | None = None) -> int:
    """Remove bloqueios que intersectam o período [data, data_fim].

    Um bloqueio de período é removido por inteiro (sem split): reabrir um dia
    no meio de férias reabre as férias todas — o chamador deve avisar isso.
    """
    ate = data_fim or data
    with _lock, _session() as s:
        achados = [
            b
            for b in s.exec(select(Bloqueio).where(Bloqueio.data <= ate)).all()
            if (b.data_fim or b.data) >= data
        ]
        for b in achados:
            s.delete(b)
        s.commit()
        return len(achados)


# ---------------------------------------------------------------------------
# Agendamentos
# ---------------------------------------------------------------------------


def listar_agendamentos(apenas_ativos: bool = True) -> list[Agendamento]:
    with _session() as s:
        stmt = select(Agendamento)
        if apenas_ativos:
            stmt = stmt.where(Agendamento.status.in_(STATUS_VIGENTES))
        return list(s.exec(stmt).all())


def agendamentos_do_telefone(telefone: str) -> list[Agendamento]:
    with _session() as s:
        ativos = s.exec(select(Agendamento).where(Agendamento.status.in_(STATUS_VIGENTES))).all()
    return [a for a in ativos if mesmo_numero(a.telefone_cliente, telefone)]


def get_agendamento(agendamento_id: int) -> Agendamento | None:
    with _session() as s:
        return s.get(Agendamento, agendamento_id)


def _conflita(s: Session, inicio: str, fim: str, ignorar_id: int | None = None) -> bool:
    """Checa sobreposição com agendamentos ativos e bloqueios (usa a sessão dada).

    Para vagas: retorna True se NÃO há vaga livre (todas ocupadas no período).
    """
    ini = datetime.fromisoformat(inicio)
    f = datetime.fromisoformat(fim)
    dia = ini.date().isoformat()

    for b in s.exec(select(Bloqueio).where(Bloqueio.data <= dia)).all():
        if (b.data_fim or b.data) < dia:
            continue
        if b.inicio is None:
            return True
        b_ini = datetime.fromisoformat(f"{dia}T{b.inicio}")
        b_fim = datetime.fromisoformat(f"{dia}T{b.fim}")
        if ini < b_fim and f > b_ini:
            return True

    # Conta quantas vagas existem
    vagas_ativas = s.exec(select(Vaga).where(Vaga.ativo == True)).all()
    total_vagas = len(vagas_ativas) or 1  # fallback p/ 1 se sem vaga configurada

    # Conta agendamentos ativos que sobrepõem o período
    contagem = 0
    for a in s.exec(select(Agendamento).where(Agendamento.status.in_(STATUS_VIGENTES))).all():
        if a.id == ignorar_id:
            continue
        a_ini = datetime.fromisoformat(a.inicio)
        a_fim = datetime.fromisoformat(a.fim)
        if ini < a_fim and f > a_ini:
            contagem += 1
            if contagem >= total_vagas:
                return True
    return False


def horario_disponivel(inicio: str, fim: str, ignorar_id: int | None = None) -> bool:
    with _session() as s:
        return not _conflita(s, inicio, fim, ignorar_id)


def vaga_disponivel_auto(inicio: str, fim: str, ignorar_id: int | None = None) -> int | None:
    """Auto-atribui uma vaga livre. Retorna o vaga_id ou None se lotado."""
    with _session() as s:
        vagas = s.exec(select(Vaga).where(Vaga.ativo == True).order_by(Vaga.ordem)).all()
        if not vagas:
            return None
        ocupadas: set[int] = set()
        ini = datetime.fromisoformat(inicio)
        f = datetime.fromisoformat(fim)
        for a in s.exec(select(Agendamento).where(Agendamento.status.in_(STATUS_VIGENTES))).all():
            if a.id == ignorar_id or a.vaga_id is None:
                continue
            a_ini = datetime.fromisoformat(a.inicio)
            a_fim = datetime.fromisoformat(a.fim)
            if ini < a_fim and f > a_ini:
                ocupadas.add(a.vaga_id)
        for v in vagas:
            if v.id not in ocupadas:
                return v.id
    return None


def criar_agendamento(
    servico_id: int | None,
    telefone_cliente: str,
    nome_cliente: str,
    inicio: str,
    fim: str,
    observacoes: str = "",
    veiculo: str = "",
    placa: str = "",
    modelo: str = "",
    ano: str = "",
    usuario_id: int | None = None,
    instancia_id: int | None = None,
    origem: str = "bot",
    descricao: str = "",
) -> Agendamento | None:
    """Checa conflito (por vagas), auto-atribui vaga e grava."""
    with _lock, _session() as s:
        if _conflita(s, inicio, fim):
            return None
        vaga_id = vaga_disponivel_auto(inicio, fim)
        a = Agendamento(
            servico_id=servico_id,
            telefone_cliente=telefone_cliente,
            nome_cliente=nome_cliente,
            inicio=inicio,
            fim=fim,
            observacoes=observacoes.strip(),
            veiculo=veiculo,
            placa=placa,
            modelo=modelo,
            ano=ano,
            vaga_id=vaga_id,
            usuario_id=usuario_id,
            instancia_id=instancia_id,
            origem=origem,
            descricao=descricao.strip(),
        )
        s.add(a)
        s.commit()
        return a


def reagendar_agendamento(agendamento_id: int, novo_inicio: str, novo_fim: str) -> bool:
    with _lock, _session() as s:
        a = s.get(Agendamento, agendamento_id)
        if not a or a.status not in STATUS_VIGENTES:
            return False
        if _conflita(s, novo_inicio, novo_fim, ignorar_id=agendamento_id):
            return False
        a.inicio = novo_inicio
        a.fim = novo_fim
        s.add(a)
        s.commit()
    return True


def cancelar_agendamento(agendamento_id: int) -> bool:
    with _lock, _session() as s:
        a = s.get(Agendamento, agendamento_id)
        if not a or a.status not in STATUS_VIGENTES:
            return False
        a.status = "cancelado"
        s.add(a)
        s.commit()
    return True


def confirmar_agendamento(agendamento_id: int) -> bool:
    with _lock, _session() as s:
        a = s.get(Agendamento, agendamento_id)
        if not a or a.status != "ativo":
            return False
        a.status = "confirmado"
        a.confirmado_em = datetime.now().isoformat(timespec="seconds")
        s.add(a)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Usuários (multi-usuário)
# ---------------------------------------------------------------------------


def listar_usuarios() -> list[Usuario]:
    with _session() as s:
        return list(s.exec(select(Usuario)).all())


def get_usuario(usuario_id: int) -> Usuario | None:
    with _session() as s:
        return s.get(Usuario, usuario_id)


def get_usuario_por_email(email: str) -> Usuario | None:
    with _session() as s:
        return s.exec(select(Usuario).where(Usuario.email == email)).first()


def criar_usuario(nome: str, email: str, senha_hash: str, papel: str = "atendente", telefone: str = "") -> Usuario:
    with _lock, _session() as s:
        u = Usuario(
            nome=nome, email=email, senha_hash=senha_hash,
            papel=papel, telefone=telefone,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(u)
        s.commit()
        return u


def editar_usuario(usuario_id: int, **campos) -> Usuario | None:
    with _lock, _session() as s:
        u = s.get(Usuario, usuario_id)
        if not u:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(u, k):
                setattr(u, k, v)
        s.add(u)
        s.commit()
        return u


def deletar_usuario(usuario_id: int) -> bool:
    """Remove um usuário do banco (hard delete).

    Desatrela agendamentos e instâncias que apontavam para ele antes de
    apagar a linha, para não deixar chave estrangeira órfã.
    """
    with _lock, _session() as s:
        u = s.get(Usuario, usuario_id)
        if not u:
            return False
        for a in s.exec(select(Agendamento).where(Agendamento.usuario_id == usuario_id)).all():
            a.usuario_id = None
            s.add(a)
        for i in s.exec(select(InstanciaWhatsApp).where(InstanciaWhatsApp.usuario_id == usuario_id)).all():
            i.usuario_id = None
            s.add(i)
        s.delete(u)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Instâncias WhatsApp (multi-instância)
# ---------------------------------------------------------------------------


def listar_instancias() -> list[InstanciaWhatsApp]:
    with _session() as s:
        return list(s.exec(select(InstanciaWhatsApp).order_by(InstanciaWhatsApp.nome)).all())


def get_instancia(instancia_id: int) -> InstanciaWhatsApp | None:
    with _session() as s:
        return s.get(InstanciaWhatsApp, instancia_id)


def get_instancia_por_nome(nome: str) -> InstanciaWhatsApp | None:
    with _session() as s:
        return s.exec(select(InstanciaWhatsApp).where(InstanciaWhatsApp.nome == nome)).first()


def instancias_do_usuario(usuario_id: int) -> list[InstanciaWhatsApp]:
    with _session() as s:
        return list(s.exec(select(InstanciaWhatsApp).where(InstanciaWhatsApp.usuario_id == usuario_id)).all())


def criar_instancia(nome: str, numero: str = "", usuario_id: int | None = None) -> InstanciaWhatsApp:
    with _lock, _session() as s:
        inst = InstanciaWhatsApp(
            nome=nome, numero=numero, usuario_id=usuario_id,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(inst)
        s.commit()
        return inst


def editar_instancia(instancia_id: int, **campos) -> InstanciaWhatsApp | None:
    with _lock, _session() as s:
        inst = s.get(InstanciaWhatsApp, instancia_id)
        if not inst:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(inst, k):
                setattr(inst, k, v)
        s.add(inst)
        s.commit()
        return inst


def deletar_instancia(instancia_id: int) -> bool:
    """Remove a instância do banco de forma definitiva (hard delete).

    Desatrela a instância de registros que a referenciam (agendamentos e
    destinos de transferência) antes de apagar a linha, para não deixar
    chave estrangeira órfã. Chamado depois de apagar a sessão na Evolution.
    """
    with _lock, _session() as s:
        inst = s.get(InstanciaWhatsApp, instancia_id)
        if not inst:
            return False
        for a in s.exec(select(Agendamento).where(Agendamento.instancia_id == instancia_id)).all():
            a.instancia_id = None
            s.add(a)
        for d in s.exec(select(TransferenciaDestino).where(TransferenciaDestino.instancia_id == instancia_id)).all():
            d.instancia_id = None
            s.add(d)
        s.delete(inst)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Vagas (boxes de atendimento)
# ---------------------------------------------------------------------------


def listar_vagas() -> list[Vaga]:
    with _session() as s:
        return list(s.exec(select(Vaga).order_by(Vaga.ordem)).all())


def get_vaga(vaga_id: int) -> Vaga | None:
    with _session() as s:
        return s.get(Vaga, vaga_id)


def criar_vaga(nome: str, descricao: str = "", ordem: int = 0) -> Vaga:
    with _lock, _session() as s:
        v = Vaga(nome=nome, descricao=descricao, ordem=ordem)
        s.add(v)
        s.commit()
        return v


def editar_vaga(vaga_id: int, **campos) -> Vaga | None:
    with _lock, _session() as s:
        v = s.get(Vaga, vaga_id)
        if not v:
            return None
        for k, v2 in campos.items():
            if v2 is not None and hasattr(v, k):
                setattr(v, k, v2)
        s.add(v)
        s.commit()
        return v


def deletar_vaga(vaga_id: int) -> bool:
    with _lock, _session() as s:
        v = s.get(Vaga, vaga_id)
        if not v:
            return False
        s.delete(v)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Transferencia de atendimento (destinos)
# ---------------------------------------------------------------------------


def listar_destinos_transferencia() -> list[TransferenciaDestino]:
    with _session() as s:
        return list(s.exec(select(TransferenciaDestino).where(TransferenciaDestino.ativo)).all())


def get_destino_transferencia(nome: str) -> TransferenciaDestino | None:
    with _session() as s:
        stmt = select(TransferenciaDestino).where(
            TransferenciaDestino.nome == nome,
            TransferenciaDestino.ativo,
        )
        return s.exec(stmt).first()


def criar_destino_transferencia(nome: str, telefone: str = "", instancia_id: int | None = None, mensagem: str = "") -> TransferenciaDestino:
    with _lock, _session() as s:
        d = TransferenciaDestino(
            nome=nome,
            telefone=telefone,
            instancia_id=instancia_id,
            mensagem=mensagem or f"Voce sera atendido pelo {nome} em breve.",
        )
        s.add(d)
        s.commit()
        return d


# ---------------------------------------------------------------------------
# Lembretes (config + busca)
# ---------------------------------------------------------------------------


def get_lembrete_config() -> LembreteConfig:
    with _session() as s:
        c = s.get(LembreteConfig, 1)
        if c is None:
            c = LembreteConfig(id=1)
            s.add(c)
            s.commit()
        return c


def update_lembrete_config(**campos) -> LembreteConfig:
    with _lock, _session() as s:
        c = s.get(LembreteConfig, 1)
        if c is None:
            c = LembreteConfig(id=1)
        for k, v in campos.items():
            if v is not None and hasattr(c, k):
                setattr(c, k, v)
        s.add(c)
        s.commit()
        return c


def agendamentos_precisando_lembrete(agora: str, horas_antes: int, stage: int = 0) -> list[Agendamento]:
    """Agendamentos ativos que precisam de lembrete no stage especificado.

    stage=0: primeiro aviso (lembretes_enviados == 0)
    stage=1: segundo aviso (lembretes_enviados == 1)
    """
    from datetime import timedelta
    limite = (datetime.fromisoformat(agora) + timedelta(hours=horas_antes)).isoformat(timespec="minutes")
    with _session() as s:
        return list(
            s.exec(
                select(Agendamento)
                .where(
                    Agendamento.status.in_(["ativo", "confirmado"]),
                    Agendamento.inicio <= limite,
                    Agendamento.inicio > agora,
                    Agendamento.lembretes_enviados == stage,
                )
            ).all()
        )


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------


def como_dict(obj) -> dict:
    return obj.model_dump()


init_db()
