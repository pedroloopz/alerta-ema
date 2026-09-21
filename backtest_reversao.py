"""
Teste minucioso da REVERSAO SEMANAL nos ativos liquidos, em historico longo.

REGRA BASE (a aprovada no teste de teses):
  toda semana, entre os liquidos, compra os 4 que mais cairam nos ultimos
  5 pregoes e vende os 4 que mais subiram. Segura ate a semana seguinte.

VEREDITO: 5 criterios definidos ANTES de ver o resultado.

VARIACOES (stop, alvo, DCA, frequencia): so contam se melhorarem a base
no treino (2015-2021) E na validacao (2022-2026).

Historico longo vem das acoes subjacentes; custo aplicado e o do perp x1,5.
"""

import io
import os

import numpy as np
import pandas as pd
import requests

from simbolos import SIMBOLOS, nome_curto
from alerta_ema import baixar_candles, custo_ida_volta, avisar

INICIO = "2015-01-01"
INICIO_VALIDACAO = pd.Timestamp("2022-01-01")
LIQ_MIN = 200_000.0
ESTRESSE = 1.5
MARGEM = 1000.0                # US$ por posicao
ALAV_BASE = 2.0
LOOKBACK = 5
POR_LADO = 4
LIMITE_SALTO = 0.40
TOLERANCIA_PRECO = 0.15
REPS_SORTEIO = 1000
REPS_BOOTSTRAP = 2000
JANELA = 12

STOPS = [5.0, 8.0, 12.0]
ALVOS = [3.0, 5.0, 8.0]
REFORCO_QUEDA = 5.0            # DCA: segunda metade entra se piorar 5%
PENETRACAO = 0.20              # ordem limitada so executa se o preco passar 0,2% do nivel
T_MINIMO = 2.0                 # melhoria precisa de t >= 2 no treino E na validacao

SEM_SUBJACENTE = {
    "SPCX", "OPENAI", "ANTHROPIC", "UNITREE", "MINIMAX", "ZHIPU", "CXMT",
    "SAMSUNG", "SKHYNIX", "SKHY", "HYUNDAI", "MEITUAN", "KUAISHOU",
    "XIAOMI", "POPMART", "BNC", "GPRO",
}
MAPA = {"STXX": "STX", "BBX": "BB"}
CAMPOS = ["Open", "High", "Low", "Close"]


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
            liq[MAPA.get(curto, curto)] = {"perp": s, "vol": vol,
                                            "custo": custo_ida_volta(vol),
                                            "preco_perp": float(d["Close"].iloc[-1])}
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
            df = _naive(df[CAMPOS].dropna().copy())
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
    return _naive(df.loc[df.index >= INICIO, CAMPOS].dropna())


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


# ============================================================ motor
class Mercado:
    """Paineis em numpy para simular caminhos diarios rapido."""

    def __init__(self, hist, custos):
        tick = sorted(custos)
        paineis = {c: pd.DataFrame({t: hist[t][c] for t in tick}).sort_index() for c in CAMPOS}
        self.datas = paineis["Close"].index
        self.tick = np.array(tick)
        self.O, self.H, self.L, self.C = (paineis[c].to_numpy(float) for c in CAMPOS)
        self.custo = np.array([custos[t] for t in tick])
        ant = np.vstack([np.full(len(tick), np.nan), self.C[:-1]])
        with np.errstate(invalid="ignore", divide="ignore"):
            ruim = (np.abs(self.C / ant - 1) > LIMITE_SALTO) | (np.abs(self.O / ant - 1) > LIMITE_SALTO)
        self.ruim_acum = np.cumsum(np.nan_to_num(ruim, nan=0).astype(int), axis=0)

    def contaminado(self, a, b):
        antes = self.ruim_acum[a - 1] if a > 0 else 0
        return (self.ruim_acum[b] - antes) > 0

    def pontos(self, freq):
        idx = pd.Series(np.arange(len(self.datas)), index=self.datas)
        if freq == "D":
            return idx.to_list()
        sem = idx.groupby(self.datas.to_period("W")).first().to_list()
        return sem if freq == "W" else sem[::2]

    def periodos(self, freq, lookback):
        """Para cada rebalanceamento: tickers validos, sinal e retorno de compra."""
        pts = self.pontos(freq)
        saida = []
        for k, ks in zip(pts[:-1], pts[1:]):
            if k - 1 - lookback < 0:
                continue
            c_fim, c_ini, o_ent, o_sai = self.C[k - 1], self.C[k - 1 - lookback], self.O[k], self.O[ks]
            ok = ~(np.isnan(c_fim) | np.isnan(c_ini) | np.isnan(o_ent) | np.isnan(o_sai))
            ok &= ~self.contaminado(k - lookback, ks)
            j = np.where(ok)[0]
            if len(j) < 4:
                continue
            saida.append({"k": k, "ks": ks, "data": self.datas[k], "j": j,
                          "sinal": c_fim[j] / c_ini[j] - 1,
                          "bruto_long": (o_sai[j] / o_ent[j] - 1) * 100,
                          "custo": self.custo[j]})
        return saida


def selecionar(periodos, por_lado):
    pos = []
    for p in periodos:
        k = min(por_lado, len(p["j"]) // 2)
        ordem = np.argsort(p["sinal"])
        for idx, lado in ((ordem[:k], 1), (ordem[-k:], -1)):
            for i in idx:
                pos.append({"k": p["k"], "ks": p["ks"], "data": p["data"], "j": p["j"][i],
                            "lado": lado, "custo": p["custo"][i],
                            "bruto_simples": lado * p["bruto_long"][i]})
    return pos


def executar(m, pos, stop=None, alvo=None, dca=None, alav=ALAV_BASE):
    """
    Resultado por posicao, em % do nocional PLANEJADO (pessimista).

    Tranches: cada entrada e (fracao do nocional, preco). No DCA com reforco,
    se o preco nao chegar ao nivel, so metade fica investida - e o resultado
    e contado assim, sem dar tamanho cheio aos acertos.
    """
    limite_liq = (1 / alav) * 100 - 0.5
    nocional = MARGEM * alav
    linhas = []
    for p in pos:
        k, ks, j, lado = p["k"], p["ks"], p["j"], p["lado"]
        O, H, L = m.O[k:ks + 1, j], m.H[k:ks, j], m.L[k:ks, j]
        saida, liquidado = O[-1], False

        if dca == "tres_parcelas" and ks - k >= 3:
            tranches = [(1 / 3, O[0]), (1 / 3, O[1]), (1 / 3, O[2])]
        elif dca == "reforco":
            nivel = O[0] * (1 - lado * REFORCO_QUEDA / 100)
            gatilho = nivel * (1 - lado * PENETRACAO / 100)
            tranches = [(0.5, O[0])]
            for d in range(len(H)):
                if d > 0 and (O[d] - nivel) * lado <= 0:          # abriu alem do nivel
                    tranches.append((0.5, O[d]))
                    break
                extremo = L[d] if lado == 1 else H[d]
                if (extremo - gatilho) * lado <= 0:
                    tranches.append((0.5, nivel))
                    break
        else:
            tranches = [(1.0, O[0])]

        entrada = O[0]
        if dca is None and (stop or alvo):
            px_stop = entrada * (1 - lado * stop / 100) if stop else None
            px_alvo = entrada * (1 + lado * alvo / 100) if alvo else None
            gat_alvo = px_alvo * (1 + lado * PENETRACAO / 100) if alvo else None
            for d in range(len(H)):
                pior = L[d] if lado == 1 else H[d]
                melhor = H[d] if lado == 1 else L[d]
                if (entrada - pior) / entrada * 100 * lado >= limite_liq:
                    liquidado = True
                    break
                if d > 0 and px_stop and (O[d] - px_stop) * lado <= 0:
                    saida = O[d]; break
                if d > 0 and px_alvo and (O[d] - px_alvo) * lado >= 0:
                    saida = O[d]; break
                if px_stop and (pior - px_stop) * lado <= 0:     # stop antes do alvo
                    saida = px_stop * (1 - lado * PENETRACAO / 100)  # escorrega contra
                    break
                if gat_alvo and (melhor - gat_alvo) * lado >= 0:
                    saida = px_alvo; break
        else:
            extremo = L.min() if lado == 1 else H.max()
            if (entrada - extremo) / entrada * 100 * lado >= limite_liq:
                liquidado = True

        investido = sum(f for f, _ in tranches)
        if liquidado:
            bruto = -(1 / alav) * 100 * investido                  # perde a margem investida
        else:
            bruto = sum(f * (saida / e - 1) * 100 * lado for f, e in tranches)
        custo = p["custo"] * investido
        pess = bruto - ESTRESSE * custo
        linhas.append({"data": p["data"], "lado": lado, "custo": custo, "bruto": bruto,
                       "pess": pess, "por_investido": pess / investido,
                       "investido": investido, "liquidado": liquidado,
                       "usd": pess * nocional / 100})
    return pd.DataFrame(linhas)


def teste_t(dif_semanal):
    d = dif_semanal.dropna()
    if len(d) < 10 or d.std() == 0:
        return 0.0
    return float(d.mean() / (d.std() / np.sqrt(len(d))))


def sorteio(periodos, por_lado, reps, rng):
    totais = np.zeros(reps)
    for p in periodos:
        n = len(p["j"])
        k = min(por_lado, n // 2)
        esc = rng.random((reps, n)).argsort(axis=1)[:, :2 * k]
        bl, cu = p["bruto_long"][esc], p["custo"][esc]
        totais += ((bl[:, :k] - ESTRESSE * cu[:, :k]).sum(1)
                   + (-bl[:, k:] - ESTRESSE * cu[:, k:]).sum(1)) * MARGEM * ALAV_BASE / 100
    return totais


# ============================================================= apoio
def semanal(df):
    return df.groupby("data")["usd"].sum()


def drawdown(serie):
    c = serie.cumsum()
    return float((c.cummax().clip(lower=0) - c).max())


fmt = lambda x: f"{x:+.2f}".replace(".", ",")
usd = lambda x: f"{x:+,.0f}".replace(",", ".")
ok = lambda b: "SIM" if b else "nao"


def dividir(df):
    return df[df["data"] < INICIO_VALIDACAO], df[df["data"] >= INICIO_VALIDACAO]


def main():
    rng = np.random.default_rng(42)
    liq = universo_liquido()
    hist, fonte = carregar_historico(sorted(liq) + ["QQQ"])

    aceitos, rejeitados = {}, []
    for t, info in liq.items():
        if t not in hist:
            rejeitados.append(f"{t}(sem dados)")
            continue
        razao = float(hist[t]["Close"].iloc[-1]) / info["preco_perp"]
        if abs(razao - 1) > TOLERANCIA_PRECO:
            rejeitados.append(f"{t}({razao:.2f}x)")
            continue
        aceitos[t] = info
    print("Aceitos:", len(aceitos), "| Rejeitados:", rejeitados)

    m = Mercado(hist, {t: aceitos[t]["custo"] for t in aceitos})
    per = {lb: m.periodos("W", lb) for lb in (3, 5, 10)}
    base_pos = selecionar(per[LOOKBACK], POR_LADO)
    base = executar(m, base_pos)
    sem = semanal(base)
    base_tr, base_va = dividir(base)

    M1, M2, M3 = [], [], []

    # ======================================== MENSAGEM 1: VEREDITO
    anos = base.groupby(base["data"].dt.year)
    anos_pos = sum(semanal(sub).sum() > 0 for _, sub in anos)
    n_anos = anos.ngroups

    grade = {}
    for lb in (3, 5, 10):
        for k in (2, 4, 6):
            grade[(lb, k)] = executar(m, selecionar(per[lb], k))["pess"].mean()
    celulas = sum(v > 0 for v in grade.values())

    aleat = sorteio(per[LOOKBACK], POR_LADO, REPS_SORTEIO, rng)
    p_acaso = (aleat >= sem.sum()).mean()
    bruto_med, custo_med = base["bruto"].mean(), base["custo"].mean()

    crit = [
        ("resultado geral positivo (custo x1,5)", base["pess"].mean() > 0,
         f"{fmt(base['pess'].mean())}% por posicao"),
        ("60%+ dos anos positivos", anos_pos / n_anos >= 0.6, f"{anos_pos} de {n_anos}"),
        ("6+ de 9 na vizinhanca", celulas >= 6, f"{celulas} de 9"),
        ("acaso vence menos de 5%", p_acaso < 0.05, f"{p_acaso*100:.1f}%"),
        ("custo suportavel 2x+ o estimado", bruto_med >= 2 * custo_med,
         f"{bruto_med / custo_med:.1f}x".replace(".", ",") if custo_med else "-"),
    ]
    aprovada = all(c[1] for c in crit)

    M1 += ["REVERSAO SEMANAL - TESTE LONGO",
           f"{sem.index.min():%m/%Y} a {sem.index.max():%m/%Y} | {len(sem)} semanas | "
           f"{len(aceitos)} acoes", "",
           f"VEREDITO: {'APROVADA' if aprovada else 'REPROVADA'}"]
    for nome, passou, valor in crit:
        M1.append(f"  {'✅' if passou else '❌'} {nome}: {valor}")
    M1 += ["",
           "RESULTADO GERAL (2x)",
           f"  semanas positivas: {(sem > 0).mean()*100:.0f}%",
           f"  total: US$ {usd(sem.sum())} | media/semana US$ {usd(sem.mean())}",
           f"  drawdown max: US$ {drawdown(sem):,.0f}".replace(",", "."), "",
           "ANO A ANO (por posicao | total)"]
    for ano, sub in anos:
        M1.append(f"  {ano}: {fmt(sub['pess'].mean())}% | US$ {usd(semanal(sub).sum())}")

    # ======================================== MENSAGEM 2: DIAGNOSTICO
    M2.append("REGIME DE MERCADO (QQQ)")
    if "QQQ" in hist:
        q = hist["QQQ"]["Close"]
        acima = (q > q.rolling(200).mean()).shift(1)
        vol = q.pct_change().rolling(20).std()
        agitado = (vol > vol.rolling(252, min_periods=60).median()).shift(1)
        for nome, serie in (("mercado em alta", acima), ("mercado agitado", agitado)):
            marca = base["data"].map(lambda d: serie.asof(d))
            for val, rot in ((True, "sim"), (False, "nao")):
                sub = base[marca == val]
                if len(sub):
                    M2.append(f"  {nome} = {rot}: {fmt(sub['pess'].mean())}%")
    M2 += ["", "SPOT x 2x (so compra = o que o spot permite)"]
    for rot, lado in (("so compra (perdedores)", 1), ("so venda (vencedores)", -1)):
        sub = base[base["lado"] == lado]
        M2.append(f"  {rot}: {fmt(sub['pess'].mean())}%")

    b1 = executar(m, base_pos, alav=1.0)
    for rot, df in (("1x", b1), ("2x", base)):
        s = semanal(df)
        M2.append(f"  {rot}: total US$ {usd(s.sum())} | DD US$ {drawdown(s):,.0f} | "
                  f"pior semana US$ {usd(s.min())} | liquidacoes {int(df['liquidado'].sum())}"
                  .replace(",", "."))

    M2 += ["", "VIZINHANCA (pregoes x posicoes por lado)"]
    for lb in (3, 5, 10):
        M2.append(f"  {lb:>2d}p: " + "  ".join(f"{fmt(grade[(lb, k)])}%" for k in (2, 4, 6)))

    janelas = sem.rolling(JANELA).sum().dropna()
    M2 += ["", "TRIMESTRES (12 semanas)",
           f"  negativos: {(janelas < 0).mean()*100:.0f}% | pior US$ {usd(janelas.min())} | "
           f"melhor US$ {usd(janelas.max())}",
           "", "MARGEM CONTRA O CUSTO",
           f"  bruto {fmt(bruto_med)}% | custo {fmt(custo_med)}% | x1,5 {fmt(custo_med*ESTRESSE)}%"]

    medias = np.array([rng.choice(sem.to_numpy(), len(sem)).mean() for _ in range(REPS_BOOTSTRAP)])
    lo, hi = np.percentile(medias, [2.5, 97.5])
    M2 += [f"  media semanal 95%: US$ {usd(lo)} a {usd(hi)}"]

    # ======================================== MENSAGEM 3: VARIACOES
    M3 += ["VARIACOES", "treino 2015-21 | validacao 2022-26 | US$ ganho / US$ investido",
           "adota so se melhorar com t >= 2 NOS DOIS periodos", "",
           f"BASE: treino {fmt(base_tr['pess'].mean())}% | valid {fmt(base_va['pess'].mean())}%", ""]

    def ponderado(df):
        """Dolar ganho / dolar investido (soma sobre soma), em %."""
        inv = df["investido"].sum()
        return df["pess"].sum() / inv if inv else float("nan")

    def por_semana_ponderado(df):
        g = df.groupby("data")
        return g["pess"].sum() / g["investido"].sum()

    base_sem = por_semana_ponderado(base)

    def linha(rot, df):
        """Mesmas semanas da base; compara retorno por dolar investido, semana a semana."""
        dif = (por_semana_ponderado(df) - base_sem).dropna()
        t_tr = teste_t(dif[dif.index < INICIO_VALIDACAO])
        t_va = teste_t(dif[dif.index >= INICIO_VALIDACAO])
        tr, va = dividir(df)
        melhora = t_tr >= T_MINIMO and t_va >= T_MINIMO
        M3.append(f"  {rot}: {fmt(ponderado(tr))}% | {fmt(ponderado(va))}% "
                  f"| t {t_tr:+.1f}/{t_va:+.1f} -> {ok(melhora)}".replace(".", ","))
        return melhora

    familias = {}
    M3.append("STOP LOSS")
    familias["stop"] = sum(linha(f"stop {s:.0f}%", executar(m, base_pos, stop=s)) for s in STOPS)
    M3.append("ALVO (vende ao atingir)")
    familias["alvo"] = sum(linha(f"alvo {a:.0f}%", executar(m, base_pos, alvo=a)) for a in ALVOS)
    M3.append("DCA")
    familias["dca"] = linha("3 parcelas em 3 dias", executar(m, base_pos, dca="tres_parcelas"))
    ref = executar(m, base_pos, dca="reforco")
    familias["dca"] += linha(f"reforco se cair {REFORCO_QUEDA:.0f}%", ref)
    M3.append(f"    (reforco acionado em {(ref['investido'] > 0.5).mean()*100:.0f}% das posicoes)")

    M3.append("FREQUENCIA")
    for rot, freq, lb in (("diaria (1 pregao)", "D", 1), ("quinzenal (10 pregoes)", "2W", 10)):
        df = executar(m, selecionar(m.periodos(freq, lb), POR_LADO))
        tr, va = dividir(df)
        n_ano = len(df) / max(df["data"].dt.year.nunique(), 1)
        M3.append(f"  {rot}: {fmt(tr['pess'].mean())}% | {fmt(va['pess'].mean())}% | "
                  f"{n_ano:.0f} posicoes/ano | total US$ {usd(df['usd'].sum())}")
    M3.append(f"  semanal (base): {fmt(base_tr['pess'].mean())}% | {fmt(base_va['pess'].mean())}% | "
              f"{len(base)/max(n_anos,1):.0f} posicoes/ano | total US$ {usd(base['usd'].sum())}")

    M3 += ["", "LIMITACOES",
           "  - universo de hoje: vies de sobrevivencia",
           "  - acao nao e o perp: o perp pode descolar",
           "  - alvo e reforco exigem o preco passar 0,2% do nivel",
           "  - stop executa 0,2% pior que o nivel",
           f"  - fora do teste: {', '.join(rejeitados) or 'nenhum'}"]

    for bloco in (M1, M2, M3):
        texto = "\n".join(bloco)
        print(texto + "\n")
        if os.environ.get("TELEGRAM_TOKEN"):
            avisar(texto[:3900])

    with open("relatorio_reversao.txt", "w", encoding="utf-8") as f:
        f.write("\n\n".join("\n".join(b) for b in (M1, M2, M3)))
    sem.rename("usd").to_csv("reversao_semanas.csv")


if __name__ == "__main__":
    main()
