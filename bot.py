"""
BOT DA CARTEIRA DO PEDRO - EUA (Binance Wallet) + B3 + futuros.

EUA: sistema Donchian 55/10 (ver donchian.py e README_BOT.md)
  - 7 vagas (no maximo 1 cripto: ASTER), valor por entrada = patrimonio / 7
  - entrada: preco rompe a maxima de 55 pregoes com SPY > MMA 200
  - saida: fechamento abaixo da minima dos 10 pregoes anteriores -> vende na abertura
  - ASTER: candle diario (fecha 21h de Brasilia), filtro BTC > MMA 200

Quando cada coisa acontece (o codigo decide pelo relogio de NY, nao pelo cron):
  pregao aberto      -> checa rompimentos (de hora em hora)
  depois do fecho    -> checa saidas + heatmap do dia (uma vez por dia)
  00h UTC (21h BR)   -> ASTER (todo dia, inclusive fim de semana)

O bot ASSUME que voce executou cada ordem. Preco real: /comprei e /vendi.
Comandos EUA: /carteira /comprei /vendi /recusar /nao /unidades /reserva /pausa
Comandos B3:  /nao_br /reserva_br (os demais servem para os dois mercados)
Futuros:      /perps /fechei

MODO (variavel de ambiente):
  auto (padrao) | tudo | pregao | fechamento | cripto | diagnostico | demo | teste
  teste: nao manda nada nem salva - imprime as mensagens no terminal.
         AGORA=2026-09-28T14:15Z simula o relogio; DADOS_LOCAIS=dados usa CSVs.
"""

import copy
import io
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import requests

import avisos
import donchian as dc
import fontes
import mercado as mk

MODO = os.environ.get("MODO", "auto")
TESTE = MODO == "teste"
TOKEN = "" if TESTE else os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = "" if TESTE else os.environ.get("TELEGRAM_CHAT_ID", "")
# Topico do grupo (vazio = manda como antes, sem topico)
THREAD = "" if TESTE else os.environ.get("TELEGRAM_THREAD_ID", "").strip()
ARQ_CARTEIRA, ARQ_ESTADO, ARQ_UNIVERSO = "carteira.json", "estado_bot.json", "universo_ondo.json"
NY = ZoneInfo("America/New_York")
DADOS_LOCAIS = os.environ.get("DADOS_LOCAIS", "")

# ================================================================ REGRAS EUA
ORDEM_MINIMA = 50.0          # US$ (definido por voce)
PASSO_ACAO = 0.0001          # casas do token de acao: nao confirmado -> 4 casas, para baixo
FOLGA_COMPRA = 0.01          # "compre ate": 1% acima do preco de referencia
PERTO_SAIDA = 0.03           # destaque: a menos de 3% da saida
DIAS_SILENCIO_RECUSA = 5
SAIDA_PARCIAL = False        # variante C (50/50) reprovada no backtest -> saida 100%
TOL_PRECO = 0.15

num = lambda x, c=2: f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")
pct = lambda x: f"{x*100:+.1f}%".replace(".", ",")
usd = lambda x, c=2: ("US$ -" if x < 0 else "US$ ") + num(abs(x), c)
usd_s = lambda x: ("US$ +" if x >= 0 else "US$ -") + num(abs(x))


def un_txt(x, passo):
    return num(x, dc.casas(passo))


def un_exato(x):
    """Quantidade que voce tem, sem arredondar para cima (ate 8 casas, sem zeros sobrando)."""
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    inteiro, _, dec = s.partition(".")
    return num(float(inteiro), 0) + ("," + dec if dec else "")


# ============================================================== TELEGRAM
def tg(metodo, **dados):
    if not (TOKEN and CHAT_ID):
        return {}
    if THREAD and metodo.startswith("send"):
        dados.setdefault("message_thread_id", int(THREAD))
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/{metodo}",
                             json=dados, timeout=30).json()
    except Exception as e:
        print("telegram falhou:", e)
        return {}


def enviar(texto, botao=None, silencioso=False):
    """Envia e devolve o id da mensagem. botao = (rotulo, dado_do_callback).
    silencioso=True chega sem som (so para o que nao pede acao)."""
    print("\n>>> TELEGRAM" + (" [sem som]" if silencioso else " [com som]")
          + (f" [botao {botao[0]}]" if botao else "") + "\n" + texto)
    extra = {"disable_notification": True} if silencioso else {}
    if botao:
        extra["reply_markup"] = {"inline_keyboard": [[{"text": botao[0], "callback_data": botao[1]}]]}
    r = tg("sendMessage", chat_id=CHAT_ID, text=texto[:4000], **extra)
    return r.get("result", {}).get("message_id")


def apagar(msg_id):
    if msg_id:
        print(f">>> TELEGRAM apagar mensagem {msg_id}")
        tg("deleteMessage", chat_id=CHAT_ID, message_id=msg_id)


def enviar_foto(caminho, legenda, silencioso=False):
    print(f"\n>>> TELEGRAM FOTO {caminho}" + (" [sem som]" if silencioso else "") + f"\n{legenda}")
    if TOKEN and CHAT_ID:
        with open(caminho, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                          data={"chat_id": CHAT_ID, "caption": legenda[:1000],
                                "disable_notification": str(silencioso).lower(),
                                **({"message_thread_id": THREAD} if THREAD else {})},
                          files={"photo": f}, timeout=60)



# ================================================================ NUCLEO (usado tambem por b3.py, perps.py e previa.py)
SETORES_PT = {
    "Information Technology": "Tecnologia", "Health Care": "Saude",
    "Financials": "Financeiro", "Consumer Discretionary": "Consumo",
    "Communication Services": "Comunicacao", "Industrials": "Industria",
    "Consumer Staples": "Consumo basico", "Energy": "Energia",
    "Utilities": "Utilidades", "Real Estate": "Imobiliario", "Materials": "Materiais",
}
EXTRAS = {
    "Tecnologia": ["CRWV", "IREN", "NBIS", "CRDO", "AAOI", "AXTI", "SNDK", "LITE", "COHR",
                   "ARM", "TSM", "ASML", "PLTR", "APP", "NET", "DDOG", "SNOW", "IONQ",
                   "RGTI", "QBTS", "MVLL"],
    "Energia": ["BE", "ENPH", "OKLO", "NNE", "SMR", "USAR", "MP"],
    "Industria": ["ASTS", "RKLB"],
    "Financeiro": ["HOOD", "COIN", "MSTR", "SOFI", "CRCL"],
    "Consumo": ["BABA", "SHOP", "GME"],
    "Comunicacao": ["RDDT", "SPOT"],
    "Saude": ["HIMS", "NVO"],
}

num = lambda x, c=2: f"{x:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")
pct = lambda x: f"{x*100:+.1f}%".replace(".", ",")




def do_meu_topico(msg):
    """Privacidade: so o chat configurado e, no grupo, so o topico deste bot (TELEGRAM_THREAD_ID).
    Sem topico configurado, so a conversa privada. O resto e descartado sem ler nem registrar."""
    chat = msg.get("chat", {})
    if str(chat.get("id")) != str(CHAT_ID):
        return False
    if THREAD:
        return str(msg.get("message_thread_id")) == THREAD
    return chat.get("type") == "private"


def ler_comandos(estado):
    """Devolve eventos: ('cmd', partes, msg_id) ou ('botao', dado, msg_id)."""
    if not TOKEN:
        return []
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                         params={"offset": estado.get("offset", 0), "timeout": 0},
                         timeout=30).json()
    except Exception:
        return []
    eventos, ignoradas = [], 0
    for u in r.get("result", []):
        estado["offset"] = u["update_id"] + 1
        if "callback_query" in u:
            cq = u["callback_query"]
            if do_meu_topico(cq.get("message", {})):
                tg("answerCallbackQuery", callback_query_id=cq["id"], text="Recusado")
                eventos.append(("botao", cq.get("data", ""), cq.get("message", {}).get("message_id")))
            else:
                ignoradas += 1
            continue
        msg = u.get("message", {})
        if not do_meu_topico(msg):
            ignoradas += 1  # outro topico/chat: so a contagem, nada do conteudo
            continue
        if msg.get("text", "").startswith("/"):
            eventos.append(("cmd", msg["text"].strip().split(), msg.get("message_id")))
    if ignoradas:
        print(f"telegram: {ignoradas} mensagem(ns) de fora do topico ignorada(s)")
    return eventos


# ================================================================= DADOS
def baixar(tickers, periodo="1y"):
    import yfinance as yf
    tickers = sorted(set(tickers))
    saida = {}
    for i in range(0, len(tickers), 150):
        lote = tickers[i:i + 150]
        try:
            bruto = yf.download(lote, period=periodo, interval="1d", auto_adjust=True,
                                group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print("falha no download:", e)
            continue
        for t in lote:
            try:
                df = bruto[t] if isinstance(bruto.columns, pd.MultiIndex) else bruto
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if getattr(df.index, "tz", None) is not None:
                    df.index = df.index.tz_localize(None)
                if len(df) >= 60:
                    saida[t] = df
            except Exception:
                pass
    return fontes.completar_acoes(saida, tickers, periodo)  # reserva: Yahoo direto


def universo():
    """S&P 500 + Nasdaq-100 (Wikipedia) + seus temas. Devolve {ticker: setor}."""
    setores = {}
    cab = {"User-Agent": "Mozilla/5.0 (bot da carteira)"}
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "https://en.wikipedia.org/wiki/Nasdaq-100"):
        try:
            html = requests.get(url, headers=cab, timeout=30).text
            for tab in pd.read_html(io.StringIO(html)):
                col = next((c for c in tab.columns if str(c) in ("Symbol", "Ticker")), None)
                if col is None or len(tab) < 90:
                    continue
                setor_col = next((c for c in tab.columns if "Sector" in str(c)), None)
                for _, linha in tab.iterrows():
                    t = str(linha[col]).replace(".", "-").strip()
                    s = SETORES_PT.get(str(linha[setor_col]), "Outros") if setor_col else "Outros"
                    setores.setdefault(t, s)
                break
        except Exception as e:
            print("wikipedia indisponivel:", e)
    for setor, lista in EXTRAS.items():
        for t in lista:
            setores.setdefault(t, setor)
    return setores


def indicadores(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    ant = c.shift(1)
    tr = pd.concat([h - l, (h - ant).abs(), (l - ant).abs()], axis=1).max(axis=1)
    return {
        "preco": float(c.iloc[-1]), "ontem": float(c.iloc[-2]),
        "abertura": float(df["Open"].iloc[-1]),
        "max": float(h.iloc[-1]), "min": float(l.iloc[-1]), "min_ontem": float(l.iloc[-2]),
        "ema20": float(c.ewm(span=20, adjust=False).mean().iloc[-1]),
        "atr": float(tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1]),
        "sma50": float(c.rolling(50).mean().iloc[-1]),
        "sma50_antes": float(c.rolling(50).mean().iloc[-21]) if len(c) > 70 else np.nan,
        "sma200": float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else np.nan,
        "ret63": float(c.iloc[-1] / c.iloc[-64] - 1) if len(c) > 64 else np.nan,
        "max52": float(h.iloc[-252:].max()), "min52": float(l.iloc[-252:].min()),
        "vol_rel": float(v.iloc[-1] / v.iloc[-21:-1].mean()) if v.iloc[-21:-1].mean() > 0 else 1.0,
        "vol_rel3": float(v.iloc[-3:].max() / v.iloc[-23:-3].mean()) if v.iloc[-23:-3].mean() > 0 else 1.0,
        "vol_tend": float(v.iloc[-20:].mean() / v.iloc[-60:].mean()) if v.iloc[-60:].mean() > 0 else 1.0,
        "liq": float((c * v).iloc[-20:].mean()),
        "atr_pct": float(tr.ewm(alpha=1 / 20, adjust=False).mean().iloc[-1] / c.iloc[-1]),
        "data": df.index[-1],
    }


def teve_resultado(ticker, dias=3):
    """True/False se houve divulgacao de resultado nos ultimos dias; None se nao souber."""
    try:
        import yfinance as yf
        datas = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if datas is None or datas.empty:
            return None
        idx = datas.index.tz_localize(None) if datas.index.tz is not None else datas.index
        hoje = pd.Timestamp.now().normalize()
        return bool(((idx <= hoje + pd.Timedelta(days=1)) &
                     (idx >= hoje - pd.Timedelta(days=dias + 2))).any())
    except Exception:
        return None


# ================================================================= NOTAS
def nota_swing(ind, spy_ok, resultado):
    """Prioridade de compra na queda (0-10). Ordena candidatos; nao preve lucro."""
    p = {}
    esticada = (ind["ema20"] - ind["preco"]) / ind["atr"] if ind["atr"] else 0
    p["esticada"] = 2.0 if esticada >= 3 else 1.0 if esticada >= ESTICADA_MIN else 0.0
    p["tendencia longa"] = 2.0 if ind["preco"] > ind["sma200"] else 0.0
    p["sem noticia"] = 1.0 if resultado is None else (0.0 if resultado else 2.0)
    fechou_em_cima = (ind["preco"] - ind["min"]) / (ind["max"] - ind["min"]) >= 0.66 \
        if ind["max"] > ind["min"] else False
    p["confirmacao"] = 1.5 if (ind["min"] > ind["min_ontem"] or fechou_em_cima) else 0.0
    p["capitulacao"] = 1.0 if ind["vol_rel3"] >= 2 else 0.0
    p["tese viva"] = 1.0 if spy_ok else 0.0
    p["liquidez"] = 0.5 if ind["liq"] >= 50e6 else 0.25 if ind["liq"] >= 10e6 else 0.0
    return round(sum(p.values()), 1), p, esticada


def nota_tendencia(ind, percentil_rs):
    """Qualidade do grafico para troca (0-10)."""
    n = 0.0
    n += 3 if percentil_rs >= 0.90 else 2 if percentil_rs >= 0.75 else 1 if percentil_rs >= 0.60 else 0
    if ind["preco"] > ind["sma50"] > ind["sma200"]:
        n += 2
    elif ind["preco"] > ind["sma200"]:
        n += 1
    n += 2 if ind["preco"] >= 0.9 * ind["max52"] else 1 if ind["preco"] >= 0.8 * ind["max52"] else 0
    n += 1 if ind["sma50"] > ind["sma50_antes"] else 0
    n += 1 if ind["vol_tend"] >= 1.2 else 0
    n += 1 if ind["liq"] >= 50e6 else 0
    return float(n)



def pregoes_desde(data_str, pregoes):
    """Quantos pregoes aconteceram depois da data (calendario real do SPY)."""
    if pregoes is None:
        return 0
    return int((pregoes > pd.Timestamp(data_str)).sum())



# ======================================================= COMPATIBILIDADE
# Usados por b3.py / perps.py / previa.py (nota_swing le ESTICADA_MIN).
ESTICADA_MIN = 2.0
HORA_VARREDURA = 19
HORA_FECHAMENTO = 21


# ================================================================ UNIVERSO EUA
def ler_universo():
    with open(ARQ_UNIVERSO, encoding="utf-8") as f:
        u = json.load(f)
    return {a["ticker"]: a for a in u["ativos"]}


def token_de(t, univ, cart=None):
    if cart and t in cart["ativos"]:
        return cart["ativos"][t]["token"]
    return univ.get(t, {}).get("token", f"{t}on")


def token_alt(t, univ):
    return univ.get(t, {}).get("token_alternativo")


def passo_de(t, univ):
    return univ.get(t, {}).get("passo_quantidade") or PASSO_ACAO


# ================================================================ RELOGIO
def agora_utc():
    a = os.environ.get("AGORA")
    if a:
        return pd.Timestamp(a).tz_convert("UTC").to_pydatetime() if pd.Timestamp(a).tzinfo \
            else pd.Timestamp(a).tz_localize("UTC").to_pydatetime()
    return datetime.now(timezone.utc)


def hoje_ny(agora=None):
    return str((agora or agora_utc()).astimezone(NY).date())


# ================================================================ DADOS EUA
_cache_local = {}


def baixar_eua(tickers, agora):
    """{ticker: DataFrame diario}. Na simulacao (DADOS_LOCAIS) corta no dia simulado;
    a barra do dia simulado faz o papel da barra 'em andamento'."""
    if not DADOS_LOCAIS:
        return baixar(tickers, periodo="1y")
    ate = agora.astimezone(NY).date()
    if not _cache_local:
        for k in ("open", "high", "low", "close", "volume"):
            _cache_local[k] = pd.read_csv(f"{DADOS_LOCAIS}/yahoo_{k}.csv.gz", index_col=0, parse_dates=True)
    out = {}
    for t in set(tickers):
        if t not in _cache_local["close"]:
            continue
        df = pd.DataFrame({k.capitalize(): _cache_local[k][t] for k in _cache_local}).dropna()
        df = df[df.index <= pd.Timestamp(ate)]
        if len(df) >= 60:
            out[t] = df.iloc[-260:]
    return out


def klines(par, agora, n=400):
    """Candles diarios da Binance spot. Devolve (completos, preco_atual)."""
    if DADOS_LOCAIS:
        df = pd.read_csv(f"{DADOS_LOCAIS}/binance_{par}.csv", index_col=0, parse_dates=True)
        df = df[df.index.normalize() <= pd.Timestamp(agora.astimezone(timezone.utc).date())]
    else:
        df = fontes.cripto(par, "1d", n)  # ccxt; reserva: REST da Binance (fontes.py)
    hoje_utc = pd.Timestamp(agora.astimezone(timezone.utc).date())
    completos = df[df.index.normalize() < hoje_utc]
    atual = float(df["Close"].iloc[-1])
    return completos, atual


def proximo_resultado(ticker, agora, pregoes=2):
    """Data do proximo balanco se cair nos proximos `pregoes` pregoes; senao None."""
    if DADOS_LOCAIS:
        return None
    try:
        import yfinance as yf
        datas = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if datas is None or datas.empty:
            return None
        idx = datas.index.tz_convert(NY) if datas.index.tz is not None else datas.index.tz_localize(NY)
        hoje = agora.astimezone(NY).date()
        fim = mk.proximo_pregao(hoje, pregoes)
        futuras = sorted(d.date() for d in idx if hoje < d.date() <= fim)
        return futuras[0] if futuras else None
    except Exception:
        return None


# ================================================================ ESTADO
ESTADO_PADRAO = {"offset": 0, "ultima": None, "pendentes": {}, "recusados": {}, "alertados": {},
                 "historico": [], "pausa_ate": None, "fechamento_em": None, "cripto_em": None,
                 "patrimonio_fech": None}


def carregar():
    with open(ARQ_CARTEIRA, encoding="utf-8") as f:
        cart = json.load(f)
    est = copy.deepcopy(ESTADO_PADRAO)
    if os.path.exists(ARQ_ESTADO):
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            est.update(json.load(f))
    return cart, est


def salvar(cart, est):
    if TESTE and not os.environ.get("TESTE_GRAVAR"):
        print("\n(modo teste: carteira e estado NAO foram gravados)")
        return
    with open(ARQ_CARTEIRA, "w", encoding="utf-8") as f:
        json.dump(cart, f, indent=2, ensure_ascii=False)
    with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
        json.dump(est, f, indent=2, ensure_ascii=False, default=str)


def migrar_carteira(antiga, univ, estado_antigo=None):
    """Formato antigo (base US$ 1.000 + preco_print) -> formato novo por posicao.
    As posicoes atuais viram 'legado' e ocupam vaga. Nada e vendido."""
    if antiga.get("versao") == 2:
        return antiga
    ativos = {}
    for t, a in antiga["ativos"].items():
        ativos[t] = {"token": a["token"], "tipo": "cripto" if univ.get(t, {}).get("tipo") == "cripto" else "acao",
                     "unidades": float(a["unidades"]), "preco_entrada": float(a["preco_print"]),
                     "data_entrada": a.get("desde", antiga.get("inicio")), "origem": "legado"}
    return {"versao": 2, "inicio": antiga.get("inicio"),
            "capital_inicial": float(antiga.get("capital_inicial", 0)),
            "reserva_usdt": float(antiga.get("reserva_usdt", 0)), "aportes": [],
            "ativos": ativos}


def migrar_estado(est_antigo):
    novo = copy.deepcopy(ESTADO_PADRAO)
    novo["offset"] = est_antigo.get("offset", 0)
    return novo


# ================================================================ CONTAS
def preco_atual(t, dados, cripto_px):
    if t in cripto_px:
        return cripto_px[t]
    d = dados.get(t)
    return float(d["Close"].iloc[-1]) if d is not None and len(d) else None


def patrimonio(cart, dados, cripto_px):
    """(patrimonio total, valor das posicoes). Sem preco novo: ultimo conhecido."""
    pos = 0.0
    for t, a in cart["ativos"].items():
        p = preco_atual(t, dados, cripto_px)
        if p is None:
            p = a.get("ultimo_preco") or a["preco_entrada"]
        pos += a["unidades"] * p
    return pos + cart["reserva_usdt"], pos


def lucro_total(cart, patr):
    base = cart.get("capital_inicial", 0) + sum(x["valor"] for x in cart.get("aportes", []))
    return patr - base, (patr / base - 1) if base else 0.0


def recusado(est, t, hoje):
    ate = est.get("recusados", {}).get(t)
    return bool(ate) and ate >= hoje


def em_pausa(est, hoje):
    return bool(est.get("pausa_ate")) and est["pausa_ate"] >= hoje


def fechar_trade(cart, est, t, preco, data, confirmado=False, frac=1.0):
    a = cart["ativos"][t]
    un = a["unidades"] * frac
    valor = un * preco
    cart["reserva_usdt"] = round(cart["reserva_usdt"] + valor, 2)
    lucro = un * (preco - a["preco_entrada"])
    est["historico"].append({"ticker": t, "token": a["token"], "unidades": un,
                             "preco_entrada": a["preco_entrada"], "data_entrada": a["data_entrada"],
                             "preco_saida": preco, "data_saida": data, "lucro_usd": round(lucro, 2),
                             "lucro_pct": preco / a["preco_entrada"] - 1, "origem": a["origem"],
                             "confirmado": confirmado})
    if frac >= 1.0 - 1e-9:
        del cart["ativos"][t]
    else:
        a["unidades"] -= un
    return lucro


# ================================================================ COMANDOS
def numero(s):
    s = s.strip().replace("US$", "").replace("$", "")
    if "," in s and "." in s:
        s = s.replace(".", "")
    return float(s.replace(",", "."))


def recusar(cart, est, chave, hoje):
    """Desfaz UMA entrada sugerida, apaga a mensagem e silencia o ativo por 5 dias."""
    pend = est.get("pendentes", {}).pop(chave, None)
    if not pend:
        return False
    t = pend["ticker"]
    a = cart["ativos"].get(t)
    if a and a.get("origem") == "donchian" and a.get("data_entrada") == pend["data"]:
        cart["reserva_usdt"] = round(cart["reserva_usdt"] + a["unidades"] * a["preco_entrada"], 2)
        del cart["ativos"][t]
    ate = str((pd.Timestamp(hoje) + pd.Timedelta(days=DIAS_SILENCIO_RECUSA)).date())
    est.setdefault("recusados", {})[t] = ate
    apagar(pend.get("msg_id"))
    return True


def processar_comandos(cart, est, eventos, univ, dados, cripto_px, hoje):
    """Trata o que e dos EUA e devolve o resto para a B3 e os futuros."""
    respostas, restantes = [], []
    for ev in eventos:
        if ev[0] == "botao":
            if ev[1].startswith("rec|"):
                recusar(cart, est, ev[1][4:], hoje)
            else:
                restantes.append(ev)
            continue
        cmd, msg_id = ev[1], ev[2]
        nome = cmd[0].lower().split("@")[0]
        try:
            r = comando(nome, cmd[1:], cart, est, univ, dados, cripto_px, hoje, msg_id)
        except (ValueError, IndexError):
            r = f"⚠️ Nao entendi: {' '.join(cmd)}"
        if r is None:
            restantes.append(ev)
        elif r == "":
            pass
        else:
            respostas.append(r)
            if nome in ("/carteira", "/pausa"):
                restantes.append(ev)                   # a B3 responde tambem
    return respostas, restantes


def comando(nome, args, cart, est, univ, dados, cripto_px, hoje, msg_id):
    """Devolve texto de resposta, '' (tratado sem resposta) ou None (nao e dos EUA)."""
    if nome == "/recusar" and len(args) == 1:
        alvo = args[0].upper()
        chaves = [k for k, v in est.get("pendentes", {}).items() if v.get("ticker") == alvo]
        if not chaves:
            return None
        for k in chaves:
            recusar(cart, est, k, hoje)
        apagar(msg_id)
        return ""
    if nome == "/nao":
        if not est.get("ultima"):
            return "↩️ EUA: nada para desfazer."
        antes = est["ultima"]
        cart.clear()
        cart.update(antes["carteira"])
        est["historico"] = est["historico"][:antes["n_historico"]]
        for k in antes.get("chaves", []):
            pend = est["pendentes"].pop(k, None)
            if pend and pend.get("ticker"):
                est["alertados"][pend["ticker"]] = hoje
        est["ultima"] = None
        return "↩️ EUA: ultima ordem desfeita. Ela nao sera repetida hoje."
    if nome == "/reserva" and len(args) == 1:
        novo = numero(args[0])
        dif = round(novo - cart["reserva_usdt"], 2)
        cart["reserva_usdt"] = novo
        if dif:
            cart.setdefault("aportes", []).append({"data": hoje, "valor": dif})
        return (f"✅ Caixa EUA: {usd(novo)} (diferenca de {usd_s(dif)} conta como aporte/retirada, "
                "nao como lucro)")
    if nome == "/unidades" and len(args) == 2 and args[0].upper() in cart["ativos"]:
        t = args[0].upper()
        cart["ativos"][t]["unidades"] = numero(args[1])
        return f"✅ {t}: {args[1]} unidades"
    if nome == "/pausa" and len(args) == 1 and args[0].isdigit():
        n = int(args[0])
        if n == 0:
            est["pausa_ate"] = None
            return "▶️ EUA: pausa encerrada. Entradas liberadas."
        est["pausa_ate"] = str((pd.Timestamp(hoje) + pd.Timedelta(days=n - 1)).date())
        return (f"⏸️ EUA: sem entradas novas ate {pd.Timestamp(est['pausa_ate']):%d/%m/%Y}. "
                "Saidas continuam. /pausa 0 encerra.")
    if nome == "/comprei" and len(args) == 3:
        t, un, px = args[0].upper(), numero(args[1]), numero(args[2])
        a = cart["ativos"].get(t)
        if a:
            cart["reserva_usdt"] = round(cart["reserva_usdt"] + a["unidades"] * a["preco_entrada"] - un * px, 2)
            a.update(unidades=un, preco_entrada=px)
        else:
            cart["ativos"][t] = {"token": token_de(t, univ), "tipo": univ.get(t, {}).get("tipo", "acao"),
                                 "unidades": un, "preco_entrada": px, "data_entrada": hoje, "origem": "donchian"}
            cart["reserva_usdt"] = round(cart["reserva_usdt"] - un * px, 2)
        for k in [k for k, v in est["pendentes"].items() if v.get("ticker") == t]:
            est["pendentes"].pop(k)                  # ja executada: o ❌ nao desfaz mais
        avisos = ""
        if cart["reserva_usdt"] < 0:
            avisos += "\n   ⚠️ Caixa negativo: confira com /reserva VALOR."
        if len(cart["ativos"]) > dc.VAGAS:
            avisos += f"\n   ⚠️ {len(cart['ativos'])} posicoes para {dc.VAGAS} vagas: nenhuma entrada nova ate liberar."
        return (f"✅ Compra registrada: {t} {un_exato(un)} a {usd(px)} = {usd(un * px)}\n"
                f"   Caixa agora: {usd(cart['reserva_usdt'])}" + avisos)
    if nome == "/vendi" and len(args) == 3:
        t, un, px = args[0].upper(), numero(args[1]), numero(args[2])
        a = cart["ativos"].get(t)
        if a:
            frac = min(1.0, un / a["unidades"]) if a["unidades"] else 1.0
            lucro = fechar_trade(cart, est, t, px, hoje, confirmado=True, frac=frac)
            return (f"✅ Venda registrada: {t} {un_exato(un)} a {usd(px)} | lucro {usd_s(lucro)}\n"
                    f"   Caixa agora: {usd(cart['reserva_usdt'])}")
        abertos = [h for h in est["historico"] if h["ticker"] == t and not h.get("confirmado")]
        if abertos:
            h = abertos[-1]
            ajuste = un * px - h["unidades"] * h["preco_saida"]
            cart["reserva_usdt"] = round(cart["reserva_usdt"] + ajuste, 2)
            h.update(unidades=un, preco_saida=px, confirmado=True,
                     lucro_usd=round(un * (px - h["preco_entrada"]), 2),
                     lucro_pct=px / h["preco_entrada"] - 1)
            return (f"✅ Venda corrigida: {t} {un_exato(un)} a {usd(px)} | lucro {usd_s(h['lucro_usd'])}\n"
                    f"   Caixa agora: {usd(cart['reserva_usdt'])}")
        return f"⚠️ {t} nao esta na carteira EUA."
    if nome == "/carteira":
        return texto_carteira(cart, dados, cripto_px, univ)
    return None


def texto_carteira(cart, dados, cripto_px, univ):
    patr, _ = patrimonio(cart, dados, cripto_px)
    L = ["📋 CARTEIRA EUA"]
    for t, a in sorted(cart["ativos"].items()):
        p = preco_atual(t, dados, cripto_px) or a["preco_entrada"]
        extra = " | saida na abertura" if a.get("saida") else ""
        L.append(f"  {t} ({a['token']}): {usd(a['unidades'] * p)} | {pct(p / a['preco_entrada'] - 1)}"
                 f" | {a['origem']}{extra}")
    livres, _ = dc.vagas_livres(cart["ativos"].values())
    lt, lp = lucro_total(cart, patr)
    L.append(f"  caixa USDT: {usd(cart['reserva_usdt'])} | vagas livres: {livres} de {dc.VAGAS}")
    L.append(f"  TOTAL: {usd(patr)} | desde o inicio {usd_s(lt)} ({pct(lp)})")
    return "\n".join(L)


# ================================================================ ORDENS
def msg_entrada(t, token, alt, preco, valor, un, passo, saida, cripto=False):
    L = [f"🟢 COMPRAR {t}",
         f"Token: {token}" + (f" (ou {alt})" if alt else ""),
         f"Preco de referencia: {usd(preco, 4 if preco < 5 else 2)}",
         f"Compre agora ate {usd(preco * (1 + FOLGA_COMPRA), 4 if preco < 5 else 2)}",
         f"Valor: {usd(valor)} = {un_txt(un, passo)} {token}",
         f"Saida atual: {usd(saida, 4 if saida < 5 else 2)}"]
    return "\n".join(L)


def msg_saida(t, token, un, passo, preco_ref, a, agora_mesmo=False, frac=1.0):
    lucro = un * (preco_ref - a["preco_entrada"])
    L = [f"🔴 VENDER {'AGORA' if agora_mesmo else 'NA ABERTURA'}: {t}",
         f"Token: {token}",
         f"Vender {int(round(frac * 100))}% = {un_exato(un) if frac >= 1 else un_txt(dc.arredondar_baixo(un, passo), passo)} {token}",
         f"Lucro estimado do trade: {pct(preco_ref / a['preco_entrada'] - 1)} ({usd_s(lucro)})"]
    return "\n".join(L)


def completos(df, agora):
    """Barras completas das acoes: tira a de hoje se o pregao ainda nao fechou."""
    fase = mk.fase_eua(agora)
    hoje = agora.astimezone(NY).date()
    return dc.barras_completas(df, hoje, em_andamento=(fase in ("pregao", "antes")))


def finalizar_saidas(cart, est, dados, agora, hoje):
    """Saidas marcadas no fechamento anterior: registra a venda pela abertura de hoje."""
    for t in [t for t, a in cart["ativos"].items() if a.get("saida") and a["tipo"] == "acao"]:
        a = cart["ativos"][t]
        df = dados.get(t)
        if df is None or not len(df):
            continue
        barra = pd.Timestamp(df.index[-1]).normalize()
        if barra <= pd.Timestamp(a["saida"]["data"]):
            continue                                   # ainda nao abriu o pregao seguinte
        fechar_trade(cart, est, t, float(df["Open"].iloc[-1]), hoje, frac=a["saida"].get("frac", 1.0))


def checar_entradas(cart, est, univ, dados, agora, hoje, ordens, sinais_dia):
    spy = dados.get("SPY")
    if spy is None:
        return
    spy_ok = dc.filtro_mercado(completos(spy, agora)["Close"].values)
    sinais = []
    for t, info in univ.items():
        if info["tipo"] != "acao" or t in cart["ativos"] or t not in dados:
            continue
        df = dados[t]
        comp = completos(df, agora)
        if len(comp) == len(df):
            continue                                   # sem barra de hoje (sem pregao ou sem dado)
        nv = dc.nivel_entrada(comp["High"].values)
        preco = float(df["Close"].iloc[-1])
        if not dc.rompeu(preco, nv):
            continue
        sinais_dia[t] = {"preco": preco, "nivel": nv}
        if not spy_ok or recusado(est, t, hoje) or est["alertados"].get(t) == hoje or em_pausa(est, hoje):
            continue
        sinais.append({"ticker": t, "tipo": "acao", "momentum": dc.momentum(comp["Close"].values),
                       "preco": preco, "saida": dc.nivel_saida(comp["Low"].values)})
    escolhidos, sem_vaga = dc.escolher(sinais, cart["ativos"].values())
    for s in sem_vaga:
        sinais_dia[s["ticker"]]["obs"] = "sem vaga"
    patr, _ = patrimonio(cart, dados, {})
    for s in escolhidos:
        t = s["ticker"]
        passo = passo_de(t, univ)
        tam = dc.tamanho_entrada(patr, cart["reserva_usdt"], s["preco"], passo, ORDEM_MINIMA)
        if tam is None:
            sinais_dia[t]["obs"] = "sem caixa"
            continue
        valor, un = tam
        token = token_de(t, univ)
        cart["reserva_usdt"] = round(cart["reserva_usdt"] - valor, 2)
        cart["ativos"][t] = {"token": token, "tipo": "acao", "unidades": un, "preco_entrada": s["preco"],
                             "data_entrada": hoje, "origem": "donchian"}
        est["alertados"][t] = hoje
        sinais_dia[t]["obs"] = "ordem enviada"
        ordens.append((f"entrada:{t}", msg_entrada(t, token, token_alt(t, univ), s["preco"], valor, un,
                                                   passo, s["saida"]),
                       {"tipo": "entrada", "ticker": t}))


def checar_saidas(cart, est, univ, dados, agora, hoje, ordens):
    """Depois do fechamento: fechamento abaixo da minima dos 10 pregoes anteriores."""
    for t, a in cart["ativos"].items():
        if a["tipo"] != "acao" or a.get("saida") or t not in dados:
            continue
        df = dados[t]
        if pd.Timestamp(df.index[-1]).normalize() != pd.Timestamp(hoje):
            continue                                   # sem barra de hoje
        c, l = df["Close"].values, df["Low"].values
        sair = dc.sinal_saida(c, l, dc.N_SAIDA)
        frac = 1.0
        if not sair and SAIDA_PARCIAL and not a.get("parcial"):
            sair, frac = dc.sinal_saida(c, l, dc.N_SAIDA_RAPIDA), 0.5
        if not sair:
            continue
        preco = float(c[-1])
        un = a["unidades"] * frac
        a["saida"] = {"data": hoje, "preco_ref": preco, "frac": frac}
        ordens.append((f"saida:{t}", msg_saida(t, a["token"], un, passo_de(t, univ), preco, a, frac=frac), None))


def checar_cripto(cart, est, univ, agora, ordens, cripto_px):
    """ASTER: candle diario fechado (21h de Brasilia). Saida e entrada executam na hora."""
    dia = str(mk.dia_cripto_fechado(agora))
    hoje = hoje_ny(agora)
    cripto = [t for t, i in univ.items() if i["tipo"] == "cripto"]
    if not cripto:
        return dia
    btc, _ = klines("BTCUSDT", agora)
    btc_ok = dc.filtro_mercado(btc["Close"].values)
    for t in cripto:
        par = univ[t].get("par", f"{t}USDT")
        comp, atual = klines(par, agora)
        cripto_px[t] = atual
        if not len(comp) or str(comp.index[-1].date()) != dia:
            continue
        a = cart["ativos"].get(t)
        passo = univ[t].get("passo_quantidade", 0.01)
        if a:
            if dc.sinal_saida(comp["Close"].values, comp["Low"].values):
                un = a["unidades"]
                ordens.append((f"saida:{t}", msg_saida(t, a["token"], un, passo, atual, a, agora_mesmo=True), None))
                fechar_trade(cart, est, t, atual, hoje)
            continue
        nv = dc.nivel_entrada(comp["High"].values[:-1])
        if not (btc_ok and dc.rompeu(float(comp["Close"].iloc[-1]), nv)):
            continue
        if recusado(est, t, hoje) or em_pausa(est, hoje):
            continue
        esc, _ = dc.escolher([{"ticker": t, "tipo": "cripto", "momentum": 0.0}], cart["ativos"].values())
        if not esc:
            continue
        patr, _ = patrimonio(cart, {}, cripto_px)
        minimo = max(ORDEM_MINIMA, univ[t].get("ordem_minima_usd") or 0)
        tam = dc.tamanho_entrada(patr, cart["reserva_usdt"], atual, passo, minimo)
        if tam is None:
            continue
        valor, un = tam
        cart["reserva_usdt"] = round(cart["reserva_usdt"] - valor, 2)
        cart["ativos"][t] = {"token": univ[t]["token"], "tipo": "cripto", "unidades": un,
                             "preco_entrada": atual, "data_entrada": hoje, "origem": "donchian"}
        saida = dc.nivel_saida(comp["Low"].values)
        ordens.append((f"entrada:{t}", msg_entrada(t, univ[t]["token"], None, atual, valor, un, passo, saida),
                       {"tipo": "entrada", "ticker": t}))
    return dia


def ultimo_preco_posicoes(cart, dados, cripto_px):
    """Guarda o ultimo preco conhecido (a ASTER roda sem baixar acoes)."""
    for t, a in cart["ativos"].items():
        p = preco_atual(t, dados, cripto_px)
        if p is not None:
            a["ultimo_preco"] = round(p, 6)


# ================================================================ DIAGNOSTICO
def diagnostico(cart, univ, dados, cripto_px, agora):
    fase = mk.fase_eua(agora)
    L = [f"🩺 DIAGNOSTICO DA CARTEIRA EUA ({agora.astimezone(mk.BR):%d/%m/%Y %Hh%M} Brasilia)",
         "Regra de saida: fechamento abaixo da minima dos 10 pregoes anteriores", ""]
    for t, a in cart["ativos"].items():
        df = dados.get(t)
        if a["tipo"] == "cripto" or df is None:
            L.append(f"{t}: sem dados")
            continue
        comp = completos(df, agora)
        px = float(df["Close"].iloc[-1])
        sai = dc.sinal_saida(comp["Close"].values, comp["Low"].values) and fase != "pregao"
        nivel = dc.nivel_saida(comp["Low"].values)
        dist = px / nivel - 1
        dec = "SAIR NA PROXIMA ABERTURA" if (sai or a.get("saida")) else "manter"
        L.append(f"{t} ({a['token']}) {a['origem']}\n"
                 f"  preco {usd(px)} | entrada {usd(a['preco_entrada'])} | lucro {pct(px / a['preco_entrada'] - 1)} "
                 f"({usd_s(a['unidades'] * (px - a['preco_entrada']))})\n"
                 f"  minima 10 dias {usd(nivel)} | distancia {pct(dist)} | {dec}")
    patr, _ = patrimonio(cart, dados, cripto_px)
    livres, _ = dc.vagas_livres(cart["ativos"].values())
    L += ["", f"Patrimonio {usd(patr)} | caixa {usd(cart['reserva_usdt'])} | vagas livres {livres} de {dc.VAGAS}"]
    return "\n".join(L)


# ================================================================ FECHAMENTO
def fechamento(cart, est, univ, dados, cripto_px, agora, hoje, sinais_dia):
    """Heatmap + destaques (sem som), depois do fechamento de NY."""
    import heatmap_eua as hm
    itens, destaques = [], []
    var = {}
    for t, a in cart["ativos"].items():
        df = dados.get(t)
        if a["tipo"] == "cripto":
            comp, atual = klines(univ.get(t, {}).get("par", f"{t}USDT"), agora)
            cripto_px[t] = atual
            v = atual / float(comp["Close"].iloc[-1]) - 1 if len(comp) else 0.0
            px = atual
        elif df is not None and len(df) >= 2:
            px = float(df["Close"].iloc[-1])
            v = px / float(df["Close"].iloc[-2]) - 1
            if a["data_entrada"] == hoje:
                v = px / a["preco_entrada"] - 1
        else:
            continue
        var[t] = v
        marca = "sai amanha" if a.get("saida") else ""
        itens.append({"ticker": t, "valor": a["unidades"] * px, "var_dia": v, "marca": marca})

    patr, _ = patrimonio(cart, dados, cripto_px)
    # lucro do dia: diferenca de patrimonio desde o ultimo fechamento (sem aportes)
    ant = est.get("patrimonio_fech")
    if ant and ant.get("data") != hoje:
        aportes = sum(x["valor"] for x in cart.get("aportes", []) if x["data"] > ant["data"])
        dia_usd = patr - ant["valor"] - aportes
        dia_pct = dia_usd / ant["valor"] if ant["valor"] else 0.0
    else:
        dia_usd = sum(it["valor"] - it["valor"] / (1 + it["var_dia"]) for it in itens)
        dia_pct = dia_usd / (patr - dia_usd) if patr - dia_usd else 0.0
    lt, lp = lucro_total(cart, patr)
    livres, _ = dc.vagas_livres(cart["ativos"].values())

    # destaques
    if var:
        alta = max(var, key=var.get)
        queda = min(var, key=var.get)
        destaques.append(f"🔼 maior alta: {alta} {pct(var[alta])} | 🔽 maior queda: {queda} {pct(var[queda])}")
    novas, perto, saindo = [], [], []
    for t, a in cart["ativos"].items():
        df = dados.get(t)
        if a["tipo"] != "acao" or df is None or len(df) < 30:
            continue
        px = float(df["Close"].iloc[-1])
        if px > dc.nivel_entrada(df["High"].values[:-1]):
            novas.append(t)
        prox = dc.nivel_saida(df["Low"].values)
        if a.get("saida"):
            saindo.append(t)
        elif px / prox - 1 < PERTO_SAIDA:
            perto.append(f"{t} ({pct(px / prox - 1)} ate {usd(prox)})")
    if novas:
        destaques.append("🏔️ nova maxima de 55 dias: " + ", ".join(novas))
    if saindo:
        destaques.append("🔴 saem na abertura: " + ", ".join(saindo))
    if perto:
        destaques.append("⚠️ a menos de 3% da saida: " + ", ".join(perto))
    if sinais_dia:
        destaques.append("🟢 sinais de entrada hoje: " + ", ".join(
            f"{t}" + (f" ({s['obs']})" if s.get("obs") else "") for t, s in sorted(sinais_dia.items())))
    bal = []
    for t, a in cart["ativos"].items():
        if a["tipo"] == "acao":
            d = proximo_resultado(t, agora)
            if d:
                bal.append(f"{t} {d:%d/%m}")
    if bal:
        destaques.append("📰 balanco nos proximos 2 pregoes: " + ", ".join(bal))
    refs = []
    for t, nome in (("SPY", "SPY"), ("QQQ", "QQQ")):
        df = dados.get(t)
        if df is not None and len(df) >= 2:
            refs.append(f"{nome} {num(float(df['Close'].iloc[-1]))} ({pct(df['Close'].iloc[-1] / df['Close'].iloc[-2] - 1)})")
    try:
        btc, btc_px = klines("BTCUSDT", agora)
        refs.append(f"BTC {num(btc_px, 0)} ({pct(btc_px / float(btc['Close'].iloc[-1]) - 1)})")
    except Exception:
        pass
    if refs:
        destaques.append("📈 " + " | ".join(refs))

    spy = dados.get("SPY")
    dia_txt = pd.Timestamp(spy.index[-1]) if spy is not None and len(spy) else pd.Timestamp(hoje)
    legenda = [f"Patrimonio {usd(patr)}   |   caixa USDT {usd(cart['reserva_usdt'])}   |   vagas livres {livres} de {dc.VAGAS}",
               f"Hoje {usd_s(dia_usd)} ({pct(dia_pct)})   |   desde o inicio {usd_s(lt)} ({pct(lp)})"]
    caminho = hm.desenhar(itens, f"Carteira EUA - {dia_txt:%d/%m/%Y}", legenda + destaques)
    texto = "\n".join([f"📊 FECHAMENTO EUA {dia_txt:%d/%m}", legenda[1], legenda[0], ""] + destaques)
    enviar_foto(caminho, texto[:1000], silencioso=True)
    if len(texto) > 1000:
        enviar(texto, silencioso=True)
    est["patrimonio_fech"] = {"data": hoje, "valor": round(patr, 2)}


# ================================================================ EUA
def enviar_ordens(ordens, est, antes, hoje):
    if not ordens:
        return
    est["ultima"] = {**antes, "chaves": [o[0] for o in ordens]}
    for chave, txt, x in ordens:
        if x is not None:
            mid = enviar(txt, botao=("❌ Recusar", f"rec|{chave}"))
            est["pendentes"][chave] = {**x, "msg_id": mid, "data": hoje}
        else:
            enviar(txt)


def rodar_eua(agora, forcar=(), auto=True):
    """forcar: fases a rodar fora do horario ('pregao', 'depois', 'cripto').
    auto: decide as fases pelo relogio de NY. Devolve os comandos que nao sao dos EUA."""
    cart, est = carregar()
    eventos = ler_comandos(est) if not TESTE else \
        [("cmd", c.split(), None) for c in os.environ.get("TESTE_COMANDOS", "").split(";") if c.strip()]
    univ = ler_universo()
    if cart.get("versao") != 2:
        raise SystemExit("carteira.json ainda no formato antigo: rode a migracao (MODO=migrar).")
    hoje = hoje_ny(agora)
    fase = mk.fase_eua(agora)
    dia_cripto = str(mk.dia_cripto_fechado(agora))

    fazer_pregao = "pregao" in forcar or (auto and fase == "pregao")
    fazer_depois = "depois" in forcar or (auto and fase == "depois" and est.get("fechamento_em") != hoje)
    fazer_cripto = "cripto" in forcar or (auto and est.get("cripto_em") != dia_cripto)
    precisa_acoes = fazer_pregao or fazer_depois or bool(eventos) or MODO == "diagnostico"

    limite = str((pd.Timestamp(hoje) - pd.Timedelta(days=2)).date())
    est["pendentes"] = {k: v for k, v in est["pendentes"].items() if v["data"] >= limite}
    est["alertados"] = {k: v for k, v in est["alertados"].items() if v >= hoje}

    alvo = set(cart["ativos"]) | {"SPY", "QQQ"}
    if fazer_pregao or fazer_depois:
        alvo |= {t for t, i in univ.items() if i["tipo"] == "acao"}
    alvo -= {t for t, i in univ.items() if i["tipo"] == "cripto"}
    dados = baixar_eua(alvo, agora) if precisa_acoes else {}
    cripto_px = {}

    respostas, restantes = processar_comandos(cart, est, eventos, univ, dados, cripto_px, hoje)
    for r in respostas:
        enviar(r, silencioso=True)

    if MODO == "diagnostico":
        enviar(diagnostico(cart, univ, dados, cripto_px, agora), silencioso=True)

    antes = {"carteira": copy.deepcopy(cart), "n_historico": len(est["historico"])}
    ordens, sinais_dia = [], {}
    if (fazer_pregao or fazer_depois) and mk.eh_pregao(agora.astimezone(NY).date()):
        finalizar_saidas(cart, est, dados, agora, hoje)
    if fazer_pregao:
        checar_entradas(cart, est, univ, dados, agora, hoje, ordens, sinais_dia)
    if fazer_depois and mk.eh_pregao(agora.astimezone(NY).date()):
        checar_saidas(cart, est, univ, dados, agora, hoje, ordens)
        for t, i in univ.items():                        # sinais do dia (para os destaques)
            df = dados.get(t)
            if i["tipo"] == "acao" and t not in cart["ativos"] and df is not None and len(df) > 30 \
                    and pd.Timestamp(df.index[-1]).normalize() == pd.Timestamp(hoje) \
                    and df["High"].iloc[-1] > dc.nivel_entrada(df["High"].values[:-1]):
                sinais_dia.setdefault(t, {"obs": "alertado" if est["alertados"].get(t) == hoje else ""})
    if fazer_cripto:
        try:
            est["cripto_em"] = checar_cripto(cart, est, univ, agora, ordens, cripto_px)
            if not TESTE:
                avisos.registrar("bot (ASTER)")
        except Exception as e:
            print("cripto falhou:", e)
            if not TESTE:  # topico Avisos, so se a mesma falha se repetir (avisos.py)
                avisos.registrar("bot (ASTER)", erro=f"{type(e).__name__}: {e}")
    enviar_ordens(ordens, est, antes, hoje)
    if fazer_depois and mk.eh_pregao(agora.astimezone(NY).date()):
        fechamento(cart, est, univ, dados, cripto_px, agora, hoje, sinais_dia)
        est["fechamento_em"] = hoje
    ultimo_preco_posicoes(cart, dados, cripto_px)
    salvar(cart, est)
    return restantes


# ================================================================ DEMO / MIGRAR
def demo(agora):
    """Uma mensagem real de teste + heatmap de exemplo. Nao grava nada."""
    cart, est = carregar()
    univ = ler_universo()
    hoje = hoje_ny(agora)
    dados = baixar_eua(set(cart["ativos"]) | {"SPY", "QQQ", "NVDA"}, agora)
    df = dados.get("NVDA")
    px = float(df["Close"].iloc[-1])
    patr, _ = patrimonio(cart, dados, {})
    valor, un = dc.tamanho_entrada(patr, max(patr / dc.VAGAS, ORDEM_MINIMA), px, PASSO_ACAO, ORDEM_MINIMA)
    enviar("🧪 TESTE DO BOT NOVO - exemplo, NAO execute\n\n"
           + msg_entrada("NVDA", token_de("NVDA", univ), token_alt("NVDA", univ), px, valor, un, PASSO_ACAO,
                         dc.nivel_saida(df["Low"].values)))
    fechamento(copy.deepcopy(cart), copy.deepcopy(est), univ, dados, {}, agora, hoje, {})


def migrar():
    """carteira.json antiga -> nova (backup em carteira_antiga.json) + estado limpo."""
    with open(ARQ_CARTEIRA, encoding="utf-8") as f:
        antiga = json.load(f)
    if antiga.get("versao") == 2:
        print("carteira.json ja esta no formato novo.")
        return
    univ = ler_universo()
    with open("carteira_antiga.json", "w", encoding="utf-8") as f:
        json.dump(antiga, f, indent=2, ensure_ascii=False)
    est_antigo = {}
    if os.path.exists(ARQ_ESTADO):
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            est_antigo = json.load(f)
        with open("estado_bot_antigo.json", "w", encoding="utf-8") as f:
            json.dump(est_antigo, f, indent=2, ensure_ascii=False)
    nova = migrar_carteira(antiga, univ)
    with open(ARQ_CARTEIRA, "w", encoding="utf-8") as f:
        json.dump(nova, f, indent=2, ensure_ascii=False)
    with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
        json.dump(migrar_estado(est_antigo), f, indent=2, ensure_ascii=False)
    print(json.dumps(nova, indent=2, ensure_ascii=False))


# ================================================================== MAIN
FASES_POR_MODO = {"tudo": ("pregao", "depois", "cripto"), "pregao": ("pregao",),
                  "fechamento": ("depois",), "cripto": ("cripto",), "diagnostico": ()}
MODO_B3 = {"auto": "auto", "tudo": "tudo", "fechamento": "fechamento", "pregao": "monitor"}


def main():
    agora = agora_utc()
    if MODO == "migrar":
        return migrar()
    if MODO == "demo":
        return demo(agora)
    if TESTE:
        fases = tuple(f for f in os.environ.get("FASE", "").split(",") if f)
        restantes = rodar_eua(agora, forcar=fases, auto=not fases)
    else:
        restantes = rodar_eua(agora, forcar=FASES_POR_MODO.get(MODO, ()), auto=(MODO == "auto"))

    # Execucoes extras (ASTER 00h05 e reforco dos :45) so cuidam dos EUA.
    so_eua = os.environ.get("CRON", "").strip() in ("5 0 * * *", "45 13-22 * * 1-5")
    modo_b3 = MODO_B3.get(MODO)
    if TESTE or so_eua or modo_b3 is None:
        return

    # ---------------- B3: mesmo esquema, feriados e horarios proprios
    if os.path.exists("carteira_b3.json"):
        try:
            import b3
            b3.rodar(restantes, modo_b3, agora)
        except Exception as e:
            print(f"A parte da B3 falhou nesta execucao: {e}")  # aviso: avisos.py no workflow
            raise

    # ---------------- Futuros: vigilancia das posicoes alavancadas
    if os.path.exists("perps.json"):
        try:
            import perps
            perps.rodar(restantes, modo_b3, agora)
        except Exception as e:
            print(f"A parte dos futuros falhou nesta execucao: {e}")  # aviso: avisos.py no workflow
            raise


if __name__ == "__main__":
    main()
