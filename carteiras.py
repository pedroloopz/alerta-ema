"""Engenharia reversa de carteiras vencedoras da Hyperliquid (histórico público on-chain).

1. Pega o leaderboard e escolhe as carteiras grandes e lucrativas.
2. Baixa os fills (ordens executadas) dos últimos DIAS e reconstrói cada trade (abre -> zera).
3. Perfil de cada carteira: acerto, payoff, tempo de trade, % long, stop/alvo implícitos.
4. Condições de entrada (candles de 1h): RSI, distância da EMA20, rompimento, volume,
   comparadas com a média de todas as horas (o que elas veem que o mercado não vê).
5. Teste de cópia: entra/sai na abertura do candle de 1h seguinte (atraso até 1h) com taxa
   de 0,10% ida e volta. Seleção nos primeiros (DIAS-30) dias, teste nos últimos 30 dias.

Saídas: relatorio_carteiras.txt, carteiras.json, carteiras_trades.csv.
Só leitura de dados públicos; não opera nada.
"""
import json
import math
import os
import statistics as st
import time

import requests

API = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DIAS = int(os.environ.get("DIAS", "90"))
N_CARTEIRAS = int(os.environ.get("N_CARTEIRAS", "50"))
TAXA = 0.0010            # ida e volta (taker 0,05% cada lado)
OOS_DIAS = 30            # janela fora da amostra
HORA = 3600_000


# ---------------------------------------------------------------- coleta

def info(payload, tentativas=4):
    for i in range(tentativas):
        try:
            r = requests.post(API, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))
    return None


def janela(row, nome):
    for k, v in row.get("windowPerformances", []):
        if k == nome:
            return {c: float(v.get(c) or 0) for c in ("pnl", "roi", "vlm")}
    return {"pnl": 0.0, "roi": 0.0, "vlm": 0.0}


def escolher_carteiras(rows, n):
    boas = []
    for r in rows:
        mes, tudo = janela(r, "month"), janela(r, "allTime")
        conta = float(r.get("accountValue") or 0)
        if conta < 100_000 or mes["pnl"] <= 0 or tudo["pnl"] <= 0 or mes["vlm"] < 1_000_000:
            continue
        boas.append({"carteira": r["ethAddress"], "nome": r.get("displayName") or "",
                     "conta": conta, "pnl_mes": mes["pnl"], "roi_mes": mes["roi"],
                     "pnl_total": tudo["pnl"]})
    boas.sort(key=lambda c: c["pnl_mes"], reverse=True)
    return boas[:n]


def baixar_fills(carteira, inicio_ms):
    todos, ini = [], inicio_ms
    for _ in range(5):                       # a API guarda no máx. ~10 mil fills recentes
        lote = info({"type": "userFillsByTime", "user": carteira,
                     "startTime": ini, "aggregateByTime": True}) or []
        if not lote:
            break
        todos += lote
        if len(lote) < 2000:
            break
        ini = max(f["time"] for f in lote) + 1
        time.sleep(0.4)
    return todos


def baixar_candles(coin, inicio_ms, fim_ms):
    c = info({"type": "candleSnapshot",
              "req": {"coin": coin, "interval": "1h", "startTime": inicio_ms, "endTime": fim_ms}}) or []
    return [{"t": int(x["t"]), "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]),
             "c": float(x["c"]), "v": float(x["v"])} for x in c]


# ---------------------------------------------------------------- trades

def reconstruir(fills):
    """Fills de UMA moeda -> trades (de posição zerada até zerar de novo).

    Trades que já estavam abertos no começo da janela são ignorados. Virada de mão
    (long -> short no mesmo fill) fecha um trade e abre outro no mesmo preço."""
    fills = sorted(fills, key=lambda f: f["time"])
    trades, aberto = [], None
    for f in fills:
        px, sz = float(f["px"]), float(f["sz"])
        delta = sz if f["side"] == "B" else -sz
        antes = float(f["startPosition"])
        depois = antes + delta
        tol = 1e-9 * max(1.0, abs(antes), abs(depois))
        pnl, fee = float(f.get("closedPnl") or 0), float(f.get("fee") or 0)

        if aberto is None:
            if abs(antes) > tol or abs(depois) <= tol:
                continue                     # pegou o trade no meio, ou fill solto
            aberto = {"lado": 1 if depois > 0 else -1, "t_ent": f["time"], "q_ent": abs(delta),
                      "n_ent": abs(delta) * px, "q_sai": 0.0, "n_sai": 0.0, "pnl": 0.0,
                      "fee": fee, "fills": 1, "taker": int(bool(f.get("crossed")))}
            continue

        aberto["fills"] += 1
        aberto["taker"] += int(bool(f.get("crossed")))
        virou = abs(depois) > tol and (depois > 0) != (antes > 0)
        if abs(depois) <= tol or virou:
            q = abs(antes)
            aberto["q_sai"] += q
            aberto["n_sai"] += q * px
            aberto["pnl"] += pnl
            aberto["fee"] += fee * (q / sz if sz else 1)
            aberto["t_sai"] = f["time"]
            trades.append(fechar(aberto))
            aberto = None
            if virou:
                aberto = {"lado": 1 if depois > 0 else -1, "t_ent": f["time"], "q_ent": abs(depois),
                          "n_ent": abs(depois) * px, "q_sai": 0.0, "n_sai": 0.0, "pnl": 0.0,
                          "fee": fee * (abs(depois) / sz if sz else 0), "fills": 1,
                          "taker": int(bool(f.get("crossed")))}
        elif abs(depois) > abs(antes):       # aumentou a posição
            aberto["q_ent"] += abs(delta)
            aberto["n_ent"] += abs(delta) * px
            aberto["fee"] += fee
        else:                                # reduziu parte
            aberto["q_sai"] += abs(delta)
            aberto["n_sai"] += abs(delta) * px
            aberto["pnl"] += pnl
            aberto["fee"] += fee
    return trades


def fechar(a):
    p_ent = a["n_ent"] / a["q_ent"]
    p_sai = a["n_sai"] / a["q_sai"] if a["q_sai"] else p_ent
    return {"lado": a["lado"], "t_ent": a["t_ent"], "t_sai": a["t_sai"],
            "p_ent": p_ent, "p_sai": p_sai,
            "ret": a["lado"] * (p_sai / p_ent - 1),           # % do preço, sem alavancagem
            "pnl": a["pnl"] - a["fee"], "notional": a["n_ent"],
            "horas": (a["t_sai"] - a["t_ent"]) / HORA, "fills": a["fills"],
            "taker": a["taker"] / a["fills"]}


# ---------------------------------------------------------------- indicadores

def indicadores(cs):
    """Por candle i (fechado): rsi14, dist_ema20, ret24, rompimento (+1/-1/0), vol_ratio, atr%."""
    out, ema, ganho, perda = [], None, None, None
    for i, c in enumerate(cs):
        ema = c["c"] if ema is None else ema + (c["c"] - ema) * 2 / 21
        if i:
            d = c["c"] - cs[i - 1]["c"]
            g, p = max(d, 0), max(-d, 0)
            ganho = g if ganho is None else (ganho * 13 + g) / 14
            perda = p if perda is None else (perda * 13 + p) / 14
        if i < 30:
            out.append(None)
            continue
        ant = cs[i - 24:i]
        rsi = 100.0 if perda == 0 else 100 - 100 / (1 + ganho / perda)
        trs = [max(x["h"], cs[j - 1]["c"]) - min(x["l"], cs[j - 1]["c"])
               for j, x in zip(range(i - 13, i + 1), cs[i - 13:i + 1])]
        vm = st.mean(x["v"] for x in ant) or 1e-12
        out.append({"rsi": rsi, "dist_ema": c["c"] / ema - 1,
                    "ret24": c["c"] / cs[i - 24]["c"] - 1,
                    "romp": 1 if c["c"] > max(x["h"] for x in ant) else
                            (-1 if c["c"] < min(x["l"] for x in ant) else 0),
                    "vol": c["v"] / vm, "atr": st.mean(trs) / c["c"]})
    return out


def idx_antes(cs, t):
    """Último candle de 1h já FECHADO antes de t."""
    lo, hi, r = 0, len(cs) - 1, -1
    while lo <= hi:
        m = (lo + hi) // 2
        if cs[m]["t"] + HORA <= t:
            r, lo = m, m + 1
        else:
            hi = m - 1
    return r


def idx_depois(cs, t):
    """Primeiro candle que ABRE depois de t (é onde a cópia entra)."""
    lo, hi, r = 0, len(cs) - 1, None
    while lo <= hi:
        m = (lo + hi) // 2
        if cs[m]["t"] > t:
            r, hi = m, m - 1
        else:
            lo = m + 1
    return r


def copiar(trade, cs):
    i, j = idx_depois(cs, trade["t_ent"]), idx_depois(cs, trade["t_sai"])
    if i is None or j is None or j <= i:
        return None                          # saiu antes da cópia conseguir entrar
    return trade["lado"] * (cs[j]["o"] / cs[i]["o"] - 1) - TAXA


# ---------------------------------------------------------------- relatório

def pct(x):
    return f"{x * 100:+.2f}%".replace(".", ",")


def num(x, casas=1):
    return f"{x:.{casas}f}".replace(".", ",")


def resumo_trades(ts):
    if not ts:
        return None
    gan = [t["ret"] for t in ts if t["ret"] > 0]
    per = [t["ret"] for t in ts if t["ret"] <= 0]
    return {"n": len(ts), "acerto": len(gan) / len(ts),
            "ganho_med": st.median(gan) if gan else 0.0,
            "perda_med": st.median(per) if per else 0.0,
            "payoff": (st.mean(gan) / -st.mean(per)) if gan and per and st.mean(per) < 0 else None,
            "horas_med": st.median(t["horas"] for t in ts),
            "long": sum(t["lado"] > 0 for t in ts) / len(ts),
            "taker": st.mean(t["taker"] for t in ts),
            "pnl": sum(t["pnl"] for t in ts)}


def main():
    agora = int(time.time() * 1000)
    inicio = agora - DIAS * 24 * HORA
    corte = agora - OOS_DIAS * 24 * HORA

    rows = requests.get(LEADERBOARD, timeout=60).json()
    rows = rows.get("leaderboardRows", rows) if isinstance(rows, dict) else rows
    escolhidas = escolher_carteiras(rows, N_CARTEIRAS)
    print(f"leaderboard: {len(rows)} contas, {len(escolhidas)} escolhidas")

    trades = []
    for k, c in enumerate(escolhidas, 1):
        try:
            fills = baixar_fills(c["carteira"], inicio)
        except Exception as e:
            print(f"  {c['carteira'][:10]} falhou: {e}")
            continue
        por_coin = {}
        for f in fills:
            if f["coin"].startswith("@") or "/" in f["coin"]:
                continue                     # spot
            por_coin.setdefault(f["coin"], []).append(f)
        n = 0
        for coin, fs in por_coin.items():
            for t in reconstruir(fs):
                t.update(carteira=c["carteira"], coin=coin)
                trades.append(t)
                n += 1
        c["fills"] = len(fills)
        print(f"  [{k}/{len(escolhidas)}] {c['carteira'][:10]} fills={len(fills)} trades={n}")
        time.sleep(0.4)

    # robô / market maker: trades curtos demais pra copiar na mão
    perfis = {}
    for c in escolhidas:
        ts = [t for t in trades if t["carteira"] == c["carteira"]]
        r = resumo_trades(ts)
        if not r:
            continue
        r.update(c)
        r["tipo"] = ("robô/MM" if r["horas_med"] < 0.25 or r["n"] > 2500 else
                     "scalper" if r["horas_med"] < 2 else "swing/day")
        r["coins"] = sorted({t["coin"] for t in ts}, key=lambda x: -sum(t["coin"] == x for t in ts))[:5]
        perfis[c["carteira"]] = r

    copiaveis = {k for k, p in perfis.items() if p["tipo"] != "robô/MM"}
    ts_cop = [t for t in trades if t["carteira"] in copiaveis]
    contagem = {}
    for t in ts_cop:
        contagem[t["coin"]] = contagem.get(t["coin"], 0) + 1
    coins = [c for c, n in sorted(contagem.items(), key=lambda x: -x[1]) if n >= 5][:80]
    print(f"carteiras copiáveis: {len(copiaveis)}; moedas p/ candles: {len(coins)}")

    candles, ind = {}, {}
    for coin in coins:
        try:
            cs = baixar_candles(coin, inicio - 40 * HORA, agora)
        except Exception as e:
            print(f"  candles {coin} falhou: {e}")
            continue
        if len(cs) > 60:
            candles[coin], ind[coin] = cs, indicadores(cs)
        time.sleep(0.3)

    # condições de entrada x média do mercado
    feats = {"long": [], "short": []}
    base = []
    for coin, lst in ind.items():
        base += [x for x in lst if x]
    for t in ts_cop:
        if t["coin"] not in candles:
            continue
        i = idx_antes(candles[t["coin"]], t["t_ent"])
        x = ind[t["coin"]][i] if i >= 0 else None
        if x:
            t["feat"] = x
            feats["long" if t["lado"] > 0 else "short"].append((x, t))
        t["copia"] = copiar(t, candles[t["coin"]])

    # seleção dentro da amostra e teste fora dela (carteira a carteira)
    sel, oos = [], []
    for k in copiaveis:
        dentro = [t["copia"] for t in ts_cop if t["carteira"] == k and t.get("copia") is not None
                  and t["t_ent"] < corte]
        fora = [t["copia"] for t in ts_cop if t["carteira"] == k and t.get("copia") is not None
                and t["t_ent"] >= corte]
        p = perfis[k]
        p["copia_dentro"] = (st.mean(dentro), len(dentro)) if dentro else None
        p["copia_fora"] = (st.mean(fora), len(fora)) if fora else None
        if len(dentro) >= 15 and st.mean(dentro) > 0:
            sel.append(k)
            oos += fora
    todas_fora = [t["copia"] for t in ts_cop if t.get("copia") is not None and t["t_ent"] >= corte]

    L = []
    L.append(f"ENGENHARIA REVERSA DE CARTEIRAS — Hyperliquid — gerado {time.strftime('%d/%m/%Y %H:%M', time.gmtime(agora / 1000 - 3 * 3600))} (Brasília)")
    L.append(f"Janela: {DIAS} dias | teste fora da amostra: últimos {OOS_DIAS} dias | taxa da cópia: 0,10% ida e volta")
    L.append(f"Carteiras analisadas: {len(perfis)} | robôs/MM (não copiáveis): {len(perfis) - len(copiaveis)} | trades reconstruídos: {len(trades)}")
    L.append("")
    L.append("== 1. QUEM SÃO (copiáveis, por PnL do mês) ==")
    for k in sorted(copiaveis, key=lambda k: -perfis[k]["pnl_mes"])[:25]:
        p = perfis[k]
        cd, cf = p["copia_dentro"], p["copia_fora"]
        L.append(f"{k[:10]} {p['tipo']:9} n={p['n']:4} acerto={num(p['acerto']*100,0)}% "
                 f"payoff={num(p['payoff'],2) if p['payoff'] else '-'} hold={num(p['horas_med'])}h "
                 f"long={num(p['long']*100,0)}% ganho_med={pct(p['ganho_med'])} perda_med={pct(p['perda_med'])} "
                 f"| cópia dentro={pct(cd[0]) + ' n=' + str(cd[1]) if cd else '-'} "
                 f"fora={pct(cf[0]) + ' n=' + str(cf[1]) if cf else '-'} | {','.join(p['coins'])}")
    L.append("")
    L.append("== 2. O QUE ELAS VEEM NA ENTRADA (1h, candle fechado antes da entrada) ==")
    def bloco(nome, lst):
        if not lst:
            return
        xs = [x for x, _ in lst]
        win = [x for x, t in lst if t["ret"] > 0]
        L.append(f"{nome}: {len(xs)} entradas (vencedoras {len(win)})")
        for chave, rot in (("rsi", "RSI14"), ("dist_ema", "dist. EMA20"), ("ret24", "retorno 24h"),
                           ("vol", "volume/média24"), ("atr", "ATR%")):
            f = (lambda v: num(v, 1)) if chave in ("rsi", "vol") else pct
            L.append(f"   {rot:15} todas={f(st.median(x[chave] for x in xs))} "
                     f"vencedoras={f(st.median(x[chave] for x in win)) if win else '-'} "
                     f"mercado={f(st.median(b[chave] for b in base))}")
        alvo = 1 if nome.startswith("LONG") else -1
        L.append(f"   rompimento 24h a favor: entradas={num(100*sum(x['romp']==alvo for x in xs)/len(xs),0)}% "
                 f"mercado={num(100*sum(b['romp']==alvo for b in base)/len(base),0)}%")
        L.append(f"   contra a tendência 24h: {num(100*sum((x['ret24']*alvo)<0 for x in xs)/len(xs),0)}%")
    bloco("LONG", feats["long"])
    bloco("SHORT", feats["short"])
    L.append("")
    L.append("== 3. COPIAR DÁ DINHEIRO? (entra/sai no candle 1h seguinte, -0,10%) ==")
    def linha(rot, xs):
        if xs:
            L.append(f"{rot}: n={len(xs)} média={pct(st.mean(xs))} mediana={pct(st.median(xs))} "
                     f"acerto={num(100*sum(x>0 for x in xs)/len(xs),0)}%")
        else:
            L.append(f"{rot}: sem trades")
    linha("Todas as copiáveis, últimos 30d", todas_fora)
    linha(f"Só as {len(sel)} aprovadas antes (cópia lucrativa nos 60d), testadas nos 30d", oos)
    L.append("Aprovadas: " + ", ".join(k[:10] for k in sel))
    L.append("")
    L.append("Retornos são % do preço (sem alavancagem). Backtest não garante resultado futuro.")

    texto = "\n".join(L)
    print(texto)
    open("relatorio_carteiras.txt", "w", encoding="utf-8").write(texto + "\n")
    json.dump({"gerado": agora, "aprovadas": sel, "perfis": perfis}, open("carteiras.json", "w"),
              indent=1, default=str)
    with open("carteiras_trades.csv", "w", encoding="utf-8") as f:
        cols = ["carteira", "coin", "lado", "t_ent", "t_sai", "p_ent", "p_sai", "ret", "pnl",
                "horas", "copia", "rsi", "dist_ema", "ret24", "romp", "vol", "atr"]
        f.write(",".join(cols) + "\n")
        for t in ts_cop:
            x = t.get("feat") or {}
            v = [t["carteira"], t["coin"], t["lado"], t["t_ent"], t["t_sai"], t["p_ent"], t["p_sai"],
                 t["ret"], t["pnl"], t["horas"], t.get("copia")] + [x.get(c) for c in cols[11:]]
            f.write(",".join("" if a is None else (f"{a:.6g}" if isinstance(a, float) else str(a))
                             for a in v) + "\n")


if __name__ == "__main__":
    main()
