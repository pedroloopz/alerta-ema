"""
Varredura de parametros com treino e validacao.

Testa 81 combinacoes (EMA x prazo x stop x alvo) sob premissas
PESSIMISTAS e mostra o que sobrevive nos dados que o ajuste nunca viu.

Premissas:
  - entrada na ABERTURA do candle seguinte ao toque (o alerta chega depois)
  - se SL e TP cabem no mesmo candle, conta SL
  - cenario pessimista: custo real = 1,5 x custo estimado
  - a nota e sempre calculada como o robo ao vivo calcula, para que o
    filtro de sinais seja o mesmo; so a SAIDA varia entre combinacoes
"""

import os
from itertools import product

import numpy as np
import pandas as pd

from simbolos import SIMBOLOS, setor_de
from alerta_ema import (
    baixar_candles, calcular_nota, forca_rejeicao, limitar,
    custo_ida_volta, tp_com_custo, avisar,
    MULT_SL, PERIODO_ATR, PERIODO_VOLUME, CANDLES_INCLINACAO,
    CANDLES_PARA_TRAS, MAX_DIST_ENTRADA_ATR,
)

# ---------------------------------------------------------------- grade
EMAS = [9, 20, 50]
PRAZOS_DIAS = [1, 2, 3]
STOPS_ATR = [0.5, 0.8, 1.0]
RAZOES = [1.0, 1.5, 2.0]
NOTA_CORTE = 7.0

FRACAO_TREINO = 0.60
ESTRESSE_CUSTO = 1.5
N_MINIMO = 40
CANDLES_POR_DIA = 6
BASE = (20, 3, 0.8, 2.0)          # configuracao atual do robo
# ----------------------------------------------------------------------


def serie_atr_pct(h, l, c, periodo=PERIODO_ATR):
    ant = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - ant), np.abs(l - ant)])
    atr = pd.Series(tr).ewm(alpha=1 / periodo, adjust=False).mean().to_numpy()
    return atr / c * 100


def preparar(simbolo):
    c4 = baixar_candles(simbolo, "4h", 1500)
    d1 = baixar_candles(simbolo, "1d", 500)
    if len(c4) < 200 or len(d1) < PERIODO_ATR + 5:
        return None

    o, h, l, c = (c4[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    v, vusd = c4["Volume"].to_numpy(float), c4["VolumeUSD"].to_numpy(float)
    t = c4["Inicio"].to_numpy()

    # Diario: usar sempre o ultimo dia FECHADO antes do candle
    dh, dl, dc = (d1[k].to_numpy(float) for k in ("High", "Low", "Close"))
    d_atr = serie_atr_pct(dh, dl, dc)
    d_ema = pd.Series(dc).ewm(span=20, adjust=False).mean().to_numpy()
    d_dias = d1["Inicio"].dt.normalize().to_numpy()
    idx_dia = np.searchsorted(d_dias, c4["Inicio"].dt.normalize().to_numpy(), side="left") - 1

    return {
        "simbolo": simbolo, "t": t, "o": o, "h": h, "l": l, "c": c, "v": v,
        "atr4": serie_atr_pct(h, l, c),
        "liq": pd.Series(vusd).rolling(6, min_periods=1).sum().to_numpy(),
        "vol_media": pd.Series(v).shift(1).rolling(PERIODO_VOLUME).mean().to_numpy(),
        "idx_dia": idx_dia, "d_atr": d_atr,
        "d_acima": dc > d_ema,
        "emas": {p: pd.Series(c).ewm(span=p, adjust=False).mean().to_numpy() for p in EMAS},
    }


def sinais(d, periodo):
    """Mesma logica do robo ao vivo, em versao vetorizada."""
    h, l, c, e = d["h"], d["l"], d["c"], d["emas"][periodo]
    n = len(c)
    aquecimento = max(periodo, PERIODO_VOLUME) + CANDLES_INCLINACAO + 10
    saida = []

    for i in np.where((l <= e) & (e <= h))[0]:
        if i < aquecimento or i >= n - 2:
            continue
        j = d["idx_dia"][i]
        if j < PERIODO_ATR:
            continue

        lado = None
        for k in range(i - 1, max(i - CANDLES_PARA_TRAS, -1), -1):
            if l[k] > e[k]:
                lado = "acima"; break
            if h[k] < e[k]:
                lado = "abaixo"; break
        if lado is None:
            continue
        direcao = "LONG" if lado == "acima" else "SHORT"

        incl = (e[i] / e[i - CANDLES_INCLINACAO] - 1) * 100
        if (direcao == "LONG" and incl < 0) or (direcao == "SHORT" and incl > 0):
            continue
        if (direcao == "LONG" and c[i] < e[i]) or (direcao == "SHORT" and c[i] > e[i]):
            continue

        dist = (c[i] / e[i] - 1) * 100
        atr4 = d["atr4"][i]
        if abs(dist) > MAX_DIST_ENTRADA_ATR * atr4:
            continue

        atrd = d["d_atr"][j]
        custo = custo_ida_volta(d["liq"][i])
        sl_vivo = limitar(MULT_SL * atrd)
        tp_vivo = tp_com_custo(sl_vivo, custo)

        vm = d["vol_media"][i]
        vol_rel = float(d["v"][i] / vm) if vm and vm > 0 else None
        rej = forca_rejeicao(h[i], l[i], c[i], direcao)

        no_lado = 0
        for k in range(i - 1, -1, -1):
            if (lado == "acima" and l[k] > e[k]) or (lado == "abaixo" and h[k] < e[k]):
                no_lado += 1
            else:
                break

        dia_ok = bool(d["d_acima"][j]) if direcao == "LONG" else not bool(d["d_acima"][j])
        nota, _ = calcular_nota(sl_vivo, tp_vivo, atrd, incl, dist, atr4,
                                vol_rel, no_lado, d["liq"][i], rej, dia_ok)
        if nota < NOTA_CORTE:
            continue

        saida.append({"i": i, "direcao": direcao, "atrd": atrd,
                      "custo": custo, "nota": nota})
    return saida


def simular(d, s, prazo, mult_sl, razao):
    i = s["i"]
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    entrada = o[i + 1]
    sl = limitar(mult_sl * s["atrd"])
    # TP que entrega a razao pedida LIQUIDA do custo estimado
    tp = razao * sl + (razao + 1) * s["custo"]

    fim = min(i + prazo * CANDLES_POR_DIA, len(c) - 1)
    hh, ll = h[i + 1: fim + 1], l[i + 1: fim + 1]
    if s["direcao"] == "LONG":
        p_sl, p_tp = entrada * (1 - sl / 100), entrada * (1 + tp / 100)
        bate_sl, bate_tp = ll <= p_sl, hh >= p_tp
    else:
        p_sl, p_tp = entrada * (1 + sl / 100), entrada * (1 - tp / 100)
        bate_sl, bate_tp = hh >= p_sl, ll <= p_tp

    k_sl = np.argmax(bate_sl) if bate_sl.any() else 10**9
    k_tp = np.argmax(bate_tp) if bate_tp.any() else 10**9

    if k_sl == 10**9 and k_tp == 10**9:
        var = (c[fim] / entrada - 1) * 100
        return ("PRAZO", var if s["direcao"] == "LONG" else -var)
    if k_sl <= k_tp:                          # empate conta como stop
        return ("SL", -sl)
    return ("TP", tp)


def metricas(ops):
    if not ops:
        return None
    base = np.array([o["base"] for o in ops])
    pess = np.array([o["pess"] for o in ops])
    saidas = pd.Series([o["saida"] for o in ops]).value_counts()
    return {"n": len(ops), "acerto": (pess > 0).mean() * 100,
            "base": base.mean(), "pess": pess.mean(),
            "tp": int(saidas.get("TP", 0)), "sl": int(saidas.get("SL", 0)),
            "prazo": int(saidas.get("PRAZO", 0))}


def risco(ops):
    """Drawdown maximo e pior sequencia, em dolar (US$ 1.000 a 2x)."""
    ops = sorted(ops, key=lambda o: o["t"])
    curva = np.cumsum([o["pess"] * 20 for o in ops])
    dd = float((np.maximum.accumulate(np.r_[0, curva]) - np.r_[0, curva]).max())
    seq = pior = 0
    for o in ops:
        seq = seq + 1 if o["pess"] <= 0 else 0
        pior = max(pior, seq)
    return dd, pior


def main():
    dados = []
    for s in SIMBOLOS:
        try:
            d = preparar(s)
        except Exception as e:
            print(f"{s}: {e}")
            continue
        if d:
            dados.append(d)
    print(f"{len(dados)} simbolos carregados")

    # Sinais por EMA (nao dependem da saida)
    por_ema = {p: [(d, s) for d in dados for s in sinais(d, p)] for p in EMAS}
    todos_t = [d["t"][s["i"]] for p in EMAS for d, s in por_ema[p]]
    t0, t1 = min(todos_t), max(todos_t)
    corte = t0 + (t1 - t0) * FRACAO_TREINO

    resultados = {}
    for ema, prazo, stop, razao in product(EMAS, PRAZOS_DIAS, STOPS_ATR, RAZOES):
        treino, valid = [], []
        for d, s in por_ema[ema]:
            saida, bruto = simular(d, s, prazo, stop, razao)
            op = {"t": d["t"][s["i"]], "saida": saida,
                  "base": bruto - s["custo"],
                  "pess": bruto - ESTRESSE_CUSTO * s["custo"]}
            (treino if op["t"] < corte else valid).append(op)
        resultados[(ema, prazo, stop, razao)] = (treino, valid)

    linhas_csv = []
    for chave, (tr, va) in resultados.items():
        mt, mv = metricas(tr), metricas(va)
        linhas_csv.append({
            "ema": chave[0], "prazo_dias": chave[1], "stop_atr": chave[2], "razao": chave[3],
            **{f"treino_{k}": v for k, v in (mt or {}).items()},
            **{f"valid_{k}": v for k, v in (mv or {}).items()},
        })
    pd.DataFrame(linhas_csv).to_csv("varredura.csv", index=False)

    # ---------------------------------------------------------- relatorio
    fmt = lambda x: f"{x:+.2f}".replace(".", ",")
    pct = lambda x: f"{x:.0f}%"
    dia = lambda t: pd.Timestamp(t).strftime("%d/%m")
    L = ["VARREDURA - 81 combinacoes",
         f"Treino {dia(t0)} a {dia(corte)} | Validacao {dia(corte)} a {dia(t1)}",
         "Entrada no candle seguinte | SL primeiro | custo x1,5", ""]

    def linha_cfg(chave):
        tr, va = resultados[chave]
        mt, mv = metricas(tr), metricas(va)
        e, p, s, r = chave
        nome = f"EMA{e} {p}d SL{s:.1f} {r:.1f}:1".replace(".", ",")
        a = f"{fmt(mt['pess'])}%" if mt else "-"
        b = (f"n={mv['n']} {pct(mv['acerto'])} {fmt(mv['pess'])}%" if mv else "-")
        return f"{nome}\n  treino {a} | valid {b}"

    L.append("ATUAL DO ROBO:")
    L.append(linha_cfg(BASE))
    L.append("")

    elegiveis = [k for k, (tr, va) in resultados.items()
                 if metricas(tr) and metricas(tr)["n"] >= N_MINIMO]
    top = sorted(elegiveis, key=lambda k: metricas(resultados[k][0])["pess"], reverse=True)[:5]

    L.append("TOP 5 NO TREINO -> resultado na validacao:")
    for k in top:
        L.append(linha_cfg(k))
    L.append("")

    positivas = sum(1 for k, (tr, va) in resultados.items()
                    if metricas(va) and metricas(va)["n"] >= 20 and metricas(va)["pess"] > 0)
    L.append(f"Positivas na validacao (pessimista): {positivas} de {len(resultados)}")

    vencedor = max(top, key=lambda k: (metricas(resultados[k][1]) or {"pess": -99})["pess"])
    mv = metricas(resultados[vencedor][1])
    if mv:
        dd, seq = risco(resultados[vencedor][1])
        e, p, s, r = vencedor
        L += ["", "MELHOR DO TOP 5 NA VALIDACAO:",
              f"EMA {e} | prazo {p}d | stop {s} ATR | alvo {r}:1".replace(".", ","),
              f"n={mv['n']} | acerto {pct(mv['acerto'])}",
              f"medio: base {fmt(mv['base'])}% | pessimista {fmt(mv['pess'])}%",
              f"em dolar (pess.): US$ {fmt(mv['pess'] * 20)} por operacao",
              f"saidas: TP {mv['tp']} | SL {mv['sl']} | prazo {mv['prazo']}",
              f"drawdown max: US$ {dd:.0f} | pior sequencia: {seq} perdas"]

    texto = "\n".join(L)
    print(texto)
    if os.environ.get("TELEGRAM_TOKEN"):
        avisar(texto[:3900])


if __name__ == "__main__":
    main()
