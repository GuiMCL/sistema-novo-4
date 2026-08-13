"""Regras de expediente e validade de datas (camada de defesa do backend)."""

from datetime import date, datetime

from app import db


def test_dia_futuro_com_expediente_e_valido():
    assert db.pode_agendar_no_dia(date(2026, 8, 18), datetime(2026, 8, 17, 9, 0))


def test_dia_futuro_sem_expediente_e_invalido():
    # sábado (22/08/2026) — sem horários no padrão (seg-sex)
    assert not db.pode_agendar_no_dia(date(2026, 8, 22), datetime(2026, 8, 17, 9, 0))


def test_hoje_antes_do_encerramento_e_valido():
    # segunda 17/08 — 09:00 ainda dentro do expediente (08:00–12:00/13:30–18:00)
    assert db.pode_agendar_no_dia(date(2026, 8, 17), datetime(2026, 8, 17, 9, 0))


def test_hoje_no_limite_antes_do_encerramento_e_valido():
    assert db.pode_agendar_no_dia(date(2026, 8, 17), datetime(2026, 8, 17, 17, 59))


def test_hoje_apos_encerramento_nao_e_valido():
    # 18:01 → expediente encerrado → hoje não pode mais ser agendado
    assert not db.pode_agendar_no_dia(date(2026, 8, 17), datetime(2026, 8, 17, 18, 1))


def test_hoje_no_instante_do_encerramento_nao_e_valido():
    assert not db.pode_agendar_no_dia(date(2026, 8, 17), datetime(2026, 8, 17, 18, 0))


def test_hoje_antes_da_abertura_e_valido():
    # 06:00 da segunda — ainda dá para agendar para hoje (abre às 08:00)
    assert db.pode_agendar_no_dia(date(2026, 8, 17), datetime(2026, 8, 17, 6, 0))


def test_dia_bloqueado_por_periodo():
    db.criar_bloqueio(data="2026-08-17", inicio=None, fim=None, motivo="férias")
    assert db.dia_bloqueado(date(2026, 8, 17))
    assert not db.dia_bloqueado(date(2026, 8, 18))


def test_dia_bloqueio_parcial_nao_fecha_o_dia():
    db.criar_bloqueio(data="2026-08-17", inicio="10:00", fim="11:00", motivo="parcial")
    assert not db.dia_bloqueado(date(2026, 8, 17))
