"""COLETA 5: paginas oficiais sobre taxas e spread (Ondo, bStocks, Binance Wallet)."""
import os
import requests
OUT = os.environ.get("SAIDA", "saida"); os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124"}
for nome, url in [("ondo_fees", "https://docs.ondo.finance/ondo-stocks/fees-and-taxes.md"),
                  ("ondo_invest", "https://docs.ondo.finance/ondo-stocks/investing-and-redeeming.md"),
                  ("ondo_attest", "https://docs.ondo.finance/api-reference/attestations/request-a-mint-or-redeem-attestation.md"),
                  ("bstocks_home", "https://www.bstocks.finance/en/home"),
                  ("bnb_blog_bstocks", "https://www.bnbchain.org/en/blog/introducing-bstocks-on-bnb-chain-trade-24-7-with-zero-fees-deploy-across-defi-protocols-with-full-self-custody")]:
    try:
        r = requests.get(url, headers=UA, timeout=40)
        open(f"{OUT}/custo_{nome}.txt", "w").write(r.text)
        print(nome, r.status_code, len(r.text))
    except Exception as e:
        print(nome, e)
