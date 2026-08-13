"""Debounce: entrega duplicada do webhook não gera resposta duplicada."""

from app import whatsapp
from tests.conftest import JID_CLIENTE


class _FakeTask:
    """Substitui asyncio.create_task no teste — não agenda o sleep de 6s."""

    def __init__(self, coro):
        coro.close()
        self._cancelado = False

    def done(self):
        return self._cancelado

    def cancel(self):
        self._cancelado = True


def test_debounce_ignora_mensagem_repetida_consecutiva(monkeypatch):
    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    whatsapp._buffers.clear()
    whatsapp._timers.clear()
    try:
        whatsapp._agendar_lote(JID_CLIENTE, "oi")
        whatsapp._agendar_lote(JID_CLIENTE, "oi")  # re-entrega → ignorada
        whatsapp._agendar_lote(JID_CLIENTE, "tudo bem?")
        assert whatsapp._buffers[JID_CLIENTE] == ["oi", "tudo bem?"]
    finally:
        whatsapp._buffers.clear()
        whatsapp._timers.clear()


def test_debounce_aceita_mensagens_diferentes(monkeypatch):
    monkeypatch.setattr(whatsapp.asyncio, "create_task", _FakeTask)
    whatsapp._buffers.clear()
    whatsapp._timers.clear()
    try:
        whatsapp._agendar_lote(JID_CLIENTE, "sim")
        whatsapp._agendar_lote(JID_CLIENTE, "não")
        whatsapp._agendar_lote(JID_CLIENTE, "sim")
        assert whatsapp._buffers[JID_CLIENTE] == ["sim", "não", "sim"]
    finally:
        whatsapp._buffers.clear()
        whatsapp._timers.clear()
