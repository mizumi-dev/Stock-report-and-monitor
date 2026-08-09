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
- **対象指数(NASDAQ100/S&P500)もクエリパラメータで切り替え可能(2026-08-07追加)**。
  それまでこのページはNASDAQ100固定表示で、S&P500のスクリーニング結果は
  メール登録しないと見られなかった。register-stockのように owner_token に
  紐付けたDB設定にはせず、langと同じ「`?universe=SP500`を都度指定する」
  読み取り専用の方式にする(このページはログイン不要・書き込み一切なしの
  設計原則を維持するため)。切替リンクは_universe_nav_html()が生成する
"""
import base64
import html
import json
import os
import random
import re

import boto3

from config import SETTINGS_TABLE_DEFAULT, UNIVERSES, normalize_universe, selection_snapshot_key
from report import LANG_EN, LANG_JA, render_selection_cards, selection_subject, t

SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
REGISTER_URL = os.environ.get("REGISTER_URL", "")

dynamodb = boto3.resource("dynamodb")

SUPPORTED_LANGS = (LANG_JA, LANG_EN)
_MODES = ("SHORT", "MID", "LONG")
_ANCHOR = {"SHORT": "short", "MID": "mid", "LONG": "long"}


def _detect_lang(event) -> str:
    """?lang=クエリをX-Site-Langヘッダーより優先する(2026-08-08、?lang=enが
    無視される不具合の修正)。

    register-stock(app.pegsignal.com/en.pegsignal.com)はドメインごとに
    X-Site-Langヘッダーが変わる構成なのでヘッダー優先で問題ないが、
    landing-previewはpegsignal.com一本(en側のサブドメインを持たない)で、
    英語表示は`?lang=en`クエリのみで切り替える設計(docs/growth-drafts.md参照)。
    ところがpegsignal.comのCloudFrontはen.pegsignal.comとの対称性のため
    X-Site-Lang: jaを全リクエストに固定で付与している
    (deploy_patch_ja_lang_header.sh)。ヘッダーを先に見る実装だと、この
    固定ヘッダーが常に勝ってしまい?lang=enが黙って無視される。"""
    params = event.get("queryStringParameters") or {}
    value = (params.get("lang") or "").strip().lower()
    if value in SUPPORTED_LANGS:
        return value
    headers = event.get("headers") or {}
    value = (headers.get("x-site-lang") or "").strip().lower()
    return value if value in SUPPORTED_LANGS else LANG_JA


def _detect_universe(event) -> str:
    """対象指数(NASDAQ100/S&P500)の切り替え。ログイン不要・書き込み不要の
    読み取り専用ページなので、register-stockのようにowner_token紐付けのDB設定に
    せず、langと同じ「クエリパラメータで都度指定」方式にする(2026-08-07、
    S&P500をこのページからも見られるようにする要望を受けて追加)。"""
    params = event.get("queryStringParameters") or {}
    return normalize_universe(params.get("universe"))


def _get_selection_snapshot(mode: str, universe: str) -> dict | None:
    """daily-batchが保存した「本日の」選定スナップショットを読むだけ。
    新規スキャンは行わない(handler.pyのget_selection_snapshotと同じ設計)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": selection_snapshot_key(mode, universe)})
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
.universe-nav a.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);}
.notice-box{background:#eafbf0;border:1px solid #bbf0cf;border-radius:10px;
  padding:14px 16px;margin:22px 0;}
.btn-link{display:inline-block;background:var(--accent);color:var(--accent-ink)!important;
  border-radius:8px;padding:10px 18px;font-weight:600;text-decoration:none!important;}
.site-footer{max-width:560px;margin:16px auto 0;text-align:center;}
.disclaimer{border-left:3px solid var(--border);padding-left:10px;margin:0 0 18px;}
/* 旧トップページ(register-stockの申込み画面)にあった、文字だけに頼らない
   直感的な導入要素をこちらへ引き継いだもの(2026-08-07、apex統合に伴う移設)。
   クラス名・見た目はregister-stock側の_PAGE_STYLEと揃えている */
.trust-line{font-size:13px;color:var(--ink-muted);margin:0 0 16px;}
.steps{display:flex;gap:8px;margin:14px 0 18px;}
.step{flex:1;text-align:center;}
.step-num{display:flex;align-items:center;justify-content:center;width:26px;height:26px;
  margin:0 auto 4px;border-radius:50%;background:var(--accent);color:var(--accent-ink);
  font-size:13px;font-weight:700;}
.step-label{font-size:11.5px;color:var(--ink-muted);}
/* 「登録すると何が増えるのか」を一目で分かるようにする2列比較。
   このページ自体が既に選定結果を見せているため、登録の価値は「差分」を
   示すのが最も直感的という判断(2026-08-07、ユーザーからの指摘で追加) */
.compare{display:flex;gap:10px;margin:6px 0 16px;flex-wrap:wrap;}
.compare-col{flex:1 1 190px;border:1px solid var(--border);border-radius:10px;
  padding:12px 14px;background:var(--card);}
.compare-col.highlight{border-color:var(--accent);border-width:2px;padding:11px 13px;}
.compare-head{font-size:12.5px;font-weight:700;margin:0 0 8px;}
.compare-col.highlight .compare-head{color:var(--accent);}
.compare-list{list-style:none;margin:0;padding:0;}
.compare-list li{font-size:13px;padding:3px 0 3px 20px;position:relative;line-height:1.5;}
.compare-list li::before{content:"✓";position:absolute;left:0;color:var(--accent);font-weight:700;}
.compare-list li.off{color:var(--ink-muted);}
.compare-list li.off::before{content:"—";color:var(--ink-muted);font-weight:400;}
"""


def _intro_html(lang: str, universe: str, register_url: str) -> str:
    """このページ自体がメイン機能の1つであるため、メール配信機能への言及は
    意図的に冒頭に置かない(2026-08-06、ユーザーからのフィードバックで修正)。
    メール登録の案内は_signup_pitch_html()としてページ下部にのみ置く。

    ただし「登録不要でそのまま読める」ことと「スパムなし・1クリックで削除できる」
    ことは、訪問者が読み進めるかどうかを決める安心材料なので冒頭に置く
    (2026-08-07、apex統合で旧トップページから引き継いだtrust-line)。

    見出し・本文の対象指数名はuniverseで動的に切り替える(2026-08-07、
    NASDAQ100固定表記だとS&P500選択時に誤認を招くため)。

    使い方ガイドへのリンクは、当初ページ最下部のsignup pitch内にしか無く、
    先にランキングの読み方を知りたい訪問者が発見しづらかった。冒頭の
    trust-lineの直後に移動する(2026-08-07、ユーザーからの指摘で追加)。
    register-stock(app.pegsignal.com)のガイドページをそのまま使う"""
    guide_url = html.escape(f"{register_url}?guide=1" + ("&lang=en" if lang == LANG_EN else ""))
    preset = UNIVERSES[universe]
    if lang == LANG_EN:
        label = preset["label_en"]
        return f"""
        <p class="eyebrow">PegSignal (free, indie-built)</p>
        <h1>PEG screening results ({html.escape(label)})</h1>
        <p>We rank {html.escape(label)} growth stocks by PEG ratio (P/E ratio ÷ earnings growth rate)
        across three time horizons — short-term, mid-term, and long-term. Jump to one of
        the three below, or just scroll through all of them.</p>
        <p class="trust-line">👀 No signup needed to read this page ・ 🔒 If you do sign up,
        your email is used only for report delivery, no spam, delete anytime in one click.</p>
        <p class="muted"><a href="{guide_url}">See how it works, with pictures →</a></p>
        """
    label = preset["label"]
    return f"""
    <p class="eyebrow">PegSignal（無料・個人開発）</p>
    <h1>PEGスクリーニング結果（{html.escape(label)}）</h1>
    <p>{html.escape(label)}構成銘柄の中から、PEGレシオ（株価収益率÷利益成長率）が低い成長株を、
    短期・中期・長期の3つの期間でランキングしています。
    下のリンクで見たい期間へ移動するか、そのまま3つとも順にご覧ください。</p>
    <p class="trust-line">👀 このページは登録不要でそのまま読めます ・
    🔒 登録する場合もメールはレポート配信にのみ使用・スパムなし・いつでも1クリックで削除</p>
    <p class="muted"><a href="{guide_url}">図でわかる使い方ガイドを見る →</a></p>
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


def _universe_nav_html(lang: str, universe: str) -> str:
    """対象指数の切り替え。langと同様クエリパラメータでの別ページ遷移方式
    (mode-navのようなページ内アンカーではなく、ユニバースごとに全く異なる
    銘柄・スナップショットを読むため、素のリンクで作り直す必要がある)。
    現在選択中のlangはクエリに引き継ぎ、選択中のuniverseは強調表示する。"""
    lang_qs = f"&lang={lang}" if lang != LANG_JA else ""
    links = []
    for key, preset in UNIVERSES.items():
        label = preset["label_en"] if lang == LANG_EN else preset["label"]
        cls = ' class="active"' if key == universe else ""
        links.append(f'<a href="?universe={key}{lang_qs}"{cls}>{html.escape(label)}</a>')
    return f'<div class="mode-nav universe-nav">{"".join(links)}</div>'


def _back_to_top_html(lang: str) -> str:
    label = "↑ Back to top" if lang == LANG_EN else "↑ トップに戻る"
    return f'<p class="muted"><a href="#top">{label}</a></p>'


def _mode_section_html(mode: str, lang: str, universe: str, register_base_url: str) -> str:
    snapshot = _get_selection_snapshot(mode, universe)
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
        <h2 style="border-top:none;padding-top:0;margin-top:0;">What you get by signing up</h2>
        <div class="compare">
          <div class="compare-col">
            <p class="compare-head">👀 This page (no signup)</p>
            <ul class="compare-list">
              <li>Read the current rankings</li>
              <li class="off">Track your own stocks</li>
              <li class="off">Get alerted when they change</li>
            </ul>
          </div>
          <div class="compare-col highlight">
            <p class="compare-head">✉️ Signed up (free)</p>
            <ul class="compare-list">
              <li>Read the current rankings</li>
              <li><strong>Pick stocks to track</strong></li>
              <li><strong>Trend / RSI / volume alerts by email</strong></li>
            </ul>
          </div>
        </div>
        <div class="steps">
          <div class="step"><div class="step-num">1</div><div class="step-label">Enter your email</div></div>
          <div class="step"><div class="step-num">2</div><div class="step-label">Pick stocks</div></div>
          <div class="step"><div class="step-num">3</div><div class="step-label">Reports arrive</div></div>
        </div>
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
        <a href="{url}?privacy=1&amp;lang=en">Privacy Policy</a>.</p>
        </div>
        """
    return f"""
    <div class="notice-box">
    <h2 style="border-top:none;padding-top:0;margin-top:0;">登録すると何が増えるか</h2>
    <div class="compare">
      <div class="compare-col">
        <p class="compare-head">👀 このページ（登録不要）</p>
        <ul class="compare-list">
          <li>今のランキングを見る</li>
          <li class="off">自分の銘柄を監視</li>
          <li class="off">変化をメールで通知</li>
        </ul>
      </div>
      <div class="compare-col highlight">
        <p class="compare-head">✉️ 登録すると（無料）</p>
        <ul class="compare-list">
          <li>今のランキングを見る</li>
          <li><strong>気になる銘柄を監視</strong></li>
          <li><strong>トレンド・RSI・出来高の変化をメールで通知</strong></li>
        </ul>
      </div>
    </div>
    <div class="steps">
      <div class="step"><div class="step-num">1</div><div class="step-label">メール入力</div></div>
      <div class="step"><div class="step-num">2</div><div class="step-label">銘柄を選ぶ</div></div>
      <div class="step"><div class="step-num">3</div><div class="step-label">レポート受信</div></div>
    </div>
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
    への同意とみなされます。</p>
    </div>
    """


# X/note等にリンクを貼った際のシェアカード用。今まで一切無く(空リンクプレビュー
# になっていた)、集客チャネル展開の前提として2026-08-08に追加。og:imageは
# OG_IMAGE_URL環境変数が設定されている場合のみ出力する(画像アセットが未確定
# なため、無ければテキストのみのカードになる。register-stock側のfaviconのような
# data URIはSNS側のクローラーが取得できないため使えない)
_OG_DESCRIPTION = {
    LANG_JA: (
        "NASDAQ100・S&P500構成銘柄からPEGレシオが低い成長株を選定し、"
        "短期・中期・長期でランキング。登録不要でそのまま見られる無料サービスです。"
    ),
    LANG_EN: (
        "A free service that screens NASDAQ100 / S&P 500 constituents for growth stocks with a low "
        "PEG ratio, ranked across short/mid/long-term horizons — no signup needed to read it."
    ),
}
# og:url・canonical・og:imageに使う公開オリジン。
#
# **リクエストのHostヘッダーから組み立ててはいけない**(2026-08-08、noteの埋め込みで
# シェアカードに画像が出ない不具合の原因調査で判明し修正)。このサービスの
# CloudFrontディストリビューションは全てAWS管理のオリジンリクエストポリシー
# "AllViewerExceptHostHeader"(ID b689b0a8-...)を使っており、名前の通り
# **Hostヘッダーだけはオリジンへ転送しない**(Lambda Function URLをオリジンに
# する場合、Hostはオリジン自身のドメインでなければならないため、この設定自体は
# 正しい。deploy_domain.sh等を参照)。そのためCloudFront経由でこのLambdaが
# 受け取るHostは常に生のFunction URLのホスト名
# (xxxxx.lambda-url.us-east-1.on.aws)であり、pegsignal.com ではない。
# Hostから組み立てると、シェアカードもcanonicalも生Lambda URLを指してしまう
# (canonicalが生URLを指すのはSEO上さらに有害)。
#
# したがって公開URLは明示的に持つ。SITE_URL環境変数で上書きできるようにして
# あるが、既定値のままでも本番(pegsignal.com)は正しく動く。
PUBLIC_ORIGIN = os.environ.get("SITE_URL", "https://pegsignal.com").rstrip("/")

# シェア用バナー画像(1200x630、Geminiで生成・2026-08-08追加)。日本語版・英語版で
# バナー内の文言が異なる(和文合成/欧文合成)ため、言語ごとに別ファイル・別ルートを
# 持つ(2026-08-08、英語ページに日本語バナーが出ていた不具合を修正)。deploy.yml・
# deploy_preview.sh・deploy_dev.shがhandler.pyと同じディレクトリからzipへ同梱する
# ため、__file__基準でこのファイル自身の隣を見れば本番・開発どちらのデプロイでも
# 同じパスで見つかる。画像はこのLambda自身が_OG_IMAGE_ROUTESで配信する
_OG_IMAGE_ROUTES = {LANG_JA: "/og-image.jpg", LANG_EN: "/og-image-en.jpg"}
_OG_IMAGE_FILENAMES = {LANG_JA: "og-image.jpg", LANG_EN: "og-image-en.jpg"}
OG_IMAGE_URL_OVERRIDE = os.environ.get("OG_IMAGE_URL", "")


def _load_og_image_bytes(lang: str) -> bytes:
    path = os.path.join(os.path.dirname(__file__), _OG_IMAGE_FILENAMES[lang])
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        print(f"[WARN] シェア用画像({lang})の読み込みに失敗: {e}")
        return b""


_OG_IMAGE_BYTES = {lang: _load_og_image_bytes(lang) for lang in SUPPORTED_LANGS}


def _og_image_response(lang: str) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "image/jpeg", "Cache-Control": "public, max-age=86400"},
        "body": base64.b64encode(_OG_IMAGE_BYTES[lang]).decode("ascii"),
        "isBase64Encoded": True,
    }


def _resolve_og_image_url(lang: str) -> str:
    if OG_IMAGE_URL_OVERRIDE:
        return OG_IMAGE_URL_OVERRIDE
    if _OG_IMAGE_BYTES.get(lang):
        return f"{PUBLIC_ORIGIN}{_OG_IMAGE_ROUTES[lang]}"
    return ""


# ホーム画面へ追加(PWA/apple-touch-icon)対応(2026-08-08追加)。noteの記事で
# 「メール登録が不安ならURLをホーム画面に保存」と案内する予定だったが、apex
# (pegsignal.com)側にはマニフェスト・アイコン指定が一切無く、追加すると
# ページのスクリーンショットが代用アイコンになってしまうことが判明したため
# 対応した。register-stock側は独自SVGを使うが、こちらは外部展開用に既に
# 用意済みのPEGモノグラムのPNG(docs/assets/pegsignal-icon-square.png)を
# 流用する(バナー画像と同じくicon.pngとしてhandler.pyの隣に同梱・配信)
_ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.png")
_ICON_ROUTE = "/icon.png"
_MANIFEST_ROUTE = "/manifest.json"
_APP_SHORT_NAME = {LANG_JA: "PegSignal", LANG_EN: "PegSignal"}
_APP_NAME = {LANG_JA: "PegSignal（PEGスクリーニング）", LANG_EN: "PegSignal PEG screening"}


def _load_icon_bytes() -> bytes:
    try:
        with open(_ICON_PATH, "rb") as f:
            return f.read()
    except OSError as e:
        print(f"[WARN] アイコン画像の読み込みに失敗: {e}")
        return b""


_ICON_BYTES = _load_icon_bytes()


def _icon_response() -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "image/png", "Cache-Control": "public, max-age=86400"},
        "body": base64.b64encode(_ICON_BYTES).decode("ascii"),
        "isBase64Encoded": True,
    }


def _manifest_response(lang: str) -> dict:
    manifest = {
        "name": _APP_NAME[lang],
        "short_name": _APP_SHORT_NAME[lang],
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f4f6f9",
        "theme_color": "#0f172a",
        "icons": [
            {"src": f"{PUBLIC_ORIGIN}{_ICON_ROUTE}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/manifest+json; charset=utf-8"},
        "body": json.dumps(manifest, ensure_ascii=False),
    }


def _page(inner_html: str, lang: str, universe: str, status: int = 200,
          canonical_url: str = "", og_image_url: str = "") -> dict:
    preset = UNIVERSES[universe]
    title = (f"PegSignal — {preset['label_en']} PEG screening results"
             if lang == LANG_EN else f"PegSignal — {preset['label']} PEGスクリーニング結果")
    description = _OG_DESCRIPTION[lang]
    footer = ('<a href="?">Home</a>' if lang == LANG_EN else '<a href="?">トップ</a>')
    og_tags = [
        f'<meta name="description" content="{html.escape(description)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(description)}">',
        f'<meta name="twitter:title" content="{html.escape(title)}">',
        f'<meta name="twitter:description" content="{html.escape(description)}">',
    ]
    if canonical_url:
        og_tags.append(f'<meta property="og:url" content="{html.escape(canonical_url)}">')
        og_tags.append(f'<link rel="canonical" href="{html.escape(canonical_url)}">')
    if og_image_url:
        og_tags.append(f'<meta property="og:image" content="{html.escape(og_image_url)}">')
        og_tags.append('<meta name="twitter:card" content="summary_large_image">')
    else:
        og_tags.append('<meta name="twitter:card" content="summary">')
    if _ICON_BYTES:
        icon_url = f"{PUBLIC_ORIGIN}{_ICON_ROUTE}"
        og_tags.append(f'<link rel="apple-touch-icon" href="{html.escape(icon_url)}">')
        og_tags.append(f'<link rel="icon" href="{html.escape(icon_url)}">')
        og_tags.append(f'<link rel="manifest" href="{_MANIFEST_ROUTE}?lang={lang}">')
        og_tags.append('<meta name="theme-color" content="#0f172a">')
        og_tags.append('<meta name="apple-mobile-web-app-capable" content="yes">')
        og_tags.append(f'<meta name="apple-mobile-web-app-title" content="{html.escape(_APP_SHORT_NAME[lang])}">')
    body = f"""<html lang="{lang}"><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta charset="utf-8">
    <title>{title}</title>
    {"".join(og_tags)}
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


# register-stock側(lambda_register/handler.py)の_is_probable_bot/_access_log_fieldsと
# 同じ規約(bot判定・?src=の流入元ラベル)をここにも揃える(2026-08-08、集客チャネル
# 展開に向けてlanding-preview側にも計測を追加)。両Lambdaはhandler.py自体を共有しない
# 設計(CLAUDE.md参照)のため、このファイル内に複製する
_BOT_UA_SUBSTRINGS = (
    "bot", "spider", "crawl", "scan", "curl/", "wget/", "python-requests",
    "go-http-client", "scrapy", "java/", "libwww", "httpclient", "okhttp",
    "postman", "insomnia", "sniffer", "nmap", "nikto", "masscan", "zgrab",
)


def _is_probable_bot(user_agent: str) -> bool:
    return any(s in user_agent.lower() for s in _BOT_UA_SUBSTRINGS)


def handler(event, context):
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path") or "/"
    headers = event.get("headers") or {}
    params = event.get("queryStringParameters") or {}
    if path in _OG_IMAGE_ROUTES.values():
        image_lang = LANG_EN if path == _OG_IMAGE_ROUTES[LANG_EN] else LANG_JA
        if _OG_IMAGE_BYTES.get(image_lang):
            return _og_image_response(image_lang)
    if path == _ICON_ROUTE and _ICON_BYTES:
        return _icon_response()
    if path == _MANIFEST_ROUTE:
        manifest_lang = params.get("lang") if params.get("lang") in SUPPORTED_LANGS else LANG_JA
        return _manifest_response(manifest_lang)
    user_agent = (headers.get("user-agent") or headers.get("User-Agent") or "")[:120]
    referer_raw = headers.get("referer") or headers.get("Referer") or ""
    referer = referer_raw.replace("\n", " ").replace("\r", " ")[:200]
    # 告知文の?src=をそのまま流入元ラベルとして記録する(例: ?src=note, ?src=x, ?src=hn)。
    # register-stock側と同じ規約(任意の英数字・アンダースコア・ハイフンのみ許可)
    src_raw = (params.get("src") or "").strip()
    src = src_raw if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", src_raw) else ""
    print(f"[ACCESS] preview path={path!r} bot={_is_probable_bot(user_agent)} "
          f"src={src!r} referer={referer!r} ua={user_agent!r}")

    lang = _detect_lang(event)
    universe = _detect_universe(event)
    sections = "".join(_mode_section_html(m, lang, universe, REGISTER_URL) for m in _MODES)
    inner = (_intro_html(lang, universe, REGISTER_URL) + _disclaimer_html(lang) + _universe_nav_html(lang, universe)
             + _mode_nav_html(lang) + sections + _signup_pitch_html(lang, REGISTER_URL))
    # og:url/canonicalはsrc等のトラッキングクエリを含めない(シェア・インデックス
    # されるURLをクリーンに保つため)。ホスト名はHostヘッダーではなく
    # PUBLIC_ORIGINを使う(理由はPUBLIC_ORIGINの定義箇所のコメントを参照)
    canonical_url = f"{PUBLIC_ORIGIN}{path}"
    og_image_url = _resolve_og_image_url(lang)
    return _page(inner, lang, universe, canonical_url=canonical_url, og_image_url=og_image_url)
