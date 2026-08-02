"""
signals.pyのユニットテスト。
2026-08-02にアドホックで手動検証した内容(docs/test-cases.md D1-D7)をコード化したもの。
"""
import warnings

import pandas as pd
import numpy as np
import pytest

from config import get_preset
from signals import _cycle_window, calc_momentum_signal, calc_volume_signal


def _bdate_series(start, end, exclude=None, value=100.0, seed=None):
    idx = pd.bdate_range(start, end)
    if exclude:
        idx = idx[~idx.isin(pd.to_datetime(exclude))]
    if seed is not None:
        rng = np.random.default_rng(seed)
        data = value + np.cumsum(rng.standard_normal(len(idx)))
    else:
        data = [value] * len(idx)
    return pd.Series(data, index=idx)


class TestCycleWindow:
    def test_day_unit_returns_only_latest(self):
        s = _bdate_series("2026-06-01", "2026-06-10", seed=1)
        window = _cycle_window(s, "day")
        assert len(window) == 1
        assert window.index[-1] == s.index[-1]

    def test_week_unit_excludes_holiday(self):
        """独立記念日(7/3金)の休場を挟んだ前の暦週は、実取引日4日分だけで
        構成されること(休場日があれば自然に減る)。"""
        s = _bdate_series("2026-05-01", "2026-07-08", exclude=["2026-07-03"], seed=1)
        window = _cycle_window(s, "week")
        assert len(window) == 4
        assert list(window.index.date) == [
            pd.Timestamp("2026-06-29").date(),
            pd.Timestamp("2026-06-30").date(),
            pd.Timestamp("2026-07-01").date(),
            pd.Timestamp("2026-07-02").date(),
        ]

    def test_month_unit_returns_previous_calendar_month(self):
        s = _bdate_series("2026-05-01", "2026-07-08", exclude=["2026-07-03"], seed=1)
        window = _cycle_window(s, "month")
        assert len(window) == 22
        assert window.index.min().month == 6

    def test_tz_aware_index_does_not_warn(self):
        """yfinanceの実データはタイムゾーン付き。to_periodのtz drop警告が
        出ないこと(出るとログがノイズだらけになり運用上の可視性を損なう)。"""
        s = _bdate_series("2026-05-01", "2026-07-08", exclude=["2026-07-03"], seed=1)
        s.index = s.index.tz_localize("America/New_York")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            window = _cycle_window(s, "week")
        assert len(window) == 4

    def test_insufficient_data_falls_back_to_latest_day(self):
        s = pd.Series([1.0, 2.0], index=pd.bdate_range("2026-07-01", "2026-07-02"))
        window = _cycle_window(s, "month")
        assert len(window) == 1
        assert window.index[-1] == s.index[-1]


class TestMomentumSignal:
    def _df_with_flat_close(self, n=60):
        idx = pd.bdate_range("2026-01-01", periods=n)
        return pd.DataFrame({"Close": [100.0] * n, "Volume": [1_000_000] * n}, index=idx)

    def test_symmetric_threshold_buy_at_oversold_boundary(self, monkeypatch):
        import signals as sm
        monkeypatch.setattr(sm, "_rsi", lambda close, period: pd.Series([25.0] * len(close), index=close.index))
        preset = dict(get_preset("SHORT"))  # rsi_oversold=25, rsi_overbought=75
        result = calc_momentum_signal(self._df_with_flat_close(), preset)
        assert result["buy_momentum"] is True
        assert result["sell_momentum"] is False

    def test_symmetric_threshold_sell_at_overbought_boundary(self, monkeypatch):
        import signals as sm
        monkeypatch.setattr(sm, "_rsi", lambda close, period: pd.Series([75.0] * len(close), index=close.index))
        preset = dict(get_preset("SHORT"))
        result = calc_momentum_signal(self._df_with_flat_close(), preset)
        assert result["buy_momentum"] is False
        assert result["sell_momentum"] is True

    def test_neutral_rsi_triggers_neither(self, monkeypatch):
        import signals as sm
        monkeypatch.setattr(sm, "_rsi", lambda close, period: pd.Series([50.0] * len(close), index=close.index))
        preset = dict(get_preset("MID"))
        result = calc_momentum_signal(self._df_with_flat_close(), preset)
        assert result["buy_momentum"] is False
        assert result["sell_momentum"] is False


class TestVolumeSignal:
    def test_mid_baseline_excludes_judged_period(self):
        """中期/長期モードでは判定期間そのものを基準値の集計から除く(自己参照なし)。
        判定期間だけ出来高を極端に増やしても基準値が汚染されないこと。"""
        idx = pd.bdate_range("2026-05-01", "2026-07-08")
        idx = idx[idx != pd.Timestamp("2026-07-03")]
        close = pd.Series(100.0, index=idx)
        vol = pd.Series(1_000_000, index=idx)

        from signals import _cycle_window
        judged_window = _cycle_window(close, "week")
        vol.loc[judged_window.index] = 999_000_000

        df = pd.DataFrame({"Close": close, "Volume": vol})
        preset = dict(get_preset("MID"))
        result = calc_volume_signal(df, preset)

        assert result["volume_high"] is True
        # 基準値が汚染されていれば比率は1倍近くになるはず。実際は999倍相当。
        assert result["volume_ratio"] > 100

    def test_day_unit_uses_rolling_average_including_today(self):
        idx = pd.bdate_range("2026-01-01", periods=30)
        vol = pd.Series(1_000_000, index=idx)
        vol.iloc[-1] = 2_000_000  # 当日だけ2倍
        df = pd.DataFrame({"Close": pd.Series(100.0, index=idx), "Volume": vol})
        preset = dict(get_preset("SHORT"))  # volume_multiplier=1.5, cycle_unit=day
        result = calc_volume_signal(df, preset)
        assert result["volume_high"] is True
