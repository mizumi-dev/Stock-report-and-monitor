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
# 銘柄データ取得の並列数。非公式APIへの負荷・レート制限リスクを考慮し、
# 全並列ではなくI/O待ち解消に十分な程度に抑える
FETCH_MAX_WORKERS = 10

# ===== 銘柄ユニバース(対象指数) =====
# 期間モードと同じ仕組み(app_settingsテーブル + このプリセット辞書)で
# 利用者ごとに切り替え可能にする(2026-08-07、S&P500追加着手時に新設)。
# NASDAQ100が既定・唯一の対象だった時期のcache_keyとの後方互換のため、
# NASDAQ100_CACHE_KEYは従来通りモジュールレベル定数としても残す
UNIVERSES = {
    "NASDAQ100": {
        "label": "NASDAQ100",
        "label_en": "NASDAQ100",
        "cache_key": NASDAQ100_CACHE_KEY,
    },
    "SP500": {
        "label": "S&P500",
        "label_en": "S&P 500",
        "cache_key": "sp500_tickers_cache",
    },
}
DEFAULT_UNIVERSE = "NASDAQ100"
UNIVERSE_KEY = "universe"

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
        "label_en": "Short-term",
        "description": "値動きの変化に早く反応する。シグナルは多いが騙しも多い。",
        "description_en": "Reacts fast to price moves. More signals, but more false ones too.",
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
        "label_en": "Mid-term",
        "description": "日足スイングの標準設定。200日線で相場の地合いを絞り込む。",
        "description_en": "Standard daily-swing setup, filtered by the 200-day average.",
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
        "label_en": "Long-term",
        "description": "50日線と200日線のクロスで相場全体の転換を捉える。点灯は年に数回程度。",
        "description_en": "Tracks broad trend reversals via the 50/200-day cross. Fires a few times a year.",
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

# ===== マルチテナント =====
# 利用者の識別は招待トークンそのものを流用する(別途user_idを発行しない)。
# approved_tokensのstatus: "pending"=メール確認待ち、"approved"=利用可能
TOKEN_STATUS_PENDING = "pending"
TOKEN_STATUS_APPROVED = "approved"
WATCHLIST_MAX_PER_USER_DEFAULT = 5


def get_preset(mode: str | None) -> dict:
    """モード名からプリセットを返す。未知の値は既定モードにフォールバックする"""
    return PERIOD_PRESETS.get((mode or "").upper(), PERIOD_PRESETS[DEFAULT_PERIOD_MODE])


def normalize_mode(mode: str | None) -> str:
    m = (mode or "").upper()
    return m if m in PERIOD_PRESETS else DEFAULT_PERIOD_MODE


def user_period_mode_key(owner_token: str) -> str:
    """app_settingsテーブルで利用者ごとの期間モードを保存する際のキー。
    利用者ごとに分離しつつ、テーブルの主キー構成(keyのみ)を変えずに済む
    (文字列に利用者を埋め込むだけなのでテーブル作り直し不要)。"""
    return f"{owner_token}#{PERIOD_MODE_KEY}"


def normalize_universe(universe: str | None) -> str:
    u = (universe or "").upper()
    return u if u in UNIVERSES else DEFAULT_UNIVERSE


def get_universe_preset(universe: str | None) -> dict:
    """ユニバース名からプリセットを返す。未知の値は既定ユニバースにフォールバックする"""
    return UNIVERSES.get((universe or "").upper(), UNIVERSES[DEFAULT_UNIVERSE])


def universe_cache_key(universe: str | None) -> str:
    """app_settingsに構成銘柄一覧をキャッシュする際のキー。ユニバースごとに別キー"""
    return get_universe_preset(universe)["cache_key"]


def user_universe_key(owner_token: str) -> str:
    """app_settingsテーブルで利用者ごとのユニバース選択を保存する際のキー
    (user_period_mode_keyと同じ仕組み)。"""
    return f"{owner_token}#{UNIVERSE_KEY}"


def signal_history_date_key(date_str: str, mode: str, universe: str | None = None) -> str:
    """signal_historyのdate(RANGE key)に期間モード・ユニバースも埋め込む。
    RSI/トレンドはモード・ユニバースで値が変わるため、組み合わせごとに別キャッシュに
    する必要がある(ただし利用者ごとではなく、同じ組み合わせの利用者同士で共有できる)。
    既定ユニバース(NASDAQ100)は既存データとの後方互換のためサフィックスを付けない。"""
    key = f"{date_str}#{normalize_mode(mode)}"
    universe = normalize_universe(universe)
    if universe != DEFAULT_UNIVERSE:
        key += f"#{universe}"
    return key


def selection_snapshot_key(mode: str, universe: str | None = None) -> str:
    """app_settingsに選定レポートのスナップショット(ランキング+シグナル)を
    モード・ユニバースごとに保存する際のキー。レポート即時発行ボタンはこの
    スナップショットを読むだけで、新規スキャンは行わない(利用者間で共有する
    定時実行結果と常に一致させ、ボタン連打によるコスト増・シグナル履歴汚染を
    防ぐため)。既定ユニバース(NASDAQ100)は既存データとの後方互換のため
    サフィックスを付けない。"""
    key = f"selection_snapshot#{normalize_mode(mode)}"
    universe = normalize_universe(universe)
    if universe != DEFAULT_UNIVERSE:
        key += f"#{universe}"
    return key
