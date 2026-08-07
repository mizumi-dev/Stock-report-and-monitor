"""
data_sources.pyのユニットテスト。実際のネットワーク呼び出しはモックし、
パースロジック(get_sp500_tickers)とfallback動作を検証する。

get_sp500_tickers()自体の実データ疎通確認(Wikipediaページの実際のHTML構造との
整合性)は、このサンドボックス開発環境からは外部通信がブロックされているため
実施できない。daily-batch-devの診断フック(_test_fetch_sp500)で実データ検証済み
(TODO.md参照)。
"""
import itertools
import string
from unittest import mock

import pytest

from data_sources import get_sp500_tickers


def _fake_tickers(n):
    """実際のティッカーは英字のみ(まれにピリオド区切りのクラス株)なので、
    パース対象の正規表現(英字のみ許可)に合わせたテスト用ティッカーを生成する。"""
    combos = ("".join(c) for c in itertools.product(string.ascii_uppercase, repeat=3))
    return list(itertools.islice(combos, n))


def _fake_sp500_html(tickers):
    rows = "".join(
        f'<tr><td><a rel="nofollow" href="#">{t}</a></td><td><a href="#">Company {t}</a></td></tr>'
        for t in tickers
    )
    return f"""
    <html><body>
    <h2 id="constituents">S&amp;P 500 component stocks</h2>
    <table class="wikitable">{rows}</table>
    <h2 id="changes">Selected changes to the list of S&amp;P 500 components</h2>
    <table><tr><td>OLDTICK</td></tr></table>
    </body></html>
    """


def _mock_urlopen(html: str):
    fake_response = mock.MagicMock()
    fake_response.read.return_value = html.encode("utf-8")
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False
    return fake_response


class TestGetSp500Tickers:
    def test_parses_tickers_from_constituents_table(self):
        tickers = _fake_tickers(450)
        html = _fake_sp500_html(tickers)
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers()
        assert result == tickers

    def test_excludes_rows_from_changes_table(self):
        """id="constituents"からid="change"より前までだけをパース対象にする
        (過去の構成変更履歴テーブルの銘柄を誤って含めない)"""
        tickers = _fake_tickers(450)
        html = _fake_sp500_html(tickers)
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers()
        assert "OLDTICK" not in result

    def test_converts_share_class_periods_to_hyphens(self):
        """WikipediaはBRK.Bのような表記だが、yfinanceはBRK-Bを期待するため変換する"""
        tickers = _fake_tickers(449) + ["BRK.B"]
        html = _fake_sp500_html(tickers)
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers()
        assert "BRK-B" in result
        assert "BRK.B" not in result

    def test_falls_back_when_constituents_table_not_found(self):
        html = "<html><body>no matching content here</body></html>"
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers(fallback=["AAPL", "MSFT"])
        assert result == ["AAPL", "MSFT"]

    def test_falls_back_when_too_few_tickers_parsed(self):
        """パース件数が明らかに少なすぎる場合は、ページ構造変化によるパース失敗と
        みなしてfallbackする(黙って不完全なユニバースで運用してしまうのを防ぐ)"""
        tickers = _fake_tickers(10)
        html = _fake_sp500_html(tickers)
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers(fallback=["AAPL", "MSFT"])
        assert result == ["AAPL", "MSFT"]

    def test_raises_when_fetch_fails_and_no_fallback_given(self):
        with mock.patch("data_sources.urllib.request.urlopen", side_effect=OSError("network down")):
            with pytest.raises(OSError):
                get_sp500_tickers()

    def test_no_duplicate_tickers_in_typical_response(self):
        tickers = _fake_tickers(450)
        html = _fake_sp500_html(tickers)
        with mock.patch("data_sources.urllib.request.urlopen", return_value=_mock_urlopen(html)):
            result = get_sp500_tickers()
        assert len(result) == len(set(result))
