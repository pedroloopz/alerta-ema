"""
REVERSAO SO COMPRADA NO SPOT (1x, sem liquidacao, sem venda a descoberto).

Duas entradas, definidas antes de ver o resultado:
  A. toda semana, compra os 4 que mais cairam nos ultimos 5 pregoes
  B. compra quando o fechamento fica 2 ATR ou mais ABAIXO da EMA 20 diaria

Saidas comparadas: 1 semana | cruzar a EMA 20 de volta | alvos 3/5/8%.
Todas com prazo maximo de 20 pregoes.

Universo: tokens de acao no SPOT da Binance com volume >= US$ 200 mil/dia.
Historico longo: acoes subjacentes (2015+). Custo: taxa spot + slippage.

Veredito comparado com COMPRAR E SEGURAR as mesmas acoes.
"""

import io
import os

import numpy as np
import pandas as pd
import requests

from alerta_ema import avisar
from simbolos import SIMBOLOS, nome_curto

# ---------------------------------------------------------------- regras
INICIO = "2015-01-01"
INICIO_VALIDACAO = pd.Timestamp("2022-01-01")
SPOT_API = "https://data-api.binance.vision/api/v3"
LIQ_MIN = 200_000.0
TAXA_SPOT_LADO = 0.10          # % por lado. Ajuste para a taxa real da sua conta
SLIPPAGE = [(1_000_000, 0.10), (200_000, 0.25), (50_000, 0.50), (0, 1.00)]
ESTRESSE = 1.5                 # custo pessimista = 1,5x o estimado
MARGEM = 1000.0
MAX_POS = 8
CAPITAL = MARGEM * MAX_POS

LOOKBACK = 5                   # entrada A
POR_SEMANA = 4
ESTICADA_ATR = 2.0             # entrada B
PERIODO = 20                   # EMA e ATR
PRAZO_MAX = 20                 # pregoes
SEMANA = 5
ALVOS = [3.0, 5.0, 8.0]
PENETRACAO = 0.20
LIMITE_SALTO = 0.40
TOL_PRECO = 0.15
T_MIN = 2.0
REPS = 1000

SEM_SUBJACENTE = {"SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU",
                  "CXMT", "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN",
                  "KUAISHOU", "XIAOMI", "POPMART", "BNC", "GPRO"}
MAPA = {"STXX": "STX", "BBX": "BB"}
CAMPOS = ["Open", "High", "Low", "Close"]

fmt = lambda x: f"{x:+.2f}".replace(".", ",")
usd = lambda x: f"{x:+,.0f}".replace(",", ".")
num0 = lambda x: f"{x:,.0f}".replace(",", ".")


def custo_spot(vol):
    for minimo, slip in SLIPPAGE:
        if vol >= minimo:
            return 2 * TAXA_SPOT_LADO + slip
    return 2 * TAXA_SPOT_LADO + SLIPPAGE[-1][1]


# ============================================================ universo
def conhecidos():
    """Acoes da sua lista que tem subjacente americano."""
    return {MAPA.get(nome_curto(s), nome_curto(s)) for s in SIMBOLOS} - SEM_SUBJACENTE


def universo_spot():
    """
    Tokens de acao no spot (ex.: NVDAB/USDT) cujo nome bate com a sua lista.
    Restringir a lista evita confundir cripto com acao (TRB -> 'TR').
    """
    alvo = conhecidos()
    info = requests.get(f"{SPOT_API}/exchangeInfo", timeout=60).json()
    tokens = {}
    for s in info.get("symbols", []):
        base = s.get("baseAsset", "")
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING" \
                and base.endswith("B") and base[:-1] in alvo:
            tokens[s["symbol"]] = base[:-1]
    tick = requests.get(f"{SPOT_API}/ticker/24hr", timeout=60).json()
    dados = {t["symbol"]: t for t in tick}
    saida = {}
    for simbolo, acao in tokens.items():
        t = dados.get(simbolo)
        if t:
            saida[acao] = {"token": simbolo, "vol": float(t["quoteVolume"]),
                           "preco": float(t["lastPrice"])}
    print(f"{len(saida)} tokens spot da sua lista encontrados")
    return saida


def universo_reserva():
    """Se o spot nao responder: todos os tickers com subjacente, sem filtro de volume."""
    saida = {}
    for s in SIMBOLOS:
        c = nome_curto(s)
        if c not in SEM_SUBJACENTE:
            saida[MAPA.get(c, c)] = {"token": "-", "vol": LIQ_MIN, "preco": None}
    return saida


# ============================================================= historico
def _naive(df):
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


def historico(tickers):
    hist = {}
    try:
        import yfinance as yf
        bruto = yf.download(tickers, start=INICIO, interval="1d", auto_adjust=True,
                            group_by="ticker", progress=False, threads=True)
        for t in tickers:
            try:
                df = bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto
                df = _naive(df[CAMPOS].dropna().copy())
                if len(df) > 150:
                    hist[t] = df
            except Exception:
                pass
    except Exception as e:
        print("Yahoo falhou:", e)
    for t in tickers:
        if t in hist:
            continue
        try:
            r = requests.get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d", timeout=30)
            if r.status_code == 200 and r.text.startswith("Date"):
                df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"]).set_index("Date")
                df = _naive(df.loc[df.index >= INICIO, CAMPOS].dropna())
                if len(df) > 150:
                    hist[t] = df
        except Exception:
            pass
    return hist


# ================================================================ motor
class Mercado:
    def __init__(self, hist, custos):
        self.tick = sorted(custos)
        p = {c: pd.DataFrame({t: hist[t][c] for t in self.tick}).sort_index() for c in CAMPOS}
        self.datas = p["Close"].index
        self.O, self.H, self.L, self.C = (p[c].to_numpy(float) for c in CAMPOS)
        self.custo = np.array([custos[t] for t in self.tick])
        C = p["Close"]
        self.EMA = C.ewm(span=PERIODO, adjust=False).mean().to_numpy()
        ant = C.shift(1)
        tr = pd.concat([p["High"] - p["Low"], (p["High"] - ant).abs(), (p["Low"] - ant).abs()]) \
            .groupby(level=0).max().reindex(C.index)
        self.ATR = tr.ewm(alpha=1 / PERIODO, adjust=False).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            a = ant.to_numpy()
            ruim = (np.abs(self.C / a - 1) > LIMITE_SALTO) | (np.abs(self.O / a - 1) > LIMITE_SALTO)
        self.ruim = np.cumsum(np.nan_to_num(ruim).astype(int), axis=0)
        self.n = len(self.datas)
        self.semanas = pd.Series(np.arange(self.n), index=self.datas) \
            .groupby(self.datas.to_period("W")).first().to_list()

    def limpo(self, a, b, j):
        a, b = max(a, 1), min(b, self.n - 1)
        return self.ruim[b, j] - self.ruim[a - 1, j] == 0

    # ------------------------------------------------------- entradas
    def candidatos_A(self):
        out = []
        for k in self.semanas:
            if k - 1 - LOOKBACK < 0 or k >= self.n - 2:
                continue
            ret = self.C[k - 1] / self.C[k - 1 - LOOKBACK] - 1
            ok = ~np.isnan(ret) & ~np.isnan(self.O[k])
            js = [j for j in np.where(ok)[0] if self.limpo(k - LOOKBACK, k + PRAZO_MAX, j)]
            if len(js) < POR_SEMANA * 2:
                continue
            for j in sorted(js, key=lambda x: ret[x])[:POR_SEMANA]:
                out.append((k, j, ret[j]))
        return out

    def candidatos_B(self):
        with np.errstate(invalid="ignore", divide="ignore"):
            esticada = (self.EMA - self.C) / self.ATR
        out = []
        for t, j in zip(*np.where(esticada >= ESTICADA_ATR)):
            k = t + 1
            if t < PERIODO * 2 or k >= self.n - 2 or np.isnan(self.O[k, j]):
                continue
            if self.limpo(t - PERIODO, k + PRAZO_MAX, j):
                out.append((k, j, -esticada[t, j]))
        return out

    # ---------------------------------------------------------- saidas
    def sair(self, k, j, regra):
        """Devolve (indice do dia de saida, preco de saida, motivo)."""
        entrada = self.O[k, j]
        fim = min(k + PRAZO_MAX, self.n - 1)
        if regra == "semana":
            d = min(k + SEMANA, self.n - 1)
            return d, self.O[d, j], "tempo"
        if regra == "ema":
            for d in range(k, fim):
                if self.C[d, j] >= self.EMA[d, j]:
                    return d + 1, self.O[d + 1, j], "ema"
            return fim, self.O[fim, j], "prazo"
        alvo = entrada * (1 + regra / 100)
        gatilho = alvo * (1 + PENETRACAO / 100)
        for d in range(k, fim):
            if d > k and self.O[d, j] >= alvo:
                return d, self.O[d, j], "alvo"
            if self.H[d, j] >= gatilho:
                return d, alvo, "alvo"
        return fim, self.O[fim, j], "prazo"


def carteira(m, cands, regra):
    """Respeita no maximo 8 posicoes e 1 por acao. Prioridade: maior queda."""
    trades = []
    for k, j, prio in cands:
        d, px, motivo = m.sair(k, j, regra)
        if np.isnan(px):
            continue
        trades.append((k, prio, j, d, px, motivo))
    trades.sort(key=lambda x: (x[0], x[1]))
    abertas, aceitos = [], []
    for k, _, j, d, px, motivo in trades:
        abertas = [(dd, jj) for dd, jj in abertas if dd > k]
        if len(abertas) >= MAX_POS or any(jj == j for _, jj in abertas):
            continue
        abertas.append((d, j))
        bruto = (px / m.O[k, j] - 1) * 100
        aceitos.append({"entrada": m.datas[k], "saida": m.datas[d], "acao": m.tick[j],
                        "dias": d - k, "motivo": motivo, "bruto": bruto,
                        "custo": m.custo[j],
                        "pess": bruto - ESTRESSE * m.custo[j],
                        "dobro": bruto - 2 * ESTRESSE * m.custo[j]})
    df = pd.DataFrame(aceitos)
    if not df.empty:
        df["usd"] = df["pess"] * MARGEM / 100
        df["usd_dobro"] = df["dobro"] * MARGEM / 100
        df["semana"] = pd.to_datetime(df["saida"]).dt.to_period("W").dt.start_time
    return df


def comprar_e_segurar(m):
    """US$ 8.000 sempre investidos, divididos igualmente entre as acoes disponiveis."""
    with np.errstate(invalid="ignore", divide="ignore"):
        r = m.C[1:] / m.C[:-1] - 1
    r[np.abs(r) > LIMITE_SALTO] = np.nan
    diario = pd.Series(np.nanmean(r, axis=1) * CAPITAL, index=m.datas[1:]).fillna(0)
    return diario.groupby(diario.index.to_period("W").start_time).sum()


def teste_t(serie):
    s = serie.dropna()
    if len(s) < 10 or s.std() == 0:
        return 0.0
    return float(s.mean() / (s.std() / np.sqrt(len(s))))


def partes(serie):
    return serie[serie.index < INICIO_VALIDACAO], serie[serie.index >= INICIO_VALIDACAO]


# ================================================================ main
def main():
    rng = np.random.default_rng(7)
    try:
        univ, modo = universo_spot(), "spot Binance"
        if not univ:
            raise RuntimeError("nenhum token encontrado")
    except Exception as e:
        print("Spot indisponivel:", e)
        univ, modo = universo_reserva(), "RESERVA (spot nao respondeu)"

    liquidos = {a: i for a, i in univ.items() if i["vol"] >= LIQ_MIN}
    hist = historico(sorted(liquidos) + ["QQQ"])

    aceitos, rejeitados = {}, []
    for a, i in liquidos.items():
        if a not in hist:
            rejeitados.append(a)
            continue
        if i["preco"] and abs(float(hist[a]["Close"].iloc[-1]) / i["preco"] - 1) > TOL_PRECO:
            rejeitados.append(a)
            continue
        aceitos[a] = i

    M1, M2, M3 = [], [], []
    M1 += ["REVERSAO SO COMPRADA - SPOT 1x", f"universo: {modo}",
           f"da sua lista: {len(univ)} tem token spot | {len(liquidos)} com US$ 200 mil+/dia "
           f"| {len(aceitos)} com historico"]
    if len(aceitos) < POR_SEMANA * 3:
        M1.append("POUCAS ACOES PARA TESTAR - resultado nao seria confiavel.")
        texto = "\n".join(M1)
        print(texto)
        if os.environ.get("TELEGRAM_TOKEN"):
            avisar(texto)
        return

    top = sorted(aceitos.items(), key=lambda x: -x[1]["vol"])[:12]
    M1.append("maiores volumes: " + ", ".join(f"{a} {num0(i['vol']/1000)}k" for a, i in top))
    custos = {a: custo_spot(i["vol"]) for a, i in aceitos.items()}
    M1.append(f"custo medio estimado: {fmt(np.mean(list(custos.values())))}% ida e volta "
              f"(x1,5 no teste)")

    m = Mercado(hist, custos)
    bh = comprar_e_segurar(m)
    bh_tr, bh_va = partes(bh)

    regras = [("semana", "1 semana"), ("ema", "cruzar a EMA")] + \
             [(a, f"alvo {a:.0f}%") for a in ALVOS]
    entradas = {"A": ("perdedores da semana", m.candidatos_A()),
                "B": (f"{ESTICADA_ATR:.0f} ATR abaixo da EMA", m.candidatos_B())}

    resultados = {}
    for e, (nome, cands) in entradas.items():
        for regra, _ in regras:
            resultados[(e, regra)] = carteira(m, cands, regra)

    # ------------------------------------------------ vereditos
    def veredito(e, regra, rotulo, com_sorteio):
        df = resultados[(e, regra)]
        sem = df.groupby("semana")["usd"].sum().reindex(bh.index, fill_value=0)
        sem2 = df.groupby("semana")["usd_dobro"].sum().reindex(bh.index, fill_value=0)
        tr, va = partes(sem)
        crit = [("lucro no treino (custo x1,5)", tr.sum() > 0, f"US$ {usd(tr.sum())}"),
                ("lucro na validacao", va.sum() > 0, f"US$ {usd(va.sum())}"),
                ("bate comprar e segurar na validacao", va.sum() > bh_va.sum(),
                 f"US$ {usd(va.sum())} x {usd(bh_va.sum())}"),
                ("positiva com custo em dobro", sem2.sum() > 0, f"US$ {usd(sem2.sum())}")]
        if com_sorteio:
            cands = entradas["A"][1]
            semanas_k = sorted({k for k, _, _ in cands})
            totais = np.zeros(REPS)
            for k in semanas_k:
                ret = m.C[k - 1] / m.C[k - 1 - LOOKBACK] - 1
                d = min(k + SEMANA, m.n - 1)
                ok = ~np.isnan(ret) & ~np.isnan(m.O[k]) & ~np.isnan(m.O[d])
                js = np.where(ok)[0]
                if len(js) < POR_SEMANA * 2:
                    continue
                br = (m.O[d, js] / m.O[k, js] - 1) * 100 - ESTRESSE * m.custo[js]
                esc = rng.random((REPS, len(js))).argsort(axis=1)[:, :POR_SEMANA]
                totais += br[esc].sum(axis=1) * MARGEM / 100
            p = (totais >= sem.sum()).mean()
            crit.append(("acaso vence menos de 5%", p < 0.05, f"{p*100:.1f}%"))
        passou = all(c[1] for c in crit)
        M1.append("")
        M1.append(f"{rotulo}: {'APROVADA' if passou else 'REPROVADA'}")
        for n, ok_, v in crit:
            M1.append(f"  {'✅' if ok_ else '❌'} {n}: {v}")

    veredito("A", "semana", "A. PERDEDORES DA SEMANA, segura 1 semana", True)
    veredito("B", "ema", "B. 2 ATR ABAIXO DA EMA, sai ao cruzar a EMA", False)
    M1 += ["", f"Referencia comprar e segurar: treino US$ {usd(bh_tr.sum())} | "
               f"valid US$ {usd(bh_va.sum())}"]
    if "QQQ" in hist:
        q = hist["QQQ"]["Close"]
        M1.append(f"QQQ no periodo: {fmt((q.iloc[-1] / q.iloc[0] - 1) * 100)}%")

    # ------------------------------------------------ detalhe das saidas
    for e, (nome, _) in entradas.items():
        M2.append(f"ENTRADA {e}: {nome}")
        M2.append("saida | treino | valid (por trade) | dias | prazo% | pior | t")
        base_sem = resultados[(e, regras[0][0] if e == "A" else "ema")] \
            .groupby("semana")["usd"].sum().reindex(bh.index, fill_value=0)
        for regra, rot in regras:
            df = resultados[(e, regra)]
            if df.empty:
                M2.append(f"  {rot}: sem trades")
                continue
            tr = df[df["entrada"] < INICIO_VALIDACAO]
            va = df[df["entrada"] >= INICIO_VALIDACAO]
            sem = df.groupby("semana")["usd"].sum().reindex(bh.index, fill_value=0)
            dif_tr, dif_va = partes(sem - base_sem)
            tt, tv = teste_t(dif_tr), teste_t(dif_va)
            M2.append(f"  {rot}: {fmt(tr['pess'].mean())}% | {fmt(va['pess'].mean())}% | "
                      f"{df['dias'].mean():.0f}d | {(df['motivo']=='prazo').mean()*100:.0f}% | "
                      f"{fmt(df['pess'].min())}% | t {tt:+.1f}/{tv:+.1f}".replace(".", ","))
        M2.append("")
    M2 += ["t: comparacao com a saida principal (A: 1 semana | B: cruzar EMA).",
           f"Uma saida so substitui se t >= {T_MIN:.0f} no treino E na validacao.".replace(".", ",")]

    # ------------------------------------------------ ano a ano
    M3.append("ANO A ANO (US$)")
    M3.append("ano | A 1 semana | B cruzar EMA | segurar")
    sA = resultados[("A", "semana")].groupby("semana")["usd"].sum().reindex(bh.index, fill_value=0)
    sB = resultados[("B", "ema")].groupby("semana")["usd"].sum().reindex(bh.index, fill_value=0)
    ganha = {"A": 0, "B": 0}
    anos = sorted(set(bh.index.year))
    for ano in anos:
        a_, b_, h_ = (s[s.index.year == ano].sum() for s in (sA, sB, bh))
        ganha["A"] += a_ > h_
        ganha["B"] += b_ > h_
        M3.append(f"  {ano}: {usd(a_)} | {usd(b_)} | {usd(h_)}")
    M3 += [f"  anos batendo o segurar: A {ganha['A']} de {len(anos)} | "
           f"B {ganha['B']} de {len(anos)}", ""]
    for e in ("A", "B"):
        df = resultados[(e, "semana" if e == "A" else "ema")]
        M3.append(f"{e}: {len(df)} trades | capital medio investido "
                  f"US$ {num0(df['dias'].sum() / m.n * MARGEM)}")
    M3 += ["", "LIMITACOES",
           "  - universo de hoje: vies de sobrevivencia",
           "  - historico das acoes; o token pode descolar",
           f"  - taxa spot assumida: {fmt(TAXA_SPOT_LADO)}% por lado",
           f"  - fora do teste: {', '.join(rejeitados[:15]) or 'nenhum'}"
           f"{'...' if len(rejeitados) > 15 else ''}"]

    for bloco in (M1, M2, M3):
        texto = "\n".join(bloco)
        print(texto + "\n")
        if os.environ.get("TELEGRAM_TOKEN"):
            avisar(texto[:3900])
    with open("relatorio_spot.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join("\n".join(b) for b in (M1, M2, M3)))


if __name__ == "__main__":
    main()
