"""
データ取得層
- Nasdaq公式(非公式内部)APIからNASDAQ100構成銘柄を取得
- WikipediaのS&P500構成銘柄一覧からS&P500構成銘柄を取得
- yfinanceで個別銘柄データを取得
"""
import json
from datetime import datetime, timezone
import math
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

        # 各行(<tr>)の1列目(Symbol)が <td><a ...>TICKER</a> という形。BRK.B/BF.Bのような
        # ピリオド入りティッカーも許可する。行ごとに区切ってから最初の一致だけを
        # 拾う設計にしている点が重要: 2列目(Security名)にも<td><a>リンクがあり、
        # IQVIA(社名がそのままティッカー風の全大文字)のような銘柄では2列目も
        # 正規表現にマッチしてしまい、行全体をfindallすると誤ったティッカー("IQVIA"、
        # 本来は"IQV")や既存ティッカーの重複("IBM"の記事タイトルがそのまま"IBM"のため)
        # が混入することを実データ検証(2026-08-07)で確認した
        row_pattern = re.compile(r'<td[^>]*>\s*<a[^>]*>([A-Z]{1,6}(?:\.[A-Z])?)</a>')
        tickers = []
        for row in re.split(r'<tr\b', section)[1:]:
            m = row_pattern.search(row)
            if m:
                tickers.append(m.group(1))
        # yfinance側の表記に合わせ、"BRK.B"のようなピリオドはハイフンに変換する
        # (yfinanceはクラス株をBRK-Bのように表記する)
        tickers = [t.replace(".", "-") for t in tickers]
        # 行スコープでのパースにより既知の原因(2列目の誤マッチ)は解消済みだが、
        # 未知のページ構造要因での重複に備えた安全策として、順序を保ったまま
        # 重複除去しておく(実害はなく、正しい銘柄を誤って落とすことはない)
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


# 銘柄の基本情報(tk.info)は判定期間モードに依存しないため、同じ日の中では1回だけ
# 問い合わせて使い回す(モードごとのスキャン・監視レポートで同じ銘柄を何度も問い合わせ、
# Yahooへの負荷が積み上がるのを防ぐ、2026-11-26)。株価履歴は期間がモードごとに
# 違うので毎回取得する。Lambdaの再利用コンテナでも日付が変われば取り直す
_INFO_CACHE: dict[tuple[str, str], dict] = {}


def _get_info(tk, ticker: str) -> dict:
    key = (ticker, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if key not in _INFO_CACHE:
        if len(_INFO_CACHE) > 5000:
            _INFO_CACHE.clear()
        _INFO_CACHE[key] = tk.info
    return _INFO_CACHE[key]


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

        info = _get_info(tk, ticker)
        # PEG・成長率はここでは確定させない(YahooのpegRatioは異常値があるため使わない)。
        # 呼び出し側で来期の予想EPS成長率を取得し、apply_forward_pegで埋める
        return {
            "ticker": ticker,
            "history": hist,
            "forward_pe": info.get("forwardPE"),
            "trailing_eps": info.get("trailingEps"),
            # 今期・来期の予想EPS(アナリストのコンセンサス)。来期の予想EPS成長率は
            # この2つから計算でき、追加の問い合わせが要らない
            "eps_current_year": info.get("epsCurrentYear"),
            "eps_next_year": info.get("forwardEps") or info.get("epsForward"),
            "peg": None,
            "earnings_growth": None,
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice") or hist["Close"].iloc[-1],
            "prev_close": info.get("previousClose") or hist["Close"].iloc[-2],
            "company_name": info.get("shortName", ticker),
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "history": None, "error": str(e)}


def _as_finite_float(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def get_growth_estimate(ticker: str) -> dict | None:
    """今期(0y)・来期(+1y)の予想EPS成長率(アナリストのコンセンサス、0.2=+20%)を
    {"current_year": ..., "next_year": ...} で返す。予想表自体が無い銘柄はNone、
    片方だけ無い場合はその値がNone。通信エラー等の取得失敗は例外を送出する(呼び出し側で
    「予想が無い」と「取れなかった」を区別し、取れなかった分はキャッシュしないため)。"""
    ge = yf.Ticker(ticker).growth_estimates
    if ge is None or getattr(ge, "empty", True) or "stockTrend" not in ge.columns:
        return None
    def pick(period):
        return _as_finite_float(ge.loc[period, "stockTrend"]) if period in ge.index else None
    return {"current_year": pick("0y"), "next_year": pick("+1y")}


def forward_peg(forward_pe, growth) -> float | None:
    """PEG = 予想PER ÷ 来期の予想EPS成長率(%)。どちらかが無い・0以下ならNone
    (赤字予想や減益予想の銘柄はPEGが意味を持たないため)。"""
    if forward_pe is None or growth is None:
        return None
    try:
        forward_pe, growth = float(forward_pe), float(growth)
    except (TypeError, ValueError):
        return None
    if forward_pe <= 0 or growth <= 0:
        return None
    return forward_pe / (growth * 100)


def next_year_growth(eps_current_year, eps_next_year) -> float | None:
    """来期の予想EPS成長率 = 来期の予想EPS ÷ 今期の予想EPS − 1。今期の予想EPSが0以下
    (赤字予想)だと成長率が意味を持たないためNone(足切りで外れる)。
    Yahooの予想表(growth_estimatesの+1y)と同じ値になることを実データで確認済み
    (NVDA +71.7%、TWLO +11.1%、2026-11-26)。"""
    cur, nxt = _as_finite_float(eps_current_year), _as_finite_float(eps_next_year)
    if cur is None or nxt is None or cur <= 0:
        return None
    return nxt / cur - 1


def apply_forward_peg(stock: dict) -> dict:
    """get_stock_dataの結果に、来期の予想EPS成長率とそれを使ったPEGを埋める。
    今期の予想EPS成長率(一時的な落ち込みの判定用)は、足切りの候補にだけ別途埋める"""
    stock["earnings_growth"] = next_year_growth(stock.get("eps_current_year"), stock.get("eps_next_year"))
    stock["peg"] = forward_peg(stock.get("forward_pe"), stock["earnings_growth"])
    stock.setdefault("current_year_growth", None)
    return stock
