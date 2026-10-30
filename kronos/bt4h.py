"""Backtest de 6 meses do setup ao vivo do robô (30/10/2026) sobre previsões reais do Kronos-small em velas de 4h.

Base: branch kronos-dados-4h (dados_teste.py com KRONOS_INTERVALO=4h): por partida, o resumo dos caminhos e as
24 velas de 4h reais que vieram depois (96h), mais o funding real. Só resultado em R (sem dinheiro).

Setup (o mesmo do robo-bingx): lado pela maioria dos caminhos; entra só com p_dir mínimo e mediana andando 0,4%+;
ordem limite no recuo mediano previsto, vale 3 velas (12h); stop = faixa de 15% dos caminhos (0,8% a 5%) × 1,5
(teto 8%); sem alvo e sem stop móvel; corte da faca (vela da entrada fechou 1,5%+ contra); sai no fechamento da
última vela do prazo; fora cripto, sai antes do fim de semana (sexta 15h de Nova York) e não entra a menos de 24h
dele. Um trade por ativo de cada vez. Custo 0,07% + funding real.

  python kronos/bt4h.py <pasta com dados/*.json.gz> <saida.json>
"""
import bisect
import datetime
import glob
import gzip
import itertools
import json
import os
import sys
from zoneinfo import ZoneInfo

CUSTO = 0.0007
MOVE_MIN = 0.004
SL_MIN, SL_MAX = 0.008, 0.05
VELA_MS = 4 * 3600 * 1000
NY = ZoneInfo("America/New_York")
PRINCIPAL = {"p_min": 0.995, "sl_mult": 1.5, "velas": 24, "faca": 0.015}


def corte_fds(t_ms):
    agora = datetime.datetime.fromtimestamp(t_ms / 1000, datetime.timezone.utc)
    ny = agora.astimezone(NY)
    sexta = ny.date() - datetime.timedelta(days=(ny.weekday() - 4) % 7)
    corte = datetime.datetime.combine(sexta, datetime.time(15, 0), NY)
    if ny > corte + datetime.timedelta(days=2, hours=12):
        corte += datetime.timedelta(days=7)
    return int(corte.timestamp() * 1000)


def fds_perto(t_ms, horas=24):
    c = corte_fds(t_ms)
    return c - horas * 3600_000 <= t_ms <= c + int(2.5 * 86400_000)


def carregar(pasta):
    ativos = []
    for f in sorted(glob.glob(os.path.join(pasta, "dados", "*.json.gz"))):
        d = json.load(gzip.open(f))
        if not d.get("partidas"):
            continue
        fund = d.get("funding") or []
        d["_f_ts"] = [x[0] for x in fund]
        d["_f_ac"] = list(itertools.accumulate([x[1] for x in fund], initial=0.0))
        ativos.append(d)
    return ativos


def funding(d, ini, fim):
    """Soma das taxas de funding entre ini e fim (positivo = long paga)."""
    ts, ac = d["_f_ts"], d["_f_ac"]
    if not ts:
        return 0.0
    return ac[bisect.bisect_right(ts, fim)] - ac[bisect.bisect_right(ts, ini)]


def trade(d, p, cfg):
    """Um trade (ou None). t = abertura da última vela do contexto; a 1ª vela futura abre em t + 4h."""
    lado = "LONG" if p["p_alta"] >= 0.5 else "SHORT"
    s = 1 if lado == "LONG" else -1
    p_dir = p["p_alta"] if s == 1 else 1 - p["p_alta"]
    if p_dir < cfg["p_min"] or s * p["ret_mediano"] < MOVE_MIN:
        return None
    t0 = p["t"] + VELA_MS                                  # hora do sinal (vela fechou)
    cripto = d["tipo"] == "cripto"
    if not cripto and fds_perto(t0):
        return None
    sl = min(max(p["queda_q15"] if s == 1 else p["alta_q15"], SL_MIN), SL_MAX)
    sl = min(sl * cfg["sl_mult"], 0.08)
    rc = max(((p.get("recuo") or {}).get(lado) or {}).get("q50", 0), 0.001)
    ent = 1 - s * rc                                       # preços relativos ao último fechamento
    velas = p["reais"]
    n = min(cfg["velas"], len(velas))
    i0 = next((i for i, v in enumerate(velas[:3]) if (1 + v[2] <= ent if s == 1 else 1 + v[1] >= ent)), None)
    if i0 is None:
        return None
    corte = corte_fds(t0) if not cripto else None
    saida, como, i_fim = None, "prazo", n - 1
    for i in range(i0, n):
        ini_v = t0 + i * VELA_MS
        if corte and ini_v + VELA_MS > corte:              # a vela passa do corte de sexta: sai no fechamento anterior
            if i == i0:
                return None
            saida, como, i_fim = 1 + velas[i - 1][3], "fds", i - 1
            break
        o, h, l, c = (1 + x for x in velas[i])
        fav = s * ((h if s == 1 else l) / ent - 1)
        con = s * ((l if s == 1 else h) / ent - 1)
        if i == i0:                                        # na vela da entrada só vale o fechamento
            con = min(s * (c / ent - 1), 0.0)
            if con <= -sl:
                saida, como, i_fim = ent * (1 - s * sl), "stop", i
                break
            if cfg.get("faca") and con <= -cfg["faca"]:
                saida, como, i_fim = c, "faca", i
                break
            continue
        if con <= -sl:
            saida, como, i_fim = ent * (1 - s * sl), "stop", i
            break
    if saida is None:
        saida = 1 + velas[n - 1][3]
    ini_ms, fim_ms = t0 + i0 * VELA_MS, t0 + (i_fim + 1) * VELA_MS
    fu = funding(d, ini_ms, fim_ms) * s                    # long paga funding positivo
    r = (s * (saida / ent - 1) - CUSTO - fu) / sl
    return {"ativo": d["ativo"].split(" (")[0], "simbolo": d["simbolo"], "tipo": d["tipo"], "lado": lado,
            "t": ini_ms, "fim": fim_ms, "r": round(r, 4), "como": como, "sl": round(sl, 4), "p_dir": round(p_dir, 3)}


def simular(ativos, cfg):
    out = []
    for d in ativos:
        livre = 0
        for p in d["partidas"]:
            if p["t"] + VELA_MS < livre:                   # um trade por ativo de cada vez
                continue
            x = trade(d, p, cfg)
            if x:
                out.append(x)
                livre = x["fim"]
    return sorted(out, key=lambda x: x["t"])


def resumo(tr):
    if not tr:
        return {"n": 0}
    rs = [x["r"] for x in tr]
    g, p = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    acum = pico = dd = 0.0
    for r in rs:
        acum += r
        pico = max(pico, acum)
        dd = max(dd, pico - acum)
    lados = {l: round(sum(x["r"] for x in tr if x["lado"] == l), 2) for l in ("LONG", "SHORT")}
    return {"n": len(rs), "acerto": round(sum(r > 0 for r in rs) / len(rs), 3), "r_medio": round(sum(rs) / len(rs), 3),
            "r_total": round(sum(rs), 2), "pf": round(g / p, 2) if p else None, "dd_r": round(dd, 2),
            "longs_r": lados["LONG"], "shorts_r": lados["SHORT"],
            "saidas": {c: sum(1 for x in tr if x["como"] == c) for c in ("stop", "prazo", "faca", "fds")}}


def carteira(tr, risco=0.01, max_abertos=8):
    """Juros compostos com risco fixo em % e no máximo `max_abertos` ao mesmo tempo (teto de 8% de risco)."""
    banca, abertos, curva, pico, dd = 1.0, [], [], 1.0, 0.0
    for x in tr:
        for a in [a for a in abertos if a["fim"] <= x["t"]]:
            banca += a["risco"] * a["r"]
            abertos.remove(a)
        if len(abertos) >= max_abertos:
            continue
        abertos.append({**x, "risco": banca * risco})
        pico = max(pico, banca)
        dd = max(dd, 1 - banca / pico)
    for a in abertos:
        banca += a["risco"] * a["r"]
    return {"retorno": round(banca - 1, 4), "dd_max": round(max(dd, 1 - banca / max(pico, banca)), 4)}


def por_ativo(tr, corte_ms):
    out = {}
    for x in tr:
        a = out.setdefault(x["ativo"], {"ativo": x["ativo"], "tipo": x["tipo"], "n": 0, "r": 0.0, "ganhos": 0,
                                        "r_antes": 0.0, "n_antes": 0, "r_depois": 0.0, "n_depois": 0})
        a["n"] += 1
        a["r"] = round(a["r"] + x["r"], 3)
        a["ganhos"] += x["r"] > 0
        k = "antes" if x["t"] < corte_ms else "depois"
        a[f"r_{k}"] = round(a[f"r_{k}"] + x["r"], 3)
        a[f"n_{k}"] += 1
    for a in out.values():
        a["r_medio"] = round(a["r"] / a["n"], 3)
        a["previsivel"] = a["n"] >= 5 and a["r_medio"] > 0 and a["r_antes"] > 0 and a["r_depois"] > 0
    return sorted(out.values(), key=lambda a: -a["r"])


def principal(pasta, destino):
    ativos = carregar(pasta)
    todos_t = [p["t"] for d in ativos for p in d["partidas"]]
    corte = min(todos_t) + (max(todos_t) - min(todos_t)) * 2 // 3 if todos_t else 0   # 4 meses × 2 últimos
    variantes = []
    for p_min, sl_mult, velas, faca in itertools.product((0.995, 0.9), (1.5, 1.0), (24, 12), (0.015, 0)):
        cfg = {"p_min": p_min, "sl_mult": sl_mult, "velas": velas, "faca": faca}
        tr = simular(ativos, cfg)
        variantes.append({**cfg, **resumo(tr), "carteira_1pct": carteira(tr)})
    tr = simular(ativos, PRINCIPAL)
    tr90 = simular(ativos, dict(PRINCIPAL, p_min=0.9))
    meses = {}
    for x in tr:
        m = datetime.datetime.fromtimestamp(x["t"] / 1000, datetime.timezone.utc).strftime("%Y-%m")
        meses[m] = round(meses.get(m, 0.0) + x["r"], 2)
    ranking = por_ativo(tr90, corte)                       # 90%+ para ter amostra por ativo; o principal ao lado
    r100 = {a["ativo"]: a for a in por_ativo(tr, corte)}
    for a in ranking:
        b = r100.get(a["ativo"]) or {}
        a["n_100"], a["r_100"] = b.get("n", 0), b.get("r", 0.0)
    out = {"gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "ativos": len(ativos), "partidas": len(todos_t),
           "periodo": [datetime.datetime.fromtimestamp(min(todos_t) / 1000, datetime.timezone.utc).date().isoformat(),
                       datetime.datetime.fromtimestamp(max(todos_t) / 1000, datetime.timezone.utc).date().isoformat()]
           if todos_t else None,
           "corte_fora_da_amostra": datetime.datetime.fromtimestamp(corte / 1000, datetime.timezone.utc).date().isoformat()
           if todos_t else None,
           "principal": {**PRINCIPAL, **resumo(tr), "carteira_1pct": carteira(tr), "por_mes_r": meses},
           "variantes": sorted(variantes, key=lambda v: -(v.get("r_total") or -1e9)),
           "previsiveis": [a["ativo"] for a in ranking if a["previsivel"]],
           "por_ativo": ranking}
    json.dump(out, open(destino, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("ativos", "partidas", "periodo", "principal", "previsiveis")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    principal(sys.argv[1], sys.argv[2])
