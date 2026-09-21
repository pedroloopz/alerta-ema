"""
Descobre em qual corretora e endpoint estao os seus contratos.

Testa varios enderecos, conta quantos dos seus simbolos existem em cada
um e mostra como a corretora nomeia os contratos de acao.
"""

import requests
from simbolos import SIMBOLOS

ENDPOINTS = {
    "ASTER fapi v3":    "https://fapi.asterdex.com/fapi/v3/exchangeInfo",
    "ASTER fapi v1":    "https://fapi.asterdex.com/fapi/v1/exchangeInfo",
    "BINANCE futuros":  "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "BINANCE publico":  "https://data-api.binance.vision/api/v3/exchangeInfo",
    "BINANCE spot":     "https://api.binance.com/api/v3/exchangeInfo",
}

# Se algum desses aparecer no catalogo, achamos a corretora certa
PROCURAR = ["NVDA", "AAPL", "TSLA", "SPCX", "OPENAI", "ANTHROPIC", "MSTR"]


def main() -> None:
    melhor = None

    for rotulo, url in ENDPOINTS.items():
        print(f"\n--- {rotulo}")
        try:
            r = requests.get(url, timeout=30)
        except Exception as e:
            print(f"    falha de conexao: {e}")
            continue

        if r.status_code == 451:
            print("    HTTP 451 - bloqueio geografico neste servidor")
            continue
        if r.status_code != 200:
            print(f"    HTTP {r.status_code}: {r.text[:100]}")
            continue

        try:
            catalogo = {s["symbol"] for s in r.json().get("symbols", [])}
        except Exception as e:
            print(f"    resposta ilegivel: {e}")
            continue

        achados = [s for s in SIMBOLOS if s in catalogo]
        print(f"    catalogo com {len(catalogo)} contratos")
        print(f"    {len(achados)} dos seus {len(SIMBOLOS)} simbolos batem")

        # Como essa corretora nomeia os contratos de acao?
        parecidos = sorted(
            c for c in catalogo
            if any(p in c.upper() for p in PROCURAR)
        )
        if parecidos:
            print(f"    exemplos parecidos: {', '.join(parecidos[:12])}")

        if melhor is None or len(achados) > melhor[1]:
            melhor = (rotulo, len(achados), url, catalogo)

    print("\n=== RESUMO ===")
    if not melhor or melhor[1] == 0:
        print("Nenhum endpoint reconheceu os simbolos.")
        print("Mande este log inteiro para eu ajustar.")
        return

    rotulo, quantos, url, catalogo = melhor
    print(f"Melhor endpoint: {rotulo}")
    print(f"Simbolos reconhecidos: {quantos} de {len(SIMBOLOS)}")
    print(f"Use em BINANCE_KLINES: {url.replace('exchangeInfo', 'klines')}")

    faltando = [s for s in SIMBOLOS if s not in catalogo]
    if faltando:
        print(f"\nNao existem nesse endpoint ({len(faltando)}):")
        print("  " + ", ".join(faltando))


if __name__ == "__main__":
    main()
