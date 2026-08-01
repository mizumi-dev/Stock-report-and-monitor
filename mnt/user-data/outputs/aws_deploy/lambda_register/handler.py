"""
register-stock Lambda ハンドラ
Function URL(認証なし)で直接呼ばれる想定。招待トークン(?token=)を持つ人のみ操作可能。
メール内の登録ボタン、または検索ボックスからGETリクエストで呼ばれる。
例: https://xxxx.lambda-url.us-east-1.on.aws/?token=xxxx&ticker=NVDA
"""
import html
import os
import re
import boto3
from datetime import datetime, timezone

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
MAX_WATCHLIST_SIZE = int(os.environ.get("MAX_WATCHLIST_SIZE", "5"))
REPORT_URL = os.environ.get("REPORT_URL", "")  # daily-batchのFunction URL(レポート送信・仕組み説明ページ)

dynamodb = boto3.resource("dynamodb")

# ASCII英大文字・数字のみ1〜6文字。isalnum()は全角文字等も通すため正規表現で制限する
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def _response(body: str, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def _page(inner_html: str, status=200):
    return _response(
        f"""<html><body style="font-family:sans-serif;max-width:400px;margin:40px auto;">
        {inner_html}
        </body></html>""",
        status=status,
    )


def _back_link(token: str) -> str:
    return f'<p><a href="?token={html.escape(token)}">戻る</a></p>'


def _is_valid_token(token: str) -> bool:
    if not token:
        return False
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.get_item(Key={"token": token})
    return "Item" in resp


def _current_watchlist() -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.scan()
    return sorted(item["ticker"] for item in resp.get("Items", []))


def _render_form(token: str) -> str:
    watchlist = _current_watchlist()
    if watchlist:
        rows = "".join(
            f'<li>{html.escape(t)} '
            f'<a href="?token={html.escape(token)}&ticker={html.escape(t)}&action=delete">[削除]</a></li>'
            for t in watchlist
        )
        list_html = f"<ul>{rows}</ul>"
    else:
        list_html = "<p>(監視リストは空です)</p>"

    info_link = ""
    if REPORT_URL:
        info_link = f'<p><a href="{REPORT_URL}?token={html.escape(token)}&info=1">この仕組みについて(判定ロジック・データ元)</a></p>'

    return f"""
    <h3>銘柄を監視リストに登録</h3>
    <form method="GET">
      <input type="hidden" name="token" value="{html.escape(token)}">
      <input name="ticker" placeholder="例: NVDA" style="padding:8px;font-size:16px;">
      <button type="submit" style="padding:8px 16px;">登録</button>
    </form>
    <p>現在の監視リスト（上限{MAX_WATCHLIST_SIZE}件）</p>
    {list_html}
    {info_link}
    """


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()

    if not _is_valid_token(token):
        return _page("<p>アクセス権がありません。招待リンクを確認してください。</p>", status=403)

    ticker = (params.get("ticker") or "").strip().upper()
    action = (params.get("action") or "register").strip().lower()

    if not ticker:
        return _page(_render_form(token))

    if not TICKER_RE.match(ticker):
        return _page(f"<p>無効なティッカーです。</p>{_back_link(token)}", status=400)

    table = dynamodb.Table(WATCHLIST_TABLE)

    if action == "delete":
        table.delete_item(Key={"ticker": ticker})
        return _page(f"<p>{ticker} を監視リストから削除しました。</p>{_back_link(token)}")

    watchlist = _current_watchlist()
    if ticker not in watchlist and len(watchlist) >= MAX_WATCHLIST_SIZE:
        return _page(
            f"<p>監視リストは上限{MAX_WATCHLIST_SIZE}件です。"
            f"他の銘柄を削除してから登録してください。</p>{_back_link(token)}",
            status=400,
        )

    try:
        table.put_item(
            Item={
                "ticker": ticker,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "source": "email_or_search",
            },
            ConditionExpression="attribute_not_exists(ticker)",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _page(f"<p>{ticker} はすでに監視リストに登録済みです。</p>{_back_link(token)}")

    return _page(f"<p>{ticker} を監視リストに登録しました。</p>{_back_link(token)}")
