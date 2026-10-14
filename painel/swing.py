"""Swing de 1 a 3 dias (EXA long em candles de 1h) para o scanner do painel, com o setup de exposição.

Regra e saída iguais às do backtest_swing.py / backtest_swing_cripto.py:
  sinal: RSI(14) de 1h < 25 em um dos 2 candles anteriores e candle que fecha acima da máxima do
  anterior com volume >= 1,3x a média de 20.
  entrada em 3 parcelas iguais: no sinal, a -0,5 ATR e a -1 ATR (limitadas);
  stop em ATR abaixo do sinal; alvo em R a partir do preço médio planejado; sai por tempo no prazo.
Ações: só candles do pregão de NY (como no backtest). Cripto: 24h.

Setup de exposição (editar só aqui):
  BANCA, EXPOSICAO e VAGAS dão o valor de cada posição; a alavancagem por posição é a menor
  que deixa CAIXA_MIN da banca livre com todas as vagas ocupadas.
"""
import datetime
import math
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

AQUI = os.path.dirname(__file__)
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, ".."))
import backtest_swing as bs  # noqa: E402
import dados  # noqa: E402
import mercado  # noqa: E402

NY = ZoneInfo("America/New_York")
# (stop em ATR de 1h, alvo em R, prazo em dias de trade) — escolhidos nos backtests
CONFIG = {"acao": (3.0, 3.0, 3), "cripto": (3.0, 1.5, 3)}

BANCA = 14_500
EXPOSICAO = 29_000
VAGAS = 6
CAIXA_MIN = 0.30          # fração da banca que fica livre mesmo com todas as vagas ocupadas
JANELA_MIN = 15           # só avisa o candle de 1h que fechou nos últimos 15 min


def plano():
    """Valor por posição, por parcela, alavancagem e margens."""
    pos = EXPOSICAO / VAGAS
    alav = max(1, math.ceil(EXPOSICAO / (BANCA * (1 - CAIXA_MIN))))
    return {"banca": BANCA, "exposicao": EXPOSICAO, "vagas": VAGAS, "posicao": pos, "parcela": pos / 3,
            "alav": alav, "margem_posicao": pos / alav, "margem_total": EXPOSICAO / alav,
            "caixa": BANCA - EXPOSICAO / alav}


# ------------------------------------------------------------------ candles de 1h
def _barras_pregao(d):
    """Fechamentos dos candles de 1h do pregão do dia d (NY): 10:30 ... 15:30 e o meio candle das 16h."""
    h = mercado.horario_pregao(d)
    if not h:
        return []
    ab, fe = h
    out, t = [], ab + datetime.timedelta(hours=1)
    while t < fe:
        out.append(t)
        t += datetime.timedelta(hours=1)
    return out + [fe]


def acao_1h(df15):
    """Junta os candles de 15 min (índice = fechamento, UTC) em candles de 1h só do pregão."""
    if df15 is None or df15.empty:
        return None
    ab = (df15.index - pd.Timedelta(minutes=15)).tz_convert(NY)
    chave, manter, cache = [], [], {}
    for t in ab:
        if t.date() not in cache:
            cache[t.date()] = mercado.horario_pregao(t.date())
        h = cache[t.date()]
        if h and h[0] <= t < h[1]:
            fim = min(h[0] + datetime.timedelta(hours=1 + int((t - h[0]).total_seconds() // 3600)), h[1])
            chave.append(pd.Timestamp(fim).tz_convert("UTC")); manter.append(True)
        else:
            chave.append(pd.NaT); manter.append(False)
    d = df15[manter].copy()
    d["k"] = [c for c, m in zip(chave, manter) if m]
    g = d.groupby("k").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                           close=("close", "last"), volume=("volume", "sum"))
    g.index.name = None
    return g


def cripto_1h(simbolo):
    d = dados.bx("/openApi/swap/v3/quote/klines", symbol=simbolo, interval="1h", limit=300)
    if not d:
        return None
    df = pd.DataFrame(d)
    df["time"] = pd.to_datetime(df["time"].astype("int64") + 3_600_000, unit="ms", utc=True)
    df = df.astype({k: float for k in ("open", "high", "low", "close", "volume")})
    df = df.drop_duplicates("time").set_index("time").sort_index()[["open", "high", "low", "close", "volume"]]
    return df[df.index <= pd.Timestamp.now(tz="UTC")]


def hora_de_checar(classe, agora):
    """Cripto: logo depois da virada da hora. Ações: logo depois de fechar um candle de 1h do pregão."""
    if classe == "cripto":
        return agora.minute < JANELA_MIN
    ny = agora.astimezone(NY)
    return any(datetime.timedelta(0) <= ny - t < datetime.timedelta(minutes=JANELA_MIN)
               for t in _barras_pregao(ny.date()))


def prazo(classe, t_sinal, dias):
    """Hora de sair por tempo: cripto = 24h por dia; ações = 7 candles de pregão por dia."""
    if classe == "cripto":
        return t_sinal + datetime.timedelta(hours=24 * dias)
    falta, d = dias * bs.POR_DIA, t_sinal.astimezone(NY).date()
    while True:
        for t in _barras_pregao(d):
            if t > t_sinal:
                falta -= 1
                if falta == 0:
                    return t
        d += datetime.timedelta(days=1)


# ------------------------------------------------------------------ sinal
def avaliar(ativo, agora):
    classe = ativo["classe"]
    if not hora_de_checar(classe, agora):
        return None
    try:
        df = cripto_1h(ativo["simbolo"]) if classe == "cripto" else acao_1h(dados.klines(ativo["simbolo"], dias=21))
        if df is None or len(df) < 60:
            return None
        ult = df.index[-1]
        if agora - ult.to_pydatetime() >= datetime.timedelta(minutes=JANELA_MIN):
            return None                      # o último candle de 1h não acabou de fechar
        atr, sig = bs.preparar(df)
        i = len(df) - 1
        if not len(sig) or sig[-1] != i or not atr[i] > 0:
            return None
        sl_atr, tp_r, dias = CONFIG[classe]
        e0, a = float(df["close"].iloc[i]), float(atr[i])
        niveis = [e0, e0 - 0.5 * a, e0 - a]
        medio = sum(niveis) / 3
        stop = e0 - sl_atr * a
        alvo = medio + tp_r * (medio - stop)
        casas = max(2, -int(math.floor(math.log10(e0))) + 4)
        return {"ativo": ativo["nome"], "simbolo": ativo["simbolo"], "classe": classe,
                "tempo": ult.isoformat(), "parcelas": [round(x, casas) for x in niveis],
                "stop": round(stop, casas), "alvo": round(alvo, casas),
                "stop_pct": round((medio - stop) / medio * 100, 2), "alvo_pct": round((alvo - medio) / medio * 100, 2),
                "prazo": prazo(classe, ult.to_pydatetime(), dias).astimezone(datetime.timezone.utc).isoformat()}
    except Exception as e:
        print(f"swing: falhou {ativo['simbolo']}: {e}", flush=True)
        return None


def varrer(universo, agora):
    return [x for x in dados.em_paralelo(lambda a: avaliar(a, agora), universo, n=8) if x]


# ------------------------------------------------------------------ mensagem
def _n(x, casas=2):
    s = f"{x:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def texto(sinais):
    p = plano()
    br = ZoneInfo("America/Sao_Paulo")
    L = [f"📆 Swing 1–3 dias — {len(sinais)} entrada(s)",
         f"Posição US$ {_n(p['posicao'], 0)} a {p['alav']}x (margem US$ {_n(p['margem_posicao'], 0)}), "
         f"3 parcelas de US$ {_n(p['parcela'], 0)}"]
    for x in sinais:
        pz = datetime.datetime.fromisoformat(x["prazo"]).astimezone(br)
        par = x["parcelas"]
        L.append(f"\n🟢 LONG {x['ativo']} ({'ação' if x['classe'] == 'acao' else 'cripto'})\n"
                 f"1ª agora ~{_n(par[0], 4).rstrip('0').rstrip(',')} · "
                 f"2ª limite {_n(par[1], 4).rstrip('0').rstrip(',')} · 3ª limite {_n(par[2], 4).rstrip('0').rstrip(',')}\n"
                 f"Stop {_n(x['stop'], 4).rstrip('0').rstrip(',')} (−{_n(x['stop_pct'])}%) · "
                 f"Alvo {_n(x['alvo'], 4).rstrip('0').rstrip(',')} (+{_n(x['alvo_pct'])}%)\n"
                 f"Sem stop nem alvo: fechar em {pz:%d/%m %H:%M}")
    L.append("\nStop e alvo valem para a posição toda. No prazo: feche o que restar e cancele as limitadas abertas. "
             f"Só entre se houver vaga (máx. {p['vagas']} posições).")
    return "\n".join(L)
