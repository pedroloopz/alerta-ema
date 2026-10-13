"""Sonda temporaria: testa quais APIs publicas respondem ao GitHub Actions."""
import json, urllib.request, time
T = [
 ("bx_contracts", "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"),
 ("bx_ticker", "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"),
 ("bx_premium", "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"),
 ("bx_oi", "https://open-api.bingx.com/openApi/swap/v2/quote/openInterest?symbol=BTC-USDT"),
 ("bx_kl", "https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol=BTC-USDT&interval=15m&limit=3"),
 ("bx_kl_max", "https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol=BTC-USDT&interval=15m&limit=1440"),
 ("bx_rules", "https://open-api.bingx.com/openApi/swap/v1/tradingRules?symbol=BTC-USDT"),
 ("cg_markets", "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"),
 ("cmc_web", "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing?start=1&limit=300&sortBy=market_cap"),
 ("okx_ls", "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H"),
 ("bybit_ls", "https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1h&limit=2"),
 ("binf_ls", "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=2"),
 ("gate_ls", "https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval=1h&limit=2"),
]
out = {}
for k, u in T:
    t = time.time()
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 painel"})
        with urllib.request.urlopen(req, timeout=25) as r:
            b = r.read().decode()
        try:
            js = json.loads(b)
        except Exception:
            js = b[:500]
        out[k] = {"ok": True, "ms": int((time.time()-t)*1000), "len": len(b), "amostra": js if len(b) < 3000 else b[:3000]}
        if k == "bx_contracts":
            json.dump(js, open("painel/contracts.json", "w"))
        if k == "bx_kl_max":
            out[k]["n"] = len(js.get("data", []))
    except Exception as e:
        out[k] = {"ok": False, "erro": str(e)[:300]}
json.dump(out, open("painel/sonda.json", "w"), indent=1, ensure_ascii=False)
print({k: v["ok"] for k, v in out.items()})
