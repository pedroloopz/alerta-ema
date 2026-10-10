# Cache de historicos (alerta-ema)

Gerado pelo workflow `historico` (historico.py na main). Nao edite a mao.

- `yahoo_{open,high,low,close,volume}.csv.gz`: diario desde 2013 (acoes da Ondo, B3, referencias)
- `yahoo_1h_*.csv.gz`: 1 hora (o Yahoo da ~730 dias; o cache acumula com o tempo)
- `binance_{PAR}.csv` e `binance_{PAR}_1h.csv`: ASTER e BTC (Binance spot)
- `_log.txt`: resumo da ultima atualizacao

Para backtest: `DADOS=dados python backtest_final.py` com esta branch em `dados/`.
