"""
マルチテナント化(利用者ごとのwatchlist/期間モード、モードごとの選定スキャン共有)と、
レポート即時発行ボタンの低コスト化(保存済みスナップショットを読むだけで新規計算・
新規スキャンをしない設計)のユニットテスト。
"""
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
    def fake_build(ranked, signals_map, url, token, mode, lang="ja", as_of=None):
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
    monkeypatch.setattr(handler, "save_signal_snapshot", lambda t, mode, entry: None)
    monkeypatch.setattr(handler, "build_monitoring_report", lambda *a, **k: "body")

    sent = []
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to_address: sent.append(to_address))

    handler.run_monitoring_reports(users)

    assert sent == ["a@example.com"]  # tok-bは監視リストが空なので送られない


def test_monitoring_reports_fetch_shared_ticker_only_once(monkeypatch):
    """同じ銘柄を複数人が監視リストに登録していても、取得・計算は1回だけであること
    (低コスト化の副次効果として、定時実行自体の重複取得も解消したことの確認)。"""
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    watchlists = {"tok-a": ["NVDA"], "tok-b": ["NVDA"]}
    monkeypatch.setattr(handler, "get_user_watchlist", lambda token: watchlists[token])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")

    fetch_calls = []
    def fake_get_stock_data(t, period, min_rows):
        fetch_calls.append(t)
        return {"ticker": t, "history": "H", "current_price": 1, "prev_close": 1, "peg": 1}
    monkeypatch.setattr(handler, "get_stock_data", fake_get_stock_data)
    monkeypatch.setattr(handler, "calc_all_signals", lambda hist, mode: {})
    monkeypatch.setattr(handler, "get_prev_signals", lambda t, mode: None)
    monkeypatch.setattr(handler, "save_signal_snapshot", lambda t, mode, entry: None)
    monkeypatch.setattr(handler, "build_monitoring_report", lambda *a, **k: "body")
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.run_monitoring_reports(users)

    assert fetch_calls == ["NVDA"]  # 2人が登録していても取得は1回だけ


def test_send_monitoring_report_now_uses_cached_snapshot_without_fetching(monkeypatch):
    """レポート即時発行ボタンは、保存済みスナップショットがあれば新規取得しない
    (yfinance呼び出しゼロ・低コスト化の核心部分)。"""
    monkeypatch.setattr(handler, "get_user_watchlist", lambda token: ["NVDA"])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")
    monkeypatch.setattr(handler, "get_latest_signal_snapshot", lambda t, mode: {
        "signals": {"buy_trend": True}, "price": 100, "prev_close": 99,
        "peg": 1.2, "prev_signals": None, "as_of_date": "2026-08-01",
    })

    def fail_if_called(*a, **k):
        raise AssertionError("キャッシュがあるのにget_stock_dataが呼ばれた")
    monkeypatch.setattr(handler, "get_stock_data", fail_if_called)

    captured = {}
    def fake_build(results, mode, skipped, base_url, token, lang="ja", as_of=None):
        captured["results"] = results
        captured["as_of"] = as_of
        return "body"
    monkeypatch.setattr(handler, "build_monitoring_report", fake_build)
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.send_monitoring_report_now(_user("tok-a", "a@example.com"))

    assert captured["results"] == [{
        "ticker": "NVDA", "price": 100, "prev_close": 99,
        "signals": {"buy_trend": True}, "prev_signals": None, "peg": 1.2,
    }]
    assert captured["as_of"] == "2026-08-01"


def test_send_monitoring_report_now_fetches_and_caches_when_no_snapshot(monkeypatch):
    """まだ誰の分も計算されていない銘柄(登録直後等)だけは、その場で1回取得・保存する。"""
    monkeypatch.setattr(handler, "get_user_watchlist", lambda token: ["NVDA"])
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")
    monkeypatch.setattr(handler, "get_latest_signal_snapshot", lambda t, mode: None)
    monkeypatch.setattr(handler, "get_stock_data", lambda t, period, min_rows: {
        "ticker": t, "history": "H", "current_price": 100, "prev_close": 99, "peg": 1.2,
    })
    monkeypatch.setattr(handler, "calc_all_signals", lambda hist, mode: {"buy_trend": True})
    monkeypatch.setattr(handler, "get_prev_signals", lambda t, mode: None)

    saved = []
    monkeypatch.setattr(handler, "save_signal_snapshot", lambda t, mode, entry: saved.append((t, mode, entry)))
    monkeypatch.setattr(handler, "build_monitoring_report", lambda *a, **k: "body")
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.send_monitoring_report_now(_user("tok-a", "a@example.com"))

    assert len(saved) == 1
    assert saved[0][0] == "NVDA"
    assert saved[0][2]["signals"] == {"buy_trend": True}


def test_send_selection_report_now_uses_cached_snapshot_without_scanning(monkeypatch):
    """レポート即時発行ボタンは、保存済みスナップショットがあれば新規スキャンしない。"""
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode: {
        "ranked": [{"ticker": "NVDA"}], "signals_map": {"NVDA": {}}, "as_of_date": "2026-08-01",
    })

    def fail_if_called(mode):
        raise AssertionError("キャッシュがあるのに_scan_universeが呼ばれた")
    monkeypatch.setattr(handler, "_scan_universe", fail_if_called)

    captured = {}
    def fake_build(ranked, signals_map, url, token, mode, lang="ja", as_of=None):
        captured["ranked"] = ranked
        captured["as_of"] = as_of
        return "body"
    monkeypatch.setattr(handler, "build_selection_report", fake_build)
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.send_selection_report_now(_user("tok-a", "a@example.com"))

    assert captured["ranked"] == [{"ticker": "NVDA"}]
    assert captured["as_of"] == "2026-08-01"


def test_send_selection_report_now_scans_when_no_snapshot(monkeypatch):
    """そのモードで一度もバッチが走っていない場合のみ、その場で1回スキャンする。"""
    monkeypatch.setattr(handler, "get_user_period_mode", lambda token: "MID")
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode: None)

    scan_calls = []
    def fake_scan(mode):
        scan_calls.append(mode)
        return ([{"ticker": "NVDA"}], {"NVDA": {}})
    monkeypatch.setattr(handler, "_scan_universe", fake_scan)
    monkeypatch.setattr(handler, "build_selection_report", lambda *a, **k: "body")
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: None)

    handler.send_selection_report_now(_user("tok-a", "a@example.com"))

    assert scan_calls == ["MID"]


def test_dispatch_monitoring_only_with_owner_token_sends_to_requester_only(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    monkeypatch.setattr(handler, "get_approved_users", lambda: users)

    received = []
    monkeypatch.setattr(handler, "send_monitoring_report_now", lambda user: received.append(user))

    handler.handler({"monitoring_only": True, "owner_token": "tok-a"}, None)

    assert len(received) == 1
    assert received[0]["token"] == "tok-a"


def test_dispatch_force_weekly_with_owner_token_sends_to_requester_only(monkeypatch):
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    monkeypatch.setattr(handler, "get_approved_users", lambda: users)

    received = []
    monkeypatch.setattr(handler, "send_selection_report_now", lambda user: received.append(user))

    handler.handler({"force_weekly": True, "owner_token": "tok-a"}, None)

    assert len(received) == 1
    assert received[0]["token"] == "tok-a"


def test_dispatch_monitoring_only_without_owner_token_falls_back_to_bulk_run(monkeypatch):
    """owner_token無しの呼び出し(テスト・運用目的の一斉送信用)は従来通り
    run_monitoring_reportsに全利用者を渡す。"""
    users = [_user("tok-a", "a@example.com"), _user("tok-b", "b@example.com")]
    monkeypatch.setattr(handler, "get_approved_users", lambda: users)

    received = []
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda targets: received.extend(targets))

    handler.handler({"monitoring_only": True}, None)

    assert sorted(u["token"] for u in received) == ["tok-a", "tok-b"]
