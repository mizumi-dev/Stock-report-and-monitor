"""
データ取得層
- Nasdaq公式(非公式内部)APIからNASDAQ100構成銘柄を取得
- WikipediaのS&P500構成銘柄一覧からS&P500構成銘柄を取得
- yfinanceで個別銘柄データを取得
"""
import json
import re
import urllib.request
import yfinance as yf

NASDAQ_API_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
# S&P500の構成銘柄を配信する公式無償APIは存在しない(S&P Dow Jones Indices社の
# 正規データは有償)。NASDAQ100と同様の「公式一次情報+障害時fallback」の設計は
# 維持できないため、次善としてWikipediaの一覧ページ(頻繁に更新され、多くの
# 金融系OSSツールが同様に参照している)から取得する。ページ構造が変わると
# パースが壊れる可能性があるため、失敗時はNASDAQ100と同じくfallback
# (app_settingsの前回キャッシュ)に必ずフォールバックする設計にしている。
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
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


def get_sp500_tickers(fallback: list[str] | None = None) -> list[str]:
    """
    S&P500構成銘柄のティッカー一覧を取得する。
    失敗した場合はfallbackリストを返す(app_settingsのキャッシュ等から渡す想定、
    get_nasdaq100_tickersと同じ資本弾性設計)。
    """
    try:
        req = urllib.request.Request(SP500_WIKI_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # id="constituents"のテーブルだけを対象にする(ページ内には他の表もあるため)。
        # 次のテーブル(id="changes"、過去の構成変更履歴)の手前までに区切る
        start = html.find('id="constituents"')
        if start == -1:
            raise ValueError("constituentsテーブルが見つからない")
        end = html.find('id="change', start)
        section = html[start:end if end != -1 else start + 500_000]

        # 各行の1列目(Symbol)は <td><a ...>TICKER</a> という形。BRK.B/BF.Bのような
        # ピリオド入りティッカーも許可する
        tickers = re.findall(r'<td[^>]*>\s*<a[^>]*>([A-Z]{1,6}(?:\.[A-Z])?)</a>', section)
        # yfinance側の表記に合わせ、"BRK.B"のようなピリオドはハイフンに変換する
        # (yfinanceはクラス株をBRK-Bのように表記する)
        tickers = [t.replace(".", "-") for t in tickers]
        # 実データ検証(2026-08-07)で、ページ内の脚注等により1銘柄だけ重複して
        # マッチすることがあると判明した。原因のページ構造までは特定していないが、
        # 実害としては重複除去で十分安全に解消できるため、順序を保ったまま除去する
        tickers = list(dict.fromkeys(tickers))
        if len(tickers) < 400:  # 500件のはずが大幅に少ない場合はパース失敗とみなす
            raise ValueError(f"取得件数が少なすぎる({len(tickers)}件)")
        return tickers
    except Exception as e:
        print(f"[WARN] S&P500構成銘柄の取得失敗: {e}")
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
