"""Teste de CARTEIRA: swing comprado/vendido misturando sistemas, como o robô faria, desde 2020.

Sistemas (cripto e ações juntos):
- Donchian 55/10 (diário)            — tendência, semanas
- RSI(2) de Connors (diário)         — volta à média, ~3–5 dias, stop de 2,5x ATR
- Ichimoku semanal                   — tendência longa
- Momento semanal (26 semanas)       — tendência longa (Moskowitz, Ooi e Pedersen)
Filtro de mercado: long só com BTC (cripto) / SPY (ações) acima da média de 200 dias;
short (só Donchian e RSI(2)) só com o mercado abaixo dela.

Carteira: no máximo 10 posições; risco fixo por trade (1%, 2% ou 3% do saldo) sobre a distância do stop;
custo de 0,3% por trade; funding real da Binance nas criptos (arquivos públicos) e, nas ações, a média
do funding das criptos no mesmo dia (suposição: a BingX não publica o histórico das ações).
Relatório: relatorio_carteira.txt (ano a ano, mês a mês, pior queda, por sistema). Não mexe no robô.
"""
import argparse
import datetime
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import teste_longo as TL  # noqa: E402

AQUI = os.path.dirname(__file__)
SAIDA = os.path.join(AQUI, "..", "relatorio_carteira.txt")
INICIO = pd.Timestamp("2020-01-01", tz="UTC")
CUSTO = 0.003
VAGAS = 10
MAX_POSICAO = 1.0      # cada posição: no máximo 1x o saldo em valor (stop curto não vira alavancagem enorme)
MAX_TOTAL = 5.0        # todas juntas: no máximo 5x o saldo (cabe na margem isolada da BingX)
RISCOS = (0.01, 0.02, 0.03)
CENARIOS = ((0.03, 10), (0.03, 15), (0.02, 10), (0.01, 10))     # (risco por trade, vagas)


# ------------------------------------------------------------------ dados
def diario_cripto(par, meses):
    d = TL.klines_binance(par, meses, "1d")
    return None if d is None else d[["open", "high", "low", "close"]].astype("float64")


def funding_cripto(par, meses):
    partes = []
    for mes in meses:
        c = TL.baixar(f"{TL.BV}/monthly/fundingRate/{par}/{par}-fundingRate-{mes}.zip")
        if not c:
            continue
        d = TL.ler_zip(c)
        col_t = [x for x in d.columns if "time" in str(x)]
        col_r = [x for x in d.columns if "rate" in str(x)]
        if not col_t or not col_r:
            continue
        t = pd.to_numeric(d[col_t[0]], errors="coerce")
        r = pd.to_numeric(d[col_r[0]], errors="coerce")
        partes.append(pd.Series(r.to_numpy(), index=pd.to_datetime(t, unit="ms", utc=True)))
    if not partes:
        return None
    s = pd.concat(partes).dropna()
    return s.groupby(s.index.normalize()).sum()          # soma do dia (positivo = comprado paga)


def diario_acao(nome):
    import requests
    for t in dict.fromkeys([nome, nome[:-2] if nome.endswith("US") else nome]):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}",
                             params={"period1": int(pd.Timestamp("2019-01-01").timestamp()),
                                     "period2": int(time.time()), "interval": "1d"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            d = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")},
                             index=pd.to_datetime(res["timestamp"], unit="s", utc=True).normalize()).dropna()
            if len(d) > 300:
                return d[~d.index.duplicated()].astype("float64")
        except Exception:
            pass
    return None


# ------------------------------------------------------------------ sistemas -> trades
def semanal(d):
    """Candles semanais com o índice no último dia com negociação da semana."""
    sem = d.index.tz_convert(None).to_period("W-SUN")
    w = d.groupby(sem).agg({"high": "max", "low": "min", "close": "last"})
    w.index = pd.DatetimeIndex(d.index.to_series().groupby(sem).max().to_numpy())
    return w


def regime(close_mercado, tipo="200 dias"):
    """Mercado em alta? '200 dias' (diário) ou 'N semanas' (média semanal, vale a partir do fechamento da semana)."""
    if tipo == "200 dias":
        return close_mercado > close_mercado.rolling(200).mean()
    n = int(tipo.split()[0])
    w = semanal(pd.DataFrame({"high": close_mercado, "low": close_mercado, "close": close_mercado}))["close"]
    return (w > w.rolling(n).mean()).reindex(close_mercado.index, method="ffill").fillna(False)


REGIMES = ("200 dias", "40 semanas", "20 semanas")


def atr(d, n=14):
    return (d["high"] - d["low"]).rolling(n).mean()


def trades_ativo(d, alta, classe, nome):
    """Lista de trades possíveis do ativo (antes da carteira escolher). alta = mercado em alta (bool por dia)."""
    out = []
    c, h, l = d["close"], d["high"], d["low"]
    alta = alta.reindex(d.index, method="ffill").fillna(False).astype(bool)
    A = atr(d)

    def rodar(sist, lado, entra, sai, stop0, max_dias=None, saida_em=None):
        ent, sai_ = entra.to_numpy(bool), sai.to_numpy(bool)
        st = stop0.to_numpy()
        cc, hh, ll, tt = c.to_numpy(), h.to_numpy(), l.to_numpy(), d.index
        i = 0
        while i < len(cc) - 1:
            if not ent[i] or tt[i] < INICIO or not np.isfinite(st[i]) or st[i] <= 0:
                i += 1
                continue
            e, s0 = cc[i], st[i]
            dist = abs(e - s0) / e
            if dist < 0.005 or dist > 0.5:
                i += 1
                continue
            j, px = i + 1, None
            while j < len(cc):
                if (lado == 1 and ll[j] <= s0) or (lado == -1 and hh[j] >= s0):
                    px = s0
                elif sai_[j] or (max_dias and (tt[j] - tt[i]).days >= max_dias):
                    px = cc[j]
                if px is not None:
                    break
                j += 1
            if px is None:
                break                                        # ainda aberto
            out.append({"sist": sist, "classe": classe, "ativo": nome, "lado": lado, "ent": tt[i], "sai": tt[j],
                        "ret": lado * (px / e - 1), "dist": dist})
            i = j + 1

    # Donchian 55/10
    topo, fundo = h.rolling(55).max().shift(), l.rolling(55).min().shift()
    min10, max10 = l.rolling(10).min().shift(), h.rolling(10).max().shift()
    rodar("Donchian", 1, (c > topo) & alta, c < min10, min10)
    rodar("Donchian", -1, (c < fundo) & ~alta, c > max10, max10)
    # RSI(2)
    r2 = TL._rsi(c, 2)
    ma200, ma5 = c.rolling(200).mean(), c.rolling(5).mean()
    rodar("RSI(2)", 1, (c > ma200) & (r2 < 10) & alta, c > ma5, c - 2.5 * A, max_dias=10)
    rodar("RSI(2)", -1, (c < ma200) & (r2 > 90) & ~alta, c < ma5, c + 2.5 * A, max_dias=10)
    # semanais (sinal no fechamento da semana, levado ao diário)
    w = semanal(d)                                                 # índice = último dia com pregão
    wc, wh, wl = w["close"], w["high"], w["low"]
    tenkan = (wh.rolling(9).max() + wl.rolling(9).min()) / 2
    kijun = (wh.rolling(26).max() + wl.rolling(26).min()) / 2
    nuvem = np.maximum(((tenkan + kijun) / 2).shift(26), ((wh.rolling(52).max() + wl.rolling(52).min()) / 2).shift(26))
    compra = (wc > nuvem) & (tenkan > kijun)
    ichi_ent = compra & ~compra.shift(fill_value=False)
    mom_ent = (wc / wc.shift(26) - 1 > 0) & (wc > wc.rolling(10).mean())
    mom_ent = mom_ent & ~mom_ent.shift(fill_value=False)

    def para_dia(s):
        return s.reindex(d.index, method="ffill").fillna(0)

    so_fech = pd.Series(d.index.isin(w.index), index=d.index)   # só no dia do fechamento semanal
    kij_d = para_dia(kijun)
    ma10_d = para_dia(wc.rolling(10).mean())
    rodar("Ichimoku sem.", 1, para_dia(ichi_ent).astype(bool) & so_fech & alta, (c < kij_d) & so_fech, kij_d)
    rodar("Momento sem.", 1, para_dia(mom_ent).astype(bool) & so_fech & alta, (c < ma10_d) & so_fech,
          c - 3 * A)
    return out


# ------------------------------------------------------------------ carteira
def carteira(trades, funding, risco, vagas=VAGAS):
    """Escolhe os trades em ordem de data, até `vagas` abertos; resultado realizado na saída."""
    trades = sorted(trades, key=lambda x: (x["ent"], x["sist"], x["ativo"]))
    saldo, abertos, fechados = 7000.0, [], []
    curva = []
    for t in trades:
        abertos.sort(key=lambda x: x["sai"])
        while abertos and abertos[0]["sai"] <= t["ent"]:
            x = abertos.pop(0)
            saldo += x["pnl"]
            fechados.append(x)
            curva.append((x["sai"], saldo))
        if len(abertos) >= vagas or any(a["ativo"] == t["ativo"] for a in abertos):
            continue
        livre = saldo * MAX_TOTAL - sum(a["nocional"] for a in abertos)
        nocional = min(saldo * risco / t["dist"], saldo * MAX_POSICAO, livre)
        if nocional < saldo * 0.02:
            continue
        risco_us = nocional * t["dist"]
        f = funding(t)                                           # soma do funding no período (fração)
        ret = t["ret"] - CUSTO - t["lado"] * f
        x = dict(t, nocional=nocional, pnl=nocional * ret, risco_us=risco_us, funding=nocional * t["lado"] * f)
        abertos.append(x)
    for x in sorted(abertos, key=lambda x: x["sai"]):
        saldo += x["pnl"]
        fechados.append(x)
        curva.append((x["sai"], saldo))
    return fechados, pd.Series([s for _, s in curva], index=[t for t, _ in curva]).sort_index()


def resumo_curva(curva, titulo, extra=""):
    if curva.empty:
        return [f"{titulo}: sem dados"]
    final = curva.iloc[-1] / curva.iloc[0] * 7000
    anos = max((curva.index[-1] - curva.index[0]).days / 365.25, 0.1)
    dd = float((1 - curva / curva.cummax()).max())
    anual = curva.resample("YE").last()
    anual = pd.concat([curva.iloc[:1], anual]).pct_change().dropna()
    return [f"{titulo}: US$ 7.000 -> US$ {final:,.0f} · {((final / 7000) ** (1 / anos) - 1):+.0%} ao ano · "
            f"pior queda {dd:.0%}{extra}",
            "  por ano: " + " · ".join(f"{t.year} {v:+.0%}" for t, v in anual.items())]


def tendencia_alavancada(series, alav, fund_med, tipo="40 semanas"):
    """BTC, ETH e QQQ em partes iguais, comprados só com o próprio preço acima da média semanal; alavancagem fixa."""
    rets = []
    for nome, c in series.items():
        dentro = regime(c, tipo).shift(1, fill_value=False)          # decide no fechamento, vale no dia seguinte
        r = c.pct_change().fillna(0)
        f = fund_med.reindex(c.index).fillna(0)
        troca = dentro.astype(int).diff().abs().fillna(0)
        rets.append((dentro * alav * (r - f) - troca * CUSTO * alav).rename(nome))
    R = pd.concat(rets, axis=1).fillna(0)
    R = R[R.index >= INICIO]
    return (1 + R.mean(axis=1)).cumprod() * 7000


def resumo_carteira(fechados, curva, risco, vagas=VAGAS):
    if curva.empty:
        return [f"risco {risco:.0%}: sem trades"]
    final = curva.iloc[-1]
    anos = max((curva.index[-1] - INICIO).days / 365.25, 0.1)
    pico = curva.cummax()
    dd = float((1 - curva / pico).max())
    mensal = curva.resample("ME").last().ffill()
    mensal = pd.concat([pd.Series([7000.0], index=[INICIO]), mensal])
    rm = mensal.pct_change().dropna()
    L = [f"-- risco {risco:.0%} por trade, até {vagas} posições (risco aberto máx. {risco * vagas:.0%}) --",
         f"US$ 7.000 -> US$ {final:,.0f} em {anos:.1f} anos · {((final / 7000) ** (1 / anos) - 1):+.0%} ao ano · "
         f"média {rm.mean():+.1%}/mês · meses positivos {int((rm > 0).sum())}/{len(rm)} · pior mês {rm.min():+.0%} · "
         f"pior queda {dd:.0%} · funding pago US$ {sum(x['funding'] for x in fechados):,.0f}"]
    anual = pd.concat([pd.Series([7000.0], index=[INICIO]), curva.resample("YE").last().ffill()]).pct_change().dropna()
    L.append("  por ano: " + " · ".join(f"{t.year} {v:+.0%}" for t, v in anual.items()))
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cripto", type=int, default=100)
    ap.add_argument("--acoes", type=int, default=400)
    a = ap.parse_args()
    t0 = time.time()
    import dados
    uni = dados.universo()
    cripto = sorted([x for x in uni if x["classe"] == "cripto" and x["nome"] not in TL.MEMES],
                    key=lambda x: -(x.get("vol24h") or 0))[: a.cripto]
    acoes = sorted([x for x in uni if x["classe"] == "acao"], key=lambda x: -(x.get("vol24h") or 0))[: a.acoes]
    hoje = datetime.date.today()
    meses = TL.meses_ate(hoje.replace(day=1), (hoje.year - 2019) * 12 + hoje.month)   # desde jan/2019

    btc = diario_cripto("BTCUSDT", meses)
    spy = diario_acao("SPY")
    if btc is None or spy is None:
        raise SystemExit("sem BTC ou SPY")
    print(f"BTC desde {btc.index[0]:%d/%m/%Y}; SPY desde {spy.index[0]:%d/%m/%Y}", flush=True)
    qqq = diario_acao("QQQ")
    eth = diario_cripto("ETHUSDT", meses)

    ativos, fundings = [], {}

    def um_cripto(at):
        par = f"{at['nome']}USDT"
        d = btc if par == "BTCUSDT" else diario_cripto(par, meses)
        if d is None or len(d) < 260:
            return
        fundings[at["nome"]] = funding_cripto(par, meses)
        ativos.append((0, at["nome"], d))

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(um_cripto, cripto))
    print(f"criptos: {len(ativos)} em {time.time() - t0:.0f}s", flush=True)

    def um_acao(at):
        d = diario_acao(at["nome"])
        if d is not None:
            ativos.append((1, at["nome"], d))

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(um_acao, acoes))
    n_acoes = sum(1 for x in ativos if x[0] == 1)
    print(f"ações: {n_acoes} em {time.time() - t0:.0f}s", flush=True)

    trades_por_regime = {}
    for tipo in REGIMES:
        alta_btc, alta_spy = regime(btc["close"], tipo), regime(spy["close"], tipo)
        tr = []
        for classe, nome, d in ativos:
            tr += trades_ativo(d, alta_btc if classe == 0 else alta_spy, classe, nome)
        trades_por_regime[tipo] = tr
        print(f"filtro {tipo}: {len(tr)} trades possíveis", flush=True)

    def media_f_dia(fd):
        fs_ = [s for s in fd.values() if s is not None and len(s)]
        return pd.concat(fs_, axis=1).mean(axis=1).sort_index() if fs_ else pd.Series(dtype=float)

    fs = [s for s in fundings.values() if s is not None and len(s)]
    media_f = pd.concat(fs, axis=1).mean(axis=1).sort_index() if fs else pd.Series(dtype=float)
    acum = {k: s.sort_index().cumsum() for k, s in fundings.items() if s is not None and len(s)}
    acum_media = media_f.cumsum()

    def funding(t):
        s = acum.get(t["ativo"]) if t["classe"] == 0 else None
        s = s if s is not None else acum_media
        if s.empty:
            return 0.0
        a_ = s.asof(t["ent"]) if t["ent"] >= s.index[0] else 0.0
        b_ = s.asof(t["sai"]) if t["sai"] >= s.index[0] else 0.0
        return float((b_ if np.isfinite(b_) else 0) - (a_ if np.isfinite(a_) else 0))

    L = [f"TESTE DE CARTEIRA — {datetime.datetime.now(datetime.timezone.utc):%d/%m/%Y %H:%M} UTC",
         f"Desde {INICIO:%d/%m/%Y} · {len(fundings)} criptos (Binance) + {n_acoes} ações da BingX (Yahoo, diário) · "
         f"custo {CUSTO:.1%} por trade · funding real nas criptos (ações: média das criptos no dia, suposição)",
         "Sistemas: Donchian 55/10, RSI(2), Ichimoku semanal, Momento semanal.",
         "Filtro de mercado (BTC p/ cripto, SPY p/ ações): long só em alta, short (Donchian e RSI(2)) só em baixa.",
         f"Tamanho: risco fixo sobre o stop, mas cada posição até {MAX_POSICAO:.0f}x o saldo e todas juntas até {MAX_TOTAL:.0f}x.", ""]
    L.append("== Comparação: comprar e segurar (sem alavancagem) ==")
    for nome, d in (("BTC", btc), ("QQQ", qqq), ("SPY", spy)):
        if d is not None:
            c = d["close"][d.index >= INICIO]
            L += resumo_curva(c, f"{nome} comprar e segurar")
    L.append("")
    L.append("== Tendência alavancada: BTC + ETH + QQQ, comprado só acima da média de 40 semanas ==")
    series = {k: v["close"] for k, v in (("BTC", btc), ("ETH", eth), ("QQQ", qqq)) if v is not None}
    for alav in (1, 2, 3):
        L += resumo_curva(tendencia_alavancada(series, alav, media_f_dia(fundings)), f"{alav}x")
    L.append("")
    melhor = None
    L.append("== Carteira por filtro de mercado (risco 3%, 10 vagas) ==")
    for tipo in REGIMES:
        fech, curva = carteira(trades_por_regime[tipo], funding, 0.03, 10)
        L += [f"[filtro {tipo}]"] + resumo_carteira(fech, curva, 0.03, 10)
        if not curva.empty and (melhor is None or curva.iloc[-1] > melhor[1]):
            melhor = (tipo, curva.iloc[-1])
    L.append("")
    tipo = melhor[0] if melhor else REGIMES[0]
    trades = trades_por_regime[tipo]
    L.append(f"(daqui para baixo: filtro '{tipo}', o melhor acima)")
    L.append("")
    T = pd.DataFrame(trades)
    if T.empty:
        raise SystemExit("sem trades")
    T["R"] = ((T["ret"] - CUSTO) / T["dist"]).clip(upper=20)       # R limitado a 20 (stop muito curto distorce a média)
    L.append("== Cada sistema sozinho (todos os sinais, sem limite de vagas) ==")
    L.append(f"{'sistema':<15} {'mercado':<13} {'trades':>6} {'acerto':>7} {'R méd':>6} {'dias':>5}  por ano (R médio)")
    for (s, cl, ld), g in T.groupby(["sist", "classe", "lado"]):
        anos = " · ".join(f"{y} {v:+.2f}" for y, v in g.groupby(g["ent"].dt.year)["R"].mean().items())
        L.append(f"{s:<15} {('cripto' if cl == 0 else 'ações') + (' long' if ld == 1 else ' short'):<13} {len(g):>6} "
                 f"{(g['R'] > 0).mean():>7.0%} {g['R'].mean():>+6.2f} {(g['sai'] - g['ent']).dt.days.mean():>5.0f}  {anos}")
    L.append("")
    L.append("== Carteira (mistura de tudo, como o robô faria) ==")
    for risco, vagas in CENARIOS:
        fech, curva = carteira(trades, funding, risco, vagas)
        L += resumo_carteira(fech, curva, risco, vagas)
        if (risco, vagas) == CENARIOS[0]:
            F = pd.DataFrame(fech)
            if not F.empty:
                L.append("  contribuição por sistema (US$): " + " · ".join(
                    f"{k} {v:+,.0f}" for k, v in F.groupby("sist")["pnl"].sum().sort_values(ascending=False).items()))
                L.append(f"  trades feitos: {len(F)} (~{len(F) / max(1, (F['sai'].max() - INICIO).days) * 30:.0f} por mês)")
        L.append("")
    txt = "\n".join(L)
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
