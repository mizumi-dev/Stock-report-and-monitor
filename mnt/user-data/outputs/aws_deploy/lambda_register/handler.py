"""
register-stock Lambda ハンドラ
Function URL(認証なし)で直接呼ばれる想定。招待トークン(?token=)を持つ人のみ操作可能。

この1つのURLに、銘柄の登録/削除・期間モードの切替・レポートの即時送信・
仕組みの説明ページをすべて集約している(画面が分かれていると迷うため)。

例: https://xxxx.lambda-url.us-east-1.on.aws/?token=xxxx&ticker=NVDA
"""
import html
import json
import os
import re
import boto3
from datetime import datetime, timezone

from config import (
    MAX_PEG,
    MIN_GROWTH,
    PERIOD_MODE_KEY,
    PERIOD_PRESETS,
    SETTINGS_TABLE_DEFAULT,
    get_preset,
    normalize_mode,
)

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
MAX_WATCHLIST_SIZE = int(os.environ.get("MAX_WATCHLIST_SIZE", "5"))
DAILY_BATCH_FUNCTION = os.environ.get("DAILY_BATCH_FUNCTION", "daily-batch")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

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
        f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="font-family:sans-serif;max-width:420px;margin:40px auto;padding:0 12px;">
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


def get_period_mode() -> str:
    """設定が読めなくても画面は出したいので、失敗時は既定モード扱いにする"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": PERIOD_MODE_KEY})
        return normalize_mode(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] 期間モード取得に失敗: {e}")
        return normalize_mode(None)


def set_period_mode(mode: str) -> str:
    mode = normalize_mode(mode)
    table = dynamodb.Table(SETTINGS_TABLE)
    table.put_item(Item={"key": PERIOD_MODE_KEY, "value": mode,
                         "updated_at": datetime.now(timezone.utc).isoformat()})
    return mode


def _trigger_report(report: str):
    """daily-batchを非同期(Event)で起動する。
    レポート生成は40秒前後かかるので、同期で待つとブラウザが固まる。
    起動だけして即座に画面を返し、結果はメールで受け取ってもらう。"""
    payload = {"force_weekly": True} if report == "selection" else {"monitoring_only": True}
    lambda_client.invoke(
        FunctionName=DAILY_BATCH_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _render_form(token: str, notice: str = "") -> str:
    t = html.escape(token)
    watchlist = _current_watchlist()
    mode = get_period_mode()

    if watchlist:
        rows = "".join(
            f'<li>{html.escape(x)} '
            f'<a href="?token={t}&ticker={html.escape(x)}&action=delete">[削除]</a></li>'
            for x in watchlist
        )
        list_html = f"<ul>{rows}</ul>"
    else:
        list_html = "<p>(監視リストは空です)</p>"

    options = "".join(
        f'<option value="{k}"{" selected" if k == mode else ""}>{p["label"]}</option>'
        for k, p in PERIOD_PRESETS.items()
    )
    preset = get_preset(mode)

    notice_html = f'<p style="background:#eef;padding:8px;border-radius:4px;">{notice}</p>' if notice else ""

    return f"""
    {notice_html}
    <h3>銘柄を監視リストに登録</h3>
    <form method="GET">
      <input type="hidden" name="token" value="{t}">
      <input name="ticker" placeholder="例: NVDA" style="padding:8px;font-size:16px;">
      <button type="submit" style="padding:8px 16px;">登録</button>
    </form>
    <p>現在の監視リスト（上限{MAX_WATCHLIST_SIZE}件）</p>
    {list_html}

    <hr style="margin:24px 0;">
    <h3>判定期間</h3>
    <form method="GET">
      <input type="hidden" name="token" value="{t}">
      <select name="mode" style="padding:8px;font-size:16px;">{options}</select>
      <button type="submit" style="padding:8px 16px;">変更</button>
    </form>
    <p style="color:#888;font-size:12px;">現在: <strong>{preset['label']}</strong> — {preset['description']}</p>

    <hr style="margin:24px 0;">
    <h3>レポートを今すぐ送信</h3>
    <p><a href="?token={t}&report=selection">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📈 選定レポートを送る</button></a></p>
    <p><a href="?token={t}&report=monitoring">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📊 監視レポートを送る</button></a></p>
    <p style="color:#888;font-size:12px;">
      送信を受け付けたらすぐこの画面に戻ります。メール到着まで1分ほどかかります。</p>

    <hr style="margin:24px 0;">
    <p><a href="?token={t}&info=1">この仕組みについて（判定ロジック・データ元）</a></p>
    """


def _render_info(token: str) -> str:
    """説明ページ。数値は config.py の実際の設定から生成するので実装とズレない"""
    t = html.escape(token)
    mode = get_period_mode()

    rows = []
    for key, p in PERIOD_PRESETS.items():
        mark = " ←現在" if key == mode else ""
        filt = f" / {p['ma_filter']}日線で絞り込み" if p["ma_filter"] else ""
        rows.append(
            f"<tr><td style='padding:4px 8px;'><strong>{p['label']}{mark}</strong></td>"
            f"<td style='padding:4px 8px;'>{p['ma_short']}日線 × {p['ma_long']}日線{filt}</td>"
            f"<td style='padding:4px 8px;'>RSI{p['rsi_period']}<br>"
            f"{p['rsi_recovery']}以下から上昇で買い / {p['rsi_overbought']}以上で警告</td></tr>"
        )
    table = (
        "<table style='border-collapse:collapse;font-size:13px;'>"
        "<tr><th style='padding:4px 8px;'>モード</th><th style='padding:4px 8px;'>トレンド</th>"
        "<th style='padding:4px 8px;'>モメンタム</th></tr>" + "".join(rows) + "</table>"
    )

    return f"""
    <h3>このサービスの仕組み</h3>

    <h4>📈 選定レポート（週次）</h4>
    <p>対象: NASDAQ100構成銘柄（NASDAQ公式APIから取得）</p>
    <p>次の条件を両方満たす銘柄だけを候補にします:</p>
    <ul>
      <li>利益成長率が {MIN_GROWTH * 100:.0f}% 以上</li>
      <li>PEGレシオが 0 より大きく {MAX_PEG} 以下</li>
    </ul>
    <p>通過した銘柄をPEGの小さい順（同値なら時価総額の大きい順）に並べ、上位を送ります。</p>
    <p>PEG・利益成長率・時価総額・株価は <strong>Yahoo Finance（yfinanceライブラリ）</strong> から取得しています。
    PEGは「株価収益率(PER) ÷ 利益成長率」で、低いほど成長性に対して割安とされる指標です。
    Yahoo Financeが算出した値をそのまま使っており、当サービスで再計算はしていません。</p>

    <h4>📊 監視レポート（日次）</h4>
    <p>登録した銘柄について、3種類のシグナルを判定します。</p>

    <p><strong>トレンド</strong>は次の2つのどちらかが成立すると点灯します:</p>
    <ul>
      <li><strong>クロス</strong>: 短い方の移動平均線が長い方を「その日に」上抜けた（買い）／下抜けた（売り）。
      交差した瞬間だけ点灯します。</li>
      <li><strong>並び順</strong>: 株価・各移動平均線が上から順にきれいに並んでいる状態。
      トレンドが続く限り毎日点灯します。</li>
    </ul>

    <p><strong>モメンタム</strong>はRSIで判定します。買いは「一定水準まで下げた後、上昇に転じた」ことを見ます
    （単純な売られすぎ判定より反転の兆しを捉える意図です）。</p>

    <p><strong>出来高</strong>は過去N日平均の{PERIOD_PRESETS[mode]['volume_multiplier']}倍を超えたら「急増」と表示します
    （{PERIOD_PRESETS[mode]['volume_lookback']}日平均・{PERIOD_PRESETS[mode]['label']}モードの場合）。
    買い／売りの方向は持たない参考指標です。</p>

    <h4>期間モードごとの設定値</h4>
    {table}
    <p style="color:#888;font-size:12px;">
      短期は反応が早いぶん騙しも多く、長期は点灯頻度が年に数回程度まで下がります。
      シグナルが出ない日が続くのは長期モードでは正常な状態です。</p>

    <p style="color:#888;font-size:12px;">
      🟢/⚪のランプは上記シグナルの点灯有無を表示しているだけで、複数条件を合成したスコアではありません。</p>

    <p style="color:#888;font-size:12px;">
      ※本サービスは投資助言ではありません。判定はYahoo Financeが提供する値に依存しており、
      その正確性・遅延について当サービスは保証しません。</p>

    <p><a href="?token={t}">戻る</a></p>
    """


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()

    if not _is_valid_token(token):
        return _page("<p>アクセス権がありません。招待リンクを確認してください。</p>", status=403)

    if params.get("info"):
        return _page(_render_info(token))

    # 期間モードの変更
    if params.get("mode"):
        mode = set_period_mode(params["mode"])
        label = get_preset(mode)["label"]
        return _page(_render_form(token, notice=f"判定期間を「{label}」に変更しました。次回のレポートから反映されます。"))

    # レポートの即時送信(非同期起動)
    report = (params.get("report") or "").strip().lower()
    if report in ("selection", "monitoring"):
        label = "選定" if report == "selection" else "監視"
        try:
            _trigger_report(report)
        except Exception as e:
            return _page(f"<p>{label}レポートの送信に失敗しました: {html.escape(str(e))}</p>{_back_link(token)}", status=500)
        return _page(_render_form(token, notice=f"{label}レポートの送信を受け付けました。まもなくメールが届きます。"))

    ticker = (params.get("ticker") or "").strip().upper()
    action = (params.get("action") or "register").strip().lower()

    if not ticker:
        return _page(_render_form(token))

    if not TICKER_RE.match(ticker):
        return _page(f"<p>無効なティッカーです。</p>{_back_link(token)}", status=400)

    table = dynamodb.Table(WATCHLIST_TABLE)

    if action == "delete":
        table.delete_item(Key={"ticker": ticker})
        return _page(_render_form(token, notice=f"{ticker} を監視リストから削除しました。"))

    watchlist = _current_watchlist()
    if ticker not in watchlist and len(watchlist) >= MAX_WATCHLIST_SIZE:
        return _page(
            _render_form(token, notice=f"監視リストは上限{MAX_WATCHLIST_SIZE}件です。"
                                       f"他の銘柄を削除してから登録してください。"),
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
        return _page(_render_form(token, notice=f"{ticker} はすでに監視リストに登録済みです。"))

    return _page(_render_form(token, notice=f"{ticker} を監視リストに登録しました。"))
