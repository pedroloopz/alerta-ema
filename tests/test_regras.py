import math

import numpy as np
import pandas as pd
import pytest

import donchian as d


# ------------------------------------------------ rompimento de 55 dias
def test_nivel_entrada_usa_55_barras():
    h = list(range(1, 101))            # maxima cresce 1..100
    assert d.nivel_entrada(h) == 100
    assert d.nivel_entrada(h[:-1]) == 99


def test_nivel_entrada_ignora_barras_antigas():
    h = [500] + [10] * 55              # o 500 ficou fora da janela de 55
    assert d.nivel_entrada(h) == 10


def test_rompimento_exige_preco_acima():
    h = [10.0] * 60
    nv = d.nivel_entrada(h)
    assert not d.rompeu(10.0, nv)      # igual nao rompe
    assert d.rompeu(10.01, nv)


def test_janela_parcial_para_historico_curto():
    assert math.isnan(d.nivel_entrada([1.0] * 19))
    assert d.nivel_entrada(list(range(20))) == 19          # 20 barras: usa todas
    assert d.nivel_entrada(list(range(30))) == 29


# ------------------------------------------------ saida na minima de 10 dias
def test_saida_quando_fecha_abaixo_da_minima_anterior():
    minimas = [10.0] * 10 + [9.5]       # a barra do dia nao entra na referencia
    fech = [11.0] * 10 + [9.9]
    assert d.sinal_saida(fech, minimas)                    # 9,9 < 10


def test_sem_saida_no_empate_ou_acima():
    minimas = [10.0] * 11
    assert not d.sinal_saida([11.0] * 10 + [10.0], minimas)
    assert not d.sinal_saida([11.0] * 11, minimas)


def test_saida_usa_so_10_barras_anteriores():
    minimas = [5.0] + [10.0] * 10 + [9.0]   # o 5,0 esta fora das 10 anteriores
    fech = [11.0] * 11 + [9.5]
    assert d.sinal_saida(fech, minimas)


def test_nivel_saida_vigente():
    assert d.nivel_saida([9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]) == 11   # o 9 saiu da janela
    assert math.isnan(d.nivel_saida([1, 2, 3]))


# ------------------------------------------------ filtro SPY / BTC
def test_filtro_mercado_acima_da_mma200():
    subindo = np.linspace(100, 200, 250)
    caindo = np.linspace(200, 100, 250)
    assert d.filtro_mercado(subindo)
    assert not d.filtro_mercado(caindo)
    assert not d.filtro_mercado(subindo[:150])            # sem 200 dias: nao libera


# ------------------------------------------------ momentum
def test_momentum_126():
    c = [100.0] * 126 + [150.0]
    assert d.momentum(c) == pytest.approx(0.5)
    assert d.momentum([100.0] * 30 + [110.0]) == pytest.approx(0.10)   # historico curto


# ------------------------------------------------ vagas: 7 no total, 1 cripto
def _pos(n_acao, n_cripto=0):
    return [{"tipo": "acao"}] * n_acao + [{"tipo": "cripto"}] * n_cripto


def test_vagas_livres():
    assert d.vagas_livres(_pos(0)) == (7, 1)
    assert d.vagas_livres(_pos(6)) == (1, 1)
    assert d.vagas_livres(_pos(6, 1)) == (0, 0)
    assert d.vagas_livres(_pos(3, 1)) == (3, 0)
    assert d.vagas_livres(_pos(9)) == (0, 0)               # legado acima de 7 nao quebra


def test_escolher_prioriza_momentum_e_respeita_vagas():
    sinais = [{"ticker": "A", "tipo": "acao", "momentum": 0.1},
              {"ticker": "B", "tipo": "acao", "momentum": 0.5},
              {"ticker": "C", "tipo": "acao", "momentum": 0.3}]
    esc, fora = d.escolher(sinais, _pos(5))
    assert [s["ticker"] for s in esc] == ["B", "C"]
    assert [s["ticker"] for s in fora] == ["A"]


def test_escolher_no_maximo_uma_cripto():
    sinais = [{"ticker": "ASTER", "tipo": "cripto", "momentum": 0.9},
              {"ticker": "X", "tipo": "acao", "momentum": 0.1}]
    esc, fora = d.escolher(sinais, _pos(2, 1))
    assert [s["ticker"] for s in esc] == ["X"]
    assert [s["ticker"] for s in fora] == ["ASTER"]
    esc, _ = d.escolher(sinais, _pos(2))
    assert [s["ticker"] for s in esc] == ["ASTER", "X"]


def test_escolher_sem_vaga_nenhuma():
    esc, fora = d.escolher([{"ticker": "A", "tipo": "acao", "momentum": 1}], _pos(7))
    assert esc == [] and len(fora) == 1


# ------------------------------------------------ tamanho e arredondamento
def test_arredonda_para_baixo_sem_erro_de_float():
    assert d.arredondar_baixo(1.23456789, 0.00001) == 1.23456
    assert d.arredondar_baixo(0.3, 0.1) == 0.3               # 0.3/0.1 = 2.9999... em float
    assert d.arredondar_baixo(123.459, 0.01) == 123.45
    assert d.casas(0.00001) == 5 and d.casas(0.01) == 2 and d.casas(1) == 0


def test_tamanho_patrimonio_dividido_por_7():
    valor, un = d.tamanho_entrada(patrimonio=7000, caixa=5000, preco=100, passo=0.00001)
    assert un == 10.0 and valor == 1000.0


def test_tamanho_limitado_ao_caixa():
    valor, un = d.tamanho_entrada(patrimonio=7000, caixa=300, preco=100, passo=0.001)
    assert un == 3.0 and valor == 300.0


def test_tamanho_respeita_ordem_minima_e_caixa_zero():
    assert d.tamanho_entrada(7000, 0, 100, 0.001) is None
    assert d.tamanho_entrada(7000, 4, 1.0, 0.01, minimo_usd=5) is None
    valor, un = d.tamanho_entrada(7000, 1000, 0.7, 0.01, minimo_usd=5)   # ASTER: passo 0,01
    assert un == 1428.57 and valor == pytest.approx(999.999, abs=0.01) and valor <= 1000


def test_barras_completas_tira_barra_em_andamento():
    idx = pd.to_datetime(["2026-09-24", "2026-09-25", "2026-09-28"])
    df = pd.DataFrame({"Close": [1, 2, 3]}, index=idx)
    assert len(d.barras_completas(df, "2026-09-28", True)) == 2
    assert len(d.barras_completas(df, "2026-09-28", False)) == 3
    assert len(d.barras_completas(df, "2026-09-29", True)) == 3
