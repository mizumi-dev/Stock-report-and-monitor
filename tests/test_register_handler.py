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


class TestAccessRecoveryHint:
    def test_signup_form_mentions_resend_on_lost_link(self, rh):
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "アクセス用リンクを再送します" in r["body"]


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
