"""
シグナル計算ロジック
- MA/MACD (排他選択、短期/中期切替)
- RSI (モメンタム)
- 出来高
"""
import pandas as pd

# ===== config（configで切替可能な値） =====
MA_OR_MACD = "MA"        # "MA" または "MACD"
PERIOD_MODE = "MID"      # "SHORT" または "MID"
VOLUME_MULTIPLIER = 1.5
VOLUME_LOOKBACK = 20
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


def _sma(series, window):
    return series.rolling(window=window).mean()


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close, period=RSI_PERIOD):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def calc_trend_signal(df: pd.DataFrame) -> dict:
    """
    MA または MACD の買い/売りシグナルを計算する。
    戻り値: {'buy_trend': bool, 'sell_trend': bool}
    """
    close = df["Close"]

    if MA_OR_MACD == "MA":
        if PERIOD_MODE == "SHORT":
            short, long_ = _sma(close, 5), _sma(close, 20)
        else:  # MID
            short, long_ = _sma(close, 20), _sma(close, 50)
            sma200 = _sma(close, 200)

        golden_cross = (short.iloc[-1] > long_.iloc[-1]) and (short.iloc[-2] <= long_.iloc[-2])
        dead_cross = (short.iloc[-1] < long_.iloc[-1]) and (short.iloc[-2] >= long_.iloc[-2])

        if PERIOD_MODE == "SHORT":
            perfect_order_up = close.iloc[-1] > short.iloc[-1] > long_.iloc[-1]
            perfect_order_down = close.iloc[-1] < short.iloc[-1] < long_.iloc[-1]
        else:
            perfect_order_up = close.iloc[-1] > short.iloc[-1] > long_.iloc[-1] > sma200.iloc[-1]
            perfect_order_down = close.iloc[-1] < short.iloc[-1] < long_.iloc[-1] < sma200.iloc[-1]

        return {
            "buy_cross": bool(golden_cross),
            "buy_alignment": bool(perfect_order_up),
            "sell_cross": bool(dead_cross),
            "sell_alignment": bool(perfect_order_down),
        }

    else:  # MACD
        if PERIOD_MODE == "SHORT":
            fast, slow, sig = 5, 13, 5
        else:  # MID (標準)
            fast, slow, sig = 12, 26, 9

        macd_line = _ema(close, fast) - _ema(close, slow)
        signal_line = _ema(macd_line, sig)

        signal_cross_up = (macd_line.iloc[-1] > signal_line.iloc[-1]) and (macd_line.iloc[-2] <= signal_line.iloc[-2])
        signal_cross_down = (macd_line.iloc[-1] < signal_line.iloc[-1]) and (macd_line.iloc[-2] >= signal_line.iloc[-2])
        zero_up = macd_line.iloc[-1] > 0
        zero_down = macd_line.iloc[-1] < 0

        return {
            "buy_cross": bool(signal_cross_up),
            "buy_alignment": bool(zero_up),
            "sell_cross": bool(signal_cross_down),
            "sell_alignment": bool(zero_down),
        }


def calc_momentum_signal(df: pd.DataFrame) -> dict:
    """RSIによる買い/売りモメンタムシグナル"""
    close = df["Close"]
    rsi = _rsi(close)
    latest_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]

    buy = bool((latest_rsi <= 40) and (latest_rsi > prev_rsi))
    sell = bool(latest_rsi >= RSI_OVERBOUGHT)

    return {"buy_momentum": buy, "sell_momentum": sell, "rsi": round(float(latest_rsi), 1)}


def calc_volume_signal(df: pd.DataFrame) -> dict:
    """出来高が過去N日平均の1.5倍を超えているか（買い/売り方向を持たない中立指標）"""
    vol = df["Volume"]
    avg = vol.rolling(window=VOLUME_LOOKBACK).mean().iloc[-1]
    latest = vol.iloc[-1]
    ratio = float(latest / avg) if avg else 0.0
    return {"volume_high": bool(ratio >= VOLUME_MULTIPLIER), "volume_ratio": round(ratio, 2)}


def calc_all_signals(df: pd.DataFrame) -> dict:
    """1銘柄分のシグナルをまとめて計算"""
    trend = calc_trend_signal(df)
    momentum = calc_momentum_signal(df)
    volume = calc_volume_signal(df)

    buy_trend = trend["buy_cross"] or trend["buy_alignment"]
    sell_trend = trend["sell_cross"] or trend["sell_alignment"]

    return {
        "buy_trend": buy_trend,
        "buy_momentum": momentum["buy_momentum"],
        "sell_trend": sell_trend,
        "sell_momentum": momentum["sell_momentum"],
        "volume_high": volume["volume_high"],
        "volume_ratio": volume["volume_ratio"],
        "rsi": momentum["rsi"],
        "trend_detail": trend,
    }
