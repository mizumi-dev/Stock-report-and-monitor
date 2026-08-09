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
    UNIVERSES,
    get_preset,
    normalize_mode,
    normalize_universe,
    universe_cache_key,
    user_period_mode_key,
    user_universe_key,
)
from mailer import ResendMailer

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
MAX_WATCHLIST_SIZE = int(os.environ.get("MAX_WATCHLIST_SIZE", "5"))
DAILY_BATCH_FUNCTION = os.environ.get("DAILY_BATCH_FUNCTION", "daily-batch")
SES_SENDER = os.environ.get("SES_SENDER", "")
SES_RECIPIENT = os.environ.get("SES_RECIPIENT", "")
REGISTER_FUNCTION_URL = os.environ.get("REGISTER_FUNCTION_URL", "")
# landing-preview(メール登録不要で選定レポートを読める読み取り専用ページ)のURL。
# 未設定なら申込み画面にリンクを出さない(dev環境等、まだ用意していない場合の
# 既存の他リンクと同じフォールバック方針)
PREVIEW_URL = os.environ.get("PREVIEW_URL", "")
# SESが本番アクセス許可待ち(サンドボックス)の間など、新規登録だけを一時停止したい時に
# "false"にする。既存トークンでのアクセス(レポート受信・銘柄変更・退会等)には影響しない
SIGNUPS_ENABLED = os.environ.get("SIGNUPS_ENABLED", "true").lower() != "false"

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")
# SESの本番アクセス審査がus-east-1・大阪どちらでも即時却下となり解決不能と
# 判断したため、Resend(mailer.py)に乗り換えた。ses変数名・呼び出し方は
# 既存コードとの互換のためそのまま残している。
ses = ResendMailer()

# ASCII英大文字・数字のみ1〜6文字。isalnum()は全角文字等も通すため正規表現で制限する
TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 英語版(en.pegsignal.com)対応。CloudFrontのオリジンカスタムヘッダー(X-Site-Lang)で
# どちらのドメイン経由かを判定する(deploy_domain_en.sh/deploy_patch_ja_lang_header.sh参照)。
# 登録済み利用者は申込み時に判定した言語をapproved_tokensの"lang"属性として保存し、
# 以後はどちらのドメインからアクセスしても保存済みの言語で表示する
# (期間モードと同じ仕組みで、メイン画面からいつでも手動切替もできる)
LANG_JA = "ja"
LANG_EN = "en"
SUPPORTED_LANGS = (LANG_JA, LANG_EN)

# config.py の PERIOD_PRESETS には label_en/description_en が入っているため、英訳の
# 単一の情報源としてそちらを参照する(このファイルではラベル・説明文を複製しない)


def _detect_request_lang(event) -> str:
    """トークンを持たない新規訪問者(申込みフォーム)向け。
    優先順位: ?lang=(明示指定) > X-Site-Lang(ドメイン固定、en.pegsignal.com等) >
    訪問者の地理的位置(CloudFront-Viewer-Country、日本ならja・それ以外はen) >
    既定のja。

    ?lang=を最優先にする理由(2026-08-09、埋め込みフォーム経由の申込みが
    日本語で処理される不具合の修正): pegsignal.com(landing-preview)の埋め込み
    申込みフォームは、英語版で送信すると隠しフィールドで`lang=en`を送るが、
    app.pegsignal.comにはX-Site-Langヘッダーが無いため、以前はこの`lang=en`を
    無視して常にヘッダー/地理判定頼みで日本語になっていた
    (英語話者が申し込むと確認画面・以後のレポートメールまで日本語になる
    実害があった)。app.pegsignal.comはドメインが1本(en側のサブドメインを
    持たない)なので、landing-previewと同じく明示指定を最優先にする。

    地理判定は「日本語が既定」という従来挙動を壊さないよう、ヘッダーが
    無い場合(Function URL直叩き・開発環境等)はjaにフォールバックする
    (2026-08-07、海外流入向けに追加)。"""
    params = event.get("queryStringParameters") or {}
    value = (params.get("lang") or "").strip().lower()
    if value in SUPPORTED_LANGS:
        return value
    headers = event.get("headers") or {}
    value = (headers.get("x-site-lang") or "").strip().lower()
    if value in SUPPORTED_LANGS:
        return value
    country = (headers.get("cloudfront-viewer-country") or "").strip().upper()
    if country:
        return LANG_JA if country == "JP" else LANG_EN
    return LANG_JA


def _get_user_lang(token_item: dict | None) -> str:
    """登録済み利用者の保存済み言語設定。未設定なら日本語"""
    lang = (token_item or {}).get("lang")
    return lang if lang in SUPPORTED_LANGS else LANG_JA


def _set_user_lang(token: str, lang: str, token_item: dict | None = None) -> str:
    """token_itemを渡せば、呼び出し側が既に取得済みのDynamoDB読み取り結果を再利用できる
    (handler()内では既にtoken_itemを取得済みのため、二重読み取りを避ける)。"""
    lang = lang if lang in SUPPORTED_LANGS else LANG_JA
    item = token_item if token_item is not None else _get_token_item(token)
    if item:
        table = dynamodb.Table(APPROVED_TOKENS_TABLE)
        table.put_item(Item={**item, "lang": lang})
    return lang


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


_SITE_TITLE = {
    LANG_JA: "PegSignal（株式レポートサービス） | NASDAQ100・S&P500 成長株シグナル配信",
    LANG_EN: "PegSignal | NASDAQ100 / S&P 500 growth-stock signal alerts",
}
_SITE_DESCRIPTION = {
    LANG_JA: (
        "PEGレシオが低い成長株をNASDAQ100・S&P500構成銘柄から選定し、"
        "トレンド・モメンタム・出来高のシグナルを定期メールで通知する無料サービスです。"
    ),
    LANG_EN: (
        "A free service that screens NASDAQ100 / S&P 500 constituents for growth stocks with a low "
        "PEG ratio and emails you trend, momentum, and volume signals on a regular schedule."
    ),
}
# ホーム画面に追加した際のアイコン名(iOS)・PWAアプリ名。短く収まる必要がある
_APP_SHORT_NAME = {LANG_JA: "株式レポート", LANG_EN: "PegSignal"}
_APP_NAME = {LANG_JA: "株式レポートサービス", LANG_EN: "PegSignal stock reports"}


# 個人開発のLambda単体サービスであることが伝わるよう、外部CSS/フォントは使わず
# この<style>だけで見た目を完結させる(装飾ゼロだと不審に見えるとの指摘への対応)
_PAGE_STYLE = """
:root{--bg:#f4f6f9;--card:#ffffff;--ink:#161a22;--ink-muted:#5a6270;
  --border:#e1e4ea;--accent:#16a34a;--accent-ink:#ffffff;--danger:#b91c1c;
  --notice-bg:#eafbf0;--notice-border:#bbf0cf;--notice-ink:#166534;}
*{box-sizing:border-box;}
body{background:var(--bg);color:var(--ink);margin:0;padding:28px 14px;line-height:1.6;
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif;}
.card{max-width:440px;margin:0 auto;background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:24px 20px;box-shadow:0 1px 2px rgba(20,24,33,.05),0 8px 24px -12px rgba(20,24,33,.15);}
h1{font-size:1.25em;margin:0 0 6px;}
h3{font-size:1.05em;margin:22px 0 8px;}
h3:first-child{margin-top:0;}
h4{font-size:.95em;margin:16px 0 6px;}
p{margin:0 0 12px;}
a{color:var(--accent);text-decoration:none;}
a:hover,a:focus-visible{text-decoration:underline;}
a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{
  outline:2px solid var(--accent);outline-offset:1px;}
input,select,textarea{border:1px solid var(--border);border-radius:8px;padding:9px 11px;
  font-size:16px;font-family:inherit;background:var(--card);color:var(--ink);}
input:focus,select:focus,textarea:focus{border-color:var(--accent);}
button{background:var(--accent);color:var(--accent-ink);border:none;border-radius:8px;
  padding:10px 16px;font-size:15px;font-weight:600;cursor:pointer;}
button:hover{opacity:.9;}
button.danger{background:var(--danger);}
.notice{background:var(--notice-bg);border:1px solid var(--notice-border);color:var(--notice-ink);
  padding:10px 14px;border-radius:8px;margin-bottom:16px;}
.muted{color:var(--ink-muted);font-size:12.5px;}
hr{border:none;border-top:1px solid var(--border);margin:20px 0;}
ul,ol{padding-left:20px;}
table{border-collapse:collapse;width:100%;font-size:13px;}
code{background:#eef0f4;padding:1px 5px;border-radius:4px;}
label{display:block;margin-bottom:12px;}
.site-footer{max-width:440px;margin:16px auto 0;text-align:center;}
.steps{display:flex;gap:8px;margin:14px 0 18px;}
.step{flex:1;text-align:center;}
.step-num{display:flex;align-items:center;justify-content:center;width:26px;height:26px;
  margin:0 auto 4px;border-radius:50%;background:var(--accent);color:var(--accent-ink);
  font-size:13px;font-weight:700;}
.step-label{font-size:11.5px;color:var(--ink-muted);}
details{margin:10px 0;border:1px solid var(--border);border-radius:8px;padding:2px 12px;}
details[open]{padding-bottom:10px;}
summary{cursor:pointer;padding:8px 0;font-weight:600;font-size:14px;}
summary::marker{color:var(--accent);}
details p:last-child{margin-bottom:0;}
.eyebrow{font-size:12px;letter-spacing:.03em;color:var(--ink-muted);margin:0 0 2px;}
.trust-line{font-size:13px;color:var(--ink-muted);margin:0 0 14px;}
.sample-card{background:var(--bg);border:1px solid var(--border);border-radius:10px;
  padding:12px 14px;margin:0 0 16px;}
.sample-label{font-size:11px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--ink-muted);margin-bottom:8px;}
.sample-row{display:flex;align-items:center;justify-content:space-between;gap:8px;
  padding:5px 0;}
.ticker{font-family:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;font-weight:700;}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:100px;
  font-size:12px;font-weight:600;white-space:nowrap;}
.pill.buy{background:#dcfce7;color:#166534;}
.pill.neutral{background:var(--border);color:var(--ink-muted);}
/* 使い方ガイド(?guide=1)用。図を主役にするため余白を広めに取る */
.fig{margin:14px 0 6px;}
.fig svg{display:block;width:100%;height:auto;}
.fig-cap{font-size:12px;color:var(--ink-muted);margin:0 0 18px;text-align:center;}
.guide-card{border:1px solid var(--border);border-radius:10px;padding:14px;margin:10px 0;}
.guide-card h4{margin:0 0 6px;}
.guide-card p{margin:0 0 6px;font-size:13.5px;}
.guide-card .tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;
  border-radius:100px;background:#dcfce7;color:#166534;margin-bottom:6px;}
.guide-card .tag.blue{background:#e0edff;color:#1e40af;}
.faq{margin:8px 0;}
.faq summary{font-size:13.5px;}
.faq p{font-size:13.5px;}
"""


def _page(inner_html: str, status=200, lang: str = LANG_JA):
    if lang == LANG_EN:
        footer = (
            '<a href="?guide=1">How to use</a> ・ <a href="?about=1">About</a> ・ '
            '<a href="?contact=1">Contact</a> ・ '
            '<a href="?privacy=1">Privacy Policy</a> ・ <a href="?terms=1">Terms</a>'
        )
    else:
        footer = (
            '<a href="?guide=1">使い方</a> ・ <a href="?about=1">運営者情報</a> ・ '
            '<a href="?contact=1">お問い合わせ</a> ・ '
            '<a href="?privacy=1">プライバシーポリシー</a> ・ <a href="?terms=1">利用規約</a>'
        )
    return _response(
        f"""<html lang="{lang}"><head><meta name="viewport" content="width=device-width,initial-scale=1">
        <meta charset="utf-8">
        <title>{_SITE_TITLE[lang]}</title>
        <meta name="description" content="{_SITE_DESCRIPTION[lang]}">
        <meta name="theme-color" content="#0f172a">
        <link rel="manifest" href="?manifest=1&amp;lang={lang}">
        <link rel="apple-touch-icon" href="{_ICON_DATA_URI}">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-title" content="{_APP_SHORT_NAME[lang]}">
        <style>{_PAGE_STYLE}</style></head>
        <body>
        <div class="card">
        {inner_html}
        </div>
        <p class="site-footer muted">{footer}</p>
        </body></html>""",
        status=status,
    )


def _handle_manifest(lang: str = LANG_JA) -> dict:
    """Web App Manifest。start_urlは意図的に省略し、
    インストール元ページ(招待トークン付きURL)がそのまま起動先になるようにする。
    manifestはブラウザが別リクエストで取りに来るため、X-Site-Langヘッダーだけに
    頼らず_page()側で?lang=を付けたURLを埋め込み、その値で言語を決める。"""
    manifest = {
        "lang": lang,
        "name": _APP_NAME[lang],
        "short_name": _APP_SHORT_NAME[lang],
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


def _back_link(token: str, lang: str = LANG_JA) -> str:
    label = "Back" if lang == LANG_EN else "戻る"
    return f'<p><a href="?token={html.escape(token)}">{label}</a></p>'


def _get_token_item(token: str) -> dict | None:
    if not token:
        return None
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.get_item(Key={"token": token})
    return resp.get("Item")


def _find_token_by_email(email: str) -> dict | None:
    """既に申込み済みかを確認する(招待制ではなくなったため件数が読めず、
    Scanで十分な小規模を前提とする)。"""
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.scan()
    for item in resp.get("Items", []):
        if item.get("email") == email:
            return item
    return None


def _email_guide_link(lang: str = LANG_JA) -> str:
    """すべての送信メールから使い方ガイドへ辿れるようにする。トークン不要で読める。
    レポートメール側(report.py)にも同等のリンクがある。"""
    lang_param = "&amp;lang=en" if lang == LANG_EN else ""
    label = ("📖 New here? Read the illustrated how-to guide →" if lang == LANG_EN
             else "📖 はじめての方へ — 使い方ガイド（図でわかる説明） →")
    return (f'<p style="margin:12px 0 0;font-size:13px;">'
            f'<a href="{html.escape(REGISTER_FUNCTION_URL)}?guide=1{lang_param}">{label}</a></p>')


def _send_confirmation_email(email: str, token: str, lang: str = LANG_JA):
    link = f"{REGISTER_FUNCTION_URL}?token={token}&confirm=1"
    if lang == LANG_EN:
        subject = "[Confirm your registration] PegSignal sign-up"
        body = f"""
        <p>Thanks for signing up. Click the link below to complete your registration.</p>
        <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
        <p>If you didn't request this, please disregard this email.</p>
        {_email_guide_link(lang)}
        """
    else:
        subject = "【登録確認】株式レポートサービスのお申込み"
        body = f"""
        <p>お申込みありがとうございます。以下のリンクをクリックすると登録が完了します。</p>
        <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
        <p>心当たりがない場合はこのメールを破棄してください。</p>
        {_email_guide_link(lang)}
        """
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _current_watchlist(owner_token: str) -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.query(KeyConditionExpression=Key("owner_token").eq(owner_token))
    return sorted(item["ticker"] for item in resp.get("Items", []))


def _is_known_ticker(ticker: str) -> bool:
    """daily-batchが保存する構成銘柄キャッシュと突き合わせ、実在しない/打ち間違えた
    ティッカーの登録を防ぐ(TODO#13)。

    これは「打ち間違いの検出」であって「対象指数による絞り込み」ではないため、
    対応する全ユニバースのキャッシュのいずれかに含まれていれば許可する。
    監視レポートは対象指数に依存せず「利用者が明示的に選んだ銘柄」を見張る機能
    なので、選択中の指数の外にある銘柄を監視したいケースは正当
    (例: 対象指数はS&P500にしつつ、NASDAQ100固有のPDDも監視し続けたい)。
    選択中の指数だけで判定すると、指数を切り替えた利用者が「既存の監視銘柄は
    残るのに、消すと二度と登録できない」という一貫性のない状態に陥る。

    キャッシュが1つも無い/読み取り失敗の場合は、正しい銘柄の登録を
    誤ってブロックしないよう許可する(fail-open)"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        has_any_cache = False
        for universe in UNIVERSES:
            resp = table.get_item(Key={"key": universe_cache_key(universe)})
            known = resp.get("Item", {}).get("value")
            if not known:
                continue
            has_any_cache = True
            if ticker in known:
                return True
        return not has_any_cache
    except Exception as e:
        print(f"[WARN] 銘柄キャッシュの確認に失敗、登録は許可: {e}")
        return True


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


def get_universe(owner_token: str) -> str:
    """利用者が選んだ対象指数(ユニバース)。get_period_modeと同じ方針で、
    設定が読めなくても画面は出したいので失敗時は既定(NASDAQ100)扱いにする。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": user_universe_key(owner_token)})
        return normalize_universe(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] ユニバース取得に失敗: {e}")
        return normalize_universe(None)


def set_universe(owner_token: str, universe: str) -> str:
    universe = normalize_universe(universe)
    table = dynamodb.Table(SETTINGS_TABLE)
    table.put_item(Item={"key": user_universe_key(owner_token), "value": universe,
                         "updated_at": datetime.now(timezone.utc).isoformat()})
    return universe


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


# 「今すぐ送信」ボタンの連打・マクロ連続実行でdaily-batch(実際の株価取得・メール送信を
# 行う重い処理)を高頻度に起動されるのを防ぐためのクールダウン。利用者ごと(owner_token)に
# 直近のリクエスト時刻をapp_settingsへ記録し、一定時間内の再リクエストは拒否する
REPORT_REQUEST_COOLDOWN_SECONDS = 300


def _report_cooldown_remaining(owner_token: str) -> int:
    """クールダウン中なら残り秒数、そうでなければ0を返す。
    設定の読み取りに失敗した場合は安全側(画面は出す)に倒し0を返す"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": f"{owner_token}#last_report_request"})
        last = resp.get("Item", {}).get("value")
        if not last:
            return 0
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        return max(0, int(REPORT_REQUEST_COOLDOWN_SECONDS - elapsed))
    except Exception as e:
        print(f"[WARN] レポート送信クールダウンの確認に失敗: {e}")
        return 0


def _record_report_request(owner_token: str):
    table = dynamodb.Table(SETTINGS_TABLE)
    table.put_item(Item={"key": f"{owner_token}#last_report_request",
                         "value": datetime.now(timezone.utc).isoformat()})


def _preview_link_html(preview_url: str, lang: str) -> str:
    """メール登録不要で選定レポートを読めるlanding-previewへの案内リンク。
    PREVIEW_URL未設定(dev環境の初期状態等)では何も出さない(壊れたリンクを
    出さない、既存の各種案内リンクと同じフォールバック方針)。"""
    if not preview_url:
        return ""
    url = html.escape(preview_url)
    if lang == LANG_EN:
        text = "Just want to see it first? View this week's picks without signing up"
    else:
        text = "まずは中身だけ見たい方へ — 登録せずに今週の選定結果を見る"
    return f'<p><a href="{url}">👀 {text} →</a></p>'


def _render_signup_form(lang: str = LANG_JA, notice: str = "") -> str:
    n1, n2 = random.randint(1, 9), random.randint(1, 9)

    if lang == LANG_EN:
        notice_html = f'<p class="notice">{notice}</p>' if notice else ""
        return f"""
        {notice_html}
        <p class="eyebrow">PegSignal (free, indie-built)</p>
        <h1>NASDAQ100 / S&amp;P 500 growth stocks, ranked by PEG, delivered on a schedule</h1>
        <p class="trust-line">🔒 Your email is used only for report delivery, no spam.
        <a href="?about=1">Delete your data anytime, one click.</a></p>

        <div class="sample-card">
          <div class="sample-label">Sample email</div>
          <div class="sample-row">
            <span class="ticker">NVDA</span>
            <span class="pill buy">🟢 Buy signal</span>
          </div>
          <div class="sample-row">
            <span class="ticker">AVGO</span>
            <span class="pill neutral">⚪ No signal</span>
          </div>
          <p class="muted" style="margin:8px 0 0;">Illustrative only — actual content changes daily with the market.</p>
        </div>

        <div class="steps">
          <div class="step"><div class="step-num">1</div><div class="step-label">Sign up</div></div>
          <div class="step"><div class="step-num">2</div><div class="step-label">Pick stocks</div></div>
          <div class="step"><div class="step-num">3</div><div class="step-label">Scheduled reports</div></div>
        </div>

        <p><a href="?guide=1">📖 New here? See how it works, with pictures</a></p>

        <details>
          <summary>How stocks are picked, and important notes</summary>
          <p>We screen NASDAQ100 or S&amp;P 500 constituents (your choice, switchable anytime)
          for growth stocks with a low
          <strong>PEG ratio</strong> (P/E ÷ earnings growth rate). For stocks you track,
          we monitor trend, momentum, and volume signals on a regular schedule.</p>
          <p class="muted">How often you hear from us depends on the time horizon you pick
          (short-term = every trading day, mid-term = weekly, long-term = monthly). You can
          change it at any time.</p>
          <p class="muted">Not investment advice. Data comes from Yahoo Finance; we don't
          guarantee its accuracy or timeliness. See <a href="?info=1">how it works</a>
          for details.</p>
        </details>

        {_preview_link_html(PREVIEW_URL, LANG_EN)}

        <h3>Sign up</h3>
        <p class="muted">Your email is used <strong>only to deliver reports and your
        access link</strong>, and is never shared with third parties. Built and run by
        an individual developer (<a href="?about=1">about</a>).</p>
        <form method="GET">
          <input name="signup_email" type="email" placeholder="example@example.com"
                 style="width:100%;" required>
          <p>Quick check: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
          <input type="hidden" name="captcha_n1" value="{n1}">
          <input type="hidden" name="captcha_n2" value="{n2}">
          <button type="submit">Sign up</button>
        </form>
        <p class="muted">
          By signing up you agree to our <a href="?terms=1">Terms</a> and
          <a href="?privacy=1">Privacy Policy</a>.</p>
        <p class="muted">Already signed up? Use your existing access link.
        <strong>If you lost it, sign up again with the same email address above
        and we'll resend it.</strong></p>
        """

    notice_html = f'<p class="notice">{notice}</p>' if notice else ""
    return f"""
    {notice_html}
    <p class="eyebrow">PegSignal（無料・個人開発）</p>
    <h1>NASDAQ100・S&amp;P500構成銘柄のPEGスクリーニング結果を、定期メールでチェック</h1>
    <p class="trust-line">🔒 メールはレポート配信にのみ使用・スパムなし・
    <a href="?about=1">いつでも1クリックで完全削除</a>できます。</p>

    <div class="sample-card">
      <div class="sample-label">配信メールのサンプル</div>
      <div class="sample-row">
        <span class="ticker">NVDA</span>
        <span class="pill buy">🟢 買いシグナル点灯</span>
      </div>
      <div class="sample-row">
        <span class="ticker">AVGO</span>
        <span class="pill neutral">⚪ シグナルなし</span>
      </div>
      <p class="muted" style="margin:8px 0 0;">※イメージです。内容は日々の相場で変動します。</p>
    </div>

    <div class="steps">
      <div class="step"><div class="step-num">1</div><div class="step-label">メール登録</div></div>
      <div class="step"><div class="step-num">2</div><div class="step-label">銘柄を選ぶ</div></div>
      <div class="step"><div class="step-num">3</div><div class="step-label">定期レポート受信</div></div>
    </div>

    <p><a href="?guide=1">📖 はじめての方へ — 使い方をイラストで見る</a></p>

    <details>
      <summary>くわしい選定方法・注意事項</summary>
      <p>NASDAQ100またはS&amp;P500構成銘柄（登録後にいつでも切り替え可能）の中から、
      <strong>PEGレシオ</strong>（株価収益率÷利益成長率）が
      低い成長株を選定します。登録した銘柄については、トレンド・モメンタム・
      出来高の3種類のシグナルを定期的に監視して通知します。</p>
      <p class="muted">※配信の頻度は選んだ判定期間モードによって変わります
      （短期＝毎営業日、中期＝週1回、長期＝月1回）。あとからいつでも変更できます。</p>
      <p class="muted">※投資助言ではありません。判定はYahoo Financeが提供するデータに
      基づいており、正確性・遅延について当サービスは保証しません。詳しくは
      <a href="?info=1">この仕組みについて</a>をご覧ください。</p>
    </details>

    {_preview_link_html(PREVIEW_URL, LANG_JA)}

    <h3>お申込み</h3>
    <p class="muted">ご登録いただいたメールアドレスは<strong>レポート配信・ログイン用
    リンクの送信にのみ</strong>使用し、第三者への提供は行いません。個人が開発・運営して
    います（<a href="?about=1">運営者情報</a>）。</p>
    <form method="GET">
      <input name="signup_email" type="email" placeholder="example@example.com"
             style="width:100%;" required>
      <p>簡単な確認: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
      <input type="hidden" name="captcha_n1" value="{n1}">
      <input type="hidden" name="captcha_n2" value="{n2}">
      <button type="submit">申し込む</button>
    </form>
    <p class="muted">
      お申込みは <a href="?terms=1">利用規約</a>・<a href="?privacy=1">プライバシーポリシー</a>
      への同意とみなされます。本サービスは日本・米国在住の方向けです。
      <strong>欧州経済領域(EEA)・英国・スイス在住の方はお申込みをご遠慮ください。</strong></p>
    <p class="muted">既に登録済みの方は、そのままお持ちのアクセス用リンクからご利用ください。
    <strong>アクセス用リンクを紛失した場合は、登録時と同じメールアドレスでもう一度
    上記フォームからお申込みください。アクセス用リンクを再送します。</strong></p>
    """


def _render_privacy(lang: str = LANG_JA) -> str:
    if lang == LANG_EN:
        return """
        <h3>Privacy Policy</h3>
        <p>We collect only <strong>your email address, registered tickers, your time horizon
        selection, your display language, and the timestamps of those settings</strong>. We never
        collect payment details, your name or address, IP address logs, or cookie-based tracking.
        We never share or sell your personal data to third parties.</p>
        <p>Data is stored in AWS's managed database (DynamoDB); nobody without your invite token
        can access it. That said, this is a personal project and has not undergone a large-scale
        security audit. No passwords or other sensitive credentials are required — your email
        address alone is enough to use the service.</p>
        <p>We may show non-personalized ads or affiliate links on the site in the future, but
        even then we will not deliver ads that identify or track individuals.</p>
        <p>This service is intended for residents of Japan and the United States.
        <strong>Residents of the EEA, UK, and Switzerland are not supported.</strong></p>
        <p>You can delete your registration data <strong>yourself, instantly, at any time</strong>
        via your access link ("Delete account" on the main screen). If you've lost your access
        link or have another disclosure request, please reach us via the
        <a href="?contact=1">contact form</a>.</p>
        <p><a href="?">Back</a></p>
        """
    return """
    <h3>プライバシーポリシー</h3>
    <p>収集する情報は<strong>メールアドレス・登録銘柄・判定期間モードの選択・表示言語の設定と、
    それらの設定日時のみ</strong>です。クレジットカード等の決済情報、氏名・住所、
    IPアドレスのログ、Cookieによる行動追跡は一切行っていません。
    第三者への個人情報の提供・販売もありません。</p>
    <p>データはAWSのマネージド型データベース(DynamoDB)に保管しており、招待トークンを
    知らない第三者はアクセスできません。ただし本サービスは個人開発のため、大規模な
    セキュリティ監査は実施していません。パスワード等の機密情報の入力は不要な設計に
    しています(メールアドレスのみでご利用いただけます)。</p>
    <p>将来、Webページ上に非パーソナライズ広告またはアフィリエイトリンクを掲載する
    可能性がありますが、その場合も個人を特定・追跡する形での広告配信は行いません。</p>
    <p>本サービスは日本国内・米国在住の方を対象としています。
    <strong>欧州経済領域(EEA)・英国・スイス在住の方は対象外です。</strong></p>
    <p>登録情報の削除は、アクセス用リンクから<strong>ご自身でいつでも即時に</strong>行えます
    （メイン画面の「退会」から）。アクセス用リンクを紛失した場合や、その他開示等の
    ご希望は<a href="?contact=1">お問い合わせフォーム</a>からご連絡ください。</p>
    <p><a href="?">戻る</a></p>
    """


def _render_terms(lang: str = LANG_JA) -> str:
    if lang == LANG_EN:
        return """
        <h3>Terms of Service</h3>
        <p><strong>The reports and signals this service sends are not investment advice, and do
        not recommend or solicit the purchase or sale of any specific security.</strong>
        They mechanically apply fixed formulas (PEG ratio, moving averages, RSI, etc.) to
        external data (such as Yahoo Finance), and we do not guarantee its accuracy or
        completeness. This service is provided free of charge and does not offer individualized
        advice tailored to your financial situation or investment objectives.
        You are solely responsible for your own investment decisions.</p>
        <p>This is a free service intended for residents of Japan and the United States.
        <strong>Residents of the EEA, UK, and Switzerland are not supported.</strong></p>
        <p>Please keep your invite token (access link) to yourself. If we find evidence of abuse
        or excessive automated access, we may suspend your account without prior notice.</p>
        <p>The operator may change or discontinue features at their discretion, without prior
        notice.</p>
        <p>See also our <a href="?privacy=1">Privacy Policy</a> and
        <a href="?about=1">About</a> page. Contact us <a href="?contact=1">here</a>.</p>
        <p><a href="?">Back</a></p>
        """
    return """
    <h3>利用規約</h3>
    <p><strong>本サービスが配信するレポート・シグナルは投資助言ではなく、特定の銘柄の
    売買を推奨・勧誘するものでもありません。</strong>
    公開データ(Yahoo Finance等)に機械的な計算式(PEGレシオ・移動平均・RSI等)を
    適用した結果を機械的に表示するものであり、正確性・完全性を保証しません。
    本サービスは無償で提供しており、利用者個々の資産状況・投資目的に応じた
    個別の助言を行うものではありません。投資判断はご自身の責任で行ってください。</p>
    <p>本サービスは日本国内・米国在住の方を対象とする無料サービスです。
    <strong>欧州経済領域(EEA)・英国・スイス在住の方は対象外です。</strong></p>
    <p>招待トークン(アクセスリンク)はご自身で管理してください。不正利用・過度な自動アクセス等が
    判明した場合、事前の通知なくアカウントを停止することがあります。</p>
    <p>本サービスは運営者の判断により、事前の通知なく機能変更・停止を行うことがあります。</p>
    <p>詳細は <a href="?privacy=1">プライバシーポリシー</a>・<a href="?about=1">運営者情報</a>
    も参照してください。お問い合わせは<a href="?contact=1">こちら</a>から。</p>
    <p><a href="?">戻る</a></p>
    """


def _render_about(lang: str = LANG_JA) -> str:
    if lang == LANG_EN:
        return """
        <h3>About</h3>
        <p>PegSignal is <strong>developed and operated by a single individual</strong>, not a
        company. Planning, development, and operations are all handled by one person.</p>
        <p>It's a free service that picks NASDAQ100 or S&amp;P 500 growth stocks (your choice)
        ranked by PEG ratio
        and emails you signals. See
        <a href="?info=1">how it works</a> for the logic details.</p>

        <h4>How we handle your email address</h4>
        <p>Your registered email address is used <strong>only to send reports and login
        links</strong>. We never share it with third parties for advertising or marketing
        purposes. See the <a href="?privacy=1">Privacy Policy</a> for storage and deletion
        details.</p>

        <h4>Contact</h4>
        <p>For questions or requests, please use the <a href="?contact=1">contact form</a>.
        Deleting your account doesn't require contacting us — if you have your access link, you
        can do it yourself instantly.</p>
        <p><a href="?">Back to top</a></p>
        """
    return """
    <h3>運営者情報</h3>
    <p>「PegSignal（株式レポートサービス）」は、<strong>個人が開発・運営しているサービス</strong>
    です。法人ではなく、企画・開発・運用のすべてを個人で行っています。</p>
    <p>NASDAQ100またはS&amp;P500構成銘柄（選択制）からPEGレシオが低い成長株を選定し、
    シグナルをメール配信する
    無料サービスです。判定ロジックの詳細は<a href="?info=1">この仕組みについて</a>を
    ご覧ください。</p>

    <h4>メールアドレスの取り扱いについて</h4>
    <p>ご登録いただくメールアドレスは、<strong>レポート配信・ログイン用リンクの送信にのみ</strong>
    使用します。広告・マーケティング目的での第三者提供は一切行いません。保管方法や
    削除方法の詳細は<a href="?privacy=1">プライバシーポリシー</a>をご覧ください。</p>

    <h4>お問い合わせ</h4>
    <p>ご質問・ご要望は<a href="?contact=1">お問い合わせフォーム</a>からご連絡ください。
    なお登録データの削除（退会）は、アクセス用リンクをお持ちであればお問い合わせ不要で
    ご自身ですぐに行えます。</p>
    <p><a href="?">トップに戻る</a></p>
    """


def _render_contact_form(lang: str = LANG_JA, notice: str = "") -> str:
    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    notice_html = f'<p class="notice">{notice}</p>' if notice else ""
    if lang == LANG_EN:
        return f"""
        {notice_html}
        <h3>Contact</h3>
        <p>Send your question or request using the form below. Please include a reply email
        address so we can get back to you.</p>
        <p class="muted">If you want to delete your account, you don't need to contact us — with
        your access link, use "Delete account" on the main screen to remove it yourself
        instantly.</p>
        <form method="GET">
          <input type="hidden" name="contact_submit" value="1">
          <label>Reply email address
            <input name="contact_email" type="email" placeholder="example@example.com"
                   style="width:100%;" required>
          </label>
          <label>Message
            <textarea name="contact_message" rows="5" style="width:100%;" required></textarea>
          </label>
          <p>Quick check: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
          <input type="hidden" name="captcha_n1" value="{n1}">
          <input type="hidden" name="captcha_n2" value="{n2}">
          <button type="submit">Send</button>
        </form>
        <p><a href="?">Back to top</a></p>
        """
    return f"""
    {notice_html}
    <h3>お問い合わせ</h3>
    <p>ご質問・ご要望などを以下のフォームからお送りください。折り返しご連絡できるよう、
    返信用のメールアドレスをご入力ください。</p>
    <p class="muted">登録データの削除（退会）をご希望の場合、アクセス用リンクを
    お持ちであればメイン画面の「退会」からお問い合わせ不要でご自身ですぐに削除できます。</p>
    <form method="GET">
      <input type="hidden" name="contact_submit" value="1">
      <label>返信用メールアドレス
        <input name="contact_email" type="email" placeholder="example@example.com"
               style="width:100%;" required>
      </label>
      <label>お問い合わせ内容
        <textarea name="contact_message" rows="5" style="width:100%;" required></textarea>
      </label>
      <p>簡単な確認: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
      <input type="hidden" name="captcha_n1" value="{n1}">
      <input type="hidden" name="captcha_n2" value="{n2}">
      <button type="submit">送信する</button>
    </form>
    <p><a href="?">トップに戻る</a></p>
    """


def _render_form(token: str, lang: str = LANG_JA, notice: str = "") -> str:
    t = html.escape(token)
    watchlist = _current_watchlist(token)
    mode = get_period_mode(token)
    preset = get_preset(mode)
    universe = get_universe(token)
    universe_preset = UNIVERSES[universe]

    if lang == LANG_EN:
        delete_label = "delete"
        empty_watchlist = "(Your watchlist is empty)"
        if watchlist:
            rows = "".join(
                f'<li>{html.escape(x)} '
                f'<a href="?token={t}&ticker={html.escape(x)}&action=delete">[{delete_label}]</a></li>'
                for x in watchlist
            )
            list_html = f"<ul>{rows}</ul>"
        else:
            list_html = f"<p>{empty_watchlist}</p>"

        options = "".join(
            f'<option value="{k}"{" selected" if k == mode else ""}>{PERIOD_PRESETS[k]["label_en"]}</option>'
            for k in PERIOD_PRESETS
        )
        preset_en = PERIOD_PRESETS[mode]
        universe_options = "".join(
            f'<option value="{k}"{" selected" if k == universe else ""}>{p["label_en"]}</option>'
            for k, p in UNIVERSES.items()
        )
        notice_html = f'<p class="notice">{notice}</p>' if notice else ""
        lang_switch = (
            f'<p class="muted"><a href="?token={t}&lang=ja">日本語で表示</a></p>'
        )

        return f"""
        {notice_html}
        <h3>Add stocks to your watchlist</h3>
        <form method="GET">
          <input type="hidden" name="token" value="{t}">
          <input name="ticker" placeholder="e.g. NVDA" style="padding:8px;font-size:16px;">
          <button type="submit" style="padding:8px 16px;">Add</button>
        </form>
        <p>Your watchlist (max {MAX_WATCHLIST_SIZE})</p>
        {list_html}

        <hr style="margin:24px 0;">
        <h3>Time horizon</h3>
        <form method="GET">
          <input type="hidden" name="token" value="{t}">
          <select name="mode" style="padding:8px;font-size:16px;">{options}</select>
          <button type="submit" style="padding:8px 16px;">Change</button>
        </form>
        <p style="color:#888;font-size:12px;">Current: <strong>{preset_en['label_en']}</strong> — {preset_en['description_en']}</p>

        <hr style="margin:24px 0;">
        <h3>Index universe</h3>
        <form method="GET">
          <input type="hidden" name="token" value="{t}">
          <select name="universe" style="padding:8px;font-size:16px;">{universe_options}</select>
          <button type="submit" style="padding:8px 16px;">Change</button>
        </form>
        <p style="color:#888;font-size:12px;">
          Current: <strong>{universe_preset['label_en']}</strong> — this only changes which index the
          <strong>selection report</strong> ranks. Your watchlist is unaffected: you can track any
          stock from either index, whichever you pick here.</p>

        <hr style="margin:24px 0;">
        <h3>Send a report now</h3>
        <p><a href="?token={t}&report=selection">
          <button style="padding:10px 16px;font-size:16px;width:100%;">📈 Send selection report</button></a></p>
        <p><a href="?token={t}&report=monitoring">
          <button style="padding:10px 16px;font-size:16px;width:100%;">📊 Send monitoring report</button></a></p>
        <p style="color:#888;font-size:12px;">
          You'll return to this screen right away. The email arrives within a minute or so.</p>

        <hr style="margin:24px 0;">
        <p><a href="?token={t}&guide=1">📖 How to use PegSignal (illustrated guide)</a></p>
        <p><a href="?token={t}&info=1">How it works</a></p>
        <p class="muted"><a href="?token={t}&delete_account=1">Delete account (erase all your data)</a></p>
        {lang_switch}
        """

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
    universe_options = "".join(
        f'<option value="{k}"{" selected" if k == universe else ""}>{p["label"]}</option>'
        for k, p in UNIVERSES.items()
    )

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
    <h3>対象指数</h3>
    <form method="GET">
      <input type="hidden" name="token" value="{t}">
      <select name="universe" style="padding:8px;font-size:16px;">{universe_options}</select>
      <button type="submit" style="padding:8px 16px;">変更</button>
    </form>
    <p style="color:#888;font-size:12px;">
      現在: <strong>{universe_preset['label']}</strong> — ここで変わるのは
      <strong>選定レポート</strong>がどの指数からランキングするかだけです。
      監視リストには影響しません（どちらを選んでも、両方の指数の銘柄を登録・監視できます）。</p>

    <hr style="margin:24px 0;">
    <h3>レポートを今すぐ送信</h3>
    <p><a href="?token={t}&report=selection">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📈 選定レポートを送る</button></a></p>
    <p><a href="?token={t}&report=monitoring">
      <button style="padding:10px 16px;font-size:16px;width:100%;">📊 監視レポートを送る</button></a></p>
    <p style="color:#888;font-size:12px;">
      送信を受け付けたらすぐこの画面に戻ります。メール到着まで1分ほどかかります。</p>

    <hr style="margin:24px 0;">
    <p><a href="?token={t}&guide=1">📖 使い方ガイド（図でわかる説明）</a></p>
    <p><a href="?token={t}&info=1">この仕組みについて（判定ロジック・データ元）</a></p>
    <p class="muted"><a href="?token={t}&delete_account=1">退会（登録データを削除）</a></p>
    <p class="muted"><a href="?token={t}&lang=en">View in English</a></p>
    """


_CYCLE_LABEL = {
    "day": {"ja": "当日の値", "en": "today's value"},
    "week": {"ja": "前の暦週の取引日平均", "en": "average over last calendar week's trading days"},
    "month": {"ja": "前の暦月の取引日平均", "en": "average over last calendar month's trading days"},
}
_SEND_FREQ_LABEL = {
    "SHORT": {"ja": "毎日（平日）", "en": "Daily (weekdays)"},
    "MID": {"ja": "毎週月曜", "en": "Every Monday"},
    "LONG": {"ja": "月初の月曜（月1回）", "en": "First Monday of the month (monthly)"},
}


def _render_ai_prompt(ticker: str, lang: str = LANG_JA) -> str:
    """レポートを見て気になった銘柄をAIに分析させたい利用者向けに、コピー用の
    プロンプト文を表示するページ。メール本文はJavaScriptが動かない(Gmail等が
    scriptタグを除去する)ため、コピーボタン自体はこのWebページ側に置く。
    プロンプトは「客観的な分析」を依頼する内容にとどめ、売買の推奨・断定的な
    結論を求める文言は避けている(本サービス自体が投資助言に該当しないための
    方針と一貫させるため)。"""
    t = html.escape(ticker)
    if lang == LANG_EN:
        prompt = (
            f"Please provide an objective analysis of the stock {t}, covering its recent "
            f"price trend, volume, and fundamental metrics such as P/E and PEG ratio. "
            f"Focus on organizing decision-relevant information rather than giving a "
            f"buy/sell recommendation."
        )
        prompt_escaped = html.escape(prompt)
        return f"""
        <h3>Ask an AI to analyze {t}</h3>
        <p class="muted">Copy this prompt and paste it into your favorite AI assistant.</p>
        <textarea readonly rows="6" style="width:100%;padding:8px;font-size:14px;"
          id="ai-prompt-text">{prompt_escaped}</textarea>
        <p><button type="button" onclick="
          navigator.clipboard.writeText(document.getElementById('ai-prompt-text').value)
            .then(() => {{ this.textContent = 'Copied!'; }});
        ">Copy to clipboard</button></p>
        <p class="muted">This prompt is provided for convenience only and is not investment
        advice from PegSignal.</p>
        <p><a href="?">Back</a></p>
        """
    prompt = (
        f"{t}という銘柄について、直近の株価トレンド・出来高・PER・PEGレシオなどの"
        f"テクニカル/ファンダメンタルズ指標を踏まえて客観的に分析してください。"
        f"売買の推奨ではなく、判断材料となる情報の整理を目的としています。"
    )
    prompt_escaped = html.escape(prompt)
    return f"""
    <h3>{t}をAIに分析してもらう</h3>
    <p class="muted">以下のプロンプトをコピーして、お好きなAIアシスタントに貼り付けてください。</p>
    <textarea readonly rows="6" style="width:100%;padding:8px;font-size:14px;"
      id="ai-prompt-text">{prompt_escaped}</textarea>
    <p><button type="button" onclick="
      navigator.clipboard.writeText(document.getElementById('ai-prompt-text').value)
        .then(() => {{ this.textContent = 'コピーしました！'; }});
    ">クリップボードにコピー</button></p>
    <p class="muted">このプロンプトは利便性のために用意しているもので、PegSignalによる投資助言ではありません。</p>
    <p><a href="?">戻る</a></p>
    """


def _render_info(token: str, lang: str = LANG_JA) -> str:
    """説明ページ。数値は config.py の実際の設定から生成するので実装とズレない"""
    t = html.escape(token)
    mode = get_period_mode(token)
    # このページはトップページからトークン無しでも辿れる(申込み前の判断材料)。
    # 未登録の訪問者には「メイン画面で変更できます」と言っても意味が通らず、
    # 特定の指数を断定するとNASDAQ100専用サービスだと誤解されるため、
    # 登録済み(トークンあり)かどうかで書き分ける
    if token:
        universe_preset = UNIVERSES[get_universe(token)]
        universe_note = (
            f"<strong>{universe_preset['label_en']}</strong> constituents "
            f'(you can change this under "Index universe" on the main screen)'
            if lang == LANG_EN else
            f"<strong>{universe_preset['label']}</strong>構成銘柄"
            f"（メイン画面の「対象指数」から変更できます）"
        )
    else:
        labels = " or ".join(p["label_en"] for p in UNIVERSES.values())
        labels_ja = "または".join(p["label"] for p in UNIVERSES.values())
        universe_note = (
            f"<strong>{labels}</strong> constituents "
            f"(you choose which one after signing up, and can switch anytime)"
            if lang == LANG_EN else
            f"<strong>{labels_ja}</strong>構成銘柄"
            f"（登録後にどちらかを選べます。いつでも切替可能）"
        )

    if lang == LANG_EN:
        rows_en = []
        for key, p in PERIOD_PRESETS.items():
            mark = " ←current" if key == mode else ""
            filt = f" / filtered by the {p['ma_filter']}-day line" if p["ma_filter"] else ""
            rows_en.append(
                f"<tr><td style='padding:4px 8px;'><strong>{p['label_en']}{mark}</strong></td>"
                f"<td style='padding:4px 8px;'>{p['ma_short']}-day × {p['ma_long']}-day{filt}</td>"
                f"<td style='padding:4px 8px;'>RSI{p['rsi_period']}<br>"
                f"buy at or below {p['rsi_oversold']} / sell at or above {p['rsi_overbought']}<br>"
                f"(judged on {_CYCLE_LABEL[p['cycle_unit']]['en']})</td>"
                f"<td style='padding:4px 8px;'>{_SEND_FREQ_LABEL[key]['en']}</td></tr>"
            )
        table_en = (
            "<table style='border-collapse:collapse;font-size:13px;'>"
            "<tr><th style='padding:4px 8px;'>Mode</th><th style='padding:4px 8px;'>Trend</th>"
            "<th style='padding:4px 8px;'>Momentum</th><th style='padding:4px 8px;'>Selection report frequency</th></tr>"
            + "".join(rows_en) + "</table>"
        )
        return f"""
        <h3>How this service works</h3>
        <p class="muted">The 🟢/⚪ lamps just show whether each individual signal is on — they are
        not a combined score across multiple conditions. This is not investment advice, and it
        relies on values provided by Yahoo Finance, so accuracy and timeliness are not
        guaranteed.</p>

        <details open>
          <summary>📈 Selection report — auto-picks growth stocks ranked by PEG ratio</summary>
          <p>Universe: {universe_note}. Only stocks
          meeting both conditions below become candidates, and the top ones are sent ranked by
          PEG ascending (ties broken by larger market cap first).</p>
          <ul>
            <li>Earnings growth rate at or above {MIN_GROWTH * 100:.0f}%</li>
            <li>PEG ratio greater than 0 and at or below {MAX_PEG}</li>
          </ul>
          <p class="muted">PEG, earnings growth, market cap, and price all come from
          <strong>Yahoo Finance (the yfinance library)</strong>. PEG is "P/E ratio ÷ earnings
          growth rate" — a lower PEG means the price is lower relative to growth. We don't recalculate it
          ourselves. Send frequency depends on the mode (short-term = every trading day,
          mid-term = every Monday, long-term = first Monday of the month).</p>
          <p class="muted">Delivery timing: before the US market opens (6:00am US Eastern Time),
          using data through the previous trading day. On Mondays, since no new trading data
          exists over the weekend, this naturally becomes data through the prior Friday.</p>
        </details>

        <details>
          <summary>📊 Monitoring report — trend/momentum/volume checks on your watchlist,
          every trading day</summary>
          <p><strong>Trend</strong> lights up when either of these happens:
          a <strong>cross</strong> (the short moving average crosses above/below the long one
          "on that day"), or <strong>alignment</strong> (price and each moving average line up
          cleanly from highest to lowest). All modes use daily data for this judgment.</p>
          <p><strong>Momentum</strong> is judged by RSI. If the average RSI over the judging
          cycle (short-term = today, mid-term = last calendar week, long-term = last calendar
          month's actual trading days) is at or below the oversold threshold, it's a buy signal;
          at or above the overbought threshold, it's a sell signal.</p>
          <p><strong>Volume</strong> shows "spiking" as a reference indicator (it has no buy/sell
          direction) when the average volume over the judging cycle exceeds
          {PERIOD_PRESETS[mode]['volume_multiplier']}x the baseline.</p>
        </details>

        <details>
          <summary>⚙️ Settings by time horizon (current: {PERIOD_PRESETS[mode]['label_en']})</summary>
          {table_en}
          <p class="muted">Short-term reacts faster but has more false signals; long-term fires
          less often. Going a while without a signal is normal in long-term mode.</p>
        </details>

        <p><a href="?token={t}">Back</a></p>
        """

    rows = []
    for key, p in PERIOD_PRESETS.items():
        mark = " ←現在" if key == mode else ""
        filt = f" / {p['ma_filter']}日線で絞り込み" if p["ma_filter"] else ""
        rows.append(
            f"<tr><td style='padding:4px 8px;'><strong>{p['label']}{mark}</strong></td>"
            f"<td style='padding:4px 8px;'>{p['ma_short']}日線 × {p['ma_long']}日線{filt}</td>"
            f"<td style='padding:4px 8px;'>RSI{p['rsi_period']}<br>"
            f"{p['rsi_oversold']}以下で買い / {p['rsi_overbought']}以上で売り<br>"
            f"({_CYCLE_LABEL[p['cycle_unit']]['ja']}で判定)</td>"
            f"<td style='padding:4px 8px;'>{_SEND_FREQ_LABEL[key]['ja']}</td></tr>"
        )
    table = (
        "<table style='border-collapse:collapse;font-size:13px;'>"
        "<tr><th style='padding:4px 8px;'>モード</th><th style='padding:4px 8px;'>トレンド</th>"
        "<th style='padding:4px 8px;'>モメンタム</th><th style='padding:4px 8px;'>選定レポート頻度</th></tr>"
        + "".join(rows) + "</table>"
    )

    return f"""
    <h3>このサービスの仕組み</h3>
    <p class="muted">🟢/⚪のランプはシグナルの点灯有無を表示しているだけで、複数条件を
    合成したスコアではありません。投資助言ではなく、Yahoo Financeが提供する値に
    依存しており正確性・遅延を保証しません。</p>

    <details open>
      <summary>📈 選定レポート — PEGレシオが低い成長株を自動選定</summary>
      <p>対象: {universe_note}。次の条件を両方満たす
      銘柄だけを候補にし、PEGの小さい順（同値なら時価総額の大きい順）に上位を送ります。</p>
      <ul>
        <li>利益成長率が {MIN_GROWTH * 100:.0f}% 以上</li>
        <li>PEGレシオが 0 より大きく {MAX_PEG} 以下</li>
      </ul>
      <p class="muted">PEG・利益成長率・時価総額・株価は<strong>Yahoo Finance
      （yfinanceライブラリ）</strong>から取得。PEGは「株価収益率(PER) ÷ 利益成長率」で、
      低いほど株価が利益成長率に対して低い水準にあることを示す指標です。当サービスでの再計算はしていません。
      発行頻度はモードによって異なります（短期=毎営業日、中期=毎週月曜、長期=月初の月曜で月1回）。</p>
      <p class="muted">配信タイミング: 米国市場が開く前（米東部時間 朝6:00、日本時間では
      夏時間中は夜19:00頃・冬時間中は夜20:00頃）に、直前の取引日までのデータで配信します。
      週明け分は、週末に新しい取引データが存在しないため、自動的に前週金曜までのデータに
      なります。</p>
    </details>

    <details>
      <summary>📊 監視レポート — 登録銘柄のトレンド・モメンタム・出来高を毎営業日判定</summary>
      <p><strong>トレンド</strong>は次のどちらかが成立すると点灯します:
      <strong>クロス</strong>（短い移動平均線が長い方を「その日に」上抜け／下抜け）、
      または<strong>並び順</strong>（株価・各移動平均線が上から順にきれいに並んでいる状態）。
      全モード共通で日足データを使って判定します。</p>
      <p><strong>モメンタム</strong>はRSIで判定します。判定サイクル分（短期=当日、中期=前の週、
      長期=前の月の実際の取引日）のRSI平均が、売られすぎの水準以下なら買い、買われすぎの
      水準以上なら売りです。</p>
      <p><strong>出来高</strong>は判定サイクル分の平均出来高が基準値の
      {PERIOD_PRESETS[mode]['volume_multiplier']}倍を超えたら「急増」と表示する参考指標
      （買い／売りの方向は持ちません）です。</p>
    </details>

    <details>
      <summary>⚙️ 期間モードごとの設定値（現在: {PERIOD_PRESETS[mode]['label']}）</summary>
      {table}
      <p class="muted">短期は反応が早いぶん騙しも多く、長期は点灯頻度が下がります。
      シグナルが出ない期間が続くのは長期モードでは正常な状態です。</p>
    </details>

    <p><a href="?token={t}">戻る</a></p>
    """


# ===== 使い方ガイド(?guide=1)の図 =====
# 外部の画像ファイル・CDNを持たずLambda単体で完結させる方針(アイコンのdata URIと同じ)
# のため、図はすべてインラインSVGで描く。文字は言語ごとに差し替えられるよう引数で受ける。
# SVGは自動改行しないので、渡すラベルは短く保つこと(長い説明は図の下のキャプションへ)。

def _svg_flow(s1: str, s2: str, s3: str) -> str:
    """申込み→銘柄選択→レポート受信、の3ステップ"""
    def box(x: int, num: int, label: str) -> str:
        return (
            f'<rect x="{x}" y="16" width="92" height="56" rx="10" fill="#ffffff" stroke="#e1e4ea"/>'
            f'<circle cx="{x + 46}" cy="34" r="10" fill="#16a34a"/>'
            f'<text x="{x + 46}" y="38" text-anchor="middle" font-size="11.5" font-weight="700"'
            f' fill="#ffffff">{num}</text>'
            f'<text x="{x + 46}" y="60" text-anchor="middle" font-size="10.5" fill="#161a22">{label}</text>'
        )

    def arrow(x: int) -> str:
        return (f'<path d="M{x} 44 L{x + 14} 44" stroke="#5a6270" stroke-width="1.6"/>'
                f'<path d="M{x + 13} 40 L{x + 19} 44 L{x + 13} 48 Z" fill="#5a6270"/>')

    return (
        '<div class="fig"><svg viewBox="0 0 330 88" role="img">'
        + box(0, 1, s1) + arrow(94)
        + box(119, 2, s2) + arrow(213)
        + box(238, 3, s3)
        + "</svg></div>"
    )


def _svg_peg(per: str, growth: str, peg: str, low: str, high: str) -> str:
    """PEG = PER ÷ 利益成長率。小さいほど「成長のわりに株価が安い」"""
    def chip(x: int, w: int, label: str, fill: str, ink: str) -> str:
        return (
            f'<rect x="{x}" y="10" width="{w}" height="34" rx="8" fill="{fill}" stroke="#e1e4ea"/>'
            f'<text x="{x + w // 2}" y="32" text-anchor="middle" font-size="11" fill="{ink}">{label}</text>'
        )

    return (
        '<div class="fig"><svg viewBox="0 0 330 118" role="img">'
        + chip(0, 104, per, "#ffffff", "#161a22")
        + '<text x="112" y="33" text-anchor="middle" font-size="16" fill="#5a6270">÷</text>'
        + chip(122, 104, growth, "#ffffff", "#161a22")
        + '<text x="234" y="33" text-anchor="middle" font-size="16" fill="#5a6270">=</text>'
        + chip(244, 86, peg, "#dcfce7", "#166534")
        # 下段: PEGの大小が何を意味するかを1本の軸で示す
        + '<line x1="10" y1="82" x2="320" y2="82" stroke="#e1e4ea" stroke-width="2"/>'
        + '<circle cx="70" cy="82" r="6" fill="#16a34a"/>'
        + '<circle cx="260" cy="82" r="6" fill="#b91c1c"/>'
        + f'<text x="70" y="104" text-anchor="middle" font-size="10.5" fill="#166534">{low}</text>'
        + f'<text x="260" y="104" text-anchor="middle" font-size="10.5" fill="#b91c1c">{high}</text>'
        + "</svg></div>"
    )


def _svg_signals(trend: str, momentum: str, volume: str,
                 on_label: str, off_label: str, sell_label: str) -> str:
    """3種類のシグナルと、ランプの色の意味"""
    rows = [(trend, 26), (momentum, 54), (volume, 82)]
    body = "".join(
        f'<text x="0" y="{y + 4}" font-size="11.5" fill="#161a22">{name}</text>'
        f'<circle cx="150" cy="{y}" r="7" fill="#16a34a"/>'
        f'<circle cx="200" cy="{y}" r="7" fill="#e1e4ea"/>'
        f'<circle cx="250" cy="{y}" r="7" fill="#b91c1c"/>'
        for name, y in rows
    )
    # 出来高は買い/売りの方向を持たない参考指標なので、売りランプは描かない
    body = body.replace('<circle cx="250" cy="82" r="7" fill="#b91c1c"/>',
                        '<text x="250" y="86" text-anchor="middle" font-size="11" fill="#5a6270">—</text>')
    head = (
        f'<text x="150" y="8" text-anchor="middle" font-size="10" fill="#166534">{on_label}</text>'
        f'<text x="200" y="8" text-anchor="middle" font-size="10" fill="#5a6270">{off_label}</text>'
        f'<text x="250" y="8" text-anchor="middle" font-size="10" fill="#b91c1c">{sell_label}</text>'
    )
    return f'<div class="fig"><svg viewBox="0 0 290 96" role="img">{head}{body}</svg></div>'


def _svg_frequency(short: str, mid: str, long: str, axis: str) -> str:
    """判定期間モードごとの配信頻度。1か月ぶんの営業日(20日)を点で表す"""
    days, x0, step = 20, 74, 12
    rows = [
        (short, 20, set(range(days))),
        (mid, 50, {0, 5, 10, 15}),
        (long, 80, {0}),
    ]
    body = ""
    for label, y, active in rows:
        body += f'<text x="0" y="{y + 4}" font-size="11" fill="#161a22">{label}</text>'
        body += f'<line x1="{x0}" y1="{y}" x2="{x0 + (days - 1) * step}" y2="{y}" stroke="#eef0f4" stroke-width="2"/>'
        for i in range(days):
            on = i in active
            body += (f'<circle cx="{x0 + i * step}" cy="{y}" r="{4.5 if on else 2.5}" '
                     f'fill="{"#16a34a" if on else "#e1e4ea"}"/>')
    body += f'<text x="{x0}" y="104" font-size="10" fill="#5a6270">{axis}</text>'
    return f'<div class="fig"><svg viewBox="0 0 320 112" role="img">{body}</svg></div>'


def _render_guide(token: str, lang: str = LANG_JA) -> str:
    """初めての人向けの使い方ガイド。専門用語を避け、図を主役にして説明する。
    トークンの有無に関わらず読めるようにしておく(申込み前の判断材料になるため)。"""
    t_ = html.escape(token)
    back = f'<p><a href="?token={t_}">戻る</a></p>' if token else '<p><a href="?">トップに戻る</a></p>'
    back_en = f'<p><a href="?token={t_}">Back</a></p>' if token else '<p><a href="?">Back to top</a></p>'

    if lang == LANG_EN:
        return f"""
        <h3>📖 How to use PegSignal</h3>
        <p class="muted">A plain-language walkthrough for first-time users. No finance
        background needed.</p>

        <h4>1. What this service does</h4>
        {_svg_flow("Sign up", "Pick stocks", "Get reports")}
        <p class="fig-cap">Sign up with your email, choose the stocks you care about,
        and reports arrive automatically.</p>
        <p>PegSignal looks at the 100 largest non-financial companies on the NASDAQ
        (the "NASDAQ100" — Apple, Microsoft, NVIDIA and so on) — or, if you prefer,
        the broader S&amp;P 500 (switchable anytime on the main screen) — ranks them by
        <strong>PEG ratio — price relative to how fast they're growing</strong>, and emails
        you the list. It also watches the specific stocks you pick and tells you when
        something changes.</p>

        <h4>2. The two emails you'll receive</h4>
        <div class="guide-card">
          <span class="tag">📈 Selection report</span>
          <h4>"Here are today's top picks by PEG ratio"</h4>
          <p>A ranked list picked automatically from your chosen index (NASDAQ100 or
          S&amp;P 500). You don't have to
          set anything up — this arrives on its own.</p>
        </div>
        <div class="guide-card">
          <span class="tag blue">📊 Monitoring report</span>
          <h4>"Here's what's happening to the stocks you chose"</h4>
          <p>Only covers stocks you added to your watchlist. If your watchlist is empty,
          this email isn't sent.</p>
        </div>

        <h4>3. What "PEG ratio" means</h4>
        {_svg_peg("P/E ratio", "Growth rate", "PEG", "Low PEG", "High PEG")}
        <p class="fig-cap">PEG = P/E ratio ÷ growth rate. Lower means the price is smaller
        relative to growth.</p>
        <p>A stock can have a high P/E ratio and still be reasonable <em>if</em> the company is
        growing fast. PEG captures exactly that: it divides the P/E ratio by how fast profits
        are growing. <strong>A lower PEG means the price is lower relative to that growth.
        </strong> We only include stocks with a PEG between 0 and {MAX_PEG}, and earnings
        growth of {MIN_GROWTH * 100:.0f}% or more.</p>

        <h4>4. Reading the signal lamps</h4>
        {_svg_signals("Trend", "Momentum", "Volume", "Buy", "None", "Sell")}
        <p class="fig-cap">Three independent checks. Volume has no buy/sell direction.</p>
        <p><strong>Trend</strong> — is the price generally heading up or down?<br>
        <strong>Momentum</strong> — has it been bought or sold too hard recently?<br>
        <strong>Volume</strong> — are unusually many people trading it right now?</p>
        <p class="muted">Important: these three are shown separately on purpose. They are
        <strong>not</strong> combined into a single score, and a green lamp is not a
        recommendation to buy. Think of them as three different instruments on a dashboard.</p>

        <h4>5. Choosing your time horizon</h4>
        {_svg_frequency("Short", "Mid", "Long", "→ one month")}
        <p class="fig-cap">Your choice changes both how sensitive the signals are and how
        often selection reports arrive.</p>
        <p><strong>Short-term</strong> reacts quickly and emails you every trading day, but
        produces more false alarms. <strong>Mid-term</strong> (the default) is a balanced weekly
        rhythm. <strong>Long-term</strong> only fires a few times a year — going quiet for a
        while is normal and expected.</p>
        <p class="muted">You can change this at any time from the main screen, and it takes
        effect from the next report.</p>

        <h4>6. Choosing your index</h4>
        <div class="guide-card">
          <span class="tag blue">NASDAQ100</span>
          <p>The 100 largest non-financial companies on the NASDAQ — tech-heavy, and the
          default. Fewer stocks, so the ranking moves less from week to week.</p>
        </div>
        <div class="guide-card">
          <span class="tag">S&amp;P 500</span>
          <p>500 large US companies across every sector, including finance, healthcare and
          energy. A wider net, so you'll see names that never appear in the NASDAQ100 list.</p>
        </div>
        <p class="muted">This setting only changes which index the <strong>selection
        report</strong> ranks. Your watchlist is unaffected — you can track stocks from either
        index no matter which one you pick here.</p>

        <h4>7. A typical week</h4>
        <div class="guide-card">
          <p><strong>①</strong> A selection report arrives. Something catches your eye.</p>
          <p><strong>②</strong> Tap "Add to watchlist" right inside the email.</p>
          <p><strong>③</strong> From then on, that stock shows up in your monitoring reports.</p>
          <p><strong>④</strong> Use the broker links or the "Ask an AI" prompt to dig deeper
          before deciding anything yourself.</p>
        </div>

        <details class="faq">
          <summary>Is this investment advice?</summary>
          <p>No. Every number here is produced by fixed formulas applied to public data, with no
          human judgment about whether you personally should buy anything. All decisions and
          their consequences are yours. See the <a href="?terms=1">Terms</a>.</p>
        </details>
        <details class="faq">
          <summary>Does it cost anything?</summary>
          <p>No. It's completely free, and there is no paid tier. We never ask for card details.</p>
        </details>
        <details class="faq">
          <summary>How do I stop the emails?</summary>
          <p>Every email has an unsubscribe link at the bottom, and the main screen has
          "Delete account". Deletion is immediate and removes everything.</p>
        </details>
        <details class="faq">
          <summary>Where do the numbers come from?</summary>
          <p>Yahoo Finance. We pass their values through unchanged rather than recalculating
          them, so their accuracy and timing limits apply here too.</p>
        </details>

        <p><a href="?info=1">→ For the exact formulas and thresholds, see "How it works"</a></p>
        {back_en}
        """

    return f"""
    <h3>📖 PegSignalの使い方</h3>
    <p class="muted">はじめての方向けに、専門用語をできるだけ使わずに説明します。
    投資の知識は不要です。</p>

    <h4>1. このサービスは何をするの？</h4>
    {_svg_flow("メール登録", "銘柄を選ぶ", "レポート受信")}
    <p class="fig-cap">メールアドレスを登録し、気になる銘柄を選ぶだけ。
    あとは自動でレポートが届きます。</p>
    <p>PegSignalは、米国NASDAQ市場の代表的な100社（「NASDAQ100」。Apple・Microsoft・
    NVIDIAなど）、またはより広い「S&amp;P500」（メイン画面からいつでも切替可能）の中から、
    <strong>PEGレシオ(株価に対する成長の速さ)が低い銘柄</strong>を
    自動でランキングしてメールでお知らせします。さらに、あなたが選んだ銘柄については
    値動きを見張って、変化があればお知らせします。</p>

    <h4>2. 届くメールは2種類</h4>
    <div class="guide-card">
      <span class="tag">📈 選定レポート</span>
      <h4>「今、PEGレシオが低い成長株はこれです」</h4>
      <p>選んでいる対象指数（NASDAQ100またはS&amp;P500）の全銘柄から自動で選んだ
      ランキングです。何も設定しなくても勝手に届きます。</p>
    </div>
    <div class="guide-card">
      <span class="tag blue">📊 監視レポート</span>
      <h4>「あなたが選んだ銘柄は今こうなっています」</h4>
      <p>監視リストに登録した銘柄だけが対象です。1件も登録していない場合、
      このメールは届きません。</p>
    </div>

    <h4>3. 「PEGレシオ」ってなに？</h4>
    {_svg_peg("PER(株価収益率)", "利益成長率", "PEG", "PEG低い", "PEG高い")}
    <p class="fig-cap">PEG＝PER(株価収益率) ÷ 成長の速さ。数値が小さいほど、
    成長に対して株価の水準が低いことを示します。</p>
    <p>株価収益率(PER)が高くても、その会社が急成長しているなら「妥当」かもしれません。
    PEGはまさにそれを測る指標で、<strong>PERを、利益の成長率で割った値</strong>です。
    <strong>PEGが小さいほど、成長率に対して株価の水準が低い</strong>ことを意味します。
    本サービスでは、PEGが0〜{MAX_PEG}の範囲かつ利益成長率が{MIN_GROWTH * 100:.0f}%以上の
    銘柄だけを対象にしています。</p>

    <h4>4. シグナルのランプの見方</h4>
    {_svg_signals("トレンド", "モメンタム", "出来高", "買い", "なし", "売り")}
    <p class="fig-cap">3つは別々の観点。出来高には買い/売りの方向がありません。</p>
    <p><strong>トレンド</strong> — 株価がおおむね上向きか、下向きか<br>
    <strong>モメンタム</strong> — 最近、買われすぎ・売られすぎになっていないか<br>
    <strong>出来高</strong> — いつもより多くの人が売買しているか</p>
    <p class="muted">大切な点として、この3つは<strong>あえて別々に</strong>表示しています。
    合成して1つのスコアにはしておらず、緑のランプが「買い推奨」を意味するものでも
    ありません。車のダッシュボードにある3つの別々の計器のようなものだとお考えください。</p>

    <h4>5. 判定期間モードの選び方</h4>
    {_svg_frequency("短期", "中期", "長期", "→ 1か月")}
    <p class="fig-cap">選んだモードで、シグナルの敏感さと選定レポートの
    届く頻度が変わります。</p>
    <p><strong>短期</strong>は反応が早く毎営業日届きますが、そのぶん「騙し」も
    増えます。<strong>中期</strong>（初期設定）は週1回のバランス型です。
    <strong>長期</strong>は年に数回しか点灯しません。しばらく何も出ないのは
    長期モードでは正常な状態です。</p>
    <p class="muted">メイン画面からいつでも変更でき、次回のレポートから反映されます。</p>

    <h4>6. 対象指数の選び方</h4>
    <div class="guide-card">
      <span class="tag blue">NASDAQ100</span>
      <p>NASDAQ市場の代表的な100社。ハイテク企業の比率が高く、これが初期設定です。
      銘柄数が少ないぶん、ランキングの顔ぶれは週ごとに大きくは変わりません。</p>
    </div>
    <div class="guide-card">
      <span class="tag">S&amp;P500</span>
      <p>金融・ヘルスケア・エネルギーなど全業種を含む、米国の主要500社。対象が広いぶん、
      NASDAQ100のランキングには決して出てこない銘柄も見られます。</p>
    </div>
    <p class="muted">この設定で変わるのは<strong>選定レポート</strong>がどの指数から
    ランキングするかだけです。監視リストには影響しません（どちらを選んでも、両方の指数の
    銘柄を登録・監視できます）。</p>

    <h4>7. 使い方の流れ（例）</h4>
    <div class="guide-card">
      <p><strong>①</strong> 選定レポートが届く。気になる銘柄を見つける。</p>
      <p><strong>②</strong> メール内の「この銘柄を監視リストに登録」を押す。</p>
      <p><strong>③</strong> 以降、その銘柄が監視レポートに載るようになる。</p>
      <p><strong>④</strong> 証券会社リンクや「AIに分析してもらう」で詳しく調べ、
      最終的にはご自身で判断する。</p>
    </div>

    <details class="faq">
      <summary>これは投資助言ですか？</summary>
      <p>いいえ。表示している数値はすべて、公開データに決まった計算式を当てはめた
      結果です。「あなたが買うべきかどうか」という人間の判断は一切含みません。
      投資判断とその結果はご自身の責任となります。詳しくは
      <a href="?terms=1">利用規約</a>をご覧ください。</p>
    </details>
    <details class="faq">
      <summary>お金はかかりますか？</summary>
      <p>かかりません。完全無料で、有料プランもありません。クレジットカード情報を
      お聞きすることは一切ありません。</p>
    </details>
    <details class="faq">
      <summary>メールを止めたいときは？</summary>
      <p>すべてのメールの末尾に配信停止リンクがあります。メイン画面の「退会」からも
      できます。削除は即時で、登録データはすべて消えます。</p>
    </details>
    <details class="faq">
      <summary>数値はどこから来ていますか？</summary>
      <p>Yahoo Financeです。当サービスで再計算はせず、提供された値をそのまま
      使っています。そのため正確性・遅延についてもYahoo Finance側の制約を
      受けます。</p>
    </details>

    <p><a href="?info=1">→ 具体的な計算式・しきい値は「この仕組みについて」をご覧ください</a></p>
    {back}
    """


def _render_signups_paused(lang: str = LANG_JA) -> str:
    """新規登録を一時停止している間に表示する案内。既存トークンでのアクセスは
    このページを経由しない(handler()側でSIGNUPS_ENABLEDを見るのはトークン無し/
    無効トークンの場合のみ)。"""
    if lang == LANG_EN:
        return """
        <p class="eyebrow">PegSignal (free, indie-built)</p>
        <h1>New sign-ups are temporarily paused</h1>
        <p>We're doing some behind-the-scenes setup right now, so we can't accept new
        sign-ups at this moment. Please check back again soon.</p>
        <p class="muted">If you already have an access link, it still works as
        usual — please use that link directly.</p>
        """
    return """
    <p class="eyebrow">PegSignal（無料・個人開発）</p>
    <h1>現在、新規登録の受付を一時停止しています</h1>
    <p>裏側の準備作業を行っているため、只今は新規のお申込みを受け付けておりません。
    しばらく経ってから改めてお試しください。</p>
    <p class="muted">既にアクセス用リンクをお持ちの方は、通常通りご利用いただけます。
    そのままリンクからお進みください。</p>
    """


def _handle_signup(params: dict, lang: str = LANG_JA) -> dict:
    if not SIGNUPS_ENABLED:
        return _page(_render_signups_paused(lang), status=503, lang=lang)

    email = (params.get("signup_email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        msg = "Please enter a valid email address." if lang == LANG_EN else "メールアドレスの形式が正しくありません。"
        return _page(f"<p>{msg}</p>{_render_signup_form(lang)}", status=400, lang=lang)

    try:
        n1, n2 = int(params.get("captcha_n1", "")), int(params.get("captcha_n2", ""))
        answer = int(params.get("captcha_answer", ""))
    except (TypeError, ValueError):
        answer = None
    captcha_msg = "That answer wasn't correct, please try again." if lang == LANG_EN else "確認の回答が正しくありません。"
    if answer is None or answer != n1 + n2:
        return _page(f"<p>{captcha_msg}</p>{_render_signup_form(lang)}", status=400, lang=lang)

    existing = _find_token_by_email(email)
    if existing and existing.get("status") == TOKEN_STATUS_APPROVED:
        _send_confirmation_email_reminder(email, existing["token"], _get_user_lang(existing))
        msg = ("This email is already registered. We've resent your access link." if lang == LANG_EN
               else "このメールアドレスは既に登録済みです。アクセス用リンクを再送しました。")
        return _page(f"<p>{msg}</p>", lang=lang)

    token = existing["token"] if existing else secrets.token_urlsafe(24)
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    table.put_item(Item={
        "token": token, "email": email, "status": TOKEN_STATUS_PENDING,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
    })
    _send_confirmation_email(email, token, lang)
    msg = ("We've sent a confirmation email. Click the link inside to complete your registration."
           if lang == LANG_EN else "確認メールを送信しました。メール内のリンクをクリックして登録を完了してください。")
    return _page(f"<p>{msg}</p>", lang=lang)


def _send_confirmation_email_reminder(email: str, token: str, lang: str = LANG_JA):
    link = f"{REGISTER_FUNCTION_URL}?token={token}"
    if lang == LANG_EN:
        subject = "Your access link"
        body = f"""
        <p>This email address is already registered. Bookmark the link below to use the service.</p>
        <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
        {_email_guide_link(lang)}
        """
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return
    body = f"""
    <p>このメールアドレスは既に登録済みです。以下のリンクをブックマークしてご利用ください。</p>
    <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
    {_email_guide_link(lang)}
    """
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "アクセスリンクの再送", "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _handle_contact(params: dict, lang: str = LANG_JA) -> dict:
    email = (params.get("contact_email") or "").strip()
    message = (params.get("contact_message") or "").strip()

    if not EMAIL_RE.match(email):
        msg = ("The reply email address isn't valid." if lang == LANG_EN
               else "返信用メールアドレスの形式が正しくありません。")
        return _page(
            f'<p class="notice">{msg}</p>{_render_contact_form(lang)}',
            status=400, lang=lang,
        )
    if not message:
        msg = "Please enter your message." if lang == LANG_EN else "お問い合わせ内容を入力してください。"
        return _page(
            f'<p class="notice">{msg}</p>{_render_contact_form(lang)}',
            status=400, lang=lang,
        )
    try:
        n1, n2 = int(params.get("captcha_n1", "")), int(params.get("captcha_n2", ""))
        answer = int(params.get("captcha_answer", ""))
    except (TypeError, ValueError):
        msg = "That answer wasn't correct, please try again." if lang == LANG_EN else "確認の回答が正しくありません。"
        return _page(
            f'<p class="notice">{msg}</p>{_render_contact_form(lang)}',
            status=400, lang=lang,
        )
    if answer != n1 + n2:
        msg = "That answer wasn't correct, please try again." if lang == LANG_EN else "確認の回答が正しくありません。"
        return _page(
            f'<p class="notice">{msg}</p>{_render_contact_form(lang)}',
            status=400, lang=lang,
        )

    if SES_RECIPIENT:
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [SES_RECIPIENT]},
            ReplyToAddresses=[email],
            Message={
                "Subject": {"Data": "【PegSignal】お問い合わせ", "Charset": "UTF-8"},
                "Body": {"Html": {"Data": (
                    f"<p>差出人(返信用): {html.escape(email)}</p>"
                    f"<p>言語設定: {lang}</p>"
                    f"<p>{html.escape(message)}</p>"
                ), "Charset": "UTF-8"}},
            },
        )
    if lang == LANG_EN:
        return _page(
            '<p class="notice">We\'ve received your message and will get back to you.</p>'
            '<p><a href="?">Back to top</a></p>',
            lang=lang,
        )
    return _page(
        '<p class="notice">お問い合わせを受け付けました。折り返しご連絡します。</p>'
        '<p><a href="?">トップに戻る</a></p>'
    )


def _render_delete_confirm(token: str, lang: str = LANG_JA) -> str:
    t = html.escape(token)
    if lang == LANG_EN:
        return f"""
        <h3>Delete account</h3>
        <p>The following data will be permanently deleted.
        <strong>This cannot be undone.</strong></p>
        <ul>
          <li>Your registered email address</li>
          <li>All stocks in your watchlist</li>
          <li>Your time horizon setting</li>
        </ul>
        <p><a href="?token={t}&delete_account_confirm=1">
          <button class="danger">Delete my account</button></a></p>
        <p><a href="?token={t}">Cancel, go back</a></p>
        """
    return f"""
    <h3>退会（登録データの削除）</h3>
    <p>以下のデータが完全に削除されます。<strong>この操作は取り消せません。</strong></p>
    <ul>
      <li>登録メールアドレス</li>
      <li>監視リストに登録した銘柄（すべて）</li>
      <li>判定期間モードの設定</li>
    </ul>
    <p><a href="?token={t}&delete_account_confirm=1">
      <button class="danger">削除を実行する</button></a></p>
    <p><a href="?token={t}">キャンセルして戻る</a></p>
    """


def _handle_delete_account(token: str, lang: str = LANG_JA) -> dict:
    watchlist_table = dynamodb.Table(WATCHLIST_TABLE)
    for ticker in _current_watchlist(token):
        watchlist_table.delete_item(Key={"owner_token": token, "ticker": ticker})
    settings_table = dynamodb.Table(SETTINGS_TABLE)
    settings_table.delete_item(Key={"key": user_period_mode_key(token)})
    settings_table.delete_item(Key={"key": f"{token}#last_report_request"})
    dynamodb.Table(APPROVED_TOKENS_TABLE).delete_item(Key={"token": token})
    if lang == LANG_EN:
        return _page(
            "<h3>Account deleted</h3>"
            "<p>All your data has been permanently deleted. Thank you for trying PegSignal.</p>"
            '<p>You\'re welcome back anytime. <a href="?">Home</a></p>',
            lang=lang,
        )
    return _page(
        "<h3>退会が完了しました</h3>"
        "<p>登録データはすべて削除されました。ご利用ありがとうございました。</p>"
        '<p>またのご利用をお待ちしております。<a href="?">トップページ</a></p>',
        lang=lang,
    )


def _handle_confirm(token: str, token_item: dict | None = None,
                    request_lang: str = LANG_JA) -> dict:
    """token_itemを渡せば、handler()側で既に取得済みのDynamoDB読み取り結果を再利用できる。
    トークンが無効な場合は保存済みの言語設定が読めないため、アクセス元ドメインから
    判定したrequest_langで表示する。"""
    item = token_item if token_item is not None else _get_token_item(token)
    if not item:
        msg = "This link is not valid." if request_lang == LANG_EN else "無効なリンクです。"
        return _page(f"<p>{msg}</p>", status=404, lang=request_lang)
    if item.get("status") == TOKEN_STATUS_PENDING:
        table = dynamodb.Table(APPROVED_TOKENS_TABLE)
        table.put_item(Item={**item, "status": TOKEN_STATUS_APPROVED,
                             "approved_at": datetime.now(timezone.utc).isoformat()})
    lang = _get_user_lang(item)
    notice = ("Registration complete! Bookmark this page's URL." if lang == LANG_EN
              else "登録が完了しました。このページのURLをブックマークしてください。")
    return _page(_render_form(token, lang, notice=notice), lang=lang)


def _classify_route(params: dict) -> str:
    """CloudWatch Logsでアクセス傾向を把握するための分類のみ。プライバシーポリシー
    (IPアドレス・行動トラッキングを行わない旨)に反しないよう、メールアドレスや
    トークン本体は一切ログに含めない(handler()側ではhas_tokenという真偽値のみ渡す)。"""
    if params.get("confirm"):
        return "confirm"
    if params.get("signup_email"):
        return "signup"
    if params.get("manifest"):
        return "manifest"
    if params.get("privacy"):
        return "privacy"
    if params.get("terms"):
        return "terms"
    if params.get("about"):
        return "about"
    if params.get("contact_submit"):
        return "contact_submit"
    if params.get("contact"):
        return "contact"
    if params.get("info"):
        return "info"
    if params.get("guide"):
        return "guide"
    if params.get("ai_prompt"):
        return "ai_prompt"
    if params.get("lang") in SUPPORTED_LANGS:
        return "lang_switch"
    if params.get("delete_account_confirm"):
        return "delete_account_confirm"
    if params.get("delete_account"):
        return "delete_account"
    if params.get("mode"):
        return "mode_change"
    if params.get("universe"):
        return "universe_change"
    if params.get("report") in ("selection", "monitoring"):
        return f"report_{params['report']}"
    if params.get("ticker"):
        return "ticker_action"
    return "main_form"


# 既知のbot/スキャナー/クローラーのUser-Agent断片。ここに載っている文字列を
# 含むUAは"[ACCESS] ... bot=True"としてログに残し、CloudWatch Logs Insightsで
# 「実際の人間らしいアクセス」を都度手作業で絞り込まずに済むようにする
# (2026-08-06、流入分析で毎回同じ除外作業をしていたことから追加)。
_BOT_UA_SUBSTRINGS = (
    "bot", "spider", "crawl", "scan", "curl/", "wget/", "python-requests",
    "go-http-client", "scrapy", "java/", "libwww", "httpclient", "okhttp",
    "postman", "insomnia", "sniffer", "nmap", "nikto", "masscan", "zgrab",
)
# real_form_ja/enは_classify_route()が実際に応答する既知パス。それ以外への
# アクセスは、UAを詐称していても「そもそも存在しないURLを叩いている」時点で
# botとみなせる(典型的な脆弱性スキャン挙動)
_KNOWN_PATHS = {"/", "/favicon.ico"}


def _is_probable_bot(user_agent: str, path: str) -> bool:
    ua_lower = user_agent.lower()
    if any(s in ua_lower for s in _BOT_UA_SUBSTRINGS):
        return True
    if path not in _KNOWN_PATHS:
        return True
    return False


def _access_log_fields(event: dict) -> tuple[str, str, str, str]:
    """流入分析用の最小限の付加情報。IPアドレスは含めない
    (プライバシーポリシーに明記の通り)。パスは、クエリ文字列だけを見る
    _classify_route()では判別できない「そもそも存在しないパスを叩いている
    (典型的なボットの脆弱性スキャン挙動)」ケースを見分けるために必要。
    User-Agentはブラウザ種別を示す一般的なサーバーログ項目で、個人特定情報ではない。
    RefererはURLのみ(どのページ・サイトから来たか)で、個人を特定する情報は
    含まない。値はそのまま外部から送られてくるため、ログの行崩れを防ぐ目的で
    改行を除去したうえで長さを制限する。"""
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or "/"
    headers = event.get("headers") or {}
    user_agent = (headers.get("user-agent") or headers.get("User-Agent") or "")[:120]
    referer_raw = headers.get("referer") or headers.get("Referer") or ""
    referer = referer_raw.replace("\n", " ").replace("\r", " ")[:200]
    return path, user_agent, referer, ("true" if _is_probable_bot(user_agent, path) else "false")


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()
    path, user_agent, referer, is_bot = _access_log_fields(event)
    # 告知文への?src=をそのまま流入元ラベルとして記録する(例: ?src=twitter,
    # ?src=guide_email)。任意の英数字・アンダースコア・ハイフンのみ許可し、
    # 自由記述にしない(ログの行崩れ・検索性低下を防ぐため)。
    src_raw = (params.get("src") or "").strip()
    src = src_raw if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", src_raw) else ""
    print(f"[ACCESS] route={_classify_route(params)} has_token={bool(token)} "
          f"bot={is_bot} src={src!r} referer={referer!r} path={path!r} ua={user_agent!r}")

    # 英語版(en.pegsignal.com)対応。トークンが登録済みならその利用者が保存した言語を
    # 優先し(どちらのドメインから来ても本人の設定が優先される)、未登録の新規訪問者は
    # アクセスしてきたドメイン(CloudFrontのX-Site-Langヘッダー)で判定する
    token_item = _get_token_item(token) if token else None
    lang = _get_user_lang(token_item) if token_item else _detect_request_lang(event)

    if params.get("confirm"):
        return _handle_confirm(token, token_item, lang)

    if params.get("signup_email"):
        return _handle_signup(params, lang)

    if params.get("manifest"):
        # manifestはブラウザが単独で取得するためX-Site-Langが付かないことがある。
        # _page()が埋め込む?lang=を優先し、無ければリクエスト判定にフォールバックする
        manifest_lang = params.get("lang") if params.get("lang") in SUPPORTED_LANGS else lang
        return _handle_manifest(manifest_lang)

    if params.get("privacy"):
        return _page(_render_privacy(lang), lang=lang)

    if params.get("terms"):
        return _page(_render_terms(lang), lang=lang)

    if params.get("about"):
        return _page(_render_about(lang), lang=lang)

    if params.get("contact_submit"):
        return _handle_contact(params, lang)

    if params.get("contact"):
        return _page(_render_contact_form(lang), lang=lang)

    if params.get("info"):
        # トークン未所持でも仕組みの説明は読めるようにする(SEO・信頼性向上のため公開)。
        # _render_info()は不正なtokenでも例外を出さず既定モードにフォールバックする
        return _page(_render_info(token, lang), lang=lang)

    if params.get("guide"):
        # 使い方ガイド。申込み前の判断材料になるためトークン不要で公開する。
        # メール内リンクからも来るため、?lang=での明示指定を受け付ける
        # (レポートを発行した言語がX-Site-Langヘッダーだけでは伝わらないため)
        guide_lang = params.get("lang") if params.get("lang") in SUPPORTED_LANGS else lang
        return _page(_render_guide(token, guide_lang), lang=guide_lang)

    if params.get("ai_prompt"):
        # レポートメールの「AIに分析してもらう」リンクから来る。トークン不要で
        # 誰でも使える(このページ自体は利用者データを一切参照・変更しないため)。
        # このルートは他の言語切替ハンドラより前で分岐するため、lang=en等の
        # 明示的なクエリパラメータをここで自前で反映する(レポート発行時のlangが
        # X-Site-Langヘッダーだけでは伝わらないメールリンクからの遷移のため)
        ai_lang = params.get("lang") if params.get("lang") in SUPPORTED_LANGS else lang
        ticker = (params.get("ticker") or "").strip().upper()
        if not TICKER_RE.match(ticker):
            msg = "Invalid ticker." if ai_lang == LANG_EN else "無効なティッカーです。"
            return _page(f"<p>{msg}</p>{_back_link(token, ai_lang)}", status=400, lang=ai_lang)
        return _page(_render_ai_prompt(ticker, ai_lang), lang=ai_lang)

    if not (token_item and token_item.get("status") == TOKEN_STATUS_APPROVED):
        if not SIGNUPS_ENABLED:
            return _page(_render_signups_paused(lang), status=403 if token else 503, lang=lang)
        return _page(_render_signup_form(lang), status=403 if token else 200, lang=lang)

    # 言語の手動切替(期間モードと同じ仕組み)。メイン画面のリンクから、
    # どちらのドメインからアクセスしていても言語を切り替えられる
    if params.get("lang") in SUPPORTED_LANGS:
        lang = _set_user_lang(token, params["lang"], token_item)
        return _page(_render_form(token, lang), lang=lang)

    # 退会(登録データの自己削除)。トークンが有効な間だけ操作でき、
    # お問い合わせフォーム経由の手動対応を待たずにその場で完結する
    if params.get("delete_account_confirm"):
        return _handle_delete_account(token, lang)

    if params.get("delete_account"):
        return _page(_render_delete_confirm(token, lang), lang=lang)

    # 期間モードの変更
    if params.get("mode"):
        mode = set_period_mode(token, params["mode"])
        preset = get_preset(mode)
        # 英語表示時に日本語のラベル(「中期」等)が混ざらないよう、言語ごとのラベルを使う
        notice = (f'Time horizon changed to "{preset["label_en"]}". This applies from the next report.'
                  if lang == LANG_EN
                  else f"判定期間を「{preset['label']}」に変更しました。次回のレポートから反映されます。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    # 対象指数(ユニバース)の変更。期間モードと全く同じ仕組み(app_settings +
    # プリセット辞書)で実装している(S&P500対応、2026-08-07)
    if params.get("universe"):
        universe = set_universe(token, params["universe"])
        preset = UNIVERSES[universe]
        notice = (f'Universe changed to "{preset["label_en"]}". This applies from the next report.'
                  if lang == LANG_EN
                  else f"対象指数を「{preset['label']}」に変更しました。次回のレポートから反映されます。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    # レポートの即時送信(非同期起動)
    report = (params.get("report") or "").strip().lower()
    if report in ("selection", "monitoring"):
        label_en = "Selection" if report == "selection" else "Monitoring"
        label = "選定" if report == "selection" else "監視"
        wait = _report_cooldown_remaining(token)
        if wait > 0:
            notice = (f"Please wait {wait} more seconds before requesting another report." if lang == LANG_EN
                      else f"連続でのレポート送信リクエストはできません。あと{wait}秒お待ちください。")
            return _page(_render_form(token, lang, notice=notice), status=429, lang=lang)
        try:
            _trigger_report(report, token)
        except Exception as e:
            err_msg = (f"Failed to send the {label_en.lower()} report: {html.escape(str(e))}" if lang == LANG_EN
                       else f"{label}レポートの送信に失敗しました: {html.escape(str(e))}")
            return _page(f"<p>{err_msg}</p>{_back_link(token, lang)}", status=500, lang=lang)
        _record_report_request(token)
        # 「メール到着まで1分ほど」はメイン画面のボタン付近の案内文言(既存)と表記を揃える
        notice = (f"Your {label_en.lower()} report request was accepted. It'll arrive by email "
                  f"within a minute or so." if lang == LANG_EN
                  else f"{label}レポートの送信を受け付けました。メール到着まで1分ほどかかります。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    ticker = (params.get("ticker") or "").strip().upper()
    action = (params.get("action") or "register").strip().lower()

    if not ticker:
        return _page(_render_form(token, lang), lang=lang)

    if not TICKER_RE.match(ticker):
        msg = "Invalid ticker." if lang == LANG_EN else "無効なティッカーです。"
        return _page(f"<p>{msg}</p>{_back_link(token, lang)}", status=400, lang=lang)

    table = dynamodb.Table(WATCHLIST_TABLE)

    if action == "delete":
        table.delete_item(Key={"owner_token": token, "ticker": ticker})
        notice = (f"Removed {ticker} from your watchlist." if lang == LANG_EN
                  else f"{ticker} を監視リストから削除しました。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    # 対象指数に関わらず、対応している全指数の構成銘柄を登録できる(_is_known_ticker
    # の判定は打ち間違い検出が目的で、対象指数の絞り込みではないため)
    if not _is_known_ticker(ticker):
        known_labels = "・".join(p["label"] for p in UNIVERSES.values())
        known_labels_en = " / ".join(p["label_en"] for p in UNIVERSES.values())
        notice = (f'Couldn\'t find a ticker called "{ticker}". Please check the symbol '
                   f"against {known_labels_en} constituents and try again." if lang == LANG_EN
                  else f"「{ticker}」という銘柄は見つかりませんでした。"
                       f"{known_labels}構成銘柄の中から、ティッカーを確認してもう一度お試しください。")
        return _page(_render_form(token, lang, notice=notice), status=400, lang=lang)

    watchlist = _current_watchlist(token)
    if ticker not in watchlist and len(watchlist) >= MAX_WATCHLIST_SIZE:
        notice = (f"Your watchlist is limited to {MAX_WATCHLIST_SIZE} stocks. "
                   f"Remove one before adding another." if lang == LANG_EN
                  else f"監視リストは上限{MAX_WATCHLIST_SIZE}件です。他の銘柄を削除してから登録してください。")
        return _page(_render_form(token, lang, notice=notice), status=400, lang=lang)

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
        notice = (f"{ticker} is already in your watchlist." if lang == LANG_EN
                  else f"{ticker} はすでに監視リストに登録済みです。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    notice = (f"Added {ticker} to your watchlist." if lang == LANG_EN
              else f"{ticker} を監視リストに登録しました。")
    return _page(_render_form(token, lang, notice=notice), lang=lang)
