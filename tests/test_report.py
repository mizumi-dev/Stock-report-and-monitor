"""
report.py(HTMLメール本文生成)のテスト。

メール内リンクのhref属性で"&"を生のまま埋め込むと、Gmailモバイルアプリ等の
HTMLサニタイザがクエリ文字列を正しく解釈できずリンクが機能しなくなることが
実際に報告された(&amp;へのエスケープ漏れ)。同種の回帰を防ぐための最小テスト。
"""
from report import build_monitoring_report, build_selection_report


def test_selection_report_ticker_link_escapes_ampersand():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False}}

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert 'href="https://pegsignal.com/?token=tok123&amp;ticker=NVDA"' in body
    # hrefの中に生の"&"(&amp;の一部を除く)が残っていないことを確認
    href_start = body.index('href="https://pegsignal.com/?token=tok123')
    href_value = body[href_start:body.index('"', href_start + 6)]
    assert "&" not in href_value.replace("&amp;", "")


def test_selection_report_footer_link_has_no_query_ampersand():
    body = build_selection_report([], {}, "https://pegsignal.com/", "tok123")
    assert 'href="https://pegsignal.com/?token=tok123"' in body


def _monitoring_body(mode):
    results = [{"ticker": "NVDA", "price": 100.0, "prev_close": 98.0,
                "signals": {"buy_trend": True, "volume_ratio": 1.8, "volume_high": True},
                "peg": 1.2}]
    return build_monitoring_report(results, mode, None, "https://pegsignal.com/", "tok123")


def test_monitoring_report_volume_baseline_matches_mode():
    """出来高の基準日数はモードごとに違う(短期・中期=20日、長期=50日)。
    以前は「20日平均比」と決め打ちで、長期モードだけ実際の計算とズレていた。"""
    assert "20日平均比" in _monitoring_body("SHORT")
    assert "20日平均比" in _monitoring_body("MID")
    assert "50日平均比" in _monitoring_body("LONG")


def test_monitoring_report_volume_baseline_is_english_and_mode_aware():
    assert "20-day average" in _monitoring_body_en("MID")
    assert "50-day average" in _monitoring_body_en("LONG")


def _monitoring_body_en(mode):
    results = [{"ticker": "NVDA", "price": 100.0, "prev_close": 98.0,
                "signals": {"buy_trend": True, "volume_ratio": 1.8, "volume_high": True},
                "peg": 1.2}]
    return build_monitoring_report(results, mode, None, "https://pegsignal.com/", "tok123",
                                   lang="en")


def test_monitoring_report_volume_baseline_falls_back_when_mode_missing():
    """モード未指定でも既定(中期)の日数を出し、KeyErrorで落ちないこと"""
    assert "20日平均比" in _monitoring_body("")


def test_selection_report_links_to_usage_guide():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert 'href="https://pegsignal.com/?guide=1"' in body


def test_monitoring_report_links_to_usage_guide():
    results = [{"ticker": "NVDA", "price": 100.0, "prev_close": 98.0,
                "signals": {"buy_trend": True, "volume_ratio": 1.1}, "peg": 1.2}]
    body = build_monitoring_report(results, "MID", None, "https://pegsignal.com/", "tok123")
    assert 'href="https://pegsignal.com/?guide=1"' in body


def test_report_guide_link_carries_language_for_english_users():
    """メール内リンクにはX-Site-Langが付かないため、?lang=enで言語を引き継ぐ"""
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123", lang="en")
    assert 'href="https://pegsignal.com/?guide=1&amp;lang=en"' in body


def test_selection_report_strongest_signals_counts_buy_only_not_sell():
    """sell_trend/sell_momentumは「シグナルが最も強い」の対象外(バグ修正)。
    デッドクロス+買われすぎ(2件の売りシグナル)を持つ銘柄が、買いシグナル1件の
    銘柄より「強い」と誤って紹介されてはいけない。"""
    ranked = [
        {"ticker": "AAA", "company_name": "A社", "peg": 1.0, "earnings_growth": 0.3},
        {"ticker": "BBB", "company_name": "B社", "peg": 2.0, "earnings_growth": 0.25},
    ]
    signals_map = {
        "AAA": {"buy_trend": False, "buy_momentum": False,
                "sell_trend": True, "sell_momentum": True, "volume_high": True},
        "BBB": {"buy_trend": True, "buy_momentum": False,
                "sell_trend": False, "sell_momentum": False, "volume_high": False},
    }
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert "シグナルが最も強いのは BBB です。" in body


def test_selection_report_strongest_signals_lists_all_tied_tickers():
    ranked = [
        {"ticker": "AAA", "company_name": "A社", "peg": 1.0, "earnings_growth": 0.3},
        {"ticker": "BBB", "company_name": "B社", "peg": 2.0, "earnings_growth": 0.25},
    ]
    signals_map = {
        "AAA": {"buy_trend": True, "buy_momentum": False, "volume_high": False},
        "BBB": {"buy_trend": True, "buy_momentum": False, "volume_high": False},
    }
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert "シグナルが最も強いのは AAA・BBB です。" in body


def test_selection_report_strongest_signals_ties_are_english_when_lang_is_en():
    ranked = [
        {"ticker": "AAA", "company_name": "A社", "peg": 1.0, "earnings_growth": 0.3},
        {"ticker": "BBB", "company_name": "B社", "peg": 2.0, "earnings_growth": 0.25},
    ]
    signals_map = {
        "AAA": {"buy_trend": True, "buy_momentum": False, "volume_high": False},
        "BBB": {"buy_trend": True, "buy_momentum": False, "volume_high": False},
    }
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123", lang="en")
    assert "Strongest signals: AAA / BBB." in body


def test_selection_report_strongest_signals_none_when_no_buy_signal():
    ranked = [{"ticker": "AAA", "company_name": "A社", "peg": 1.0, "earnings_growth": 0.3}]
    signals_map = {"AAA": {"buy_trend": False, "buy_momentum": False,
                           "sell_trend": True, "volume_high": True}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert "現在、買いシグナルが点灯している銘柄はありません。" in body


def test_selection_report_includes_broker_links():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert 'href="https://www.moomoo.com/ja/stock/NVDA-US"' in body
    assert 'href="https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric=NVDA.O"' in body


def test_selection_report_includes_signal_legend():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert "PEG" in body and "参考情報" in body


def test_selection_report_distinguishes_fresh_cross_from_continued_alignment():
    ranked = [
        {"ticker": "AAA", "company_name": "A社", "peg": 1.0, "earnings_growth": 0.3},
        {"ticker": "BBB", "company_name": "B社", "peg": 1.1, "earnings_growth": 0.3},
    ]
    signals_map = {
        "AAA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                "trend_detail": {"buy_cross": True, "buy_alignment": True}},
        "BBB": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                "trend_detail": {"buy_cross": False, "buy_alignment": True}},
    }

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert "ゴールデンクロス発生(直近)" in body
    assert "上昇トレンド継続中(直近のクロスではありません)" in body


def test_selection_report_shows_downtrend_warning_even_if_peg_passed_gate():
    ranked = [{"ticker": "NXPI", "company_name": "NXP", "peg": 1.5, "earnings_growth": 0.3}]
    signals_map = {"NXPI": {"buy_trend": False, "buy_momentum": True, "volume_high": False,
                            "trend_detail": {"sell_cross": True}}}

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert "デッドクロス発生" in body


def test_monitoring_report_includes_broker_links():
    watchlist_results = [{
        "ticker": "AAPL", "price": 210.5, "prev_close": 208.0,
        "signals": {"buy_trend": True, "buy_momentum": False, "sell_trend": False,
                    "sell_momentum": False, "volume_high": False, "volume_ratio": 1.1},
        "prev_signals": {},
    }]

    body = build_monitoring_report(watchlist_results)

    assert 'href="https://www.moomoo.com/ja/stock/AAPL-US"' in body
    assert 'href="https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric=AAPL.O"' in body


def test_monitoring_report_includes_register_page_link_when_provided():
    watchlist_results = [{
        "ticker": "AAPL", "price": 210.5, "prev_close": 208.0,
        "signals": {"buy_trend": True, "buy_momentum": False, "sell_trend": False,
                    "sell_momentum": False, "volume_high": False, "volume_ratio": 1.1},
        "prev_signals": {},
    }]

    body = build_monitoring_report(watchlist_results, register_base_url="https://pegsignal.com/",
                                    register_token="tok123")

    assert 'href="https://pegsignal.com/?token=tok123"' in body


def test_monitoring_report_omits_register_link_when_not_provided():
    body = build_monitoring_report([])
    assert "登録ページを開く" not in body


def test_selection_report_includes_unsubscribe_link():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert 'href="https://pegsignal.com/?token=tok123&amp;delete_account=1"' in body


def test_monitoring_report_includes_unsubscribe_link_when_token_provided():
    body = build_monitoring_report([], register_base_url="https://pegsignal.com/", register_token="tok123")
    assert 'href="https://pegsignal.com/?token=tok123&amp;delete_account=1"' in body


def test_monitoring_report_omits_unsubscribe_link_when_not_provided():
    body = build_monitoring_report([])
    assert "delete_account=1" not in body


def test_selection_report_defaults_to_japanese_when_lang_omitted():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert "今週のPEGスクリーニング結果" in body
    assert 'href="https://www.moomoo.com/ja/stock/NVDA-US"' in body


def test_selection_report_is_english_when_lang_is_en():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123", lang="en")

    assert "This week's PEG screening results" in body
    assert "今週のPEGスクリーニング結果" not in body
    # 米国口座数ランキング上位のうち、ログイン不要で個別銘柄ページへ直接リンクできる
    # Fidelity・Robinhoodを採用(Schwab/Vanguardは非対応、TODO.md参照)
    assert 'href="https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=NVDA"' in body
    assert 'href="https://robinhood.com/stocks/NVDA"' in body
    assert "moomoo" not in body
    assert "楽天" not in body


def test_selection_report_heading_varies_by_mode():
    body_short = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="SHORT")
    body_mid = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="MID")
    body_long = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="LONG")

    assert "本日のPEGスクリーニング結果" in body_short
    assert "今週のPEGスクリーニング結果" in body_mid
    assert "今月のPEGスクリーニング結果" in body_long


def test_selection_report_heading_varies_by_mode_when_english():
    body_short = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="SHORT", lang="en")
    body_long = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="LONG", lang="en")

    assert "Today's PEG screening results" in body_short
    assert "This month's PEG screening results" in body_long


def test_selection_subject_varies_by_mode():
    from report import selection_subject

    assert selection_subject("SHORT", "ja") == "📈 本日のPEGスクリーニング結果"
    assert selection_subject("MID", "ja") == "📈 今週のPEGスクリーニング結果"
    assert selection_subject("LONG", "ja") == "📈 今月のPEGスクリーニング結果"
    assert selection_subject("SHORT", "en") == "📈 Today's PEG screening results"
    assert selection_subject("", "ja") == "📈 今週のPEGスクリーニング結果"


def test_selection_report_mode_label_is_english_when_lang_is_en():
    body = build_selection_report([], {}, "https://pegsignal.com/", "tok123", mode="SHORT", lang="en")
    assert "Time horizon: Short-term" in body


def test_selection_report_unsubscribe_link_is_english_when_lang_is_en():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123", lang="en")
    assert "Unsubscribe / delete account" in body


def test_monitoring_report_is_english_when_lang_is_en():
    watchlist_results = [{
        "ticker": "AAPL", "price": 210.5, "prev_close": 208.0,
        "signals": {"buy_trend": True, "buy_momentum": False, "sell_trend": False,
                    "sell_momentum": False, "volume_high": False, "volume_ratio": 1.1},
        "prev_signals": {},
    }]
    body = build_monitoring_report(watchlist_results, mode="MID", register_base_url="https://pegsignal.com/",
                                    register_token="tok123", lang="en")

    assert "Today's monitoring report" in body
    assert "本日の監視レポート" not in body
    assert "Time horizon: Mid-term" in body
    assert 'href="https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=AAPL"' in body
    assert 'href="https://robinhood.com/stocks/AAPL"' in body
    assert "Add or remove stocks" in body


def test_selection_report_includes_ai_prompt_link():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")
    assert 'href="https://pegsignal.com/?ai_prompt=1&amp;ticker=NVDA"' in body
    assert "AIに分析してもらう" in body


def test_monitoring_report_includes_ai_prompt_link_when_register_url_provided():
    watchlist_results = [{
        "ticker": "AAPL", "price": 210.5, "prev_close": 208.0,
        "signals": {"buy_trend": True, "buy_momentum": False, "sell_trend": False,
                    "sell_momentum": False, "volume_high": False, "volume_ratio": 1.1},
        "prev_signals": {},
    }]
    body = build_monitoring_report(watchlist_results, register_base_url="https://pegsignal.com/",
                                    register_token="tok123")
    assert 'href="https://pegsignal.com/?ai_prompt=1&amp;ticker=AAPL"' in body


def test_monitoring_report_omits_ai_prompt_link_when_register_url_not_provided():
    watchlist_results = [{
        "ticker": "AAPL", "price": 210.5, "prev_close": 208.0,
        "signals": {"buy_trend": True, "buy_momentum": False, "sell_trend": False,
                    "sell_momentum": False, "volume_high": False, "volume_ratio": 1.1},
        "prev_signals": {},
    }]
    body = build_monitoring_report(watchlist_results)
    assert "ai_prompt=1" not in body


def test_selection_report_ai_prompt_link_carries_lang_when_english():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False,
                            "trend_detail": {"buy_cross": True}}}
    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123", lang="en")
    assert 'href="https://pegsignal.com/?ai_prompt=1&amp;ticker=NVDA&amp;lang=en"' in body
