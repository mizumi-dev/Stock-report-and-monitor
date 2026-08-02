"""
マルチテナント化(利用者ごとのwatchlist/期間モード、モードごとの選定スキャン共有)の
ユニットテスト。
"""
from unittest import mock

import handler


def _user(token, email):
    return {"token": token, "email": email}


def test_selection_reports_scan_once_per_distinct_mode(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com"), _user("tok-c", "c@example.com")]
    modes = {"tok-a": "SHORT", "tok-b": "SHORT", "tok-c": "LONG"}
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: modes[token])

    scan_calls = []
    def fake_scan(mode):
        scan_calls.append(mode)
        return ([], {})
    monkeypatch.setattr(handler, "_scan_universe", fake_scan)

    sent = []
    monkeypatch.setattr(handler, "build_selection_report", lambda *a, **k: "body")
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to_address: sent.append(to_address))

    handler.run_selection_reports(users)

    assert sorted(scan_calls) == ["LONG", "SHORT"]  # SHORTは2人いても1回だけスキャン
    assert sorted(sent) == ["a@example.com", "b@example.com", "c@example.com"]


def test_selection_report_links_use_each_users_own_token(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "SHORT")
    monkeypatch.setattr(handler, "_scan_universe", lambda mode: ([], {}))

    used_tokens = []
    def fake_build(ranked, signals_map, url, token, label):
        used_tokens.append(token)
        return "body"
    monkeypatch.setattr(handler, "build_selection_report", fake_build)
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.run_selection_reports(users)
    assert sorted(used_tokens) == ["tok-a", "tok-b"]


def test_monitoring_reports_use_each_users_own_watchlist(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    watchlists = {"tok-a": ["NVDA"], "tok-b": []}  # tok-bは空なのでスキップされるはず
    monkeypatch.setattr(handler, "get_user_watchlist", lambda token: watchlists[token])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")
    monkeypatch.setattr(handler, "get_stock_data", lambda t, period, min_rows: {
        "ticker": t, "history": "H", "current_price": 1, "prev_close": 1, "peg": 1,
    })
    monkeypatch.setattr(handler, "calc_all_signals", lambda hist, mode: {})
    monkeypatch.setattr(handler, "get_prev_signals", lambda t, mode: None)
    monkeypatch.setattr(handler, "save_signals", lambda t, mode, sig: None)
    monkeypatch.setattr(handler, "build_monitoring_report", lambda *a, **k: "body")

    sent = []
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to_address: sent.append(to_address))

    handler.run_monitoring_reports(users)

    assert sent == ["a@example.com"]  # tok-bは監視リストが空なので送られない


def test_dispatch_monitoring_only_with_owner_token_sends_to_requester_only(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    monkeypatch.setattr(handler, "get_approved_users", lambda: users)

    received = []
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda targets: received.extend(targets))

    handler.handler({"monitoring_only": True, "owner_token": "tok-a"}, None)

    assert len(received) == 1
    assert received[0]["token"] == "tok-a"
