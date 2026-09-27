"""
CALENDARIO E HORARIOS - bolsa americana (NYSE/Nasdaq) e cripto.

Tudo com zoneinfo: o codigo decide o horario, nao o cron.
  - Brasilia (America/Sao_Paulo) nao tem horario de verao.
  - Nova York (America/New_York) sai do horario de verao em 01/11/2026:
    o fechamento das 16h de NY passa de 17h para 18h em Brasilia.
  - Candle diario da Binance fecha 00h UTC = 21h em Brasilia.

Feriados calculados pelas regras da NYSE (nao depende de internet).
Fechamentos extraordinarios (luto nacional etc.) nao sao previsiveis: o bot
confere tambem se o SPY teve pregao no dia (dupla checagem).
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BR = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")

ABERTURA = time(9, 30)
FECHAMENTO = time(16, 0)
FECHAMENTO_CURTO = time(13, 0)


def _pascoa(ano):
    """Domingo de Pascoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _enesimo(ano, mes, dia_semana, n):
    """n-esimo dia da semana do mes (n=-1: ultimo). dia_semana: 0=segunda."""
    if n > 0:
        d = date(ano, mes, 1)
        d += timedelta(days=(dia_semana - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    prox = date(ano + (mes == 12), mes % 12 + 1, 1)
    d = prox - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - dia_semana) % 7)


def _observado(d):
    """Sabado -> sexta; domingo -> segunda."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def feriados(ano):
    """{data: nome} dos feriados da NYSE no ano."""
    f = {}
    ano_novo = date(ano, 1, 1)
    if ano_novo.weekday() == 6:
        f[date(ano, 1, 2)] = "Ano Novo"
    elif ano_novo.weekday() != 5:          # sabado: a NYSE nao compensa na sexta
        f[ano_novo] = "Ano Novo"
    f[_enesimo(ano, 1, 0, 3)] = "Martin Luther King Jr."
    f[_enesimo(ano, 2, 0, 3)] = "Presidents' Day"
    f[_pascoa(ano) - timedelta(days=2)] = "Sexta-feira Santa"
    f[_enesimo(ano, 5, 0, -1)] = "Memorial Day"
    if ano >= 2022:
        f[_observado(date(ano, 6, 19))] = "Juneteenth"
    f[_observado(date(ano, 7, 4))] = "Independencia"
    f[_enesimo(ano, 9, 0, 1)] = "Labor Day"
    f[_enesimo(ano, 11, 3, 4)] = "Acao de Gracas"
    f[_observado(date(ano, 12, 25))] = "Natal"
    return f


def eh_pregao(d):
    return d.weekday() < 5 and d not in feriados(d.year)


def pregao_curto(d):
    """Dias que fecham as 13h de NY."""
    if not eh_pregao(d):
        return False
    acao_gracas = _enesimo(d.year, 11, 3, 4)
    if d == acao_gracas + timedelta(days=1):
        return True
    if d == date(d.year, 12, 24):
        return True
    if d == date(d.year, 7, 3) and date(d.year, 7, 4).weekday() < 5:
        return True
    return False


def horario_pregao(d):
    """(abertura, fechamento) em datetime com fuso de NY; None se nao ha pregao."""
    if not eh_pregao(d):
        return None
    fim = FECHAMENTO_CURTO if pregao_curto(d) else FECHAMENTO
    return datetime.combine(d, ABERTURA, NY), datetime.combine(d, fim, NY)


def proximo_pregao(d, n=1):
    """n-esimo pregao depois de d."""
    while n > 0:
        d += timedelta(days=1)
        if eh_pregao(d):
            n -= 1
    return d


def pregao_anterior(d):
    d -= timedelta(days=1)
    while not eh_pregao(d):
        d -= timedelta(days=1)
    return d


def fase_eua(agora):
    """
    Em que momento do dia de NY estamos.
      'fechado'     - sem pregao hoje (fim de semana/feriado)
      'antes'       - antes da abertura
      'pregao'      - bolsa aberta
      'depois'      - depois do fechamento (hora de checar saidas)
    agora: datetime com fuso (qualquer).
    """
    d = agora.astimezone(NY).date()
    h = horario_pregao(d)
    if h is None:
        return "fechado"
    if agora < h[0]:
        return "antes"
    if agora < h[1]:
        return "pregao"
    return "depois"


def fechamento_em_brasilia(d):
    """Hora do fechamento de NY convertida para Brasilia (17h ou 18h; 14h/15h no dia curto)."""
    h = horario_pregao(d)
    return h[1].astimezone(BR) if h else None


def dia_cripto_fechado(agora):
    """Data (UTC) do ultimo candle diario da Binance ja fechado."""
    return (agora.astimezone(UTC) - timedelta(days=1)).date()


def hora_br(agora):
    return agora.astimezone(BR)
