"""
config.pyのユニットテスト。銘柄ユニバース(対象指数)関連のヘルパー関数
(2026-08-07、S&P500対応の一環で追加)を対象とする。既存キー(NASDAQ100)の
後方互換性を守れているかが特に重要(既存のselection_snapshot・
signal_historyのキー形式が変わると、過去に保存済みのデータと不整合になる)。
"""
from config import (
    DEFAULT_UNIVERSE,
    UNIVERSES,
    get_universe_preset,
    normalize_universe,
    normalize_universes,
    universes_label,
    universes_to_value,
    selection_snapshot_key,
    signal_history_date_key,
    universe_cache_key,
    user_universe_key,
)


class TestNormalizeUniverse:
    def test_known_universe_passes_through_uppercased(self):
        assert normalize_universe("sp500") == "SP500"
        assert normalize_universe("NASDAQ100") == "NASDAQ100"

    def test_unknown_universe_falls_back_to_default(self):
        assert normalize_universe("NIKKEI225") == DEFAULT_UNIVERSE

    def test_none_falls_back_to_default(self):
        assert normalize_universe(None) == DEFAULT_UNIVERSE

    def test_empty_string_falls_back_to_default(self):
        assert normalize_universe("") == DEFAULT_UNIVERSE


class TestGetUniversePreset:
    def test_returns_preset_for_known_universe(self):
        assert get_universe_preset("SP500")["label"] == "S&P500"

    def test_unknown_universe_falls_back_to_default_preset(self):
        assert get_universe_preset("BOGUS") == UNIVERSES[DEFAULT_UNIVERSE]


class TestUniverseCacheKey:
    def test_nasdaq100_cache_key_matches_legacy_constant(self):
        """既存のnasdaq100_tickers_cacheというキー名を変えていないことを保証する
        (変わるとapp_settingsの既存キャッシュが読めなくなる)"""
        from config import NASDAQ100_CACHE_KEY
        assert universe_cache_key("NASDAQ100") == NASDAQ100_CACHE_KEY == "nasdaq100_tickers_cache"

    def test_sp500_cache_key_is_distinct(self):
        assert universe_cache_key("SP500") == "sp500_tickers_cache"


class TestUserUniverseKey:
    def test_embeds_owner_token(self):
        assert user_universe_key("tok123") == "tok123#universe"


class TestSelectionSnapshotKeyBackwardCompatibility:
    def test_default_universe_key_is_unchanged_from_before_universe_support(self):
        """universe引数を省略した場合、S&P500対応前と全く同じキーになること
        (既存の本番スナップショットとの互換性を守るため)"""
        assert selection_snapshot_key("SHORT") == "selection_snapshot#SHORT"
        assert selection_snapshot_key("MID") == "selection_snapshot#MID"

    def test_explicit_default_universe_matches_omitted_universe(self):
        assert selection_snapshot_key("SHORT", "NASDAQ100") == selection_snapshot_key("SHORT")

    def test_non_default_universe_gets_distinct_key(self):
        assert selection_snapshot_key("SHORT", "SP500") == "selection_snapshot#SHORT#SP500"
        assert selection_snapshot_key("SHORT", "SP500") != selection_snapshot_key("SHORT")


class TestSignalHistoryDateKeyBackwardCompatibility:
    def test_default_universe_key_is_unchanged_from_before_universe_support(self):
        assert signal_history_date_key("2026-08-07", "MID") == "2026-08-07#MID"

    def test_explicit_default_universe_matches_omitted_universe(self):
        assert (signal_history_date_key("2026-08-07", "MID", "NASDAQ100")
                == signal_history_date_key("2026-08-07", "MID"))

    def test_non_default_universe_gets_distinct_key(self):
        assert signal_history_date_key("2026-08-07", "MID", "SP500") == "2026-08-07#MID#SP500"


class TestNormalizeUniverses:
    """対象指数の複数選択(2026-11-25、チェックボックス化)。"""

    def test_comma_separated_string_becomes_list_in_definition_order(self):
        assert normalize_universes("SP500,NASDAQ100") == ["NASDAQ100", "SP500"]

    def test_single_value_saved_before_multi_select_still_reads(self):
        """プルダウン時代に保存された値("SP500")がそのまま読めること(後方互換)"""
        assert normalize_universes("SP500") == ["SP500"]

    def test_list_input_and_duplicates(self):
        assert normalize_universes(["sp500", "SP500"]) == ["SP500"]

    def test_unknown_values_are_dropped(self):
        assert normalize_universes("NIKKEI225,SP500") == ["SP500"]

    def test_empty_or_all_unknown_falls_back_to_default(self):
        assert normalize_universes(None) == [DEFAULT_UNIVERSE]
        assert normalize_universes("") == [DEFAULT_UNIVERSE]
        assert normalize_universes("BOGUS") == [DEFAULT_UNIVERSE]

    def test_round_trip_through_stored_value(self):
        assert universes_to_value(["SP500", "NASDAQ100"]) == "NASDAQ100,SP500"
        assert normalize_universes(universes_to_value(["SP500"])) == ["SP500"]

    def test_label_joins_selected_universes(self):
        assert universes_label(["NASDAQ100", "SP500"]) == "NASDAQ100・S&P500"
        assert universes_label(["NASDAQ100", "SP500"], lang_en=True) == "NASDAQ100 + S&P 500"
        assert universes_label(["SP500"]) == "S&P500"
