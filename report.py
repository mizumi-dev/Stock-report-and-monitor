"""
レポート生成（HTMLメール本文）
"""

DISCLAIMER = "※本レポートは投資助言ではありません"


def _signal_dot(flag: bool) -> str:
    return "●" if flag else "○"


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
            lamps = "".join(
                "🟢" if sig.get(k) else "⚪"
                for k in ("buy_trend", "buy_momentum", "volume_high")
            )
            cards.append(f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
              <p style="margin:4px 0;"><strong>【第{i}位】{s['ticker']} — {s.get('company_name','')}</strong></p>
              <p style="margin:4px 0;">PEG: {s['peg']:.2f} ｜ 利益成長率: +{s['earnings_growth']*100:.1f}%</p>
              <p style="margin:4px 0;">シグナル: {lamps}</p>
              <p style="margin:4px 0;"><a href="{register_form_url}&ticker={s['ticker']}">この銘柄を監視リストに登録 →</a></p>
            </div>
            """)
        body_rows = f"<p>🔎 サマリー<br>{summary}</p>" + "".join(cards)

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
                            skipped: list[str] | None = None) -> str:
    """監視（売買判断）レポート・日次。各要素は {ticker, price, prev_close, signals, prev_signals}"""
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

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>📊 本日の監視レポート</h2>
      {mode_note}
      <p>🔎 サマリー<br>{summary}</p>
      {''.join(blocks)}
      {skipped_note}
      <p style="color:#888;font-size:12px;">{DISCLAIMER}</p>
    </body></html>
    """
