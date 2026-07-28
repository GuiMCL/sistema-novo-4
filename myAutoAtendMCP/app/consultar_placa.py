"""Consulta de placa veicular via APIBrasil (api.apibrasil.io)."""

from __future__ import annotations

import re

import httpx

API_URL = "https://gateway.apibrasil.io/api/v2/vehicles/dados"


class PlacaError(Exception):
    """Erro na consulta de placa."""


def consultar_placa(
    placa: str,
    bearer_token: str,
    device_token: str,
    timeout: int = 10,
) -> dict:
    """Retorna {marca, modelo, ano, placa} ou levanta PlacaError."""
    placa_limpa = re.sub(r"[^A-Z0-9]", "", placa.upper())
    if len(placa_limpa) != 7:
        raise PlacaError("Placa inválida — deve ter 7 caracteres (ex.: ABC1D23)")

    headers = {
        "Content-Type": "application/json",
        "DeviceToken": device_token,
        "Authorization": f"Bearer {bearer_token}",
    }
    body = {"placa": placa_limpa}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(API_URL, json=body, headers=headers)
    except httpx.TimeoutException:
        raise PlacaError("Timeout ao consultar API de placa")
    except httpx.RequestError as e:
        raise PlacaError(f"Erro de conexão: {e}")

    if resp.status_code == 401:
        raise PlacaError("Token da API de placa inválido ou não autorizado")
    if resp.status_code == 429:
        raise PlacaError("Limite de consultas de placa excedido (100/dia)")
    if resp.status_code != 200:
        raise PlacaError(f"Erro na API de placa (HTTP {resp.status_code})")

    data = resp.json()
    veiculo = data.get("response") or data.get("data") or data

    marca = veiculo.get("marca") or veiculo.get("brand") or ""
    modelo = veiculo.get("modelo") or veiculo.get("model") or ""
    ano = veiculo.get("ano") or veiculo.get("year") or ""

    if not modelo and not marca:
        raise PlacaError("Placa não encontrada ou veículo não localizado na base")

    return {
        "placa": placa_limpa,
        "marca": marca.strip(),
        "modelo": modelo.strip(),
        "ano": str(ano).strip(),
    }