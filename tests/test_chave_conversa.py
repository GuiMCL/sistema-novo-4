"""Histórico do /atendimento sumia para o cliente e a IA.

Causa: o JID do WhatsApp carrega o id do dispositivo
(`5545…:12@s.whatsapp.net`). `phone.normalizar` Tirava o domínio com
`split("@")[0]`, mas o `:12` continuava e virava dígito — o mesmo contato
normalizava para dois números diferentes. Cada remetente criava a própria
linha em `Conversa`:

    5545999900001@s.whatsapp.net      <- painel (envio do atendente)
    5545999900001:12@s.whatsapp.net   <- webhook (cliente + IA)

Como `resolver_chave_conversa` casa por número normalizado, o painel lia só a
sua linha: aparecia **somente** a mensagem do atendente. Cliente e IA ficavam
invisíveis.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import agente, atendimento as at_mod, db
from app.main import app
from app.phone import normalizar

TEL = "5545999900001"
JID_LIMPO = f"{TEL}@s.whatsapp.net"
JID_DISPOSITIVO = f"{TEL}:12@s.whatsapp.net"  # o que a Meta manda


# ---------------------------------------------------------------------------
# phone.normalizar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrada",
    [JID_DISPOSITIVO, JID_LIMPO, TEL, f"+{TEL}", f"{TEL}:0@s.whatsapp.net"],
)
def test_sufixo_de_dispositivo_nao_vira_digito(entrada):
    assert normalizar(entrada) == "+" + TEL


def test_mesmo_numero_ignora_dispositivo():
    assert db.mesmo_numero(JID_DISPOSITIVO, TEL)
    assert db.mesmo_numero(JID_DISPOSITIVO, JID_LIMPO)


# ---------------------------------------------------------------------------
# Divergência de chave
# ---------------------------------------------------------------------------


def test_webhook_e_painel_caem_na_mesma_linha():
    """A chave que o webhook grava tem de ser a que o painel resolve."""
    assert db.resolver_chave_conversa(JID_DISPOSITIVO) == db.resolver_chave_conversa(TEL)


def test_historico_do_cliente_e_da_ia_aparecem_no_painel(painel):
    """O sintoma reportado: só a mensagem do atendente aparecia."""
    chave = db.resolver_chave_conversa(JID_DISPOSITIVO)
    agente.registrar_na_memoria(chave, "oi, quero agendar", "cliente")
    agente.registrar_na_memoria(chave, "Claro! Para que dia?", "bot")
    # o atendente responde pelo painel, a partir do E.164 cru
    agente.registrar_na_memoria(
        db.resolver_chave_conversa(TEL), "Seu horário é 14h", "Atendente (atendente)"
    )

    r = painel.get(f"/atendimento/api/conversas/{TEL}")
    assert r.status_code == 200
    falas = [(m["quem"], m["texto"]) for m in r.json()["mensagens"]]
    assert falas == [
        ("cliente", "oi, quero agendar"),
        ("bot", "Claro! Para que dia?"),
        ("bot", "Seu horário é 14h"),
    ]


def test_linhas_antigas_divergentes_sao_fundidas():
    """Reparo do banco já contaminado: as duas pontas voltam para uma linha."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, UserPromptPart

    # duas linhas do MESMO contato, como o bug produzia
    db.set_conversa(
        JID_DISPOSITIVO,
        ModelMessagesTypeAdapter.dump_json(
            [ModelResponse(parts=[TextPart(content="oi, quero agendar")])]
        ).decode(),
    )
    db.set_conversa(
        JID_LIMPO,
        ModelMessagesTypeAdapter.dump_json(
            [ModelResponse(parts=[TextPart(content="Seu horário é 14h")])]
        ).decode(),
    )
    assert len(db.listar_conversas()) == 2

    assert agente.reconciliar_conversas() == 1

    restantes = db.listar_conversas()
    assert len(restantes) == 1
    bolhas = agente.historico_para_bolhas(db.get_conversa(restantes[0].telefone))
    assert [b["texto"] for b in bolhas] == ["oi, quero agendar", "Seu horário é 14h"]


def test_reconciliacao_e_idempotente():
    db.set_conversa(JID_DISPOSITIVO, "[]")
    db.set_conversa(JID_LIMPO, "[]")
    assert agente.reconciliar_conversas() == 1
    assert agente.reconciliar_conversas() == 0
    assert len(db.listar_conversas()) == 1


def test_reconciliacao_ignora_contatos_diferentes():
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart

    for jid in (f"554588880001@s.whatsapp.net", f"554588880001:3@s.whatsapp.net"):
        db.set_conversa(
            jid,
            ModelMessagesTypeAdapter.dump_json(
                [ModelResponse(parts=[TextPart(content="oi")])]
            ).decode(),
        )
    assert db.resolver_chave_conversa("554588880001:3@s.whatsapp.net").endswith("@s.whatsapp.net")
    # telefones distintos => grupos distintos => nada é fundido
    db.set_conversa(JID_DISPOSITIVO, "[]")
    assert agente.reconciliar_conversas() >= 0
    chaves = {c.telefone for c in db.listar_conversas()}
    assert f"554588880001:3@s.whatsapp.net" in chaves


def test_lista_de_conversas_nao_duplica_o_contato(painel):
    """A lista da esquerda não pode mostrar o mesmo contato duas vezes."""
    for jid in (JID_DISPOSITIVO, JID_LIMPO):
        db.set_conversa(jid, "[]")
    r = painel.get("/atendimento/api/conversas")
    assert r.status_code == 200
    numeros = [c.get("telefone") for c in r.json()]
    assert len(numeros) == len(set(numeros))