"""
Backtest v2 — ONDO, ASTER e PENDLE (perpétuos BingX), gatilho Donchian no 1h,
filtro de tendência no 4h, long e short, margem ISOLADA de US$ 100 a 10x por ordem.

Stop e alvo maleáveis (mudam com o mercado):
  - distância medida em ATR (volatilidade da hora): mercado agitado = stop e alvo mais largos
  - força da tendência no 4h (distância EMA21–EMA55) comparada à mediana dos últimos 30 dias:
      tendência forte  -> stop 2,5 ATR e SEM alvo fixo (deixa correr no stop móvel)
      tendência normal -> stop 2 ATR e alvo em 3 ATR
  - DCA: alvo = 1 ATR (entre 0,5% e 2%); martingale: alvo = 2 ATR (entre 1% e 4%)

Variantes:
  A  Entrada única      — US$ 1.000 de posição, stop maleável (máx. 3%), stop móvel
  B  Piramidação        — US$ 500 no rompimento + US$ 500 se andar +1 ATR a favor
  C  DCA de futuros     — 3 ordens de US$ 1.000 (0, −1 ATR, −2 ATR), alvo maleável sobre o
                          preço médio, stop do ciclo a 3 ATR da 1ª entrada
  D  Martingale         — dobra a cada −5% (100/200/400 de margem), alvo maleável, sem stop

Dados: candles de 1h dos perpétuos da BingX; se a BingX não responder ao GitHub, usa o spot
da Binance (data-api.binance.vision), que acompanha o preço do perpétuo de perto.

Uso (precisa de internet — rode no GitHub Actions):
    python backtest_bingx.py
    python backtest_bingx.py --symbols ONDO-USDT,PENDLE-USDT
    python backtest_bingx.py --synthetic   # só para testar o código, dados falsos

Grava relatorio_bingx.txt e manda um resumo simples no Telegram se TELEGRAM_TOKEN e
TELEGRAM_CHAT_ID existirem. Usa só dados públicos (sem chave de API).
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

BASE = "https://open-api.bingx.com"

P = dict(
    capital=1000.0,
    margin=100.0, lev=10.0,          # US$ 100 × 10 = US$ 1.000 por ordem
    taker=0.0005, maker=0.0002,      # taxas BingX perpétuos
    slip=0.0005,                     # slippage estimado em ordens a mercado/stop
    funding_8h=0.0001,               # 0,01% a cada 8h, cobrado nos dois lados (conservador)
    maint=0.005,                     # margem de manutenção aproximada
    ema_fast=21, ema_slow=55, donchian=20, atr_len=14,
    max_hold=96,                     # horas (4 dias)
    a_atr=2.0, a_atr_forte=2.5, a_tp_atr=3.0, a_max_stop=0.03,
    b_add_atr=1.0,
    c_step_atr=1.0, c_orders=3, c_tp_atr=1.0, c_tp_min=0.005, c_tp_max=0.02,
    c_stop_atr=3.0, c_max_stop=0.06,
    d_step=0.05, d_tp_atr=2.0, d_tp_min=0.01, d_tp_max=0.04, d_max_margin=1000.0,
)
LIQ_MOVE = 1 / P["lev"] - P["maint"]   # ~9,5% contra o preço médio liquida (isolada)


# ------------------------------------------------------------------ dados
def _get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "backtest/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _rows(data):
    out = []
    for k in data:
        if isinstance(k, dict):
            out.append((int(k["time"]), float(k["open"]), float(k["high"]),
                        float(k["low"]), float(k["close"])))
        else:
            out.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])))
    return out


VISION = "https://data-api.binance.vision/api/v3/klines"


def fetch_vision(symbol):
    par = symbol.replace("-", "")
    out, ini = [], int(pd.Timestamp("2023-01-01", tz="UTC").timestamp() * 1000)
    while True:
        req = urllib.request.Request(VISION + "?" + urllib.parse.urlencode(
            {"symbol": par, "interval": "1h", "startTime": ini, "limit": 1000}),
            headers={"User-Agent": "backtest/2.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            k = json.loads(r.read().decode())
        if not k:
            break
        out += k
        ini = k[-1][0] + 3_600_000
        if len(k) < 1000:
            break
    if not out:
        raise RuntimeError(f"{symbol} sem dados na Binance")
    df = pd.DataFrame([r[:5] for r in out], columns=["time", "open", "high", "low", "close"])
    df = df.astype({"open": float, "high": float, "low": float, "close": float})
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.set_index("time")
    df = df[~df.index.duplicated()]
    return df[df.index < pd.Timestamp.now(tz="UTC").floor("h")]


FONTE = {}


def fetch_1h(symbol, fonte="binance"):
    """Binance spot tem o histórico completo de 1h; a API da BingX só devolve
    os últimos ~2 meses. O preço do spot acompanha o do perpétuo de perto."""
    ordem = [("binance", fetch_vision, "Binance spot"), ("bingx", fetch_bingx, "BingX perpétuo")]
    if fonte == "bingx":
        ordem.reverse()
    for _, fn, nome in ordem:
        try:
            df = fn(symbol)
            FONTE[symbol] = nome
            return df
        except Exception as e:
            print(f"{nome} falhou para {symbol}: {e}")
    raise RuntimeError(f"Sem dados para {symbol}")


def fetch_bingx(symbol):
    path = None
    for p in ("/openApi/swap/v3/quote/klines", "/openApi/swap/v2/quote/klines"):
        try:
            js = _get(p, {"symbol": symbol, "interval": "1h", "limit": 5})
            if js.get("code", 0) == 0 and js.get("data"):
                path = p
                break
        except Exception:
            pass
    if not path:
        raise RuntimeError(f"Não consegui baixar candles de {symbol} na BingX")
    out, end = {}, int(time.time() * 1000)
    for _ in range(300):
        js = _get(path, {"symbol": symbol, "interval": "1h", "limit": 1000, "endTime": end})
        rows = [r for r in _rows(js.get("data") or []) if r[0] not in out]
        if not rows:
            break
        for r in rows:
            out[r[0]] = r
        end = min(r[0] for r in rows) - 1
        time.sleep(0.25)
    df = pd.DataFrame(sorted(out.values()), columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.set_index("time")
    df = df[~df.index.duplicated()]
    # descarta a vela ainda aberta
    return df[df.index < pd.Timestamp.now(tz="UTC").floor("h")]


def synthetic(seed, n=24 * 420):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([0.0007, -0.0007, 0.0], size=n // 250 + 1), 250)[:n]
    ret = drift * 0.6 + rng.standard_t(4, n) * 0.006
    close = np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    idx = pd.date_range("2025-03-03", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


# ------------------------------------------------------------- indicadores
def _atr(df, n):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def prepare(df):
    h4 = df.resample("4h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    ef = h4["close"].ewm(span=P["ema_fast"], adjust=False).mean()
    es = h4["close"].ewm(span=P["ema_slow"], adjust=False).mean()
    h4["forca"] = (ef - es).abs() / es
    # forte = acima da mediana dos últimos 30 dias (180 candles de 4h)
    h4["forte"] = h4["forca"] > h4["forca"].rolling(180, min_periods=30).median()
    h4["up"] = (h4["close"] > es) & (ef > es)
    h4["down"] = (h4["close"] < es) & (ef < es)
    h4["exit_long"] = h4["close"] < es
    h4["exit_short"] = h4["close"] > es
    # um candle de 4h só é conhecido depois de fechar
    h4.index = h4.index + pd.Timedelta(hours=4)
    reg = h4[["up", "down", "exit_long", "exit_short", "forte"]].astype(float)
    d = df.copy()
    # vela de 1h que FECHA no instante t usa o 4h fechado até t (index da vela = abertura)
    d = d.join(reg.reindex(d.index + pd.Timedelta(hours=1), method="ffill")
               .set_axis(d.index))
    d["atr"] = _atr(df, P["atr_len"])
    d["dc_hi"] = df["high"].rolling(P["donchian"]).max().shift(1)
    d["dc_lo"] = df["low"].rolling(P["donchian"]).min().shift(1)
    d["long_sig"] = (d["close"] > d["dc_hi"]) & (d["up"] == 1)
    d["short_sig"] = (d["close"] < d["dc_lo"]) & (d["down"] == 1)
    warm = P["ema_slow"] * 4 + P["donchian"] + 8
    return d.iloc[warm:].dropna()


# ---------------------------------------------------------------- simulação
class Pos:
    def __init__(self, side, t):
        self.side, self.t0, self.bars = side, t, 0
        self.qty = 0.0      # unidades (sempre positivo)
        self.cost = 0.0     # soma px*qty
        self.margin = 0.0
        self.fees = 0.0
        self.stop = None
        self.tp = None
        self.ladder = []    # [(preço, nocional)] ordens limitadas pendentes
        self.entry1 = None
        self.added = False
        self.last_px = None

    @property
    def avg(self):
        return self.cost / self.qty

    @property
    def notional(self):
        return self.cost

    def fill(self, px, notional, maker=False):
        if not maker:
            px *= 1 + self.side * P["slip"]
        q = notional / px
        self.qty += q
        self.cost += q * px
        self.margin += notional / P["lev"]
        self.fees += notional * (P["maker"] if maker else P["taker"])
        if self.entry1 is None:
            self.entry1 = px
        self.last_px = px

    def upnl(self, px):
        return self.side * self.qty * (px - self.avg)

    def liq_px(self):
        return self.avg * (1 - self.side * LIQ_MOVE)


def run(d, v, lados="ambos"):
    realized, trades, curve = 0.0, [], []
    pos = None
    dead = False

    def close(px, reason, t, maker=False, liq=False):
        nonlocal realized, pos
        if liq:
            pnl = -pos.margin - pos.fees
        else:
            if not maker:
                px *= 1 - pos.side * P["slip"]
            fee = pos.qty * px * (P["maker"] if maker else P["taker"])
            pnl = pos.upnl(px) - pos.fees - fee
        realized += pnl
        trades.append(dict(side="long" if pos.side > 0 else "short", pnl=pnl,
                           t0=pos.t0, t1=t, reason=reason))
        pos = None

    for t, r in d.iterrows():
        o, h, l, c, atr = r["open"], r["high"], r["low"], r["close"], r["atr"]
        if dead:
            curve.append(0.0)
            continue
        filled = False
        if pos:
            pos.bars += 1
            pos.fees += pos.notional * P["funding_8h"] / 8
            s = pos.side
            adverse = l if s > 0 else h
            favor = h if s > 0 else l
            # 1) ordens limitadas da escada (C) — preço vai contra
            if v == "C" and pos.ladder:
                rest = []
                for px, n in pos.ladder:
                    if (s > 0 and l <= px) or (s < 0 and h >= px):
                        pos.fill(min(px, o) if s > 0 else max(px, o), n, maker=True)
                        filled = True
                    else:
                        rest.append((px, n))
                pos.ladder = rest
                pos.tp = pos.avg * (1 + s * pos.tp_pct)
            # 2) martingale (D): dobra a cada −5% a partir da última entrada
            if v == "D":
                while True:
                    nxt = pos.last_px * (1 - s * P["d_step"])
                    n = pos.notional * 1.0  # dobra: próxima ordem = tudo que já tem
                    if not ((s > 0 and l <= nxt) or (s < 0 and h >= nxt)):
                        break
                    if pos.margin + n / P["lev"] > P["d_max_margin"]:
                        break
                    pos.fill(nxt, n, maker=True)
                    filled = True
                pos.tp = pos.avg * (1 + s * pos.tp_pct)
            # 3) stop
            if pos.stop is not None and ((s > 0 and l <= pos.stop) or (s < 0 and h >= pos.stop)):
                px = min(pos.stop, o) if s > 0 else max(pos.stop, o)
                if (s > 0 and px <= pos.liq_px()) or (s < 0 and px >= pos.liq_px()):
                    close(px, "liquidação", t, liq=True)
                else:
                    close(px, "stop", t)
            # 4) liquidação isolada
            elif (s > 0 and l <= pos.liq_px()) or (s < 0 and h >= pos.liq_px()):
                close(pos.liq_px(), "liquidação", t, liq=True)
            # 5) alvo (C e D) — conservador: se uma ordem da escada encheu nesta vela,
            #    não assume que o alvo também foi atingido depois, na mesma vela
            elif (pos.tp is not None and not filled
                  and ((s > 0 and h >= pos.tp) or (s < 0 and l <= pos.tp))):
                close(pos.tp, "alvo", t, maker=True)
            else:
                # 6) gestão no fechamento da vela
                if v in ("A", "B"):
                    if v == "B" and not pos.added and s * (c - pos.entry1) >= P["b_add_atr"] * atr:
                        pos.fill(c, P["margin"] * P["lev"] / 2)
                        pos.added = True
                        pos.stop = max(pos.stop, pos.entry1) if s > 0 else min(pos.stop, pos.entry1)
                    trail = c - s * pos.mult * atr
                    pos.stop = max(pos.stop, trail) if s > 0 else min(pos.stop, trail)
                    flip = r["exit_long"] if s > 0 else r["exit_short"]
                    if flip == 1:
                        close(c, "tendência virou", t)
                if pos and v != "D" and pos.bars >= P["max_hold"]:
                    close(c, "prazo de 4 dias", t)
        # entradas (avaliadas no fechamento da vela)
        if pos is None:
            side = 1 if r["long_sig"] else (-1 if r["short_sig"] else 0)
            if lados == "long" and side < 0:
                side = 0
            if side:
                N = P["margin"] * P["lev"]
                forte = r["forte"] == 1
                if v in ("A", "B"):
                    mult = P["a_atr_forte"] if forte else P["a_atr"]
                    stop = c - side * mult * atr
                    if abs(c - stop) / c <= P["a_max_stop"]:
                        pos = Pos(side, t)
                        pos.fill(c, N if v == "A" else N / 2)
                        pos.stop = stop
                        pos.mult = mult
                        # tendência normal: alvo em 3 ATR; forte: sem alvo, deixa correr
                        pos.tp = None if forte else c + side * P["a_tp_atr"] * atr
                elif v == "C":
                    dist = P["c_stop_atr"] * atr
                    if dist / c <= P["c_max_stop"]:
                        pos = Pos(side, t)
                        pos.fill(c, N)
                        pos.ladder = [(c - side * k * P["c_step_atr"] * atr, N)
                                      for k in range(1, P["c_orders"])]
                        pos.stop = c - side * dist
                        pos.tp_pct = float(np.clip(P["c_tp_atr"] * atr / c, P["c_tp_min"], P["c_tp_max"]))
                        pos.tp = pos.avg * (1 + side * pos.tp_pct)
                else:
                    pos = Pos(side, t)
                    pos.fill(c, N)
                    pos.tp_pct = float(np.clip(P["d_tp_atr"] * atr / c, P["d_tp_min"], P["d_tp_max"]))
                    pos.tp = pos.avg * (1 + side * pos.tp_pct)
        eq = P["capital"] + realized + (pos.upnl(c) - pos.fees if pos else 0.0)
        if eq <= 0:  # subconta zerou: para de operar
            if pos:
                close(c, "liquidação", t, liq=True)
            dead = True
            eq = 0.0
        curve.append(eq)
    if pos:
        close(d["close"].iloc[-1], "fim do teste", d.index[-1])
    return pd.Series(curve, index=d.index), pd.DataFrame(trades)


# ---------------------------------------------------------------- métricas
def weekly(curve):
    wk = curve.resample("W-SUN").last()
    first = curve.iloc[0]
    return wk.diff().fillna(wk.iloc[0] - first)


def money(x):
    x = 0.0 if abs(x) < 0.5 else x
    return f"{x:+.0f}"


def stats(curve, tr):
    w = weekly(curve)
    peak = curve.cummax()
    dd = (curve - peak).min()
    def side(s):
        x = tr[tr["side"] == s] if len(tr) else tr
        if not len(x):
            return "0 trades"
        return f"{len(x)} trades, acerto {(x.pnl > 0).mean()*100:.0f}%, US$ {x.pnl.sum():+.0f}"
    liq = int((tr["reason"] == "liquidação").sum()) if len(tr) else 0
    weeks = max(len(w), 1)
    return {
        "Lucro total (US$)": money(curve.iloc[-1] - P['capital']),
        "Pior queda acumulada (US$)": f"{dd:.0f}",
        "Trades por semana": f"{(len(tr) / weeks):.1f}",
        "Semanas no positivo": f"{(w > 0).mean()*100:.0f}%",
        "Semana típica (mediana, US$)": money(w.median()),
        "Semana ruim (1 em 10, US$)": money(w.quantile(0.10)),
        "Semana boa (1 em 10, US$)": money(w.quantile(0.90)),
        "Pior semana (US$)": money(w.min()),
        "Melhor semana (US$)": money(w.max()),
        "Long": side("long"),
        "Short": side("short"),
        "Liquidações": liq,
    }, w


NAMES = {"A": "A Entrada única", "B": "B Piramidação", "C": "C DCA de futuros", "D": "D Martingale"}


def table(title, res):
    keys = list(next(iter(res.values())).keys())
    out = [f"## {title}", "", "| | " + " | ".join(res) + " |", "|---|" + "---|" * len(res)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(res[v][k]) for v in res) + " |")
    return "\n".join(out) + "\n"


def verdict(comb, weeks):
    ok = {}
    for v, s in comb.items():
        tot = float(s["Lucro total (US$)"])
        dd = float(s["Pior queda acumulada (US$)"])
        ok[v] = (tot, dd, s["Liquidações"], weeks[v])
    safe = {v: x for v, x in ok.items() if x[2] == 0 and x[1] > -300}
    best = max(safe or ok, key=lambda v: ok[v][0])
    best_week = max(ok, key=lambda v: ((ok[v][3] > 0).mean(), ok[v][3].median()))
    w = ok[best][3]
    lines = [
        f"🏆 O que mais funcionou no período: {best}",
        f"   Lucro total {ok[best][0]:+.0f} US$, pior queda {ok[best][1]:.0f} US$, liquidações: {ok[best][2]}.",
        f"📅 Maior chance de fechar UMA semana no positivo: {best_week} "
        f"({(ok[best_week][3] > 0).mean()*100:.0f}% das semanas).",
        "",
        f"💵 Numa semana com {best} (3 pares juntos, US$ 1.000):",
        f"   • Semana típica: {money(w.median())} US$",
        f"   • 1 em cada 10 semanas: perde mais que {abs(min(w.quantile(0.10), 0)):.0f} US$",
        f"   • 1 em cada 10 semanas: ganha mais que {max(w.quantile(0.90), 0):.0f} US$",
        f"   • Pior semana do teste: {money(w.min())} US$ · melhor: {money(w.max())} US$",
    ]
    if not safe:
        lines.append("⚠️ Nenhuma variante passou sem liquidação e com queda menor que US$ 300.")
    if ok[best][0] <= 0:
        lines.append("⚠️ Nenhuma variante deu lucro no período: não opere com dinheiro real.")
    return "\n".join(lines)


def telegram(text):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    msg = {"chat_id": chat, "text": text[:4000]}
    th = os.getenv("TELEGRAM_THREAD_ID", "").strip()
    if th:
        msg["message_thread_id"] = th
    data = urllib.parse.urlencode(msg).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ONDO-USDT,ASTER-USDT,PENDLE-USDT")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--fonte", default="binance", choices=["binance", "bingx"])
    a = ap.parse_args()
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    data = {}
    for i, s in enumerate(syms):
        df = synthetic(i + 1) if a.synthetic else fetch_1h(s, a.fonte)
        data[s] = prepare(df)
    start = max(d.index[0] for d in data.values())
    end = min(d.index[-1] for d in data.values())
    md = ["# Backtest v2 — ONDO, ASTER, PENDLE (1h + 4h, isolada US$ 100 × 10x)", ""]
    if a.synthetic:
        md.append("**DADOS SIMULADOS — só teste de código, resultados sem valor.**\n")
    md.append("Cada par é testado no seu histórico inteiro; a carteira junta usa só o "
              f"período comum aos {len(syms)} pares: {start:%d/%m/%Y} a {end:%d/%m/%Y}.\n")
    curves = {v: [] for v in NAMES}
    all_tr = {v: [] for v in NAMES}
    for s, d in data.items():
        res = {}
        for v in NAMES:
            cv, tr = run(d, v)
            res[NAMES[v]], _ = stats(cv, tr)
            cvc, trc = run(d.loc[start:end], v)
            curves[v].append(cvc - P["capital"])
            all_tr[v].append(trc)
        md.append(table(f"{s} — {d.index[0]:%d/%m/%Y} a {d.index[-1]:%d/%m/%Y} "
                        f"(dados: {FONTE.get(s, 'simulados')})", res))
    comb, weeks = {}, {}
    for v in NAMES:
        cv = P["capital"] + pd.concat(curves[v], axis=1).ffill().fillna(0).sum(axis=1)
        broke = cv[cv <= 0]
        if len(broke):  # carteira zerou: nada mais a partir daí
            cv[cv.index >= broke.index[0]] = 0.0
        tr = pd.concat([t for t in all_tr[v] if len(t)], ignore_index=True) \
            if any(len(t) for t in all_tr[v]) else pd.DataFrame(columns=["side", "pnl", "reason"])
        comb[NAMES[v]], weeks[NAMES[v]] = stats(cv, tr)
    md.append(table("Os 3 pares juntos (carteira de US$ 1.000)", comb))
    summary = verdict(comb, weeks)
    # mesma coisa, só comprado (sem shorts)
    combL, weeksL = {}, {}
    for v in NAMES:
        cs, ts = [], []
        for s, d in data.items():
            cvc, trc = run(d.loc[start:end], v, "long")
            cs.append(cvc - P["capital"])
            if len(trc):
                ts.append(trc)
        cv = P["capital"] + pd.concat(cs, axis=1).ffill().fillna(0).sum(axis=1)
        broke = cv[cv <= 0]
        if len(broke):
            cv[cv.index >= broke.index[0]] = 0.0
        tr = pd.concat(ts, ignore_index=True) if ts else pd.DataFrame(columns=["side", "pnl", "reason"])
        combL[NAMES[v]], weeksL[NAMES[v]] = stats(cv, tr)
    md.append(table("Só comprado (sem shorts) — os 3 pares juntos", combL))
    summary += "\n\nSÓ COMPRADO (sem shorts):\n" + verdict(combL, weeksL)
    md += ["## Resumo em linguagem simples", "", summary.replace("\n", "  \n"), "",
           "Stop e alvo maleáveis: em ATR, mais largos com o mercado agitado; com a tendência "
           "do 4h forte, A e B ficam sem alvo e seguem no stop móvel. ",
           "Premissas: taxa taker 0,05% e maker 0,02%, slippage 0,05% em ordens a mercado, "
           "funding estimado 0,01%/8h, liquidação isolada a ~9,5% do preço médio. "
           "Resultado passado não garante resultado futuro."]
    text = "\n".join(md)
    with open("relatorio_bingx.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    telegram(("[DADOS SIMULADOS]\n" if a.synthetic else "") +
             f"Backtest ONDO/ASTER/PENDLE ({start:%d/%m/%Y}–{end:%d/%m/%Y})\n\n" + summary)


if __name__ == "__main__":
    main()
