"""
Teste minucioso da REVERSAO SEMANAL nos ativos liquidos, em historico longo.

A regra testada e exatamente a aprovada no teste de teses:
  toda semana, entre os perps liquidos, compra os 4 que mais cairam
  nos ultimos 5 pregoes e vende os 4 que mais subiram. Segura 1 semana.

Como os perps sao recentes, o historico longo vem das ACOES subjacentes
(2015 em diante). O custo aplicado e o custo do perp, x1,5.

Limitacoes declaradas no relatorio:
  - vies de sobrevivencia: o universo e o de HOJE (acoes que chegaram vivas
    e liquidas ate 2026). Isso tende a favorecer a compra de perdedores.
  - a acao nao e o perp: o perp pode descolar do subjacente.
"""

import io
import os

import numpy as np
import pandas as pd
import requests

from simbolos import SIMBOLOS, nome_curto
from alerta_ema import baixar_candles, custo_ida_volta, avisar

INICIO = "2015-01-01"
LIQ_MIN = 200_000.0
ESTRESSE = 1.5
NOCIONAL = 2000.0              # US$ 1.000 a 2x
LOOKBACK = 5                   # pregoes
POR_LADO = 4
LIMITE_SALTO = 0.40            # salto diario maior que isso = desdobramento/erro
TOLERANCIA_PRECO = 0.15        # acao x perp: diferenca maxima aceitavel
REPS_SORTEIO = 1000
REPS_BOOTSTRAP = 2000
JANELA = 12                    # semanas (um trimestre)

# Perps sem acao americana correspondente
SEM_SUBJACENTE = {
    "SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU", "CXMT",
    "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN", "KUAISHOU",
    "XIAOMI", "POPMART", "BNC", "GPRO",
}
MAPA = {"STXX": "STX", "BBX": "BB"}


# ================================================================ dados
def universo_liquido():
    liq = {}
    for s in SIMBOLOS:
        curto = nome_curto(s)
        if curto in SEM_SUBJACENTE:
            continue
        try:
            d = baixar_candles(s, "1d", 45)
        except Exception as e:
            print(f"{s}: {e}")
            continue
        if len(d) < 20:
            continue
        vol = float(d["VolumeUSD"].iloc[-30:].median())
        if vol >= LIQ_MIN:
            liq[MAPA.get(curto, curto)] = {
                "perp": s, "vol": vol, "custo": custo_ida_volta(vol),
                "preco_perp": float(d["Close"].iloc[-1]),
            }
    return liq


def _naive(df):
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


def historico_yahoo(tickers):
    try:
        import yfinance as yf
        bruto = yf.download(tickers, start=INICIO, interval="1d", auto_adjust=True,
                            group_by="ticker", progress=False, threads=True)
    except Exception as e:
        print("Yahoo falhou:", e)
        return {}
    saida = {}
    for t in tickers:
        try:
            df = bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto
            df = _naive(df[["Open", "Close"]].dropna().copy())
            if len(df) > 120:
                saida[t] = df
        except Exception:
            pass
    return saida


def historico_stooq(t):
    r = requests.get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d", timeout=30)
    if r.status_code != 200 or not r.text.startswith("Date"):
        raise RuntimeError(r.text[:60].strip())
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"]).set_index("Date")
    return _naive(df.loc[df.index >= INICIO, ["Open", "Close"]].dropna())


def carregar_historico(tickers):
    hist = historico_yahoo(tickers)
    fonte = {t: "yahoo" for t in hist}
    for t in tickers:
        if t in hist:
            continue
        try:
            df = historico_stooq(t)
            if len(df) > 120:
                hist[t], fonte[t] = df, "stooq"
        except Exception as e:
            print(f"{t}: stooq falhou ({e})")
    return hist, fonte


# ============================================================ simulacao
def preparar_semanas(O, C, custos):
    ret = C / C.shift(1) - 1
    gap = O / C.shift(1) - 1
    ruim = (ret.abs() > LIMITE_SALTO) | (gap.abs() > LIMITE_SALTO)

    datas = C.index
    primeiros = pd.Series(datas, index=datas).groupby(datas.to_period("W")).first().to_list()
    pos = {d: i for i, d in enumerate(datas)}

    semanas = {}
    for lb in (3, 5, 10):
        lista = []
        for d_ent, d_sai in zip(primeiros[:-1], primeiros[1:]):
            k, ks = pos[d_ent], pos[d_sai]
            if k - 1 - lb < 0:
                continue
            c_fim, c_ini = C.iloc[k - 1], C.iloc[k - 1 - lb]
            o_ent, o_sai = O.iloc[k], O.iloc[ks]
            contaminado = ruim.iloc[k - lb:ks + 1].any()
            ok = c_fim.notna() & c_ini.notna() & o_ent.notna() & o_sai.notna() & ~contaminado
            tick = ok[ok].index.tolist()
            if len(tick) < 4:
                continue
            lista.append({
                "data": d_ent,
                "tick": np.array(tick),
                "sinal": (c_fim[tick] / c_ini[tick] - 1).to_numpy(),
                "bruto_long": ((o_sai[tick] / o_ent[tick] - 1) * 100).to_numpy(),
                "custo": np.array([custos[t] for t in tick]),
            })
        semanas[lb] = lista
    return semanas


def rodar(semanas, por_lado):
    linhas = []
    for s in semanas:
        n = len(s["tick"])
        k = min(por_lado, n // 2)
        ordem = np.argsort(s["sinal"])
        for idx, lado in [(ordem[:k], 1), (ordem[-k:], -1)]:
            for j in idx:
                bruto = lado * s["bruto_long"][j]
                linhas.append({"data": s["data"], "ticker": s["tick"][j], "lado": lado,
                               "bruto": bruto, "custo": s["custo"][j],
                               "pess": bruto - ESTRESSE * s["custo"][j]})
    return pd.DataFrame(linhas)


def sorteio(semanas, por_lado, reps, rng):
    """Total em US$ de cada repeticao escolhendo posicoes ao acaso."""
    totais = np.zeros(reps)
    for s in semanas:
        n = len(s["tick"])
        k = min(por_lado, n // 2)
        escolha = rng.random((reps, n)).argsort(axis=1)[:, :2 * k]
        bl, cu = s["bruto_long"][escolha], s["custo"][escolha]
        longs = (bl[:, :k] - ESTRESSE * cu[:, :k]).sum(axis=1)
        shorts = (-bl[:, k:] - ESTRESSE * cu[:, k:]).sum(axis=1)
        totais += (longs + shorts) * NOCIONAL / 100
    return totais


# ============================================================= metricas
def por_semana(pos):
    return pos.groupby("data")["pess"].sum() * NOCIONAL / 100


def drawdown(serie):
    curva = serie.cumsum()
    return float((curva.cummax().clip(lower=0) - curva).max())


def main():
    rng = np.random.default_rng(42)
    liq = universo_liquido()
    print(f"{len(liq)} perps liquidos com acao correspondente")

    hist, fonte = carregar_historico(sorted(liq) + ["QQQ"])

    # Conferencia acao x perp
    aceitos, rejeitados = {}, []
    for t, info in liq.items():
        if t not in hist:
            rejeitados.append(f"{t}(sem dados)")
            continue
        razao = float(hist[t]["Close"].iloc[-1]) / info["preco_perp"]
        if abs(razao - 1) > TOLERANCIA_PRECO:
            rejeitados.append(f"{t}(preco {razao:.2f}x)")
            continue
        aceitos[t] = info
    print("Aceitos:", len(aceitos), "| Rejeitados:", rejeitados)

    O = pd.DataFrame({t: hist[t]["Open"] for t in aceitos}).sort_index()
    C = pd.DataFrame({t: hist[t]["Close"] for t in aceitos}).sort_index()
    custos = {t: aceitos[t]["custo"] for t in aceitos}
    semanas = preparar_semanas(O, C, custos)

    base = semanas[LOOKBACK]
    pos = rodar(base, POR_LADO)
    sem = por_semana(pos)

    L = []
    fmt = lambda x: f"{x:+.2f}".replace(".", ",")
    usd = lambda x: f"{x:+,.0f}".replace(",", ".")

    # ---------- 1. visao geral
    L += ["REVERSAO SEMANAL - TESTE LONGO",
          f"{sem.index.min():%m/%Y} a {sem.index.max():%m/%Y} | {len(sem)} semanas | "
          f"{len(aceitos)} acoes",
          f"compra 4 que mais cairam, vende 4 que mais subiram | custo x1,5", "",
          "[1] RESULTADO GERAL",
          f"  por posicao: {fmt(pos['pess'].mean())}% | acerto {(pos['pess'] > 0).mean()*100:.0f}%",
          f"  semanas positivas: {(sem > 0).mean()*100:.0f}%",
          f"  total: US$ {usd(sem.sum())} | media/semana US$ {usd(sem.mean())}",
          f"  drawdown max: US$ {drawdown(sem):,.0f}".replace(",", "."), ""]

    # ---------- 2. ano a ano
    L.append("[2] ANO A ANO (por posicao | sem+ | total)")
    anos_pos = 0
    for ano, sub in pos.groupby(pos["data"].dt.year):
        s_ano = por_semana(sub)
        anos_pos += s_ano.sum() > 0
        L.append(f"  {ano}: {fmt(sub['pess'].mean())}% | {(s_ano > 0).mean()*100:.0f}% | "
                 f"US$ {usd(s_ano.sum())}")
    n_anos = pos["data"].dt.year.nunique()
    L += [f"  anos positivos: {anos_pos} de {n_anos}", ""]

    # ---------- 3. regimes
    L.append("[3] REGIME DE MERCADO (QQQ)")
    if "QQQ" in hist:
        q = hist["QQQ"]["Close"]
        acima = (q > q.rolling(200).mean()).shift(1)
        vol = q.pct_change().rolling(20).std()
        agitado = (vol > vol.rolling(252, min_periods=60).median()).shift(1)
        for nome, serie in (("alta (QQQ > media 200)", acima), ("agitado (vol alta)", agitado)):
            marca = pos["data"].map(lambda d: serie.asof(d))
            for valor, rot in ((True, "sim"), (False, "nao")):
                sub = pos[marca == valor]
                if len(sub):
                    L.append(f"  {nome} = {rot}: {fmt(sub['pess'].mean())}% "
                             f"(n={len(sub)})")
    L.append("")

    # ---------- 4. pernas
    L.append("[4] DE ONDE VEM O RESULTADO")
    for lado, rot in ((1, "compra dos perdedores"), (-1, "venda dos vencedores")):
        sub = pos[pos["lado"] == lado]
        L.append(f"  {rot}: {fmt(sub['pess'].mean())}% | US$ {usd(por_semana(sub).sum())}")
    L.append("")

    # ---------- 5. vizinhanca
    L.append("[5] VIZINHANCA DE PARAMETROS (por posicao)")
    L.append("  pregoes \\ por lado:    2        4        6")
    positivos_grade = 0
    for lb in (3, 5, 10):
        cel = []
        for k in (2, 4, 6):
            m = rodar(semanas[lb], k)["pess"].mean()
            positivos_grade += m > 0
            cel.append(f"{fmt(m):>7s}%")
        L.append(f"  {lb:>2d} pregoes:        " + " ".join(cel))
    L += [f"  celulas positivas: {positivos_grade} de 9", ""]

    # ---------- 6. margem de custo
    bruto_medio = pos["bruto"].mean()
    custo_medio = pos["custo"].mean()
    L += ["[6] MARGEM CONTRA O CUSTO",
          f"  ganho bruto medio: {fmt(bruto_medio)}% por posicao",
          f"  custo estimado: {fmt(custo_medio)}% | com estresse: {fmt(custo_medio*ESTRESSE)}%",
          f"  custo maximo suportavel: {fmt(bruto_medio)}% "
          f"({bruto_medio / custo_medio:.1f}x o estimado)".replace(".", ","), ""]

    # ---------- 7. trimestres
    janelas = sem.rolling(JANELA).sum().dropna()
    L += [f"[7] JANELAS DE {JANELA} SEMANAS",
          f"  trimestres negativos: {(janelas < 0).mean()*100:.0f}%",
          f"  pior trimestre: US$ {usd(janelas.min())}",
          f"  melhor trimestre: US$ {usd(janelas.max())}", ""]

    # ---------- 8. confianca
    medias = np.array([rng.choice(sem.to_numpy(), len(sem)).mean() for _ in range(REPS_BOOTSTRAP)])
    lo, hi = np.percentile(medias, [2.5, 97.5])
    total_real = sem.sum()
    aleat = sorteio(base, POR_LADO, REPS_SORTEIO, rng)
    p = (aleat >= total_real).mean()
    L += ["[8] ISSO E SORTE?",
          f"  media semanal, intervalo 95%: US$ {usd(lo)} a US$ {usd(hi)}",
          f"  sorteio: {REPS_SORTEIO} carteiras aleatorias, total medio US$ {usd(aleat.mean())}",
          f"  chance do acaso bater a estrategia: {p*100:.1f}%", ""]

    # ---------- limitacoes
    n_stooq = sum(1 for t in aceitos if fonte.get(t) == "stooq")
    L += ["LIMITACOES",
          "  - vies de sobrevivencia: universo e o de hoje",
          "  - acao nao e o perp: o perp pode descolar",
          f"  - fora do teste: {', '.join(rejeitados) or 'nenhum'}",
          f"  - fonte: {len(aceitos)-n_stooq} Yahoo, {n_stooq} Stooq"]

    texto = "\n".join(L)
    print(texto)
    with open("relatorio_reversao.txt", "w", encoding="utf-8") as f:
        f.write(texto)
    sem.rename("usd").to_csv("reversao_semanas.csv")

    if os.environ.get("TELEGRAM_TOKEN"):
        bloco = ""
        for parte in texto.split("\n\n"):
            if len(bloco) + len(parte) > 3800:
                avisar(bloco)
                bloco = ""
            bloco += parte + "\n\n"
        if bloco.strip():
            avisar(bloco)


if __name__ == "__main__":
    main()
