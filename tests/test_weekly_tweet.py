"""
handler.post_weekly_tweetのテスト。集客の自動化(2026-08-10)で追加した週次X投稿。
X_POST_ENABLEDのオプトイン、保存済みスナップショットの再利用、投稿失敗が
本来の役務(メール配信)を止めないことを確認する。
"""
import handler


def test_post_weekly_tweet_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(handler, "X_POST_ENABLED", False)
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode, universe: (_ for _ in ()).throw(
        AssertionError("X_POST_ENABLED=falseなら呼ばれてはいけない")))

    handler.post_weekly_tweet()  # 例外が出なければOK


def test_post_weekly_tweet_uses_cached_snapshot_without_scanning(monkeypatch):
    monkeypatch.setattr(handler, "X_POST_ENABLED", True)
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode, universe: {
        "ranked": [{"ticker": "NVDA", "peg": 1.2, "company_name": "NVIDIA"}],
        "signals_map": {},
        "as_of_date": "2026-08-10",
    })
    monkeypatch.setattr(handler, "_scan_universe", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("スナップショットがあるので新規スキャンしてはいけない")))

    posted = {}
    monkeypatch.setattr(handler.x_poster, "post_tweet", lambda text: posted.setdefault("text", text))

    handler.post_weekly_tweet()

    assert "NVDA" in posted["text"]


def test_post_weekly_tweet_scans_when_no_snapshot_yet(monkeypatch):
    monkeypatch.setattr(handler, "X_POST_ENABLED", True)
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode, universe: None)
    monkeypatch.setattr(handler, "_scan_universe", lambda mode, universe: (
        [{"ticker": "AAPL", "peg": 2.0, "company_name": "Apple"}], {}
    ))

    posted = {}
    monkeypatch.setattr(handler.x_poster, "post_tweet", lambda text: posted.setdefault("text", text))

    handler.post_weekly_tweet()

    assert "AAPL" in posted["text"]


def test_post_weekly_tweet_swallows_post_failure(monkeypatch):
    monkeypatch.setattr(handler, "X_POST_ENABLED", True)
    monkeypatch.setattr(handler, "get_selection_snapshot", lambda mode, universe: {
        "ranked": [{"ticker": "NVDA", "peg": 1.2, "company_name": "NVIDIA"}],
        "signals_map": {},
        "as_of_date": "2026-08-10",
    })

    def fail(text):
        raise RuntimeError("X API error 401: Unauthorized")

    monkeypatch.setattr(handler.x_poster, "post_tweet", fail)

    handler.post_weekly_tweet()  # 例外が外に伝播しなければOK


def test_dispatch_posts_weekly_tweet_on_report_day(monkeypatch):
    monkeypatch.setattr(handler, "IS_WEEKLY_REPORT_DAY", "0")
    monkeypatch.setattr(handler, "get_approved_users", lambda: [])
    monkeypatch.setattr(handler, "run_selection_reports", lambda targets, **k: None)
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda users, **k: None)

    called = []
    monkeypatch.setattr(handler, "post_weekly_tweet", lambda: called.append(True))

    handler._dispatch({"simulate_date": "2026-08-10T00:00:00+00:00"})  # 2026-08-10は月曜(weekday=0)

    assert called == [True]


def test_dispatch_skips_weekly_tweet_on_non_report_day(monkeypatch):
    monkeypatch.setattr(handler, "IS_WEEKLY_REPORT_DAY", "0")
    monkeypatch.setattr(handler, "get_approved_users", lambda: [])
    monkeypatch.setattr(handler, "run_selection_reports", lambda targets, **k: None)
    monkeypatch.setattr(handler, "run_monitoring_reports", lambda users, **k: None)

    called = []
    monkeypatch.setattr(handler, "post_weekly_tweet", lambda: called.append(True))

    handler._dispatch({"simulate_date": "2026-08-11T00:00:00+00:00"})  # 火曜(weekday=1)

    assert called == []
