"""Kronos-small: 100 futuros possíveis das próximas 24 velas de 1h + teste semanal de 1 ano.

Só leitura: não usa chave nem manda ordem. O robô (robo-bingx) lê kronos/sinais.json e decide.

Etapas (workflow kronos.yml):
  python kronos/prever.py lista            -> saida/universo.json (30 criptos + ações dos EUA na BingX)
  python kronos/prever.py rodar <i> <n>    -> saida/parte_<i>.json (ativos i, i+n, i+2n...)
  python kronos/prever.py juntar           -> kronos/previsao.json, sinais.json, grafico.html, grafico.png
  python kronos/prever.py conferir         -> põe as velas reais ao lado dos caminhos e dá o placar após 24h

Probabilidade: o modelo sorteia cada vela (temperatura 1, top_p 0,9); 100 sorteios = 100 caminhos.
P(alta) = fração dos caminhos que fecham a 24ª vela acima do último fechamento.
Teste de 1 ano: 52 pontos de partida, um por semana, no mesmo dia e hora de agora (8 caminhos cada).
"""
import datetime
import inspect
import json
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
SAIDA = os.environ.get("KRONOS_SAIDA", "saida")
KRONOS_DIR = os.environ.get("KRONOS_DIR", "Kronos")
CTX, H = 512, 24                    # 512 velas de contexto, 24 velas previstas
N_VIVO = int(os.environ.get("N_VIVO", 100))
N_TESTE = int(os.environ.get("N_TESTE", 8))
SEMANAS = int(os.environ.get("SEMANAS", 52))
LOTE = 48                           # linhas (série x sorteio) por passada: cabe na memória do GitHub
CUSTO = 0.0012                      # taxas de ida e volta + deslize
N_CRIPTO = 30
ACOES = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "JPM", "LLY", "V", "WMT", "ORCL",
         "MA", "NFLX", "COST", "AMD", "PLTR", "UNH",                                   # top 20 EUA (tese)
         "CRWV", "NBIS", "IREN", "APLD", "CIFR", "WULF", "HUT", "CORZ", "GLXY", "BTDR"]  # neoclouds (tese)
ESTAVEIS = {"USDC", "USDT", "FDUSD", "TUSD", "USDE", "DAI", "USD1", "PYUSD", "BUSD", "USDP", "USDS", "USDD", "RLUSD",
            "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "BSC-USD", "XAUT", "PAXG", "LEO", "USDTB", "BUIDL",
            "USYC", "SUSDE", "SUSDS", "JITOSOL", "RETH", "METH", "CBETH", "BNSOL", "LBTC", "SOLVBTC", "USDF", "USDG"}
BASES = ["https://open-api.bingx.com", "https://open-api.bingx.pro"]

# regras do trade (o robô aplica sobre o preço da hora da entrada)
P_MIN = 0.65                        # pelo menos 65% dos 100 caminhos a favor
ACERTO_MIN = 0.55                   # no teste de 1 ano, acertou a direção em 55%+ das semanas
SEMANAS_MIN = 20
MOVE_MIN = 0.005                    # mediana prevista anda pelo menos 0,5% a favor
MAX_TRADES = 4
SL_MIN, SL_MAX = 0.008, 0.05
RISCO_MARGEM = 0.15                 # alavancagem tal que o stop custe ~15% da margem do trade
ALAV_MIN, ALAV_MAX = 2, 10


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------- dados
def _get(url, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tentativas - 1:
                raise
            time.sleep(2 + 3 * i)


def bingx(caminho, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    erro = None
    for base in BASES:
        try:
            d = _get(f"{base}{caminho}?{q}")
            if d.get("code", 0) != 0:
                raise RuntimeError(f"{caminho}: {d.get('code')} {d.get('msg')}")
            return d.get("data")
        except Exception as e:
            erro = e
    raise erro


def velas(simbolo, n):
    """Últimas n velas FECHADAS de 1h da BingX (pagina para trás de 1.440 em 1.440)."""
    por, fim = {}, None
    agora_ms = int(time.time() * 1000)
    while len(por) < n:
        p = {"symbol": simbolo, "interval": "1h", "limit": 1440}
        if fim:
            p["endTime"] = fim
        d = [x for x in (bingx("/openApi/swap/v3/quote/klines", p) or []) if isinstance(x, dict)]
        if not d:
            break
        antes = len(por)
        for x in d:
            por[int(x["time"])] = x
        menor = min(int(x["time"]) for x in d)
        if len(por) == antes or len(d) < 100:
            break
        fim = menor - 1
    df = pd.DataFrame([{"t": t, "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]),
                        "close": float(x["close"]), "volume": float(x.get("volume") or 0)}
                       for t, x in sorted(por.items()) if t + 3_600_000 <= agora_ms])     # só velas fechadas
    df["data"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df.tail(n).reset_index(drop=True)


def lista():
    contratos = {c["symbol"]: c for c in bingx("/openApi/swap/v2/quote/contracts", {})}
    ativos = []
    try:
        mercado = _get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc"
                       "&per_page=120&page=1")
        simbolos = [str(x["symbol"]).upper() for x in mercado]
    except Exception as e:
        log(f"CoinGecko falhou ({e}); uso a lista fixa")
        simbolos = ("BTC ETH XRP BNB SOL TRX DOGE ADA HYPE LINK BCH XLM SUI AVAX LTC HBAR TON SHIB DOT UNI XMR "
                    "AAVE ENA PEPE NEAR APT ONDO ETC ICP TAO POL ARB FIL ATOM").split()
    for s in simbolos:
        if s in ESTAVEIS:
            continue
        sim = next((f"{p}{s}-USDT" for p in ("", "1000") if f"{p}{s}-USDT" in contratos), None)
        if sim and all(a["simbolo"] != sim for a in ativos):
            ativos.append({"ativo": s, "simbolo": sim, "tipo": "cripto"})
        if len(ativos) >= N_CRIPTO:
            break
    for t in ACOES:
        sim = next((s for s in contratos if s.upper().startswith(f"NCSK{t}2")), None)
        if sim:
            ativos.append({"ativo": t, "simbolo": sim, "tipo": "ação"})
        else:
            log(f"{t}: sem contrato na BingX")
    os.makedirs(SAIDA, exist_ok=True)
    with open(os.path.join(SAIDA, "universo.json"), "w") as f:
        json.dump(ativos, f, ensure_ascii=False)
    log(f"{len(ativos)} ativos: " + ", ".join(a["ativo"] for a in ativos))


# ---------------------------------------------------------------- modelo
def carregar_modelo():
    sys.path.insert(0, KRONOS_DIR)
    import model.kronos as mk
    from model import Kronos, KronosTokenizer
    fonte = inspect.getsource(mk.auto_regressive_inference)
    assert "preds = np.mean(preds, axis=1)" in fonte, "Kronos mudou: revisar a extração dos caminhos"
    ns = {}
    exec(fonte.replace("preds = np.mean(preds, axis=1)", "pass"), mk.__dict__, ns)   # devolve TODOS os sorteios
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base").eval()
    mod = Kronos.from_pretrained("NeoQuasar/Kronos-small").eval()
    return tok, mod, ns["auto_regressive_inference"], mk.calc_time_stamps


def amostrar(motor, janelas, n):
    """janelas: lista de DataFrames com CTX velas. Devolve array (len(janelas), n, H, 6) em preço real."""
    import torch
    tok, mod, inferir, stamps = motor
    cols = ["open", "high", "low", "close", "volume", "amount"]
    xs, xst, yst, med, dp = [], [], [], [], []
    for w in janelas:
        w = w.copy()
        w["amount"] = w["volume"] * w[["open", "high", "low", "close"]].mean(axis=1)
        x = w[cols].values.astype(np.float32)
        m, s = x.mean(0), x.std(0)
        xs.append(np.clip((x - m) / (s + 1e-5), -5, 5))
        xst.append(stamps(w["data"].reset_index(drop=True)).values.astype(np.float32))
        fut = pd.Series(pd.date_range(w["data"].iloc[-1] + pd.Timedelta(hours=1), periods=H, freq="h"))
        yst.append(stamps(fut).values.astype(np.float32))
        med.append(m)
        dp.append(s)
    saida = np.zeros((len(janelas), n, H, 6), dtype=np.float32)
    por_vez = max(1, LOTE // n) if n <= LOTE else 1
    passo = min(n, LOTE)
    for i in range(0, len(janelas), por_vez):
        bloco = slice(i, i + por_vez)
        for j in range(0, n, passo):
            k = min(passo, n - j)
            with torch.no_grad():
                p = inferir(tok, mod, torch.tensor(np.stack(xs[bloco])), torch.tensor(np.stack(xst[bloco])),
                            torch.tensor(np.stack(yst[bloco])), CTX, H, 5, 1.0, 0, 0.9, k)
            saida[bloco, j:j + k] = np.asarray(p)[:, :, -H:, :]
    for i in range(len(janelas)):
        saida[i] = saida[i] * (dp[i] + 1e-5) + med[i]
    return saida


def resumo_caminhos(a, ultimo):
    """a: (n, H, 6). Estatísticas que o painel e o robô usam."""
    fech = a[:, :, 3]
    final = fech[:, -1]
    mins, maxs = a[:, :, 2].min(1), a[:, :, 1].max(1)
    q = lambda v, p: float(np.quantile(v, p))
    return {"p_alta": float((final > ultimo).mean()),
            "ret_mediano": q(final, 0.5) / ultimo - 1,
            "ret_q": {k: q(final, p) / ultimo - 1 for k, p in (("p10", .1), ("p25", .25), ("p75", .75), ("p90", .9))},
            "queda_q15": 1 - q(mins, 0.15) / ultimo,           # quanto 85% dos caminhos NÃO caem abaixo
            "alta_q15": q(maxs, 0.85) / ultimo - 1}            # quanto 85% dos caminhos NÃO sobem acima


def rodar(i, n_partes):
    ativos = json.load(open(os.path.join(SAIDA, "universo.json")))[i::n_partes]
    motor = carregar_modelo()
    out = []
    for a in ativos:
        t0 = time.time()
        try:
            df = velas(a["simbolo"], CTX + SEMANAS * 168 + H)
        except Exception as e:
            log(f"{a['ativo']}: sem dados ({e})")
            continue
        if len(df) < CTX + 1:
            log(f"{a['ativo']}: só {len(df)} velas")
            continue
        janela = df.tail(CTX).reset_index(drop=True)
        ultimo = float(janela["close"].iloc[-1])
        cam = amostrar(motor, [janela], N_VIVO)[0]
        r = {**a, "ultimo": ultimo, "origem": janela["data"].iloc[-1].isoformat(), **resumo_caminhos(cam, ultimo),
             "caminhos": np.round(cam[:, :, 3], 8).tolist(),
             "velas_medianas": np.round(np.median(cam[:, :, :4], axis=0), 8).tolist(),
             "reais": df.tail(168)[["t", "open", "high", "low", "close", "volume"]].values.tolist(),
             "atr_1h": float((df["high"] - df["low"]).tail(48).mean() / ultimo),
             "volume_24h_usd": float((df["volume"] * df["close"]).tail(24).sum())}
        # teste de 1 ano: 1 partida por semana, no mesmo dia/hora de agora
        origens = [len(df) - k * 168 for k in range(1, SEMANAS + 1) if len(df) - k * 168 - CTX >= 0]
        bt = []
        if origens:
            jan = [df.iloc[o - CTX:o].reset_index(drop=True) for o in origens]
            sa = amostrar(motor, jan, N_TESTE)
            for o, s_ in zip(origens, sa):
                ult = float(df["close"].iloc[o - 1])
                real = float(df["close"].iloc[min(o + H - 1, len(df) - 1)]) / ult - 1
                p = float((s_[:, -1, 3] > ult).mean())
                bt.append({"data": df["data"].iloc[o - 1].isoformat(), "p_alta": p,
                           "ret_mediano": float(np.median(s_[:, -1, 3])) / ult - 1, "ret_real": real})
        r["teste"] = bt
        r["teste_resumo"] = medir_teste(bt)
        out.append(r)
        log(f"{a['ativo']}: P(alta) {r['p_alta']:.0%} · teste {len(bt)} semanas · {time.time() - t0:.0f}s")
        os.makedirs(SAIDA, exist_ok=True)
        with open(os.path.join(SAIDA, f"parte_{i}.json"), "w") as f:     # grava a cada ativo (se o tempo acabar,
            json.dump(out, f)                                             # o que já saiu não se perde)


def medir_teste(bt):
    if not bt:
        return {"n": 0}
    p = np.array([x["p_alta"] for x in bt])
    real = np.array([x["ret_real"] for x in bt])
    sobe = (real > 0).astype(float)
    lado = np.where(p >= 0.5, 1, -1)
    ganho = lado * real - CUSTO
    conf = np.maximum(p, 1 - p) >= 0.625                       # 5 de 8 caminhos ou mais a favor
    return {"n": len(bt),
            "acerto": float(((lado > 0) == (real > 0)).mean()),
            "chute": float(max(sobe.mean(), 1 - sobe.mean())),  # acerto de quem aposta sempre no lado mais comum
            "brier": float(((p - sobe) ** 2).mean()),           # 0,25 = moeda; menor é melhor
            "ret_medio": float(ganho.mean()),                   # seguindo todo sinal, já com custo
            "n_conf": int(conf.sum()),
            "acerto_conf": float(((lado > 0) == (real > 0))[conf].mean()) if conf.any() else None,
            "ret_conf": float(ganho[conf].mean()) if conf.any() else None}


# ---------------------------------------------------------------- juntar
def plano(r):
    """Trade sugerido para um ativo (ou None)."""
    lado = "LONG" if r["p_alta"] >= 0.5 else "SHORT"
    p_dir = r["p_alta"] if lado == "LONG" else 1 - r["p_alta"]
    t = r["teste_resumo"]
    s = 1 if lado == "LONG" else -1
    motivo = []
    if p_dir < P_MIN:
        motivo.append(f"só {br(p_dir, 0)} dos caminhos a favor")
    if t.get("n", 0) < SEMANAS_MIN:
        motivo.append(f"teste curto ({t.get('n', 0)} semanas)")
    elif t["acerto"] < ACERTO_MIN or t["acerto"] < t["chute"] or t["ret_medio"] <= 0:
        motivo.append(f"teste fraco (acerto {br(t['acerto'], 0)}, chute {br(t['chute'], 0)}, {br(t['ret_medio'], 2, True)}/sem)")
    if s * r["ret_mediano"] < MOVE_MIN:
        motivo.append(f"movimento previsto pequeno ({br(r['ret_mediano'], 2, True)})")
    q = r["ret_q"]
    sl = min(max(r["queda_q15"] if lado == "LONG" else r["alta_q15"], SL_MIN), SL_MAX)
    tp1 = max(s * r["ret_mediano"], MOVE_MIN)
    tp2 = max(s * (q["p75"] if lado == "LONG" else q["p25"]), tp1 + 0.003)
    if (tp1 + tp2) / 2 < 0.6 * sl:
        motivo.append(f"alvo pequeno para o stop (alvos +{br(tp1, 1)}/+{br(tp2, 1)} × stop −{br(sl, 1)})")
    alav = int(min(max(RISCO_MARGEM // sl, ALAV_MIN), ALAV_MAX))
    return {"lado": lado, "p_dir": p_dir, "sl": round(sl, 4), "tp1": round(tp1, 4), "tp2": round(tp2, 4),
            "alav": alav, "nota": round(p_dir * t.get("acerto", 0), 4), "barrado": "; ".join(motivo) or None}


def juntar():
    rs = []
    for nome in sorted(os.listdir(SAIDA)):
        if nome.startswith("parte_"):
            rs += json.load(open(os.path.join(SAIDA, nome)))
    if not rs:
        raise SystemExit("nenhuma previsão")
    for r in rs:
        r["plano"] = plano(r)
    bons = sorted([r for r in rs if not r["plano"]["barrado"]], key=lambda r: -r["plano"]["nota"])[:MAX_TRADES]
    agora = datetime.datetime.now(datetime.timezone.utc)
    sid = agora.strftime("%Y%m%d%H")
    sinais = {"id": sid, "gerado_em": agora.isoformat(timespec="seconds"), "prazo_h": H, "modelo": "Kronos-small",
              "trades": [{"ativo": r["ativo"], "simbolo": r["simbolo"], "tipo": r["tipo"], **r["plano"],
                          "preco_previsao": r["ultimo"], "peso": round(1 / len(bons), 4)} for r in bons]}
    os.makedirs(os.path.join(AQUI), exist_ok=True)
    json.dump(sinais, open(os.path.join(AQUI, "sinais.json"), "w"), ensure_ascii=False, indent=1)
    prev = {"id": sid, "gerado_em": sinais["gerado_em"], "ativos": rs}
    json.dump(prev, open(os.path.join(AQUI, "previsao.json"), "w"))
    desenhar(prev, sinais)
    texto = mensagem(prev, sinais)
    print(texto)
    enviar_discord(texto, [os.path.join(AQUI, "grafico.png")])


def ordem_exibicao(prev, sinais):
    escolhidos = [t["simbolo"] for t in sinais["trades"]]
    return sorted(prev["ativos"], key=lambda r: (r["simbolo"] not in escolhidos,
                                                 -max(r["p_alta"], 1 - r["p_alta"]) * r["teste_resumo"].get("acerto", 0)))


def br(x, casas=0, sinal=False):
    """Porcentagem no formato brasileiro: 0,034 -> 3,4%."""
    return f"{x * 100:{'+' if sinal else ''}.{casas}f}%".replace(".", ",")


def mensagem(prev, sinais):
    hora = datetime.datetime.fromisoformat(prev["gerado_em"]).astimezone(datetime.timezone(datetime.timedelta(hours=-3)))
    L = [f"🔮 Kronos-small · {hora:%d/%m %H:%M} · próximas 24 velas de 1h (100 futuros por ativo)", ""]
    if sinais["trades"]:
        L.append("**Trades (teste de 24h, capital pequeno):**")
        for t in sinais["trades"]:
            L.append(f"• {t['ativo']} {t['lado'].lower()} · {br(t['p_dir'], 0)} dos caminhos a favor · stop −{br(t['sl'], 1)} · "
                     f"alvos +{br(t['tp1'], 1)} / +{br(t['tp2'], 1)} · {t['alav']}x · saída em 24h")
    else:
        L.append("**Nenhum trade:** nenhum ativo passou nas regras (65% dos caminhos + teste de 1 ano bom).")
    L += ["", "**Maiores probabilidades (alta / baixa · acerto no teste de 1 ano):**"]
    for r in ordem_exibicao(prev, sinais)[:12]:
        t = r["teste_resumo"]
        ac = f"{br(t['acerto'], 0)} em {t['n']} sem" if t.get("n") else "sem teste"
        L.append(f"• {r['ativo']}: ⬆️ {br(r['p_alta'], 0)} / ⬇️ {br(1 - r['p_alta'], 0)} · mediana {br(r['ret_mediano'], 2, True)} · {ac}")
    L += ["", "Gráfico com velas reais + caminhos: kronos/grafico.html (branch kronos do alerta-ema)."]
    return "\n".join(L)


def enviar_discord(texto, arquivos):
    try:
        sys.path.insert(0, os.path.dirname(AQUI))
        import discord_envio
        if discord_envio.ativo():
            log("discord: " + ("ok" if discord_envio.enviar("trade", texto, arquivos=arquivos) else "falhou"))
    except Exception as e:
        log(f"discord: {type(e).__name__} {e}")


# ---------------------------------------------------------------- gráficos
def desenhar(prev, sinais, reais_depois=None):
    """grafico.html (TradingView Lightweight Charts) e grafico.png (Discord)."""
    escolhidos = {t["simbolo"]: t for t in sinais["trades"]}
    dados = []
    for r in ordem_exibicao(prev, sinais):
        t0 = int(pd.Timestamp(r["origem"]).timestamp())
        dados.append({"ativo": r["ativo"], "simbolo": r["simbolo"], "tipo": r["tipo"], "p_alta": r["p_alta"],
                      "ret_mediano": r["ret_mediano"], "ret_q": r["ret_q"], "teste": r["teste_resumo"],
                      "plano": r["plano"], "escolhido": r["simbolo"] in escolhidos, "ultimo": r["ultimo"],
                      "t0": t0, "reais": [[int(x[0] // 1000), *x[1:5]] for x in r["reais"]],
                      "depois": (reais_depois or {}).get(r["simbolo"], []),
                      "mediana": [x[3] for x in r["velas_medianas"]],
                      "velas_medianas": r["velas_medianas"],
                      "caminhos": [[round(v, 8) for v in c] for c in r["caminhos"]]})
    html = open(os.path.join(AQUI, "modelo_grafico.html"), encoding="utf-8").read()
    html = html.replace("/*DADOS*/null", json.dumps({"gerado_em": prev["gerado_em"], "ativos": dados,
                                                     "trades": sinais["trades"]}, separators=(",", ":")))
    open(os.path.join(AQUI, "grafico.html"), "w", encoding="utf-8").write(html)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        mostrar = dados[:4]
        fig, eixos = plt.subplots(len(mostrar), 1, figsize=(10, 3.2 * len(mostrar)), squeeze=False)
        for ax, d in zip(eixos[:, 0], mostrar):
            reais = d["reais"][-72:]
            xs = [(x[0] - d["t0"]) / 3600 for x in reais]
            ax.plot(xs, [x[4] for x in reais], color="black", lw=1.2)
            fx = list(range(1, H + 1))
            for c in d["caminhos"]:
                ax.plot([0] + fx, [d["ultimo"]] + c, color="#3b82f6", alpha=0.07, lw=0.8)
            ax.plot([0] + fx, [d["ultimo"]] + d["mediana"], color="#ef4444", lw=1.6)
            if d["depois"]:
                ax.plot([(x[0] - d["t0"]) / 3600 for x in d["depois"]], [x[4] for x in d["depois"]], color="#16a34a", lw=1.6)
            ax.axvline(0, color="gray", ls="--", lw=0.8)
            ax.set_title(f"{d['ativo']} · alta {d['p_alta']:.0%} / baixa {1 - d['p_alta']:.0%}"
                         + (f" · TRADE {d['plano']['lado']}" if d["escolhido"] else ""), fontsize=10)
            ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(os.path.join(AQUI, "grafico.png"), dpi=110)
    except Exception as e:
        log(f"png: {e}")


def conferir():
    """Velas reais depois da previsão: atualiza o gráfico; depois de 24h, placar no Discord (1 vez)."""
    prev = json.load(open(os.path.join(AQUI, "previsao.json")))
    sinais = json.load(open(os.path.join(AQUI, "sinais.json")))
    depois, placar = {}, []
    for r in prev["ativos"]:
        try:
            df = velas(r["simbolo"], 48)
        except Exception:
            continue
        t0 = pd.Timestamp(r["origem"])
        d = df[df["data"] > t0].head(H)
        depois[r["simbolo"]] = [[int(x.t // 1000), x.open, x.high, x.low, x.close] for x in d.itertuples()]
        if len(d) >= H:
            real = float(d["close"].iloc[-1]) / r["ultimo"] - 1
            dentro = r["ret_q"]["p10"] <= real <= r["ret_q"]["p90"]
            placar.append((r, real, (real > 0) == (r["p_alta"] >= 0.5), dentro))
    desenhar(prev, sinais, depois)
    estado_p = os.path.join(AQUI, "conferido.json")
    feito = json.load(open(estado_p)) if os.path.exists(estado_p) else {}
    if placar and len(placar) >= 0.8 * len(prev["ativos"]) and feito.get("id") != prev["id"]:
        acertos = sum(p[2] for p in placar)
        L = [f"📏 Kronos · placar das 24h ({prev['id']}): direção certa em {acertos} de {len(placar)} ativos "
             f"({br(acertos / len(placar), 0)}); real dentro da faixa p10–p90 em {sum(p[3] for p in placar)}.", ""]
        escolhidos = {t["simbolo"] for t in sinais["trades"]}
        for r, real, ok, _ in sorted(placar, key=lambda p: p[0]["simbolo"] not in escolhidos)[:12]:
            L.append(f"{'✅' if ok else '❌'} {r['ativo']}{' (trade)' if r['simbolo'] in escolhidos else ''}: "
                     f"previsto ⬆️ {br(r['p_alta'], 0)} · real {br(real, 2, True)}")
        enviar_discord("\n".join(L), [os.path.join(AQUI, "grafico.png")])
        json.dump({"id": prev["id"], "acertos": acertos, "n": len(placar)}, open(estado_p, "w"))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "lista":
        lista()
    elif cmd == "rodar":
        rodar(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "juntar":
        juntar()
    elif cmd == "conferir":
        conferir()
