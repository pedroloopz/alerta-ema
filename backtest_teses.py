"""
Teste de 5 teses classicas da literatura, com parametros de livro
(sem otimizacao), em 2 universos: todos os ativos e so os liquidos.

Premissas pessimistas iguais para todas:
  - custo real = 1,5 x custo estimado pela liquidez
  - entrada na abertura do candle seguinte ao sinal
  - maximo de 8 posicoes simultaneas de US$ 1.000 a 2x
  - aprovada so se lucrar no TREINO e na VALIDACAO
"""

import os

import numpy as np
import pandas as pd

from simbolos import SIMBOLOS
from alerta_ema import baixar_candles, custo_ida_volta, avisar

MAX_POS = 8
NOCIONAL = 2000.0              # US$ 1.000 a 2x
BANCA = 8000.0
ESTRESSE = 1.5
FRACAO_TREINO = 0.60
LIQ_MIN = 200_000.0            # universo "liquidos": volume 24h
REFERENCIA = "QQQUSDT"


# ------------------------------------------------------------ dados
def carregar():
    diario, h4 = {}, {}
    for s in SIMBOLOS:
        try:
            d = baixar_candles(s, "1d", 500)
            c = baixar_candles(s, "4h", 1500)
        except Exception as e:
            print(f"{s}: {e}")
            continue
        if len(d) >= 45:
            d = d.copy()
            d["dia"] = d["Inicio"].dt.normalize()
            diario[s] = d.set_index("dia")
        if len(c) >= 120:
            h4[s] = c.reset_index(drop=True)
    return diario, h4


def painel(diario, campo):
    return pd.DataFrame({s: d[campo] for s, d in diario.items()}).sort_index()


def atr_abs(h, l, c, periodo=20):
    ant = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - ant), np.abs(l - ant)])
    return pd.Series(tr).ewm(alpha=1 / periodo, adjust=False).mean().to_numpy()


# --------------------------------------------- teses semanais (diario)
def semanal(diario, tipo, so_liquidos):
    C, O, V = painel(diario, "Close"), painel(diario, "Open"), painel(diario, "VolumeUSD")
    datas = C.index
    ops = []

    for k in range(35, len(datas) - 7, 7):
        d_sinal, d_entra, d_sai = datas[k - 1], datas[k], datas[k + 7]
        janela_v = V.iloc[k - 7:k].median()

        candidatos = []
        for s in C.columns:
            hist = C[s].iloc[:k].dropna()
            if len(hist) < 30 or pd.isna(O[s].get(d_entra)) or pd.isna(O[s].get(d_sai)):
                continue
            liq = janela_v.get(s, np.nan)
            if pd.isna(liq) or (so_liquidos and liq < LIQ_MIN):
                continue

            if tipo == "tsmom":                          # tendencia do proprio ativo, 4 sem.
                r = hist.iloc[-1] / hist.iloc[-29] - 1
                vol = hist.pct_change().iloc[-28:].std() * np.sqrt(28)
                if not vol or np.isnan(vol):
                    continue
                candidatos.append((s, np.sign(r), abs(r) / vol, liq))
            elif tipo == "reversao":                     # perdedores da semana repicam
                r = hist.iloc[-1] / hist.iloc[-8] - 1
                candidatos.append((s, r, None, liq))
            elif tipo == "xsmom":                        # 4 semanas, pulando a ultima
                r = hist.iloc[-8] / hist.iloc[-29] - 1
                candidatos.append((s, r, None, liq))

        if len(candidatos) < 4:
            continue

        if tipo == "tsmom":
            escolhidos = sorted(candidatos, key=lambda x: x[2], reverse=True)[:MAX_POS]
            posicoes = [(s, lado, liq) for s, lado, _, liq in escolhidos if lado != 0]
        else:
            ordem = sorted(candidatos, key=lambda x: x[1])
            metade = min(MAX_POS // 2, len(ordem) // 2)
            baixo, alto = ordem[:metade], ordem[-metade:]
            if tipo == "reversao":
                posicoes = [(s, +1, l) for s, _, _, l in baixo] + [(s, -1, l) for s, _, _, l in alto]
            else:
                posicoes = [(s, -1, l) for s, _, _, l in baixo] + [(s, +1, l) for s, _, _, l in alto]

        for s, lado, liq in posicoes:
            bruto = (O[s][d_sai] / O[s][d_entra] - 1) * 100 * lado
            ops.append({"entrada": d_entra, "saida": d_sai, "simbolo": s,
                        "pess": bruto - ESTRESSE * custo_ida_volta(liq)})
    return ops


# ------------------------------------------ teses de evento (4 horas)
def evento(h4, diario, tipo, so_liquidos):
    todas = []
    for s, df in h4.items():
        o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
        t = df["Inicio"].to_numpy()
        liq24 = pd.Series(df["VolumeUSD"].to_numpy(float)).rolling(6, min_periods=1).sum().to_numpy()
        atr = atr_abs(h, l, c)
        n = len(c)

        if tipo == "cruzamento":
            e9 = pd.Series(c).ewm(span=9, adjust=False).mean().to_numpy()
            e21 = pd.Series(c).ewm(span=21, adjust=False).mean().to_numpy()
            if s in diario:
                dd = diario[s]
                d_acima = (dd["Close"] > dd["Close"].ewm(span=20, adjust=False).mean())
                dias = dd.index.to_numpy()
                idx = np.searchsorted(dias, df["Inicio"].dt.normalize().to_numpy(), side="left") - 1
                filtro = np.where(idx >= 20, d_acima.to_numpy()[np.clip(idx, 0, None)], np.nan)
            else:
                filtro = np.full(n, np.nan)

        i, livre_a_partir = 30, 30
        while i < n - 2:
            if i < livre_a_partir:
                i += 1
                continue
            lado = 0
            if tipo == "donchian":
                if c[i] > h[i - 20:i].max():
                    lado = +1
                elif c[i] < l[i - 20:i].min():
                    lado = -1
            else:
                cruzou_cima = e9[i] > e21[i] and e9[i - 1] <= e21[i - 1]
                cruzou_baixo = e9[i] < e21[i] and e9[i - 1] >= e21[i - 1]
                if cruzou_cima and filtro[i] == 1:
                    lado = +1
                elif cruzou_baixo and filtro[i] == 0:
                    lado = -1

            if lado == 0 or (so_liquidos and liq24[i] < LIQ_MIN):
                i += 1
                continue

            entrada = o[i + 1]
            stop = entrada - lado * 2 * atr[i]
            saida_px, j_saida = None, None
            for j in range(i + 1, min(i + 31, n)):
                if (lado == 1 and l[j] <= stop) or (lado == -1 and h[j] >= stop):
                    saida_px, j_saida = stop, j
                    break
                if j >= i + 11 and j + 1 < n:
                    if tipo == "donchian":
                        sair = (lado == 1 and c[j] < l[j - 10:j].min()) or \
                               (lado == -1 and c[j] > h[j - 10:j].max())
                    else:
                        sair = (lado == 1 and e9[j] < e21[j]) or (lado == -1 and e9[j] > e21[j])
                    if sair:
                        saida_px, j_saida = o[j + 1], j + 1
                        break
            if saida_px is None:
                j_saida = min(i + 30, n - 1)
                saida_px = c[j_saida]

            bruto = (saida_px / entrada - 1) * 100 * lado
            todas.append({"entrada": pd.Timestamp(t[i + 1]), "saida": pd.Timestamp(t[j_saida]),
                          "simbolo": s,
                          "pess": bruto - ESTRESSE * custo_ida_volta(liq24[i])})
            livre_a_partir = j_saida + 1
            i = j_saida + 1

    # Limite de 8 posicoes abertas ao mesmo tempo
    todas.sort(key=lambda x: x["entrada"])
    aceitas, abertas = [], []
    for op in todas:
        abertas = [x for x in abertas if x > op["entrada"]]
        if len(abertas) < MAX_POS:
            aceitas.append(op)
            abertas.append(op["saida"])
    return aceitas


# ------------------------------------------------------------ metricas
def metricas(ops):
    if not ops:
        return None
    df = pd.DataFrame(ops)
    df["usd"] = df["pess"] * NOCIONAL / 100
    saidas = pd.to_datetime(df["saida"], utc=True).dt.tz_convert(None)
    semana = df.groupby(saidas.dt.to_period("W"))["usd"].sum()
    curva = semana.cumsum()
    dd = float((curva.cummax().clip(lower=0) - curva).max()) if len(curva) else 0.0
    return {"n": len(df), "acerto": (df["pess"] > 0).mean() * 100,
            "media": df["pess"].mean(), "total_usd": df["usd"].sum(),
            "semanas": len(semana), "sem_pos": (semana > 0).mean() * 100 if len(semana) else 0,
            "pior_semana": semana.min() if len(semana) else 0, "drawdown": dd}


def _ts(x):
    t = pd.Timestamp(x)
    return t.tz_convert(None) if t.tzinfo else t


def dividir(ops, corte):
    return ([o for o in ops if _ts(o["entrada"]) < corte],
            [o for o in ops if _ts(o["entrada"]) >= corte])


def main():
    diario, h4 = carregar()
    print(f"diario: {len(diario)} | 4h: {len(h4)}")

    teses = [
        ("1", "Momentum semanal (tendencia 4 sem.)", lambda liq: semanal(diario, "tsmom", liq)),
        ("2", "Rompimento Donchian 20/10 (oposta)",  lambda liq: evento(h4, diario, "donchian", liq)),
        ("3", "Cruzamento EMA 9/21 + filtro diario", lambda liq: evento(h4, diario, "cruzamento", liq)),
        ("4", "Reversao semanal",                    lambda liq: semanal(diario, "reversao", liq)),
        ("5", "Momentum transversal 4 sem.",         lambda liq: semanal(diario, "xsmom", liq)),
    ]

    usd = lambda x: f"{x:+,.0f}".replace(",", ".")
    pc = lambda x: f"{x:+.2f}%".replace(".", ",")
    L = ["TESTE DE TESES - 5 x 2 universos",
         "custo x1,5 | entrada no candle seguinte",
         "max 8 posicoes de US$ 1.000 a 2x", ""]
    linhas_csv, aprovadas = [], 0

    for cod, nome, rodar in teses:
        L.append(f"[{cod}] {nome}")
        for rotulo, liq in (("todos", False), ("liquidos", True)):
            ops = rodar(liq)
            if not ops:
                L.append(f"  {rotulo}: sem operacoes")
                continue
            datas = sorted(_ts(o["entrada"]) for o in ops)
            corte = datas[0] + (datas[-1] - datas[0]) * FRACAO_TREINO
            tr, va = dividir(ops, corte)
            mt, mv = metricas(tr), metricas(va)
            ok = bool(mt and mv and mv["n"] >= 30 and mt["media"] > 0 and mv["media"] > 0)
            aprovadas += ok
            if mt and mv:
                L.append(f"  {rotulo}: treino {pc(mt['media'])} | valid {pc(mv['media'])} "
                         f"n={mv['n']}")
                L.append(f"    valid: sem+ {mv['sem_pos']:.0f}% | total US$ {usd(mv['total_usd'])} "
                         f"| DD US$ {mv['drawdown']:.0f} -> {'APROVADA' if ok else 'reprovada'}")
            linhas_csv.append({"tese": nome, "universo": rotulo, "aprovada": ok,
                               **{f"treino_{k}": v for k, v in (mt or {}).items()},
                               **{f"valid_{k}": v for k, v in (mv or {}).items()}})
        L.append("")

    # Referencia: segurar QQQ com a mesma exposicao total (8 x US$ 2.000)
    if REFERENCIA in diario:
        q = diario[REFERENCIA]["Close"]
        sem = q.resample("W").last().pct_change().dropna() * MAX_POS * NOCIONAL
        cut = int(len(sem) * FRACAO_TREINO)
        sv = sem.iloc[cut:]
        L.append("Referencia: segurar QQQ (mesma exposicao)")
        L.append(f"  valid: sem+ {(sv > 0).mean()*100:.0f}% | total US$ {usd(sv.sum())}")
        L.append("")

    L.append(f"APROVADAS: {aprovadas} de 10")
    pd.DataFrame(linhas_csv).to_csv("teses.csv", index=False)

    texto = "\n".join(L)
    print(texto)
    if os.environ.get("TELEGRAM_TOKEN"):
        avisar(texto[:3900])


if __name__ == "__main__":
    main()
