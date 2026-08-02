"""
サービス共通の設定値。

このモジュールは daily-batch と register-stock の両方から読み込まれるため、
pandas等のサードパーティに依存させないこと
(register-stockはLayerを付けていない軽量関数のため)。
"""

# ===== 銘柄選定(ランキング)の足切り条件 =====
MIN_GROWTH = 0.20   # 利益成長率の下限
MAX_PEG = 3.0       # PEGレシオの上限
DEFAULT_TOP_N = 3

# ===== 外部API障害耐性 =====
# 選定レポートで銘柄データ取得の成功率がこれを下回ったら、空同然のレポートを送らず
# 例外を送出してエラー通知に切り替える(個別銘柄のたまたまの失敗ではなく、
# 外部APIそのものが落ちていると判断できる水準)
MIN_FETCH_SUCCESS_RATIO = 0.5
NASDAQ100_CACHE_KEY = "nasdaq100_tickers_cache"

# ===== トレンド判定の方式 =====
# "MA"(移動平均線) または "MACD"。現在はMAのみ運用。
MA_OR_MACD = "MA"

# ===== 期間モードのプリセット =====
# ma_short/ma_long: クロス判定に使う2本の移動平均
# ma_filter: 並び順(パーフェクトオーダー)判定に追加する長期線。Noneなら使わない
# cycle_unit: RSI/出来高の判定サイクル。"day"=当日、"week"=前の暦週、"month"=前の暦月
#   (週/月は実際にあった取引日だけを平均する。休場日があれば自然に減る)
# history_period: yfinanceに渡す取得期間
# min_rows: この行数未満の銘柄は計算対象外(長い移動平均がNaNになるため)
PERIOD_PRESETS = {
    "SHORT": {
        "label": "短期",
        "description": "値動きの変化に早く反応する。シグナルは多いが騙しも多い。",
        "ma_short": 5,
        "ma_long": 20,
        "ma_filter": None,
        "macd_fast": 5, "macd_slow": 13, "macd_signal": 5,
        "rsi_period": 9,
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "volume_lookback": 20,
        "volume_multiplier": 1.5,
        "cycle_unit": "day",
        "history_period": "6mo",
        "min_rows": 60,
    },
    "MID": {
        "label": "中期",
        "description": "日足スイングの標準設定。200日線で相場の地合いを絞り込む。",
        "ma_short": 20,
        "ma_long": 50,
        "ma_filter": 200,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "volume_lookback": 20,
        "volume_multiplier": 1.5,
        "cycle_unit": "week",
        "history_period": "2y",
        "min_rows": 201,
    },
    "LONG": {
        "label": "長期",
        "description": "50日線と200日線のクロスで相場全体の転換を捉える。点灯は年に数回程度。",
        "ma_short": 50,
        "ma_long": 200,
        "ma_filter": None,
        "macd_fast": 19, "macd_slow": 39, "macd_signal": 9,
        "rsi_period": 21,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "volume_lookback": 50,
        "volume_multiplier": 1.5,
        "cycle_unit": "month",
        "history_period": "2y",
        "min_rows": 201,
    },
}

DEFAULT_PERIOD_MODE = "MID"
SETTINGS_TABLE_DEFAULT = "app_settings"
PERIOD_MODE_KEY = "period_mode"


def get_preset(mode: str | None) -> dict:
    """モード名からプリセットを返す。未知の値は既定モードにフォールバックする"""
    return PERIOD_PRESETS.get((mode or "").upper(), PERIOD_PRESETS[DEFAULT_PERIOD_MODE])


def normalize_mode(mode: str | None) -> str:
    m = (mode or "").upper()
    return m if m in PERIOD_PRESETS else DEFAULT_PERIOD_MODE
