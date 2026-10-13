"""Sonda temporaria: historico de liquidacoes/fluxo (Gate, OKX) e book (BingX)."""
import json, time, urllib.request, urllib.parse
def get(u, p=None):
    if p: u += "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"erro": str(e)[:300]}
agora = int(time.time()); out = {}
G = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
for nome, p in [
  ("g_5m_l100", {"contract":"BTC_USDT","interval":"5m","limit":100}),
  ("g_5m_l1000", {"contract":"BTC_USDT","interval":"5m","limit":1000}),
  ("g_15m_from90", {"contract":"BTC_USDT","interval":"15m","from":agora-90*86400,"limit":100}),
  ("g_15m_from30", {"contract":"BTC_USDT","interval":"15m","from":agora-30*86400,"limit":100}),
  ("g_1h_from90", {"contract":"BTC_USDT","interval":"1h","from":agora-90*86400,"limit":100}),
  ("g_1h_from200", {"contract":"BTC_USDT","interval":"1h","from":agora-200*86400,"limit":100}),
  ("g_1h_l2000", {"contract":"BTC_USDT","interval":"1h","limit":2000}),
  ("g_1h_l500", {"contract":"BTC_USDT","interval":"1h","limit":500}),
]:
    d = get(G, p)
    if isinstance(d, list) and d:
        ts = [x["time"] for x in d]
        out[nome] = {"n": len(d), "de_dias": round((agora-min(ts))/86400,1), "ate_dias": round((agora-max(ts))/86400,2), "campos": list(d[-1].keys()), "ult": d[-1]}
    else:
        out[nome] = {"resp": str(d)[:300]}
O = "https://www.okx.com"
for nome, path, p in [
  ("okx_taker_5m", "/api/v5/rubik/stat/taker-volume", {"ccy":"BTC","instType":"CONTRACTS","period":"5m"}),
  ("okx_taker_1h", "/api/v5/rubik/stat/taker-volume", {"ccy":"BTC","instType":"CONTRACTS","period":"1H"}),
  ("okx_oi_1h", "/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy":"BTC","period":"1H"}),
  ("okx_ls_5m_inst", "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract", {"instId":"BTC-USDT-SWAP","period":"5m","limit":100}),
  ("okx_liq", "/api/v5/public/liquidation-orders", {"instType":"SWAP","uly":"BTC-USDT","state":"filled"}),
  ("okx_topls", "/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader", {"instId":"BTC-USDT-SWAP","period":"15m","limit":100}),
]:
    d = get(O+path, p); data = d.get("data") if isinstance(d, dict) else None
    if data:
        def t0(x): return int(x[0]) if isinstance(x, list) else int(x.get("ts", 0) or 0)
        ts = [t0(x) for x in data] if isinstance(data, list) else []
        out[nome] = {"n": len(data), "de_dias": round((agora*1000-min(ts))/86400000,1) if ts and min(ts) else None, "ex": str(data[0])[:300]}
    else:
        out[nome] = {"resp": str(d)[:300]}
d = get("https://open-api.bingx.com/openApi/swap/v2/quote/depth", {"symbol":"BTC-USDT","limit":100})
out["bx_depth"] = {"ok": isinstance(d, dict) and d.get("code")==0, "ex": str(d)[:300]}
d = get("https://api.gateio.ws/api/v4/futures/usdt/liq_orders", {"contract":"BTC_USDT","from":agora-7*86400,"limit":1000})
out["g_liq_orders"] = {"n": len(d) if isinstance(d, list) else None, "ex": str(d)[:300]}
d = get("https://api.gateio.ws/api/v4/futures/usdt/order_book", {"contract":"BTC_USDT","limit":50})
out["g_book"] = {"ex": str(d)[:200]}
json.dump(out, open("painel/sonda.json","w"), indent=1)
print(json.dumps(out)[:4000])
