"""
レポート生成（HTMLメール本文）
"""

DISCLAIMER = "※本レポートは投資助言ではありません"


def _signal_dot(flag: bool) -> str:
    return "●" if flag else "○"


SIGNAL_LEGEND = (
    '<p style="color:#888;font-size:12px;">'
    "シグナルの見方: 「トレンド」はゴールデンクロス/デッドクロス(移動平均線の交差)、"
    "「モメンタム」はRSI(買われすぎ・売られすぎ)、「出来高」は平常時との比較です。"
    "この選定は<strong>PEG(株価に対する利益成長率の割安度)を基準にランキング</strong>したもので、"
    "シグナルは現在の値動きの状態を補足する参考情報です。"
    "トレンドが既に下向き(デッドクロス)の銘柄は、割安に見えても値動きが弱い可能性があるため"
    "注意してください。</p>"
)


def _trend_text(detail: dict | None) -> str:
    """トレンドシグナルの状態を文言化する。
    buy_trendは「直近でゴールデンクロスした」か「既に上昇順に並んでいる(継続中)」かを
    区別せずTrueにしていたため、クロスからかなり時間が経っていても「クロス発生」のように
    見えてしまう問題があった。ここで両者を明示的に分けて伝える。"""
    detail = detail or {}
    if detail.get("sell_cross"):
        return "🔴 デッドクロス発生(下降転換)"
    if detail.get("sell_alignment"):
        return "🔴 下降トレンド継続中"
    if detail.get("buy_cross"):
        return "🟢 ゴールデンクロス発生(直近)"
    if detail.get("buy_alignment"):
        return "🟢 上昇トレンド継続中(直近のクロスではありません)"
    return "⚪ 明確なトレンドなし"


def _momentum_text(sig: dict) -> str:
    if sig.get("sell_momentum"):
        return f"🔴 買われすぎ(RSI {sig.get('rsi', '―')})"
    if sig.get("buy_momentum"):
        return f"🟢 売られすぎ・押し目の可能性(RSI {sig.get('rsi', '―')})"
    return f"⚪ 中立(RSI {sig.get('rsi', '―')})"


def _signal_summary_html(sig: dict) -> str:
    trend = _trend_text(sig.get("trend_detail"))
    momentum = _momentum_text(sig)
    volume = "🟢 急増" if sig.get("volume_high") else "⚪ 平常"
    return (
        '<table style="border-collapse:collapse;font-size:13px;">'
        f'<tr><td style="padding:2px 8px;">トレンド</td><td style="padding:2px 8px;">{trend}</td></tr>'
        f'<tr><td style="padding:2px 8px;">モメンタム</td><td style="padding:2px 8px;">{momentum}</td></tr>'
        f'<tr><td style="padding:2px 8px;">出来高</td><td style="padding:2px 8px;">{volume}</td></tr>'
        "</table>"
    )


def _broker_links(ticker: str) -> str:
    """レポートを見てすぐ行動に移せるよう、証券会社の銘柄ページへのリンクを添える。
    tickerは登録時にTICKER_RE(英大文字・数字1〜6文字)で検証済みのため、
    URLエンコード等は行わずそのまま埋め込んで問題ない。
    現時点では利用者は全員日本語版(pegsignal.com)経由の想定のため、日本の証券会社のみ。
    英語版(en.pegsignal.com)が実装され利用者ごとの言語属性が持てるようになったら、
    英語圏の利用者には米国の証券会社(Robinhood/Fidelity等、要調査)のリンクに出し分ける"""
    moomoo_url = f"https://www.moomoo.com/ja/stock/{ticker}-US"
    # RIC(Reuters Instrument Code)形式。NASDAQ銘柄は".O"サフィックスが一般的
    rakuten_url = f"https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric={ticker}.O"
    return (
        f'<p style="margin:4px 0;font-size:13px;">'
        f'<a href="{moomoo_url}">moomoo証券で見る →</a>　'
        f'<a href="{rakuten_url}">楽天証券で見る →</a></p>'
    )


def build_selection_report(ranked: list[dict], signals_map: dict, register_base_url: str,
                           register_token: str, mode_label: str = "") -> str:
    """選定（買い時）レポート・週次"""
    register_form_url = f"{register_base_url}?token={register_token}"
    mode_note = f'<p style="color:#888;font-size:12px;">判定期間: {mode_label}モード</p>' if mode_label else ""
    if not ranked:
        body_rows = "<p>本日、足切り条件を通過した銘柄はありませんでした。</p>"
    else:
        top = ranked[0]
        strongest = max(
            ranked,
            key=lambda s: sum(1 for v in signals_map.get(s["ticker"], {}).values() if v is True),
        )
        summary = (
            f"PEG最上位は {top['ticker']}（PEG {top['peg']:.2f}）。"
            f"シグナルが最も強いのは {strongest['ticker']} です。"
        )

        cards = []
        for i, s in enumerate(ranked, 1):
            sig = signals_map.get(s["ticker"], {})
            cards.append(f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
              <p style="margin:4px 0;"><strong>【第{i}位】{s['ticker']} — {s.get('company_name','')}</strong></p>
              <p style="margin:4px 0;">PEG: {s['peg']:.2f} ｜ 利益成長率: +{s['earnings_growth']*100:.1f}%</p>
              {_signal_summary_html(sig)}
              <p style="margin:4px 0;"><a href="{register_form_url}&amp;ticker={s['ticker']}">この銘柄を監視リストに登録 →</a></p>
              {_broker_links(s['ticker'])}
            </div>
            """)
        body_rows = f"<p>🔎 サマリー<br>{summary}</p>" + SIGNAL_LEGEND + "".join(cards)

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>📈 今週の買い時レポート</h2>
      {mode_note}
      {body_rows}
      <p><a href="{register_form_url}">検索ボックスから他の銘柄を登録する →</a></p>
      <p style="color:#888;font-size:12px;">{DISCLAIMER}</p>
    </body></html>
    """


def build_monitoring_report(watchlist_results: list[dict], mode_label: str = "",
                            skipped: list[str] | None = None,
                            register_base_url: str | None = None,
                            register_token: str | None = None) -> str:
    """監視（売買判断）レポート・日次。各要素は {ticker, price, prev_close, signals, prev_signals}。
    register_base_url/register_tokenを渡すと、登録ページへのリンクを末尾に付ける
    (銘柄追加・削除は登録ページ側で行うため、ここから迷わず辿れるようにする)"""
    increased = []
    warnings = []
    for r in watchlist_results:
        sig = r["signals"]
        prev = r.get("prev_signals") or {}
        buy_count = sum(1 for k in ("buy_trend", "buy_momentum") if sig.get(k))
        prev_buy_count = sum(1 for k in ("buy_trend", "buy_momentum") if prev.get(k))
        if buy_count > prev_buy_count:
            increased.append(r["ticker"])
        if sig.get("sell_trend") or sig.get("sell_momentum"):
            warnings.append(r["ticker"])

    summary = f"登録{len(watchlist_results)}銘柄中、買いシグナル増加は{len(increased)}件、警告は{len(warnings)}件です。"

    blocks = []
    for r in watchlist_results:
        sig = r["signals"]
        change_pct = (r["price"] - r["prev_close"]) / r["prev_close"] * 100 if r.get("prev_close") else 0
        warn_items = []
        if sig.get("sell_trend"):
            warn_items.append("デッドクロス")
        if sig.get("sell_momentum"):
            warn_items.append(f"RSI買われすぎ({sig.get('rsi')})")
        warn_text = "・".join(warn_items) if warn_items else "なし"

        blocks.append(f"""
        <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
          <p style="margin:4px 0;"><strong>【{r['ticker']}】${r['price']:.2f}（前日比 {change_pct:+.1f}%）</strong></p>
          <table style="border-collapse:collapse;font-size:13px;">
            <tr><td></td><td style="padding:2px 8px;">トレンド</td><td style="padding:2px 8px;">モメンタム</td></tr>
            <tr><td style="padding:2px 8px;">🟢買い</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_trend'))}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_momentum'))}</td></tr>
            <tr><td style="padding:2px 8px;">🔴売り</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_trend'))}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_momentum'))}</td></tr>
          </table>
          <p style="margin:4px 0;">出来高: {'急増' if sig.get('volume_high') else '平常'}（20日平均比{sig.get('volume_ratio')}倍）</p>
          <p style="margin:4px 0;">警告: {warn_text}</p>
          <p style="margin:4px 0;">PEG(参考): {r.get('peg','―')}</p>
          {_broker_links(r['ticker'])}
        </div>
        """)

    mode_note = f'<p style="color:#888;font-size:12px;">判定期間: {mode_label}モード</p>' if mode_label else ""
    skipped_note = ""
    if skipped:
        skipped_note = (
            f'<p style="color:#888;font-size:12px;">'
            f'次の銘柄は判定に必要な期間の株価データが不足しているため除外しました'
            f'（上場から日が浅い可能性があります）: {", ".join(skipped)}</p>'
        )

    register_link = ""
    if register_base_url and register_token:
        register_link = (
            f'<p><a href="{register_base_url}?token={register_token}">'
            f"銘柄を追加・削除する（登録ページを開く） →</a></p>"
        )

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>📊 本日の監視レポート</h2>
      {mode_note}
      <p>🔎 サマリー<br>{summary}</p>
      {''.join(blocks)}
      {skipped_note}
      {register_link}
      <p style="color:#888;font-size:12px;">{DISCLAIMER}</p>
    </body></html>
    """
