"""
REGRAS DO SISTEMA DONCHIAN (carteira EUA) - funcoes puras, sem rede.

Usadas pelo bot.py (ao vivo), pelo backtest_final.py e pelos testes.

Entrada
  acao:  preco rompe a maxima dos ultimos 55 pregoes COMPLETOS, com o SPY
         acima da media simples de 200 dias no fechamento anterior.
  ASTER: fechamento diario acima da maxima dos 55 dias anteriores, com o BTC
         acima da media simples de 200 dias. Historico curto: usa a janela
         disponivel se tiver pelo menos 20 dias.
  Mais sinais que vagas: prioridade para a maior alta em 126 pregoes.
Saida
  fechamento abaixo da minima dos 10 pregoes ANTERIORES -> vende na abertura.
Vagas
  7 no total, no maximo 1 cripto. Posicao legada ocupa vaga.
Tamanho
  patrimonio / 7, limitado ao caixa; quantidade arredondada PARA BAIXO no
  passo do token; abaixo da ordem minima nao entra.
"""

import math
from decimal import ROUND_DOWN, Decimal

import numpy as np
import pandas as pd

N_ENTRADA = 55
N_SAIDA = 10
N_SAIDA_RAPIDA = 5          # usada so pela variante C (50/50)
N_MIN_HISTORICO = 20        # ASTER e acoes novas: janela parcial a partir de 20 dias
N_MOMENTUM = 126
MMA_FILTRO = 200
VAGAS = 7
MAX_CRIPTO = 1


def _arr(x):
    return np.asarray(x, dtype=float)


def nivel_entrada(maximas, n=N_ENTRADA, minimo=N_MIN_HISTORICO):
    """Maxima das ultimas n barras COMPLETAS (sem a de hoje, se estiver em aberto).
    Com menos de n barras usa todas, desde que haja pelo menos `minimo`."""
    h = _arr(maximas)
    h = h[~np.isnan(h)]
    if len(h) < minimo:
        return math.nan
    return float(h[-n:].max())


def rompeu(preco, nivel):
    return not math.isnan(nivel) and preco > nivel


def nivel_saida(minimas, n=N_SAIDA):
    """Minima das ultimas n barras completas = preco de saida vigente para o
    PROXIMO fechamento."""
    l = _arr(minimas)
    l = l[~np.isnan(l)]
    if len(l) < n:
        return math.nan
    return float(l[-n:].min())


def sinal_saida(fechamentos, minimas, n=N_SAIDA):
    """True se o ultimo fechamento ficou abaixo da minima das n barras anteriores
    a ele (a propria barra do dia nao entra)."""
    c, l = _arr(fechamentos), _arr(minimas)
    if len(c) < n + 1 or len(l) < n + 1:
        return False
    ref = l[-n - 1:-1]
    if np.isnan(ref).any() or np.isnan(c[-1]):
        return False
    return bool(c[-1] < ref.min())


def filtro_mercado(fechamentos, n=MMA_FILTRO):
    """Ultimo fechamento acima da media simples de n dias (inclui o proprio dia)."""
    c = _arr(fechamentos)
    c = c[~np.isnan(c)]
    if len(c) < n:
        return False
    return bool(c[-1] > c[-n:].mean())


def momentum(fechamentos, n=N_MOMENTUM):
    """Alta em n pregoes; com historico curto usa o que tiver (minimo 20)."""
    c = _arr(fechamentos)
    c = c[~np.isnan(c)]
    if len(c) < N_MIN_HISTORICO + 1:
        return math.nan
    base = c[-n - 1] if len(c) > n else c[0]
    return float(c[-1] / base - 1)


# ---------------------------------------------------------------- tamanho
def arredondar_baixo(qtd, passo):
    """Arredonda para baixo no multiplo do passo (sem erro de ponto flutuante)."""
    if qtd <= 0:
        return 0.0
    p = Decimal(str(passo))
    return float((Decimal(str(qtd)) / p).to_integral_value(rounding=ROUND_DOWN) * p)


def casas(passo):
    """Numero de casas decimais do passo (0.00001 -> 5)."""
    e = Decimal(str(passo)).normalize().as_tuple().exponent
    return max(0, -e)


def tamanho_entrada(patrimonio, caixa, preco, passo, minimo_usd=0.0, vagas=VAGAS):
    """(valor_usd, unidades) da entrada, ou None se nao da para entrar.
    valor = patrimonio / vagas, limitado ao caixa; unidades arredondadas para baixo."""
    if preco <= 0 or caixa <= 0:
        return None
    alvo = min(patrimonio / vagas, caixa)
    un = arredondar_baixo(alvo / preco, passo)
    valor = un * preco
    if un <= 0 or valor < max(minimo_usd, 1e-9):
        return None
    return round(valor, 2), un


# ---------------------------------------------------------------- vagas
def vagas_livres(posicoes, vagas=VAGAS, max_cripto=MAX_CRIPTO):
    """posicoes: iteravel de dicts com 'tipo' ('acao' | 'cripto').
    Devolve (livres_total, livres_cripto)."""
    pos = list(posicoes)
    total = max(0, vagas - len(pos))
    cripto = sum(1 for p in pos if p.get("tipo") == "cripto")
    return total, max(0, min(total, max_cripto - cripto))


def escolher(sinais, posicoes, vagas=VAGAS, max_cripto=MAX_CRIPTO):
    """Ordena os sinais pela maior alta em 126 pregoes e respeita as vagas.
    sinais: lista de dicts com 'ticker', 'tipo', 'momentum'.
    Devolve (escolhidos, sem_vaga)."""
    livres, livres_c = vagas_livres(posicoes, vagas, max_cripto)
    ordem = sorted(sinais, key=lambda s: -(s["momentum"] if not math.isnan(s["momentum"]) else -1e9))
    escolhidos, sem_vaga = [], []
    for s in ordem:
        if livres <= 0 or (s["tipo"] == "cripto" and livres_c <= 0):
            sem_vaga.append(s)
            continue
        escolhidos.append(s)
        livres -= 1
        if s["tipo"] == "cripto":
            livres_c -= 1
        livres_c = min(livres_c, livres)
    return escolhidos, sem_vaga


# ---------------------------------------------------------------- barras
def barras_completas(df, hoje, em_andamento):
    """Tira a barra de hoje se o pregao ainda esta em andamento."""
    if em_andamento and len(df) and pd.Timestamp(df.index[-1]).normalize() == pd.Timestamp(hoje):
        return df.iloc[:-1]
    return df
