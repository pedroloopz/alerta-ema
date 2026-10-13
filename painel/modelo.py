"""Modelo de fluxo (experimental): liquidações, fluxo taker, open interest e posicionamento
(Gate, 1h) + preço e volume (15 min) -> chance de bater TP de 2% antes de SL de 3% em 12h.

Treino diário (calibrar.py). A chance mostrada no painel não é a do modelo cru: é o acerto
que cada faixa de pontuação teve FORA da amostra (últimos 35% do período, sem sobreposição).
"""
import pickle

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

import dados

TP, SL, H = 0.02, 0.03, 48           # alvo 2%, stop 3%, até 12h (48 candles de 15 min)
MAKER, TAKER_SLIP = 0.0002, 0.0008
GATE = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"


def gate_1h(nome, limite=2000):
    d = dados._get(GATE, {"contract": f"{nome}_USDT", "interval": "1h", "limit": limite}, tentativas=2)
    if not d:
        return None
    g = pd.DataFrame(d)
    g["t"] = pd.to_datetime(g["time"], unit="s", utc=True) + pd.Timedelta(hours=1)  # vale no fim da hora
    return g.drop_duplicates("t").set_index("t").sort_index()


def juntar(k, g):
    g = g[g.index <= k.index.max()]
    return k.join(g.reindex(k.index, method="ffill", limit=4), how="left")


def features(df, btc):
    f = pd.DataFrame(index=df.index)
    c, v = df["close"], df["volume"] * df["close"]
    lr = np.log(c)
    for h in (1, 4, 16, 96):
        f[f"ret_{h}"] = lr - lr.shift(h)
    vol = lr.diff().rolling(96).std()
    f["vol96"] = vol
    f["z1"] = f["ret_1"] / vol.shift()
    f["z4"] = f["ret_4"] / (vol.shift() * 2)
    f["volrel"] = v / v.rolling(96).median().shift()
    f["rng"] = (df["high"] - df["low"]) / c / vol.shift()
    f["pos96"] = (c - df["low"].rolling(96).min()) / (df["high"].rolling(96).max() - df["low"].rolling(96).min())
    oi = df["open_interest_usd"].astype(float).replace(0, np.nan).ffill()
    f["oi_4"] = np.log(oi) - np.log(oi.shift(4))
    f["oi_16"] = np.log(oi) - np.log(oi.shift(16))
    ll = df["long_liq_usd"].astype(float).fillna(0)
    sl_ = df["short_liq_usd"].astype(float).fillna(0)
    f["liqL_1"] = ll / oi
    f["liqS_1"] = sl_ / oi
    f["liqL_4"] = ll.rolling(16).max() / oi
    f["liqS_4"] = sl_.rolling(16).max() / oi
    base = (ll + sl_).rolling(96 * 3, min_periods=96).mean().shift()
    f["liq_spike"] = (ll + sl_) / base.replace(0, np.nan)
    f["liq_dir"] = (ll - sl_) / (ll + sl_).replace(0, np.nan)
    f["taker"] = np.log(df["lsr_taker"].astype(float).clip(0.01, 100))
    f["taker_4"] = f["taker"].rolling(16).mean()
    f["users_ls"] = np.log((df["long_users"].astype(float) / df["short_users"].astype(float).replace(0, np.nan)).clip(0.01, 100))
    f["funding"] = df["last_funding_rate"].astype(float)
    f["acc_ls"] = np.log(df["lsr_account"].astype(float).clip(0.01, 100))
    f["top_ls"] = np.log(df["top_lsr_size"].astype(float).clip(0.01, 100))
    f["top_minus_acc"] = f["top_ls"] - f["acc_ls"]
    f["d_top_ls_16"] = f["top_ls"] - f["top_ls"].shift(16)
    b = np.log(btc.reindex(df.index).ffill())
    f["btc_4"] = b - b.shift(4)
    f["btc_16"] = b - b.shift(16)
    f["rel_16"] = f["ret_16"] - f["btc_16"]
    f["hora"] = df.index.hour
    return f


COLUNAS = None  # preenchido no treino; o scanner usa a lista salva junto do modelo


def rotular(df):
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    out = {}
    Hw, Lw = swv(hi[1:], H)[: n - H], swv(lo[1:], H)[: n - H]
    c0, fimc = cl[: n - H, None], cl[H:n]
    for lado in (1, -1):
        if lado == 1:
            btp, bsl = Hw >= c0 * (1 + TP), Lw <= c0 * (1 - SL)
        else:
            btp, bsl = Lw <= c0 * (1 - TP), Hw >= c0 * (1 + SL)
        itp = np.where(btp.any(1), btp.argmax(1), H + 1)
        isl = np.where(bsl.any(1), bsl.argmax(1), H + 1)
        g = itp < isl
        stop = (isl <= itp) & (isl <= H)
        rt = lado * (fimc / cl[: n - H] - 1)
        r = np.where(g, TP - 2 * MAKER, np.where(stop, -SL - MAKER - TAKER_SLIP, rt - MAKER - TAKER_SLIP)) / SL
        y = np.full(n, np.nan); y[: n - H] = g
        rr = np.full(n, np.nan); rr[: n - H] = r
        out[lado] = (y, rr)
    return out


def _novo():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=400, l2_regularization=1.0, random_state=0)


def faixas(p_tr, p_te, y, r, n=10):
    """Acerto e R médio fora da amostra por faixa de pontuação (cortes tirados do treino)."""
    cortes = np.unique(np.quantile(p_tr, np.linspace(0, 1, n + 1)[1:-1]))
    cortes = list(np.r_[cortes, np.quantile(p_tr, [0.97, 0.99])])
    cortes = sorted(set(round(float(c), 6) for c in cortes))
    lim = [-np.inf] + cortes + [np.inf]
    out = []
    for a, b in zip(lim[:-1], lim[1:]):
        m = (p_te >= a) & (p_te < b)
        if m.sum():
            out.append({"de": None if a == -np.inf else a, "ate": None if b == np.inf else b,
                        "n": int(m.sum()), "acerto": round(float(y[m].mean()), 3), "r": round(float(r[m].mean()), 3)})
    return out


def treinar(linhas):
    """linhas: DataFrame com features + y_1/r_1/y_-1/r_-1 (decisões de hora em hora)."""
    cols = [c for c in linhas.columns if not c.startswith(("y_", "r_")) and c != "sym"]
    t = linhas.index
    corte = t.min() + (t.max() - t.min()) * 0.65
    tr, te = linhas[t < corte], linhas[t >= corte + pd.Timedelta(hours=13)]
    pacote = {"colunas": cols, "tp": TP, "sl": SL, "h_horas": H // 4, "modelos": {}, "faixas": {},
              "teste": [str(te.index.min()), str(te.index.max())]}
    for lado in (1, -1):
        y, r = f"y_{lado}", f"r_{lado}"
        a, b = tr.dropna(subset=[y]), te.dropna(subset=[y])
        m = _novo().fit(a[cols], a[y])
        pacote["faixas"][lado] = faixas(m.predict_proba(a[cols])[:, 1], m.predict_proba(b[cols])[:, 1],
                                        b[y].to_numpy(), b[r].to_numpy())
        tudo = linhas.dropna(subset=[y])
        pacote["modelos"][lado] = _novo().fit(tudo[cols], tudo[y])
    return pacote


def chance(pacote, lado, p):
    """Converte a pontuação crua no acerto/R medidos fora da amostra para aquela faixa."""
    for fx in pacote["faixas"][lado]:
        if (fx["de"] is None or p >= fx["de"]) and (fx["ate"] is None or p < fx["ate"]):
            return fx
    return None


def salvar(pacote, caminho):
    with open(caminho, "wb") as f:
        pickle.dump(pacote, f)


def carregar(caminho):
    with open(caminho, "rb") as f:
        return pickle.load(f)
