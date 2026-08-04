"""
レポート生成（HTMLメール本文）

lang="ja"(既定)なら従来通り日本語、lang="en"なら英語版を生成する。
既存の呼び出し(lang省略)は今まで通り日本語のまま動く。
"""
from config import PERIOD_PRESETS

LANG_JA = "ja"
LANG_EN = "en"

DISCLAIMER_JA = "※本レポートは投資助言ではありません"
DISCLAIMER_EN = "This report is not investment advice."

# config.PERIOD_PRESETS[key]["label"]は日本語("短期"等)のみのため、英語版はここで
# ローカルに対訳を持つ(register-stock側の_PERIOD_PRESET_LABELS_ENと同じ発想。
# config.pyはdaily-batch/register-stock双方が参照する共有データなので手を入れない)
_MODE_LABEL_EN = {"SHORT": "Short-term", "MID": "Mid-term", "LONG": "Long-term"}


def _mode_label(mode: str, lang: str) -> str:
    if not mode:
        return ""
    if lang == LANG_EN:
        return _MODE_LABEL_EN.get(mode, mode)
    return PERIOD_PRESETS.get(mode, {}).get("label", mode)


def _signal_dot(flag: bool) -> str:
    return "●" if flag else "○"


SIGNAL_LEGEND_JA = (
    '<p style="color:#888;font-size:12px;">'
    "シグナルの見方: 「トレンド」はゴールデンクロス/デッドクロス(移動平均線の交差)、"
    "「モメンタム」はRSI(買われすぎ・売られすぎ)、「出来高」は平常時との比較です。"
    "この選定は<strong>PEG(株価に対する利益成長率の割安度)を基準にランキング</strong>したもので、"
    "シグナルは現在の値動きの状態を補足する参考情報です。"
    "トレンドが既に下向き(デッドクロス)の銘柄は、割安に見えても値動きが弱い可能性があるため"
    "注意してください。</p>"
)
SIGNAL_LEGEND_EN = (
    '<p style="color:#888;font-size:12px;">'
    "How to read the signals: \"Trend\" is a golden/dead cross (moving average crossover), "
    "\"Momentum\" is RSI (overbought/oversold), and \"Volume\" compares against the normal "
    "baseline. This selection is <strong>ranked by PEG ratio</strong> "
    "(how undervalued the price is relative to earnings growth) — the signals are just "
    "supplementary information about current price action. "
    "A stock with an already-declining trend (dead cross) may be weak even if it looks "
    "cheap, so please keep that in mind.</p>"
)


def _trend_text(detail: dict | None, lang: str) -> str:
    """トレンドシグナルの状態を文言化する。
    buy_trendは「直近でゴールデンクロスした」か「既に上昇順に並んでいる(継続中)」かを
    区別せずTrueにしていたため、クロスからかなり時間が経っていても「クロス発生」のように
    見えてしまう問題があった。ここで両者を明示的に分けて伝える。"""
    detail = detail or {}
    if lang == LANG_EN:
        if detail.get("sell_cross"):
            return "🔴 Dead cross (turning down)"
        if detail.get("sell_alignment"):
            return "🔴 Downtrend continuing"
        if detail.get("buy_cross"):
            return "🟢 Golden cross (recent)"
        if detail.get("buy_alignment"):
            return "🟢 Uptrend continuing (not a recent cross)"
        return "⚪ No clear trend"
    if detail.get("sell_cross"):
        return "🔴 デッドクロス発生(下降転換)"
    if detail.get("sell_alignment"):
        return "🔴 下降トレンド継続中"
    if detail.get("buy_cross"):
        return "🟢 ゴールデンクロス発生(直近)"
    if detail.get("buy_alignment"):
        return "🟢 上昇トレンド継続中(直近のクロスではありません)"
    return "⚪ 明確なトレンドなし"


def _momentum_text(sig: dict, lang: str) -> str:
    rsi = sig.get("rsi", "―")
    if lang == LANG_EN:
        if sig.get("sell_momentum"):
            return f"🔴 Overbought (RSI {rsi})"
        if sig.get("buy_momentum"):
            return f"🟢 Oversold, possible dip (RSI {rsi})"
        return f"⚪ Neutral (RSI {rsi})"
    if sig.get("sell_momentum"):
        return f"🔴 買われすぎ(RSI {rsi})"
    if sig.get("buy_momentum"):
        return f"🟢 売られすぎ・押し目の可能性(RSI {rsi})"
    return f"⚪ 中立(RSI {rsi})"


def _signal_summary_html(sig: dict, lang: str = LANG_JA) -> str:
    trend = _trend_text(sig.get("trend_detail"), lang)
    momentum = _momentum_text(sig, lang)
    if lang == LANG_EN:
        volume = "🟢 Spiking" if sig.get("volume_high") else "⚪ Normal"
        labels = ("Trend", "Momentum", "Volume")
    else:
        volume = "🟢 急増" if sig.get("volume_high") else "⚪ 平常"
        labels = ("トレンド", "モメンタム", "出来高")
    return (
        '<table style="border-collapse:collapse;font-size:13px;">'
        f'<tr><td style="padding:2px 8px;">{labels[0]}</td><td style="padding:2px 8px;">{trend}</td></tr>'
        f'<tr><td style="padding:2px 8px;">{labels[1]}</td><td style="padding:2px 8px;">{momentum}</td></tr>'
        f'<tr><td style="padding:2px 8px;">{labels[2]}</td><td style="padding:2px 8px;">{volume}</td></tr>'
        "</table>"
    )


def _broker_links(ticker: str, lang: str = LANG_JA) -> str:
    """レポートを見てすぐ行動に移せるよう、証券会社の銘柄ページへのリンクを添える。
    tickerは登録時にTICKER_RE(英大文字・数字1〜6文字)で検証済みのため、
    URLエンコード等は行わずそのまま埋め込んで問題ない。
    日本語版: moomoo証券・楽天証券(いずれも実在URLをユーザー本人が確認済み)。
    英語版: 米国の口座数ランキング上位のうち、ログイン不要で個別銘柄ページに
    直接リンクできることを確認できたFidelity(1位)・Robinhood(4位、リンク可能な
    範囲での最上位)を採用。Schwab(2位)・Vanguard(3位)は個別銘柄ページの
    公開URLパターンが確認できなかったため見送った(2026-08-03時点)。"""
    if lang == LANG_EN:
        fidelity_url = (
            "https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary"
            f"?symbol={ticker}"
        )
        robinhood_url = f"https://robinhood.com/stocks/{ticker}"
        return (
            f'<p style="margin:4px 0;font-size:13px;">'
            f'<a href="{fidelity_url}">View on Fidelity →</a>　'
            f'<a href="{robinhood_url}">View on Robinhood →</a></p>'
        )
    moomoo_url = f"https://www.moomoo.com/ja/stock/{ticker}-US"
    # RIC(Reuters Instrument Code)形式。NASDAQ銘柄は".O"サフィックスが一般的
    rakuten_url = f"https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric={ticker}.O"
    return (
        f'<p style="margin:4px 0;font-size:13px;">'
        f'<a href="{moomoo_url}">moomoo証券で見る →</a>　'
        f'<a href="{rakuten_url}">楽天証券で見る →</a></p>'
    )


def _unsubscribe_link(register_base_url: str, register_token: str, lang: str = LANG_JA) -> str:
    """メールを見ている場でそのまま退会できるよう、登録ページの退会確認画面への
    リンクを直接張る(?delete_account=1は確認画面を表示するだけで、実際の削除は
    確認画面のボタンをもう一度押すまで実行されない)。"""
    label = "Unsubscribe / delete account →" if lang == LANG_EN else "配信停止・退会はこちら →"
    return (
        f'<p style="margin:16px 0 0;font-size:12px;">'
        f'<a href="{register_base_url}?token={register_token}&amp;delete_account=1" style="color:#888;">'
        f"{label}</a></p>"
    )


def build_selection_report(ranked: list[dict], signals_map: dict, register_base_url: str,
                           register_token: str, mode: str = "", lang: str = LANG_JA) -> str:
    """選定（買い時）レポート・週次"""
    register_form_url = f"{register_base_url}?token={register_token}"
    label = _mode_label(mode, lang)
    if lang == LANG_EN:
        mode_note = f'<p style="color:#888;font-size:12px;">Time horizon: {label}</p>' if label else ""
    else:
        mode_note = f'<p style="color:#888;font-size:12px;">判定期間: {label}モード</p>' if label else ""

    if not ranked:
        body_rows = ("<p>No stocks passed the screening criteria today.</p>" if lang == LANG_EN
                     else "<p>本日、足切り条件を通過した銘柄はありませんでした。</p>")
    else:
        top = ranked[0]
        strongest = max(
            ranked,
            key=lambda s: sum(1 for v in signals_map.get(s["ticker"], {}).values() if v is True),
        )
        if lang == LANG_EN:
            summary = (
                f"Top by PEG: {top['ticker']} (PEG {top['peg']:.2f}). "
                f"Strongest signals: {strongest['ticker']}."
            )
        else:
            summary = (
                f"PEG最上位は {top['ticker']}（PEG {top['peg']:.2f}）。"
                f"シグナルが最も強いのは {strongest['ticker']} です。"
            )

        cards = []
        for i, s in enumerate(ranked, 1):
            sig = signals_map.get(s["ticker"], {})
            if lang == LANG_EN:
                cards.append(f"""
                <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
                  <p style="margin:4px 0;"><strong>#{i} {s['ticker']} — {s.get('company_name','')}</strong></p>
                  <p style="margin:4px 0;">PEG: {s['peg']:.2f} | Earnings growth: +{s['earnings_growth']*100:.1f}%</p>
                  {_signal_summary_html(sig, lang)}
                  <p style="margin:4px 0;"><a href="{register_form_url}&amp;ticker={s['ticker']}">Add to watchlist →</a></p>
                  {_broker_links(s['ticker'], lang)}
                </div>
                """)
            else:
                cards.append(f"""
                <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
                  <p style="margin:4px 0;"><strong>【第{i}位】{s['ticker']} — {s.get('company_name','')}</strong></p>
                  <p style="margin:4px 0;">PEG: {s['peg']:.2f} ｜ 利益成長率: +{s['earnings_growth']*100:.1f}%</p>
                  {_signal_summary_html(sig, lang)}
                  <p style="margin:4px 0;"><a href="{register_form_url}&amp;ticker={s['ticker']}">この銘柄を監視リストに登録 →</a></p>
                  {_broker_links(s['ticker'], lang)}
                </div>
                """)
        legend = SIGNAL_LEGEND_EN if lang == LANG_EN else SIGNAL_LEGEND_JA
        summary_label = "🔎 Summary" if lang == LANG_EN else "🔎 サマリー"
        body_rows = f"<p>{summary_label}<br>{summary}</p>" + legend + "".join(cards)

    if lang == LANG_EN:
        heading = "📈 This week's buy-time picks"
        other_ticker_link = f'<p><a href="{register_form_url}">Add another ticker via search →</a></p>'
        disclaimer = DISCLAIMER_EN
    else:
        heading = "📈 今週の買い時レポート"
        other_ticker_link = f'<p><a href="{register_form_url}">検索ボックスから他の銘柄を登録する →</a></p>'
        disclaimer = DISCLAIMER_JA

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>{heading}</h2>
      {mode_note}
      {body_rows}
      {other_ticker_link}
      <p style="color:#888;font-size:12px;">{disclaimer}</p>
      {_unsubscribe_link(register_base_url, register_token, lang)}
    </body></html>
    """


def build_monitoring_report(watchlist_results: list[dict], mode: str = "",
                            skipped: list[str] | None = None,
                            register_base_url: str | None = None,
                            register_token: str | None = None,
                            lang: str = LANG_JA) -> str:
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

    if lang == LANG_EN:
        summary = (f"Of {len(watchlist_results)} tracked stocks, {len(increased)} had increased "
                   f"buy signals and {len(warnings)} have warnings.")
    else:
        summary = f"登録{len(watchlist_results)}銘柄中、買いシグナル増加は{len(increased)}件、警告は{len(warnings)}件です。"

    blocks = []
    for r in watchlist_results:
        sig = r["signals"]
        change_pct = (r["price"] - r["prev_close"]) / r["prev_close"] * 100 if r.get("prev_close") else 0
        if lang == LANG_EN:
            warn_items = []
            if sig.get("sell_trend"):
                warn_items.append("Dead cross")
            if sig.get("sell_momentum"):
                warn_items.append(f"RSI overbought ({sig.get('rsi')})")
            warn_text = ", ".join(warn_items) if warn_items else "None"
            blocks.append(f"""
            <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
              <p style="margin:4px 0;"><strong>{r['ticker']}: ${r['price']:.2f} ({change_pct:+.1f}% vs prev close)</strong></p>
              <table style="border-collapse:collapse;font-size:13px;">
                <tr><td></td><td style="padding:2px 8px;">Trend</td><td style="padding:2px 8px;">Momentum</td></tr>
                <tr><td style="padding:2px 8px;">🟢 Buy</td>
                    <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_trend'))}</td>
                    <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_momentum'))}</td></tr>
                <tr><td style="padding:2px 8px;">🔴 Sell</td>
                    <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_trend'))}</td>
                    <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_momentum'))}</td></tr>
              </table>
              <p style="margin:4px 0;">Volume: {'Spiking' if sig.get('volume_high') else 'Normal'} ({sig.get('volume_ratio')}x 20-day average)</p>
              <p style="margin:4px 0;">Warnings: {warn_text}</p>
              <p style="margin:4px 0;">PEG (reference): {r.get('peg','―')}</p>
              {_broker_links(r['ticker'], lang)}
            </div>
            """)
        else:
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
              {_broker_links(r['ticker'], lang)}
            </div>
            """)

    label = _mode_label(mode, lang)
    if lang == LANG_EN:
        mode_note = f'<p style="color:#888;font-size:12px;">Time horizon: {label}</p>' if label else ""
    else:
        mode_note = f'<p style="color:#888;font-size:12px;">判定期間: {label}モード</p>' if label else ""

    skipped_note = ""
    if skipped:
        if lang == LANG_EN:
            skipped_note = (
                f'<p style="color:#888;font-size:12px;">'
                f'The following tickers were excluded because there isn\'t enough price history yet '
                f'(possibly a recent listing): {", ".join(skipped)}</p>'
            )
        else:
            skipped_note = (
                f'<p style="color:#888;font-size:12px;">'
                f'次の銘柄は判定に必要な期間の株価データが不足しているため除外しました'
                f'（上場から日が浅い可能性があります）: {", ".join(skipped)}</p>'
            )

    register_link = ""
    unsubscribe_link = ""
    if register_base_url and register_token:
        if lang == LANG_EN:
            register_link = (
                f'<p><a href="{register_base_url}?token={register_token}">'
                f"Add or remove stocks (open the registration page) →</a></p>"
            )
        else:
            register_link = (
                f'<p><a href="{register_base_url}?token={register_token}">'
                f"銘柄を追加・削除する（登録ページを開く） →</a></p>"
            )
        unsubscribe_link = _unsubscribe_link(register_base_url, register_token, lang)

    if lang == LANG_EN:
        heading = "📊 Today's monitoring report"
        summary_label = "🔎 Summary"
        disclaimer = DISCLAIMER_EN
    else:
        heading = "📊 本日の監視レポート"
        summary_label = "🔎 サマリー"
        disclaimer = DISCLAIMER_JA

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>{heading}</h2>
      {mode_note}
      <p>{summary_label}<br>{summary}</p>
      {''.join(blocks)}
      {skipped_note}
      {register_link}
      <p style="color:#888;font-size:12px;">{disclaimer}</p>
      {unsubscribe_link}
    </body></html>
    """
