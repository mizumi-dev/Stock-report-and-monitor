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
    NASDAQ100_CACHE_KEY,
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
SES_RECIPIENT = os.environ.get("SES_RECIPIENT", "")
REGISTER_FUNCTION_URL = os.environ.get("REGISTER_FUNCTION_URL", "")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")
ses = boto3.client("ses")

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
    ヘッダーがない場合(Function URL直叩き等)は日本語を既定とする"""
    headers = event.get("headers") or {}
    value = (headers.get("x-site-lang") or "").strip().lower()
    return value if value in SUPPORTED_LANGS else LANG_JA


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


_SITE_TITLE = "PegSignal（株式レポートサービス） | NASDAQ100 成長株シグナル配信"
_SITE_DESCRIPTION = (
    "PEGレシオで割安な成長株をNASDAQ100構成銘柄から選定し、"
    "トレンド・モメンタム・出来高のシグナルを毎日メールで通知する無料サービスです。"
)


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
"""


def _page(inner_html: str, status=200, lang: str = LANG_JA):
    if lang == LANG_EN:
        footer = (
            '<a href="?about=1">About</a> ・ <a href="?contact=1">Contact</a> ・ '
            '<a href="?privacy=1">Privacy Policy</a> ・ <a href="?terms=1">Terms</a>'
        )
    else:
        footer = (
            '<a href="?about=1">運営者情報</a> ・ <a href="?contact=1">お問い合わせ</a> ・ '
            '<a href="?privacy=1">プライバシーポリシー</a> ・ <a href="?terms=1">利用規約</a>'
        )
    return _response(
        f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
        <meta charset="utf-8">
        <title>{_SITE_TITLE}</title>
        <meta name="description" content="{_SITE_DESCRIPTION}">
        <meta name="theme-color" content="#0f172a">
        <link rel="manifest" href="?manifest=1">
        <link rel="apple-touch-icon" href="{_ICON_DATA_URI}">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-title" content="株式レポート">
        <style>{_PAGE_STYLE}</style></head>
        <body>
        <div class="card">
        {inner_html}
        </div>
        <p class="site-footer muted">{footer}</p>
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


def _find_token_by_email(email: str) -> dict | None:
    """既に申込み済みかを確認する(招待制ではなくなったため件数が読めず、
    Scanで十分な小規模を前提とする)。"""
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.scan()
    for item in resp.get("Items", []):
        if item.get("email") == email:
            return item
    return None


def _send_confirmation_email(email: str, token: str, lang: str = LANG_JA):
    link = f"{REGISTER_FUNCTION_URL}?token={token}&confirm=1"
    if lang == LANG_EN:
        subject = "[Confirm your registration] PegSignal sign-up"
        body = f"""
        <p>Thanks for signing up. Click the link below to complete your registration.</p>
        <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
        <p>If you didn't request this, please disregard this email.</p>
        """
    else:
        subject = "【登録確認】株式レポートサービスのお申込み"
        body = f"""
        <p>お申込みありがとうございます。以下のリンクをクリックすると登録が完了します。</p>
        <p><a href="{html.escape(link)}">{html.escape(link)}</a></p>
        <p>心当たりがない場合はこのメールを破棄してください。</p>
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
    """daily-batchが保存するNASDAQ100構成銘柄キャッシュと突き合わせ、
    実在しない/打ち間違えたティッカーの登録を防ぐ(TODO#13)。
    キャッシュ自体が未生成・読み取り失敗の場合は、正しい銘柄の登録を
    誤ってブロックしないよう許可する(fail-open)"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": NASDAQ100_CACHE_KEY})
        known = resp.get("Item", {}).get("value")
        if not known:
            return True
        return ticker in known
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


def _render_signup_form(lang: str = LANG_JA, notice: str = "") -> str:
    n1, n2 = random.randint(1, 9), random.randint(1, 9)

    if lang == LANG_EN:
        notice_html = f'<p class="notice">{notice}</p>' if notice else ""
        return f"""
        {notice_html}
        <p class="eyebrow">PegSignal (free, indie-built)</p>
        <h1>Track undervalued NASDAQ100 growth stocks, every morning</h1>
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
          <div class="step"><div class="step-num">3</div><div class="step-label">Daily reports</div></div>
        </div>

        <details>
          <summary>How stocks are picked, and important notes</summary>
          <p>We screen NASDAQ100 constituents for undervalued growth stocks by
          <strong>PEG ratio</strong> (P/E ÷ earnings growth rate). For stocks you track,
          we monitor trend, momentum, and volume signals daily.</p>
          <p class="muted">Not investment advice. Data comes from Yahoo Finance; we don't
          guarantee its accuracy or timeliness. See <a href="?info=1">how it works</a>
          for details.</p>
        </details>

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
    <h1>NASDAQ100の割安な成長株を、毎朝メールでチェック</h1>
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
      <div class="step"><div class="step-num">3</div><div class="step-label">毎日レポート受信</div></div>
    </div>

    <details>
      <summary>くわしい選定方法・注意事項</summary>
      <p>NASDAQ100構成銘柄の中から、<strong>PEGレシオ</strong>（株価収益率÷利益成長率）が
      低く割安な成長株を選定します。登録した銘柄については、トレンド・モメンタム・
      出来高の3種類のシグナルを毎日監視して通知します。</p>
      <p class="muted">※投資助言ではありません。判定はYahoo Financeが提供するデータに
      基づいており、正確性・遅延について当サービスは保証しません。詳しくは
      <a href="?info=1">この仕組みについて</a>をご覧ください。</p>
    </details>

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
        <p>We collect only <strong>your email address, registered tickers, and time horizon
        selection</strong>. We never collect payment details, your name or address, IP address
        logs, or cookie-based tracking. We never share or sell your personal data to third
        parties.</p>
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
    <p>収集する情報は<strong>メールアドレス・登録銘柄・判定期間モードの選択のみ</strong>です。
    クレジットカード等の決済情報、氏名・住所、IPアドレスのログ、Cookieによる行動追跡は
    一切行っていません。第三者への個人情報の提供・販売もありません。</p>
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
        <p><strong>The reports and signals this service sends are not investment advice.</strong>
        Information is based on external data (such as Yahoo Finance) and we do not guarantee its
        accuracy or completeness. You are responsible for your own investment decisions.</p>
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
    <p><strong>本サービスが配信するレポート・シグナルは投資助言ではありません。</strong>
    情報はYahoo Finance等の外部データに基づいており、正確性・完全性を保証しません。
    投資判断はご自身の責任で行ってください。</p>
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
        <p>It's a free service that picks undervalued growth stocks from the NASDAQ100
        constituents by PEG ratio and emails you signals. See
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
    <p>NASDAQ100構成銘柄からPEGレシオで割安な成長株を選定し、シグナルをメール配信する
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
        <h3>Send a report now</h3>
        <p><a href="?token={t}&report=selection">
          <button style="padding:10px 16px;font-size:16px;width:100%;">📈 Send selection report</button></a></p>
        <p><a href="?token={t}&report=monitoring">
          <button style="padding:10px 16px;font-size:16px;width:100%;">📊 Send monitoring report</button></a></p>
        <p style="color:#888;font-size:12px;">
          You'll return to this screen right away. The email arrives within a minute or so.</p>

        <hr style="margin:24px 0;">
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


def _render_info(token: str, lang: str = LANG_JA) -> str:
    """説明ページ。数値は config.py の実際の設定から生成するので実装とズレない"""
    t = html.escape(token)
    mode = get_period_mode(token)

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
          <summary>📈 Selection report — auto-picks undervalued growth stocks by PEG ratio</summary>
          <p>Universe: NASDAQ100 constituents (fetched from NASDAQ's official API). Only stocks
          meeting both conditions below become candidates, and the top ones are sent ranked by
          PEG ascending (ties broken by larger market cap first).</p>
          <ul>
            <li>Earnings growth rate at or above {MIN_GROWTH * 100:.0f}%</li>
            <li>PEG ratio greater than 0 and at or below {MAX_PEG}</li>
          </ul>
          <p class="muted">PEG, earnings growth, market cap, and price all come from
          <strong>Yahoo Finance (the yfinance library)</strong>. PEG is "P/E ratio ÷ earnings
          growth rate" — lower means more undervalued relative to growth. We don't recalculate it
          ourselves. Send frequency depends on the mode (short-term = daily, mid-term = every
          Monday, long-term = first Monday of the month).</p>
          <p class="muted">Delivery timing: before the US market opens (6:00am US Eastern Time),
          using data through the previous trading day. On Mondays, since no new trading data
          exists over the weekend, this naturally becomes data through the prior Friday.</p>
        </details>

        <details>
          <summary>📊 Monitoring report — daily trend/momentum/volume checks on your watchlist</summary>
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
      <summary>📈 選定レポート — PEGレシオが低い割安な成長株を自動選定</summary>
      <p>対象: NASDAQ100構成銘柄（NASDAQ公式APIから取得）。次の条件を両方満たす
      銘柄だけを候補にし、PEGの小さい順（同値なら時価総額の大きい順）に上位を送ります。</p>
      <ul>
        <li>利益成長率が {MIN_GROWTH * 100:.0f}% 以上</li>
        <li>PEGレシオが 0 より大きく {MAX_PEG} 以下</li>
      </ul>
      <p class="muted">PEG・利益成長率・時価総額・株価は<strong>Yahoo Finance
      （yfinanceライブラリ）</strong>から取得。PEGは「株価収益率(PER) ÷ 利益成長率」で、
      低いほど成長性に対して割安とされる指標です。当サービスでの再計算はしていません。
      発行頻度はモードによって異なります（短期=毎日、中期=毎週月曜、長期=月初の月曜で月1回）。</p>
      <p class="muted">配信タイミング: 米国市場が開く前（米東部時間 朝6:00、日本時間では
      夏時間中は夜19:00頃・冬時間中は夜20:00頃）に、直前の取引日までのデータで配信します。
      週明け分は、週末に新しい取引データが存在しないため、自動的に前週金曜までのデータに
      なります。</p>
    </details>

    <details>
      <summary>📊 監視レポート — 登録銘柄のトレンド・モメンタム・出来高を毎日判定</summary>
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


def _handle_signup(params: dict, lang: str = LANG_JA) -> dict:
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
            '<p>You\'re welcome back anytime. <a href="?">Home</a></p>'
        )
    return _page(
        "<h3>退会が完了しました</h3>"
        "<p>登録データはすべて削除されました。ご利用ありがとうございました。</p>"
        '<p>またのご利用をお待ちしております。<a href="?">トップページ</a></p>'
    )


def _handle_confirm(token: str, token_item: dict | None = None) -> dict:
    """token_itemを渡せば、handler()側で既に取得済みのDynamoDB読み取り結果を再利用できる。"""
    item = token_item if token_item is not None else _get_token_item(token)
    if not item:
        return _page("<p>無効なリンクです。</p>", status=404)
    if item.get("status") == TOKEN_STATUS_PENDING:
        table = dynamodb.Table(APPROVED_TOKENS_TABLE)
        table.put_item(Item={**item, "status": TOKEN_STATUS_APPROVED,
                             "approved_at": datetime.now(timezone.utc).isoformat()})
    lang = _get_user_lang(item)
    notice = ("Registration complete! Bookmark this page's URL." if lang == LANG_EN
              else "登録が完了しました。このページのURLをブックマークしてください。")
    return _page(_render_form(token, lang, notice=notice), lang=lang)


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()

    # 英語版(en.pegsignal.com)対応。トークンが登録済みならその利用者が保存した言語を
    # 優先し(どちらのドメインから来ても本人の設定が優先される)、未登録の新規訪問者は
    # アクセスしてきたドメイン(CloudFrontのX-Site-Langヘッダー)で判定する
    token_item = _get_token_item(token) if token else None
    lang = _get_user_lang(token_item) if token_item else _detect_request_lang(event)

    if params.get("confirm"):
        return _handle_confirm(token, token_item)

    if params.get("signup_email"):
        return _handle_signup(params, lang)

    if params.get("manifest"):
        return _handle_manifest()

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

    if not (token_item and token_item.get("status") == TOKEN_STATUS_APPROVED):
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
        label = get_preset(mode)["label"]
        notice = (f'Time horizon changed to "{label}". This applies from the next report.' if lang == LANG_EN
                  else f"判定期間を「{label}」に変更しました。次回のレポートから反映されます。")
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
            return _page(f"<p>{err_msg}</p>{_back_link(token)}", status=500)
        _record_report_request(token)
        notice = (f"Your {label_en.lower()} report request was accepted. It'll arrive by email shortly." if lang == LANG_EN
                  else f"{label}レポートの送信を受け付けました。まもなくメールが届きます。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    ticker = (params.get("ticker") or "").strip().upper()
    action = (params.get("action") or "register").strip().lower()

    if not ticker:
        return _page(_render_form(token, lang), lang=lang)

    if not TICKER_RE.match(ticker):
        msg = "Invalid ticker." if lang == LANG_EN else "無効なティッカーです。"
        return _page(f"<p>{msg}</p>{_back_link(token)}", status=400)

    table = dynamodb.Table(WATCHLIST_TABLE)

    if action == "delete":
        table.delete_item(Key={"owner_token": token, "ticker": ticker})
        notice = (f"Removed {ticker} from your watchlist." if lang == LANG_EN
                  else f"{ticker} を監視リストから削除しました。")
        return _page(_render_form(token, lang, notice=notice), lang=lang)

    if not _is_known_ticker(ticker):
        notice = (f'Couldn\'t find a ticker called "{ticker}". Please check the symbol '
                   f"against NASDAQ100 constituents and try again." if lang == LANG_EN
                  else f"「{ticker}」という銘柄は見つかりませんでした。"
                       f"NASDAQ100構成銘柄の中から、ティッカーを確認してもう一度お試しください。")
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
