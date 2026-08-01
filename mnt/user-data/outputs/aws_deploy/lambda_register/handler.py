"""
register-stock Lambda ハンドラ
Function URL(認証なし)で直接呼ばれる想定。
メール内の登録ボタン、または検索ボックスからGETリクエストで呼ばれる。
例: https://xxxx.lambda-url.us-east-1.on.aws/?ticker=NVDA
"""
import os
import re
import boto3
from datetime import datetime, timezone

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
dynamodb = boto3.resource("dynamodb")

# ASCII英大文字・数字のみ1〜6文字。isalnum()は全角文字等も通すため正規表現で制限する
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")

SEARCH_FORM_HTML = """
<html><body style="font-family:sans-serif;max-width:400px;margin:40px auto;">
  <h3>銘柄を監視リストに登録</h3>
  <form method="GET">
    <input name="ticker" placeholder="例: NVDA" style="padding:8px;font-size:16px;">
    <button type="submit" style="padding:8px 16px;">登録</button>
  </form>
</body></html>
"""


def _response(body: str, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    ticker = (params.get("ticker") or "").strip().upper()

    if not ticker:
        return _response(SEARCH_FORM_HTML)

    if not TICKER_RE.match(ticker):
        return _response("<p>無効なティッカーです。</p>", status=400)

    table = dynamodb.Table(WATCHLIST_TABLE)
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
        return _response(f"<p>{ticker} はすでに監視リストに登録済みです。</p><p><a href='./'>他の銘柄を登録する</a></p>")

    return _response(f"<p>{ticker} を監視リストに登録しました。</p><p><a href='./'>他の銘柄も登録する</a></p>")
