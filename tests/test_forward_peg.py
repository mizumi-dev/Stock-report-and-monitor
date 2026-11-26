"""
PEGの自前計算(2026-11-26〜)。
- PEG = 予想PER ÷ 来期の予想EPS成長率(%)。来期の成長率は基本情報の今期・来期の予想EPS
  (epsCurrentYear・forwardEps)から計算し、追加の問い合わせをしない
- 一時的な落ち込みからの反動を外すため、今期の予想EPS成長率が-10%未満・今期予想なし・
  実績EPSが赤字の銘柄を足切りで除外。今期の成長率は他の足切りを通った候補にだけ問い合わせる
"""
from unittest import mock

import pandas as pd
import pytest

import data_sources
import handler
from fake_dynamodb import FakeDynamoDBResource
from ranking import passes_gate, passes_pre_gate


def _stock(eps_cur, eps_next, fpe, trailing_eps=3.0, current_year_growth=0.3):
    s = data_sources.apply_forward_peg({"forward_pe": fpe, "trailing_eps": trailing_eps,
                                        "eps_current_year": eps_cur, "eps_next_year": eps_next})
    s["growth_basis"] = "forward_1y"
    s["current_year_growth"] = current_year_growth
    return s


class TestForwardPeg:
    def test_next_year_growth_from_eps_matches_yahoo_estimates(self):
        # 2026-11-26の実データ: NVDA 9.67→16.61 = +71.7%、TWLO 6.17→6.85 = +11.1%
        assert data_sources.next_year_growth(9.67246, 16.60528) == pytest.approx(0.7168, abs=1e-3)
        assert data_sources.next_year_growth(6.16678, 6.85219) == pytest.approx(0.1111, abs=1e-3)

    def test_next_year_growth_none_for_loss_or_missing(self):
        assert data_sources.next_year_growth(-0.99, 10.15) is None  # GILD(今期赤字予想)
        assert data_sources.next_year_growth(None, 2.0) is None
        assert data_sources.next_year_growth(2.0, None) is None

    def test_divides_forward_pe_by_growth_in_percent(self):
        assert data_sources.forward_peg(26.4, 0.111) == pytest.approx(2.378, abs=1e-3)

    def test_forward_peg_none_when_growth_or_pe_not_positive(self):
        assert data_sources.forward_peg(10.0, None) is None
        assert data_sources.forward_peg(10.0, -0.15) is None
        assert data_sources.forward_peg(-5.0, 0.3) is None

    def test_apply_sets_growth_and_peg(self):
        s = _stock(9.67246, 16.60528, 8.99)
        assert s["earnings_growth"] == pytest.approx(0.7168, abs=1e-3)
        assert s["peg"] == pytest.approx(8.99 / 71.68, abs=1e-3)

    def test_twlo_is_excluded_by_next_year_growth(self):
        assert passes_gate(_stock(6.16678, 6.85219, 26.38, trailing_eps=7.96, current_year_growth=0.26)) is False

    def test_nvda_passes(self):
        assert passes_gate(_stock(9.67246, 16.60528, 8.99, trailing_eps=9.31, current_year_growth=1.03)) is True


class TestDipExclusion:
    """今期の一時的な落ち込みからの反動を外すルール(閾値はユーザー判断で-10%)"""
    def test_rebound_after_big_current_year_drop_is_excluded(self):
        assert passes_gate(_stock(2.0, 4.2, 10.0, current_year_growth=-0.40)) is False  # CHTR型

    def test_small_dip_within_10_percent_is_kept(self):
        assert passes_gate(_stock(2.0, 2.5, 10.0, current_year_growth=-0.01)) is True   # META型
        assert passes_gate(_stock(2.0, 2.5, 10.0, current_year_growth=-0.10)) is True
        assert passes_gate(_stock(2.0, 2.5, 10.0, current_year_growth=-0.11)) is False

    def test_missing_current_year_estimate_is_excluded(self):
        assert passes_gate(_stock(2.0, 2.5, 10.0, current_year_growth=None)) is False

    def test_trailing_loss_is_excluded(self):
        assert passes_gate(_stock(2.0, 2.5, 10.0, trailing_eps=-1.2)) is False
        assert passes_pre_gate(_stock(2.0, 2.5, 10.0, trailing_eps=None)) is False

    def test_rules_do_not_apply_to_old_yahoo_peg_rows(self):
        assert passes_gate({"earnings_growth": 0.5, "peg": 1.0}) is True

class TestGetGrowthEstimate:
    def _ticker(self, df):
        tk = mock.MagicMock()
        tk.growth_estimates = df
        return tk

    def test_reads_next_year_stock_trend(self, monkeypatch):
        df = pd.DataFrame({"stockTrend": [0.26, 0.11], "indexTrend": [0.3, 0.2]}, index=["0y", "+1y"])
        monkeypatch.setattr(data_sources.yf, "Ticker", lambda t: self._ticker(df))
        assert data_sources.get_growth_estimate("TWLO") == {"current_year": pytest.approx(0.26),
                                                            "next_year": pytest.approx(0.11)}

    def test_none_when_no_estimate(self, monkeypatch):
        df = pd.DataFrame({"stockTrend": [float("nan")]}, index=["+1y"])
        monkeypatch.setattr(data_sources.yf, "Ticker", lambda t: self._ticker(df))
        assert data_sources.get_growth_estimate("X") == {"current_year": None, "next_year": None}
        monkeypatch.setattr(data_sources.yf, "Ticker", lambda t: self._ticker(None))
        assert data_sources.get_growth_estimate("X") is None


@pytest.fixture
def settings(monkeypatch):
    fake = FakeDynamoDBResource({"app_settings": ("key", None)})
    monkeypatch.setattr(handler, "dynamodb", fake)
    monkeypatch.setattr(handler, "SETTINGS_TABLE", "app_settings")
    return fake.Table("app_settings")


class TestGrowthCache:
    def test_fetches_once_per_day_and_shares_between_calls(self, settings, monkeypatch):
        calls = []
        def fake(t):
            calls.append(t)
            return {"AAA": {"current_year": 0.1, "next_year": 0.3}, "BBB": None}[t]
        monkeypatch.setattr(handler, "get_growth_estimate", fake)

        first, failed = handler.get_growth_estimates(["AAA", "BBB"])
        assert first == {"AAA": {"current_year": 0.1, "next_year": 0.3}, "BBB": None} and failed == 0
        # 2回目(別モード・別指数のスキャン)は問い合わせない。「予想なし」(None)もキャッシュ済み
        second, _ = handler.get_growth_estimates(["BBB", "AAA"])
        assert second == {"AAA": {"current_year": 0.1, "next_year": 0.3}, "BBB": None}
        assert sorted(calls) == ["AAA", "BBB"]

    def test_only_missing_tickers_are_fetched(self, settings, monkeypatch):
        monkeypatch.setattr(handler, "get_growth_estimate", lambda t: {"current_year": 0.0, "next_year": 0.25})
        handler.get_growth_estimates(["AAA"])
        calls = []
        monkeypatch.setattr(handler, "get_growth_estimate",
                            lambda t: calls.append(t) or {"current_year": 0.0, "next_year": 0.4})
        result, _ = handler.get_growth_estimates(["AAA", "CCC"])
        assert calls == ["CCC"]
        assert result["AAA"]["next_year"] == 0.25 and result["CCC"]["next_year"] == 0.4

    def test_failed_fetch_is_not_cached_and_counted(self, settings, monkeypatch):
        def boom(t):
            raise RuntimeError("401 Invalid Crumb")
        monkeypatch.setattr(handler, "get_growth_estimate", boom)
        result, failed = handler.get_growth_estimates(["AAA"])
        assert result == {"AAA": None} and failed == 1
        # 次の呼び出しでは取り直す
        monkeypatch.setattr(handler, "get_growth_estimate", lambda t: {"current_year": 0.1, "next_year": 0.5})
        result, failed = handler.get_growth_estimates(["AAA"])
        assert result["AAA"]["next_year"] == 0.5 and failed == 0

    def test_yesterdays_cache_is_ignored(self, settings, monkeypatch):
        settings.put_item(Item={"key": handler.GROWTH_CACHE_KEY, "as_of_date": "2000-01-01",
                                "values": {"AAA": {"current_year": 0.0, "next_year": 0.9}}})
        monkeypatch.setattr(handler, "get_growth_estimate", lambda t: {"current_year": 0.0, "next_year": 0.2})
        result, _ = handler.get_growth_estimates(["AAA"])
        assert result["AAA"]["next_year"] == 0.2


class TestFillForwardPeg:
    def test_only_pre_gate_candidates_are_asked_for_current_year_growth(self, settings, monkeypatch):
        calls = []
        monkeypatch.setattr(handler, "get_growth_estimate",
                            lambda t: calls.append(t) or {"current_year": 0.5, "next_year": 0.7})
        stocks = [
            {"ticker": "NVDA", "forward_pe": 8.99, "trailing_eps": 9.31, "eps_current_year": 9.67, "eps_next_year": 16.6},
            {"ticker": "TWLO", "forward_pe": 26.4, "trailing_eps": 7.96, "eps_current_year": 6.17, "eps_next_year": 6.85},
            {"ticker": "GILD", "forward_pe": 17.4, "trailing_eps": -3.1, "eps_current_year": -0.99, "eps_next_year": 10.1},
        ]
        requested, failed = handler._fill_forward_peg(stocks)
        assert calls == ["NVDA"] and requested == 1 and failed == 0
        assert stocks[0]["current_year_growth"] == 0.5
        assert stocks[1]["current_year_growth"] is None and stocks[2]["peg"] is None
        assert all(s["growth_basis"] == handler.GROWTH_BASIS_FORWARD for s in stocks)


class TestScanUniverseUsesForwardPeg:
    STOCKS = {
        "TWLO": dict(forward_pe=26.4, trailing_eps=7.96, eps_current_year=6.17, eps_next_year=6.85),
        "NVDA": dict(forward_pe=9.0, trailing_eps=9.31, eps_current_year=9.67, eps_next_year=16.6),
        "AVGO": dict(forward_pe=15.5, trailing_eps=5.0, eps_current_year=8.0, eps_next_year=13.3),
        "CHTR": dict(forward_pe=3.2, trailing_eps=30.0, eps_current_year=20.0, eps_next_year=42.2),
    }
    CURRENT_YEAR = {"NVDA": 1.03, "AVGO": 0.71, "CHTR": -0.40, "TWLO": 0.26}

    def _setup(self, monkeypatch, growth_fn):
        hist = pd.DataFrame({"Close": [1.0] * 10})
        monkeypatch.setattr(handler, "get_cached_tickers", lambda u: None)
        monkeypatch.setattr(handler, "save_cached_tickers", lambda u, t: None)
        monkeypatch.setattr(handler, "get_nasdaq100_tickers", lambda fallback=None: list(self.STOCKS))
        monkeypatch.setattr(handler, "get_stock_data", lambda t, period, min_rows: {
            "ticker": t, "history": hist, "market_cap": 1, **self.STOCKS[t]})
        monkeypatch.setattr(handler, "calc_all_signals", lambda h, mode: {})
        monkeypatch.setattr(handler, "get_growth_estimate", growth_fn)
        saved = {}
        monkeypatch.setattr(handler, "save_selection_snapshot",
                            lambda mode, ranked, sm, universe: saved.update(ranked=ranked))
        return saved

    def test_ranks_by_computed_peg_and_drops_rebounds(self, settings, monkeypatch):
        saved = self._setup(monkeypatch, lambda t: {"current_year": self.CURRENT_YEAR[t], "next_year": None})
        ranked, _ = handler._scan_universe("MID", "NASDAQ100")
        assert [s["ticker"] for s in ranked] == ["NVDA", "AVGO"]  # CHTRは今期-40%で除外、TWLOは来期+11%
        assert saved["ranked"][0]["growth_basis"] == handler.GROWTH_BASIS_FORWARD

    def test_raises_when_estimates_mostly_fail(self, settings, monkeypatch):
        def boom(t):
            raise RuntimeError("401")
        self._setup(monkeypatch, boom)
        with pytest.raises(RuntimeError, match="予想成長率の取得成功率"):
            handler._scan_universe("MID", "NASDAQ100")


def test_test_scan_hook_scans_without_sending(monkeypatch):
    """診断フック: メールは送らず、スキャン結果の要約だけを返す"""
    import json
    calls = []
    monkeypatch.setattr(handler, "_scan_universe", lambda mode, universe: calls.append((mode, universe)) or (
        [{"ticker": "NVDA", "peg": 0.125, "earnings_growth": 0.72}], {"NVDA": {}, "AAPL": {}}))
    monkeypatch.setattr(handler, "send_email", lambda *a, **k: (_ for _ in ()).throw(AssertionError("送信された")))
    r = handler._dispatch({"_test_scan": {"mode": "MID", "universe": "SP500"}})
    body = json.loads(r["body"])
    assert calls == [("MID", "SP500")]
    assert body["universe"] == "SP500" and body["scanned"] == 2
    assert body["top"] == [{"ticker": "NVDA", "peg": 0.125, "growth": 0.72}]



def test_stock_info_is_fetched_once_per_day_across_modes(monkeypatch):
    """基本情報はモードをまたいで1日1回だけ問い合わせる(Yahooの負荷対策)"""
    data_sources._INFO_CACHE.clear()
    info_calls = []
    class FakeTicker:
        def __init__(self, t):
            self.t = t
        def history(self, period):
            return pd.DataFrame({"Close": [1.0] * 300})
        @property
        def info(self):
            info_calls.append(self.t)
            return {"forwardPE": 10.0, "trailingEps": 2.0, "shortName": self.t}
    monkeypatch.setattr(data_sources.yf, "Ticker", FakeTicker)
    a = data_sources.get_stock_data("AAA", period="6mo", min_rows=60)
    b = data_sources.get_stock_data("AAA", period="2y", min_rows=201)
    assert info_calls == ["AAA"]
    assert a["forward_pe"] == b["forward_pe"] == 10.0
    data_sources._INFO_CACHE.clear()
