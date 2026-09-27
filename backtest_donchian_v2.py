"""
DONCHIAN v2 - SPOT, SO COMPRADO.

1. Saidas mais rapidas (entrada fixa: maxima de 55 dias):
     min10 (atual) | min5 | chandelier 3 ATR | chandelier 2 ATR | fecha abaixo da MME 10
   x tamanho: igual | por volatilidade. Escolha SO no treino (2015-2021).
2. Universo amplo: S&P 500 + Nasdaq-100 (proxy da lista da Ondo) + lista do bot.
3. Setores (GICS) e semicondutores x as suas 7 acoes.
4. Cripto: 30 grandes + ONDO, ASTER, PENDLE, GRAM (filtro BTC > MMA 200).
5. Carteira combinada acoes + ONDO/ASTER/PENDLE/GRAM.
Custos x1,5: acoes 0,20%/lado, cripto 0,10%/lado. 5 posicoes.
"""

import io
import json

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import backtest_donchian as bd

ESTRESSE = 1.5
K = 5
TETO = 0.30
RISCO = 0.01
CORTE = pd.Timestamp("2022-01-01")
SAIDAS = ["min10", "min5", "chand3", "chand2", "mme10"]
PEDIDAS = ["ONDO", "ASTER", "PENDLE", "GRAM"]
CRIPTO = ["BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE", "TRX", "LINK", "AVAX",
          "DOT", "LTC", "BCH", "XLM", "ATOM", "ETC", "FIL", "NEAR", "UNI", "AAVE",
          "ALGO", "HBAR", "VET", "ICP", "INJ", "SUI", "APT", "ARB", "OP", "TON"]
UA = {"User-Agent": "Mozilla/5.0"}
pct = lambda x: f"{x:+.1f}%".replace(".", ",")
usd = lambda x: f"US$ {x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------- universo
def wiki(url, col_t, col_s, col_sub=None):
    html = requests.get(url, headers=UA, timeout=30).text
    for t in pd.read_html(io.StringIO(html)):
        if col_t in t.columns and col_s in t.columns:
            out = {}
            for _, r in t.iterrows():
                tk = str(r[col_t]).replace(".", "-").strip()
                setor = r[col_s]
                if col_sub and "Semiconductor" in str(r.get(col_sub, "")):
                    setor = "Semicondutores"
                out[tk] = setor
            return out
    return {}


def universo_acoes():
    setores = {}
    try:
        setores.update(wiki("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker", "GICS Sector", "GICS Sub-Industry"))
    except Exception as e:
        print("nasdaq-100 falhou:", e)
    try:
        setores.update(wiki("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol", "GICS Sector", "GICS Sub-Industry"))
    except Exception as e:
        print("s&p 500 falhou:", e)
    for t in bd.universo():
        setores.setdefault(t, "Lista do bot (outros)")
    cart = list(json.load(open("carteira.json"))["ativos"])
    for t in cart:
        setores.setdefault(t, "Lista do bot (outros)")
    return setores, cart


def baixar_acoes(tickers):
    bruto = yf.download(tickers + ["SPY"], start="2015-01-01", auto_adjust=True, progress=False,
                        group_by="ticker", threads=True)
    ok = [t for t in tickers if t in bruto.columns.get_level_values(0)
          and bruto[t]["Close"].notna().sum() > 300]
    campos = {k: np.column_stack([bruto[t][k].to_numpy() for t in ok]) for k in ("Open", "High", "Low", "Close")}
    return ok, pd.DatetimeIndex(bruto.index), campos, bruto["SPY"]["Close"].to_numpy()


def klines(par):
    out, ini = [], int(pd.Timestamp("2018-01-01").timestamp() * 1000)
    while True:
        r = requests.get("https://data-api.binance.vision/api/v3/klines",
                         params={"symbol": par, "interval": "1d", "startTime": ini, "limit": 1000}, timeout=30)
        if r.status_code != 200:
            return None
        k = r.json()
        if not k:
            break
        out += k
        ini = k[-1][0] + 86_400_000
        if len(k) < 1000:
            break
    if len(out) < 80:
        return None
    df = pd.DataFrame(out).iloc[:, :5].astype(float)
    df.columns = ["t", "Open", "High", "Low", "Close"]
    df.index = pd.to_datetime(df["t"], unit="ms").dt.normalize()
    return df.drop(columns="t")


# ---------------------------------------------------------------- trades
def roll(a, n, f, mp=None):
    return getattr(pd.DataFrame(a).rolling(n, min_periods=mp or n), f)().to_numpy()


def trades(campos, saida, n_ent=55):
    O, H, L, C = (campos[k] for k in ("Open", "High", "Low", "Close"))
    nivel = roll(H, n_ent, "max", max(20, n_ent // 2))       # ativos novos: janela parcial
    lo10, lo5 = roll(L, 10, "min"), roll(L, 5, "min")
    tr_ = np.fmax(H - L, np.fmax(np.abs(H - np.roll(C, 1, 0)), np.abs(L - np.roll(C, 1, 0))))
    atr = roll(tr_, 20, "mean", 10)
    ema10 = pd.DataFrame(C).ewm(span=10, adjust=False).mean().to_numpy()
    mom = C / np.roll(C, 126, 0) - 1
    nd, na = C.shape
    out = []
    for a in range(na):
        i = 30
        while i < nd - 1:
            s, e = i, i + 1
            nv = nivel[s, a]
            if np.isnan(nv) or np.isnan(O[e, a]) or np.isnan(C[s, a]):
                i += 1
                continue
            ent = O[e, a] if O[e, a] > nv else (nv if H[e, a] >= nv else None)
            if ent is None:
                i += 1
                continue
            topo, js = H[e, a], None
            for j in range(e + 1, nd):
                if np.isnan(O[j, a]) or np.isnan(C[j - 1, a]):
                    continue
                c1 = C[j - 1, a]
                topo = max(topo, H[j - 1, a]) if not np.isnan(H[j - 1, a]) else topo
                if saida == "min10":
                    sair = c1 < lo10[j - 2, a]
                elif saida == "min5":
                    sair = c1 < lo5[j - 2, a]
                elif saida == "chand3":
                    sair = c1 < topo - 3 * atr[j - 1, a]
                elif saida == "chand2":
                    sair = c1 < topo - 2 * atr[j - 1, a]
                else:
                    sair = c1 < ema10[j - 1, a]
                if sair:
                    js = j
                    break
            if js is None:
                break
            m = mom[s, a] if not np.isnan(mom[s, a]) else 0.0
            out.append((a, e, js, ent, O[js, a], m, atr[s, a] / C[s, a]))
            i = js
    return out


# ---------------------------------------------------------------- carteira spot
def simular(trs, campos, ok, custo_lado, tamanho, max_pos=K):
    """ok[d, a]: filtro de mercado por ativo. custo_lado: array por ativo."""
    C = campos["Close"]
    nd = C.shape[0]
    por_dia = {}
    for t in trs:
        por_dia.setdefault(t[1], []).append(t)
    caixa, pos, curva, ult = 1.0, [], np.empty(nd), np.nan_to_num(C[0])
    feitos = []
    for d in range(nd):
        cd = np.where(np.isnan(C[d]), ult, C[d])
        eq = curva[d - 1] if d else caixa
        for a, e, js, ent, sai, sc, atrp in sorted(por_dia.get(d, []), key=lambda t: -t[5]):
            if len(pos) >= max_pos:
                break
            if not ok[e - 1, a] or any(p[0] == a for p in pos):
                continue
            v = eq / max_pos
            if tamanho == "vol" and atrp > 0 and not np.isnan(atrp):
                v = min(eq * RISCO / (2 * atrp), eq * TETO)
            v = min(v, caixa)
            if v <= 0:
                continue
            c = custo_lado[a] * ESTRESSE
            caixa -= v
            pos.append((a, js, sai, v * (1 - c) / ent, ent, c, e))
        rest = []
        for p in pos:
            if p[1] == d:
                caixa += p[3] * p[2] * (1 - p[5])
                feitos.append((p[0], p[6], (p[2] * (1 - p[5])) / (p[4] / (1 - p[5])) - 1))
            else:
                rest.append(p)
        pos = rest
        ult = cd
        curva[d] = caixa + sum(p[3] * cd[p[0]] for p in pos)
    return curva, feitos


def met(curva, idx, mask):
    s = pd.Series(curva, index=idx)[mask]
    anos = (s.index[-1] - s.index[0]).days / 365.25
    if anos <= 0.2 or s.iloc[0] <= 0:
        return np.nan, np.nan
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1) * 100, ((s / s.cummax()) - 1).min() * 100


# ---------------------------------------------------------------- main
def main():
    capital = json.load(open("carteira.json"))["capital_inicial"]
    L = [f"DONCHIAN v2 - SPOT, SO COMPRADO | capital {usd(capital)} | {K} posicoes", ""]

    # ---------- acoes
    setores, cart = universo_acoes()
    tick, idx, campos, spy = baixar_acoes(sorted(setores))
    spy_ok = spy > pd.Series(spy).rolling(200).mean().to_numpy()
    ok = np.repeat(spy_ok[:, None], len(tick), axis=1)
    custo = np.full(len(tick), 0.20 / 100)
    m_tr = (idx < CORTE) & (np.arange(len(idx)) >= 200)
    m_va = idx >= CORTE
    L.append(f"ACOES: {len(tick)} (S&P 500 + Nasdaq-100 + lista do bot) | {idx[200]:%d/%m/%Y} a {idx[-1]:%d/%m/%Y} | validacao desde {CORTE:%d/%m/%Y}")
    L.append("filtro: SPY acima da MMA 200 | entrada: maxima de 55 dias")
    L.append("")

    cache, grade = {}, []
    for s in SAIDAS:
        cache[s] = trades(campos, s)
        for tam in ("igual", "vol"):
            curva, feitos = simular(cache[s], campos, ok, custo, tam)
            ct, qt = met(curva, idx, m_tr)
            cv, qv = met(curva, idx, m_va)
            r = np.array([f[2] for f in feitos]) * 100
            dur = np.mean([t[2] - t[1] for t in cache[s]])
            grade.append(dict(saida=s, tam=tam, ct=ct, qt=qt, cv=cv, qv=qv, n=len(r),
                              acerto=(r > 0).mean() * 100, g=r[r > 0].mean(), p=r[r <= 0].mean(), dur=dur))
    g = pd.DataFrame(grade)
    g["mar_tr"] = g.ct / g.qt.abs()
    nomes = {"min10": "minima 10 dias (atual)", "min5": "minima 5 dias", "chand3": "chandelier 3 ATR",
             "chand2": "chandelier 2 ATR", "mme10": "fecha abaixo da MME 10"}
    L.append("SAIDAS (treino -> validacao, CAGR / pior queda):")
    for _, r in g.sort_values(["tam", "mar_tr"], ascending=[True, False]).iterrows():
        L.append(f"  {nomes[r.saida]} | tamanho {r.tam}: treino {pct(r.ct)}/{pct(r.qt)} -> valid {pct(r.cv)}/{pct(r.qv)} "
                 f"| acerto {r.acerto:.0f}% | ganho {pct(r.g)} perda {pct(r.p)} | {r.dur:.0f} pregoes")
    best_mar = g.sort_values("mar_tr", ascending=False).iloc[0]
    best_cagr = g.sort_values("ct", ascending=False).iloc[0]
    L += ["", f"Melhor retorno/risco no treino: {nomes[best_mar.saida]}, tamanho {best_mar.tam} -> valid {pct(best_mar.cv)}/{pct(best_mar.qv)}",
          f"Maior retorno no treino: {nomes[best_cagr.saida]}, tamanho {best_cagr.tam} -> valid {pct(best_cagr.cv)}/{pct(best_cagr.qv)}", ""]
    esc_s, esc_t = best_cagr.saida, best_cagr.tam

    # ---------- setores (trades da saida escolhida, lucro por trade com custo x1,5)
    trs = cache[esc_s]
    df = pd.DataFrame(trs, columns=["a", "e", "js", "ent", "sai", "mom", "atr"])
    df["t"] = [tick[a] for a in df.a]
    df["setor"] = [setores.get(t, "Outros") for t in df.t]
    df["ret"] = (df.sai / df.ent - 1) * 100 - 0.40 * ESTRESSE
    df["valid"] = idx[df.e] >= CORTE
    L.append(f"SETORES - lucro medio por trade ({nomes[esc_s]}, custo x1,5), treino | validacao:")
    linhas_set = []
    for setor, x in df.groupby("setor"):
        tr_, va_ = x[~x.valid].ret, x[x.valid].ret
        linhas_set.append((va_.mean(), f"  {setor}: {len(x)} trades | treino {pct(tr_.mean())} | valid {pct(va_.mean())} | acerto {(x.ret > 0).mean() * 100:.0f}%"))
    for _, txt in sorted(linhas_set, key=lambda z: -np.nan_to_num(z[0], nan=-99)):
        L.append(txt)
    x = df[df.t.isin(cart)]
    L.append(f"  >> SUAS 7 ({', '.join(cart)}): {len(x)} trades | treino {pct(x[~x.valid].ret.mean())} | valid {pct(x[x.valid].ret.mean())} | acerto {(x.ret > 0).mean() * 100:.0f}%")
    L.append("")

    # carteiras por grupo
    L.append("CARTEIRA SO COM O GRUPO (validacao CAGR / pior queda):")
    grupos = {"Todas": list(range(len(tick))), "Suas 7": [tick.index(t) for t in cart if t in tick]}
    for setor in sorted(set(setores.values())):
        grupos[setor] = [i for i, t in enumerate(tick) if setores.get(t) == setor]
    for nome, ids in grupos.items():
        if len(ids) < 3 and nome != "Suas 7":
            continue
        sub = [t for t in trs if t[0] in set(ids)]
        curva, _ = simular(sub, campos, ok, custo, esc_t)
        cv, qv = met(curva, idx, m_va)
        ca, qa = met(curva, idx, np.arange(len(idx)) >= 200)
        L.append(f"  {nome} ({len(ids)}): valid {pct(cv)}/{pct(qv)} | todo {pct(ca)}/{pct(qa)}")
    L.append("")

    # ---------- cripto
    moedas = {}
    for m in CRIPTO + PEDIDAS:
        k = klines(m + "USDT")
        if k is not None:
            moedas[m] = k
    faltam = [m for m in PEDIDAS if m not in moedas]
    cidx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in moedas.values()])))
    cn = list(moedas)
    ccamp = {k: np.column_stack([moedas[m][k].reindex(cidx).to_numpy() for m in cn]) for k in ("Open", "High", "Low", "Close")}
    btc = moedas["BTC"]["Close"].reindex(cidx).ffill().to_numpy()
    btc_ok = btc > pd.Series(btc).rolling(200).mean().to_numpy()
    cok = np.repeat(btc_ok[:, None], len(cn), axis=1)
    ccusto = np.full(len(cn), 0.10 / 100)
    L.append(f"CRIPTO: {len(cn)} moedas | {cidx[200]:%d/%m/%Y} a {cidx[-1]:%d/%m/%Y} | sem dados na Binance spot: {', '.join(faltam) or 'nenhuma'}")
    for s in dict.fromkeys(["min10", esc_s]):
        ctr = trades(ccamp, s)
        curva, _ = simular(ctr, ccamp, cok, ccusto, esc_t)
        cv, qv = met(curva, cidx, cidx >= CORTE)
        ca, qa = met(curva, cidx, np.arange(len(cidx)) >= 200)
        L.append(f"  saida {nomes[s]}: valid {pct(cv)}/{pct(qv)} | todo {pct(ca)}/{pct(qa)}")
        cdf = pd.DataFrame(ctr, columns=["a", "e", "js", "ent", "sai", "mom", "atr"])
        cdf["ret"] = (cdf.sai / cdf.ent - 1) * 100 - 0.20 * ESTRESSE
        for m in PEDIDAS:
            if m in cn:
                x = cdf[cdf.a == cn.index(m)]
                desde = moedas[m].index[0]
                if len(x):
                    L.append(f"    {m} (desde {desde:%d/%m/%Y}): {len(x)} trades | medio {pct(x.ret.mean())} | acerto {(x.ret > 0).mean() * 100:.0f}% | melhor {pct(x.ret.max())} | pior {pct(x.ret.min())}")
                else:
                    L.append(f"    {m} (desde {desde:%d/%m/%Y}): nenhum trade")
    bs_ = pd.Series(btc, index=cidx)
    for rot, msk in [("valid", cidx >= CORTE), ("todo", np.arange(len(cidx)) >= 200)]:
        s = bs_[msk]
        anos = (s.index[-1] - s.index[0]).days / 365.25
        L.append(f"  BTC segurar {rot}: {pct(((s.iloc[-1] / s.iloc[0]) ** (1 / anos) - 1) * 100)}/{pct(((s / s.cummax()) - 1).min() * 100)}")
    L.append("")

    # ---------- combinado: acoes + as 4 pedidas
    ped = [m for m in PEDIDAS if m in moedas]
    if ped:
        uidx = idx.union(cidx)
        cols = {k: [] for k in ("Open", "High", "Low", "Close")}
        for k in cols:
            A = pd.DataFrame(campos[k], index=idx, columns=tick).reindex(uidx)
            B = pd.DataFrame({m: moedas[m][k] for m in ped}).reindex(uidx)
            cols[k] = pd.concat([A, B], axis=1).to_numpy()
        spy_u = pd.Series(spy_ok, index=idx).reindex(uidx).ffill().fillna(False).to_numpy()
        btc_u = pd.Series(btc_ok, index=cidx).reindex(uidx).ffill().fillna(False).to_numpy()
        uok = np.column_stack([np.repeat(spy_u[:, None], len(tick), 1), np.repeat(btc_u[:, None], len(ped), 1)])
        ucusto = np.r_[np.full(len(tick), 0.20 / 100), np.full(len(ped), 0.10 / 100)]
        desde = min(moedas[m].index[0] for m in ped)
        msk = uidx >= max(desde, pd.Timestamp("2023-01-01"))
        for rot, n in [("so acoes", len(tick)), ("acoes + " + "/".join(ped), len(tick) + len(ped))]:
            trs_u = [t for t in trades(cols, esc_s) if t[0] < n]
            curva, _ = simular(trs_u, cols, uok, ucusto, esc_t)
            c_, q_ = met(curva, uidx, msk)
            L.append(f"COMBINADO desde {uidx[msk][0]:%d/%m/%Y} - {rot}: CAGR {pct(c_)} | pior queda {pct(q_)}")
    L.append("")
    L.append("Obs.: S&P 500/Nasdaq-100 de hoje = vies de sobrevivencia. Lista real da Ondo: app.ondo.finance.")

    texto = "\n".join(L)
    print(texto)
    open("relatorio_donchian_v2.txt", "w").write(texto)
    g.to_csv("donchian_v2_saidas.csv", index=False)


if __name__ == "__main__":
    main()
