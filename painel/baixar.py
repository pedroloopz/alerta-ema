"""Pacote de pesquisa: estatisticas de 15 min da Gate (liquidacoes, fluxo taker, OI,
long/short de contas e de top traders) por 90 dias, para as cripto do universo."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(__file__))
import dados
import pandas as pd
os.makedirs("pacote", exist_ok=True)
uni = [u for u in json.load(open("painel/stats.json"))["universo"] if u["classe"] == "cripto"]
G = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
def um(a):
    linhas, ini, fim = [], int(time.time()) - 90 * 86400, int(time.time())
    try:
        while ini < fim:
            d = dados._get(G, {"contract": f"{a['nome']}_USDT", "interval": "15m", "from": ini, "limit": 100}, tentativas=3)
            if not d:
                break
            linhas += d
            novo = max(x["time"] for x in d) + 900
            if novo <= ini:
                break
            ini = novo
            time.sleep(0.15)
        if linhas:
            pd.DataFrame(linhas).drop_duplicates("time").to_csv(f"pacote/g_{a['simbolo']}.csv.gz", index=False)
            print(a["simbolo"], len(linhas), flush=True)
    except Exception as e:
        print("falhou", a["simbolo"], e, flush=True)
dados.em_paralelo(um, uni, n=4)
print("arquivos:", len(os.listdir("pacote")))
