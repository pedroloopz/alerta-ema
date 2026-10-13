"""Pacote de pesquisa: estatisticas de 1h da Gate (liquidacoes, fluxo taker, OI,
long/short de contas e de top traders) por ~83 dias, para as cripto do universo."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(__file__))
import dados
import pandas as pd
os.makedirs("pacote", exist_ok=True)
uni = [u for u in json.load(open("painel/stats.json"))["universo"] if u["classe"] == "cripto"]
G = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
def um(a):
    try:
        linhas = dados._get(G, {"contract": f"{a['nome']}_USDT", "interval": "1h", "limit": 2000}, tentativas=3)
        time.sleep(0.2)
        if linhas:
            pd.DataFrame(linhas).drop_duplicates("time").to_csv(f"pacote/g_{a['simbolo']}.csv.gz", index=False)
            print(a["simbolo"], len(linhas), flush=True)
    except Exception as e:
        print("falhou", a["simbolo"], e, flush=True)
dados.em_paralelo(um, uni, n=4)
print("arquivos:", len(os.listdir("pacote")))
