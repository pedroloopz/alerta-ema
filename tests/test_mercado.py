from datetime import date, datetime

import mercado as m

UTC = m.UTC


def test_feriados_2026_nyse():
    esperado = {date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
                date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
                date(2026, 11, 26), date(2026, 12, 25)}
    assert set(m.feriados(2026)) == esperado


def test_feriados_2027_observados():
    f = m.feriados(2027)
    assert date(2027, 6, 18) in f        # Juneteenth no sabado -> sexta
    assert date(2027, 7, 5) in f         # 4 de julho no domingo -> segunda
    assert date(2027, 12, 24) in f       # Natal no sabado -> sexta
    assert date(2027, 3, 26) in f        # Sexta-feira Santa


def test_ano_novo_no_sabado_nao_compensa():
    assert date(2021, 12, 31) not in m.feriados(2021)
    assert date(2022, 1, 1) not in m.feriados(2022)


def test_pregao_e_fim_de_semana():
    assert m.eh_pregao(date(2026, 9, 28))
    assert not m.eh_pregao(date(2026, 9, 27))       # domingo
    assert not m.eh_pregao(date(2026, 11, 26))      # Acao de Gracas
    assert m.proximo_pregao(date(2026, 11, 25)) == date(2026, 11, 27)
    assert m.pregao_anterior(date(2026, 9, 28)) == date(2026, 9, 25)


def test_pregoes_curtos():
    assert m.pregao_curto(date(2026, 11, 27))
    assert m.pregao_curto(date(2026, 12, 24))
    assert not m.pregao_curto(date(2026, 7, 2))     # 3/7/2026 e feriado, 2/7 e normal
    assert not m.pregao_curto(date(2027, 12, 24))   # feriado observado


def test_horario_de_verao_ny_muda_em_01_11_2026():
    assert m.fechamento_em_brasilia(date(2026, 10, 30)).hour == 17
    assert m.fechamento_em_brasilia(date(2026, 11, 2)).hour == 18
    assert m.fechamento_em_brasilia(date(2026, 11, 27)).hour == 15    # dia curto, 13h NY


def test_fase_do_dia():
    # 28/09/2026 (segunda, horario de verao em NY: UTC-4)
    assert m.fase_eua(datetime(2026, 9, 28, 13, 15, tzinfo=UTC)) == "antes"
    assert m.fase_eua(datetime(2026, 9, 28, 14, 15, tzinfo=UTC)) == "pregao"
    assert m.fase_eua(datetime(2026, 9, 28, 19, 59, tzinfo=UTC)) == "pregao"
    assert m.fase_eua(datetime(2026, 9, 28, 20, 15, tzinfo=UTC)) == "depois"
    # 02/11/2026 (sem horario de verao: UTC-5) -> 20h15 UTC ainda e pregao
    assert m.fase_eua(datetime(2026, 11, 2, 20, 15, tzinfo=UTC)) == "pregao"
    assert m.fase_eua(datetime(2026, 11, 2, 21, 15, tzinfo=UTC)) == "depois"
    assert m.fase_eua(datetime(2026, 11, 26, 16, 0, tzinfo=UTC)) == "fechado"


def test_candle_cripto_fecha_21h_brasilia():
    agora = datetime(2026, 9, 28, 0, 5, tzinfo=UTC)          # 21h05 de 27/09 em Brasilia
    assert m.hora_br(agora).hour == 21
    assert m.dia_cripto_fechado(agora) == date(2026, 9, 27)
