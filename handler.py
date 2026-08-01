"""
daily-batch Lambda ハンドラ
毎日1回、EventBridge Schedulerから起動される想定。
Function URL(認証なし、招待トークン必須)からも呼べて、ボタンクリックで
選定/監視レポートを即時送信できる。
"""
import html
import os
import boto3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data_sources import get_nasdaq100_tickers, get_stock_data
from signals import calc_all_signals
from ranking import rank_stocks
from report import build_selection_report, build_monitoring_report

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
SIGNAL_HISTORY_TABLE = os.environ.get("SIGNAL_HISTORY_TABLE", "signal_history")
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
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
    """週次: NASDAQ100全銘柄をスキャンしてランキング上位3件のレポートを送る"""
    tickers = get_nasdaq100_tickers(fallback=None)
    stocks, signals_map = [], {}

    for t in tickers:
        d = get_stock_data(t, period="6mo")
        if d.get("history") is None:
            continue
        stocks.append(d)
        signals_map[t] = calc_all_signals(d["history"])

    ranked = rank_stocks(stocks, top_n=TOP_N)
    body = build_selection_report(ranked, signals_map, REGISTER_URL, REGISTER_TOKEN)
    send_email("📈 今週の買い時レポート", body)


def run_monitoring_report() -> bool:
    """日次: 登録銘柄のみ監視してレポートを送る。送信したらTrueを返す"""
    watchlist = get_watchlist()
    if not watchlist:
        print("[INFO] 監視リストが空のため監視レポートはスキップ")
        return False

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

    body = build_monitoring_report(results)
    send_email("📊 本日の監視レポート", body)
    return True


def _is_web_trigger(event: dict) -> bool:
    """Function URL経由の呼び出しか(EventBridge Schedulerからの定期実行と区別する)"""
    return bool(event.get("requestContext", {}).get("http"))


def _is_valid_token(token: str) -> bool:
    if not token:
        return False
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.get_item(Key={"token": token})
    return "Item" in resp


def _http_response(body: str, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def _page(inner_html: str, status=200):
    return _http_response(
        f"""<html><body style="font-family:sans-serif;max-width:400px;margin:40px auto;">
        {inner_html}
        </body></html>""",
        status=status,
    )


def _back_link(token: str) -> str:
    return f'<p><a href="?token={html.escape(token)}">戻る</a></p>'


def _render_report_buttons(token: str) -> str:
    t = html.escape(token)
    return f"""
    <h3>レポートを今すぐ送信</h3>
    <p><a href="?token={t}&report=selection">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📈 選定レポートを送る</button>
    </a></p>
    <p><a href="?token={t}&report=monitoring">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📊 監視レポートを送る</button>
    </a></p>
    <p style="color:#888;font-size:12px;">送信には数十秒〜数分かかる場合があります。</p>
    """


def _handle_web_trigger(event: dict):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()

    if not _is_valid_token(token):
        return _page("<p>アクセス権がありません。招待リンクを確認してください。</p>", status=403)

    report = (params.get("report") or "").strip().lower()

    if report == "selection":
        try:
            run_selection_report()
        except Exception as e:
            return _page(f"<p>選定レポートの送信に失敗しました: {html.escape(str(e))}</p>{_back_link(token)}", status=500)
        return _page(f"<p>選定レポートを送信しました。</p>{_back_link(token)}")

    if report == "monitoring":
        try:
            sent = run_monitoring_report()
        except Exception as e:
            return _page(f"<p>監視レポートの送信に失敗しました: {html.escape(str(e))}</p>{_back_link(token)}", status=500)
        msg = "監視レポートを送信しました。" if sent else "監視リストが空のため送信していません。"
        return _page(f"<p>{msg}</p>{_back_link(token)}")

    return _page(_render_report_buttons(token))


def handler(event, context):
    event = event or {}

    if _is_web_trigger(event):
        return _handle_web_trigger(event)

    weekday = str(datetime.now(timezone.utc).weekday())
    # event={"force_weekly": true} でスケジュールを待たずに選別レポートをテスト実行できる
    if event.get("force_weekly") or weekday == IS_WEEKLY_REPORT_DAY:
        run_selection_report()
    run_monitoring_report()
    return {"statusCode": 200, "body": "OK"}
