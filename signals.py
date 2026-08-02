"""
シグナル計算ロジック
- MA/MACD (排他選択、期間モードで短期/中期/長期を切替)
- RSI (モメンタム)
- 出来高

期間ごとの具体的な数値は config.PERIOD_PRESETS に集約している。
"""
import pandas as pd

from config import MA_OR_MACD, get_preset


def _sma(series, window):
    return series.rolling(window=window).mean()


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close, period: int):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def calc_trend_signal(df: pd.DataFrame, preset: dict) -> dict:
    """
    MA または MACD の買い/売りシグナルを計算する。
    戻り値: {'buy_cross','buy_alignment','sell_cross','sell_alignment'}
    """
    close = df["Close"]

    if MA_OR_MACD == "MA":
        short = _sma(close, preset["ma_short"])
        long_ = _sma(close, preset["ma_long"])

        golden_cross = (short.iloc[-1] > long_.iloc[-1]) and (short.iloc[-2] <= long_.iloc[-2])
        dead_cross = (short.iloc[-1] < long_.iloc[-1]) and (short.iloc[-2] >= long_.iloc[-2])

        # 並び順(パーフェクトオーダー)。ma_filterがあれば最長線として末尾に加える
        levels_up = [close.iloc[-1], short.iloc[-1], long_.iloc[-1]]
        levels_down = list(levels_up)
        if preset["ma_filter"]:
            filt = _sma(close, preset["ma_filter"]).iloc[-1]
            levels_up.append(filt)
            levels_down.append(filt)

        # NaNが混ざると比較は常にFalseになる。データ不足を黙って握りつぶさないよう
        # 呼び出し側(data_sources)でmin_rowsを担保している前提。
        perfect_order_up = all(a > b for a, b in zip(levels_up, levels_up[1:]))
        perfect_order_down = all(a < b for a, b in zip(levels_down, levels_down[1:]))

        return {
            "buy_cross": bool(golden_cross),
            "buy_alignment": bool(perfect_order_up),
            "sell_cross": bool(dead_cross),
            "sell_alignment": bool(perfect_order_down),
        }

    # MACD方式(現在は未使用。MA_OR_MACDを切り替えたときのみ動く)
    macd_line = _ema(close, preset["macd_fast"]) - _ema(close, preset["macd_slow"])
    signal_line = _ema(macd_line, preset["macd_signal"])

    signal_cross_up = (macd_line.iloc[-1] > signal_line.iloc[-1]) and (macd_line.iloc[-2] <= signal_line.iloc[-2])
    signal_cross_down = (macd_line.iloc[-1] < signal_line.iloc[-1]) and (macd_line.iloc[-2] >= signal_line.iloc[-2])

    return {
        "buy_cross": bool(signal_cross_up),
        "buy_alignment": bool(macd_line.iloc[-1] > 0),
        "sell_cross": bool(signal_cross_down),
        "sell_alignment": bool(macd_line.iloc[-1] < 0),
    }


def _cycle_window(series: pd.Series, unit: str) -> pd.Series:
    """判定サイクル分のウィンドウを返す。
    "day"は当日のみ、"week"/"month"は前の暦週/暦月に実際にあった取引日だけを拾う
    (休場日があれば自然に減る)。データ不足でウィンドウが空になった場合は
    当日1件にフォールバックする(黙って全銘柄が判定不能になるのを防ぐため)。"""
    if unit == "day":
        return series.iloc[-1:]
    freq = "W-SUN" if unit == "week" else "M"
    # yfinanceのインデックスは取引所タイムゾーン付き。暦週/暦月への丸めはタイムゾーンに
    # 依存しないため、意図的にtz情報を落として警告を出さないようにする。
    naive_index = series.index.tz_localize(None) if series.index.tz is not None else series.index
    periods = naive_index.to_period(freq)
    prev_period = naive_index[-1].to_period(freq) - 1
    window = series[periods == prev_period]
    return window if not window.empty else series.iloc[-1:]


def calc_momentum_signal(df: pd.DataFrame, preset: dict) -> dict:
    """RSIによる買い/売りモメンタムシグナル。
    判定サイクル分のRSI平均が、売られすぎ/買われすぎの閾値を跨いだら
    即座にシグナルとする(対称な閾値判定)。"""
    close = df["Close"]
    rsi = _rsi(close, preset["rsi_period"])
    judged = float(_cycle_window(rsi, preset["cycle_unit"]).mean())

    buy = bool(judged <= preset["rsi_oversold"])
    sell = bool(judged >= preset["rsi_overbought"])

    return {"buy_momentum": buy, "sell_momentum": sell, "rsi": round(judged, 1)}


def calc_volume_signal(df: pd.DataFrame, preset: dict) -> dict:
    """出来高が基準値の指定倍を超えているか（買い/売り方向を持たない中立指標）。
    "day"モードは直近1日 vs 直近lookback日平均(当日を含む)。
    "week"/"month"モードは判定サイクル平均 vs その直前lookback日平均
    (判定期間は基準値の集計から除く、自己参照なし)。"""
    vol = df["Volume"]
    unit = preset["cycle_unit"]
    lookback = preset["volume_lookback"]
    window = _cycle_window(vol, unit)
    judged = float(window.mean())

    if unit == "day":
        baseline = float(vol.rolling(window=lookback).mean().iloc[-1])
    else:
        start_pos = vol.index.get_loc(window.index[0])
        baseline_slice = vol.iloc[max(0, start_pos - lookback):start_pos]
        baseline = float(baseline_slice.mean()) if len(baseline_slice) else 0.0

    ratio = judged / baseline if baseline else 0.0
    return {"volume_high": bool(ratio >= preset["volume_multiplier"]), "volume_ratio": round(ratio, 2)}


def calc_all_signals(df: pd.DataFrame, mode: str | None = None) -> dict:
    """1銘柄分のシグナルをまとめて計算"""
    preset = get_preset(mode)
    trend = calc_trend_signal(df, preset)
    momentum = calc_momentum_signal(df, preset)
    volume = calc_volume_signal(df, preset)

    return {
        "buy_trend": trend["buy_cross"] or trend["buy_alignment"],
        "buy_momentum": momentum["buy_momentum"],
        "sell_trend": trend["sell_cross"] or trend["sell_alignment"],
        "sell_momentum": momentum["sell_momentum"],
        "volume_high": volume["volume_high"],
        "volume_ratio": volume["volume_ratio"],
        "rsi": momentum["rsi"],
        "trend_detail": trend,
    }
