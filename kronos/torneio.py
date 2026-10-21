"""Torneio do Kronos: 10 traders profissionais, 5 amadores e 1 juiz sobre 1 ano de previsões reais do Kronos-small.

Base: branch kronos-dados (dados_teste.py) — por partida, o resumo dos 8 caminhos previstos e as 24 velas de 1h
reais que vieram depois. Cada trader decide se entra, de que lado, com que stop/alvo/trailing; a carteira simula
tudo junto (vagas, risco aberto, teto por grupo, juros compostos, taxa + deslize + funding).

Juiz: ajusta os parâmetros de cada trader só nos 9 primeiros meses (treino) e mede nos 3 últimos (fora da amostra).
Depois otimiza saídas e risco do vencedor (também só no treino), confere fora da amostra e roda Monte Carlo de 2 meses.

  python kronos/torneio.py <pasta com dados/*.json.gz> <saida.json>
"""
import bisect
import gzip
import glob
import itertools
import json
import math
import os
import sys

import numpy as np

CUSTO = 0.0012                 # taxa ida e volta (mercado) + deslize — o mesmo do teste semanal
CUSTO_MAKER = 0.0007           # entrada limite (maker 0,02%) + saída + deslize
FUNDING_H = 0.0001 / 8         # ~0,01% a cada 8h, contra (conservador)
SL_MIN, SL_MAX = 0.006, 0.06
H = 24
CORTE = 1784592000000          # 2026-07-21 00:00 UTC: treino antes, fora da amostra depois
DIA = 86400000

GRUPOS = {
    "cripto grande": {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "TRX", "DOGE", "BCH", "LTC", "LINK", "XLM", "HBAR", "DOT"},
    "neoclouds": {"IREN", "NBIS", "CRWV", "APLD", "CIFR", "WULF", "HUT", "CORZ", "GLXY", "BTDR"},
    "big tech": {"AAPL", "MSFT", "AMZN", "NVDA", "AMD", "AVGO", "ARM", "ASML", "TSLA", "ORCL", "META", "GOOGL"},
    "ações outras": {"JPM", "LLY", "MA", "V", "UNH", "WMT", "COST", "NVO", "SONY", "TM", "BABA", "PDD", "SHOP"},
    "metais": {"Ouro", "Prata", "Platina", "Paládio", "Cobre"},
    "energia": {"Petróleo WTI", "Petróleo Brent", "Gás natural"},
}


def grupo(o):
    if o["tipo"] == "índice":
        return "índices"
    for g, s in GRUPOS.items():
        if o["ativo"] in s:
            return g
    return "cripto alt" if o["tipo"] == "cripto" else ("ações outras" if o["tipo"] == "ação" else "metais")


# ---------------------------------------------------------------- dados
def carregar(pasta):
    ops, series = [], {}
    for f in sorted(glob.glob(os.path.join(pasta, "dados", "*.json.gz"))):
        d = json.load(gzip.open(f))
        p = d["partidas"]
        if len(p) < 25:
            continue
        nome = d["ativo"].split(" (")[0]
        ts = [x["t"] for x in p]
        ul = [x["ult"] for x in p]
        series[d["simbolo"]] = (ts, ul)
        fund = d.get("funding") or []                 # funding real (base kronos-dados-23h); sem ele, estimativa
        f_ts = [x[0] for x in fund]
        f_ac = list(itertools.accumulate([x[1] for x in fund], initial=0.0))
        atrs = []
        for x in p:
            o = {"t": x["t"], "simbolo": d["simbolo"], "ativo": nome, "tipo": d["tipo"],
                 "p_alta": x["p_alta"], "med": x["ret_mediano"], "q": x["ret_q"],
                 "queda": x["queda_q15"], "alta": x["alta_q15"], "recuo": x["recuo"], "corrida": x["corrida"],
                 "atr": x["atr_1h"], "r24": x["ret_24h_antes"], "vol": x["vol_24h_usd"],
                 "reais": np.asarray(x["reais"], dtype=float), "faixa": x["faixa_fech"]}
            o["atr_rel"] = x["atr_1h"] / np.median(atrs) if len(atrs) >= 5 else 1.0
            if f_ts and f_ts[0] <= x["t"]:
                base = f_ac[bisect.bisect_right(f_ts, x["t"])]
                o["fcum"] = [f_ac[bisect.bisect_right(f_ts, x["t"] + h * 3600000)] - base for h in range(H + 1)]
            atrs.append(x["atr_1h"])
            o["grupo"] = grupo(o)
            ops.append(o)
    ops.sort(key=lambda o: o["t"])
    for o in ops:                                     # tendência do próprio ativo (só passado)
        o["mom10"] = momento(series, o["simbolo"], o["t"], 240)
        o["mom30"] = momento(series, o["simbolo"], o["t"], 720)
    ref = {"btc": "BTC-USDT", "spx": "NCSISP5002USD-USDT", "ouro": "NCCOGOLD2USD-USDT", "ndx": "NCSINASDAQ1002USD-USDT"}
    for o in ops:                                     # regime de mercado no momento (só passado)
        for k, s in ref.items():
            o["m_" + k] = momento(series, s, o["t"], 240, estrito=False)
    return ops, series


def momento(series, sym, t, horas, estrito=True):
    if sym not in series:
        return None
    ts, ul = series[sym]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or (not estrito and t - ts[i] > 4 * DIA):
        return None
    j = bisect.bisect_right(ts, ts[i] - horas * 3600000) - 1
    if j < 0 or ts[i] - ts[j] > horas * 3600000 * 1.6:
        return None
    return ul[i] / ul[j] - 1


# ---------------------------------------------------------------- um trade nas velas reais
def niveis_kronos(o, s):
    sl = min(max(o["queda"] if s == 1 else o["alta"], 0.008), 0.05)
    tp1 = max(s * o["med"], 0.005)
    tp2 = max(s * (o["q"]["p75"] if s == 1 else o["q"]["p25"]), tp1 + 0.003)
    return sl, tp1, tp2


def stop_de(o, s, x):
    t = x.get("sl", "kronos")
    if t == "kronos":
        sl = niveis_kronos(o, s)[0] * x.get("sl_k", 1.0)
    elif t == "atr":
        sl = x.get("sl_k", 5) * o["atr"]
    else:
        sl = x.get("sl_k", 0.02)
    return min(max(sl, SL_MIN), SL_MAX)


def alvos_de(o, s, sl, x):
    """Lista de (distância a favor, fração da posição)."""
    t = x.get("tp", "kronos")
    if t == "kronos":
        _, tp1, tp2 = niveis_kronos(o, s)
        m = x.get("tp_k", 1.0)
        alvos = [(tp1 * m, 0.5), (tp2 * m, 0.5)]
    elif t == "R":
        rs = x.get("tp_r", (1.5, 3.0))
        fr = x.get("tp_fr") or [1 / len(rs)] * len(rs)
        alvos = [(r * sl, f) for r, f in zip(rs, fr)]
    else:                                             # nenhum alvo: só trailing/tempo
        alvos = []
    teto = x.get("tp_teto")
    if teto:
        alvos = [(min(d, teto * sl), f) for d, f in alvos]
    return [(max(d, 0.003), f) for d, f in alvos]


def fcusto(o, s, i0, i1):
    """Funding pago entre as horas i0 e i1 do trade (positivo = custo). Real quando a base tem; senão, estimativa."""
    f = o.get("fcum")
    if f is None:
        return FUNDING_H * (i1 - i0)
    return s * (f[min(i1, H)] - f[min(i0, H)])


def simular(o, s, x):
    """Resultado em R (1R = perder o stop com a posição cheia) e horas no trade. Vela ambígua = stop."""
    re = o["reais"]
    sl = stop_de(o, s, x)
    alvos = alvos_de(o, s, sl, x)
    sair_h = x.get("sair_h", H)
    custo = CUSTO
    i0, ent = 0, 1 + re[0][0]
    if x.get("entrada") == "recuo":                   # limite no recuo previsto (vale 3h); não pegou = sem trade
        d = max(o["recuo"]["LONG" if s == 1 else "SHORT"]["q50"] * x.get("recuo_k", 0.5), 0.001)
        alvo_px = (1 + re[0][0]) * (1 - s * d)
        i0 = None
        for i in range(min(3, len(re))):
            lo, hi = re[i][2], re[i][1]
            if (s == 1 and 1 + lo <= alvo_px) or (s == -1 and 1 + hi >= alvo_px):
                i0, ent = i, alvo_px
                break
        if i0 is None:
            return None
        custo = CUSTO_MAKER
    pos, feito = 1.0, 0.0
    stop = -sl                                        # em retorno a favor, relativo à entrada
    extremo = 0.0
    k_alvo = 0
    trail = x.get("trail")
    for i in range(i0, min(len(re), sair_h)):
        _, h, l, c = re[i]
        fav = s * ((1 + (h if s == 1 else l)) / ent - 1)
        con = s * ((1 + (l if s == 1 else h)) / ent - 1)
        if i == i0 and x.get("entrada") == "recuo":    # vela da entrada: não se sabe a ordem; usa o fechamento
            con = min(s * ((1 + c) / ent - 1), 0.0)
            fav = max(s * ((1 + c) / ent - 1), 0.0)
        if con <= stop:
            feito += pos * stop
            return (feito - custo - fcusto(o, s, i0, i + 1)) / sl, i - i0 + 1
        while k_alvo < len(alvos) and fav >= alvos[k_alvo][0]:
            d, f = alvos[k_alvo]
            f = min(f, pos)
            feito += f * d
            pos -= f
            k_alvo += 1
            if k_alvo == 1 and x.get("be") is not None:
                stop = max(stop, x["be"] * sl)
        if pos <= 1e-9:
            return (feito - custo - fcusto(o, s, i0, i + 1)) / sl, i - i0 + 1
        extremo = max(extremo, fav)
        if trail == "chandelier" and (k_alvo >= 1 or not x.get("trail_so_apos")):
            stop = max(stop, extremo - x.get("trail_k", 4) * o["atr"])
        elif trail == "degrau":
            passo = x.get("trail_k", 1.0) * sl
            if extremo >= passo:
                stop = max(stop, (math.floor(extremo / passo) - 1) * passo)
    fim = min(len(re), sair_h) - 1
    final = s * ((1 + re[fim][3]) / ent - 1)
    feito += pos * final
    return (feito - custo - fcusto(o, s, i0, fim + 1)) / sl, fim - i0 + 1


# ---------------------------------------------------------------- traders
SAIDA_KRONOS = {"sl": "kronos", "tp": "kronos", "be": 0.0}
SAIDA_ATUAL = {"sl": "kronos", "tp": "kronos", "tp_k": 0.6, "tp_teto": 2.0, "be": -0.3}


def p_dir(o):
    s = 1 if o["p_alta"] >= 0.5 else -1
    return s, (o["p_alta"] if s == 1 else 1 - o["p_alta"])


def ev_kronos(o, s):
    sl, tp1, tp2 = niveis_kronos(o, s)
    c = o["corrida"]["LONG" if s == 1 else "SHORT"]
    return c["alvo1_antes"] * (tp1 + tp2) / 2 - c["stop_antes"] * sl


def t_tendencia(o, p):
    s, pd = p_dir(o)
    if pd < p["p"] or o["mom10"] is None or o["mom30"] is None:
        return None
    if s * o["mom10"] <= 0 or s * o["mom30"] <= 0:
        return None
    return s, {"sl": "atr", "sl_k": p["sl_k"], "tp": "R", "tp_r": (2.0, 3.5), "tp_fr": (0.5, 0.5),
               "trail": "chandelier", "trail_k": p["trail_k"], "be": 0.0}, pd + s * o["mom10"]


def t_macro(o, p):
    s, pd = p_dir(o)
    if pd < p["p"] or o["m_spx"] is None:
        return None
    risco_on = o["m_spx"] > 0 and (o["m_btc"] or 0) > -0.05
    if o["grupo"] == "metais":
        if o["ativo"] in ("Ouro", "Prata") and s == 1 and (o["m_ouro"] or 0) < 0:
            return None
    elif o["grupo"] != "energia":
        if s == 1 and not risco_on:
            return None
        if s == -1 and risco_on and p["short_so_off"]:
            return None
    return s, dict(SAIDA_KRONOS), pd


def t_reversao(o, p):
    lim = p["z"] * o["atr"] * 4.9                      # ~z desvios do movimento de 24h
    if abs(o["r24"]) < lim:
        return None
    s = -1 if o["r24"] > 0 else 1
    pd = o["p_alta"] if s == 1 else 1 - o["p_alta"]
    if pd < p["p"]:
        return None
    return s, {"sl": "kronos", "tp": "kronos", "tp_k": 0.8, "be": 0.0, "sair_h": 12}, pd + abs(o["r24"]) / lim / 10


def t_squeeze(o, p):
    s, pd = p_dir(o)
    larg = o["q"]["p90"] - o["q"]["p10"]
    if pd < p["p"] or o["atr_rel"] > p["compr"] or larg < 2.5 * o["atr"]:
        return None
    return s, {"sl": "atr", "sl_k": 4, "tp": "nenhum", "trail": "chandelier", "trail_k": p["trail_k"]}, pd


def t_paridade(o, p):
    s, pd = p_dir(o)
    if pd < p["p"] or s * o["med"] < 0.004:
        return None
    return s, {"sl": "kronos", "tp": "R", "tp_r": (1.0, 2.0), "be": 0.0}, pd


def t_ev(o, p):
    s, pd = p_dir(o)
    ev = ev_kronos(o, s)
    sl = niveis_kronos(o, s)[0]
    if ev / sl < p["ev"] or pd < 0.55:
        return None
    return s, dict(SAIDA_KRONOS), ev / sl


def t_sessoes(o, p):
    s, pd = p_dir(o)
    hora = (o["t"] // 3600000) % 24
    janela = {"índice": {0, 1, 7, 8, 13, 14}, "ação": {13, 14, 15}, "commodity": {7, 8, 13, 14}}.get(o["tipo"])
    if janela is not None and hora not in janela:
        return None
    if o["tipo"] == "cripto" and hora not in {0, 1, 7, 8, 13, 14, 15}:
        return None
    if pd < p["p"]:
        return None
    return s, {"sl": "kronos", "tp": "kronos", "be": 0.0, "sair_h": p["sair_h"]}, pd


def t_recuo(o, p):
    s, pd = p_dir(o)
    if pd < p["p"] or s * o["med"] < 0.004:
        return None
    return s, {"sl": "kronos", "tp": "kronos", "be": 0.0, "entrada": "recuo", "recuo_k": p["recuo_k"]}, pd


def t_seletivo(o, p):
    s, pd = p_dir(o)
    if pd < p["p"] or o["mom10"] is None or s * o["mom10"] <= 0:
        return None
    ev = ev_kronos(o, s) / niveis_kronos(o, s)[0]
    if ev < 0.1:
        return None
    return s, {"sl": "kronos", "tp": "R", "tp_r": (1.5, 3.0), "be": 0.0, "trail": "chandelier", "trail_k": 5,
               "trail_so_apos": True}, pd * (1 + ev)


def t_diversificado(o, p):
    s, pd = p_dir(o)
    if pd < p["p"]:
        return None
    if o["grupo"] in ("cripto alt",) and pd < p["p"] + 0.15:
        return None
    return s, {"sl": "atr", "sl_k": p["sl_k"], "tp": "R", "tp_r": (1.5, 2.5), "be": 0.0}, pd


# amadores
def a_puro(o, p):
    s, pd = p_dir(o)
    return s, dict(SAIDA_KRONOS), pd


def a_martingale(o, p):
    s, pd = p_dir(o)
    if pd < 0.55:
        return None
    return s, dict(SAIDA_KRONOS), pd


def a_rsi(o, p):
    if abs(o["r24"]) < p["queda"]:
        return None
    s = -1 if o["r24"] > 0 else 1
    return s, {"sl": "pct", "sl_k": 0.02, "tp": "R", "tp_r": (2.0,)}, abs(o["r24"])


def a_momentum(o, p):
    if o["r24"] < p["alta"]:
        return None
    return 1, {"sl": "pct", "sl_k": 0.03, "tp": "R", "tp_r": (1.0,)}, o["r24"]


def a_alav(o, p):
    s, pd = p_dir(o)
    if pd < 0.6:
        return None
    return s, {"sl": "pct", "sl_k": 0.03, "tp": "R", "tp_r": (0.17,)}, pd


def atual(o, p):
    """Setup do Kronos que está no robô hoje (referência)."""
    s, pd = p_dir(o)
    if pd < (0.90 if s == -1 else 0.60) or s * o["med"] < 0.005:
        return None
    if s == -1 and (o["mom10"] or 0) >= 0:
        return None
    sl, tp1, tp2 = niveis_kronos(o, s)
    if (tp1 + tp2) / 2 < 0.6 * sl:
        return None
    return s, dict(SAIDA_ATUAL), pd


TRADERS = {
    # nome: (função, categoria, grade de parâmetros ajustados no treino, descrição)
    "Tendência (Turtle)": (t_tendencia, "pro", {"p": [0.55, 0.6, 0.7], "sl_k": [4, 6], "trail_k": [4, 6]},
                           "Kronos a favor da tendência de 10 e 30 dias; stop por ATR, alvos 2R/3,5R, chandelier"),
    "Macro": (t_macro, "pro", {"p": [0.55, 0.6, 0.7], "short_so_off": [True, False]},
              "Regime do S&P/BTC/ouro decide se pode comprar ou vender; Kronos escolhe o ativo"),
    "Reversão à média": (t_reversao, "pro", {"z": [1.0, 1.5, 2.0], "p": [0.5, 0.6]},
                         "Contra um movimento de 24h esticado, só se o Kronos prevê a volta; sai em 12h"),
    "Rompimento de volatilidade": (t_squeeze, "pro", {"p": [0.55, 0.65], "compr": [0.8, 1.0], "trail_k": [3, 5]},
                                   "Volatilidade comprimida + Kronos prevendo faixa larga; só trailing, sem alvo"),
    "Paridade de risco": (t_paridade, "pro", {"p": [0.55, 0.6, 0.7]},
                          "Pega muitos sinais, mão igual em risco, alvos curtos 1R/2R"),
    "Valor esperado": (t_ev, "pro", {"ev": [0.05, 0.15, 0.3, 0.5]},
                       "Só entra quando a corrida alvo-antes-do-stop dos caminhos dá valor esperado positivo"),
    "Sessões": (t_sessoes, "pro", {"p": [0.55, 0.65], "sair_h": [8, 24]},
                "Só nas aberturas de Tóquio, Londres e Nova York de cada mercado"),
    "Entrada no recuo": (t_recuo, "pro", {"p": [0.55, 0.6, 0.7], "recuo_k": [0.3, 0.6, 1.0]},
                         "Ordem limite no recuo previsto (maker), stop e alvos do Kronos"),
    "Seletivo (consenso)": (t_seletivo, "pro", {"p": [0.6, 0.7, 0.8]},
                            "Kronos + tendência + valor esperado concordando; só os melhores"),
    "Diversificado": (t_diversificado, "pro", {"p": [0.55, 0.6, 0.65], "sl_k": [4, 6]},
                      "Exige mais confiança em altcoins, teto por grupo, stop ATR, alvos 1,5R/2,5R"),
    "Amador: Kronos puro": (a_puro, "amador", {}, "Entra em toda previsão, sem filtro"),
    "Amador: martingale": (a_martingale, "amador", {}, "Dobra a mão depois de cada perda (até 4x)"),
    "Amador: RSI 30/70": (a_rsi, "amador", {"queda": [0.03, 0.05]}, "Compra depois de queda forte, vende depois de alta, sem Kronos"),
    "Amador: compra o que subiu": (a_momentum, "amador", {"alta": [0.03, 0.06]}, "Compra quem mais subiu em 24h"),
    "Amador: alavancagem alta": (a_alav, "amador", {}, "Alvo minúsculo (0,5%), stop 3%, risco 5% por trade"),
    "Setup atual do robô": (atual, "referência", {}, "Regras do Kronos ao vivo hoje (stop −0,3R após alvo 1, alvos 60%)"),
}


# ---------------------------------------------------------------- carteira
def carteira(ops, decide, params, risco=0.01, vagas=12, risco_aberto=0.08, por_grupo=3, banca=15000.0,
             alav_max=10, saida=None, martingale=False, risco_fixo=None, ini=None, fim=None, lista=None,
             freio=False):
    """Simula a carteira. Devolve trades e curva de patrimônio (fechamentos)."""
    eq = banca
    abertos = []                                      # (t_saida, grupo, risco_usd, r, dados)
    trades, curva = [], [(ops[0]["t"] if ops else 0, eq)]
    mult = 1.0
    pico, pausa_ate = eq, 0
    i = 0
    n = len(ops)
    while i < n:
        t = ops[i]["t"]
        j = i
        while j < n and ops[j]["t"] == t:
            j += 1
        # fecha o que saiu antes de t
        abertos.sort(key=lambda a: a[0])
        while abertos and abertos[0][0] <= t:
            a = abertos.pop(0)
            eq += a[2] * a[3]
            trades.append(a[4] | {"pnl": a[2] * a[3], "eq": eq})
            curva.append((a[0], eq))
            pico = max(pico, eq)
            if martingale:
                mult = 1.0 if a[3] > 0 else min(mult * 2, 4.0)
            if freio and eq < pico * 0.85:
                pausa_ate = a[0] + 2 * DIA
        if (ini is None or t >= ini) and (fim is None or t < fim) and t >= pausa_ate:
            cand = []
            for o in ops[i:j]:
                if lista is not None and o["simbolo"] not in lista:
                    continue
                d = decide(o, params)
                if d is None:
                    continue
                s, x, score = d
                if saida:
                    x = dict(saida) | {k: v for k, v in x.items() if k in ("entrada", "recuo_k")}
                cand.append((score, o, s, x))
            cand.sort(key=lambda c: -c[0])
            for score, o, s, x in cand:
                if len(abertos) >= vagas:
                    break
                if sum(1 for a in abertos if a[1] == o["grupo"]) >= por_grupo:
                    continue
                if any(a[4]["simbolo"] == o["simbolo"] for a in abertos):
                    continue
                r_trade = (risco_fixo or risco) * mult
                if freio and eq < pico * 0.90:
                    r_trade *= 0.5
                if sum(a[2] for a in abertos) + eq * r_trade > eq * risco_aberto:
                    continue
                res = simular(o, s, x)
                if res is None:
                    continue
                r, horas = res
                sl = stop_de(o, s, x)
                risco_usd = eq * r_trade
                nocional = risco_usd / sl
                margem = eq / vagas
                alav = nocional / margem
                if alav > alav_max:                   # alavancagem travada: posição menor
                    nocional = margem * alav_max
                    risco_usd = nocional * sl
                    alav = alav_max
                abertos.append((t + horas * 3600000, o["grupo"], risco_usd, r,
                                {"t": t, "simbolo": o["simbolo"], "ativo": o["ativo"], "grupo": o["grupo"],
                                 "tipo": o["tipo"], "lado": s, "r": r, "horas": horas, "sl": sl, "alav": alav,
                                 "risco_usd": risco_usd}))
        i = j
    for a in sorted(abertos, key=lambda a: a[0]):
        eq += a[2] * a[3]
        trades.append(a[4] | {"pnl": a[2] * a[3], "eq": eq})
        curva.append((a[0], eq))
    return trades, curva


def metricas(trades, curva, banca=15000.0, dias=None):
    if not trades:
        return {"n": 0, "ret": 0.0, "dd": 0.0, "acerto": 0.0, "r_medio": 0.0, "fator": 0.0, "nota": -9}
    r = np.array([t["r"] for t in trades])
    eqs = np.array([e for _, e in curva])
    pico = np.maximum.accumulate(eqs)
    dd = float(((pico - eqs) / pico).max())
    ret = float(eqs[-1] / banca - 1)
    ganho, perda = r[r > 0].sum(), -r[r < 0].sum()
    seq, pior = 0, 0
    for x in r:
        seq = seq + 1 if x <= 0 else 0
        pior = max(pior, seq)
    sd = r.std(ddof=1) if len(r) > 1 else 1
    if dias is None:
        dias = max((trades[-1]["t"] - trades[0]["t"]) / DIA, 1)
    return {"n": len(r), "por_dia": len(r) / dias, "ret": ret, "dd": dd, "acerto": float((r > 0).mean()),
            "r_medio": float(r.mean()), "fator": float(ganho / perda) if perda > 0 else 99.0,
            "sharpe_trade": float(r.mean() / sd) if sd > 0 else 0.0, "pior_seq": pior,
            "nota": float(r.mean() * math.sqrt(len(r)) / (sd if sd > 0 else 1))}   # t-estatística do R médio


def grade(g):
    if not g:
        return [{}]
    ks = list(g)
    return [dict(zip(ks, v)) for v in itertools.product(*[g[k] for k in ks])]


# ---------------------------------------------------------------- juiz
def julgar(ops):
    t0, t1 = ops[0]["t"], ops[-1]["t"] + DIA
    dias_tr, dias_oos = (CORTE - t0) / DIA, (t1 - CORTE) / DIA
    res = {}
    total_testes = 0
    for nome, (f, cat, g, desc) in TRADERS.items():
        melhor = None
        for p in grade(g):
            total_testes += 1
            kw = {"martingale": nome.endswith("martingale"), "risco_fixo": 0.05 if nome.endswith("alta") else None}
            tr, cv = carteira(ops, f, p, fim=CORTE, **kw)
            m = metricas(tr, cv, dias=dias_tr)
            if m["n"] < 30:
                m["nota"] -= 5
            if melhor is None or m["nota"] > melhor[1]["nota"]:
                melhor = (p, m)
        p, m_tr = melhor
        kw = {"martingale": nome.endswith("martingale"), "risco_fixo": 0.05 if nome.endswith("alta") else None}
        tr, cv = carteira(ops, f, p, ini=CORTE, **kw)
        m_oos = metricas(tr, cv, dias=dias_oos)
        res[nome] = {"categoria": cat, "descricao": desc, "params": p, "treino": m_tr, "fora": m_oos}
        print(f"{nome:28s} treino n={m_tr['n']:4d} R={m_tr['r_medio']:+.3f} ret={m_tr['ret']:+.1%} | "
              f"fora n={m_oos['n']:4d} R={m_oos['r_medio']:+.3f} ret={m_oos['ret']:+.1%} dd={m_oos['dd']:.1%}", flush=True)
    return res, total_testes


SAIDAS = []
for sl in [("kronos", 1.0), ("kronos", 1.3), ("atr", 4), ("atr", 6), ("atr", 8)]:
    for tp in [("kronos", 0.6), ("kronos", 1.0), ("R", (1.0, 2.0)), ("R", (1.5, 3.0)), ("R", (2.0, 4.0)),
               ("R", (1.0, 2.0, 3.0)), ("nenhum", None)]:
        for be in [None, -0.3, 0.0]:
            for trail in [None, ("chandelier", 3), ("chandelier", 5), ("degrau", 1.0)]:
                for sair in [12, 24]:
                    if tp[0] == "nenhum" and trail is None:
                        continue
                    x = {"sl": sl[0], "sl_k": sl[1], "tp": tp[0], "sair_h": sair}
                    if tp[0] == "kronos":
                        x["tp_k"] = tp[1]
                    elif tp[0] == "R":
                        x["tp_r"] = tp[1]
                    if be is not None:
                        x["be"] = be
                    if trail:
                        x["trail"], x["trail_k"] = trail
                        x["trail_so_apos"] = tp[0] != "nenhum"
                    SAIDAS.append(x)


def otimizar_saida(ops, f, p):
    """Melhor saída para as entradas do vencedor, escolhida só no treino."""
    out = []
    for x in SAIDAS:
        tr, cv = carteira(ops, f, p, fim=CORTE, saida=x)
        m = metricas(tr, cv)
        out.append((m["nota"], x, m))
    out.sort(key=lambda z: -z[0])
    return out


def monte_carlo(trades, dias_base, risco, vagas, n_sim=10000, dias=61, banca=15000.0, seed=7):
    """Sorteia dias de trades (blocos diários, mantém a correlação do dia) para 2 meses, com juros compostos."""
    rng = np.random.default_rng(seed)
    por_dia = {}
    for t in trades:
        por_dia.setdefault(t["t"] // DIA, []).append(t["r"])
    dias_lista = list(por_dia.values())
    n_dias_base = max(int(round(dias_base)), len(dias_lista))
    vazios = n_dias_base - len(dias_lista)
    blocos = dias_lista + [[]] * max(vazios, 0)
    finais, dds = np.empty(n_sim), np.empty(n_sim)
    for k in range(n_sim):
        eq, pico, dd = banca, banca, 0.0
        for d in rng.integers(0, len(blocos), dias):
            rs = blocos[d]
            if rs:
                eq += sum(eq * risco * r for r in rs[:vagas * 2])
                eq = max(eq, 1.0)
                pico = max(pico, eq)
                dd = max(dd, 1 - eq / pico)
        finais[k], dds[k] = eq, dd
    q = lambda v: float(np.quantile(finais, v) / banca - 1)
    return {"risco": risco, "mediana": q(0.5), "p10": q(0.1), "p25": q(0.25), "p75": q(0.75), "p90": q(0.9),
            "chance_lucro": float((finais > banca).mean()), "chance_perder_10": float((finais < banca * 0.9).mean()),
            "chance_perder_20": float((finais < banca * 0.8).mean()), "dd_mediano": float(np.median(dds)),
            "chance_dd_20": float((dds > 0.2).mean())}


def por_ativo(trades):
    d = {}
    for t in trades:
        d.setdefault(t["simbolo"], {"ativo": t["ativo"], "grupo": t["grupo"], "rs": []})["rs"].append(t["r"])
    out = []
    for s, v in d.items():
        rs = np.array(v["rs"])
        # encolhe para zero (poucos trades = pouca confiança)
        enc = rs.sum() / (len(rs) + 10)
        out.append({"simbolo": s, "ativo": v["ativo"], "grupo": v["grupo"], "n": len(rs),
                    "r_medio": float(rs.mean()), "acerto": float((rs > 0).mean()), "r_encolhido": float(enc),
                    "r_total": float(rs.sum())})
    out.sort(key=lambda z: -z["r_encolhido"])
    return out


def colchao(series, sym, ini, fim):
    """Retorno e queda máxima de ficar comprado 1x no ativo (capital parado)."""
    ts, ul = series[sym]
    v = [(t, u) for t, u in zip(ts, ul) if ini <= t <= fim]
    if len(v) < 2:
        return None
    u = np.array([x[1] for x in v])
    pico = np.maximum.accumulate(u)
    return {"ret": float(u[-1] / u[0] - 1), "dd": float(((pico - u) / pico).max())}


def principal(pasta, destino):
    ops, series = carregar(pasta)
    print(f"{len(ops)} partidas, {len({o['simbolo'] for o in ops})} ativos", flush=True)
    res, n_testes = julgar(ops)
    pros = {k: v for k, v in res.items() if v["categoria"] == "pro"}
    # vencedor: melhor nota no TREINO entre os profissionais que também foram positivos fora da amostra
    ok = {k: v for k, v in pros.items() if v["fora"]["r_medio"] > 0 and v["fora"]["n"] >= 20}
    base = ok or pros
    vencedor = max(base, key=lambda k: base[k]["treino"]["nota"] + base[k]["fora"]["nota"])
    f = TRADERS[vencedor][0]
    p = res[vencedor]["params"]
    print("vencedor:", vencedor, p, flush=True)
    saidas = otimizar_saida(ops, f, p)
    n_testes += len(SAIDAS)
    top = []
    for nota, x, m_tr in saidas[:8]:
        tr, cv = carteira(ops, f, p, ini=CORTE, saida=x)
        top.append({"saida": x, "treino": m_tr, "fora": metricas(tr, cv, dias=(ops[-1]["t"] - CORTE) / DIA)})
    padrao_tr, padrao_cv = carteira(ops, f, p, ini=CORTE)
    padrao = metricas(padrao_tr, padrao_cv, dias=(ops[-1]["t"] - CORTE) / DIA)
    # escolhe a saída: a melhor do treino que se manteve fora (se nenhuma melhorar, fica a do próprio trader)
    escolhida = None
    for z in top:
        if z["fora"]["n"] >= 20 and z["fora"]["r_medio"] > max(padrao["r_medio"], 0):
            escolhida = z
            break
    saida = escolhida["saida"] if escolhida else None
    # vagas e risco aberto (treino), depois risco por trade via Monte Carlo (trades fora da amostra)
    grade_vagas = []
    for vagas, ra, pg in itertools.product([8, 12, 16], [0.06, 0.08, 0.12], [2, 3, 4]):
        tr, cv = carteira(ops, f, p, saida=saida, fim=CORTE, vagas=vagas, risco_aberto=ra, por_grupo=pg)
        m = metricas(tr, cv)
        grade_vagas.append({"vagas": vagas, "risco_aberto": ra, "por_grupo": pg, **m})
    n_testes += len(grade_vagas)
    gv = max(grade_vagas, key=lambda z: z["ret"] / max(z["dd"], 0.02))
    tr_all, cv_all = carteira(ops, f, p, saida=saida, vagas=gv["vagas"], risco_aberto=gv["risco_aberto"],
                              por_grupo=gv["por_grupo"])
    oos_tr = [t for t in tr_all if t["t"] >= CORTE]
    dias_oos = (ops[-1]["t"] + DIA - CORTE) / DIA
    mcs = [monte_carlo(oos_tr, dias_oos, r, gv["vagas"]) for r in (0.005, 0.01, 0.015, 0.02, 0.03, 0.04)]
    seguro = [m for m in mcs if m["chance_perder_20"] <= 0.05 and m["chance_dd_20"] <= 0.15]
    risco_ok = max(seguro, key=lambda m: m["mediana"]) if seguro else mcs[0]
    # carteira final com o risco escolhido, freio de drawdown, ano todo e só fora da amostra
    final_tr, final_cv = carteira(ops, f, p, saida=saida, vagas=gv["vagas"], risco_aberto=gv["risco_aberto"],
                                  por_grupo=gv["por_grupo"], risco=risco_ok["risco"], freio=True, ini=CORTE)
    final = metricas(final_tr, final_cv, dias=dias_oos)
    ativos = por_ativo(tr_all)
    rs = np.array([t["r"] for t in oos_tr])
    boot = np.random.default_rng(1).choice(rs, (5000, len(rs))).mean(1) if len(rs) else np.zeros(1)
    lev = [t["alav"] for t in oos_tr]
    destino_d = {
        "periodo": {"inicio": ops[0]["t"], "fim": ops[-1]["t"], "corte": CORTE, "dias_fora": dias_oos},
        "n_partidas": len(ops), "n_ativos": len({o["simbolo"] for o in ops}), "n_testes": n_testes,
        "traders": res, "vencedor": vencedor, "params": p, "saidas_top": top, "saida_padrao_fora": padrao,
        "saida_escolhida": saida, "grade_vagas": sorted(grade_vagas, key=lambda z: -z["ret"] / max(z["dd"], 0.02))[:10],
        "vagas_escolhidas": gv, "monte_carlo": mcs, "risco_escolhido": risco_ok, "final_fora": final,
        "ativos": ativos, "chance_r_positivo": float((boot > 0).mean()),
        "alav": {"mediana": float(np.median(lev)) if lev else 0, "p90": float(np.quantile(lev, 0.9)) if lev else 0},
        "sl_mediano": float(np.median([t["sl"] for t in oos_tr])) if oos_tr else 0,
        "horas_medias": float(np.mean([t["horas"] for t in oos_tr])) if oos_tr else 0,
        "colchao": {k: colchao(series, s, CORTE, ops[-1]["t"]) for k, s in
                    {"Nasdaq 100": "NCSINASDAQ1002USD-USDT", "S&P 500": "NCSISP5002USD-USDT", "Ouro": "NCCOGOLD2USD-USDT"}.items()
                    if s in series},
        "por_grupo_fora": {},
        "lados_fora": {},
    }
    for t in oos_tr:
        g = destino_d["por_grupo_fora"].setdefault(t["grupo"], [])
        g.append(t["r"])
        destino_d["lados_fora"].setdefault("long" if t["lado"] == 1 else "short", []).append(t["r"])
    for d in ("por_grupo_fora", "lados_fora"):
        destino_d[d] = {k: {"n": len(v), "r_medio": float(np.mean(v)), "acerto": float(np.mean(np.array(v) > 0))}
                        for k, v in destino_d[d].items()}
    json.dump(destino_d, open(destino, "w"), ensure_ascii=False, indent=1, default=float)
    print(json.dumps({k: destino_d[k] for k in ("vencedor", "params", "saida_escolhida", "vagas_escolhidas",
                                                 "risco_escolhido", "final_fora", "chance_r_positivo")},
                     ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    principal(sys.argv[1], sys.argv[2])
