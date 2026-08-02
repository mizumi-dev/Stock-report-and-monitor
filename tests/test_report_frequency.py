"""
選定レポートの送信可否判定(should_send_selection)のユニットテスト。
2026-08-02にアドホックで手動検証した内容(docs/test-cases.md E1-E5)をコード化したもの。
"""
import pytest

from handler import should_send_selection

MONDAY = "0"
TUESDAY = "1"


@pytest.mark.parametrize(
    "mode, weekday, day_of_month, expected",
    [
        ("SHORT", TUESDAY, 15, True),   # 短期は曜日・日付を問わず毎日送る
        ("SHORT", MONDAY, 1, True),
        ("MID", MONDAY, 15, True),      # 中期は指定曜日(既定:月曜)のみ
        ("MID", TUESDAY, 15, False),
        ("LONG", MONDAY, 3, True),      # 長期は月初(1〜7日)の指定曜日のみ、月1回
        ("LONG", MONDAY, 15, False),    # 月曜だが月初でない
        ("LONG", TUESDAY, 3, False),    # 月初だが指定曜日でない
        ("LONG", MONDAY, 7, True),      # 境界値: 7日はまだ月初扱い
        ("LONG", MONDAY, 8, False),     # 境界値: 8日はもう月初ではない
    ],
)
def test_should_send_selection(mode, weekday, day_of_month, expected):
    assert should_send_selection(mode, weekday, day_of_month, MONDAY) == expected


def test_weekly_report_day_is_configurable():
    """WEEKLY_REPORT_WEEKDAY環境変数で指定曜日を変えられること"""
    assert should_send_selection("MID", "2", 15, weekly_report_day="2") is True
    assert should_send_selection("MID", "0", 15, weekly_report_day="2") is False
