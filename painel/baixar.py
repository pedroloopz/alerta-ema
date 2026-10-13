"""Pacote de dados para pesquisa: 90 dias de candles de 15 min (cripto, Binance spot) e
o que a BingX der para as ações, funding da BingX e long/short da Gate (1h)."""
import os, sys, json, time, tarfile, io
sys.path.insert(0, os.path.dirname(__file__))
import dados
import pandas as pd

os.makedirs("pacote", exist_ok=True)
st = json.load(open("painel/stats.json"))
uni = st["universo"]
json.dump(uni, open("pacote/universo.json", "w"))

def um(a):
    try:
        df = dados.historico(a, 90)
        if df is None or df.empty:
            return
        df.to_csv(f"pacote/k_{a['simbolo']}.csv.gz")
        if a["classe"] == "cripto":
            try:
                f = dados.bx("/openApi/swap/v2/quote/fundingRate", symbol=a["simbolo"], limit=1000)
                pd.DataFrame(f).to_csv(f"pacote/f_{a['simbolo']}.csv.gz", index=False)
            except Exception as e:
                print("funding", a["simbolo"], e)
            try:
                g = dados._get("https://api.gateio.ws/api/v4/futures/usdt/contract_stats",
                               {"contract": f"{a['nome']}_USDT", "interval": "1h",
                                "from": int(time.time()) - 90 * 86400, "limit": 2200}, tentativas=1)
                if g:
                    pd.DataFrame(g).to_csv(f"pacote/g_{a['simbolo']}.csv.gz", index=False)
            except Exception as e:
                print("gate", a["simbolo"], e)
    except Exception as e:
        print("falhou", a["simbolo"], e)

dados.em_paralelo(um, uni, n=6)
print("arquivos:", len(os.listdir("pacote")))
