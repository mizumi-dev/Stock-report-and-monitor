"""
register-stock Lambda ハンドラ
Function URL(認証なし)で直接呼ばれる想定。招待トークン(?token=)を持つ人のみ操作可能。

この1つのURLに、申込み(ダブルオプトイン)・銘柄の登録/削除・期間モードの切替・
レポートの即時送信・仕組みの説明ページをすべて集約している(画面が分かれていると迷うため)。

マルチテナント方針: 利用者の識別は招待トークンそのものを使う(別途user_idは発行しない)。
watchlistは(owner_token, ticker)の複合キー、期間モードはapp_settingsに
"{token}#period_mode"というキーで保存し、利用者ごとに分離する。

例: https://xxxx.lambda-url.us-east-1.on.aws/?token=xxxx&ticker=NVDA
"""
import base64
import html
import json
import os
import random
import re
import secrets
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone

from config import (
    MAX_PEG,
    MIN_GROWTH,
    PERIOD_PRESETS,
    SETTINGS_TABLE_DEFAULT,
    TOKEN_STATUS_APPROVED,
    TOKEN_STATUS_PENDING,
    get_preset,
    normalize_mode,
    user_period_mode_key,
)

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
MAX_WATCHLIST_SIZE = int(os.environ.get("MAX_WATCHLIST_SIZE", "5"))
DAILY_BATCH_FUNCTION = os.environ.get("DAILY_BATCH_FUNCTION", "daily-batch")
SES_SENDER = os.environ.get("SES_SENDER", "")
REGISTER_FUNCTION_URL = os.environ.get("REGISTER_FUNCTION_URL", "")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")
ses = boto3.client("ses")

# ASCII英大文字・数字のみ1〜6文字。isalnum()は全角文字等も通すため正規表現で制限する
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _response(body: str, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


# PWA用アイコン(SVG)。外部ファイルを持たず、Lambda単体で完結させるためdata URIで埋め込む
_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
    '<rect width="192" height="192" rx="32" fill="#0f172a"/>'
    '<polyline points="32,140 76,96 108,124 160,56" fill="none" stroke="#22c55e" '
    'stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="160" cy="56" r="12" fill="#22c55e"/></svg>'
)
_ICON_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_ICON_SVG.encode()).decode()


_SITE_TITLE = "PegSignal（株式レポートサービス） | NASDAQ100 成長株シグナル配信"
_SITE_DESCRIPTION = (
    "PEGレシオで割安な成長株をNASDAQ100構成銘柄から選定し、"
    "トレンド・モメンタム・出来高のシグナルを毎日メールで通知する無料サービスです。"
)


def _page(inner_html: str, status=200):
    return _response(
        f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
        <meta charset="utf-8">
        <title>{_SITE_TITLE}</title>
        <meta name="description" content="{_SITE_DESCRIPTION}">
        <meta name="theme-color" content="#0f172a">
        <link rel="manifest" href="?manifest=1">
        <link rel="apple-touch-icon" href="{_ICON_DATA_URI}">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-title" content="株式レポート"></head>
        <body style="font-family:sans-serif;max-width:420px;margin:40px auto;padding:0 12px;">
        {inner_html}
        </body></html>""",
        status=status,
    )


def _handle_manifest() -> dict:
    """Web App Manifest。start_urlは意図的に省略し、
    インストール元ページ(招待トークン付きURL)がそのまま起動先になるようにする"""
    manifest = {
        "name": "株式レポートサービス",
        "short_name": "株式レポート",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {"src": _ICON_DATA_URI, "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/manifest+json; charset=utf-8"},
        "body": json.dumps(manifest, ensure_ascii=False),
    }


def _back_link(token: str) -> str:
    return f'<p><a href="?token={html.escape(token)}">戻る</a></p>'


def _get_token_item(token: str) -> dict | None:
    if not token:
        return None
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.get_item(Key={"token": token})
    return resp.get("Item")


def _is_valid_token(token: str) -> bool:
    item = _get_token_item(token)
    return bool(item) and item.get("status") == TOKEN_STATUS_APPROVED


def _find_token_by_email(email: str) -> dict | None:
    """既に申込み済みかを確認する(招待制ではなくなったため件数が読めず、
    Scanで十分な小規模を前提とする)。"""
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.scan()
    for item in resp.get("Items", []):
        if item.get("email") == email:
            return item
    return None


def _send_confirmation_email(email: str, token: str):
    link = f"{REGISTER_FUNCTION_URL}?token={token}&confirm=1"
    body = f"""
    <p>お申込みありがとうございます。以下のリンクをクリックすると登録が完了します。</p>
    <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
    <p>心当たりがない場合はこのメールを破棄してください。</p>
    """
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "【登録確認】株式レポートサービスのお申込み", "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _current_watchlist(owner_token: str) -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.query(KeyConditionExpression=Key("owner_token").eq(owner_token))
    return sorted(item["ticker"] for item in resp.get("Items", []))


def get_period_mode(owner_token: str) -> str:
    """設定が読めなくても画面は出したいので、失敗時は既定モード扱いにする"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": user_period_mode_key(owner_token)})
        return normalize_mode(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] 期間モード取得に失敗: {e}")
        return normalize_mode(None)


def set_period_mode(owner_token: str, mode: str) -> str:
    mode = normalize_mode(mode)
    table = dynamodb.Table(SETTINGS_TABLE)
    table.put_item(Item={"key": user_period_mode_key(owner_token), "value": mode,
                         "updated_at": datetime.now(timezone.utc).isoformat()})
    return mode


def _trigger_report(report: str, owner_token: str):
    """daily-batchを非同期(Event)で起動する。自分の分だけを送るようowner_tokenを渡す。
    レポート生成は時間がかかるので、同期で待つとブラウザが固まる。
    起動だけして即座に画面を返し、結果はメールで受け取ってもらう。"""
    payload = {"owner_token": owner_token}
    payload["force_weekly" if report == "selection" else "monitoring_only"] = True
    lambda_client.invoke(
        FunctionName=DAILY_BATCH_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _render_signup_form(notice: str = "") -> str:
    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    notice_html = f'<p style="background:#eef;padding:8px;border-radius:4px;">{notice}</p>' if notice else ""
    return f"""
    {notice_html}
    <h1 style="font-size:1.3em;">PegSignal（株式レポートサービス）</h1>
    <p>NASDAQ100構成銘柄の中から、<strong>PEGレシオ</strong>（株価収益率÷利益成長率）が
    低く割安な成長株を選定し、無料でメール配信します。登録した銘柄については、
    トレンド・モメンタム・出来高の3種類のシグナルを毎日監視して通知します。</p>
    <p style="color:#888;font-size:12px;">
    ※投資助言ではありません。判定はYahoo Financeが提供するデータに基づいており、
    正確性・遅延について当サービスは保証しません。詳しくは
    <a href="?info=1">この仕組みについて</a>をご覧ください。</p>

    <h3>お申込み</h3>
    <p>メールアドレスを入力すると、確認メールが届きます。リンクをクリックすると登録が完了します。</p>
    <form method="GET">
      <input name="signup_email" type="email" placeholder="example@example.com"
             style="padding:8px;font-size:16px;width:100%;box-sizing:border-box;" required>
      <p>簡単な確認: {n1} + {n2} = <input name="captcha_answer" style="width:60px;padding:4px;" required></p>
      <input type="hidden" name="captcha_n1" value="{n1}">
      <input type="hidden" name="captcha_n2" value="{n2}">
      <button type="submit" style="padding:8px 16px;">申し込む</button>
    </form>
    <p style="color:#888;font-size:12px;">
      お申込みは <a href="?terms=1">利用規約</a>・<a href="?privacy=1">プライバシーポリシー</a>
      への同意とみなされます。本サービスは日本・米国在住の方向けです。
      <strong>欧州経済領域(EEA)・英国・スイス在住の方はお申込みをご遠慮ください。</strong></p>
    <p style="color:#888;font-size:12px;">既に招待リンクをお持ちの場合は、そのリンクからアクセスしてください。</p>
    """


def _render_privacy() -> str:
    return """
    <h3>プライバシーポリシー</h3>
    <p>収集する情報は<strong>メールアドレス・登録銘柄・判定期間モードの選択のみ</strong>です。
    クレジットカード等の決済情報、氏名・住所、IPアドレスのログ、Cookieによる行動追跡は
    一切行っていません。第三者への個人情報の提供・販売もありません。</p>
    <p>将来、Webページ上に非パーソナライズ広告またはアフィリエイトリンクを掲載する
    可能性がありますが、その場合も個人を特定・追跡する形での広告配信は行いません。</p>
    <p>本サービスは日本国内・米国在住の方を対象としています。
    <strong>欧州経済領域(EEA)・英国・スイス在住の方は対象外です。</strong></p>
    <p>登録情報の削除・開示をご希望の場合は <code>mohatose4929@gmail.com</code> までご連絡ください。</p>
    <p><a href="?">戻る</a></p>
    """


def _render_terms() -> str:
    return """
    <h3>利用規約</h3>
    <p><strong>本サービスが配信するレポート・シグナルは投資助言ではありません。</strong>
    情報はYahoo Finance等の外部データに基づいており、正確性・完全性を保証しません。
    投資判断はご自身の責任で行ってください。</p>
    <p>本サービスは日本国内・米国在住の方を対象とする無料サービスです。
    <strong>欧州経済領域(EEA)・英国・スイス在住の方は対象外です。</strong></p>
    <p>招待トークン(アクセスリンク)はご自身で管理してください。不正利用・過度な自動アクセス等が
    判明した場合、事前の通知なくアカウントを停止することがあります。</p>
    <p>本サービスは運営者の判断により、事前の通知なく機能変更・停止を行うことがあります。</p>
    <p>詳細は <a href="?privacy=1">プライバシーポリシー</a> も参照してください。
    お問い合わせ: <code>mohatose4929@gmail.com</code></p>
    <p><a href="?">戻る</a></p>
    """


def _render_form(token: str, notice: str = "") -> str:
    t = html.escape(token)
    watchlist = _current_watchlist(token)
    mode = get_period_mode(token)

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
    <p style="color:#888;font-size:12px;">
      <a href="?terms=1">利用規約</a> ・ <a href="?privacy=1">プライバシーポリシー</a></p>
    """


_CYCLE_LABEL = {
    "day": "当日の値",
    "week": "前の暦週の取引日平均",
    "month": "前の暦月の取引日平均",
}
_SEND_FREQ_LABEL = {
    "SHORT": "毎日（平日）",
    "MID": "毎週月曜",
    "LONG": "月初の月曜（月1回）",
}


def _render_info(token: str) -> str:
    """説明ページ。数値は config.py の実際の設定から生成するので実装とズレない"""
    t = html.escape(token)
    mode = get_period_mode(token)

    rows = []
    for key, p in PERIOD_PRESETS.items():
        mark = " ←現在" if key == mode else ""
        filt = f" / {p['ma_filter']}日線で絞り込み" if p["ma_filter"] else ""
        rows.append(
            f"<tr><td style='padding:4px 8px;'><strong>{p['label']}{mark}</strong></td>"
            f"<td style='padding:4px 8px;'>{p['ma_short']}日線 × {p['ma_long']}日線{filt}</td>"
            f"<td style='padding:4px 8px;'>RSI{p['rsi_period']}<br>"
            f"{p['rsi_oversold']}以下で買い / {p['rsi_overbought']}以上で売り<br>"
            f"({_CYCLE_LABEL[p['cycle_unit']]}で判定)</td>"
            f"<td style='padding:4px 8px;'>{_SEND_FREQ_LABEL[key]}</td></tr>"
        )
    table = (
        "<table style='border-collapse:collapse;font-size:13px;'>"
        "<tr><th style='padding:4px 8px;'>モード</th><th style='padding:4px 8px;'>トレンド</th>"
        "<th style='padding:4px 8px;'>モメンタム</th><th style='padding:4px 8px;'>選定レポート頻度</th></tr>"
        + "".join(rows) + "</table>"
    )

    return f"""
    <h3>このサービスの仕組み</h3>

    <h4>📈 選定レポート</h4>
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
    <p>発行頻度はモードによって異なります（短期=毎日、中期=毎週月曜、長期=月初の月曜で月1回）。
    詳しくは下表を参照してください。</p>

    <h4>📊 監視レポート（日次）</h4>
    <p>登録した銘柄について、3種類のシグナルを判定します。監視レポートは全モード共通で毎日送ります。</p>

    <p><strong>トレンド</strong>は次の2つのどちらかが成立すると点灯します:</p>
    <ul>
      <li><strong>クロス</strong>: 短い方の移動平均線が長い方を「その日に」上抜けた（買い）／下抜けた（売り）。
      交差した瞬間だけ点灯します。</li>
      <li><strong>並び順</strong>: 株価・各移動平均線が上から順にきれいに並んでいる状態。
      トレンドが続く限り毎日点灯します。</li>
    </ul>
    <p>トレンドは全モード共通で日足データを使って判定します。</p>

    <p><strong>モメンタム</strong>はRSIで判定します。判定サイクル分（短期=当日、中期=前の週、長期=前の月の
    実際の取引日）のRSI平均が、売られすぎの水準以下なら買い、買われすぎの水準以上なら売りです
    （閾値を跨いだ時点で対称に判定します）。中期・長期では、休場日があった週・月はその分を除いた
    日数で平均します。</p>

    <p><strong>出来高</strong>は判定サイクル分の平均出来高が、基準値の
    {PERIOD_PRESETS[mode]['volume_multiplier']}倍を超えたら「急増」と表示します
    （{PERIOD_PRESETS[mode]['volume_lookback']}日平均・{PERIOD_PRESETS[mode]['label']}モードの場合）。
    中期・長期の基準値は、判定期間そのものは含めず、その直前の期間だけで計算します。
    買い／売りの方向は持たない参考指標です。</p>

    <h4>期間モードごとの設定値</h4>
    {table}
    <p style="color:#888;font-size:12px;">
      短期は反応が早いぶん騙しも多く、長期は点灯頻度が下がります。
      シグナルが出ない期間が続くのは長期モードでは正常な状態です。</p>

    <p style="color:#888;font-size:12px;">
      🟢/⚪のランプは上記シグナルの点灯有無を表示しているだけで、複数条件を合成したスコアではありません。</p>

    <p style="color:#888;font-size:12px;">
      ※本サービスは投資助言ではありません。判定はYahoo Financeが提供する値に依存しており、
      その正確性・遅延について当サービスは保証しません。</p>

    <p><a href="?token={t}">戻る</a></p>
    """


def _handle_signup(params: dict) -> dict:
    email = (params.get("signup_email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return _page(f"<p>メールアドレスの形式が正しくありません。</p>{_render_signup_form()}", status=400)

    try:
        n1, n2 = int(params.get("captcha_n1", "")), int(params.get("captcha_n2", ""))
        answer = int(params.get("captcha_answer", ""))
    except (TypeError, ValueError):
        return _page(f"<p>確認の回答が正しくありません。</p>{_render_signup_form()}", status=400)
    if answer != n1 + n2:
        return _page(f"<p>確認の回答が正しくありません。</p>{_render_signup_form()}", status=400)

    existing = _find_token_by_email(email)
    if existing and existing.get("status") == TOKEN_STATUS_APPROVED:
        _send_confirmation_email_reminder(email, existing["token"])
        return _page("<p>このメールアドレスは既に登録済みです。アクセス用リンクを再送しました。</p>")

    token = existing["token"] if existing else secrets.token_urlsafe(24)
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    table.put_item(Item={
        "token": token, "email": email, "status": TOKEN_STATUS_PENDING,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    })
    _send_confirmation_email(email, token)
    return _page("<p>確認メールを送信しました。メール内のリンクをクリックして登録を完了してください。</p>")


def _send_confirmation_email_reminder(email: str, token: str):
    link = f"{REGISTER_FUNCTION_URL}?token={token}"
    body = f"""
    <p>このメールアドレスは既に登録済みです。以下のリンクをブックマークしてご利用ください。</p>
    <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
    """
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "アクセスリンクの再送", "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _handle_confirm(token: str) -> dict:
    item = _get_token_item(token)
    if not item:
        return _page("<p>無効なリンクです。</p>", status=404)
    if item.get("status") == TOKEN_STATUS_PENDING:
        table = dynamodb.Table(APPROVED_TOKENS_TABLE)
        table.put_item(Item={**item, "status": TOKEN_STATUS_APPROVED,
                             "approved_at": datetime.now(timezone.utc).isoformat()})
    return _page(_render_form(token, notice="登録が完了しました。このページのURLをブックマークしてください。"))


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()

    if params.get("confirm"):
        return _handle_confirm(token)

    if params.get("signup_email"):
        return _handle_signup(params)

    if params.get("manifest"):
        return _handle_manifest()

    if params.get("privacy"):
        return _page(_render_privacy())

    if params.get("terms"):
        return _page(_render_terms())

    if params.get("info"):
        # トークン未所持でも仕組みの説明は読めるようにする(SEO・信頼性向上のため公開)。
        # _render_info()は不正なtokenでも例外を出さず既定モードにフォールバックする
        return _page(_render_info(token))

    if not _is_valid_token(token):
        return _page(_render_signup_form(), status=403 if token else 200)

    # 期間モードの変更
    if params.get("mode"):
        mode = set_period_mode(token, params["mode"])
        label = get_preset(mode)["label"]
        return _page(_render_form(token, notice=f"判定期間を「{label}」に変更しました。次回のレポートから反映されます。"))

    # レポートの即時送信(非同期起動)
    report = (params.get("report") or "").strip().lower()
    if report in ("selection", "monitoring"):
        label = "選定" if report == "selection" else "監視"
        try:
            _trigger_report(report, token)
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
        table.delete_item(Key={"owner_token": token, "ticker": ticker})
        return _page(_render_form(token, notice=f"{ticker} を監視リストから削除しました。"))

    watchlist = _current_watchlist(token)
    if ticker not in watchlist and len(watchlist) >= MAX_WATCHLIST_SIZE:
        return _page(
            _render_form(token, notice=f"監視リストは上限{MAX_WATCHLIST_SIZE}件です。"
                                       f"他の銘柄を削除してから登録してください。"),
            status=400,
        )

    try:
        table.put_item(
            Item={
                "owner_token": token,
                "ticker": ticker,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "source": "email_or_search",
            },
            ConditionExpression="attribute_not_exists(ticker)",
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _page(_render_form(token, notice=f"{ticker} はすでに監視リストに登録済みです。"))

    return _page(_render_form(token, notice=f"{ticker} を監視リストに登録しました。"))
