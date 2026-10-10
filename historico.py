"""Cache dos historicos (FASE 2), guardado na branch `dados` (workflow `historico`).

Atualiza de forma incremental: se o arquivo ja existe, baixa so os ultimos dias e junta.
Formato igual ao da coleta antiga (backtest_final.py le direto com DADOS=<pasta>):
  yahoo_{open,high,low,close,volume}.csv.gz    diario desde 2013, colunas = tickers
  yahoo_1h_{open,...}.csv.gz                   1 hora (o Yahoo da so ~730 dias; pedimos 720; o cache acumula)
  binance_{PAR}.csv e binance_{PAR}_1h.csv     ASTER e BTC (Binance spot)
  yahoo_amplo_*.csv.gz                         diario do S&P 500 + Nasdaq-100 (teste de vies)
Uso: python historico.py <pasta>
"""
import json
import os
import sys
import time

import pandas as pd
import yfinance as yf

import fontes

PASTA = sys.argv[1] if len(sys.argv) > 1 else "dados"
INICIO = "2013-01-01"
CAMPOS = ["Open", "High", "Low", "Close", "Volume"]
REFERENCIAS = ["SPY", "QQQ", "BOVA11.SA", "^BVSP", "BTC-USD", "^TNX", "USDBRL=X"]
PARES = ["ASTERUSDT", "BTCUSDT"]
# carteiras de 21/09/2026 (linha de base do backtest da FASE 3), mesmo se sairem do universo
EXTRAS = ["CRWV", "NFLX", "MVLL", "ENPH", "BE", "UBER", "UNH"] + [
    f"{t}.SA" for t in ("LREN3", "EZTC3", "VAMO3", "TTEN3", "ALOS3", "INBR32", "HBRE3", "LJQQ3",
                        "MOVI3", "MRVE3", "SIMH3")]


def universo():
    """Acoes da Ondo (universo_ondo.json) + carteira B3 + IBrX-100/Small Caps + referencias."""
    eua = [a["ticker"] for a in json.load(open("universo_ondo.json"))["ativos"] if a["tipo"] == "acao"]
    b3 = list(json.load(open("carteira_b3.json")).get("ativos", {}))
    try:
        import b3 as mod
        for codigo in ("IBXX", "SMLL"):
            b3 += mod.indice_b3(codigo)
    except Exception as e:
        print(f"lista da B3 indisponivel ({e}); so a carteira")
    return sorted(set(eua) | {f"{t}.SA" for t in b3} | set(REFERENCIAS) | set(EXTRAS))


def ler(prefixo):
    arqs = {k: f"{PASTA}/{prefixo}_{k.lower()}.csv.gz" for k in CAMPOS}
    if not all(os.path.exists(a) for a in arqs.values()):
        return None
    return {k: pd.read_csv(a, index_col=0, parse_dates=True) for k, a in arqs.items()}


def gravar(prefixo, tabelas):
    for k, df in tabelas.items():
        df.sort_index().to_csv(f"{PASTA}/{prefixo}_{k.lower()}.csv.gz", float_format="%.6g")


def baixar(tickers, **kw):
    """{campo: DataFrame(tickers)} via yfinance em lotes; reserva por ticker: Yahoo direto."""
    campos = {k: {} for k in CAMPOS}
    for i in range(0, len(tickers), 60):
        lote = tickers[i:i + 60]
        bruto = None
        for tentativa in range(3):
            try:
                bruto = yf.download(lote, auto_adjust=True, progress=False, group_by="ticker",
                                    threads=True, **kw)
                break
            except Exception as e:
                print(f"yfinance lote {i}: {e}")
                time.sleep(20)
        for t in lote:
            try:
                df = bruto[t].dropna(how="all") if bruto is not None else None
            except KeyError:
                df = None
            if df is None or df.empty:
                try:  # reserva: Yahoo direto (registra no log, sem aviso)
                    periodo = "720d" if kw.get("interval") == "1h" else "max"
                    df = fontes.yahoo_direto(t, periodo, kw.get("interval", "1d"))
                    print(f"[fontes] {t}: yfinance vazio, veio pela reserva Yahoo direto")
                except Exception:
                    continue
            idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
            unico = ~idx.duplicated(keep="last")   # a reserva as vezes repete horario
            for k in CAMPOS:
                campos[k][t] = pd.Series(df[k].values[unico], index=idx[unico])
    return {k: pd.DataFrame(v) for k, v in campos.items()}


def juntar(velho, novo):
    """Novo sobrescreve as datas repetidas (corrige o ultimo candle e ajustes recentes)."""
    if velho is None:
        return novo
    return {k: novo[k].combine_first(velho[k]) if not novo[k].empty else velho[k] for k in CAMPOS}


def acoes(tickers, prefixo, intervalo):
    velho = ler(prefixo)
    if velho is None:
        kw = {"start": INICIO} if intervalo == "1d" else {"period": "720d", "interval": "1h"}
        print(f"{prefixo}: primeira carga de {len(tickers)} tickers")
        novo = baixar(tickers, **kw)
    else:
        sem_historico = [t for t in tickers if t not in velho["Close"]]
        dias = "10d" if intervalo == "1d" else "5d"
        kw = {"period": dias} if intervalo == "1d" else {"period": dias, "interval": "1h"}
        novo = baixar([t for t in tickers if t in velho["Close"]], **kw)
        if sem_historico:  # ticker novo no universo: historico completo so dele
            kw_novo = {"start": INICIO} if intervalo == "1d" else {"period": "720d", "interval": "1h"}
            extra = baixar(sem_historico, **kw_novo)
            novo = {k: pd.concat([novo[k], extra[k]], axis=1) for k in CAMPOS}
        print(f"{prefixo}: +{len(tickers) - len(sem_historico)} atualizados, {len(sem_historico)} novos")
    tabelas = juntar(velho, novo)
    gravar(prefixo, tabelas)
    c = tabelas["Close"]
    return f"{prefixo}: {c.shape[1]} tickers, {c.index.min():%Y-%m-%d} a {c.index.max():%Y-%m-%d %H:%M}"


def cripto(par, intervalo):
    arq = f"{PASTA}/binance_{par}{'' if intervalo == '1d' else '_' + intervalo}.csv"
    velho = pd.read_csv(arq, index_col=0, parse_dates=True) if os.path.exists(arq) else None
    if velho is None or velho.empty:
        inicio = int(pd.Timestamp("2017-01-01" if intervalo == "1d" else "2023-01-01").timestamp() * 1000)
    else:
        inicio = int((velho.index[-1] - pd.Timedelta(days=3)).timestamp() * 1000)
    novo = fontes.cripto_desde(par, intervalo, inicio)
    df = novo.combine_first(velho) if velho is not None else novo
    df.sort_index().to_csv(arq)
    return f"binance {par} {intervalo}: {len(df)} candles, ate {df.index.max():%Y-%m-%d %H:%M}"


def main():
    os.makedirs(PASTA, exist_ok=True)
    tickers = universo()
    log = [f"{pd.Timestamp.now("UTC"):%Y-%m-%d %H:%M} UTC, {len(tickers)} tickers"]
    log.append(acoes(tickers, "yahoo", "1d"))
    log.append(acoes(tickers, "yahoo_1h", "1h"))
    try:  # universo amplo e neutro (S&P 500 + Nasdaq-100), so diario: teste de vies do backtest
        import bot
        amplo = sorted(set(bot.universo()) | {"SPY"})
        log.append(acoes(amplo, "yahoo_amplo", "1d"))
    except Exception as e:
        log.append(f"yahoo_amplo: ERRO {e}")
    for par in PARES:
        for intervalo in ("1d", "1h"):
            try:
                log.append(cripto(par, intervalo))
            except Exception as e:
                log.append(f"binance {par} {intervalo}: ERRO {e}")
    open(f"{PASTA}/_log.txt", "w").write("\n".join(log) + "\n")
    print("\n".join(log))


if __name__ == "__main__":
    main()
