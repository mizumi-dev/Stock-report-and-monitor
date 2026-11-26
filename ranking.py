"""
銘柄選定ランキングロジック（確定案C）
足切り: 来期の予想EPS成長率20%未満/マイナス/None除外、PEGがNone/0以下/3超を除外
(PEG = 予想PER ÷ 来期の予想EPS成長率(%)、2026-11-26からYahooのpegRatioではなく自前計算)
さらに一時的な落ち込みからの反動を外すため、今期の予想EPS成長率が-10%未満・
今期予想なし・過去12か月の実績EPSが0以下の銘柄も除外(2026-11-26)
ランキング: PEG昇順、同値は時価総額の大きい方を優先

閾値は config.py に集約している(説明ページの生成にも使うため)。
"""
from config import DEFAULT_TOP_N, GROWTH_BASIS_FORWARD, MAX_PEG, MIN_CURRENT_YEAR_GROWTH, MIN_GROWTH

# 後方互換のため名前を残す(既存の参照が壊れないように)
TOP_N = DEFAULT_TOP_N


def passes_pre_gate(stock: dict) -> bool:
    """今期の予想EPS成長率を見る前の足切り(来期成長率・PEG・実績黒字)。今期の成長率は
    Yahooへの追加の問い合わせが必要なため、ここを通った候補にだけ問い合わせる"""
    eg = stock.get("earnings_growth")
    peg = stock.get("peg")
    eps = stock.get("trailing_eps")
    return (eg is not None and eg >= MIN_GROWTH and peg is not None and 0 < peg <= MAX_PEG
            and eps is not None and eps > 0)


def passes_gate(stock: dict) -> bool:
    eg = stock.get("earnings_growth")
    peg = stock.get("peg")
    if eg is None or eg < MIN_GROWTH:
        return False
    if peg is None or peg <= 0 or peg > MAX_PEG:
        return False
    # 自前計算のPEG(来期予想ベース)のときだけ、一時的な落ち込みからの反動を除外する
    if stock.get("growth_basis") == GROWTH_BASIS_FORWARD:
        cy = stock.get("current_year_growth")
        if cy is None or cy < MIN_CURRENT_YEAR_GROWTH:
            return False
        eps = stock.get("trailing_eps")
        if eps is None or eps <= 0:
            return False
    return True


def rank_stocks(stocks: list[dict], top_n: int = DEFAULT_TOP_N) -> list[dict]:
    """足切り通過銘柄をPEG昇順・同値は時価総額降順でランキングし上位N件を返す"""
    passed = [s for s in stocks if passes_gate(s)]
    ranked = sorted(
        passed,
        key=lambda s: (s["peg"], -(s.get("market_cap") or 0)),
    )
    return ranked[:top_n]
