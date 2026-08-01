"""
銘柄選定ランキングロジック（確定案C）
足切り: 成長率20%未満/マイナス/None除外、PEGがNone/0以下/3超を除外
ランキング: PEG昇順、同値は時価総額の大きい方を優先
"""

MIN_GROWTH = 0.20
MAX_PEG = 3.0
TOP_N = 3


def passes_gate(stock: dict) -> bool:
    eg = stock.get("earnings_growth")
    peg = stock.get("peg")
    if eg is None or eg < MIN_GROWTH:
        return False
    if peg is None or peg <= 0 or peg > MAX_PEG:
        return False
    return True


def rank_stocks(stocks: list[dict], top_n: int = TOP_N) -> list[dict]:
    """足切り通過銘柄をPEG昇順・同値は時価総額降順でランキングし上位N件を返す"""
    passed = [s for s in stocks if passes_gate(s)]
    ranked = sorted(
        passed,
        key=lambda s: (s["peg"], -(s.get("market_cap") or 0)),
    )
    return ranked[:top_n]
