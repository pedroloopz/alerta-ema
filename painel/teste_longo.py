"""Teste longo do modelo (cripto + ações juntos), com a saída real do robô.

Por que existe: o modelo de fluxo é medido em só 30 dias (a Gate só dá ~83 dias). Aqui o mesmo método
(gradient boosting, faixas pela pontuação) é refeito com histórico longo e testado mês a mês
("walk-forward": aprende com tudo antes do mês, testa no mês, avança), num modelo só com os dois mercados:
- cripto: candles de 1h e fluxo (open interest, taker, long/short) dos arquivos públicos da Binance
  (data.binance.vision), ~24 meses. Sem liquidações (não há arquivo público).
- ações da BingX: candles de 1h do Yahoo com pré e pós (até 730 dias) e o QQQ como mercado; sem fluxo
  (o modelo vê "sem dado" e aprende separado pela coluna classe).
Saídas medidas: a do robô (stop -3%, 3 alvos de 1/3 em +1,5/+2,5/+3,5%, stop móvel de 1,5% depois do
1º alvo, fecha em 12 candles) e a de referência (alvo +2% / stop -3%). Na dúvida dentro do candle,
conta o stop primeiro. Custos de 0,1% por trade.

Só relatório (relatorio_longo.txt): não muda o painel nem o robô.
Uso (workflow painel-longo): python painel/teste_longo.py [--meses 24] [--cripto 40] [--acoes 80]
"""
import argparse
import datetime
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

AQUI = os.path.dirname(__file__)
SAIDA = os.path.join(AQUI, "..", "relatorio_longo.txt")
BV = "https://data.binance.vision/data/futures/um"
MEMES = {"DOGE", "SHIB", "PEPE", "FLOKI", "WIF", "BONK", "FARTCOIN", "SPX", "USELESS", "PENGU",
         "CHEEMS", "LUNC", "PUMP"}
SL, ALVOS, MOVEL, H = 0.03, (0.015, 0.025, 0.035), 0.015, 12
CUSTO = 0.001                      # taxas + deslize por trade (ida e volta)
RISCO = SL + CUSTO                 # o robô calcula o tamanho com stop + custo = 1R
FAIXAS = (("top 1%", 0.99, None), ("97–99%", 0.97, 0.99), ("90–97%", 0.90, 0.97))
DEGRAUS = ((10, 0.70, 0.25), (9, 0.65, 0.18), (8, 0.60, 0.12), (7, 0.55, 0.06), (6, 0.50, 0.0))


def nota_de(p, r, n):
    for nota, p_min, r_min in DEGRAUS:
        if p >= p_min and r > r_min - 1e-12 and (nota <= 7 or n >= 50):
            return nota
    return 5 if r > -0.05 else 4 if r > -0.10 else 3 if r > -0.20 else 2 if r > -0.35 else 1


# ------------------------------------------------------------------ dados
SESSAO = None


def baixar(url):
    import requests
    global SESSAO
    SESSAO = SESSAO or requests.Session()
    for t in range(3):
        try:
            r = SESSAO.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception:
            if t == 2:
                return None
            time.sleep(1.5 * (t + 1))


def ler_zip(conteudo):
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, header=None, dtype=str)
    try:
        float(df.iloc[0, 0])
    except ValueError:
        if not str(df.iloc[0, 0]).startswith("20"):          # primeira linha é cabeçalho
            df.columns = df.iloc[0]
            df = df.iloc[1:]
    return df


def meses_ate(fim, n):
    out, a, m = [], fim.year, fim.month
    for _ in range(n):
        m -= 1
        if m == 0:
            a, m = a - 1, 12
        out.append(f"{a}-{m:02d}")
    return out[::-1]


def klines_binance(par, meses):
    partes = []
    for mes in meses:
        c = baixar(f"{BV}/monthly/klines/{par}/1h/{par}-1h-{mes}.zip")
        if not c:
            continue
        d = ler_zip(c)
        d = d.iloc[:, :6]
        d.columns = ["t", "open", "high", "low", "close", "volume"]
        d = d.apply(pd.to_numeric, errors="coerce").dropna()
        partes.append(d)
    if not partes:
        return None
    d = pd.concat(partes)
    t = d["t"].astype("int64")
    t = np.where(t > 10 ** 14, t // 1000, t)          # alguns arquivos novos vêm em microssegundos
    d.index = pd.to_datetime(t, unit="ms", utc=True)
    return d.drop(columns="t").sort_index().loc[lambda x: ~x.index.duplicated()].astype("float32")


def metrics_binance(par, dias):
    def um(dia):
        c = baixar(f"{BV}/daily/metrics/{par}/{par}-metrics-{dia}.zip")
        if not c:
            return None
        d = ler_zip(c)
        if "create_time" not in d.columns:
            return None
        return d
    with ThreadPoolExecutor(16) as ex:
        partes = [p for p in ex.map(um, dias) if p is not None]
    if not partes:
        return None
    d = pd.concat(partes)
    d.index = pd.to_datetime(d["create_time"], utc=True)
    cols = ["sum_open_interest_value", "count_toptrader_long_short_ratio", "count_long_short_ratio",
            "sum_taker_long_short_vol_ratio"]
    d = d[[c for c in cols if c in d.columns]].apply(pd.to_numeric, errors="coerce")
    d = d.sort_index().loc[lambda x: ~x.index.duplicated()]
    h = d.resample("1h", label="right", closed="right").agg(
        {c: ("last" if c != "sum_taker_long_short_vol_ratio" else "mean") for c in d.columns})
    return h


def yahoo_1h(ticker):
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                     params={"range": "730d", "interval": "1h", "includePrePost": "true"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    return df.dropna().astype("float32")


def baixar_acao(nome):
    for t in dict.fromkeys([nome, nome[:-2] if nome.endswith("US") else nome]):
        try:
            df = yahoo_1h(t)
            if len(df) > 2000:
                return df
        except Exception:
            pass
    return None


# ------------------------------------------------------------------ atributos
def atributos(df, mkt, fluxo=None, classe=0):
    c = np.log(df["close"].astype("float64"))
    r = c.diff()
    f = pd.DataFrame(index=df.index)
    for n in (1, 4, 24, 72):
        f[f"r{n}"] = c.diff(n)
    f["vol24"] = r.rolling(24).std()
    f["vol_rel"] = f["vol24"] / r.rolling(168).std()
    lv = np.log1p(df["volume"].astype("float64"))
    f["volz"] = (lv - lv.rolling(168).mean()) / lv.rolling(168).std()
    hi, lo = df["high"].rolling(24).max(), df["low"].rolling(24).min()
    f["pos24"] = (df["close"] - lo) / (hi - lo)
    for n in (24, 168):
        f[f"ma{n}"] = (c - c.rolling(n).mean()) / f["vol24"]
    m = np.log(mkt.reindex(df.index, method="ffill").astype("float64"))
    f["m4"], f["m24"] = m.diff(4), m.diff(24)
    f["rel24"] = f["r24"] - f["m24"]
    f["hora"] = df.index.hour
    f["dia"] = df.index.dayofweek
    f["classe"] = classe
    for k in ("oi4", "oi24", "taker", "taker4", "top", "ls", "ls24"):
        f[k] = np.nan
    if fluxo is not None and len(fluxo):
        g = fluxo.reindex(df.index, method="ffill", tolerance=pd.Timedelta("2h"))
        if "sum_open_interest_value" in g:
            oi = np.log(g["sum_open_interest_value"])
            f["oi4"], f["oi24"] = oi.diff(4), oi.diff(24)
        if "sum_taker_long_short_vol_ratio" in g:
            tk = np.log(g["sum_taker_long_short_vol_ratio"])
            f["taker"], f["taker4"] = tk, tk.rolling(4).mean()
        if "count_toptrader_long_short_ratio" in g:
            f["top"] = np.log(g["count_toptrader_long_short_ratio"])
        if "count_long_short_ratio" in g:
            ls = np.log(g["count_long_short_ratio"])
            f["ls"], f["ls24"] = ls, ls.diff(24)
    return f.replace([np.inf, -np.inf], np.nan).astype("float32")


# ------------------------------------------------------------------ saídas
def janelas(df):
    """Excursões das próximas H velas em relação ao fechamento atual (entrada)."""
    e = df["close"].to_numpy("float64")
    n = len(e)
    hi = np.full((n, H), np.nan)
    lo = np.full((n, H), np.nan)
    cl = np.full((n, H), np.nan)
    for k in range(1, H + 1):
        hi[: n - k, k - 1] = df["high"].to_numpy("float64")[k:] / e[: n - k] - 1
        lo[: n - k, k - 1] = df["low"].to_numpy("float64")[k:] / e[: n - k] - 1
        cl[: n - k, k - 1] = df["close"].to_numpy("float64")[k:] / e[: n - k] - 1
    return hi, lo, cl


def saida_robo(fav_hi, fav_lo, fav_cl):
    """R do robô por linha e vela da saída. fav_* = excursão a favor (máxima, mínima, fechamento)."""
    n = fav_hi.shape[0]
    resto = np.ones(n)
    pnl = np.zeros(n)
    stop = np.full(n, -SL)
    melhor = np.full(n, -np.inf)
    pegou = np.zeros((n, 3), bool)
    fim = np.full(n, H)
    for k in range(H):
        a, b = fav_hi[:, k], fav_lo[:, k]
        vivo = resto > 1e-9
        s = vivo & (b <= stop)                                  # stop primeiro, na dúvida
        pnl += np.where(s, resto * stop, 0)
        fim = np.where(s & (fim == H), k + 1, fim)
        resto = np.where(s, 0, resto)
        for i, alvo in enumerate(ALVOS):
            h = (resto > 1e-9) & ~pegou[:, i] & (a >= alvo)
            pnl += np.where(h, alvo / 3, 0)
            resto = np.where(h, resto - 1 / 3, resto)
            pegou[:, i] |= h
        fim = np.where((resto <= 1e-9) & (fim == H), k + 1, fim)
        melhor = np.where(pegou[:, 0], np.maximum(melhor, a), melhor)
        stop = np.where(pegou[:, 0], np.maximum(stop, (1 + melhor) * (1 - MOVEL) - 1), stop)
    pnl += np.clip(resto, 0, None) * fav_cl[:, H - 1]
    return (pnl - CUSTO) / RISCO, fim


def saida_ref(fav_hi, fav_lo, fav_cl, tp=0.02):
    n = fav_hi.shape[0]
    out = np.full(n, np.nan)
    for k in range(H):
        livre = np.isnan(out)
        out = np.where(livre & (fav_lo[:, k] <= -SL), -SL, out)
        livre = np.isnan(out)
        out = np.where(livre & (fav_hi[:, k] >= tp), tp, out)
    out = np.where(np.isnan(out), fav_cl[:, H - 1], out)
    return (out - CUSTO) / RISCO


def rotular(df):
    hi, lo, cl = janelas(df)
    ok = ~np.isnan(cl[:, H - 1])
    L = pd.DataFrame(index=df.index)
    for lado in (1, -1):
        fh, fl, fc = (hi, lo, cl) if lado == 1 else (-lo, -hi, -cl)
        r, fim = saida_robo(fh, fl, fc)
        L[f"r_{lado}"] = np.where(ok, r, np.nan)
        L[f"fim_{lado}"] = fim
        L[f"ref_{lado}"] = np.where(ok, saida_ref(fh, fl, fc), np.nan)
    return L


# ------------------------------------------------------------------ teste mês a mês
def _novo():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=400, l2_regularization=1.0, random_state=0)


def sem_sobrepor(sub, lado):
    """Trades que o robô faria: no máximo 1 por ativo até a saída + 1h."""
    pos = []
    t_all, fim_all, sym_all = sub.index.to_numpy(), sub[f"fim_{lado}"].to_numpy(), sub["sym"].to_numpy()
    livre = {}
    for i in np.argsort(t_all, kind="stable"):
        s, t = sym_all[i], t_all[i]
        if s not in livre or t >= livre[s]:
            pos.append(i)
            livre[s] = t + np.timedelta64(int(fim_all[i]) + 1, "h")
    return sub.iloc[sorted(pos)]


def walk_forward(T, cols, inicio_teste):
    meses = sorted({(t.year, t.month) for t in T.index if t >= inicio_teste})
    res = []
    for (a, m) in meses:
        ini = pd.Timestamp(year=a, month=m, day=1, tz="UTC")
        fim = ini + pd.offsets.MonthBegin(1)
        tr = T[T.index < ini - pd.Timedelta(hours=H + 1)]
        te = T[(T.index >= ini) & (T.index < fim)]
        if len(tr) < 50000 or not len(te):
            continue
        for lado in (1, -1):
            y = f"r_{lado}"
            a_, b_ = tr.dropna(subset=[y]), te.dropna(subset=[y])
            amostra = a_.iloc[:: max(1, len(a_) // 600000)]           # limite de memória/tempo
            mod = _novo().fit(amostra[cols], (amostra[y] > 0).astype(int))
            p_tr = mod.predict_proba(amostra[cols])[:, 1]
            p_te = mod.predict_proba(b_[cols])[:, 1]
            for nome, q_de, q_ate in FAIXAS:
                de = np.quantile(p_tr, q_de)
                ate = np.quantile(p_tr, q_ate) if q_ate else np.inf
                sel = b_[(p_te >= de) & (p_te < ate)]
                for classe in (0, 1):
                    s = sel[sel["classe"] == classe]
                    if not len(s):
                        continue
                    tr_ = sem_sobrepor(s, lado)
                    res.append({"mes": f"{a}-{m:02d}", "lado": lado, "faixa": nome, "classe": classe,
                                "linhas": len(s), "trades": len(tr_), "r": tr_[y].tolist(),
                                "ref": tr_[f"ref_{lado}"].tolist()})
        print(f"  {a}-{m:02d}: treino {len(tr)} linhas, teste {len(te)}", flush=True)
    return res


def relatorio(res, info):
    L = [f"TESTE LONGO DO MODELO — {datetime.datetime.now(datetime.timezone.utc):%d/%m/%Y %H:%M} UTC", "",
         *info, "",
         "Como ler: cada mês o modelo aprende só com o passado e é testado no mês seguinte.",
         "'trades' = sem sobreposição (1 por ativo até sair + 1h), como o robô faria.",
         "R do robô = 3 alvos + stop móvel; R ref = alvo +2% / stop −3%. Já com 0,1% de custo.",
         "Nota pela mesma régua do painel, usando acerto e R do robô.", ""]
    df = pd.DataFrame(res)
    if df.empty:
        return "\n".join(L + ["Sem resultados (faltaram dados)."])
    for classe, nome_c in ((0, "CRIPTO"), (1, "AÇÕES")):
        for lado, nome_l in ((1, "LONG"), (-1, "SHORT")):
            L.append(f"== {nome_c} · {nome_l} ==")
            L.append(f"{'faixa':<8} {'trades':>7} {'por dia':>8} {'acerto':>7} {'R robô':>7} {'R ref':>7} "
                     f"{'nota':>5} {'meses +':>8} {'pior mês':>9}")
            for faixa, _, _ in FAIXAS:
                d = df[(df.classe == classe) & (df.lado == lado) & (df.faixa == faixa)]
                rs = [x for v in d["r"] for x in v]
                if not rs:
                    continue
                refs = [x for v in d["ref"] for x in v]
                por_mes = [np.mean(v) if v else np.nan for v in d["r"]]
                soma_mes = [np.sum(v) for v in d["r"]]
                dias = max(1, len(d["mes"].unique()) * 30)
                ac, rm = float(np.mean(np.array(rs) > 0)), float(np.mean(rs))
                pos = sum(1 for x in soma_mes if x > 0)
                L.append(f"{faixa:<8} {len(rs):>7} {len(rs) / dias:>8.1f} {ac:>7.0%} {rm:>+7.3f} "
                         f"{np.mean(refs):>+7.3f} {nota_de(ac, rm, len(rs)):>5} {pos:>4}/{len(soma_mes):<3} "
                         f"{np.nanmin(por_mes):>+9.2f}")
            top = df[(df.classe == classe) & (df.lado == lado) & (df.faixa == "top 1%")].sort_values("mes")
            if len(top):
                L.append("  top 1% mês a mês (trades, R médio): " + " · ".join(
                    f"{x.mes[2:]} {len(x.r)} {np.mean(x.r) if x.r else 0:+.2f}" for x in top.itertuples()))
            L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meses", type=int, default=24)
    ap.add_argument("--cripto", type=int, default=40)
    ap.add_argument("--acoes", type=int, default=80)
    a = ap.parse_args()
    t0 = time.time()
    import dados
    uni = dados.universo()
    cripto = sorted([x for x in uni if x["classe"] == "cripto" and x["nome"] not in MEMES],
                    key=lambda x: -(x.get("vol24h") or 0))[: a.cripto]
    acoes = sorted([x for x in uni if x["classe"] == "acao"], key=lambda x: -(x.get("vol24h") or 0))[: a.acoes]
    hoje = datetime.date.today()
    meses = meses_ate(hoje.replace(day=1), a.meses)
    dias = [(hoje - datetime.timedelta(days=i)).isoformat() for i in range(a.meses * 31, 0, -1)]

    btc = klines_binance("BTCUSDT", meses)
    if btc is None:
        raise SystemExit("sem candles do BTC na Binance")
    print(f"BTC: {len(btc)} candles de 1h desde {btc.index[0]:%d/%m/%Y}", flush=True)
    tabelas, sem = [], []

    def cripto_um(at):
        par = f"{at['nome']}USDT"
        k = btc if par == "BTCUSDT" else klines_binance(par, meses)
        if k is None or len(k) < 24 * 200:
            sem.append(par)
            return None
        fx = metrics_binance(par, dias)
        t = atributos(k, btc["close"], fx, classe=0).join(rotular(k))
        t["sym"] = par
        return t

    with ThreadPoolExecutor(4) as ex:
        tabelas += [t for t in ex.map(cripto_um, cripto) if t is not None]
    n_cripto = len(tabelas)
    print(f"cripto: {n_cripto} ativos em {time.time() - t0:.0f}s (sem dados: {', '.join(sem) or '—'})", flush=True)

    qqq = yahoo_1h("QQQ")
    sem_a = []

    def acao_um(at):
        df = baixar_acao(at["nome"])
        if df is None:
            sem_a.append(at["nome"])
            return None
        t = atributos(df, qqq["close"], None, classe=1).join(rotular(df))
        t["sym"] = at["nome"]
        return t

    with ThreadPoolExecutor(4) as ex:
        tabelas += [t for t in ex.map(acao_um, acoes) if t is not None]
    n_acoes = len(tabelas) - n_cripto
    print(f"ações: {n_acoes} em {time.time() - t0:.0f}s (sem dados: {len(sem_a)})", flush=True)

    T = pd.concat(tabelas).sort_index()
    T = T[T["vol24"].notna() & T["ma168"].notna()]
    cols = [c for c in T.columns if not c.startswith(("r_", "fim_", "ref_")) and c != "sym"]
    inicio = T.index.min() + pd.Timedelta(days=180)                 # 6 meses mínimos de treino
    print(f"tabela: {len(T)} linhas, {len(cols)} colunas; teste a partir de {inicio:%m/%Y}", flush=True)
    res = walk_forward(T, cols, inicio)
    info = [f"Cripto: {n_cripto} ativos (Binance, 1h, desde {T[T.classe == 0].index.min():%d/%m/%Y})",
            f"Ações: {n_acoes} ativos (Yahoo, 1h com pré/pós, desde {T[T.classe == 1].index.min():%d/%m/%Y}; "
            "12 velas = ~12h de negociação, não de relógio)",
            f"Linhas: {len(T)}; tempo: {(time.time() - t0) / 60:.0f} min"]
    txt = relatorio(res, info)
    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
