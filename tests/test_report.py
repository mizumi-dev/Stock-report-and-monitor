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


def test_selection_report_includes_broker_links():
    ranked = [{"ticker": "NVDA", "company_name": "NVIDIA", "peg": 1.2, "earnings_growth": 0.25}]
    signals_map = {"NVDA": {"buy_trend": True, "buy_momentum": False, "volume_high": False}}

    body = build_selection_report(ranked, signals_map, "https://pegsignal.com/", "tok123")

    assert 'href="https://www.moomoo.com/ja/stock/NVDA-US"' in body
    assert 'href="https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric=NVDA.O"' in body


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
