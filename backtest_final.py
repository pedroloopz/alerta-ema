"""
BACKTEST FINAL - sistema Donchian da carteira EUA (+ ASTER) x alternativos.

Universo: exatamente o de universo_ondo.json (acoes + ASTER).
Treino 01/01/2015 a 31/12/2021 | validacao 01/01/2022 em diante.
Escolha pelo TREINO (CAGR / |pior queda|); a validacao so confere.

Variantes do Donchian (entrada: rompe a maxima de 55 pregoes com SPY > MMA 200;
ASTER: fecha acima da maxima de 55 dias com BTC > MMA 200, compra na abertura):
  A  saida 100% no fechamento abaixo da minima de 10 dias, 7 vagas, com ASTER
  B  igual a A, sem ASTER
  C  50% na minima de 5 dias + 50% na minima de 10 dias
  D  igual a A, com 5 vagas
Alternativos (mesmo universo, mesmos custos):
  E  maxima de 52 semanas: todo mes, as 7 mais perto da propria maxima de 252
     pregoes, so com SPY > MMA 200 (George e Hwang, 2004)
  F  Donchian 55/10 so dentro das 50 de maior alta em 6 meses (lista mensal)
  G  momentum mensal: as 7 de maior alta em 6 meses; caixa com SPY < MMA 200
  H  QQQ quando fecha o mes acima da media de 10 meses; senao caixa (Faber, 2007)
  I  metade no Donchian (A) e metade no H

Execucao simulada:
  acao: compra no rompimento (no nivel, ou na abertura se abrir acima);
        venda na abertura do pregao seguinte ao sinal.
  cripto: sinal no fechamento diario (21h de Brasilia), compra/venda na
          abertura seguinte (= logo depois do fechamento).
  tamanho: patrimonio / vagas, limitado ao caixa. Sem alavancagem.
Custos por lado: acoes 0,20% | ASTER 0,10%; testados x1, x1,5 e x2.

Dados: pasta DADOS (padrao: dados/), com os arquivos da coleta
(yahoo_*.csv.gz, binance_*.csv). O workflow final.yml baixa tudo sozinho.
"""

import json
import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DADOS = os.environ.get("DADOS", "dados")
INICIO = pd.Timestamp("2015-01-01")
CORTE = pd.Timestamp("2022-01-01")
JANELA_2M = 42                      # pregoes (~2 meses)
CUSTO_ACAO = 0.20 / 100
CUSTO_CRIPTO = 0.10 / 100
# Liquidez "da epoca": para entrar, a acao precisa ter negociado em media
# US$ 20 milhoes/dia nos 60 pregoes anteriores. Equivale, no passado, ao corte
# de liquidez usado hoje para montar o universo (evita comprar em 2016 uma
# acao que era minuscula e so ficou grande depois). 0 desliga.
LIQ_MIN = float(os.environ.get("LIQ_MIN", 20e6))

pct = lambda x, c=1: "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.{c}f}%".replace(".", ",")
num = lambda x, c=1: "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")
usd = lambda x: f"US$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ================================================================ DADOS
def ler_universo(arq="universo_ondo.json"):
    u = json.load(open(arq, encoding="utf-8"))
    acoes = [a["ticker"] for a in u["ativos"] if a["tipo"] == "acao"]
    cripto = [a["ticker"] for a in u["ativos"] if a["tipo"] == "cripto"]
    return acoes, cripto


def carregar(acoes, cripto, pasta=DADOS):
    campos = {}
    for k in ("open", "high", "low", "close", "volume"):
        df = pd.read_csv(f"{pasta}/yahoo_{k}.csv.gz", index_col=0, parse_dates=True)
        campos[k] = df
    vol = campos.pop("volume")
    faltam = [t for t in acoes if t not in campos["close"] or campos["close"][t].notna().sum() < 252]
    acoes = [t for t in acoes if t not in faltam]
    precos = {t: pd.DataFrame({**{k.capitalize(): campos[k][t] for k in campos}, "Volume": vol[t]}).dropna(
        subset=["Open", "High", "Low", "Close"]) for t in acoes}
    refs = {t: pd.DataFrame({k.capitalize(): campos[k][t] for k in campos}).dropna() for t in ("SPY", "QQQ")}
    cr = {}
    for t in cripto + ["BTC"]:
        arq = f"{pasta}/binance_{t}USDT.csv"
        df = pd.read_csv(arq, index_col=0, parse_dates=True)
        df.index = df.index.normalize()
        cr[t] = df[["Open", "High", "Low", "Close"]].dropna()
    return precos, refs, cr, faltam


# ================================================================ MATRIZES
@dataclass
class Base:
    idx: pd.DatetimeIndex            # dias (uniao: pregoes + dias de cripto)
    nomes: list
    tipo: np.ndarray                 # 0 acao, 1 cripto
    O: np.ndarray
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    Cff: np.ndarray                  # fechamento propagado (marcacao a mercado)
    nivel: np.ndarray                # maxima das 55 barras anteriores (>= 20 barras)
    lo10: np.ndarray                 # minima das 10 barras anteriores (inclui ontem)
    lo5: np.ndarray
    mom: np.ndarray                  # alta em 126 barras (parcial >= 20), no fechamento
    prox52: np.ndarray               # fechamento / maxima de 252 barras
    hist: np.ndarray                 # barras de historico ate o dia
    filtro: np.ndarray               # filtro de mercado valido PARA o sinal do dia
    liquido: np.ndarray              # liquidez da epoca ok (60 pregoes anteriores)
    pregao: np.ndarray               # dia de pregao da bolsa americana
    custo: np.ndarray
    qqq: pd.Series = field(default=None)
    spy_ok: pd.Series = field(default=None)


def _por_ativo(df):
    h, l, c = df["High"], df["Low"], df["Close"]
    nivel = h.rolling(55, min_periods=20).max().shift(1)
    lo10 = l.rolling(10).min().shift(1)
    lo5 = l.rolling(5).min().shift(1)
    base = c.shift(126).fillna(c.iloc[0])
    mom = (c / base - 1).where(np.arange(len(c)) >= 20)
    prox = c / h.rolling(252, min_periods=252).max()
    hist = pd.Series(np.arange(1, len(c) + 1), index=c.index)
    if "Volume" in df:
        liq = ((c * df["Volume"]).rolling(60, min_periods=20).mean().shift(1) >= LIQ_MIN).astype(float)
    else:
        liq = pd.Series(1.0, index=c.index)                # cripto: sem filtro
    return nivel, lo10, lo5, mom, prox, hist, liq


def montar(precos, refs, cr, com_cripto=True):
    spy = refs["SPY"]["Close"]
    spy_ok = spy > spy.rolling(200).mean()
    btc = cr["BTC"]["Close"]
    btc_ok = btc > btc.rolling(200).mean()
    ativos = dict(precos)
    tipos = {t: 0 for t in precos}
    if com_cripto:
        for t, df in cr.items():
            if t != "BTC":
                ativos[t] = df
                tipos[t] = 1
    idx = spy.index
    for t, df in ativos.items():
        if tipos[t] == 1:
            idx = idx.union(df.index)
    idx = idx[idx >= pd.Timestamp("2013-06-01")]
    nomes = list(ativos)
    n, m = len(idx), len(nomes)
    mat = {k: np.full((n, m), np.nan) for k in ("O", "H", "L", "C", "nivel", "lo10", "lo5", "mom", "prox", "hist", "filtro", "liq")}
    for j, t in enumerate(nomes):
        df = ativos[t]
        nivel, lo10, lo5, mom, prox, hist, liq = _por_ativo(df)
        if tipos[t] == 0:
            filt = spy_ok.shift(1).reindex(df.index).fillna(False)      # SPY no fechamento anterior
        else:
            filt = btc_ok.reindex(df.index).fillna(False)                # BTC no fechamento do sinal
        for k, s in (("O", df["Open"]), ("H", df["High"]), ("L", df["Low"]), ("C", df["Close"]),
                     ("nivel", nivel), ("lo10", lo10), ("lo5", lo5), ("mom", mom), ("prox", prox),
                     ("hist", hist), ("filtro", filt.astype(float)), ("liq", liq)):
            mat[k][:, j] = s.reindex(idx).to_numpy(dtype=float)
    Cff = pd.DataFrame(mat["C"]).ffill().to_numpy()
    tipo = np.array([tipos[t] for t in nomes])
    pregao = idx.isin(spy.index)
    custo = np.where(tipo == 1, CUSTO_CRIPTO, CUSTO_ACAO)
    return Base(idx, nomes, tipo, mat["O"], mat["H"], mat["L"], mat["C"], Cff, mat["nivel"], mat["lo10"],
                mat["lo5"], mat["mom"], mat["prox"], mat["hist"], mat["filtro"] > 0.5, mat["liq"] > 0.5, pregao, custo,
                qqq=refs["QQQ"], spy_ok=spy_ok)


# ================================================================ DONCHIAN
@dataclass
class Resultado:
    nome: str
    curva: pd.Series
    trades: list                      # (ativo, data_entrada, data_saida, retorno_liquido)
    ordens: list                      # datas de cada ordem (entrada ou saida)


def donchian(b, vagas=7, max_cripto=1, saida="min10", estresse=1.0, elegivel=None,
             folga_entrada=0.0, capital=1.0, nome="Donchian"):
    """Simulacao dia a dia. elegivel: matriz bool (dia x ativo) para restringir entradas."""
    n, m = b.C.shape
    caixa = capital
    pos = {}              # j -> dict(un, ent, custo_ent, data, parte_vendida)
    pend_saida = {}       # j -> fracao a vender na proxima abertura
    pend_entrada = []     # cripto: j a comprar na proxima abertura
    curva = np.empty(n)
    trades, ordens = [], []
    custo = b.custo * estresse
    ultimo_eq = capital
    for d in range(n):
        dia = b.idx[d]
        # ---- 1. aberturas: saidas pendentes
        for j in list(pend_saida):
            o = b.O[d, j]
            if np.isnan(o):
                continue
            p = pos[j]
            frac = pend_saida.pop(j)
            un = p["un"] * frac if frac < 1 else p["un"]
            caixa += un * o * (1 - custo[j])
            ordens.append(dia)
            p["recebido"] += un * o * (1 - custo[j])
            p["un"] -= un
            if p["un"] <= 1e-12 or frac >= 1:
                trades.append((b.nomes[j], p["data"], dia, p["recebido"] / p["pago"] - 1))
                del pos[j]
            else:
                p["parcial"] = True
        eq_ant = ultimo_eq
        # ---- 2. aberturas: compras de cripto decididas no fechamento anterior
        for j in pend_entrada:
            o = b.O[d, j]
            if np.isnan(o) or j in pos:
                continue
            ncr = sum(1 for k in pos if b.tipo[k] == 1)
            if len(pos) >= vagas or ncr >= max_cripto:
                continue
            v = min(eq_ant / vagas, caixa)
            if v <= 1e-9:
                continue
            caixa -= v
            pos[j] = dict(un=v * (1 - custo[j]) / o, pago=v, recebido=0.0, data=dia, parcial=False)
            ordens.append(dia)
        pend_entrada = []
        # ---- 3. pregao: rompimentos de acoes
        if b.pregao[d] and len(pos) < vagas:
            cand = []
            for j in np.where((b.tipo == 0) & b.filtro[d])[0]:
                if j in pos or np.isnan(b.nivel[d, j]) or np.isnan(b.H[d, j]) or not b.liquido[d, j]:
                    continue
                if elegivel is not None and not elegivel[d, j]:
                    continue
                nv = b.nivel[d, j]
                if b.H[d, j] >= nv * (1 + folga_entrada) or b.O[d, j] > nv:
                    ent = max(b.O[d, j], nv * (1 + folga_entrada))
                    if ent > b.H[d, j]:
                        ent = b.O[d, j]
                    mo = b.mom[d - 1, j] if d else np.nan
                    cand.append((-(mo if not np.isnan(mo) else -9), j, ent))
            for _, j, ent in sorted(cand):
                if len(pos) >= vagas:
                    break
                v = min(eq_ant / vagas, caixa)
                if v <= 1e-9:
                    break
                caixa -= v
                pos[j] = dict(un=v * (1 - custo[j]) / ent, pago=v, recebido=0.0, data=dia, parcial=False)
                ordens.append(dia)
        # ---- 4. fechamento: sinais de saida e de entrada cripto
        for j, p in pos.items():
            c = b.C[d, j]
            if np.isnan(c) or pend_saida.get(j) == 1.0:
                continue
            if saida == "5050" and not p["parcial"] and not np.isnan(b.lo5[d, j]) and c < b.lo5[d, j]:
                pend_saida[j] = 0.5
            if not np.isnan(b.lo10[d, j]) and c < b.lo10[d, j]:
                pend_saida[j] = 1.0
        if max_cripto > 0:
            for j in np.where(b.tipo == 1)[0]:
                if j in pos or np.isnan(b.C[d, j]) or np.isnan(b.nivel[d, j]):
                    continue
                if b.filtro[d, j] and b.C[d, j] > b.nivel[d, j]:
                    pend_entrada.append(j)
        # ---- 5. marcacao
        eq = caixa + sum(p["un"] * b.Cff[d, j] for j, p in pos.items() if not np.isnan(b.Cff[d, j]))
        curva[d] = eq
        ultimo_eq = eq
    return Resultado(nome, pd.Series(curva, index=b.idx), trades, ordens)


# ================================================================ MENSAIS (E, G)
def primeiros_do_mes(idx_pregoes):
    s = pd.Series(idx_pregoes, index=idx_pregoes)
    return set(s.groupby([idx_pregoes.year, idx_pregoes.month]).min())


def mensal(b, criterio, vagas=7, estresse=1.0, nome="Mensal", capital=1.0):
    """Rebalanceamento no primeiro pregao do mes, na abertura, com dados do
    fechamento anterior. criterio: 'prox52' ou 'mom'. So acoes."""
    n, m = b.C.shape
    custo = b.custo * estresse
    pregoes = b.idx[b.pregao]
    inicio_mes = primeiros_do_mes(pregoes)
    ultimo_pregao = None
    caixa, pos = capital, {}
    curva, trades, ordens = np.empty(n), [], []
    ult = capital
    for d in range(n):
        dia = b.idx[d]
        if b.pregao[d] and dia in inicio_mes and ultimo_pregao is not None:
            dp = ultimo_pregao
            spy_ok = bool(b.spy_ok.asof(b.idx[dp]))                  # SPY no fechamento anterior
            alvo = []
            if spy_ok:
                sc = b.prox52 if criterio == "prox52" else b.mom
                ok = [(sc[dp, j], j) for j in np.where(b.tipo == 0)[0]
                      if not np.isnan(sc[dp, j]) and b.hist[dp, j] >= 252 and not np.isnan(b.O[d, j])
                      and b.liquido[d, j]]
                alvo = [j for _, j in sorted(ok, reverse=True)[:vagas]]
            for j in [j for j in pos if j not in alvo]:
                o = b.O[d, j] if not np.isnan(b.O[d, j]) else b.Cff[dp, j]
                p = pos.pop(j)
                rec = p["un"] * o * (1 - custo[j])
                caixa += rec
                trades.append((b.nomes[j], p["data"], dia, rec / p["pago"] - 1))
                ordens.append(dia)
            eq = caixa + sum(p["un"] * b.Cff[dp, j] for j, p in pos.items())
            for j in alvo:
                if j in pos:
                    continue
                v = min(eq / vagas, caixa)
                if v <= 1e-9:
                    break
                caixa -= v
                pos[j] = dict(un=v * (1 - custo[j]) / b.O[d, j], pago=v, data=dia)
                ordens.append(dia)
        if b.pregao[d]:
            ultimo_pregao = d
        ult = caixa + sum(p["un"] * b.Cff[d, j] for j, p in pos.items())
        curva[d] = ult
    return Resultado(nome, pd.Series(curva, index=b.idx), trades, ordens)


def top50_momentum(b, k=50):
    """Matriz elegivel: no mes, so as k acoes de maior alta em 6 meses
    (medida no ultimo pregao do mes anterior)."""
    n, m = b.C.shape
    el = np.zeros((n, m), dtype=bool)
    pregoes = b.idx[b.pregao]
    inicio_mes = primeiros_do_mes(pregoes)
    atual = np.zeros(m, dtype=bool)
    ultimo = None
    for d in range(n):
        if b.pregao[d] and b.idx[d] in inicio_mes and ultimo is not None:
            sc = [(b.mom[ultimo, j], j) for j in np.where(b.tipo == 0)[0]
                  if not np.isnan(b.mom[ultimo, j]) and b.hist[ultimo, j] >= 126 and b.liquido[d, j]]
            atual = np.zeros(m, dtype=bool)
            for _, j in sorted(sc, reverse=True)[:k]:
                atual[j] = True
            atual[b.tipo == 1] = True                  # cripto segue a regra propria
        if b.pregao[d]:
            ultimo = d
        el[d] = atual
    return el


# ================================================================ QQQ (H)
def qqq_faber(b, estresse=1.0, capital=1.0, nome="H. QQQ 10 meses"):
    q = b.qqq
    fim_mes = q["Close"].groupby([q.index.year, q.index.month]).tail(1)
    mm10 = fim_mes.rolling(10).mean()
    sinal = (fim_mes > mm10)
    custo = CUSTO_ACAO * estresse
    caixa, un, data_ent, pago = capital, 0.0, None, 0.0
    trades, ordens = [], []
    decisao = {}
    datas = list(q.index)
    for dfm, ok in sinal.items():
        i = datas.index(dfm)
        if i + 1 < len(datas) and not np.isnan(mm10.get(dfm, np.nan)):
            decisao[datas[i + 1]] = bool(ok)
    curva = []
    qo, qc = q["Open"], q["Close"]
    for dia in b.idx:
        if dia in decisao:
            quer = decisao[dia]
            if quer and un == 0:
                pago = caixa
                un = caixa * (1 - custo) / qo[dia]
                caixa, data_ent = 0.0, dia
                ordens.append(dia)
            elif not quer and un > 0:
                rec = un * qo[dia] * (1 - custo)
                trades.append(("QQQ", data_ent, dia, rec / pago - 1))
                caixa, un = rec, 0.0
                ordens.append(dia)
        preco = qc.asof(dia)
        curva.append(caixa + un * preco)
    return Resultado(nome, pd.Series(curva, index=b.idx), trades, ordens)


def metade(r1, r2, nome="I. Metade/metade"):
    return Resultado(nome, r1.curva + r2.curva, r1.trades + r2.trades, r1.ordens + r2.ordens)


# ================================================================ METRICAS
def cagr_queda(curva, ini, fim=None):
    s = curva[(curva.index >= ini) & ((curva.index < fim) if fim is not None else True)].dropna()
    if len(s) < 20 or s.iloc[0] <= 0:
        return math.nan, math.nan
    anos = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1) * 100, ((s / s.cummax()) - 1).min() * 100


def estat_trades(trades, ini, fim=None):
    r = np.array([t[3] for t in trades if t[1] >= ini and (fim is None or t[1] < fim)]) * 100
    if not len(r):
        return dict(n=0, acerto=math.nan, ganho=math.nan, perda=math.nan)
    return dict(n=len(r), acerto=(r > 0).mean() * 100,
                ganho=r[r > 0].mean() if (r > 0).any() else math.nan,
                perda=r[r <= 0].mean() if (r <= 0).any() else math.nan)


def por_periodo(datas, ini, fim, pregoes):
    d = [x for x in datas if x >= ini and (fim is None or x < fim)]
    p = pregoes[(pregoes >= ini) & ((pregoes < fim) if fim is not None else True)]
    anos = len(p) / 252
    semanas = anos * 52
    return len(d) / anos if anos else math.nan, len(d) / semanas if semanas else math.nan, len(d) / (anos * 12) if anos else math.nan


def dois_meses(curva, pregoes, ini=CORTE):
    s = curva.reindex(pregoes).dropna()
    s = s[s.index >= ini]
    r = (s.shift(-JANELA_2M) / s - 1).dropna() * 100
    if not len(r):
        return dict(med=math.nan, p10=math.nan, p90=math.nan, perda=math.nan)
    return dict(med=r.median(), p10=r.quantile(0.10), p90=r.quantile(0.90), perda=(r < 0).mean() * 100)


def resumo(res, b, pregoes):
    fim_tr = CORTE
    out = {"nome": res.nome}
    out["ct"], out["qt"] = cagr_queda(res.curva, INICIO, fim_tr)
    out["cv"], out["qv"] = cagr_queda(res.curva, CORTE)
    out["ca"], out["qa"] = cagr_queda(res.curva, INICIO)
    out["mar_t"] = out["ct"] / abs(out["qt"]) if out["qt"] else math.nan
    out["mar_v"] = out["cv"] / abs(out["qv"]) if out["qv"] else math.nan
    for rot, ini, fim in (("t", INICIO, fim_tr), ("v", CORTE, None)):
        e = estat_trades(res.trades, ini, fim)
        for k, v in e.items():
            out[f"{k}_{rot}"] = v
        tpa, _, _ = por_periodo([t[1] for t in res.trades], ini, fim, pregoes)
        _, ops, opm = por_periodo(res.ordens, ini, fim, pregoes)
        out[f"tpa_{rot}"], out[f"ops_{rot}"], out[f"opm_{rot}"] = tpa, ops, opm
    out["m2"] = dois_meses(res.curva, pregoes)
    out["m2_recente"] = dois_meses(res.curva, pregoes, pd.Timestamp("2024-01-01"))
    return out


def qqq_segurar(b):
    q = b.qqq["Close"]
    return Resultado("QQQ segurar", q.reindex(b.idx).ffill() / q[q.index >= INICIO].iloc[0], [], [])


# ================================================================ RELATORIO
def linha(r):
    return (f"  treino {pct(r['ct'])} a.a. / queda {pct(r['qt'])} (ret/queda {num(r['mar_t'], 2)}) -> "
            f"validacao {pct(r['cv'])} / {pct(r['qv'])} (ret/queda {num(r['mar_v'], 2)})")


def detalhes(r):
    L = []
    for rot, nome in (("t", "treino"), ("v", "validacao")):
        L.append(f"    {nome}: {r[f'n_{rot}']} trades | acerto {num(r[f'acerto_{rot}'], 0)}% | ganho medio "
                 f"{pct(r[f'ganho_{rot}'])} | perda media {pct(r[f'perda_{rot}'])} | "
                 f"{num(r[f'tpa_{rot}'])} trades/ano | {num(r[f'ops_{rot}'])} ordens/semana | "
                 f"{num(r[f'opm_{rot}'])} ordens/mes")
    m2 = r["m2"]
    L.append(f"    2 meses (valid.): mediana {pct(m2['med'])} | ruim (10%) {pct(m2['p10'])} | bom (90%) "
             f"{pct(m2['p90'])} | chance de prejuizo {num(m2['perda'], 0)}%")
    m2 = r["m2_recente"]
    L.append(f"    2 meses (desde 2024): mediana {pct(m2['med'])} | ruim {pct(m2['p10'])} | bom "
             f"{pct(m2['p90'])} | chance de prejuizo {num(m2['perda'], 0)}%")
    return L


def main():
    acoes, cripto = ler_universo()
    precos, refs, cr, faltam = carregar(acoes, cripto)
    b = montar(precos, refs, cr, com_cripto=True)
    b_sem = montar(precos, refs, cr, com_cripto=False)
    pregoes = refs["SPY"].index
    capital = 100.0                                   # capital fictício (repositório público); os % não mudam

    L = ["BACKTEST FINAL - DONCHIAN 55/10 NA CARTEIRA EUA (+ ASTER) x ALTERNATIVOS", ""]
    L.append(f"Universo: {len(precos)} acoes de universo_ondo.json + {', '.join(cripto)} "
             f"| sem 1 ano de historico no Yahoo (fora): {', '.join(faltam) or 'nenhuma'}")
    L.append(f"Periodo: {INICIO:%d/%m/%Y} a {pregoes[-1]:%d/%m/%Y} | treino ate 31/12/2021 | validacao desde {CORTE:%d/%m/%Y}")
    L.append(f"ASTER desde {cr[cripto[0]].index[0]:%d/%m/%Y} (Binance spot) - so pesa no fim da validacao"
             if cripto else "")
    L.append("Custos por lado: acoes 0,20% | ASTER 0,10%. Sem alavancagem. Resultado em % do patrimonio.")
    L.append(f"Liquidez da epoca: so entra acao com media de US$ {LIQ_MIN / 1e6:.0f} mi/dia nos 60 pregoes anteriores."
             if LIQ_MIN else "Sem filtro de liquidez da epoca.")
    L.append("")

    res = {}
    for est in (1.0, 1.5, 2.0):
        res[("A", est)] = donchian(b, 7, 1, "min10", est, nome="A. Donchian 55/10, 7 vagas, com ASTER")
        res[("B", est)] = donchian(b_sem, 7, 0, "min10", est, nome="B. Igual a A, sem ASTER")
        res[("C", est)] = donchian(b, 7, 1, "5050", est, nome="C. Saida 50% minima 5 + 50% minima 10")
        res[("D", est)] = donchian(b, 5, 1, "min10", est, nome="D. Igual a A, 5 vagas")
    el = top50_momentum(b)
    for est in (1.0, 1.5, 2.0):
        res[("E", est)] = mensal(b, "prox52", 7, est, nome="E. Maxima de 52 semanas (mensal)")
        res[("F", est)] = donchian(b, 7, 1, "min10", est, elegivel=el, nome="F. Donchian no top 50 de momentum")
        res[("G", est)] = mensal(b, "mom", 7, est, nome="G. Momentum mensal top 7 com filtro")
        res[("H", est)] = qqq_faber(b, est)
        a_meio = donchian(b, 7, 1, "min10", est, capital=0.5)
        h_meio = qqq_faber(b, est, capital=0.5)
        res[("I", est)] = metade(a_meio, h_meio)
    res[("A1%", 1.5)] = donchian(b, 7, 1, "min10", 1.5, folga_entrada=0.01, nome="A com entrada 1% acima do nivel")
    qs = resumo(qqq_segurar(b), b, pregoes)

    R = {k: resumo(v, b, pregoes) for k, v in res.items()}

    L.append("=" * 78)
    L.append("1. VARIANTES DO DONCHIAN (custo x1,5 = referencia; x1 e x2 abaixo)")
    L.append("=" * 78)
    for k in "ABCD":
        r = R[(k, 1.5)]
        L += [r["nome"], linha(r)] + detalhes(r)
        L.append(f"  custo x1: valid {pct(R[(k, 1.0)]['cv'])}/{pct(R[(k, 1.0)]['qv'])} | "
                 f"custo x2: treino {pct(R[(k, 2.0)]['ct'])} valid {pct(R[(k, 2.0)]['cv'])}/{pct(R[(k, 2.0)]['qv'])}")
        L.append("")
    r = R[("A1%", 1.5)]
    L += ["Sensibilidade: A pagando 1% acima do nivel de rompimento (alerta de hora em hora):", linha(r), ""]
    L.append(f"QQQ segurar: treino {pct(qs['ct'])}/{pct(qs['qt'])} -> validacao {pct(qs['cv'])}/{pct(qs['qv'])}")
    m2 = qs["m2"]
    L.append(f"  2 meses (valid.): mediana {pct(m2['med'])} | ruim {pct(m2['p10'])} | bom {pct(m2['p90'])} | "
             f"chance de prejuizo {num(m2['perda'], 0)}%")
    L.append("")

    var = {k: R[(k, 1.5)] for k in "ABCD"}
    melhor = max(var, key=lambda k: var[k]["mar_t"] if not math.isnan(var[k]["mar_t"]) else -9)
    c_melhor = (var["C"]["mar_t"] > var["A"]["mar_t"] * 1.10 and var["C"]["mar_v"] > var["A"]["mar_v"])
    L.append(f"Melhor retorno/queda no TREINO: {var[melhor]['nome']}")
    L.append(f"C (50/50) claramente melhor que A (treino +10% no ret/queda E melhor na validacao)? "
             f"{'SIM' if c_melhor else 'NAO -> a saida fica 100% na minima de 10 dias'}")
    L.append("")

    L.append("=" * 78)
    L.append("2. SISTEMAS ALTERNATIVOS (custo x1,5)")
    L.append("=" * 78)
    for k in "AEFGHI":
        r = R[(k, 1.5)]
        L += [r["nome"], linha(r)] + detalhes(r)
        L.append(f"  custo x2: treino {pct(R[(k, 2.0)]['ct'])} valid {pct(R[(k, 2.0)]['cv'])}/{pct(R[(k, 2.0)]['qv'])}")
        L.append("")
    alt = {k: R[(k, 1.5)] for k in "AEFGHI"}
    venc = max(alt, key=lambda k: alt[k]["mar_t"] if not math.isnan(alt[k]["mar_t"]) else -9)
    L.append("Ranking pelo TREINO (retorno / pior queda):")
    for k in sorted(alt, key=lambda k: -(alt[k]["mar_t"] if not math.isnan(alt[k]["mar_t"]) else -9)):
        r = alt[k]
        L.append(f"  {k}: treino {num(r['mar_t'], 2)} | validacao {num(r['mar_v'], 2)} | "
                 f"2 meses mediana {pct(r['m2']['med'])}, prejuizo {num(r['m2']['perda'], 0)}% | "
                 f"{num(r['opm_v'])} ordens/mes")
    L.append(f"Vencedor no treino: {alt[venc]['nome']}")
    L.append("")

    # ---------------- conclusao
    a, qm = R[("A", 1.5)], qs["m2"]
    escolhida = "C" if c_melhor else max("ABD", key=lambda k: var[k]["mar_t"] if not math.isnan(var[k]["mar_t"]) else -9)
    L.append("=" * 78)
    L.append("3. CONCLUSAO")
    L.append("=" * 78)
    L.append(f"Variante escolhida pelo treino: {var[escolhida]['nome']}")
    L.append(f"  C x A no treino: ret/queda {num(var['C']['mar_t'], 2)} x {num(var['A']['mar_t'], 2)}; "
             f"validacao {num(var['C']['mar_v'], 2)} x {num(var['A']['mar_v'], 2)}"
             + (" -> C nao e claramente melhor: saida 100% na minima de 10 dias." if not c_melhor else ""))
    L.append(f"  D (5 vagas) no treino: {num(var['D']['mar_t'], 2)} | B (sem ASTER) = A no treino "
             f"(a ASTER so existe desde 10/2025); na validacao {pct(var['B']['cv'])} x {pct(var['A']['cv'])}.")
    L.append(f"  Contra segurar QQQ: A {pct(a['cv'])} x QQQ {pct(qs['cv'])} a.a. na validacao "
             f"(queda {pct(a['qv'])} x {pct(qs['qv'])}).")
    L.append("Horizonte de 2 meses (janelas de 42 pregoes desde 2022):")
    for k in ("A", "D", "E", "G", "H", "I"):
        m = R[(k, 1.5)]["m2"]
        L.append(f"  {k}: mediana {pct(m['med'])} | ruim {pct(m['p10'])} | bom {pct(m['p90'])} | prejuizo {num(m['perda'], 0)}%")
    L.append(f"  QQQ segurar: mediana {pct(qm['med'])} | ruim {pct(qm['p10'])} | bom {pct(qm['p90'])} | prejuizo {num(qm['perda'], 0)}%")
    L.append("  Obs.: com o SPY abaixo da MMA 200 o Donchian fica em caixa (janela = 0%), por isso a mediana")
    L.append("  desde 2022 fica perto de zero; desde 2024 (SPY quase sempre acima) a mediana do A e "
             f"{pct(a['m2_recente']['med'])}.")
    if venc != "A":
        L.append(f"Alternativo com melhor treino: {alt[venc]['nome']} ({num(alt[venc]['mar_t'], 2)} x "
                 f"{num(alt['A']['mar_t'], 2)} do A). Validacao: {num(alt[venc]['mar_v'], 2)} x {num(alt['A']['mar_v'], 2)}. "
                 "NAO troco sem o seu ok.")
    L.append("")
    L.append("Obs.: o universo e a lista de HOJE (acoes que sobreviveram e subiram): vies de")
    L.append("sobrevivencia infla todos os sistemas de acoes, mais nos que escolhem por momentum.")

    texto = "\n".join(L)
    print(texto)
    open("relatorio_final.txt", "w", encoding="utf-8").write(texto + "\n")
    linhas = [{"chave": f"{k[0]}_x{k[1]}", **{c: v for c, v in r.items() if not isinstance(v, dict)}}
              for k, r in R.items()]
    pd.DataFrame(linhas).to_csv("final_resumo.csv", index=False)


if __name__ == "__main__":
    main()
