"""
landing-preview Lambda ハンドラ

メールアドレスを登録したくない訪問者にも継続的な価値を提供するため、短期・中期・
長期の選定レポートをその場でウェブページとして閲覧できるようにする「読み取り専用の
ランディングページ」。メール配信機能への誘導はあくまで補助的な位置づけであり、
このウェブページ自体を独立したメイン機能として設計する(2026-08-06、ユーザーからの
フィードバックにより、導入文でメール機能に触れる書き方から修正)。register-stockとは
別のLambda・別のFunction URLとしてデプロイし、本番(register-stock)には一切手を
加えない(独立したテスト展開として追加)。

設計上の要点:
- **読み取り専用**。daily-batchが計算し正本を保有するselection_snapshot
  (app_settingsテーブル)を読むだけで、新規スキャンは一切行わない
  (CLAUDE.mdの「バッチが計算し正本を保有、ボタン押下は読み取り専用」の設計を
  この新しい画面にも適用する)。IAMロールもGetItemのみ許可する
- **JS非依存・全モード同一ページ**。短期/中期/長期のいずれかをJSで出し分ける
  のではなく、3モード分のHTMLを1つのGETレスポンスに全部書き出す。AIクローラー・
  検索botはJSを実行しないため、動的な出し分けだとインデックスされない。
  ページ上部の「短期/中期/長期」リンクはページ内アンカー(#short等)で、
  クリックしなくても3モード分の内容はすべてHTML上に存在する
- **メール登録の案内はページ下部のみ**。導入部(_intro_html)ではメール配信機能に
  一切触れず、このページ自体の価値を先に伝える。個別銘柄の監視・メール受信を
  望む場合の案内は_signup_pitch_html()としてページ下部にのみ置く
- **申込みフォームの見た目はこのページに埋め込むが、処理は複製しない
  (2026-08-07更新)**。メールアドレス入力欄をこのページ上に表示し、Webで
  完結する体験にする。ただしフォームのaction属性はregister-stock
  (app.pegsignal.com)を向けたmethod="GET"の素のHTMLフォームで、送信すると
  ブラウザがそちらへ遷移する。実際の登録処理(重複チェック・トークン発行・
  ダブルオプトインメール送信)は一切複製しない(単一の正本を保つため)。
  唯一このLambda側でも生成しているのはCAPTCHA用の乱数2つで、
  register-stock側の検証が完全にステートレス(URLパラメータ同士の突き合わせ)
  なためどちらが生成しても安全に検証できる。乱数生成は書き込みを伴わないため
  「読み取り専用」の設計原則は崩れない
- **「割安」等の価値判断を示す表現を避ける**。PEGレシオという客観的な指標に
  基づく機械的な選定であることが伝わるよう、「PEGレシオが低い」という表現に
  統一する(金商法(投資助言業)リスク低減の方針、TODO.md参照)
"""
import html
import os
import random

import boto3

from config import SETTINGS_TABLE_DEFAULT, selection_snapshot_key
from report import LANG_EN, LANG_JA, render_selection_cards, selection_subject, t

SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
REGISTER_URL = os.environ.get("REGISTER_URL", "")

dynamodb = boto3.resource("dynamodb")

SUPPORTED_LANGS = (LANG_JA, LANG_EN)
_MODES = ("SHORT", "MID", "LONG")
_ANCHOR = {"SHORT": "short", "MID": "mid", "LONG": "long"}


def _detect_lang(event) -> str:
    headers = event.get("headers") or {}
    value = (headers.get("x-site-lang") or "").strip().lower()
    if value in SUPPORTED_LANGS:
        return value
    params = event.get("queryStringParameters") or {}
    value = (params.get("lang") or "").strip().lower()
    return value if value in SUPPORTED_LANGS else LANG_JA


def _get_selection_snapshot(mode: str) -> dict | None:
    """daily-batchが保存した「本日の」選定スナップショットを読むだけ。
    新規スキャンは行わない(handler.pyのget_selection_snapshotと同じ設計)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": selection_snapshot_key(mode)})
        item = resp.get("Item")
        if not item or "ranked" not in item:
            return None
        return {"ranked": list(item["ranked"]), "signals_map": dict(item.get("signals_map") or {}),
                "as_of_date": item.get("as_of_date")}
    except Exception as e:
        print(f"[WARN] 選定スナップショットの取得に失敗: {e}")
        return None


# サイトの既存ページ(register-stock側の_PAGE_STYLE)と揃えたトーンの最小限のCSS。
# 個人開発・非公式ページであることが伝わるよう、装飾は最小限にする
_PAGE_STYLE = """
:root{--bg:#f4f6f9;--card:#ffffff;--ink:#161a22;--ink-muted:#5a6270;
  --border:#e1e4ea;--accent:#16a34a;--accent-ink:#ffffff;}
*{box-sizing:border-box;}
body{background:var(--bg);color:var(--ink);margin:0;padding:28px 14px;line-height:1.6;
  font-family:-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",system-ui,sans-serif;}
.card{max-width:560px;margin:0 auto;background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:24px 20px;box-shadow:0 1px 2px rgba(20,24,33,.05),0 8px 24px -12px rgba(20,24,33,.15);}
h1{font-size:1.3em;margin:0 0 6px;}
h2{font-size:1.1em;margin:26px 0 4px;padding-top:10px;border-top:1px solid var(--border);}
h2:first-of-type{border-top:none;padding-top:0;}
p{margin:0 0 12px;}
a{color:var(--accent);text-decoration:none;}
a:hover,a:focus-visible{text-decoration:underline;}
input,button{border:1px solid var(--border);border-radius:8px;padding:9px 11px;
  font-size:16px;font-family:inherit;background:var(--card);color:var(--ink);}
button{background:var(--accent);color:var(--accent-ink);border:none;padding:10px 16px;
  font-weight:600;cursor:pointer;}
.muted{color:var(--ink-muted);font-size:12.5px;}
.eyebrow{font-size:12px;letter-spacing:.03em;color:var(--ink-muted);margin:0 0 2px;}
.mode-nav{display:flex;gap:8px;margin:14px 0 20px;flex-wrap:wrap;}
.mode-nav a{border:1px solid var(--border);border-radius:100px;padding:6px 14px;
  font-size:13px;font-weight:600;color:var(--ink);}
.notice-box{background:#eafbf0;border:1px solid #bbf0cf;border-radius:10px;
  padding:14px 16px;margin:22px 0;}
.btn-link{display:inline-block;background:var(--accent);color:var(--accent-ink)!important;
  border-radius:8px;padding:10px 18px;font-weight:600;text-decoration:none!important;}
.site-footer{max-width:560px;margin:16px auto 0;text-align:center;}
.disclaimer{border-left:3px solid var(--border);padding-left:10px;margin:0 0 18px;}
"""


def _intro_html(lang: str) -> str:
    """このページ自体がメイン機能の1つであるため、メール配信機能への言及は
    意図的に冒頭に置かない(2026-08-06、ユーザーからのフィードバックで修正)。
    メール登録の案内は_signup_pitch_html()としてページ下部にのみ置く。"""
    if lang == LANG_EN:
        return """
        <p class="eyebrow">PegSignal (free, indie-built)</p>
        <h1>PEG screening results (NASDAQ100)</h1>
        <p>We rank NASDAQ100 growth stocks by PEG ratio (P/E ratio ÷ earnings growth rate)
        across three time horizons — short-term, mid-term, and long-term. Jump to one of
        the three below, or just scroll through all of them.</p>
        """
    return """
    <p class="eyebrow">PegSignal（無料・個人開発）</p>
    <h1>PEGスクリーニング結果（NASDAQ100）</h1>
    <p>NASDAQ100構成銘柄の中から、PEGレシオ（株価収益率÷利益成長率）が低い成長株を、
    短期・中期・長期の3つの期間でランキングしています。
    下のリンクで見たい期間へ移動するか、そのまま3つとも順にご覧ください。</p>
    """


def _disclaimer_html(lang: str) -> str:
    """免責文(「機械的な計算結果であり売買推奨ではない」)。

    このページは**登録不要で誰でも読めるサービスの顔**であり、銘柄ランキングを
    そのまま公開表示する。メール本文(report.pyのbuild_selection_report)には
    免責が入っているが、このページが使う render_selection_cards には含まれない
    ため、当初このページには免責が一切表示されていなかった(2026-08-07に発覚し追加)。

    金商法(投資助言業)に該当しないための根拠として、ランキングを見る前に
    目に入る位置(ページ上部)に置く。文言はreport.pyのT辞書を正本として共有し、
    メール本文と表現がズレないようにする。"""
    return f'<p class="muted disclaimer">{t("disclaimer", lang)}</p>'


def _mode_nav_html(lang: str) -> str:
    labels_ja = {"SHORT": "短期", "MID": "中期", "LONG": "長期"}
    labels_en = {"SHORT": "Short-term", "MID": "Mid-term", "LONG": "Long-term"}
    labels = labels_en if lang == LANG_EN else labels_ja
    links = "".join(f'<a href="#{_ANCHOR[m]}">{labels[m]}</a>' for m in _MODES)
    return f'<div class="mode-nav">{links}</div>'


def _back_to_top_html(lang: str) -> str:
    label = "↑ Back to top" if lang == LANG_EN else "↑ トップに戻る"
    return f'<p class="muted"><a href="#top">{label}</a></p>'


def _mode_section_html(mode: str, lang: str, register_base_url: str) -> str:
    snapshot = _get_selection_snapshot(mode)
    heading = selection_subject(mode, lang).lstrip("📈 ")
    anchor = _ANCHOR[mode]
    back_to_top = _back_to_top_html(lang)

    if snapshot is None:
        body = ("<p>No data yet for this period." if lang == LANG_EN
                else "<p>この期間のデータはまだありません。") + "</p>"
        return f'<h2 id="{anchor}">{html.escape(heading)}</h2>{body}{back_to_top}'

    body = render_selection_cards(
        snapshot["ranked"], snapshot["signals_map"], register_base_url, lang, register_token=None,
    )
    as_of = snapshot.get("as_of_date")
    as_of_note = ""
    if as_of:
        text = f"Data as of {as_of}" if lang == LANG_EN else f"{as_of}時点のデータです"
        as_of_note = f'<p class="muted">{text}</p>'
    return f'<h2 id="{anchor}">{html.escape(heading)}</h2>{as_of_note}{body}{back_to_top}'


def _signup_pitch_html(lang: str, register_url: str) -> str:
    """個別銘柄の監視・メール配信は登録が必要である旨を説明し、その場でメール
    アドレスを入力して申し込めるフォームを埋め込む(2026-08-07、apex切替に伴い
    「Webで完結させたい」というユーザー要望を受けて追加)。

    実際の登録処理(重複チェック・トークン発行・ダブルオプトインメール送信)は
    register-stock(本番、app.pegsignal.com)が持つ唯一の実装のままで、ここでは
    一切複製しない。フォームのaction属性をregister-stockのURLに向けた
    method="GET"の素のHTMLフォームにすることで、送信するとブラウザが
    app.pegsignal.comへ遷移し、そちらの実処理がそのまま動く(JS・CORS不要)。

    唯一このLambda側でも生成しているのはCAPTCHA用の2つの乱数(n1・n2)。
    register-stock側の検証はn1+n2と回答を突き合わせるだけの完全にステートレスな
    ロジック(セッション等を参照しない)なので、どちらのLambdaが生成しても
    安全に検証できる。書き込みは一切発生しないため、landing-previewの
    「読み取り専用」設計(CLAUDE.md参照)は崩れない。"""
    url = html.escape(register_url)
    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    if lang == LANG_EN:
        return f"""
        <div class="notice-box">
        <p><strong>Want to track specific stocks, or get this by email?</strong></p>
        <p>This page always shows the latest top picks only. Sign up (free) to additionally:
        pick individual stocks to watch, get daily/weekly/monthly trend &amp; momentum
        alerts on them, and receive this selection report by email on your chosen
        schedule.</p>
        <form method="GET" action="{url}">
          <input type="hidden" name="lang" value="en">
          <input name="signup_email" type="email" placeholder="example@example.com"
                 style="width:100%;" required>
          <p>Quick check: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
          <input type="hidden" name="captcha_n1" value="{n1}">
          <input type="hidden" name="captcha_n2" value="{n2}">
          <button type="submit">Sign up (free)</button>
        </form>
        <p class="muted">Your email is used only to deliver reports and your access link,
        and is never shared with third parties. By signing up you agree to our
        <a href="{url}?terms=1&amp;lang=en">Terms</a> and
        <a href="{url}?privacy=1&amp;lang=en">Privacy Policy</a>.
        <a href="{url}?guide=1&amp;lang=en">See how it works, with pictures →</a></p>
        </div>
        """
    return f"""
    <div class="notice-box">
    <p><strong>個別銘柄を監視したい方・メールで受け取りたい方へ</strong></p>
    <p>このページは常に「最新の上位銘柄」だけを表示します。登録（無料）すると、
    追加で以下が使えるようになります: 気になる銘柄を選んで監視・トレンド/モメンタム
    の変化を通知・この選定レポートを選んだ頻度でメール受信。</p>
    <form method="GET" action="{url}">
      <input name="signup_email" type="email" placeholder="example@example.com"
             style="width:100%;" required>
      <p>簡単な確認: {n1} + {n2} = <input name="captcha_answer" style="width:60px;" required></p>
      <input type="hidden" name="captcha_n1" value="{n1}">
      <input type="hidden" name="captcha_n2" value="{n2}">
      <button type="submit">申し込む（無料）</button>
    </form>
    <p class="muted">登録いただくメールアドレスはレポート配信・ログイン用リンクの
    送信にのみ使用し、第三者への提供は行いません。お申込みは
    <a href="{url}?terms=1">利用規約</a>・<a href="{url}?privacy=1">プライバシーポリシー</a>
    への同意とみなされます。
    <a href="{url}?guide=1">図でわかる使い方ガイドを見る →</a></p>
    </div>
    """


def _page(inner_html: str, lang: str, status: int = 200) -> dict:
    title = ("PegSignal — NASDAQ100 PEG screening results"
             if lang == LANG_EN else "PegSignal — NASDAQ100 PEGスクリーニング結果")
    footer = ('<a href="?">Home</a>' if lang == LANG_EN else '<a href="?">トップ</a>')
    body = f"""<html lang="{lang}"><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta charset="utf-8">
    <title>{title}</title>
    <style>{_PAGE_STYLE}</style></head>
    <body>
    <div class="card" id="top">
    {inner_html}
    </div>
    <p class="site-footer muted">{footer}</p>
    </body></html>"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def handler(event, context):
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or "/"
    headers = event.get("headers") or {}
    user_agent = (headers.get("user-agent") or headers.get("User-Agent") or "")[:120]
    print(f"[ACCESS] preview path={path!r} ua={user_agent!r}")

    lang = _detect_lang(event)
    sections = "".join(_mode_section_html(m, lang, REGISTER_URL) for m in _MODES)
    inner = (_intro_html(lang) + _disclaimer_html(lang) + _mode_nav_html(lang)
             + sections + _signup_pitch_html(lang, REGISTER_URL))
    return _page(inner, lang)
