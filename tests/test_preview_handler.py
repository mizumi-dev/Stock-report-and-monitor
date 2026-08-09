"""
landing-preview Lambda(mnt/user-data/outputs/aws_deploy/lambda_preview/handler.py)の
コンポーネントテスト。register-stockとは別の独立したLambdaで、メールアドレスを
登録したくない訪問者向けに、3モード分の選定レポートを登録不要で閲覧できる
読み取り専用ページを提供する(2026-08-06追加)。

daily-batch/register-stockと同名の"handler.py"があるため、モジュール名衝突を
避けるべくimportlib.util.spec_from_file_locationで明示的な別名としてロードする。
"""
import importlib.util
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

from fake_dynamodb import FakeDynamoDBResource

PREVIEW_HANDLER_PATH = (
    Path(__file__).resolve().parent.parent
    / "mnt/user-data/outputs/aws_deploy/lambda_preview/handler.py"
)


@pytest.fixture
def ph():
    """テストごとに新しいモジュールインスタンスと空のfake DynamoDBを用意する"""
    spec = importlib.util.spec_from_file_location("preview_handler", PREVIEW_HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["preview_handler"] = module
    with mock.patch("boto3.resource"):
        spec.loader.exec_module(module)

    fake = FakeDynamoDBResource({"app_settings": ("key", None)})
    module.dynamodb = fake
    module.REGISTER_URL = "https://pegsignal.com/"
    return module


def _put_snapshot(ph, mode, ranked=None, signals_map=None, as_of="2026-08-06", universe="NASDAQ100"):
    ranked = ranked if ranked is not None else [
        {"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2,
         "earnings_growth": 0.3, "market_cap": 1e12},
    ]
    signals_map = signals_map if signals_map is not None else {
        "NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False},
    }
    key = f"selection_snapshot#{mode}"
    if universe != "NASDAQ100":
        key += f"#{universe}"
    ph.dynamodb.Table("app_settings").put_item(Item={
        "key": key,
        "ranked": ranked, "signals_map": signals_map, "as_of_date": as_of,
    })


def _event(_headers=None, **params):
    event = {"queryStringParameters": {k: v for k, v in params.items() if v is not None}}
    if _headers:
        event["headers"] = _headers
    return event


class TestPreviewPage:
    def test_page_is_accessible_without_any_params(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(), None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "text/html; charset=utf-8"

    def test_all_three_modes_render_in_a_single_response(self, ph):
        """JSでの出し分けではなく、3モード分すべてが1回のレスポンスに含まれること
        (クローラーがJSを実行しなくても全内容を取得できるようにするため)"""
        _put_snapshot(ph, "SHORT", ranked=[{"ticker": "AAA", "company_name": "A社", "peg": 1.0,
                                            "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"AAA": {"buy_trend": True}})
        _put_snapshot(ph, "MID", ranked=[{"ticker": "BBB", "company_name": "B社", "peg": 1.0,
                                          "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"BBB": {"buy_trend": True}})
        _put_snapshot(ph, "LONG", ranked=[{"ticker": "CCC", "company_name": "C社", "peg": 1.0,
                                           "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"CCC": {"buy_trend": True}})
        body = ph.handler(_event(), None)["body"]
        assert "AAA" in body and "BBB" in body and "CCC" in body

    def test_mode_anchors_exist_for_in_page_navigation(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert 'id="short"' in body
        assert 'id="mid"' in body
        assert 'id="long"' in body
        assert 'href="#short"' in body
        assert 'href="#mid"' in body
        assert 'href="#long"' in body

    def test_missing_snapshot_does_not_crash(self, ph):
        """サービス開始直後などバッチが一度も走っていないモードがあっても502にしない"""
        r = ph.handler(_event(), None)
        assert r["statusCode"] == 200

    def test_shows_as_of_date_from_snapshot(self, ph):
        _put_snapshot(ph, "SHORT", as_of="2026-08-05")
        body = ph.handler(_event(), None)["body"]
        assert "2026-08-05時点のデータです" in body

    def test_no_per_ticker_watchlist_link_without_registration(self, ph):
        """未登録の訪問者にはトークンが無いため、個別銘柄の「監視リストに登録」
        リンクは出さない(report.render_selection_cardsのregister_token=None動作)"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert "監視リストに登録" not in body

    def test_signup_form_submits_to_production_register_url(self, ph):
        """実際の登録処理は複製せず、フォームのaction属性で本番register-stock
        へ遷移させるだけ(2026-08-07、埋め込みフォームに変更)"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert '<form method="GET" action="https://pegsignal.com/">' in body

    def test_embedded_signup_form_has_matching_email_and_captcha_fields(self, ph):
        """register-stock側の検証(signup_email・captcha_n1・captcha_n2・
        captcha_answer)と同じフィールド名を使う必要がある。n1+n2=captcha_answerの
        関係も、register-stockが期待する形と一致させる"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert 'name="signup_email"' in body
        m1 = re.search(r'name="captcha_n1" value="(\d+)"', body)
        m2 = re.search(r'name="captcha_n2" value="(\d+)"', body)
        assert m1 and m2
        n1, n2 = int(m1.group(1)), int(m2.group(1))
        assert 1 <= n1 <= 9
        assert 1 <= n2 <= 9

    def test_embedded_signup_form_english_includes_lang_param(self, ph):
        """app.pegsignal.comにはX-Site-Langヘッダーが無いため、英語版フォームは
        ?lang=enを明示的に送信して言語を維持する必要がある"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(lang="en"), None)["body"]
        assert '<input type="hidden" name="lang" value="en">' in body

    def test_embedded_signup_form_generates_fresh_captcha_each_request(self, ph):
        """毎回同じ数字だと固定パターンでbotに突破されうるため、リクエストごとに
        乱数を生成し直すこと(register-stockの既存フォームと同じ方針)"""
        _put_snapshot(ph, "SHORT")
        n1s = set()
        for _ in range(20):
            body = ph.handler(_event(), None)["body"]
            m = re.search(r'name="captcha_n1" value="(\d+)"', body)
            n1s.add(m.group(1))
        assert len(n1s) > 1

    def test_h1_does_not_claim_today_since_frequency_varies_by_period(self, ph):
        """短期=毎営業日・中期=週1・長期=月1と更新頻度が期間で異なるため、
        ページ全体の見出しに「今日の」と断定すると長期・中期の内容と食い違う"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert "今日のPEG" not in body
        assert "PEGスクリーニング結果（NASDAQ100）" in body

    def test_english_h1_does_not_claim_today(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert "Today's PEG screening results (NASDAQ100)" not in body

    def test_intro_uses_period_not_unexplained_mode_term(self, ph):
        """「モード」という言葉を説明抜きに使うと伝わらない、との指摘を受けて
        「期間」に統一した"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        intro = body.split('<h2 id="short"')[0]
        assert "モード" not in intro
        assert "期間" in intro

    def test_each_mode_section_has_a_back_to_top_link(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert body.count('href="#top"') == 3
        assert "トップに戻る" in body

    def test_english_back_to_top_link_present(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert body.count('href="#top"') == 3
        assert "Back to top" in body

    def test_page_has_top_anchor_for_back_to_top_links_to_target(self, ph):
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert 'id="top"' in body

    def test_broker_and_ai_prompt_links_work_without_token(self, ph):
        """証券会社リンク・AIプロンプトリンクはトークン不要で機能する既存の設計を
        プレビューページでも引き継いでいること"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert "moomoo.com" in body
        assert "ai_prompt=1" in body


class TestPreviewLanguage:
    def test_english_when_lang_param_is_en(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(lang="en"), None)
        assert '<html lang="en"' in r["body"]
        assert "Sign up (free)" in r["body"]

    def test_english_when_site_lang_header_is_en(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(_headers={"x-site-lang": "en"}), None)
        assert '<html lang="en"' in r["body"]

    def test_japanese_by_default(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(), None)
        assert '<html lang="ja"' in r["body"]

    def test_no_japanese_leaks_into_english_page(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        visible = re.sub(r"<style>.*?</style>", "", body, flags=re.S)
        assert not re.search(r"[ぁ-んァ-ン一-龥]", visible)

    def test_lang_query_wins_over_fixed_x_site_lang_ja_header(self, ph):
        """pegsignal.comのCloudFrontはen.pegsignal.comとの対称性のため
        X-Site-Lang: jaを全リクエストに固定で付与している
        (deploy_patch_ja_lang_header.sh)。landing-previewはpegsignal.com
        一本(en側のサブドメインを持たない)で、英語表示は?lang=enクエリのみで
        切り替える設計のため、このヘッダーに負けて?lang=enが無視されると
        英語版が永久に表示できなくなる(2026-08-08に発覚した実際の不具合)。"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event({"x-site-lang": "ja"}, lang="en"), None)
        assert '<html lang="en"' in r["body"]

    def test_english_for_visitor_geolocated_outside_japan(self, ph):
        """CloudFront-Viewer-Countryが日本以外なら、明示指定が無くても英語を既定にする
        (2026-08-07、海外流入向けに追加)。"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(_headers={"cloudfront-viewer-country": "US"}), None)
        assert '<html lang="en"' in r["body"]

    def test_japanese_for_visitor_geolocated_in_japan(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(_headers={"cloudfront-viewer-country": "JP"}), None)
        assert '<html lang="ja"' in r["body"]

    def test_japanese_when_no_geolocation_header_present(self, ph):
        """ヘッダー自体が無い場合(Function URL直叩き・開発環境等)は
        従来通り日本語を既定とする(地理判定によって既定挙動を壊さない)。"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(), None)
        assert '<html lang="ja"' in r["body"]

    def test_explicit_lang_param_overrides_geolocation(self, ph):
        """?lang=はCloudFront-Viewer-Countryより優先される(明示指定が最優先)。"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(lang="ja", _headers={"cloudfront-viewer-country": "US"}), None)
        assert '<html lang="ja"' in r["body"]

    def test_geolocation_overrides_fixed_x_site_lang_ja_header(self, ph):
        """pegsignal.comはX-Site-Lang: jaを常時固定で付与するため(上記の?lang=不具合と
        同じ原因)、ヘッダーを地理判定より先に見ると地理判定が永久に効かなくなる。
        地理判定をX-Site-Langヘッダーより優先することで、海外からのアクセスでも
        英語を既定にできる。"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(_headers={"x-site-lang": "ja", "cloudfront-viewer-country": "US"}), None)
        assert '<html lang="en"' in r["body"]

    def test_geolocation_country_code_is_case_insensitive(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        r = ph.handler(_event(_headers={"cloudfront-viewer-country": "jp"}), None)
        assert '<html lang="ja"' in r["body"]

class TestPreviewUniverse:
    """対象指数(NASDAQ100/S&P500)の切り替え(2026-08-07追加)。langと同様、
    owner_token不要のクエリパラメータ方式で、書き込みは一切発生しない。"""

    def test_defaults_to_nasdaq100(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m, ranked=[{"ticker": "NDX", "company_name": "N社", "peg": 1.0,
                                          "earnings_growth": 0.3, "market_cap": 1}],
                          signals_map={"NDX": {"buy_trend": True}})
        body = ph.handler(_event(), None)["body"]
        assert "NASDAQ100" in body
        assert "NDX" in body

    def test_universe_param_switches_to_sp500_snapshot(self, ph):
        _put_snapshot(ph, "SHORT", ranked=[{"ticker": "NDX", "company_name": "N社", "peg": 1.0,
                                            "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"NDX": {"buy_trend": True}}, universe="NASDAQ100")
        _put_snapshot(ph, "SHORT", ranked=[{"ticker": "SPX", "company_name": "S社", "peg": 1.0,
                                            "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"SPX": {"buy_trend": True}}, universe="SP500")
        body = ph.handler(_event(universe="SP500"), None)["body"]
        assert "SPX" in body
        assert "NDX" not in body

    def test_unknown_universe_falls_back_to_default(self, ph):
        _put_snapshot(ph, "SHORT", ranked=[{"ticker": "NDX", "company_name": "N社", "peg": 1.0,
                                            "earnings_growth": 0.3, "market_cap": 1}],
                      signals_map={"NDX": {"buy_trend": True}})
        body = ph.handler(_event(universe="NIKKEI225"), None)["body"]
        assert "NDX" in body

    def test_universe_switcher_links_exist_for_both_universes(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert 'href="?universe=NASDAQ100"' in body
        assert 'href="?universe=SP500"' in body

    def test_selected_universe_link_is_marked_active(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m, universe="SP500")
        body = ph.handler(_event(universe="SP500"), None)["body"]
        assert '<a href="?universe=SP500" class="active">' in body

    def test_universe_switcher_preserves_selected_language(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert 'href="?universe=SP500&lang=en"' in body

    def test_heading_reflects_sp500_when_selected(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m, universe="SP500")
        body = ph.handler(_event(universe="SP500"), None)["body"]
        assert "S&amp;P500" in body or "S&P500" in body
        assert "NASDAQ100" not in body.split("<h1>")[1].split("</h1>")[0]

    def test_page_title_reflects_selected_universe(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m, universe="SP500")
        body = ph.handler(_event(universe="SP500"), None)["body"]
        title = body.split("<title>")[1].split("</title>")[0]
        assert "S&P500" in title


class TestPreviewIntroWording:
    """導入部(_intro_html)ではメール配信機能をメインの謳い文句にしない
    (2026-08-06、ユーザーからのフィードバックにより修正)。メール登録の
    案内・比較表はページ下部の_signup_pitch_html()に置く。

    2026-08-07: apex統合後、「登録不要で読める」という安心材料の一部として
    trust-line内でメールの取り扱い(スパムなし・1クリック削除)に触れるように
    なった。これは「メール機能が主目的である」という誤解を招く見出し・導入文と
    は別物なので、見出し(h1)・最初の段落ではメールに触れないことだけを検証する。"""

    def test_h1_and_first_paragraph_do_not_mention_email_in_japanese(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        # h1の次の</p>までが最初の段落(trust-lineより前)
        head = body.split("<h1>")[1].split("</p>")[0]
        assert "メール" not in head

    def test_h1_and_first_paragraph_do_not_mention_email_in_english(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        head = body.split("<h1>")[1].split("</p>")[0]
        assert "email" not in head.lower()

    def test_intro_reassures_no_signup_needed(self, ph):
        """登録不要で読めることは安心材料として明示する(2026-08-07追加)"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert "登録不要" in body

    def test_email_pitch_still_present_further_down_the_page(self, ph):
        """メール機能への言及自体を削除したわけではなく、位置を下部に移しただけ"""
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert "メール" in body

    def test_page_avoids_value_judgment_wording(self, ph):
        """「割安」等の価値判断を示す表現は使わず、PEGレシオという客観的指標に
        基づく機械的な選定であることを一貫させる(金商法リスク低減の方針)"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert "割安" not in body

    def test_english_page_avoids_value_judgment_wording(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert "undervalued" not in body.lower()
        assert "cheap" not in body.lower()


class TestPreviewGuideLink:
    """使い方ガイドへのリンクは当初ページ最下部のsignup pitch内にしか無く、
    先にランキングの読み方を知りたい訪問者が発見しづらかった。冒頭の
    trust-lineの直後に移動し、下部の重複は削除する(2026-08-07追加)。"""

    def test_guide_link_appears_before_the_rankings(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert body.index("使い方ガイドを見る") < body.index('<h2 id="short"')

    def test_guide_link_points_to_register_url_with_guide_param(self, ph):
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert f'href="{ph.REGISTER_URL}?guide=1"' in body

    def test_guide_link_not_duplicated_at_bottom(self, ph):
        _put_snapshot(ph, "SHORT")
        body = ph.handler(_event(), None)["body"]
        assert body.count("guide=1") == 1

    def test_english_guide_link_appears_before_the_rankings(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert body.index("See how it works") < body.index('<h2 id="short"')
        assert f'href="{ph.REGISTER_URL}?guide=1&amp;lang=en"' in body


class TestPreviewDisclaimer:
    """このページは登録不要で誰でも読める「サービスの顔」であり、銘柄ランキングを
    そのまま公開表示する。メール本文(build_selection_report)には免責があるが、
    このページが使うrender_selection_cardsには含まれないため、当初このページには
    免責が一切表示されていなかった(2026-08-07に発覚)。金商法(投資助言業)に
    該当しないための根拠として、ランキングより前の位置に表示する。"""

    def test_disclaimer_is_shown_in_japanese(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert "売買を推奨・勧誘するものではありません" in body

    def test_disclaimer_is_shown_in_english(self, ph):
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(lang="en"), None)["body"]
        assert "does not recommend or solicit" in body

    def test_disclaimer_appears_before_the_rankings(self, ph):
        """スクロールしないと見えない位置ではなく、ランキングを見る前に目に入ること"""
        for m in ("SHORT", "MID", "LONG"):
            _put_snapshot(ph, m)
        body = ph.handler(_event(), None)["body"]
        assert body.index("売買を推奨・勧誘するものではありません") < body.index('<h2 id="short"')

    def test_disclaimer_shown_even_when_no_snapshot_exists(self, ph):
        """スナップショット未生成でも免責は出す(データが無いこととは無関係のため)"""
        body = ph.handler(_event(), None)["body"]
        assert "売買を推奨・勧誘するものではありません" in body


class TestPreviewReadOnly:
    """このLambdaは読み取り専用であることが設計上の要件(CLAUDE.mdの
    「バッチが計算し正本を保有、ボタン押下は読み取り専用」の原則をこの新画面にも適用)。
    書き込み系のDynamoDB APIを一切呼ばないことをfakeの呼び出し履歴で確認する。"""

    def test_never_calls_put_item_or_delete_item(self, ph):
        _put_snapshot(ph, "SHORT")
        table = ph.dynamodb.Table("app_settings")
        with mock.patch.object(table, "put_item", wraps=table.put_item) as put_spy, \
             mock.patch.object(table, "delete_item", wraps=table.delete_item) as del_spy:
            ph.handler(_event(), None)
            put_spy.assert_not_called()
            del_spy.assert_not_called()


class TestPreviewShareMetaTags:
    """SNS(X/note等)にリンクを貼った際にタイトル・説明が空のカードになっていた問題への
    対応(2026-08-08)。og:title/og:description/twitter:cardが最低限出ることを確認する。

    og:url・canonical・og:imageのホスト名は**HostヘッダーではなくPUBLIC_ORIGINを
    使う**。CloudFrontがAllViewerExceptHostHeaderポリシーでHostを転送しないため、
    Hostから組み立てると生のLambda Function URLを指してしまうため
    (handler.pyのPUBLIC_ORIGIN定義箇所のコメント参照)。

    og:imageは言語ごとに別バナー(日本語=/og-image.jpg、英語=/og-image-en.jpg)を
    出す(2026-08-08、英語ページに日本語文言のバナーが出ていた不具合の修正)。"""

    def test_description_and_og_tags_present(self, ph):
        body = ph.handler(_event(), None)["body"]
        assert '<meta name="description" content="' in body
        assert '<meta property="og:title" content="PegSignal' in body
        assert '<meta property="og:description" content="' in body
        assert '<meta name="twitter:title" content="' in body

    def test_ja_page_uses_ja_banner(self, ph):
        """CloudFront経由ではHostが生のLambda URLになるため、それを使ってはいけない"""
        body = ph.handler(
            _event({"host": "abc123.lambda-url.us-east-1.on.aws"}), None
        )["body"]
        assert '<meta property="og:image" content="https://pegsignal.com/og-image.jpg">' in body
        assert '<meta name="twitter:card" content="summary_large_image">' in body
        assert "lambda-url" not in body

    def test_en_page_uses_en_banner_not_ja(self, ph):
        body = ph.handler(_event(lang="en"), None)["body"]
        assert '<meta property="og:image" content="https://pegsignal.com/og-image-en.jpg">' in body

    def test_og_image_emitted_even_without_host_header(self, ph):
        body = ph.handler(_event(), None)["body"]
        assert '<meta property="og:image" content="https://pegsignal.com/og-image.jpg">' in body

    def test_no_og_image_tag_when_banner_missing(self, ph):
        ph._OG_IMAGE_BYTES = {"ja": b"", "en": b""}
        body = ph.handler(_event(), None)["body"]
        assert "og:image" not in body
        assert '<meta name="twitter:card" content="summary">' in body

    def test_og_image_url_override_takes_precedence(self, ph):
        ph.OG_IMAGE_URL_OVERRIDE = "https://cdn.example.com/banner.png"
        body = ph.handler(_event(), None)["body"]
        assert '<meta property="og:image" content="https://cdn.example.com/banner.png">' in body

    def test_canonical_and_og_url_use_public_origin_and_drop_src(self, ph):
        body = ph.handler(
            _event({"host": "abc123.lambda-url.us-east-1.on.aws"}, src="note"), None
        )["body"]
        assert '<meta property="og:url" content="https://pegsignal.com/">' in body
        assert '<link rel="canonical" href="https://pegsignal.com/">' in body
        assert "src=note" not in body

    def test_canonical_keeps_lang_for_english_page(self, ph):
        """langはsrcと違い別コンテンツを指すため、canonicalに残す必要がある
        (2026-08-09、canonicalが常に/を指してしまい英語版・S&P500版が検索
        エンジンから重複ページ扱いされていた不具合の修正)。"""
        body = ph.handler(_event(lang="en"), None)["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/?lang=en">' in body

    def test_canonical_keeps_universe_for_sp500_page(self, ph):
        body = ph.handler(_event(universe="SP500"), None)["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/?universe=SP500">' in body

    def test_canonical_keeps_both_lang_and_universe(self, ph):
        body = ph.handler(_event(lang="en", universe="SP500"), None)["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/?lang=en&amp;universe=SP500">' in body

    def test_no_google_site_verification_tag_by_default(self, ph):
        """GOOGLE_SITE_VERIFICATION環境変数が未設定(開発環境等)なら何も出さない"""
        body = ph.handler(_event(), None)["body"]
        assert "google-site-verification" not in body

    def test_google_site_verification_tag_present_when_configured(self, ph):
        ph.GOOGLE_SITE_VERIFICATION = "Wu7Sr87bikKsx6bMzrkBB3Ysonb0grky6CuHmJ7jvv0"
        body = ph.handler(_event(), None)["body"]
        assert ('<meta name="google-site-verification" '
                'content="Wu7Sr87bikKsx6bMzrkBB3Ysonb0grky6CuHmJ7jvv0">') in body

    def test_canonical_drops_src_even_when_lang_is_kept(self, ph):
        body = ph.handler(_event(lang="en", src="x"), None)["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/?lang=en">' in body

    def test_site_url_env_var_overrides_public_origin(self, ph):
        ph.PUBLIC_ORIGIN = "https://staging.example.com"
        body = ph.handler(_event(), None)["body"]
        assert '<meta property="og:url" content="https://staging.example.com/">' in body

    def test_description_switches_with_lang(self, ph):
        body_en = ph.handler(_event(lang="en"), None)["body"]
        assert "free service that screens NASDAQ100" in body_en


class TestPreviewCanonicalIgnoresGeolocation:
    """canonical・og:imageは地理判定(CloudFront-Viewer-Country)を無視し、
    URLに明示された?lang=だけで決める(2026-08-09、本番反映前レビューで発覚した
    不具合の修正)。

    地理判定はGooglebot等のクローラーにも等しく適用され、大半が米国IPから
    アクセスする。canonical/og:imageに地理判定込みのlangを使うと、noteやXで
    日本語向けに共有した素のURL(pegsignal.com/)が「このページの正規版は
    英語です」と自己申告してしまい、Googleが日本語トップページを独立に
    インデックスしなくなる・SNSクローラーが英語バナーを取得してしまう、
    という実害が出る。

    ページの表示言語自体(<html lang=・本文の翻訳)は地理判定を維持したまま、
    canonical/og:imageの申告だけを決定的にする。"""

    def test_content_still_switches_to_english_by_geolocation(self, ph):
        """表示言語の地理判定機能自体は維持されていること(退行防止)"""
        r = ph.handler(_event({"cloudfront-viewer-country": "US"}), None)
        assert '<html lang="en"' in r["body"]

    def test_canonical_stays_japanese_for_geolocated_english_visitor(self, ph):
        body = ph.handler(_event({"cloudfront-viewer-country": "US"}), None)["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/">' in body
        assert 'href="https://pegsignal.com/?lang=en"' not in body

    def test_og_image_stays_japanese_for_geolocated_english_visitor(self, ph):
        body = ph.handler(_event({"cloudfront-viewer-country": "US"}), None)["body"]
        assert '<meta property="og:image" content="https://pegsignal.com/og-image.jpg">' in body

    def test_canonical_and_content_both_follow_explicit_lang_param(self, ph):
        """?lang=enが明示されていれば、地理に関わらずcanonical/表示言語とも英語"""
        r = ph.handler(_event({"cloudfront-viewer-country": "JP"}, lang="en"), None)
        assert '<html lang="en"' in r["body"]
        assert '<link rel="canonical" href="https://pegsignal.com/?lang=en">' in r["body"]


class TestPreviewOgImageRoute:
    """/og-image.jpg(日本語)・/og-image-en.jpg(英語)がそれぞれのバナー画像を
    返すこと(2026-08-08追加、英語版は同日に言語別対応)。このルートはDynamoDB等を
    一切参照しないため、_put_snapshotなしでも動作する。"""

    def test_returns_the_bundled_ja_jpeg(self, ph):
        r = ph.handler({"rawPath": "/og-image.jpg"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "image/jpeg"
        assert r["isBase64Encoded"] is True
        import base64
        assert base64.b64decode(r["body"]) == ph._OG_IMAGE_BYTES["ja"]
        assert len(ph._OG_IMAGE_BYTES["ja"]) > 0

    def test_returns_the_bundled_en_jpeg(self, ph):
        r = ph.handler({"rawPath": "/og-image-en.jpg"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "image/jpeg"
        import base64
        assert base64.b64decode(r["body"]) == ph._OG_IMAGE_BYTES["en"]
        assert len(ph._OG_IMAGE_BYTES["en"]) > 0

    def test_ja_and_en_banners_are_different_images(self, ph):
        assert ph._OG_IMAGE_BYTES["ja"] != ph._OG_IMAGE_BYTES["en"]


class TestPreviewPwaIcon:
    """ホーム画面への追加(PWA/apple-touch-icon)対応(2026-08-08追加)。noteの
    記事で「メール登録が不安ならURLをホーム画面に保存」と案内する前提として、
    apex(pegsignal.com)側にもアイコン・マニフェストを用意した。"""

    def test_icon_route_returns_the_bundled_png(self, ph):
        r = ph.handler({"rawPath": "/icon.png"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "image/png"
        assert r["isBase64Encoded"] is True
        import base64
        assert base64.b64decode(r["body"]) == ph._ICON_BYTES
        assert len(ph._ICON_BYTES) > 0

    def test_manifest_route_returns_valid_json_with_icon_url(self, ph):
        r = ph.handler({"rawPath": "/manifest.json"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "application/manifest+json; charset=utf-8"
        import json
        manifest = json.loads(r["body"])
        assert manifest["icons"][0]["src"] == "https://pegsignal.com/icon.png"
        assert manifest["short_name"]

    def test_manifest_name_switches_with_lang(self, ph):
        body_en = ph.handler({"rawPath": "/manifest.json", "queryStringParameters": {"lang": "en"}}, None)["body"]
        import json
        assert json.loads(body_en)["name"] == ph._APP_NAME["en"]

    def test_page_head_includes_apple_touch_icon_and_manifest_link(self, ph):
        body = ph.handler(_event(), None)["body"]
        assert '<link rel="apple-touch-icon" href="https://pegsignal.com/icon.png">' in body
        assert '<link rel="manifest" href="/manifest.json?lang=ja">' in body
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in body

    def test_manifest_link_uses_correct_lang_for_english_page(self, ph):
        body = ph.handler(_event(lang="en"), None)["body"]
        assert '<link rel="manifest" href="/manifest.json?lang=en">' in body


class TestPreviewSeoRoutes:
    """robots.txt・sitemap.xml(2026-08-09追加)。専用ルーティングが無かったため、
    これらのパスへのアクセスが通常のランキングページのHTMLになってしまい、
    意図しない状態だった。Google検索流入の改善作業で発覚し対応した。"""

    def test_robots_txt_allows_all_and_points_to_sitemap(self, ph):
        r = ph.handler({"rawPath": "/robots.txt"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "text/plain; charset=utf-8"
        assert "Allow: /" in r["body"]
        assert "Sitemap: https://pegsignal.com/sitemap.xml" in r["body"]

    def test_sitemap_xml_lists_all_lang_and_universe_combinations(self, ph):
        r = ph.handler({"rawPath": "/sitemap.xml"}, None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "application/xml; charset=utf-8"
        body = r["body"]
        assert "<loc>https://pegsignal.com/</loc>" in body
        assert "<loc>https://pegsignal.com/?lang=en</loc>" in body
        assert "<loc>https://pegsignal.com/?universe=SP500</loc>" in body
        assert "<loc>https://pegsignal.com/?lang=en&amp;universe=SP500</loc>" in body


class TestPreviewSrcTracking:
    """告知文への?src=(例: note/x/hn)をアクセスログに残す。register-stock側の
    ?src=規約(lambda_register/handler.py)と揃えている(2026-08-08追加)。"""

    def test_valid_src_is_logged(self, ph, capsys):
        ph.handler(_event(src="note"), None)
        captured = capsys.readouterr()
        assert "src='note'" in captured.out

    def test_invalid_src_is_not_logged_as_is(self, ph, capsys):
        ph.handler(_event(src="not valid!"), None)
        captured = capsys.readouterr()
        assert "src=''" in captured.out

    def test_bot_user_agent_is_flagged(self, ph, capsys):
        ph.handler(_event({"user-agent": "curl/8.0"}), None)
        captured = capsys.readouterr()
        assert "bot=True" in captured.out

    def test_normal_user_agent_is_not_flagged(self, ph, capsys):
        ph.handler(_event({"user-agent": "Mozilla/5.0"}), None)
        captured = capsys.readouterr()
        assert "bot=False" in captured.out


