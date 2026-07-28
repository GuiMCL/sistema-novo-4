"""Normalização de telefone via `phonenumbers` (parse real para E.164).

WhatsApp/Evolution API entrega números em formatos variados
(+55 45 9..., 5545 9..., 554599..., com/sem nono dígito, e sufixo
`@s.whatsapp.net`). Antes de QUALQUER comparação de autorização tudo é
reduzido à forma canônica E.164 (`+5545999990000`).
"""

from __future__ import annotations

import re

import phonenumbers

# Região assumida quando o número não traz DDI. Os números do WhatsApp já
# vêm com DDI, então o caminho normal é parsear com o "+" na frente.
_REGIAO_PADRAO = "BR"


def normalizar(telefone: str | None) -> str:
    """Reduz um telefone à forma canônica E.164. Vazio se não der para parsear."""
    if not telefone:
        return ""

    # Remove sufixo da Evolution (5545...@s.whatsapp.net) e símbolos.
    bruto = telefone.split("@")[0]
    digitos = re.sub(r"\D", "", bruto)
    if not digitos:
        return ""

    # 1) Número já com DDI: parseia como internacional ("+" + dígitos).
    try:
        num = phonenumbers.parse("+" + digitos, None)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(
                num, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass

    # 2) Fallback: número nacional, assume região padrão.
    try:
        num = phonenumbers.parse(digitos, _REGIAO_PADRAO)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(
                num, phonenumbers.PhoneNumberFormat.E164
            )
    except phonenumbers.NumberParseException:
        pass

    # 3) Não validou: devolve só os dígitos (comparação ainda funciona entre iguais).
    return digitos


def formatar_internacional(telefone: str | None) -> str:
    """Forma legível p/ exibição ("+55 45 99999-0000"). Aceita jid/E.164/dígitos;
    devolve o que recebeu (sem sufixo @) se não der para parsear."""
    if not telefone:
        return ""
    bruto = telefone.split("@")[0]
    digitos = re.sub(r"\D", "", bruto)
    try:
        num = phonenumbers.parse("+" + digitos, None)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(
                num, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
    except phonenumbers.NumberParseException:
        pass
    return bruto


def mesmo_numero(a: str | None, b: str | None) -> bool:
    """Compara dois telefones de forma tolerante a formatação."""
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    return na == nb
