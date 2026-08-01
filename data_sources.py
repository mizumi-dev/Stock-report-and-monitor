"""
データ取得層
- Nasdaq公式(非公式内部)APIからNASDAQ100構成銘柄を取得
- yfinanceで個別銘柄データを取得
"""
import json
import urllib.request
import yfinance as yf

NASDAQ_API_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def get_nasdaq100_tickers(fallback: list[str] | None = None) -> list[str]:
    """
    NASDAQ100構成銘柄のティッカー一覧を取得する。
    失敗した場合はfallbackリストを返す（S3キャッシュ等から渡す想定）。
    """
    try:
        req = urllib.request.Request(NASDAQ_API_URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        rows = data["data"]["data"]["rows"]
        tickers = [r["symbol"] for r in rows if r.get("symbol")]
        if not tickers:
            raise ValueError("empty ticker list")
        return tickers
    except Exception as e:
        print(f"[WARN] Nasdaq API取得失敗: {e}")
        if fallback:
            print(f"[WARN] フォールバックリストを使用 ({len(fallback)}銘柄)")
            return fallback
        raise


def get_stock_data(ticker: str, period: str = "1y", min_rows: int = 60) -> dict:
    """
    1銘柄分の価格ヒストリー・PEG・成長率・時価総額を取得する。
    取得失敗時は None を含む辞書を返す(呼び出し側でスキップ判定)。

    min_rows は期間モードごとに変える必要がある。例えば200日移動平均を使うモードで
    200行未満のデータを通すと、移動平均が全てNaNになり比較が常にFalseとなって
    「シグナルが出ない」のではなく「判定が事実上無効化される」状態になるため。
    """
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period)
        if hist.empty or len(hist) < min_rows:
            return {"ticker": ticker, "history": None, "error": "insufficient_history"}

        info = tk.info
        return {
            "ticker": ticker,
            "history": hist,
            "peg": info.get("pegRatio"),
            "earnings_growth": info.get("earningsGrowth"),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or hist["Close"].iloc[-1],
            "prev_close": info.get("previousClose") or hist["Close"].iloc[-2],
            "company_name": info.get("shortName", ticker),
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "history": None, "error": str(e)}
