"""Backtest de 2 anos das regras ATUAIS do robô da BingX (gatilho da tese) com a mesma régua de custos do
backtest_entradas.py, e janelas de 2 meses (o horizonte do Pedro). Só leitura: não opera nada.

Gatilho: cópia exata de robo-bingx/tese.py (regras_robo.py). Lados e perfis como no tese.json de 19/10/2026.
Saída aproximada do robô:
  normal: stop no fechamento de 1h a −3% (emergência −10% dentro da vela), alvos 1,5/2/2,5/3% (25/25/20/10%),
          degraus de 1% (a cada +1%, o stop sobe 1%), saída em 12h se nunca chegou a +1%. Risco = 3%.
  diário: stop no fechamento diário a −10% (emergência −15%), mesmos alvos e degraus, saída em 72h. Risco = 5%.
Não entram (sem histórico): filtros de OI, funding, liquidez, VWAP, força relativa, setor, liquidações (repique em
cripto), entrada em 2 tempos, stops parciais e DCA. Resultado: relatorio_robo.txt."""
import datetime

import numpy as np
import pandas as pd

import backtest_entradas as B
import regras_robo as RR

ALVOS = [(0.015, 0.25), (0.02, 0.25), (0.025, 0.20), (0.03, 0.10)]
PERFIS = {"normal": {"sl": 0.03, "emerg": 0.10, "tempo": 12, "risco": 0.03, "diario": False},
          "diario": {"sl": 0.10, "emerg": 0.15, "tempo": 72, "risco": 0.05, "diario": True}}
GRUPOS = {  # nome: (lados, perfil, atr_max, media_tendencia)
    "neoclouds": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "diario", 0.03, {"LONG": 50}),
    "petróleo": ({"SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
    "top 20 EUA": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"], "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
    "defesa": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"], "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
    "energia elétrica": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"], "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
    "índices": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"], "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
    "commodities": ({"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"], "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}, "normal", 0.01, None),
}
CRIPTO_LADOS = {"SHORT": ["rompimento", "squeeze", "varrida", "nr7"], "LONG": ["repique"]}


def rsi2(c):
    d = c.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    rs = up.ewm(alpha=0.5, adjust=False).mean() / dn.ewm(alpha=0.5, adjust=False).mean()
    return 100 - 100 / (1 + rs)


def candidatos(df):
    """Pré-filtro folgado (o gatilho de verdade confirma): rompimento de 20 velas ou RSI(2) extremo."""
    c, h, lo = df["close"], df["high"], df["low"]
    r = rsi2(c).shift()
    m = (c > h.shift().rolling(20).max()) | (c < lo.shift().rolling(20).min()) | (r < 25) | (r > 75)
    return m.fillna(False).values


def simular(df, i, lado, pf):
    o, h, lo, c = (df[k].values for k in ("open", "high", "low", "close"))
    horas_utc = df.index.hour
    P = PERFIS[pf]
    s = 1 if lado == "LONG" else -1
    ent = c[i] * (1 + s * B.CUSTO_LADO)
    emerg = ent * (1 - s * P["emerg"])
    stop = None                                   # stop de degrau (dentro da vela), depois do 1º degrau
    resto, ganho, alvo_i, degrau, j = 1.0, 0.0, 0, 0, i

    def sai(px, fatia):
        return fatia * s * (px * (1 - s * B.CUSTO_LADO) / ent - 1)

    limite = min(len(c) - 1, i + 24 * 30)
    for j in range(i + 1, limite + 1):
        nivel = stop if stop is not None else emerg
        if (s == 1 and lo[j] <= nivel) or (s == -1 and h[j] >= nivel):
            px = o[j] if (s == 1 and o[j] < nivel) or (s == -1 and o[j] > nivel) else nivel
            ganho += sai(px, resto)
            resto = 0
            break
        while alvo_i < len(ALVOS):
            dist, fatia = ALVOS[alvo_i]
            alvo = ent * (1 + s * dist)
            if (s == 1 and h[j] >= alvo) or (s == -1 and lo[j] <= alvo):
                ganho += sai(alvo, fatia)
                resto -= fatia
                alvo_i += 1
            else:
                break
        melhor = h[j] if s == 1 else lo[j]
        n = int(s * (melhor / ent - 1) / 0.01 + 1e-9)
        if n >= 1 and n > degrau:
            degrau = n
            stop = ent * (1 + s * ((n - 1) * 0.01 + 0.001))
        if resto <= 1e-9:
            break
        if degrau == 0:                           # ainda sem lucro: stop no fechamento e saída por tempo
            fecha = (horas_utc[j] == 23) if P["diario"] else True
            if fecha and s * (c[j] / ent - 1) <= -P["sl"]:
                ganho += sai(c[j], resto)
                resto = 0
                break
            if j - i >= P["tempo"]:
                ganho += sai(c[j], resto)
                resto = 0
                break
    if resto > 1e-9:
        ganho += sai(c[j], resto)
    ganho -= B.FUNDING_8H * (j - i) / 8
    return ganho / P["risco"], j


def velas_dict(df):
    t = df.index.as_unit("ms").asi8.astype(np.int64)
    return [{"time": int(a), "open": o, "high": h, "low": l, "close": c, "volume": v}
            for a, o, h, l, c, v in zip(t, df["open"].values, df["high"].values, df["low"].values,
                                        df["close"].values, df["volume"].values)]


def backtest(dados, regras, classe):
    trades = []
    for nome, df in dados.items():
        if nome not in regras or len(df) < 260:
            continue
        lados, pf, atr_max, mt = regras[nome]
        df = df.copy()
        df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
        vd = velas_dict(df)
        cand = candidatos(df)
        livre = 0
        for i in range(220, len(df) - 1):
            if i < livre or not cand[i]:
                continue
            r = RR.gatilho(vd[i - 219:i + 1], lados, True, 0.03, atr_max, mt)
            if not r:
                continue
            res, j = simular(df, i, r["lado"], pf)
            trades.append({"ativo": nome, "classe": classe, "grupo": regras[nome][1], "lado": r["lado"],
                           "setup": r["setup"], "t": df.index[i], "saida": df.index[j], "r": res})
            livre = j + 1
    return pd.DataFrame(trades)


def janelas_2m(t, col_data="saida", col_r="r"):
    """Soma do R em janelas de 61 dias, andando de 7 em 7 dias."""
    if t.empty:
        return None
    t = t.sort_values(col_data)
    ini, fim = t[col_data].iloc[0], t[col_data].iloc[-1]
    vals, d = [], ini
    while d + pd.Timedelta(days=61) <= fim:
        m = (t[col_data] >= d) & (t[col_data] < d + pd.Timedelta(days=61))
        vals.append(t.loc[m, col_r].sum())
        d += pd.Timedelta(days=7)
    return np.array(vals)


def linha_janelas(nome, v, unidade="R"):
    if v is None or not len(v):
        B.p(f"{nome}: sem janelas")
        return
    B.p(f"{nome}: {len(v)} janelas de 2 meses · positivas {100 * (v > 0).mean():.0f}% · mediana {np.median(v):+.1f}{unidade} · "
        f"pior {v.min():+.1f}{unidade} · melhor {v.max():+.1f}{unidade}")


def main():
    agora = datetime.datetime.now(datetime.timezone.utc)
    desde = int((agora - datetime.timedelta(days=740)).timestamp() * 1000)
    B.p(f"# Backtest das regras atuais do robô — {agora:%d/%m/%Y %H:%M} UTC")
    B.p(f"Custos: {100 * B.CUSTO_LADO:.1f}% por lado + funding {100 * B.FUNDING_8H:.2f}%/8h. R = resultado ÷ risco "
        "(3% normal, 5% perfil diário). Janela de 2 meses = soma do R de 61 dias, andando de semana em semana.")
    pares = B.binance_top(60)
    cripto, cripto_1d = {}, {}
    for s in pares:
        d1 = B.binance_velas(s, "1h", desde)
        if d1 is not None and len(d1) > 24 * 120:
            cripto[s[:-4]] = d1
        dd = B.binance_velas(s, "1d", desde)
        if dd is not None and len(dd) > 120:
            cripto_1d[s[:-4]] = dd
    top10 = [s[:-4] for s in pares[:10]]
    reg_c = {k: (CRIPTO_LADOS, "normal" if k in top10 else "diario", 0.012 if k in top10 else 0.03, None)
             for k in cripto}
    tick = {t: g for g, ts in B.ACOES.items() for t in ts}
    acoes = B.yahoo(sorted(tick), "1h", "730d")
    reg_a = {t: GRUPOS[g] for t, g in tick.items() if g in GRUPOS}
    B.p(f"Dados: {len(cripto)} criptos e {len(acoes)} ações/ETFs (1h).")

    tc = backtest(cripto, reg_c, "cripto")
    ta = backtest(acoes, reg_a, "ações")
    tudo = pd.concat([tc, ta], ignore_index=True)

    def semanas(t):
        return max((t["t"].max() - t["t"].min()).days / 7, 1) if not t.empty else 1

    B.p("\n## Regras atuais do robô (2 anos)")
    for nome, t in (("tudo", tudo), ("cripto", tc), ("ações", ta)):
        B.resumo_trades(t, nome, semanas(tudo))
    for (classe, lado, setup), x in tudo.groupby(["classe", "lado", "setup"]):
        B.resumo_trades(x, f"   {classe} · {lado.lower()} · {setup}", semanas(tudo))
    B.por_semestre(tudo)
    tudo.to_csv("robo_trades.csv", index=False)

    B.p("\n## Janelas de 2 meses")
    linha_janelas("Robô atual (tudo)", janelas_2m(tudo))
    linha_janelas("Robô atual (cripto)", janelas_2m(tc))
    linha_janelas("Robô atual (ações)", janelas_2m(ta))
    ult = tudo[tudo["saida"] >= tudo["saida"].max() - pd.Timedelta(days=61)]
    B.resumo_trades(ult, "Últimos 2 meses", 61 / 7)

    r = B.backtest_forca(cripto_1d, 7, 7, 3, serie=True)
    if r is not None and len(r):
        f = pd.DataFrame({"saida": r.index, "r": 100 * r.values})
        linha_janelas("Força relativa cripto 7d/7d (soma % por semana)", janelas_2m(f), "%")
    open("relatorio_robo.txt", "w", encoding="utf-8").write("\n".join(B.OUT) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        B.p("FALHOU:\n" + traceback.format_exc()[-3000:])
        open("relatorio_robo.txt", "w", encoding="utf-8").write("\n".join(B.OUT) + "\n")
        raise
