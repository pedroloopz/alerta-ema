"""EXA long (exaustão, RSI < 25) em cripto com cada par de SL/TP da grade do painel.

Mostra, por par: trades, chance de TP, R médio (90 dias), R ponderado (meia-vida da calibração),
R no teste (últimos 25%) e a nota que o painel daria com o ponderado.
Uso (GitHub Actions, workflow painel-rev com script backtest_exa.py). Grava relatorio_exa.txt.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import dados  # noqa: E402
import nucleo as nc  # noqa: E402

DIAS, MEIA_VIDA = 90, 21   # iguais aos de calibrar.py
SAIDA = os.path.join(os.path.dirname(__file__), "..", "relatorio_exa.txt")


def processar(ativo):
    try:
        df = dados.historico(ativo, dias=DIAS)
        if df is None or len(df) < 600:
            return None
        df = nc.preparar(df)
        sig = nc.sinais(df)
        sig = sig[(sig["setup"] == "EXA") & (sig["lado"] == 1)]
        if sig.empty:
            return None
        partes = []
        for sl, tp in nc.GRADE_SAIDA:
            r = nc.simular(df, sig, sl, tp)
            r["sl"], r["tp"] = sl, tp
            partes.append(r)
        return pd.concat(partes, ignore_index=True)
    except Exception as e:
        print(f"falhou {ativo['simbolo']}: {e}", flush=True)
        return None


def main():
    uni = [a for a in dados.universo() if a["classe"] == "cripto"]
    t = pd.concat([r for r in dados.em_paralelo(processar, uni, n=6) if r is not None], ignore_index=True)
    t = t.dropna(subset=["r"])
    fim = t["tempo"].max()
    t["w"] = 0.5 ** ((fim - t["tempo"]).dt.total_seconds() / 86400 / MEIA_VIDA)
    corte = t["tempo"].min() + (fim - t["tempo"].min()) * 0.75
    L = [f"EXA long (cripto) x pares de SL/TP — {t['tempo'].min():%d/%m} a {fim:%d/%m/%Y}, teste depois de {corte:%d/%m}",
         "SL/TP em ATR de 15 min; R já com taxas; 'pond.' = meia-vida de 21 dias (o que a nota usa).", "",
         f"{'SL/TP':8} {'n':>5} {'chance TP':>9} {'R 90d':>7} {'chance pond.':>12} {'R pond.':>8} {'R teste':>8} {'nota':>5}"]
    for (sl, tp), x in t.groupby(["sl", "tp"]):
        w = x["w"].sum()
        p, r = (x["w"] * x["ganhou"]).sum() / w, (x["w"] * x["r"]).sum() / w
        te = x[x["tempo"] >= corte]["r"]
        L.append(f"{f'{sl}/{tp}':8} {len(x):5} {x['ganhou'].mean()*100:8.0f}% {x['r'].mean():+7.3f} "
                 f"{p*100:11.0f}% {r:+8.3f} {te.mean():+8.3f} {nc.nota_de(p, r, w):5}")
    L += ["", "Backtest não garante resultado futuro."]
    texto = "\n".join(L[:4] + [x.replace(".", ",") for x in L[4:-2]] + L[-2:])
    print(texto)
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(texto + "\n")


if __name__ == "__main__":
    main()
