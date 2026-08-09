"""
register-stock Lambda(mnt/user-data/outputs/aws_deploy/lambda_register/handler.py)の
コンポーネントテスト。DynamoDBをtests/fake_dynamodb.pyの軽量fakeに差し替えて実施する。

daily-batch側にも同名の"handler.py"があるため、モジュール名衝突を避けるべく
importlib.util.spec_from_file_locationで明示的な別名("register_handler")としてロードする。
"""
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from boto3.dynamodb.conditions import Key

from fake_dynamodb import FakeDynamoDBResource

REGISTER_HANDLER_PATH = (
    Path(__file__).resolve().parent.parent
    / "mnt/user-data/outputs/aws_deploy/lambda_register/handler.py"
)

VALID_TOKEN = "valid-test-token"


@pytest.fixture
def rh():
    """テストごとに新しいモジュールインスタンスと空のfake DynamoDBを用意する"""
    spec = importlib.util.spec_from_file_location("register_handler", REGISTER_HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["register_handler"] = module
    spec.loader.exec_module(module)

    fake = FakeDynamoDBResource({
        "watchlist": ("owner_token", "ticker"),
        "approved_tokens": ("token", None),
        "app_settings": ("key", None),
    })
    fake.Table("approved_tokens").put_item(Item={"token": VALID_TOKEN, "email": "user@example.com", "status": "approved"})
    module.dynamodb = fake
    module.lambda_client = mock.MagicMock()
    module.ses = mock.MagicMock()
    module.REGISTER_FUNCTION_URL = "https://example.com/"
    module.SES_SENDER = "sender@example.com"
    module.SES_RECIPIENT = "admin@example.com"
    return module


def _event(_headers=None, **params):
    event = {"queryStringParameters": {k: v for k, v in params.items() if v is not None}}
    if _headers:
        event["headers"] = _headers
    return event


def _visible(body: str) -> str:
    """利用者の目に触れる部分だけを取り出す。<style>内はCSSと日本語コメントを
    含むが画面には出ないため、言語混在チェックの対象から除く。"""
    return re.sub(r"<style>.*?</style>", "", body, flags=re.S)


class TestAuth:
    def test_missing_token_shows_signup_form(self, rh):
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "お申込み" in r["body"]

    def test_invalid_token_shows_signup_form(self, rh):
        r = rh.handler(_event(token="bogus"), None)
        assert r["statusCode"] == 403
        assert "お申込み" in r["body"]

    def test_privacy_page_accessible_without_token(self, rh):
        r = rh.handler(_event(privacy="1"), None)
        assert r["statusCode"] == 200
        assert "プライバシーポリシー" in r["body"]

    def test_info_page_accessible_without_token(self, rh):
        r = rh.handler(_event(info="1"), None)
        assert r["statusCode"] == 200
        assert "このサービスの仕組み" in r["body"]

    def test_terms_page_accessible_without_token(self, rh):
        r = rh.handler(_event(terms="1"), None)
        assert r["statusCode"] == 200
        assert "利用規約" in r["body"]

    def test_about_page_accessible_without_token(self, rh):
        r = rh.handler(_event(about="1"), None)
        assert r["statusCode"] == 200
        assert "運営者情報" in r["body"]
        assert "個人が開発" in r["body"]

    def test_contact_form_accessible_without_token(self, rh):
        r = rh.handler(_event(contact="1"), None)
        assert r["statusCode"] == 200
        assert "お問い合わせ" in r["body"]

    def test_manifest_accessible_without_token(self, rh):
        r = rh.handler(_event(manifest="1"), None)
        assert r["statusCode"] == 200
        assert r["headers"]["Content-Type"] == "application/manifest+json; charset=utf-8"
        body = json.loads(r["body"])
        assert body["display"] == "standalone"
        assert body["icons"][0]["src"].startswith("data:image/svg+xml;base64,")

    def test_pending_token_is_not_yet_valid(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "pending-tok", "email": "p@example.com", "status": "pending"})
        r = rh.handler(_event(token="pending-tok"), None)
        assert r["statusCode"] == 403

    def test_valid_token_shows_form(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN), None)
        assert r["statusCode"] == 200
        assert "監視リスト" in r["body"]


class TestSignup:
    def test_signup_sends_confirmation_and_stores_pending(self, rh):
        r = rh.handler(_event(signup_email="new@example.com", captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert r["statusCode"] == 200
        assert rh.ses.send_email.called
        item = None
        for v in rh.dynamodb.Table("approved_tokens")._items.values():
            if v.get("email") == "new@example.com":
                item = v
        assert item is not None
        assert item["status"] == "pending"

    def test_signup_wrong_captcha_rejected(self, rh):
        r = rh.handler(_event(signup_email="new@example.com", captcha_n1="2", captcha_n2="3", captcha_answer="99"), None)
        assert r["statusCode"] == 400
        assert not rh.ses.send_email.called

    def test_signup_invalid_email_rejected(self, rh):
        r = rh.handler(_event(signup_email="not-an-email", captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert r["statusCode"] == 400

    def test_confirm_promotes_pending_to_approved(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "pending-tok", "email": "p@example.com", "status": "pending"})
        r = rh.handler(_event(token="pending-tok", confirm="1"), None)
        assert r["statusCode"] == 200
        assert "完了" in r["body"]
        item = rh.dynamodb.Table("approved_tokens").get_item(Key={"token": "pending-tok"})["Item"]
        assert item["status"] == "approved"

        # 確認後は通常のトークンとしてそのまま使えるはず
        r2 = rh.handler(_event(token="pending-tok"), None)
        assert r2["statusCode"] == 200
        assert "監視リスト" in r2["body"]

    def test_confirmation_email_is_english_when_lang_is_en(self, rh):
        rh.handler(_event(_headers={"x-site-lang": "en"}, signup_email="new@example.com",
                          captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert rh.ses.send_email.called
        sent = rh.ses.send_email.call_args.kwargs
        assert "Confirm your registration" in sent["Message"]["Subject"]["Data"]
        assert "Thanks for signing up" in sent["Message"]["Body"]["Html"]["Data"]

    def test_reminder_email_uses_existing_users_stored_lang(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "existing-tok", "email": "existing@example.com",
                  "status": "approved", "lang": "en"})
        # 再申込み時のヘッダーが日本語版ドメインでも、本人の保存済み言語(en)で再送する
        rh.handler(_event(_headers={"x-site-lang": "ja"}, signup_email="existing@example.com",
                          captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        sent = rh.ses.send_email.call_args.kwargs
        assert "Bookmark the link" in sent["Message"]["Body"]["Html"]["Data"]


class TestTickerValidation:
    def test_unknown_ticker_rejected_when_cache_present(self, rh):
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["NVDA", "AAPL"]})
        r = rh.handler(_event(token=VALID_TOKEN, ticker="ZZZZZ"), None)
        assert r["statusCode"] == 400
        assert "見つかりませんでした" in r["body"]
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 0

    def test_known_ticker_accepted_when_cache_present(self, rh):
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["NVDA", "AAPL"]})
        r = rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        assert r["statusCode"] == 200
        assert rh.dynamodb.Table("watchlist").scan()["Items"][0]["ticker"] == "NVDA"

    def test_registration_allowed_when_cache_missing(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, ticker="ZZZZZ"), None)
        assert r["statusCode"] == 200
        assert rh.dynamodb.Table("watchlist").scan()["Items"][0]["ticker"] == "ZZZZZ"

    def test_deleting_ticker_not_in_cache_is_still_allowed(self, rh):
        rh.handler(_event(token=VALID_TOKEN, ticker="ZZZZZ"), None)
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["NVDA", "AAPL"]})
        r = rh.handler(_event(token=VALID_TOKEN, ticker="ZZZZZ", action="delete"), None)
        assert r["statusCode"] == 200
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 0

    def test_ticker_accepted_from_any_universe_regardless_of_selection(self, rh):
        """_is_known_tickerは「打ち間違いの検出」であって「対象指数による絞り込み」
        ではないため、対応する全指数のいずれかに含まれていれば登録できる。
        監視レポートは対象指数に依存しないので、S&P500を選びつつNASDAQ100固有の
        銘柄を監視したいケースは正当(S&P500対応、2026-08-07)。"""
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["NVDA", "PDD"]})
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "sp500_tickers_cache", "value": ["NVDA", "KO"]})
        rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)

        # 選択中はS&P500だが、NASDAQ100固有の銘柄(PDD)も登録できる
        r = rh.handler(_event(token=VALID_TOKEN, ticker="PDD"), None)
        assert r["statusCode"] == 200
        # S&P500固有の銘柄(KO)ももちろん登録できる
        r = rh.handler(_event(token=VALID_TOKEN, ticker="KO"), None)
        assert r["statusCode"] == 200
        assert {i["ticker"] for i in rh.dynamodb.Table("watchlist").scan()["Items"]} == {"PDD", "KO"}

    def test_ticker_in_no_universe_still_rejected(self, rh):
        """どの指数にも無いティッカーは従来どおり拒否する(打ち間違い検出は機能させる)"""
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["NVDA", "PDD"]})
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "sp500_tickers_cache", "value": ["NVDA", "KO"]})
        r = rh.handler(_event(token=VALID_TOKEN, ticker="ZZZZZ"), None)
        assert r["statusCode"] == 400
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 0

    def test_watchlist_entry_survives_universe_switch_and_can_be_readded(self, rh):
        """指数を切り替えても既存の監視銘柄は残る。かつ、一度削除しても再登録できる
        (「残るのに消すと二度と登録できない」という一貫性のない状態を防ぐ回帰テスト)"""
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "nasdaq100_tickers_cache", "value": ["PDD"]})
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": "sp500_tickers_cache", "value": ["KO"]})
        rh.handler(_event(token=VALID_TOKEN, ticker="PDD"), None)
        rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)
        assert [i["ticker"] for i in rh.dynamodb.Table("watchlist").scan()["Items"]] == ["PDD"]

        rh.handler(_event(token=VALID_TOKEN, ticker="PDD", action="delete"), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="PDD"), None)
        assert r["statusCode"] == 200
        assert [i["ticker"] for i in rh.dynamodb.Table("watchlist").scan()["Items"]] == ["PDD"]


class TestAccessRecoveryHint:
    def test_signup_form_mentions_resend_on_lost_link(self, rh):
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "アクセス用リンクを再送します" in r["body"]


class TestPreviewLink:
    """メール登録不要で選定レポートを見られるlanding-previewへのリンク。
    PREVIEW_URL未設定(devの初期状態等)ではリンク自体を出さない(壊れたリンクを
    出さないための既存パターンと同じ方針)。"""

    def test_preview_link_shown_when_url_configured(self, rh):
        rh.PREVIEW_URL = "https://preview.pegsignal.com/"
        r = rh.handler(_event(), None)
        assert 'href="https://preview.pegsignal.com/"' in r["body"]
        assert "登録せずに" in r["body"]

    def test_preview_link_omitted_when_url_not_configured(self, rh):
        rh.PREVIEW_URL = ""
        r = rh.handler(_event(), None)
        assert "登録せずに" not in r["body"]

    def test_preview_link_shown_in_english(self, rh):
        rh.PREVIEW_URL = "https://preview.pegsignal.com/"
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        assert 'href="https://preview.pegsignal.com/"' in r["body"]
        assert "without signing up" in r["body"]


class TestLanguage:
    def test_signup_form_defaults_to_japanese_without_header(self, rh):
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "お申込み" in r["body"]

    def test_signup_form_is_english_when_site_lang_header_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        assert r["statusCode"] == 200
        assert "Sign up" in r["body"]
        assert "お申込み" not in r["body"]

    def test_unrecognized_site_lang_header_falls_back_to_japanese(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "fr"}), None)
        assert r["statusCode"] == 200
        assert "お申込み" in r["body"]

    def test_english_for_visitor_geolocated_outside_japan(self, rh):
        """CloudFront-Viewer-Countryが日本以外なら、X-Site-Langが無くても英語を既定にする
        (2026-08-07、海外流入向けに追加)。"""
        r = rh.handler(_event(_headers={"cloudfront-viewer-country": "US"}), None)
        assert "Sign up" in r["body"]
        assert "お申込み" not in r["body"]

    def test_japanese_for_visitor_geolocated_in_japan(self, rh):
        r = rh.handler(_event(_headers={"cloudfront-viewer-country": "JP"}), None)
        assert "お申込み" in r["body"]

    def test_japanese_when_no_geolocation_header_present(self, rh):
        """ヘッダー自体が無い場合(Function URL直叩き・開発環境等)は
        従来通り日本語を既定とする(地理判定によって既定挙動を壊さない)。"""
        r = rh.handler(_event(), None)
        assert "お申込み" in r["body"]

    def test_x_site_lang_header_overrides_geolocation(self, rh):
        """X-Site-Lang(ドメイン固定、en.pegsignal.com等)はCloudFront-Viewer-Countryより優先される。"""
        r = rh.handler(_event(_headers={"x-site-lang": "ja", "cloudfront-viewer-country": "US"}), None)
        assert "お申込み" in r["body"]

    def test_geolocation_country_code_is_case_insensitive(self, rh):
        r = rh.handler(_event(_headers={"cloudfront-viewer-country": "jp"}), None)
        assert "お申込み" in r["body"]

    def test_signup_stores_lang_from_header_on_new_registration(self, rh):
        rh.handler(_event(
            _headers={"x-site-lang": "en"}, signup_email="new@example.com",
            captcha_n1="2", captcha_n2="3", captcha_answer="5",
        ), None)
        item = None
        for v in rh.dynamodb.Table("approved_tokens")._items.values():
            if v.get("email") == "new@example.com":
                item = v
        assert item is not None
        assert item["lang"] == "en"

    def test_main_form_renders_in_stored_language_regardless_of_current_header(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": VALID_TOKEN, "email": "user@example.com",
                  "status": "approved", "lang": "en"})
        # ヘッダーが日本語版ドメインでも、本人の保存済み設定(en)が優先される
        r = rh.handler(_event(_headers={"x-site-lang": "ja"}, token=VALID_TOKEN), None)
        assert r["statusCode"] == 200
        assert "Add stocks to your watchlist" in r["body"]

    def test_lang_toggle_updates_stored_preference(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        assert r["statusCode"] == 200
        assert "Add stocks to your watchlist" in r["body"]
        item = rh.dynamodb.Table("approved_tokens").get_item(Key={"token": VALID_TOKEN})["Item"]
        assert item["lang"] == "en"

        # 明示的な切替はドメインをまたいでも本人の設定として持続する
        r2 = rh.handler(_event(token=VALID_TOKEN), None)
        assert "Add stocks to your watchlist" in r2["body"]

        # 日本語に戻すことも同じ仕組みでできる
        r3 = rh.handler(_event(token=VALID_TOKEN, lang="ja"), None)
        assert "銘柄を監視リストに登録" in r3["body"]

    def test_english_notices_for_ticker_actions(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        assert "Added NVDA to your watchlist." in r["body"]

    def test_footer_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        assert "About" in r["body"] and "Contact" in r["body"]
        assert "運営者情報" not in r["body"]

    def test_footer_is_japanese_by_default(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN), None)
        assert "運営者情報" in r["body"]

    def test_period_dropdown_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        assert "Mid-term" in r["body"]
        assert "中期" not in r["body"]


class TestLanguageConsistency:
    """英語表示時にページのどこか一部だけ日本語が残ってしまう事故を防ぐ。
    本文だけでなく、<title>・meta・PWAマニフェスト等の「目立たないが利用者に
    見える」箇所も対象にする。"""

    def test_page_title_and_description_are_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        title = re.search(r"<title>(.*?)</title>", r["body"], re.S).group(1)
        description = re.search(r'<meta name="description" content="(.*?)"', r["body"], re.S).group(1)
        assert "株式レポートサービス" not in title
        assert "PegSignal" in title
        assert "NASDAQ100" in description
        assert "割安な成長株" not in description

    def test_page_title_is_japanese_by_default(self, rh):
        r = rh.handler(_event(), None)
        title = re.search(r"<title>(.*?)</title>", r["body"], re.S).group(1)
        assert "株式レポートサービス" in title

    def test_html_lang_attribute_matches_display_language(self, rh):
        assert '<html lang="en"' in rh.handler(_event(_headers={"x-site-lang": "en"}), None)["body"]
        assert '<html lang="ja"' in rh.handler(_event(), None)["body"]

    def test_home_screen_app_title_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        app_title = re.search(
            r'<meta name="apple-mobile-web-app-title" content="(.*?)"', r["body"]
        ).group(1)
        assert app_title == "PegSignal"

    def test_manifest_is_english_when_lang_param_is_en(self, rh):
        manifest = json.loads(rh.handler(_event(manifest="1", lang="en"), None)["body"])
        assert manifest["lang"] == "en"
        assert "株式レポート" not in manifest["name"]
        assert "株式レポート" not in manifest["short_name"]

    def test_manifest_is_japanese_by_default(self, rh):
        manifest = json.loads(rh.handler(_event(manifest="1"), None)["body"])
        assert manifest["short_name"] == "株式レポート"

    def test_manifest_link_carries_display_language(self, rh):
        """manifestはブラウザが別リクエストで取りに来るため、X-Site-Langヘッダーが
        付かない。_page()側が?lang=を埋めていないと英語利用者に日本語アプリ名が出る"""
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        assert 'href="?manifest=1&amp;lang=en"' in r["body"]

    def test_mode_change_notice_uses_english_label(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        r = rh.handler(_event(token=VALID_TOKEN, mode="MID"), None)
        assert "Mid-term" in r["body"]
        assert "中期" not in r["body"]

    def test_invalid_ticker_error_has_english_back_link(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="nv da!"), None)
        assert r["statusCode"] == 400
        assert "Invalid ticker." in r["body"]
        assert "戻る" not in r["body"]
        assert "運営者情報" not in r["body"]

    def test_ai_prompt_invalid_ticker_error_is_fully_english(self, rh):
        r = rh.handler(_event(ai_prompt="1", ticker="nv da!", lang="en"), None)
        assert r["statusCode"] == 400
        assert "戻る" not in r["body"]
        assert "運営者情報" not in r["body"]

    def test_account_deleted_page_is_fully_english(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        r = rh.handler(_event(token=VALID_TOKEN, delete_account_confirm="1"), None)
        assert "Account deleted" in r["body"]
        assert "運営者情報" not in r["body"]

    def test_invalid_confirm_link_message_follows_request_language(self, rh):
        r_en = rh.handler(_event(_headers={"x-site-lang": "en"}, token="bogus", confirm="1"), None)
        assert r_en["statusCode"] == 404
        assert "無効なリンクです" not in r_en["body"]
        assert "運営者情報" not in r_en["body"]

        r_ja = rh.handler(_event(token="bogus", confirm="1"), None)
        assert "無効なリンクです" in r_ja["body"]

    def test_no_japanese_anywhere_on_english_pages(self, rh):
        """英語利用者に日本語が1文字も見えないことを、公開ページ全体で保証する。
        メイン画面(?token=)だけは「日本語で表示」という言語切替リンクを意図的に
        日本語で出すため、この網羅チェックからは除外している。"""
        pages = [{}, {"guide": "1"}, {"info": "1"}, {"about": "1"},
                 {"privacy": "1"}, {"terms": "1"}, {"contact": "1"}]
        for params in pages:
            body = _visible(rh.handler(_event(_headers={"x-site-lang": "en"}, **params), None)["body"])
            found = re.findall(r"[ぁ-んァ-ン一-龥]+", body)
            assert not found, f"英語ページ {params or 'トップ'} に日本語が残っている: {found}"

    NOTICE_RE = re.compile(
        r'<p (?:class="notice"|style="background:#eef;padding:8px;border-radius:4px;")>(.*?)</p>'
    )

    def test_report_accepted_notice_shows_approximate_arrival_time_ja(self, rh):
        """レポート送信ボタン押下時の通知に、メイン画面の案内文言(既存)と同じ
        「メール到着まで1分ほど」という目安時間を表示する(ユーザー要望)。
        同じ文言がボタン付近の静的な案内文にも既に存在するため、ページ全体では
        なく通知(_render_formのnotice枠)の中身だけを見て検証する"""
        r = rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        notice = self.NOTICE_RE.search(r["body"])
        assert notice is not None
        assert "メール到着まで1分ほどかかります" in notice.group(1)

    def test_report_accepted_notice_shows_approximate_arrival_time_en(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        r = rh.handler(_event(token=VALID_TOKEN, report="monitoring"), None)
        notice = self.NOTICE_RE.search(r["body"])
        assert notice is not None
        assert "within a minute or so" in notice.group(1)

    def test_report_send_failure_page_is_fully_english(self, rh):
        rh.handler(_event(token=VALID_TOKEN, lang="en"), None)
        rh.lambda_client.invoke.side_effect = RuntimeError("boom")
        r = rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        assert r["statusCode"] == 500
        assert "戻る" not in r["body"]
        assert "運営者情報" not in r["body"]


class TestUsageGuide:
    """初めての人向けの使い方ガイド(?guide=1)。申込み前の判断材料になるため
    トークン無しでも読める必要がある。"""

    def test_guide_is_public_without_token(self, rh):
        r = rh.handler(_event(guide="1"), None)
        assert r["statusCode"] == 200
        assert "PegSignalの使い方" in r["body"]

    def test_guide_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, guide="1"), None)
        assert "How to use PegSignal" in r["body"]
        assert not re.search(r"[ぁ-んァ-ン一-龥]", _visible(r["body"]))

    def test_guide_honors_explicit_lang_param_from_email_link(self, rh):
        """メール内リンクにはX-Site-Langが付かないため、?lang=で言語を引き継ぐ"""
        r = rh.handler(_event(guide="1", lang="en"), None)
        assert "How to use PegSignal" in r["body"]

    def test_guide_contains_diagrams(self, rh):
        r = rh.handler(_event(guide="1"), None)
        assert r["body"].count("<svg") >= 4

    def test_guide_diagrams_are_wellformed_svg(self, rh):
        import xml.etree.ElementTree as ET
        r = rh.handler(_event(guide="1"), None)
        for svg in re.findall(r"<svg.*?</svg>", r["body"], re.S):
            ET.fromstring(svg)

    def test_guide_is_linked_from_signup_form(self, rh):
        assert "?guide=1" in rh.handler(_event(), None)["body"]

    def test_guide_is_linked_from_main_form(self, rh):
        assert "guide=1" in rh.handler(_event(token=VALID_TOKEN), None)["body"]

    def test_guide_is_linked_from_every_page_footer(self, rh):
        for params in ({"privacy": "1"}, {"terms": "1"}, {"about": "1"}, {"contact": "1"}):
            assert "?guide=1" in rh.handler(_event(**params), None)["body"]

    def test_confirmation_email_links_to_guide(self, rh):
        rh.handler(_event(signup_email="new@example.com", captcha_n1="1",
                          captcha_n2="2", captcha_answer="3"), None)
        body = rh.ses.send_email.call_args.kwargs["Message"]["Body"]["Html"]["Data"]
        assert "?guide=1" in body

    def test_confirmation_email_guide_link_is_english_for_en_users(self, rh):
        rh.handler(_event(_headers={"x-site-lang": "en"}, signup_email="new@example.com",
                          captcha_n1="1", captcha_n2="2", captcha_answer="3"), None)
        body = rh.ses.send_email.call_args.kwargs["Message"]["Body"]["Html"]["Data"]
        assert "?guide=1&amp;lang=en" in body

    def test_reminder_email_links_to_guide(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(Item={
            "token": "tok-existing", "email": "existing@example.com", "status": "approved"})
        rh.handler(_event(signup_email="existing@example.com", captcha_n1="1",
                          captcha_n2="2", captcha_answer="3"), None)
        body = rh.ses.send_email.call_args.kwargs["Message"]["Body"]["Html"]["Data"]
        assert "?guide=1" in body


class TestDeliveryFrequencyWording:
    """配信頻度は判定期間モードで変わる(短期=毎営業日・中期=週1・長期=月1)ため、
    申込みページで「毎日届く」と断定してはいけない。"""

    def test_signup_page_does_not_promise_daily_delivery(self, rh):
        body = rh.handler(_event(), None)["body"]
        assert "毎朝メールでチェック" not in body
        assert "毎日レポート受信" not in body
        assert "定期" in body

    def test_english_signup_page_does_not_promise_daily_delivery(self, rh):
        body = rh.handler(_event(_headers={"x-site-lang": "en"}), None)["body"]
        assert "every morning" not in body
        assert "Daily reports" not in body

    def test_signup_page_explains_frequency_varies_by_mode(self, rh):
        body = rh.handler(_event(), None)["body"]
        assert "短期" in body and "中期" in body and "長期" in body


class TestWordingMatchesImplementation:
    """利用者向けの説明文と実装の齟齬を防ぐ。数値・頻度・収集情報などの
    「事実の主張」は、コード側の正本とズレると誤解や不信につながる。"""

    def test_privacy_policy_lists_language_setting_as_collected(self, rh):
        """_set_user_lang()がlangをapproved_tokensに保存しているため、
        収集情報の列挙に含めないと「〜のみ」という記述が不正確になる"""
        assert "表示言語" in rh.handler(_event(privacy="1"), None)["body"]
        assert "display language" in rh.handler(
            _event(_headers={"x-site-lang": "en"}, privacy="1"), None)["body"]

    def test_info_page_does_not_claim_daily_delivery_for_short_mode(self, rh):
        """EventBridgeのcronはMON-FRIのため、配信は平日のみ"""
        body = rh.handler(_event(info="1"), None)["body"]
        assert "短期=毎日" not in body
        assert "短期=毎営業日" in body

    def test_info_page_frequency_table_matches_send_freq_labels(self, rh):
        """表の頻度ラベルは_SEND_FREQ_LABELが正本。本文と食い違わせない"""
        body = rh.handler(_event(info="1"), None)["body"]
        for label in ("毎日（平日）", "毎週月曜", "月初の月曜（月1回）"):
            assert label in body

    def test_info_page_thresholds_come_from_config(self, rh):
        """足切り条件はconfig.pyの値をそのまま表示していること"""
        from config import MAX_PEG, MIN_GROWTH
        body = rh.handler(_event(info="1"), None)["body"]
        assert f"{MIN_GROWTH * 100:.0f}%" in body
        assert str(MAX_PEG) in body

    def test_guide_peg_range_excludes_zero_and_negative(self, rh):
        """ranking.pyはpeg > 0 かつ peg <= MAX_PEG。「3.0以下」だけでは
        負値を含むように読めるため範囲で表記する"""
        body = rh.handler(_event(guide="1"), None)["body"]
        assert "PEGが0〜" in body

    def test_public_pages_avoid_value_judgment_wording_about_stocks(self, rh):
        """「割安」「安く買える」等、特定銘柄の価値を断定する表現は使わない。
        PEGレシオという客観的指標に基づく機械的な選定であることに表現を統一する
        (金商法(投資助言業)リスク低減の方針、TODO.md参照。「買い時レポート」を
        「PEGスクリーニング結果」に改めた過去の対応と同じ考え方)"""
        for params in ({}, {"about": "1"}, {"info": "1"}, {"guide": "1"}):
            body = rh.handler(_event(**params), None)["body"]
            assert "割安" not in body, f"{params}: '割安'が残っている"
            assert "安く買え" not in body, f"{params}: '安く買え'が残っている"

    def test_english_public_pages_avoid_value_judgment_wording(self, rh):
        for params in ({}, {"about": "1"}, {"info": "1"}, {"guide": "1"}):
            body = rh.handler(_event(_headers={"x-site-lang": "en"}, **params), None)["body"]
            assert "undervalued" not in body.lower(), f"{params}: 'undervalued'が残っている"
            assert "cheap" not in body.lower(), f"{params}: 'cheap'が残っている"


class TestTickerRegistration:
    def test_register_valid_ticker(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        assert r["statusCode"] == 200
        assert rh.dynamodb.Table("watchlist").scan()["Items"][0]["ticker"] == "NVDA"

    def test_register_invalid_ticker_rejected(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, ticker="nv da!"), None)
        assert r["statusCode"] == 400
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 0

    def test_register_duplicate_is_idempotent_not_error(self, rh):
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        assert r["statusCode"] == 200
        assert "登録済み" in r["body"]
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 1

    def test_delete_removes_ticker(self, rh):
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="NVDA", action="delete"), None)
        assert r["statusCode"] == 200
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 0

    def test_watchlist_cap_is_enforced(self, rh):
        for t in ["AAA", "BBB", "CCC", "DDD", "EEE"]:  # MAX_WATCHLIST_SIZE既定=5
            rh.handler(_event(token=VALID_TOKEN, ticker=t), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="FFF"), None)
        assert r["statusCode"] == 400
        assert len(rh.dynamodb.Table("watchlist").scan()["Items"]) == 5

    def test_watchlist_cap_does_not_block_re_registering_existing_ticker(self, rh):
        for t in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
            rh.handler(_event(token=VALID_TOKEN, ticker=t), None)
        r = rh.handler(_event(token=VALID_TOKEN, ticker="AAA"), None)
        assert r["statusCode"] == 200

    def test_different_users_can_track_the_same_ticker_independently(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "other-tok", "email": "o@example.com", "status": "approved"})
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        r = rh.handler(_event(token="other-tok", ticker="NVDA"), None)
        assert r["statusCode"] == 200
        assert "登録済み" not in r["body"]  # 他人の登録とは独立して自分も登録できる

    def test_deleting_does_not_affect_other_users_watchlist(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "other-tok", "email": "o@example.com", "status": "approved"})
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        rh.handler(_event(token="other-tok", ticker="NVDA"), None)
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA", action="delete"), None)

        r = rh.handler(_event(token="other-tok"), None)
        assert "NVDA" in r["body"]  # 他人が削除しても自分の登録は残る


class TestContact:
    def test_contact_sends_email_to_recipient_with_reply_to(self, rh):
        r = rh.handler(_event(
            contact_submit="1", contact_email="user@example.com",
            contact_message="teardown", captcha_n1="2", captcha_n2="3", captcha_answer="5",
        ), None)
        assert r["statusCode"] == 200
        assert rh.ses.send_email.called
        kwargs = rh.ses.send_email.call_args.kwargs
        assert kwargs["Destination"]["ToAddresses"] == ["admin@example.com"]
        assert kwargs["ReplyToAddresses"] == ["user@example.com"]

    def test_contact_invalid_email_rejected(self, rh):
        r = rh.handler(_event(
            contact_submit="1", contact_email="not-an-email",
            contact_message="hi", captcha_n1="2", captcha_n2="3", captcha_answer="5",
        ), None)
        assert r["statusCode"] == 400
        assert not rh.ses.send_email.called

    def test_contact_empty_message_rejected(self, rh):
        r = rh.handler(_event(
            contact_submit="1", contact_email="user@example.com",
            contact_message="", captcha_n1="2", captcha_n2="3", captcha_answer="5",
        ), None)
        assert r["statusCode"] == 400
        assert not rh.ses.send_email.called

    def test_contact_wrong_captcha_rejected(self, rh):
        r = rh.handler(_event(
            contact_submit="1", contact_email="user@example.com",
            contact_message="hi", captcha_n1="2", captcha_n2="3", captcha_answer="99",
        ), None)
        assert r["statusCode"] == 400
        assert not rh.ses.send_email.called


class TestAccountDeletion:
    def test_delete_requires_confirmation_step(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, delete_account="1"), None)
        assert r["statusCode"] == 200
        assert "削除を実行する" in r["body"]
        # 確認画面を出しただけではまだ削除されていない
        assert rh.dynamodb.Table("approved_tokens").get_item(Key={"token": VALID_TOKEN})["Item"]

    def test_delete_confirm_removes_token_watchlist_and_period_mode(self, rh):
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        rh.handler(_event(token=VALID_TOKEN, mode="SHORT"), None)

        r = rh.handler(_event(token=VALID_TOKEN, delete_account_confirm="1"), None)
        assert r["statusCode"] == 200
        assert "退会が完了しました" in r["body"]

        assert "Item" not in rh.dynamodb.Table("approved_tokens").get_item(Key={"token": VALID_TOKEN})
        assert rh.dynamodb.Table("watchlist").query(
            KeyConditionExpression=Key("owner_token").eq(VALID_TOKEN)
        )["Items"] == []
        assert rh.get_period_mode(VALID_TOKEN) == "MID"  # トークン失効後は既定モード扱いに戻る

        # 削除後は同じトークンでアクセスしても無効(お申込みフォームに戻る)
        r2 = rh.handler(_event(token=VALID_TOKEN), None)
        assert r2["statusCode"] == 403

    def test_delete_requires_valid_token(self, rh):
        r = rh.handler(_event(token="bogus", delete_account_confirm="1"), None)
        assert r["statusCode"] == 403
        assert "お申込み" in r["body"]


class TestReportCooldown:
    def test_first_request_is_accepted(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        assert r["statusCode"] == 200
        assert "受け付けました" in r["body"]
        assert rh.lambda_client.invoke.called

    def test_immediate_repeat_request_is_rejected(self, rh):
        rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        rh.lambda_client.invoke.reset_mock()

        r = rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        assert r["statusCode"] == 429
        assert "連続でのレポート送信リクエストはできません" in r["body"]
        assert not rh.lambda_client.invoke.called

    def test_request_after_cooldown_elapsed_is_accepted(self, rh):
        old_time = datetime.now(timezone.utc) - timedelta(
            seconds=rh.REPORT_REQUEST_COOLDOWN_SECONDS + 5)
        rh.dynamodb.Table("app_settings").put_item(
            Item={"key": f"{VALID_TOKEN}#last_report_request", "value": old_time.isoformat()})

        r = rh.handler(_event(token=VALID_TOKEN, report="monitoring"), None)
        assert r["statusCode"] == 200
        assert "受け付けました" in r["body"]
        assert rh.lambda_client.invoke.called

    def test_cooldown_is_isolated_per_user(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "other-tok", "email": "o@example.com", "status": "approved"})
        rh.handler(_event(token=VALID_TOKEN, report="selection"), None)

        r = rh.handler(_event(token="other-tok", report="selection"), None)
        assert r["statusCode"] == 200
        assert "受け付けました" in r["body"]

    def test_cooldown_record_removed_on_account_deletion(self, rh):
        rh.handler(_event(token=VALID_TOKEN, report="selection"), None)
        rh.handler(_event(token=VALID_TOKEN, delete_account_confirm="1"), None)
        assert "Item" not in rh.dynamodb.Table("app_settings").get_item(
            Key={"key": f"{VALID_TOKEN}#last_report_request"})


class TestPeriodMode:
    def test_mode_switch_persists(self, rh):
        rh.handler(_event(token=VALID_TOKEN, mode="SHORT"), None)
        assert rh.get_period_mode(VALID_TOKEN) == "SHORT"
        rh.handler(_event(token=VALID_TOKEN, mode="LONG"), None)
        assert rh.get_period_mode(VALID_TOKEN) == "LONG"

    def test_unknown_mode_falls_back_to_default(self, rh):
        assert rh.get_period_mode(VALID_TOKEN) == "MID"  # 未設定時はDEFAULT_PERIOD_MODE

    def test_mode_is_isolated_per_user(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "other-tok", "email": "o@example.com", "status": "approved"})
        rh.handler(_event(token=VALID_TOKEN, mode="SHORT"), None)
        assert rh.get_period_mode("other-tok") == "MID"  # 他人のモード変更は影響しない


class TestUniverse:
    """対象指数(ユニバース、NASDAQ100/S&P500)の切替。period_modeと全く同じ
    仕組み(app_settings + プリセット辞書)で実装しているため、テストも
    TestPeriodModeと対になるように揃えている(S&P500対応、2026-08-07)。"""

    def test_universe_switch_persists(self, rh):
        rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)
        assert rh.get_universe(VALID_TOKEN) == "SP500"
        rh.handler(_event(token=VALID_TOKEN, universe="NASDAQ100"), None)
        assert rh.get_universe(VALID_TOKEN) == "NASDAQ100"

    def test_unknown_universe_falls_back_to_default(self, rh):
        assert rh.get_universe(VALID_TOKEN) == "NASDAQ100"  # 未設定時はDEFAULT_UNIVERSE

    def test_universe_is_isolated_per_user(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": "other-tok", "email": "o@example.com", "status": "approved"})
        rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)
        assert rh.get_universe("other-tok") == "NASDAQ100"  # 他人のユニバース変更は影響しない

    def test_universe_change_notice_shows_new_universe_label(self, rh):
        r = rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)
        assert "対象指数を「S&P500」に変更しました" in r["body"]


class TestInfoPageUniverseWording:
    """?info=1はトップページからトークン無しでも辿れる(申込み前の判断材料)。
    未登録の訪問者に特定の指数を断定すると「NASDAQ100専用サービス」だと
    誤解され、「メイン画面で変更できます」と言っても意味が通らない。"""

    def test_info_page_without_token_does_not_assert_a_single_universe(self, rh):
        r = rh.handler(_event(info="1"), None)
        assert "NASDAQ100またはS&P500</strong>構成銘柄" in r["body"]
        assert "メイン画面の「対象指数」から変更できます" not in r["body"]

    def test_info_page_with_token_shows_the_users_own_universe(self, rh):
        rh.handler(_event(token=VALID_TOKEN, universe="SP500"), None)
        r = rh.handler(_event(token=VALID_TOKEN, info="1"), None)
        assert "S&P500</strong>構成銘柄" in r["body"]
        assert "メイン画面の「対象指数」から変更できます" in r["body"]

    def test_info_page_without_token_is_generic_in_english(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, info="1"), None)
        assert "NASDAQ100 or S&P 500</strong> constituents" in r["body"]
        assert "on the main screen" not in r["body"]


class TestInfoPagesLanguage:
    """?about=1・?contact=1・?privacy=1・?terms=1・?info=1の各ページが
    アクセス言語(X-Site-Langヘッダー)に応じて英訳版を返すことを確認する。"""

    def test_about_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, about="1"), None)
        assert r["statusCode"] == 200
        assert "About" in r["body"]
        assert "運営者情報" not in r["body"]

    def test_about_page_is_japanese_by_default(self, rh):
        r = rh.handler(_event(about="1"), None)
        assert "運営者情報" in r["body"]

    def test_contact_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, contact="1"), None)
        assert "Contact" in r["body"]
        assert "お問い合わせ" not in r["body"]

    def test_privacy_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, privacy="1"), None)
        assert "Privacy Policy" in r["body"]
        assert "プライバシーポリシー" not in r["body"]

    def test_terms_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, terms="1"), None)
        assert "Terms of Service" in r["body"]
        assert "利用規約" not in r["body"]

    def test_info_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, info="1"), None)
        assert "How this service works" in r["body"]
        assert "このサービスの仕組み" not in r["body"]

    def test_info_page_reflects_stored_lang_for_known_token(self, rh):
        rh.dynamodb.Table("approved_tokens").put_item(
            Item={"token": VALID_TOKEN, "email": "user@example.com",
                  "status": "approved", "lang": "en"})
        r = rh.handler(_event(_headers={"x-site-lang": "ja"}, token=VALID_TOKEN, info="1"), None)
        assert "How this service works" in r["body"]

    def test_contact_submit_error_messages_are_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, contact_submit="1",
                              contact_email="not-an-email", contact_message="hi",
                              captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert "isn't valid" in r["body"]

    def test_contact_submit_success_message_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, contact_submit="1",
                              contact_email="a@example.com", contact_message="hi",
                              captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert "We've received your message" in r["body"]


class TestAccessLogging:
    """CloudWatch Logsでアクセス傾向を把握するためのログ出力。メールアドレスや
    トークン本体がログに含まれないこと(プライバシーポリシーの遵守)を確認する。"""

    def test_logs_route_classification_without_leaking_email_or_token(self, rh, capsys):
        rh.handler(_event(token=VALID_TOKEN, ticker="NVDA"), None)
        out = capsys.readouterr().out
        assert "[ACCESS] route=ticker_action has_token=True" in out
        assert VALID_TOKEN not in out

    def test_logs_signup_route_without_leaking_email(self, rh, capsys):
        rh.handler(_event(signup_email="new@example.com", captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        out = capsys.readouterr().out
        assert "[ACCESS] route=signup has_token=False" in out
        assert "new@example.com" not in out

    def test_logs_main_form_route_when_no_special_params(self, rh, capsys):
        rh.handler(_event(token=VALID_TOKEN), None)
        out = capsys.readouterr().out
        assert "[ACCESS] route=main_form has_token=True" in out

    def test_logs_path_and_user_agent(self, rh, capsys):
        event = {
            "queryStringParameters": {},
            "rawPath": "/",
            "headers": {"user-agent": "Mozilla/5.0 (Macintosh)"},
        }
        rh.handler(event, None)
        out = capsys.readouterr().out
        assert "path='/'" in out
        assert "ua='Mozilla/5.0 (Macintosh)'" in out

    def test_logs_reveal_bot_hitting_nonexistent_path(self, rh, capsys):
        """クエリ文字列だけを見るroute分類では、正規のトップページとボットが
        叩く存在しないパスを区別できない。pathを見て初めて判別できることを確認する。"""
        event = {
            "queryStringParameters": {},
            "rawPath": "/wp-login.php",
            "headers": {"user-agent": "python-requests/2.31"},
        }
        rh.handler(event, None)
        out = capsys.readouterr().out
        assert "route=main_form" in out  # クエリだけでは正規訪問と同じ分類になる
        assert "path='/wp-login.php'" in out  # pathを見ればボットだとわかる
        assert "ua='python-requests/2.31'" in out

    # ===== 流入分析用のログ拡張(2026-08-06) =====
    # IPアドレスは記録しない方針を維持しつつ、リファラ・bot判定・流入元(?src=)を
    # [ACCESS]行に追加した。以前は上のtest_logs_reveal_bot_hitting_nonexistent_pathの
    # ようにpath/uaを見て人間が都度判断する必要があったが、bot=true/falseとして
    # 機械的に判定できるようにした。

    def test_access_log_includes_referer(self, rh, capsys):
        rh.handler(_event(_headers={"referer": "https://twitter.com/foo"}), None)
        assert "referer='https://twitter.com/foo'" in capsys.readouterr().out

    def test_access_log_flags_known_bot_user_agent(self, rh, capsys):
        rh.handler(_event(_headers={"user-agent": "curl/8.7.1"}), None)
        assert "bot=true" in capsys.readouterr().out

    def test_access_log_flags_unknown_path_as_bot_even_with_browser_ua(self, rh, capsys):
        """UAを詐称していても、実在しないパスへのアクセスはbot扱いにする"""
        event = _event(_headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        event["rawPath"] = "/wp-admin/install.php"
        rh.handler(event, None)
        assert "bot=true" in capsys.readouterr().out

    def test_access_log_does_not_flag_normal_browser_visit(self, rh, capsys):
        event = _event(_headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        event["rawPath"] = "/"
        rh.handler(event, None)
        assert "bot=false" in capsys.readouterr().out

    def test_access_log_captures_valid_src_param(self, rh, capsys):
        rh.handler(_event(src="twitter_launch"), None)
        assert "src='twitter_launch'" in capsys.readouterr().out

    def test_access_log_rejects_free_text_src_param(self, rh, capsys):
        """srcは英数字・_・-のみ許可。ログの行崩れ・任意文字列注入を防ぐ"""
        rh.handler(_event(src="evil\nvalue with spaces"), None)
        assert "src=''" in capsys.readouterr().out

    def test_access_log_does_not_include_ip_address(self, rh, capsys):
        """プライバシーポリシー通り、IPアドレスは記録しない"""
        event = _event()
        event["requestContext"] = {"http": {"sourceIp": "203.0.113.5"}}
        rh.handler(event, None)
        assert "203.0.113.5" not in capsys.readouterr().out

    def test_logs_default_path_when_missing_from_event(self, rh, capsys):
        rh.handler(_event(token=VALID_TOKEN), None)
        out = capsys.readouterr().out
        assert "path='/'" in out
        assert "ua=''" in out


class TestSignupsPaused:
    """SES本番アクセス待ち等で新規登録だけを一時停止する機能(SIGNUPS_ENABLED)。
    既存トークンでのアクセスには一切影響しないことを重点的に確認する。"""

    def test_new_visitor_sees_paused_page_when_signups_disabled(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 503
        assert "一時停止" in r["body"]
        assert "申し込む" not in r["body"]  # 実際の送信ボタンは出ない

    def test_invalid_token_sees_paused_page_when_signups_disabled(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(token="no-such-token"), None)
        assert r["statusCode"] == 403
        assert "一時停止" in r["body"]

    def test_direct_signup_submission_rejected_when_signups_disabled(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(signup_email="new@example.com", captcha_n1="2", captcha_n2="3", captcha_answer="5"), None)
        assert r["statusCode"] == 503
        assert "一時停止" in r["body"]
        assert not rh.ses.send_email.called
        for v in rh.dynamodb.Table("approved_tokens")._items.values():
            assert v.get("email") != "new@example.com"

    def test_existing_approved_user_unaffected_when_signups_disabled(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(token=VALID_TOKEN), None)
        assert r["statusCode"] == 200
        assert "監視リスト" in r["body"]

    def test_existing_user_can_still_delete_account_when_signups_disabled(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(token=VALID_TOKEN, delete_account="1"), None)
        assert r["statusCode"] == 200
        assert "退会" in r["body"]

    def test_paused_page_is_english_when_lang_is_en(self, rh):
        rh.SIGNUPS_ENABLED = False
        r = rh.handler(_event(_headers={"x-site-lang": "en"}), None)
        assert "temporarily paused" in r["body"]

    def test_signup_form_shown_normally_when_signups_enabled(self, rh):
        assert rh.SIGNUPS_ENABLED is True
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "お申込み" in r["body"]


class TestAiPrompt:
    """レポートを見て気になった銘柄をAIに分析させたい利用者向けのプロンプト
    コピーページ。トークン不要で使え、利用者データを一切読み書きしないことを確認する。"""

    def test_ai_prompt_page_shows_ticker_and_copy_button(self, rh):
        r = rh.handler(_event(ai_prompt="1", ticker="NVDA"), None)
        assert r["statusCode"] == 200
        assert "NVDA" in r["body"]
        assert "クリップボードにコピー" in r["body"]
        assert "navigator.clipboard.writeText" in r["body"]

    def test_ai_prompt_page_works_without_token(self, rh):
        r = rh.handler(_event(ai_prompt="1", ticker="AAPL"), None)
        assert r["statusCode"] == 200

    def test_ai_prompt_rejects_invalid_ticker(self, rh):
        r = rh.handler(_event(ai_prompt="1", ticker="../../etc"), None)
        assert r["statusCode"] == 400

    def test_ai_prompt_page_is_english_when_lang_is_en(self, rh):
        r = rh.handler(_event(_headers={"x-site-lang": "en"}, ai_prompt="1", ticker="NVDA"), None)
        assert "Copy to clipboard" in r["body"]
        assert "クリップボードにコピー" not in r["body"]

    def test_ai_prompt_page_respects_explicit_lang_query_param(self, rh):
        """メールリンクに付与するlang=enクエリパラメータが、X-Site-Langヘッダー
        (今回は日本語版ドメイン相当)より優先されることを確認する。"""
        r = rh.handler(_event(ai_prompt="1", ticker="NVDA", lang="en"), None)
        assert "Copy to clipboard" in r["body"]

    def test_ai_prompt_disclaims_not_investment_advice(self, rh):
        r = rh.handler(_event(ai_prompt="1", ticker="NVDA"), None)
        assert "投資助言ではありません" in r["body"]
