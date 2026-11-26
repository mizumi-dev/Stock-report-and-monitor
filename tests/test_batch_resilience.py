"""
2026-11-25の本番障害の再発防止テスト。
監視レポートの保存時、値にNaNが混ざっていてDynamoDBが拒否(TypeError: Infinity and NaN
not supported)→バッチ全体がエラー終了→Lambdaの自動再試行で選定レポートが3通届き、
監視レポートは届かなかった。
"""
import math
from decimal import Decimal

import pandas as pd
import pytest

import handler
from fake_dynamodb import FakeDynamoDBResource


class StrictTable:
    """本物のDynamoDB(boto3)と同じく、NaN・無限大のDecimalを拒否するテーブル"""
    def __init__(self, inner):
        self.inner = inner

    def put_item(self, Item, **kw):
        def check(v):
            if isinstance(v, float):
                raise TypeError("Float types are not supported. Use Decimal types instead.")
            if isinstance(v, Decimal) and not v.is_finite():
                raise TypeError("Infinity and NaN not supported")
            if isinstance(v, dict):
                for x in v.values():
                    check(x)
            if isinstance(v, list):
                for x in v:
                    check(x)
        check(Item)
        return self.inner.put_item(Item=Item, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDynamoDBResource({
        "app_settings": ("key", None),
        "signal_history": ("ticker", "date"),
    })
    class Resource:
        def Table(self, name):
            return StrictTable(fake.Table(name))
    monkeypatch.setattr(handler, "dynamodb", Resource())
    monkeypatch.setattr(handler, "SETTINGS_TABLE", "app_settings")
    monkeypatch.setattr(handler, "SIGNAL_HISTORY_TABLE", "signal_history")
    return fake


def test_nan_and_infinity_are_stored_as_none():
    safe = handler._to_dynamodb_safe({"rsi": float("nan"), "ratio": float("inf"), "price": 1.5,
                                      "nested": {"x": float("-inf")}, "items": [float("nan"), 2.0]})
    assert safe == {"rsi": None, "ratio": None, "price": Decimal("1.5"),
                    "nested": {"x": None}, "items": [None, Decimal("2.0")]}


def _patch_monitoring(monkeypatch, signals_for):
    hist = pd.DataFrame({"Close": [1.0] * 5})
    monkeypatch.setattr(handler, "get_user_watchlist", lambda tok: ["GOOD", "NANY", "BOOM"])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda tok: "SHORT")
    monkeypatch.setattr(handler, "get_prev_signals", lambda t, mode: None)
    monkeypatch.setattr(handler, "get_stock_data", lambda t, period, min_rows: {
        "ticker": t, "history": hist, "current_price": 10.0, "prev_close": 9.0,
        "peg": float("nan") if t == "NANY" else 0.5})
    monkeypatch.setattr(handler, "calc_all_signals", signals_for)
    captured = {}
    def fake_build(results, mode, skipped, *a, **k):
        captured["results"] = [r["ticker"] for r in results]
        captured["skipped"] = skipped
        return "body"
    monkeypatch.setattr(handler, "build_monitoring_report", fake_build)
    sent = []
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to: sent.append(to))
    return captured, sent


def test_monitoring_survives_nan_value_and_one_broken_ticker(db, monkeypatch):
    calc = lambda history, mode: {"rsi": float("nan"), "buy_cross": False}
    captured, sent = _patch_monitoring(monkeypatch, calc)
    # BOOMだけ計算で例外を出す
    orig = handler.get_stock_data
    def get_stock_data(t, period, min_rows):
        if t == "BOOM":
            raise ValueError("unexpected data")
        return orig(t, period, min_rows)
    monkeypatch.setattr(handler, "get_stock_data", get_stock_data)

    handler.run_monitoring_reports([{"token": "tok", "email": "a@example.com"}])

    assert sent == ["a@example.com"]  # 落ちずに送られる
    assert sorted(captured["results"]) == ["GOOD", "NANY"]  # NaNの銘柄もレポートには載る
    assert captured["skipped"] == ["BOOM"]
    saved = db.Table("signal_history").get_item(
        Key={"ticker": "NANY", "date": handler.signal_history_date_key(handler._today_str(), "SHORT")})["Item"]
    assert saved["peg"] is None and saved["signals"]["rsi"] is None


def test_scheduled_retry_does_not_resend_reports(db, monkeypatch):
    """1回目の実行が送信後に落ちて自動再試行されても、同じ日は二重に送らない"""
    monkeypatch.setattr(handler, "get_user_period_mode", lambda tok: "SHORT")
    monkeypatch.setattr(handler, "get_user_universes", lambda tok: ["NASDAQ100"])
    scans = []
    monkeypatch.setattr(handler, "_scan_universe", lambda mode, u: scans.append(u) or ([], {}))
    monkeypatch.setattr(handler, "build_selection_report", lambda *a, **k: "body")
    sent = []
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to: sent.append(to))
    users = [{"token": "tok-a", "email": "a@example.com"}]

    handler.run_selection_reports(users, dedupe=True)
    handler.run_selection_reports(users, dedupe=True)  # 自動再試行
    assert sent == ["a@example.com"]
    assert scans == ["NASDAQ100"]  # 送信済みなら再スキャンもしない

    # 手動のテスト送信(dedupeなし)は従来どおり何度でも送れる
    handler.run_selection_reports(users)
    assert sent == ["a@example.com", "a@example.com"]


def test_scheduled_retry_does_not_resend_monitoring(db, monkeypatch):
    captured, sent = _patch_monitoring(monkeypatch, lambda h, m: {"buy_cross": False})
    users = [{"token": "tok", "email": "a@example.com"}]
    handler.run_monitoring_reports(users, dedupe=True)
    handler.run_monitoring_reports(users, dedupe=True)
    assert sent == ["a@example.com"]


def test_dispatch_scheduled_path_uses_dedupe(monkeypatch):
    calls = {}
    monkeypatch.setattr(handler, "get_approved_users", lambda: [{"token": "t", "email": "e"}])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda tok: "SHORT")
    monkeypatch.setattr(handler, "run_selection_reports", lambda users, **k: calls.update(sel=k))
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda users, **k: calls.update(mon=k))
    monkeypatch.setattr(handler, "post_weekly_tweet", lambda: None)
    handler._dispatch({"simulate_date": "2026-11-25T11:00:00+00:00"})
    assert calls == {"sel": {"dedupe": True}, "mon": {"dedupe": True}}


def test_monitoring_is_still_sent_when_selection_fails(monkeypatch):
    """選定レポートのスキャンが失敗(Yahooに止められた等)しても監視レポートは送り、
    失敗はその後に送出してエラー通知に乗せる"""
    order = []
    monkeypatch.setattr(handler, "get_approved_users", lambda: [{"token": "t", "email": "e"}])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda tok: "SHORT")
    def failing_selection(users, **k):
        order.append("selection")
        raise RuntimeError("予想成長率の取得成功率が低すぎます")
    monkeypatch.setattr(handler, "run_selection_reports", failing_selection)
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda users, **k: order.append("monitoring"))
    monkeypatch.setattr(handler, "post_weekly_tweet", lambda: order.append("tweet"))
    with pytest.raises(RuntimeError, match="予想成長率"):
        handler._dispatch({"simulate_date": "2026-11-25T11:00:00+00:00"})
    assert order == ["selection", "monitoring"]
