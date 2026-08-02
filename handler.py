"""
daily-batch Lambda ハンドラ
毎日1回、EventBridge Schedulerから起動される想定
"""
import html
import os
import traceback
import boto3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config import (
    DEFAULT_PERIOD_MODE,
    FETCH_MAX_WORKERS,
    MIN_FETCH_SUCCESS_RATIO,
    NASDAQ100_CACHE_KEY,
    PERIOD_MODE_KEY,
    SETTINGS_TABLE_DEFAULT,
    get_preset,
    normalize_mode,
)
from data_sources import get_nasdaq100_tickers, get_stock_data
from signals import calc_all_signals
from ranking import rank_stocks
from report import build_selection_report, build_monitoring_report

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
SIGNAL_HISTORY_TABLE = os.environ.get("SIGNAL_HISTORY_TABLE", "signal_history")
SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
SES_SENDER = os.environ["SES_SENDER"]
SES_RECIPIENT = os.environ["SES_RECIPIENT"]
REGISTER_URL = os.environ["REGISTER_FUNCTION_URL"]
REGISTER_TOKEN = os.environ["REGISTER_TOKEN"]  # メール内リンクに付与する招待トークン
IS_WEEKLY_REPORT_DAY = os.environ.get("WEEKLY_REPORT_WEEKDAY", "0")  # 0=月曜
TOP_N = int(os.environ.get("TOP_N", "3"))

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses")


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def get_period_mode() -> str:
    """利用者が画面で選んだ期間モードを読む。未設定・取得失敗時は既定モードで動かす
    (設定が読めないだけでレポート全体を止める必要はないため)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": PERIOD_MODE_KEY})
        return normalize_mode(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] 期間モードの取得に失敗、既定値({DEFAULT_PERIOD_MODE})を使用: {e}")
        return DEFAULT_PERIOD_MODE


def get_cached_tickers() -> list[str] | None:
    """前回成功時に保存したNASDAQ100構成銘柄リスト。Nasdaq API障害時のフォールバック用。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": NASDAQ100_CACHE_KEY})
        tickers = resp.get("Item", {}).get("value")
        return list(tickers) if tickers else None
    except Exception as e:
        print(f"[WARN] 銘柄キャッシュの取得に失敗: {e}")
        return None


def save_cached_tickers(tickers: list[str]):
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        table.put_item(Item={
            "key": NASDAQ100_CACHE_KEY,
            "value": tickers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        # キャッシュ更新の失敗でレポート送信自体を止める必要はない
        print(f"[WARN] 銘柄キャッシュの保存に失敗: {e}")


def get_watchlist() -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.scan()
    return [item["ticker"] for item in resp.get("Items", [])]


def get_prev_signals(ticker: str) -> dict | None:
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    resp = table.get_item(Key={"ticker": ticker, "date": _yesterday_str()})
    return resp.get("Item", {}).get("signals")


def _to_dynamodb_safe(value):
    """DynamoDBはPythonのfloatを受け付けないためDecimalに変換する"""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_safe(v) for v in value]
    return value


def save_signals(ticker: str, signals: dict):
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    table.put_item(Item={"ticker": ticker, "date": _today_str(), "signals": _to_dynamodb_safe(signals)})


def send_email(subject: str, html_body: str):
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [SES_RECIPIENT]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
        },
    )


def run_selection_report():
    """週次: NASDAQ100全銘柄をスキャンしてランキング上位N件のレポートを送る"""
    mode = get_period_mode()
    preset = get_preset(mode)

    cached = get_cached_tickers()
    tickers = get_nasdaq100_tickers(fallback=cached)
    save_cached_tickers(tickers)

    # I/O待ちが支配的な逐次取得がボトルネックのため並列化する。非公式APIへの
    # 負荷を抑えるため全並列ではなくFETCH_MAX_WORKERSで頭打ちにする。
    stocks, signals_map = [], {}
    with ThreadPoolExecutor(max_workers=FETCH_MAX_WORKERS) as executor:
        fetched = executor.map(
            lambda t: get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"]),
            tickers,
        )
        for d in fetched:
            if d.get("history") is None:
                continue
            stocks.append(d)
            signals_map[d["ticker"]] = calc_all_signals(d["history"], mode)

    # 個別銘柄のたまたまの失敗ではなく外部API自体が落ちている可能性が高い場合は、
    # 中身がほぼ空のレポートを送らずエラー通知に切り替える(handler()のtry/exceptに乗せる)
    success_ratio = len(stocks) / len(tickers) if tickers else 0
    if success_ratio < MIN_FETCH_SUCCESS_RATIO:
        raise RuntimeError(
            f"銘柄データの取得成功率が低すぎます({len(stocks)}/{len(tickers)}件、"
            f"閾値{MIN_FETCH_SUCCESS_RATIO:.0%}未満)。外部APIの障害の可能性があります。"
        )

    ranked = rank_stocks(stocks, top_n=TOP_N)
    body = build_selection_report(ranked, signals_map, REGISTER_URL, REGISTER_TOKEN, preset["label"])
    send_email("📈 今週の買い時レポート", body)


def run_monitoring_report():
    """日次: 登録銘柄のみ監視してレポートを送る"""
    watchlist = get_watchlist()
    if not watchlist:
        print("[INFO] 監視リストが空のため監視レポートはスキップ")
        return

    mode = get_period_mode()
    preset = get_preset(mode)
    results, skipped = [], []
    for t in watchlist:
        d = get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"])
        if d.get("history") is None:
            # 上場間もない等でデータが足りない銘柄。黙って消すと利用者が混乱するため
            # レポートに理由付きで載せる
            skipped.append(t)
            continue
        sig = calc_all_signals(d["history"], mode)
        prev_sig = get_prev_signals(t)
        results.append({
            "ticker": t,
            "price": d["current_price"],
            "prev_close": d["prev_close"],
            "signals": sig,
            "prev_signals": prev_sig,
            "peg": d.get("peg"),
        })
        save_signals(t, sig)

    body = build_monitoring_report(results, preset["label"], skipped)
    send_email("📊 本日の監視レポート", body)


def should_send_selection(mode: str, weekday: str, day_of_month: int, weekly_report_day: str) -> bool:
    """選定レポートを送るべきかを判定する純粋関数(テスト容易性のため副作用を持たせない)。
    短期=毎日、中期=毎週指定曜日、長期=月初(1〜7日)の指定曜日のみ(月1回)。"""
    is_report_weekday = weekday == weekly_report_day
    return (
        mode == "SHORT"
        or (mode == "MID" and is_report_weekday)
        or (mode == "LONG" and is_report_weekday and day_of_month <= 7)
    )


def _dispatch(event: dict) -> dict:
    # 画面の「監視レポートを送る」ボタンから来た場合は監視のみ実行する
    if event.get("monitoring_only"):
        run_monitoring_report()
        return {"statusCode": 200, "body": "OK"}

    # 画面の「選定レポートを送る」ボタン、またはテスト用に選定のみ実行する
    if event.get("force_weekly"):
        run_selection_report()
        return {"statusCode": 200, "body": "OK"}

    # EventBridge Schedulerからの定期実行。TODO#7a: simulate_dateがあればそれを使い、
    # 通常は実行時刻を使う(本番動作に影響を与えずテスト時だけ日付を差し替えられる)
    simulate_date = event.get("simulate_date")
    now = datetime.fromisoformat(simulate_date) if simulate_date else datetime.now(timezone.utc)
    weekday = str(now.weekday())
    day_of_month = now.day
    mode = get_period_mode()

    if should_send_selection(mode, weekday, day_of_month, IS_WEEKLY_REPORT_DAY):
        run_selection_report()
    run_monitoring_report()
    return {"statusCode": 200, "body": "OK"}


def _notify_failure(event: dict, exc: Exception):
    """失敗をメールで知らせる。通知自体が失敗しても呼び出し元の例外送出は止めない
    (元の例外を握りつぶすと、CloudWatchのErrorsメトリクスに記録されず
    無人実行が黙って失敗し続けるという、対応しようとしている問題そのものを再発させるため)。"""
    try:
        body = (
            f"<p>daily-batchの実行中にエラーが発生しました。</p>"
            f"<p>event: {html.escape(str(event))}</p>"
            f"<pre>{html.escape(traceback.format_exc())}</pre>"
        )
        send_email("🚨 daily-batchが失敗しました", body)
    except Exception as notify_exc:
        print(f"[ERROR] 失敗通知メールの送信にも失敗しました: {notify_exc}")


def handler(event, context):
    event = event or {}
    try:
        return _dispatch(event)
    except Exception as exc:
        print(f"[ERROR] handler failed: {exc}")
        _notify_failure(event, exc)
        raise
