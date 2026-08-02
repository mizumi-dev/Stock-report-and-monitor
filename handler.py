"""
daily-batch Lambda ハンドラ
毎日1回、EventBridge Schedulerから起動される想定
"""
import os
import boto3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config import (
    DEFAULT_PERIOD_MODE,
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
    tickers = get_nasdaq100_tickers(fallback=None)
    stocks, signals_map = [], {}

    for t in tickers:
        d = get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"])
        if d.get("history") is None:
            continue
        stocks.append(d)
        signals_map[t] = calc_all_signals(d["history"], mode)

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


def handler(event, context):
    event = event or {}

    # 画面の「監視レポートを送る」ボタンから来た場合は監視のみ実行する
    if event.get("monitoring_only"):
        run_monitoring_report()
        return {"statusCode": 200, "body": "OK"}

    # 画面の「選定レポートを送る」ボタン、またはテスト用に選定のみ実行する
    if event.get("force_weekly"):
        run_selection_report()
        return {"statusCode": 200, "body": "OK"}

    # EventBridge Schedulerからの定期実行
    # 選定レポートの送信頻度はモードで変える: 短期=毎日、中期=毎週指定曜日、
    # 長期=月初(1〜7日)の指定曜日のみ(月1回)
    weekday = str(datetime.now(timezone.utc).weekday())
    day_of_month = datetime.now(timezone.utc).day
    mode = get_period_mode()

    is_report_weekday = weekday == IS_WEEKLY_REPORT_DAY
    should_send_selection = (
        mode == "SHORT"
        or (mode == "MID" and is_report_weekday)
        or (mode == "LONG" and is_report_weekday and day_of_month <= 7)
    )
    if should_send_selection:
        run_selection_report()
    run_monitoring_report()
    return {"statusCode": 200, "body": "OK"}
