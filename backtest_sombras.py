"""Backtest de 2 anos das sombras de swing (estrategias_swing.py) e do "trade original arrumado" (gatilho do robô
na vela de 4h com stop de 2,5 ATR e alvo 3R, a favor do semanal), em cripto e ações, com janelas de 2 meses.
Mesma régua de custos: 0,1% por lado + funding 0,03%/dia. Resultado: relatorio_swing_sombras.txt"""
import datetime

import numpy as np
import pandas as pd

import backtest_entradas as B
import estrategias_swing as E
import regras_robo as RR

NEOS = ["CRWV", "NBIS", "IREN", "APLD", "CIFR", "WULF", "HUT", "CORZ", "GLXY", "BTDR", "NVDA", "AMD", "AVGO"]
LADOS_4H = {"LONG": ["recuo", "rompimento", "squeeze", "varrida", "nr7"],
            "SHORT": ["recuo", "rompimento", "squeeze", "varrida", "nr7"]}


def para_lista(df):
    df = df.copy()
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    t = df.index.as_unit("ms").asi8.astype(np.int64)
    return [{"time": int(a), "open": float(o), "high": float(h), "low": float(l), "close": float(c),
             "volume": float(vv)} for a, o, h, l, c, vv in zip(t, df["open"], df["high"], df["low"], df["close"],
                                                              df["volume"])]


def rodar(dias_por_ativo, classe, nome, sinal, inicio_ms):
    trades = []
    for ativo, v in dias_por_ativo.items():
        aberto = None
        for i in range(150, len(v)):
            janela = v[: i + 1]
            if aberto:
                r = E.passo(aberto, janela)
                if r is not None:
                    trades.append({"ativo": ativo, "classe": classe, "lado": aberto["lado"], "r": r, "risco": aberto["risco"],
                                   "t": pd.Timestamp(aberto["t"], unit="ms", tz="UTC"),
                                   "saida": pd.Timestamp(v[i]["time"], unit="ms", tz="UTC")})
                    aberto = None
                continue
            if v[i]["time"] < inicio_ms:
                continue
            reg = E.regime(janela)
            p = sinal(janela, reg)
            if p:
                p["t"] = v[i]["time"]
                aberto = p
    return pd.DataFrame(trades)


def para_4h(df):
    df = df.copy()
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                  "volume": "sum"}).dropna()


def original_4h(h1, dias, classe, inicio_ms, filtro=True):
    trades = []
    for ativo, df in h1.items():
        if ativo not in dias:
            continue
        v = para_lista(para_4h(df))
        d = dias[ativo]
        di, aberto = 0, None
        for i in range(220, len(v)):
            if aberto:
                r = E.passo(aberto, v[: i + 1], funding_dia=0.0003 / 6)
                if r is not None:
                    trades.append({"ativo": ativo, "classe": classe, "lado": aberto["lado"], "r": r, "risco": aberto["risco"],
                                   "t": pd.Timestamp(aberto["t"], unit="ms", tz="UTC"),
                                   "saida": pd.Timestamp(v[i]["time"], unit="ms", tz="UTC")})
                    aberto = None
                continue
            if v[i]["time"] < inicio_ms:
                continue
            while di + 1 < len(d) and d[di + 1]["time"] + E.DIA_MS <= v[i]["time"]:
                di += 1
            reg = E.regime(d[: di + 1]) if filtro else None
            lados = LADOS_4H if not filtro else (
                {"LONG": LADOS_4H["LONG"]} if reg in ("alta", "alta forte") else
                {"SHORT": LADOS_4H["SHORT"]} if reg in ("baixa", "baixa forte") else {})
            if not lados:
                continue
            a = E.atr(v[: i + 1])
            sl = 2.5 * a / v[i]["close"]
            r = RR.gatilho(v[i - 219: i + 1], lados, True, sl, 1.0)
            if r:
                p = E._plano(r["lado"], v[i]["close"], v[i]["close"] * (1 - (1 if r["lado"] == "LONG" else -1) * sl),
                             3.0, 30, "original_4h")
                if p:
                    p["t"] = v[i]["time"]
                    aberto = p
    return pd.DataFrame(trades)


def janelas(t):
    if t is None or t.empty:
        return None
    t = t.sort_values("saida")
    ini, fim, d, vals = t.saida.iloc[0], t.saida.iloc[-1], t.saida.iloc[0], []
    while d + pd.Timedelta(days=61) <= fim:
        vals.append(t.loc[(t.saida >= d) & (t.saida < d + pd.Timedelta(days=61)), "r"].sum())
        d += pd.Timedelta(days=7)
    return np.array(vals)


def relatar(nome, t, semanas):
    B.resumo_trades(t, nome, semanas)
    v = janelas(t)
    if v is not None and len(v):
        B.p(f"   janelas de 2 meses: {100 * (v > 0).mean():.0f}% positivas · mediana {np.median(v):+.1f}R · "
            f"pior {v.min():+.1f}R · melhor {v.max():+.1f}R")
        # quanto da banca ficaria em uso com 1% de risco por trade (nocional = 1% ÷ distância do stop)
        dias = pd.date_range(t.t.min().normalize(), t.saida.max().normalize(), freq="D")
        ab = np.array([((t.t <= d) & (t.saida > d)).sum() for d in dias])
        exp = np.array([(0.01 / t.risco[(t.t <= d) & (t.saida > d)]).sum() for d in dias])
        B.p(f"   em média {ab.mean():.1f} posições abertas (máx {ab.max()}) · com 1% de risco por trade, nocional "
            f"médio {100 * exp.mean():.0f}% da banca (máx {100 * exp.max():.0f}%) · stop médio {100 * t.risco.mean():.1f}%")
        ult = t[t.saida >= t.saida.max() - pd.Timedelta(days=61)]
        B.p(f"   últimos 2 meses: {len(ult)} trades · {ult.r.sum():+.1f}R")


def main():
    agora = datetime.datetime.now(datetime.timezone.utc)
    inicio = agora - datetime.timedelta(days=730)
    inicio_ms = int(inicio.timestamp() * 1000)
    desde_d = int((agora - datetime.timedelta(days=730 + 220)).timestamp() * 1000)
    desde_h = int((agora - datetime.timedelta(days=740)).timestamp() * 1000)
    B.p(f"# Sombras de swing e trade original arrumado — {agora:%d/%m/%Y %H:%M} UTC")
    B.p("Custos: 0,1% por lado + funding 0,03%/dia. R = resultado ÷ risco do stop. 2 anos de trades "
        "(com ~7 meses antes para o semanal).")
    pares = B.binance_top(60)
    cd, ch = {}, {}
    for s in pares:
        d = B.binance_velas(s, "1d", desde_d)
        if d is not None and len(d) > 300:
            cd[s[:-4]] = para_lista(d)
        h = B.binance_velas(s, "1h", desde_h)
        if h is not None and len(h) > 24 * 200:
            ch[s[:-4]] = h
    tick = sorted({t for v in B.ACOES.values() for t in v})
    ad = {k: para_lista(v) for k, v in B.yahoo(tick, "1d", "3y").items()}
    ah = B.yahoo(tick, "1h", "730d")
    B.p(f"Dados: {len(cd)} criptos · {len(ad)} ações/ETFs (diário) · 1h: {len(ch)} criptos, {len(ah)} ações")
    reg_btc = E.regime(cd["BTC"]) if "BTC" in cd else None
    B.p(f"Semanal hoje: BTC {reg_btc} · QQQ {E.regime(ad['QQQ']) if 'QQQ' in ad else '—'} · "
        + " · ".join(f"{k} {E.regime(ad[k])}" for k in ("CRWV", "NBIS", "IREN", "NVDA") if k in ad))
    sem = 730 / 7

    B.p("\n## Donchian swing (rompimento de 20 dias)")
    for nome, kw in (("filtro semanal · stop 2,5 ATR · alvo 3R", {}),
                     ("filtro semanal · stop 2 ATR · alvo 2R", {"stop_atr": 2.0, "alvo_r": 2.0}),
                     ("filtro semanal · stop 3 ATR · só saída por fundo de 10d", {"stop_atr": 3.0, "alvo_r": None}),
                     ("SEM filtro semanal · stop 2,5 ATR · alvo 3R", {"filtro": False})):
        sig = (lambda kw: (lambda v, reg: E.donchian(v, reg, **kw)))(kw)
        tc, ta = rodar(cd, "cripto", nome, sig, inicio_ms), rodar(ad, "ações", nome, sig, inicio_ms)
        relatar(f"cripto · {nome}", tc, sem)
        relatar(f"ações · {nome}", ta, sem)

    B.p("\n## Comprar queda em cripto com o semanal em alta forte")
    for nome, regs in (("só semanal em alta forte", ("alta forte",)), ("semanal em alta (forte ou não)", ("alta", "alta forte")),
                       ("sem filtro (qualquer semanal)", ())):
        sig = (lambda regs: (lambda v, reg: E.repique_forte(v, reg, regimes=regs)))(regs)
        relatar(f"cripto · {nome}", rodar(cd, "cripto", nome, sig, inicio_ms), sem)

    B.p("\n## Virada em ações depois de queda de 20%+ (neoclouds e chips; e todas as ações)")
    sig = lambda v, reg: E.virada(v)
    relatar("neoclouds + chips", rodar({k: v for k, v in ad.items() if k in NEOS}, "ações", "virada", sig, inicio_ms), sem)
    relatar("todas as ações/ETFs", rodar(ad, "ações", "virada", sig, inicio_ms), sem)

    B.p("\n## Trade original arrumado: gatilho do robô em 4h, stop 2,5 ATR, alvo 3R, até 5 dias")
    for filtro in (True, False):
        nm = "a favor do semanal" if filtro else "sem filtro semanal"
        relatar(f"cripto · {nm}", original_4h(ch, cd, "cripto", inicio_ms, filtro), sem)
        relatar(f"ações · {nm}", original_4h(ah, ad, "ações", inicio_ms, filtro), sem)
    open("relatorio_swing_sombras.txt", "w", encoding="utf-8").write("\n".join(B.OUT) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        B.p("FALHOU:\n" + traceback.format_exc()[-3000:])
        open("relatorio_swing_sombras.txt", "w", encoding="utf-8").write("\n".join(B.OUT) + "\n")
        raise
