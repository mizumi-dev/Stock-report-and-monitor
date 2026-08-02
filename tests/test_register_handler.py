"""
register-stock Lambda(mnt/user-data/outputs/aws_deploy/lambda_register/handler.py)の
コンポーネントテスト。DynamoDBをtests/fake_dynamodb.pyの軽量fakeに差し替えて実施する。

daily-batch側にも同名の"handler.py"があるため、モジュール名衝突を避けるべく
importlib.util.spec_from_file_locationで明示的な別名("register_handler")としてロードする。
"""
import importlib.util
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

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
    return module


def _event(**params):
    return {"queryStringParameters": {k: v for k, v in params.items() if v is not None}}


class TestAuth:
    def test_missing_token_shows_signup_form(self, rh):
        r = rh.handler(_event(), None)
        assert r["statusCode"] == 200
        assert "お申込み" in r["body"]

    def test_invalid_token_shows_signup_form(self, rh):
        r = rh.handler(_event(token="bogus"), None)
        assert r["statusCode"] == 403
        assert "お申込み" in r["body"]

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
