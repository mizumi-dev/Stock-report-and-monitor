"""
PEGの自前計算(2026-11-26〜): PEG = 予想PER ÷ 来期の予想EPS成長率(%)。
YahooのpegRatioが異常値(TWLOで0.01等)を返し、成長率も直近四半期の前年同期比
だったため一時的な利益で足切りを通っていた問題への対応。
"""
from unittest import mock

import pandas as pd
import pytest

import data_sources
import handler
from fake_dynamodb import FakeDynamoDBResource
from ranking import passes_gate


class TestForwardPeg:
    def test_divides_forward_pe_by_growth_in_percent(self):
        # TWLOの実データ(2026-11-26): 予想PER26.4、来期+11.1% → 2.38
        assert data_sources.forward_peg(26.4, 0.111) == pytest.approx(2.378, abs=1e-3)

    def test_none_when_growth_is_missing_zero_or_negative(self):
        assert data_sources.forward_peg(10.0, None) is None
        assert data_sources.forward_peg(10.0, 0.0) is None
        assert data_sources.forward_peg(10.0, -0.15) is None  # 減益予想(MPC等)

    def test_none_when_forward_pe_is_missing_or_not_positive(self):
        assert data_sources.forward_peg(None, 0.3) is None
        assert data_sources.forward_peg(-5.0, 0.3) is None  # 赤字予想

    def test_apply_sets_growth_and_peg(self):
        s = data_sources.apply_forward_peg({"forward_pe": 9.0}, {"current_year": 1.03, "next_year": 0.72})
        assert s["earnings_growth"] == 0.72 and s["current_year_growth"] == 1.03
        assert s["peg"] == pytest.approx(0.125)

    def test_twlo_like_case_is_now_excluded_by_gate(self):
        """一時的な利益で直近四半期が+335%でも、来期予想が+11%なら足切りで落ちる"""
        s = data_sources.apply_forward_peg({"forward_pe": 26.4, "trailing_eps": 7.96, "growth_basis": "forward_1y"},
                                           {"current_year": 0.26, "next_year": 0.111})
        assert passes_gate(s) is False

    def test_high_growth_reasonable_pe_passes_gate(self):
        s = data_sources.apply_forward_peg({"forward_pe": 9.0, "trailing_eps": 4.0, "growth_basis": "forward_1y"},
                                           {"current_year": 1.03, "next_year": 0.72})  # NVDA相当
        assert passes_gate(s) is True


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
    def test_skips_estimate_lookup_when_forward_pe_missing(self, settings, monkeypatch):
        calls = []
        monkeypatch.setattr(handler, "get_growth_estimate",
                            lambda t: calls.append(t) or {"current_year": 0.1, "next_year": 0.3})
        stocks = [{"ticker": "AAA", "forward_pe": 12.0}, {"ticker": "LOSS", "forward_pe": -3.0},
                  {"ticker": "NONE", "forward_pe": None}]
        requested, failed = handler._fill_forward_peg(stocks)
        assert calls == ["AAA"] and requested == 1 and failed == 0
        assert stocks[0]["peg"] == pytest.approx(0.4)
        assert stocks[1]["peg"] is None and stocks[2]["peg"] is None
        assert all(s["growth_basis"] == handler.GROWTH_BASIS_FORWARD for s in stocks)


class TestScanUniverseUsesForwardPeg:
    def _setup(self, monkeypatch, growth_fn):
        hist = pd.DataFrame({"Close": [1.0] * 10})
        stocks = {
            "TWLO": {"forward_pe": 26.4, "trailing_eps": 7.96},  # 来期+11% → 足切りで落ちる
            "NVDA": {"forward_pe": 9.0, "trailing_eps": 4.0},    # 来期+72% → PEG 0.125
            "AVGO": {"forward_pe": 15.5, "trailing_eps": 5.0},   # 来期+66% → PEG 0.235
            "GILD": {"forward_pe": 17.4, "trailing_eps": 0.5},   # 今期-112%の反動で来期+1121% → 除外
        }
        monkeypatch.setattr(handler, "get_cached_tickers", lambda u: None)
        monkeypatch.setattr(handler, "save_cached_tickers", lambda u, t: None)
        monkeypatch.setattr(handler, "get_nasdaq100_tickers", lambda fallback=None: list(stocks))
        monkeypatch.setattr(handler, "get_stock_data", lambda t, period, min_rows: {
            "ticker": t, "history": hist, "market_cap": 1, "peg": None, "earnings_growth": None, **stocks[t]})
        monkeypatch.setattr(handler, "calc_all_signals", lambda h, mode: {})
        monkeypatch.setattr(handler, "get_growth_estimate", growth_fn)
        saved = {}
        monkeypatch.setattr(handler, "save_selection_snapshot",
                            lambda mode, ranked, sm, universe: saved.update(ranked=ranked))
        return saved

    def test_ranks_by_computed_peg(self, settings, monkeypatch):
        g = {"TWLO": (0.26, 0.111), "NVDA": (1.03, 0.72), "AVGO": (0.71, 0.66), "GILD": (-1.12, 11.21)}
        saved = self._setup(monkeypatch, lambda t: {"current_year": g[t][0], "next_year": g[t][1]})
        ranked, _ = handler._scan_universe("MID", "NASDAQ100")
        assert [s["ticker"] for s in ranked] == ["NVDA", "AVGO"]
        assert saved["ranked"][0]["growth_basis"] == handler.GROWTH_BASIS_FORWARD

    def test_raises_when_estimates_mostly_fail(self, settings, monkeypatch):
        """Yahooにアクセスを止められた場合、「該当なし」レポートではなくエラー通知にする"""
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



class TestDipExclusion:
    """今期の一時的な落ち込みからの反動を外すルール(2026-11-26、閾値はユーザー判断で-10%)"""
    def _stock(self, cur, nxt=0.6, eps=3.0, fpe=10.0):
        return data_sources.apply_forward_peg(
            {"forward_pe": fpe, "trailing_eps": eps, "growth_basis": "forward_1y"},
            {"current_year": cur, "next_year": nxt})

    def test_rebound_after_big_current_year_drop_is_excluded(self):
        assert passes_gate(self._stock(cur=-1.12, nxt=11.21)) is False  # GILD
        assert passes_gate(self._stock(cur=-0.40, nxt=1.11)) is False   # CHTR

    def test_small_current_year_dip_within_10_percent_is_kept(self):
        assert passes_gate(self._stock(cur=-0.01, nxt=0.21)) is True   # META
        assert passes_gate(self._stock(cur=-0.10)) is True             # ちょうど-10%は残す
        assert passes_gate(self._stock(cur=-0.11)) is False

    def test_missing_current_year_estimate_is_excluded(self):
        assert passes_gate(self._stock(cur=None)) is False

    def test_trailing_loss_is_excluded(self):
        assert passes_gate(self._stock(cur=0.5, eps=-1.2)) is False
        assert passes_gate(self._stock(cur=0.5, eps=None)) is False

    def test_rules_do_not_apply_to_old_yahoo_peg_rows(self):
        """growth_basisの無い旧方式の行(テスト・過去データ)には適用しない"""
        assert passes_gate({"earnings_growth": 0.5, "peg": 1.0}) is True



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
