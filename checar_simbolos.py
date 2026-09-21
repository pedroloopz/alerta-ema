"""
Confere quais dos simbolos da lista realmente existem na API da Binance
e em qual endpoint. Rode ANTES de ligar o robo.

    pip install requests
    python checar_simbolos.py
"""

import requests
from simbolos import SIMBOLOS

ENDPOINTS = {
    "futuros (fapi)": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "dados publicos": "https://data-api.binance.vision/api/v3/exchangeInfo",
    "spot": "https://api.binance.com/api/v3/exchangeInfo",
}


def main() -> None:
    encontrados = {}

    for rotulo, url in ENDPOINTS.items():
        try:
            r = requests.get(url, timeout=30)
        except Exception as e:
            print(f"{rotulo}: falha de conexao ({e})")
            continue

        if r.status_code == 451:
            print(f"{rotulo}: HTTP 451 - bloqueio geografico neste IP")
            continue
        if r.status_code != 200:
            print(f"{rotulo}: HTTP {r.status_code}")
            continue

        disponiveis = {s["symbol"] for s in r.json().get("symbols", [])}
        achados = [s for s in SIMBOLOS if s in disponiveis]
        print(f"{rotulo}: {len(achados)} de {len(SIMBOLOS)} simbolos encontrados")
        for s in achados:
            encontrados.setdefault(s, rotulo)

    faltando = [s for s in SIMBOLOS if s not in encontrados]
    print("\n=== RESUMO ===")
    print(f"Encontrados: {len(encontrados)}")
    print(f"Nao encontrados: {len(faltando)}")
    if faltando:
        print("\nAjustar ou remover de simbolos.py:")
        for s in faltando:
            print(f"  {s}")


if __name__ == "__main__":
    main()
