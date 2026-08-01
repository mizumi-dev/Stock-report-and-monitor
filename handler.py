"""
daily-batch Lambda ハンドラ
毎日1回、EventBridge Schedulerから起動される想定
"""
import os
import boto3
from datetime import datetime, timedelta, timezone

from data_sources import get_nasdaq100_tickers, get_stock_data
from signals import calc_all_signals
from ranking import rank_stocks
from report import build_selection_report, build_monitoring_report

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
SIGNAL_HISTORY_TABLE = os.environ.get("SIGNAL_HISTORY_TABLE", "signal_history")
SES_SENDER = os.environ["SES_SENDER"]
SES_RECIPIENT = os.environ["SES_RECIPIENT"]
REGISTER_URL = os.environ["REGISTER_FUNCTION_URL"]
IS_WEEKLY_REPORT_DAY = os.environ.get("WEEKLY_REPORT_WEEKDAY", "0")  # 0=月曜

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses")


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def get_watchlist() -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.scan()
    return [item["ticker"] for item in resp.get("Items", [])]


def get_prev_signals(ticker: str) -> dict | None:
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    resp = table.get_item(Key={"ticker": ticker, "date": _yesterday_str()})
    return resp.get("Item", {}).get("signals")


def save_signals(ticker: str, signals: dict):
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    table.put_item(Item={"ticker": ticker, "date": _today_str(), "signals": signals})


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
    """週次: NASDAQ100全銘柄をスキャンしてランキング上位3件のレポートを送る"""
    tickers = get_nasdaq100_tickers(fallback=None)
    stocks, signals_map = [], {}

    for t in tickers:
        d = get_stock_data(t, period="6mo")
        if d.get("history") is None:
            continue
        stocks.append(d)
        signals_map[t] = calc_all_signals(d["history"])

    ranked = rank_stocks(stocks)
    html = build_selection_report(ranked, signals_map, REGISTER_URL)
    send_email("📈 今週の買い時レポート", html)


def run_monitoring_report():
    """日次: 登録銘柄のみ監視してレポートを送る"""
    watchlist = get_watchlist()
    if not watchlist:
        print("[INFO] 監視リストが空のため監視レポートはスキップ")
        return

    results = []
    for t in watchlist:
        d = get_stock_data(t, period="6mo")
        if d.get("history") is None:
            continue
        sig = calc_all_signals(d["history"])
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

    html = build_monitoring_report(results)
    send_email("📊 本日の監視レポート", html)


def handler(event, context):
    weekday = str(datetime.now(timezone.utc).weekday())
    if weekday == IS_WEEKLY_REPORT_DAY:
        run_selection_report()
    run_monitoring_report()
    return {"statusCode": 200, "body": "OK"}
