"""一時的な確認用(このブランチ専用、マージしない)。
全銘柄の予想PER・EPS(実績/予想)・今期/来期の予想EPS成長率・売上成長率を集め、
1行1銘柄のJSONで出力する。Yahooに止められないよう並列3・失敗時は待って再試行。"""
import sys, json, time, math
sys.path.insert(0, ".")
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
from data_sources import get_sp500_tickers, get_nasdaq100_tickers
print("yfinance", yf.__version__, flush=True)
sp, nd = get_sp500_tickers(), get_nasdaq100_tickers()
def num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None
def fetch(t):
    for attempt in range(4):
        try:
            tk = yf.Ticker(t)
            i = tk.info
            ge = tk.growth_estimates
            g = {}
            if ge is not None and not ge.empty and "stockTrend" in ge.columns:
                for p in ("0q", "+1q", "0y", "+1y"):
                    if p in ge.index:
                        g[p] = num(ge.loc[p, "stockTrend"])
            return {"t": t, "sp": t in sp, "nd": t in nd, "name": i.get("shortName"),
                    "fpe": num(i.get("forwardPE")), "tpe": num(i.get("trailingPE")),
                    "teps": num(i.get("trailingEps")), "feps": num(i.get("forwardEps")),
                    "rg": num(i.get("revenueGrowth")), "eg": num(i.get("earningsGrowth")),
                    "mc": num(i.get("marketCap")), "g0y": g.get("0y"), "g1y": g.get("+1y")}
        except Exception as e:
            err = str(e)[:80]
            time.sleep(10 * (attempt + 1))
    return {"t": t, "error": err}
tickers = sorted(set(sp) | set(nd))
t0 = time.time()
with ThreadPoolExecutor(3) as ex:
    for r in ex.map(fetch, tickers):
        print("ROW " + json.dumps(r, ensure_ascii=False), flush=True)
print(f"DONE {len(tickers)} in {time.time()-t0:.0f}s", flush=True)
