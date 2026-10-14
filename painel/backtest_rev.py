"""Backtest do setup REV ("reversão com volume", padrão das carteiras vencedoras da Hyperliquid)
nos perps da BingX, comparado com os setups atuais do painel (ROMP, PULL, EXA, SQZ).

REV, avaliado no fechamento de cada hora (candle de 1h fechado):
  long : RSI14(1h) <= RSI_L, preço abaixo da EMA20(1h), caiu nas últimas 24h, volume da hora >= VOL× média 24h
  short: espelhado (RSI >= 100-RSI_L, acima da EMA20, subiu nas 24h, volume alto)
Variantes testadas: VOL 1,0 / 1,2 / 1,5 e RSI_L 45 / 40.

Saída: mesma simulação do painel (SL/TP em ATR de 15 min, máx. 4h, taxas da BingX).
Validação honesta: SL/TP escolhidos nos primeiros 75% do período; resultado medido nos últimos 25%.
Critério (definido antes): REV aprovado se no teste n >= 100, R médio > +0,05 e acima dos setups atuais.

Uso (GitHub Actions, workflow painel-rev): python painel/backtest_rev.py [--max N]
Grava relatorio_rev.txt na raiz.
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import nucleo as nc  # noqa: E402

DIAS = 90
VARIANTES = [("REV_v10_r45", 1.0, 45), ("REV_v12_r45", 1.2, 45), ("REV_v15_r45", 1.5, 45),
             ("REV_v12_r40", 1.2, 40), ("REV_v15_r40", 1.5, 40)]
SAIDA = os.path.join(os.path.dirname(__file__), "..", "relatorio_rev.txt")


def sinais_rev(df):
    """Sinais REV no df de 15 min já preparado (nc.preparar). Só nos candles que fecham a hora."""
    h = df.resample("1h", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    c = h["close"]
    f = pd.DataFrame({"rsi": nc.rsi(c), "abaixo": c < nc.ema(c, 20), "ret24": c / c.shift(24) - 1,
                      "vol": h["volume"] / h["volume"].rolling(24).mean().shift()}, index=h.index)
    f = f.iloc[30:]
    pos = df.index.get_indexer(f.index)          # candle de 15 min que fecha a hora
    ok = pos >= 0
    f, pos = f[ok], pos[ok]
    partes = []
    for nome, vmin, rl in VARIANTES:
        for lado in (1, -1):
            if lado == 1:
                m = (f["rsi"] <= rl) & f["abaixo"] & (f["ret24"] < 0)
            else:
                m = (f["rsi"] >= 100 - rl) & ~f["abaixo"] & (f["ret24"] > 0)
            m &= f["vol"] >= vmin
            idx = pos[m.to_numpy()]
            if len(idx):
                partes.append(pd.DataFrame({"i": idx, "setup": nome, "lado": lado}))
    if not partes:
        return pd.DataFrame(columns=["i", "setup", "lado", "alinhado", "regime", "tempo"])
    s = pd.concat(partes, ignore_index=True)
    s["alinhado"] = (df["tend"].to_numpy()[s["i"]] == s["lado"]).astype(int)
    s["regime"] = df["regime"].to_numpy()[s["i"]]
    s["tempo"] = df.index[s["i"]]
    return s


def processar(ativo):
    try:
        df = dados.historico(ativo, dias=DIAS)
        if df is None or len(df) < 600:
            return None
        df = nc.preparar(df)
        sig = pd.concat([nc.sinais(df), sinais_rev(df)], ignore_index=True)
        if sig.empty:
            return None
        partes = []
        for sl, tp in nc.GRADE_SAIDA:
            r = nc.simular(df, sig, sl, tp)
            r["sl"], r["tp"] = sl, tp
            partes.append(r)
        out = pd.concat(partes, ignore_index=True)
        out["ativo"], out["classe"] = ativo["simbolo"], ativo["classe"]
        return out.drop(columns="i")
    except Exception as e:
        print(f"falhou {ativo['simbolo']}: {e}", flush=True)
        return None


def num(x, casas=3):
    return f"{x:+.{casas}f}".replace(".", ",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    uni = sorted(dados.universo(), key=lambda x: -x["vol24h"])
    if a.max:
        uni = uni[: a.max]
    print(f"universo: {len(uni)} ativos", flush=True)
    res = [r for r in dados.em_paralelo(processar, uni, n=6) if r is not None]
    t = pd.concat(res, ignore_index=True).dropna(subset=["r"])
    corte = t["tempo"].min() + (t["tempo"].max() - t["tempo"].min()) * 0.75
    treino, teste = t[t["tempo"] < corte], t[t["tempo"] >= corte]

    # saída (SL/TP) escolhida só no treino, por classe+setup+lado
    g = treino.groupby(["classe", "setup", "lado", "sl", "tp"])["r"].agg(["size", "mean"]).reset_index()
    g = g[g["size"] >= 50].sort_values("mean", ascending=False).drop_duplicates(["classe", "setup", "lado"])
    esc = g.set_index(["classe", "setup", "lado"])[["sl", "tp"]]

    def aplicar(x):
        k = x.join(esc, on=["classe", "setup", "lado"], rsuffix="_esc")
        return k[(k["sl"] == k["sl_esc"]) & (k["tp"] == k["tp_esc"])]

    tr, te = aplicar(treino), aplicar(teste)
    L = [f"BACKTEST REV (reversão com volume) x setups do painel — BingX perps, 15 min, {DIAS} dias",
         f"Gerado {pd.Timestamp.now(tz='America/Sao_Paulo'):%d/%m/%Y %H:%M} | {t['ativo'].nunique()} ativos | "
         f"treino até {corte:%d/%m %H:%M} UTC, teste depois",
         "R = resultado por trade em múltiplos do stop, já com taxas. Acerto = TP antes do SL.", ""]
    linhas = []
    for (classe, setup, lado), x in te.groupby(["classe", "setup", "lado"]):
        y = tr[(tr["classe"] == classe) & (tr["setup"] == setup) & (tr["lado"] == lado)]
        e = esc.loc[(classe, setup, lado)]
        linhas.append((classe, setup, lado, e["sl"], e["tp"], len(y), y["r"].mean(), len(x), x["r"].mean(), x["ganhou"].mean()))
    df = pd.DataFrame(linhas, columns=["classe", "setup", "lado", "sl", "tp", "n_tr", "r_tr", "n_te", "r_te", "ac_te"])
    for classe, x in df.groupby("classe"):
        L.append(f"== {classe.upper()} ==")
        L.append(f"{'setup':12} {'lado':5} {'SL/TP':8} {'treino n':>8} {'R':>7} | {'teste n':>7} {'R':>7} {'acerto':>6}")
        for r in x.sort_values("r_te", ascending=False).itertuples():
            L.append(f"{r.setup:12} {'long' if r.lado == 1 else 'short':5} {str(r.sl)+'/'+str(r.tp):8} "
                     f"{r.n_tr:8} {num(r.r_tr):>7} | {r.n_te:7} {num(r.r_te):>7} {r.ac_te*100:5.0f}%")
        L.append("")

    L.append("== VEREDITO (teste, longs+shorts juntos) ==")
    atuais = te[te["setup"].isin(nc.SETUPS)]
    for classe in sorted(te["classe"].unique()):
        base = atuais[atuais["classe"] == classe]["r"]
        L.append(f"{classe}: setups atuais n={len(base)} R médio={num(base.mean()) if len(base) else '-'}")
        for nome, _, _ in VARIANTES:
            x = te[(te["classe"] == classe) & (te["setup"] == nome)]["r"]
            if not len(x):
                continue
            ok = len(x) >= 100 and x.mean() > 0.05 and (not len(base) or x.mean() > base.mean())
            L.append(f"   {nome:12} n={len(x):5} R médio={num(x.mean())} acerto="
                     f"{te[(te['classe'] == classe) & (te['setup'] == nome)]['ganhou'].mean()*100:.0f}% "
                     f"-> {'APROVADO' if ok else 'reprovado'}")
    L.append("")
    L.append("Backtest não garante resultado futuro.")
    texto = "\n".join(L).replace("nan", "-")
    print(texto)
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(texto + "\n")
    print(f"ok em {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
