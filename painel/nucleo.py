"""Núcleo do painel de day trade: indicadores, setups, simulação de TP/SL e nota 1–10.

Funções puras (sem rede), usadas pela calibração diária e pelo scanner de 15 min.

Setups no candle de 15 min fechado (long e short espelhados):
  ROMP  rompimento do canal Donchian de 20 candles com volume ≥ 1,5× a média
  PULL  pullback a favor da tendência do 1h: RSI(14) volta a cruzar 40 (long) / 60 (short)
  EXA   exaustão: RSI < 25 (> 75) nos últimos 2 candles e candle de reversão com volume
  SQZ   compressão: Bollinger na menor largura de 100 candles e rompimento da banda
"alinhado" = o lado do trade bate com a tendência do 1h (EMA 20 x EMA 50).

Saída: SL e TP em múltiplos do ATR(14) de 15 min; o trade dura no máximo MAX_BARRAS
candles (4h). Se SL e TP caem no mesmo candle, conta SL (conservador). Taxas: taker
0,05% na entrada e na saída + 0,02% de slippage em cada ponta.
"""
import numpy as np
import pandas as pd

SETUPS = ("ROMP", "PULL", "EXA", "SQZ")
MAX_BARRAS = 16                 # 16 × 15 min = 4h
CUSTO = 2 * (0.0005 + 0.0002)   # ida e volta, fração do preço
# pares (SL, TP) em ATR testados na calibração; cada setup fica com o de melhor resultado
GRADE_SAIDA = ((1.0, 1.0), (1.0, 1.5), (1.5, 1.5), (1.5, 2.0), (2.0, 2.0), (2.0, 3.0))
K_ATIVO, K_HORA = 30.0, 150.0   # força do "puxão" para a média (encolhimento bayesiano)


# ------------------------------------------------------------------ indicadores
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def tendencia_1h(df):
    """+1 alta, -1 baixa, 0 indefinida — usando só candles de 1h já fechados."""
    h = df.resample("1h", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    e20, e50 = ema(h["close"], 20), ema(h["close"], 50)
    t = pd.Series(0, index=h.index)
    t[(e20 > e50) & (h["close"] > e20)] = 1
    t[(e20 < e50) & (h["close"] < e20)] = -1
    # o valor de uma hora só vale depois que ela fecha
    return t.reindex(df.index, method="ffill").fillna(0).astype(int)


def preparar(df):
    """df: índice de tempo (fechamento do candle, UTC), colunas open/high/low/close/volume."""
    df = df.copy()
    df["atr"] = atr(df)
    df["rsi"] = rsi(df["close"])
    df["vol_med"] = df["volume"].rolling(20).mean().shift()
    df["don_hi"] = df["high"].rolling(20).max().shift()
    df["don_lo"] = df["low"].rolling(20).min().shift()
    m = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()
    df["bb_hi"], df["bb_lo"] = m + 2 * sd, m - 2 * sd
    larg = (4 * sd / m)
    df["aperto"] = (larg <= larg.rolling(100).min() * 1.05).rolling(3).max().shift().fillna(0) > 0
    df["tend"] = tendencia_1h(df)
    return df


def sinais(df):
    """Devolve DataFrame com uma linha por sinal: tempo, setup, lado (+1/-1), alinhado."""
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    vivo = (v > 0) & (df["atr"] > 0) & df["vol_med"].notna() & (df["vol_med"] > 0)
    vol_forte = v >= 1.5 * df["vol_med"]
    r, r1 = df["rsi"], df["rsi"].shift()
    regras = {
        ("ROMP", 1): (c > df["don_hi"]) & vol_forte & (c > o),
        ("ROMP", -1): (c < df["don_lo"]) & vol_forte & (c < o),
        ("PULL", 1): (df["tend"] == 1) & (r1 < 40) & (r >= 40),
        ("PULL", -1): (df["tend"] == -1) & (r1 > 60) & (r <= 60),
        ("EXA", 1): (r.rolling(2).min().shift() < 25) & (c > h.shift()) & (v >= 1.3 * df["vol_med"]),
        ("EXA", -1): (r.rolling(2).max().shift() > 75) & (c < l.shift()) & (v >= 1.3 * df["vol_med"]),
        ("SQZ", 1): df["aperto"] & (c > df["bb_hi"]),
        ("SQZ", -1): df["aperto"] & (c < df["bb_lo"]),
    }
    partes = []
    for (setup, lado), m in regras.items():
        idx = np.flatnonzero((m & vivo).to_numpy())
        if len(idx):
            partes.append(pd.DataFrame({"i": idx, "setup": setup, "lado": lado}))
    if not partes:
        return pd.DataFrame(columns=["i", "setup", "lado", "alinhado", "tempo"])
    s = pd.concat(partes, ignore_index=True)
    s["alinhado"] = (df["tend"].to_numpy()[s["i"]] == s["lado"]).astype(int)
    s["tempo"] = df.index[s["i"]]
    return s.sort_values("i").reset_index(drop=True)


# ------------------------------------------------------------------ simulação
def simular(df, sig, sl_atr, tp_atr):
    """Para cada sinal: ganhou (TP antes do SL), R realizado (já com custos), duração."""
    hi, lo, cl, at = (df[k].to_numpy() for k in ("high", "low", "close", "atr"))
    n = len(df)
    ganhou, rr, ok = [], [], []
    for i, lado in zip(sig["i"].to_numpy(), sig["lado"].to_numpy()):
        ent, a = cl[i], at[i]
        if i + 1 >= n or not a or a != a:
            ganhou.append(0); rr.append(np.nan); ok.append(False); continue
        fim = min(n, i + 1 + MAX_BARRAS)
        if fim - (i + 1) < MAX_BARRAS:      # trade ainda não terminou no histórico
            ganhou.append(0); rr.append(np.nan); ok.append(False); continue
        sl = ent - lado * sl_atr * a
        tp = ent + lado * tp_atr * a
        sai, g = cl[fim - 1], 0
        for j in range(i + 1, fim):
            bate_sl = lo[j] <= sl if lado == 1 else hi[j] >= sl
            bate_tp = hi[j] >= tp if lado == 1 else lo[j] <= tp
            if bate_sl:
                sai = sl; break
            if bate_tp:
                sai, g = tp, 1; break
        risco = sl_atr * a / ent
        r = (lado * (sai - ent) / ent - CUSTO) / risco
        ganhou.append(g); rr.append(r); ok.append(True)
    out = sig.copy()
    out["ganhou"], out["r"], out["ok"] = ganhou, rr, ok
    return out[out["ok"]].drop(columns="ok")


# ------------------------------------------------------------------ nota
def nota_de(p, ev_r, n_base):
    """Probabilidade de bater TP antes do SL -> nota 1–10.
    0,50 = 5 · 0,58 = 7 · 0,62 = 8 · 0,66 = 9 · 0,70 = 10. Sem lucro esperado, no máximo 6."""
    s = 5 + (p - 0.50) / 0.04
    if ev_r <= 0:
        s = min(s, 6)
    if n_base < 50:
        s = min(s, 7)
    return int(np.clip(np.floor(s + 1e-9), 1, 10))


def estimar(stats, classe, ativo, setup, lado, alinhado, hora):
    """Probabilidade e R esperado encolhidos: geral do setup -> ativo -> hora do dia."""
    chave = f"{classe}|{setup}|{lado}|{alinhado}"
    base = stats["base"].get(chave)
    if not base:
        return None
    n0, p0, r0 = base["n"], base["g"] / base["n"], base["r"] / base["n"]
    a = stats["ativo"].get(f"{ativo}|{setup}|{lado}", {"n": 0, "g": 0, "r": 0.0})
    p = (a["g"] + K_ATIVO * p0) / (a["n"] + K_ATIVO)
    ev = (a["r"] + K_ATIVO * r0) / (a["n"] + K_ATIVO)
    hc = stats["hora"].get(f"{classe}|{hora}")
    pc = stats["classe"][classe]
    if hc:
        dp = (hc["g"] + K_HORA * pc["p"]) / (hc["n"] + K_HORA) - pc["p"]
        dr = (hc["r"] + K_HORA * pc["r"]) / (hc["n"] + K_HORA) - pc["r"]
        p, ev = p + dp, ev + dr
    return {"p": float(np.clip(p, 0.01, 0.99)), "ev_r": float(ev), "n_ativo": a["n"], "n_base": n0}


def alavancagem(sl_pct, perda_margem=0.25):
    """Alavancagem para o stop custar ~25% da margem, entre 5x e 25x."""
    if sl_pct <= 0:
        return 5
    return int(np.clip(np.floor(perda_margem / sl_pct), 5, 25))
