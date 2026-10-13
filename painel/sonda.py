"""Sonda temporaria: profundidade do historico de klines 15m na BingX e Binance vision."""
import json, time, urllib.request, urllib.parse
def get(u, p):
    u = u + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"erro": str(e)[:200]}
agora = int(time.time()*1000); dia = 86_400_000
out = {}
B = "https://open-api.bingx.com"
for nome, path, p in [
  ("end_10d", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"endTime":agora-10*dia}),
  ("end_20d", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"endTime":agora-20*dia}),
  ("start_40d", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"startTime":agora-40*dia}),
  ("start_end_40d", "/openApi/swap/v3/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"startTime":agora-40*dia,"endTime":agora-30*dia}),
  ("v2_end_20d", "/openApi/swap/v2/quote/klines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"endTime":agora-20*dia}),
  ("mark_end_20d", "/openApi/swap/v1/market/markPriceKlines", {"symbol":"BTC-USDT","interval":"15m","limit":1000,"endTime":agora-20*dia}),
  ("stock_start_40d", "/openApi/swap/v3/quote/klines", {"symbol":"NCSKNVDA2USD-USDT","interval":"15m","limit":1000,"startTime":agora-40*dia,"endTime":agora-30*dia}),
  ("stock_1h_end_60d", "/openApi/swap/v3/quote/klines", {"symbol":"NCSKNVDA2USD-USDT","interval":"1h","limit":1000,"endTime":agora-60*dia}),
]:
    js = get(B+path, p); d = js.get("data") if isinstance(js, dict) else None
    if isinstance(d, list) and d:
        ts = [int(k["time"] if isinstance(k, dict) else k[0]) for k in d]
        out[nome] = {"n": len(d), "de_dias": round((agora-min(ts))/dia,1), "ate_dias": round((agora-max(ts))/dia,1)}
    else:
        out[nome] = {"resp": str(js)[:200]}
V = "https://data-api.binance.vision/api/v3/klines"
for s in ("BTCUSDT","HYPEUSDT"):
    js = get(V, {"symbol":s,"interval":"15m","limit":1000,"startTime":agora-90*dia})
    out["vision_"+s] = {"n": len(js) if isinstance(js, list) else str(js)[:200]}
# volume e amplitude por hora numa acao perp (ultimos 1000 candles 15m)
js = get(B+"/openApi/swap/v3/quote/klines", {"symbol":"NCSKNVDA2USD-USDT","interval":"15m","limit":1000})
import collections
amp = collections.defaultdict(list); vol = collections.defaultdict(list)
for k in js.get("data", []):
    t = time.gmtime((int(k["time"]))/1000); h = f"{t.tm_wday}|{t.tm_hour}"
    amp[h].append((float(k["high"])-float(k["low"]))/float(k["close"])*100); vol[h].append(float(k["volume"]))
out["nvda_hora"] = {h: [round(sum(a)/len(a),3), round(sum(vol[h])/len(vol[h]),1)] for h, a in sorted(amp.items())}
json.dump(out, open("painel/sonda.json","w"), indent=1)
print(json.dumps(out)[:3000])
