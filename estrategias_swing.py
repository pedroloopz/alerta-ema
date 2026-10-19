"""Estratégias de swing em vela diária (mesmo código no backtest do alerta-ema e nas sombras do robo-bingx).
Velas = lista de dicts {time (ms), open, high, low, close}, em ordem, só velas fechadas.

- donchian: rompimento de 20 dias a favor do semanal; stop 2,5 ATR; alvo 3R; sai se fechar além do fundo/topo de
  10 dias; no máximo 20 dias.
- repique_forte: cripto, só com o semanal em ALTA FORTE; compra a queda (RSI(2) < 10 ou fechamento abaixo da banda de
  Bollinger de baixo); stop 2,5 ATR; sai quando fecha acima da média de 5 dias; no máximo 10 dias.
- virada: ações, depois de cair 20%+ da máxima de 60 dias, fecha acima da média de 20 dias com ela virando para cima;
  stop no fundo de 10 dias (até 15%); alvo 3R; no máximo 20 dias.
"""
import datetime

DIA_MS = 86_400_000


def ema(v, n):
    k, e = 2 / (n + 1), None
    for x in v:
        e = x if e is None else e + k * (x - e)
    return e


def serie_ema(v, n):
    k, out, e = 2 / (n + 1), [], None
    for x in v:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def rsi2(c):
    g = p = None
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        up, dn = max(d, 0), max(-d, 0)
        g = up if g is None else g + (up - g) / 2
        p = dn if p is None else p + (dn - p) / 2
    if g is None:
        return 50.0
    return 100.0 if p == 0 else 100 - 100 / (1 + g / p)


def atr(v, n=14):
    tr = [max(v[i]["high"] - v[i]["low"], abs(v[i]["high"] - v[i - 1]["close"]), abs(v[i]["low"] - v[i - 1]["close"]))
          for i in range(len(v) - n, len(v))]
    return sum(tr) / n


def semanas(v):
    """Fechamentos semanais (semanas completas, de segunda a domingo) a partir das velas diárias."""
    out, chave_ant = [], None
    for x in v:
        d = datetime.datetime.fromtimestamp(x["time"] / 1000, datetime.timezone.utc).date()
        chave = d.isocalendar()[:2]
        if chave != chave_ant:
            out.append([chave, x["close"]])
            chave_ant = chave
        else:
            out[-1][1] = x["close"]
    if out:
        ult = datetime.datetime.fromtimestamp(v[-1]["time"] / 1000, datetime.timezone.utc).date()
        if ult.weekday() != 6:                     # semana atual ainda aberta: fica de fora
            out = out[:-1]
    return [c for _, c in out]


def regime(v):
    """'alta forte' | 'alta' | 'neutro' | 'baixa' | 'baixa forte' pelo semanal (médias de 10 e 20 semanas)."""
    w = semanas(v)
    if len(w) < 24:
        return None
    m10s = serie_ema(w, 10)
    m10, m20, m10_4 = m10s[-1], ema(w, 20), m10s[-5]
    c = w[-1]
    if c > m10 > m20:
        return "alta forte" if m10 > m10_4 else "alta"
    if c < m10 < m20:
        return "baixa forte" if m10 < m10_4 else "baixa"
    return "neutro"


def _plano(lado, ent, stop, alvo_r, max_dias, nome, extra=None):
    s = 1 if lado == "LONG" else -1
    risco = abs(ent - stop) / ent
    if risco <= 0 or risco > 0.25:
        return None
    alvo = ent * (1 + s * alvo_r * risco) if alvo_r else None
    return dict({"estrategia": nome, "lado": lado, "ent": ent, "stop": stop, "alvo": alvo, "risco": risco,
                 "max_dias": max_dias, "dias": 0}, **(extra or {}))


def donchian(v, reg, stop_atr=2.5, alvo_r=3.0, n=20, filtro=True, saida_n=10, max_dias=20):
    if len(v) < 60:
        return None
    c = v[-1]["close"]
    a = atr(v)
    topo = max(x["high"] for x in v[-n - 1:-1])
    fundo = min(x["low"] for x in v[-n - 1:-1])
    pode_l = (not filtro) or reg in ("alta", "alta forte")
    pode_s = (not filtro) or reg in ("baixa", "baixa forte")
    if pode_l and c > topo:
        return _plano("LONG", c, c - stop_atr * a, alvo_r, max_dias, "donchian", {"saida_n": saida_n})
    if pode_s and c < fundo:
        return _plano("SHORT", c, c + stop_atr * a, alvo_r, max_dias, "donchian", {"saida_n": saida_n})
    return None


def repique_forte(v, reg, regimes=("alta forte",), stop_atr=2.5, max_dias=10):
    if len(v) < 60 or (regimes and reg not in regimes):
        return None
    cs = [x["close"] for x in v]
    m20 = sum(cs[-20:]) / 20
    dp = (sum((x - m20) ** 2 for x in cs[-20:]) / 20) ** 0.5
    if rsi2(cs[-30:]) < 10 or cs[-1] < m20 - 2 * dp:
        return _plano("LONG", cs[-1], cs[-1] - stop_atr * atr(v), None, max_dias, "repique_forte", {"saida_media": 5})
    return None


def virada(v, queda_min=0.20, max_stop=0.15, alvo_r=3.0, max_dias=20):
    if len(v) < 70:
        return None
    cs = [x["close"] for x in v]
    m = serie_ema(cs, 20)
    pico = max(x["high"] for x in v[-60:])
    if min(cs[-15:]) / pico - 1 > -queda_min:
        return None                                    # não caiu 20%+ nas últimas 3 semanas
    if not (cs[-1] > m[-1] and cs[-2] <= m[-2] and m[-1] > m[-2]):
        return None
    stop = min(x["low"] for x in v[-10:])
    if 1 - stop / cs[-1] > max_stop:
        return None
    return _plano("LONG", cs[-1], stop, alvo_r, max_dias, "virada")


def passo(t, v, custo=0.001, funding_dia=0.0003):
    """Avança o trade t com a vela diária mais nova de v (v[-1]). Devolve o R líquido se fechou, senão None."""
    x = v[-1]
    s = 1 if t["lado"] == "LONG" else -1
    t["dias"] += 1

    def fecha(px):
        bruto = s * (px / t["ent"] - 1)
        t["saida"], t["r"] = px, (bruto - 2 * custo - funding_dia * t["dias"]) / t["risco"]
        return t["r"]

    if (s == 1 and x["low"] <= t["stop"]) or (s == -1 and x["high"] >= t["stop"]):
        px = x["open"] if (s == 1 and x["open"] < t["stop"]) or (s == -1 and x["open"] > t["stop"]) else t["stop"]
        return fecha(px)
    if t.get("alvo") and ((s == 1 and x["high"] >= t["alvo"]) or (s == -1 and x["low"] <= t["alvo"])):
        px = x["open"] if (s == 1 and x["open"] > t["alvo"]) or (s == -1 and x["open"] < t["alvo"]) else t["alvo"]
        return fecha(px)
    if t.get("saida_n") and len(v) > t["saida_n"] + 1:
        ref = v[-t["saida_n"] - 1:-1]
        if (s == 1 and x["close"] < min(y["low"] for y in ref)) or (s == -1 and x["close"] > max(y["high"] for y in ref)):
            return fecha(x["close"])
    if t.get("saida_media"):
        cs = [y["close"] for y in v[-t["saida_media"]:]]
        if s * (x["close"] - sum(cs) / len(cs)) > 0:
            return fecha(x["close"])
    if t["dias"] >= t["max_dias"]:
        return fecha(x["close"])
    return None
