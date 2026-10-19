"""Cópia das funções de gatilho do robô (robo-bingx/tese.py, 19/10/2026) para o backtest. Não edite: copie de novo."""

def _ema(v, n):
    k, e = 2 / (n + 1), None
    for x in v:
        e = x if e is None else e + k * (x - e)
    return e


def _rsi(v, n=2):
    g_, p_, r = None, None, 50.0
    for a, b in zip(v[:-1], v[1:]):
        g, p = max(b - a, 0), max(a - b, 0)
        g_ = g if g_ is None else g_ + (g - g_) / n
        p_ = p if p_ is None else p_ + (p - p_) / n
        r = 100.0 if p_ == 0 else 100 - 100 / (1 + g_ / p_)
    return r


def gatilho(velas, setups_por_lado, tendencia=True, sl=0.03, atr_max=0.01, media_tendencia=None):
    """Gatilho de qualidade na última vela fechada. Devolve dict (lado, setup, nota) ou None.
    Filtros: o stop (−3%) precisa ficar a pelo menos 3 ATRs de 1h (ATR% <= atr_max) e, fora o repique,
    a entrada tem de ser a favor da tendência (preço do lado certo da média de 200 velas de 1h)."""
    if len(velas) < 210:
        return None
    c = [float(x["close"]) for x in velas]
    h = [float(x["high"]) for x in velas]
    lo = [float(x["low"]) for x in velas]
    tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(len(c) - 14, len(c))]
    atr_pct = sum(tr) / 14 / c[-1]
    if atr_pct > atr_max:
        return None                                       # ativo agitado demais para o stop de 3%
    media = sum(c[-20:]) / 20
    dp = (sum((x - media) ** 2 for x in c[-20:]) / 20) ** 0.5
    sup, inf = media + 2 * dp, media - 2 * dp
    rsi = _rsi(c[-30:])
    rsi_ant = _rsi(c[-31:-1])                             # o extremo é na vela anterior; a atual confirma a virada
    media_ant = sum(c[-21:-1]) / 20
    dp_ant = (sum((x - media_ant) ** 2 for x in c[-21:-1]) / 20) ** 0.5
    sup_ant, inf_ant = media_ant + 2 * dp_ant, media_ant - 2 * dp_ant
    medias = {n: _ema(c[-n:], n) for n in {200, *(media_tendencia or {}).values()}}   # por lado (padrão: 200)
    folga = sl / atr_pct                                   # quantos ATRs cabem até o stop (mais = melhor)
    for lado, setups in setups_por_lado.items():
        ema = medias[(media_tendencia or {}).get(lado, 200)]
        a_favor = (c[-1] > ema) if lado == "LONG" else (c[-1] < ema)
        for st in setups:
            if st != "repique" and tendencia and not a_favor:
                continue
            if lado == "LONG":
                ok = ((st == "recuo" and rsi_ant < 15 and lo[-2] <= inf_ant)
                      or (st == "rompimento" and c[-1] > max(h[-21:-1]))
                      or (st == "repique" and rsi_ant < 5 and c[-2] < inf_ant))
            else:
                ok = ((st == "recuo" and rsi_ant > 85 and h[-2] >= sup_ant)
                      or (st == "rompimento" and c[-1] < min(lo[-21:-1])))
            if ok:                                           # vela de confirmação
                o_, rng = float(velas[-1].get("open") or c[-2]), max(h[-1] - lo[-1], 1e-12)
                pos = (c[-1] - lo[-1]) / rng                 # onde fechou dentro da vela (0 = mínima, 1 = máxima)
                if st == "rompimento" and ((lado == "LONG" and pos < 0.7) or (lado == "SHORT" and pos > 0.3)):
                    ok = False                               # rompimento com pavio contra: fraco
                if st in ("recuo", "repique") and ((lado == "LONG" and not (c[-1] > o_ and c[-1] > c[-2]))
                                                    or (lado == "SHORT" and not (c[-1] < o_ and c[-1] < c[-2]))):
                    ok = False                               # recuo só quando a vela já virou a favor
            if ok and st == "rompimento":                    # rompimento só com volume: 1,5x o volume do mesmo
                por_hora = {int(x["time"]): float(x.get("volume") or 0) for x in velas}   # horário nos 5 dias antes
                t0 = int(velas[-1]["time"])
                mesmas = [por_hora[t0 - k * 86400000] for k in range(1, 6) if por_hora.get(t0 - k * 86400000, 0) > 0]
                vol = [float(x.get("volume") or 0) for x in velas[-21:]]
                ref = sum(mesmas) / len(mesmas) if len(mesmas) >= 3 else sum(vol[:-1]) / 20
                if ref > 0 and vol[-1] < 1.5 * ref:
                    ok = False
            if ok and st != "repique" and abs(c[-1] - media) > 3 * atr_pct * c[-1]:
                ok = False                                   # não corre atrás: já esticado da média de 20
            if ok and st != "repique":                       # espaço até o alvo: 3% livres até a máx/mín de 24h
                nivel = max(h[-25:-1]) if lado == "LONG" else min(lo[-25:-1])
                folga_nivel = (nivel / c[-1] - 1) if lado == "LONG" else (1 - nivel / c[-1])
                if 0 < folga_nivel < sl:
                    ok = False
            if ok:
                distancia_media = abs(c[-1] / ema - 1) / atr_pct      # tendência firme, sem estar esticada
                nota = folga + (1.0 if a_favor else 0.0) + (0.5 if 1 <= distancia_media <= 8 else 0.0)
                return {"lado": lado, "setup": st, "nota": round(nota, 2), "atr_pct": round(atr_pct, 4), "adx": adx(velas)}
    return None


def adx(velas, n=14):
    """ADX de Wilder (força da tendência, 0–100). Abaixo de ~20 = mercado andando de lado."""
    h = [float(x["high"]) for x in velas]
    lo = [float(x["low"]) for x in velas]
    c = [float(x["close"]) for x in velas]
    if len(c) < 3 * n:
        return None
    tr_s = mp_s = mm_s = adx_ = None
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        tr = max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
        mp, mm = (up if up > dn and up > 0 else 0.0), (dn if dn > up and dn > 0 else 0.0)
        if tr_s is None:
            tr_s, mp_s, mm_s = tr, mp, mm
        else:
            tr_s, mp_s, mm_s = tr_s - tr_s / n + tr, mp_s - mp_s / n + mp, mm_s - mm_s / n + mm
        if tr_s > 0:
            di_p, di_m = 100 * mp_s / tr_s, 100 * mm_s / tr_s
            dx = 100 * abs(di_p - di_m) / (di_p + di_m) if di_p + di_m > 0 else 0.0
            adx_ = dx if adx_ is None else adx_ + (dx - adx_) / n
    return round(adx_, 1) if adx_ is not None else None

