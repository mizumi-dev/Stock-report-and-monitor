"""
レポート生成（HTMLメール本文）

lang="ja"(既定)なら従来通り日本語、lang="en"なら英語版を生成する。
既存の呼び出し(lang省略)は今まで通り日本語のまま動く。

単語の並び順が言語間で大きく異なる箇所(銘柄カード・監視ブロックの本文)は
テンプレートを共有せず素直にja/enを別々に書いているが、単純な文言の
出し分け(見出し・ラベル・凡例等)はすべて下のT辞書 + t()に集約している。
"""
from config import PERIOD_PRESETS

LANG_JA = "ja"
LANG_EN = "en"

# 単純な文言の出し分けはすべてここに集約する({キー: {ja:..., en:...}})。
# カード本文のように語順自体が言語で変わる箇所は対象外(それぞれ個別にja/en分岐する)。
T = {
    "disclaimer": {
        LANG_JA: (
            "※本レポートは、公開データに機械的な計算式(PEGレシオ・移動平均・RSI等)を"
            "適用した結果を機械的に表示するものであり、特定の銘柄の売買を推奨・勧誘する"
            "ものではありません。投資判断は、ご自身の責任において行ってください。"
        ),
        LANG_EN: (
            "This report mechanically applies fixed formulas (PEG ratio, moving averages, "
            "RSI, etc.) to publicly available data. It does not recommend or solicit the "
            "purchase or sale of any specific security. You are solely responsible for "
            "your own investment decisions."
        ),
    },
    "summary_label": {LANG_JA: "🔎 サマリー", LANG_EN: "🔎 Summary"},
    "monitoring_heading": {LANG_JA: "📊 本日の監視レポート", LANG_EN: "📊 Today's monitoring report"},
    "no_candidates": {LANG_JA: "本日、足切り条件を通過した銘柄はありませんでした。",
                       LANG_EN: "No stocks passed the screening criteria today."},
    "other_ticker_link": {LANG_JA: "検索ボックスから他の銘柄を登録する →",
                          LANG_EN: "Add another ticker via search →"},
    "add_to_watchlist": {LANG_JA: "この銘柄を監視リストに登録 →", LANG_EN: "Add to watchlist →"},
    "unsubscribe": {LANG_JA: "配信停止・退会はこちら →", LANG_EN: "Unsubscribe / delete account →"},
    "register_link": {LANG_JA: "銘柄を追加・削除する（登録ページを開く） →",
                      LANG_EN: "Add or remove stocks (open the registration page) →"},
    "ai_prompt_link": {LANG_JA: "🤖 AIに分析してもらう →", LANG_EN: "🤖 Ask an AI to analyze →"},
    "guide_link": {LANG_JA: "📖 はじめての方へ — 使い方ガイド（図でわかる説明） →",
                   LANG_EN: "📖 New here? Read the illustrated how-to guide →"},
    "col_trend": {LANG_JA: "トレンド", LANG_EN: "Trend"},
    "col_momentum": {LANG_JA: "モメンタム", LANG_EN: "Momentum"},
    "col_volume": {LANG_JA: "出来高", LANG_EN: "Volume"},
    "row_buy": {LANG_JA: "🟢買いシグナル", LANG_EN: "🟢 Buy signal"},
    "row_sell": {LANG_JA: "🔴売りシグナル", LANG_EN: "🔴 Sell signal"},
    "volume_high": {LANG_JA: "急増", LANG_EN: "Spiking"},
    "volume_normal": {LANG_JA: "平常", LANG_EN: "Normal"},
    "no_warnings": {LANG_JA: "なし", LANG_EN: "None"},
    "warn_dead_cross": {LANG_JA: "デッドクロス", LANG_EN: "Dead cross"},
    "peg_reference": {LANG_JA: "PEG(参考)", LANG_EN: "PEG (reference)"},
    "signal_legend_summary": {LANG_JA: "🔎 シグナルの見方・PEGの基準（タップで表示）",
                              LANG_EN: "🔎 How to read the signals & PEG criteria (tap to expand)"},
    "signal_legend": {
        LANG_JA: (
            "「トレンド」はゴールデンクロス/デッドクロス(移動平均線の交差)、"
            "「モメンタム」はRSI(買われすぎ・売られすぎ)、「出来高」は平常時との比較です。"
            "この選定は<strong>PEG(株価収益率÷利益成長率)を基準にランキング</strong>したもので、"
            "シグナルは現在の値動きの状態を補足する参考情報です。"
            "トレンドが既に下向き(デッドクロス)の銘柄は、PEGが低くても値動きが弱い可能性があるため"
            "注意してください。"
        ),
        LANG_EN: (
            "\"Trend\" is a golden/dead cross (moving average crossover), "
            "\"Momentum\" is RSI (overbought/oversold), and \"Volume\" compares against the normal "
            "baseline. This selection is <strong>ranked by PEG ratio</strong> "
            "(P/E ratio ÷ earnings growth rate) — the signals are just "
            "supplementary information about current price action. "
            "A stock with an already-declining trend (dead cross) may be weak even with a low "
            "PEG, so please keep that in mind."
        ),
    },
}


def t(key: str, lang: str) -> str:
    return T[key][lang]


def _as_of_note(as_of: str | None, lang: str) -> str:
    """レポート即時発行ボタンは保存済みスナップショットをそのまま送るため、
    「これはいつ時点のデータか」を明記する(定時実行のバッチ結果とボタン押下の
    タイミングがずれても、利用者が古いデータと気づけるようにするため)。"""
    if not as_of:
        return ""
    text = f"Data as of {as_of}" if lang == LANG_EN else f"{as_of}時点のデータです"
    return f'<p style="color:#888;font-size:12px;">{text}</p>'


def _mode_label(mode: str, lang: str) -> str:
    if not mode:
        return ""
    preset = PERIOD_PRESETS.get(mode, {})
    key = "label_en" if lang == LANG_EN else "label"
    return preset.get(key, mode)


def _mode_note(mode: str, lang: str) -> str:
    label = _mode_label(mode, lang)
    if not label:
        return ""
    text = f"Time horizon: {label}" if lang == LANG_EN else f"判定期間: {label}モード"
    return f'<p style="color:#888;font-size:12px;">{text}</p>'


# 選定レポートの見出し・件名は、判定期間モードに応じて「本日の/今週の/今月の」を
# 出し分ける(モード未指定時はMID相当の「今週の」をデフォルトとする)。
_SELECTION_PERIOD_PHRASE = {
    "SHORT": {LANG_JA: "本日の", LANG_EN: "Today's"},
    "MID": {LANG_JA: "今週の", LANG_EN: "This week's"},
    "LONG": {LANG_JA: "今月の", LANG_EN: "This month's"},
}


def selection_subject(mode: str, lang: str) -> str:
    phrase = _SELECTION_PERIOD_PHRASE.get(mode, _SELECTION_PERIOD_PHRASE["MID"])[lang]
    if lang == LANG_EN:
        return f"📈 {phrase} PEG screening results"
    return f"📈 {phrase}PEGスクリーニング結果"


def _signal_dot(flag: bool, on_emoji: str) -> str:
    """●/○(黒丸・白丸)は点灯有無が一目で分かりにくいとの指摘を受け、行の色
    (買い行=🟢、売り行=🔴)に合わせた色付き丸に変更。消灯時は共通で⚪。"""
    return on_emoji if flag else "⚪"


# トレンド/モメンタムの状態ごとの文言。buy_trendは「直近でゴールデンクロスした」か
# 「既に上昇順に並んでいる(継続中)」かを区別せずTrueにしていたため、クロスから
# かなり時間が経っていても「クロス発生」のように見えてしまう問題があった。
# ここで両者を明示的に分けて伝える。
_TREND_TEXT = {
    "sell_cross": {LANG_JA: "🔴 デッドクロス発生(下降転換)", LANG_EN: "🔴 Dead cross (turning down)"},
    "sell_alignment": {LANG_JA: "🔴 下降トレンド継続中", LANG_EN: "🔴 Downtrend continuing"},
    "buy_cross": {LANG_JA: "🟢 ゴールデンクロス発生(直近)", LANG_EN: "🟢 Golden cross (recent)"},
    "buy_alignment": {LANG_JA: "🟢 上昇トレンド継続中(直近のクロスではありません)",
                       LANG_EN: "🟢 Uptrend continuing (not a recent cross)"},
    "none": {LANG_JA: "⚪ 明確なトレンドなし", LANG_EN: "⚪ No clear trend"},
}


def _trend_text(detail: dict | None, lang: str) -> str:
    detail = detail or {}
    for key in ("sell_cross", "sell_alignment", "buy_cross", "buy_alignment"):
        if detail.get(key):
            return _TREND_TEXT[key][lang]
    return _TREND_TEXT["none"][lang]


def _momentum_text(sig: dict, lang: str) -> str:
    rsi = sig.get("rsi", "―")
    if sig.get("sell_momentum"):
        return f"🔴 買われすぎ(RSI {rsi})" if lang == LANG_JA else f"🔴 Overbought (RSI {rsi})"
    if sig.get("buy_momentum"):
        return (f"🟢 売られすぎ水準(RSI {rsi})" if lang == LANG_JA
                else f"🟢 Oversold level (RSI {rsi})")
    return f"⚪ 中立(RSI {rsi})" if lang == LANG_JA else f"⚪ Neutral (RSI {rsi})"


def _signal_summary_html(sig: dict, lang: str = LANG_JA) -> str:
    trend = _trend_text(sig.get("trend_detail"), lang)
    momentum = _momentum_text(sig, lang)
    volume_state = t("volume_high", lang) if sig.get("volume_high") else t("volume_normal", lang)
    volume = f"🟢 {volume_state}" if sig.get("volume_high") else f"⚪ {volume_state}"
    return (
        '<table style="border-collapse:collapse;font-size:13px;">'
        f'<tr><td style="padding:2px 8px;">{t("col_trend", lang)}</td><td style="padding:2px 8px;">{trend}</td></tr>'
        f'<tr><td style="padding:2px 8px;">{t("col_momentum", lang)}</td><td style="padding:2px 8px;">{momentum}</td></tr>'
        f'<tr><td style="padding:2px 8px;">{t("col_volume", lang)}</td><td style="padding:2px 8px;">{volume}</td></tr>'
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


def _ai_prompt_link(register_base_url: str, ticker: str, lang: str = LANG_JA) -> str:
    """気になった銘柄をAIに分析させたい利用者向けに、コピー用プロンプトを表示する
    ページへのリンクを添える。register_base_url(register-stockのURL)を流用し、
    トークンは不要(このページは利用者データを一切参照・変更しないため)。"""
    lang_param = f"&amp;lang={lang}" if lang == LANG_EN else ""
    return (
        f'<p style="margin:4px 0;font-size:13px;">'
        f'<a href="{register_base_url}?ai_prompt=1&amp;ticker={ticker}{lang_param}">'
        f'{t("ai_prompt_link", lang)}</a></p>'
    )


def _guide_link(register_base_url: str, lang: str = LANG_JA) -> str:
    """初めてレポートを受け取った人が用語やランプの意味で迷わないよう、
    すべてのメールから使い方ガイドへ辿れるようにする。トークンは不要
    (ガイドは利用者データを一切参照・変更しないため)。レポートを発行した言語は
    X-Site-Langヘッダーでは伝わらないので、?lang=で明示的に引き継ぐ。"""
    lang_param = f"&amp;lang={lang}" if lang == LANG_EN else ""
    return (
        f'<p style="margin:12px 0 0;font-size:13px;">'
        f'<a href="{register_base_url}?guide=1{lang_param}">{t("guide_link", lang)}</a></p>'
    )


def _unsubscribe_link(register_base_url: str, register_token: str, lang: str = LANG_JA) -> str:
    """メールを見ている場でそのまま退会できるよう、登録ページの退会確認画面への
    リンクを直接張る(?delete_account=1は確認画面を表示するだけで、実際の削除は
    確認画面のボタンをもう一度押すまで実行されない)。"""
    return (
        f'<p style="margin:16px 0 0;font-size:12px;">'
        f'<a href="{register_base_url}?token={register_token}&amp;delete_account=1" style="color:#888;">'
        f"{t('unsubscribe', lang)}</a></p>"
    )


def render_selection_cards(ranked: list[dict], signals_map: dict, register_base_url: str,
                           lang: str = LANG_JA, register_token: str | None = None) -> str:
    """選定レポートの本文(サマリー+銘柄カード一覧)のHTMLを組み立てる。
    メール本文(build_selection_report)と、ウェブの未登録訪問者向けプレビュー
    ページ(register-stockの?preview=1)の両方から使う共通部品。

    register_tokenがあれば「この銘柄を監視リストに登録」の個別リンクを付ける
    (メール本文・登録済み利用者向け)。Noneならその行を省略する(未登録の
    プレビュー訪問者はまだトークンを持たないため、個別銘柄への操作はできない)。"""
    register_form_url = f"{register_base_url}?token={register_token}" if register_token else ""

    if not ranked:
        return f"<p>{t('no_candidates', lang)}</p>"

    top = ranked[0]
    # 「シグナルが最も強い」は買いシグナル(buy_trend・buy_momentum)のみを数える。
    # volume_highは買い/売りの方向を持たない参考指標のため対象外(_render_info参照)。
    # 以前はsell系も同じ重みで数えており、デッドクロス等の警告銘柄を誤って
    # 「シグナルが最も強い」と紹介してしまうバグがあった。
    buy_counts = {
        s["ticker"]: sum(
            1 for k in ("buy_trend", "buy_momentum") if signals_map.get(s["ticker"], {}).get(k)
        )
        for s in ranked
    }
    max_count = max(buy_counts.values())
    strongest_tickers = [s["ticker"] for s in ranked if buy_counts[s["ticker"]] == max_count]

    if max_count == 0:
        strongest_note = ("No stock currently shows a buy signal." if lang == LANG_EN
                          else "現在、買いシグナルが点灯している銘柄はありません。")
    elif lang == LANG_EN:
        strongest_note = f"Strongest signals: {' / '.join(strongest_tickers)}."
    else:
        strongest_note = f"シグナルが最も強いのは {'・'.join(strongest_tickers)} です。"

    if lang == LANG_EN:
        summary = f"Top by PEG: {top['ticker']} (PEG {top['peg']:.2f}). {strongest_note}"
    else:
        summary = f"PEG最上位は {top['ticker']}（PEG {top['peg']:.2f}）。{strongest_note}"

    cards = []
    for i, s in enumerate(ranked, 1):
        sig = signals_map.get(s["ticker"], {})
        if lang == LANG_EN:
            title = f"#{i} {s['ticker']} — {s.get('company_name','')}"
            growth = f"PEG: {s['peg']:.2f} | Earnings growth: +{s['earnings_growth']*100:.1f}%"
        else:
            title = f"【第{i}位】{s['ticker']} — {s.get('company_name','')}"
            growth = f"PEG: {s['peg']:.2f} ｜ 利益成長率: +{s['earnings_growth']*100:.1f}%"
        watchlist_link = (
            f'<p style="margin:4px 0;"><a href="{register_form_url}&amp;ticker={s["ticker"]}">'
            f'{t("add_to_watchlist", lang)}</a></p>'
        ) if register_token else ""
        cards.append(f"""
        <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
          <p style="margin:4px 0;"><strong>{title}</strong></p>
          <p style="margin:4px 0;">{growth}</p>
          {_signal_summary_html(sig, lang)}
          {watchlist_link}
          {_broker_links(s['ticker'], lang)}
          {_ai_prompt_link(register_base_url, s['ticker'], lang)}
        </div>
        """)
    # 凡例(シグナルの見方・PEGの基準)は<details>で折りたたむ。この関数はメール本文と
    # ウェブのプレビューページ(短期・中期・長期の3回分)の両方から使われるため、
    # 常時表示だと特にプレビューページで文字量が多くなりすぎるとの指摘を受けて変更した。
    # <details>はHTML/CSSのみで動作しJS不要なため、対応しないメールクライアントでも
    # 「常に展開された状態」として安全にフォールバックする。
    legend = (
        f'<details style="color:#888;font-size:12px;margin:8px 0;">'
        f'<summary style="cursor:pointer;">{t("signal_legend_summary", lang)}</summary>'
        f'<p>{t("signal_legend", lang)}</p>'
        f'</details>'
    )
    return (f"<p>{t('summary_label', lang)}<br>{summary}</p>"
           + legend
           + "".join(cards))


def build_selection_report(ranked: list[dict], signals_map: dict, register_base_url: str,
                           register_token: str, mode: str = "", lang: str = LANG_JA,
                           as_of: str | None = None) -> str:
    """選定（PEGスクリーニング）レポート・週次。as_ofを渡すと「◯月◯日時点のデータです」の
    注記を末尾に付ける(レポート即時発行ボタンが保存済みスナップショットを送る際に使う)。"""
    register_form_url = f"{register_base_url}?token={register_token}"
    body_rows = render_selection_cards(ranked, signals_map, register_base_url, lang, register_token)

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>{selection_subject(mode, lang)}</h2>
      {_mode_note(mode, lang)}
      {body_rows}
      <p><a href="{register_form_url}">{t('other_ticker_link', lang)}</a></p>
      {_guide_link(register_base_url, lang)}
      <p style="color:#888;font-size:12px;">{t('disclaimer', lang)}</p>
      {_as_of_note(as_of, lang)}
      {_unsubscribe_link(register_base_url, register_token, lang)}
    </body></html>
    """


def build_monitoring_report(watchlist_results: list[dict], mode: str = "",
                            skipped: list[str] | None = None,
                            register_base_url: str | None = None,
                            register_token: str | None = None,
                            lang: str = LANG_JA,
                            as_of: str | None = None) -> str:
    """監視（売買判断）レポート・日次。各要素は {ticker, price, prev_close, signals, prev_signals}。
    register_base_url/register_tokenを渡すと、登録ページへのリンクを末尾に付ける
    (銘柄追加・削除は登録ページ側で行うため、ここから迷わず辿れるようにする)。
    as_ofを渡すと「◯月◯日時点のデータです」の注記を末尾に付ける(レポート即時発行
    ボタンが保存済みスナップショットを送る際に使う)。"""
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

    # 出来高の基準日数はモードごとに違う(短期・中期=20日、長期=50日)。
    # 以前は「20日平均比」と決め打ちしていたため、長期モードでは実際の計算
    # (volume_lookback=50)と表示がずれていた。config.pyの値を正本にする。
    volume_baseline_days = PERIOD_PRESETS.get(mode, {}).get("volume_lookback") or \
        PERIOD_PRESETS["MID"]["volume_lookback"]

    blocks = []
    for r in watchlist_results:
        sig = r["signals"]
        change_pct = (r["price"] - r["prev_close"]) / r["prev_close"] * 100 if r.get("prev_close") else 0

        warn_items = []
        if sig.get("sell_trend"):
            warn_items.append(t("warn_dead_cross", lang))
        if sig.get("sell_momentum"):
            rsi_warn = f"RSI overbought ({sig.get('rsi')})" if lang == LANG_EN else f"RSI買われすぎ({sig.get('rsi')})"
            warn_items.append(rsi_warn)
        sep = ", " if lang == LANG_EN else "・"
        warn_text = sep.join(warn_items) if warn_items else t("no_warnings", lang)

        volume_state = t("volume_high", lang) if sig.get("volume_high") else t("volume_normal", lang)
        if lang == LANG_EN:
            title = f"{r['ticker']}: ${r['price']:.2f} ({change_pct:+.1f}% vs prev close)"
            volume_line = (f"Volume: {volume_state} "
                           f"({sig.get('volume_ratio')}x the {volume_baseline_days}-day average)")
            warn_line = f"Warnings: {warn_text}"
        else:
            title = f"【{r['ticker']}】${r['price']:.2f}（前日比 {change_pct:+.1f}%）"
            volume_line = (f"出来高: {volume_state}"
                           f"（{volume_baseline_days}日平均比{sig.get('volume_ratio')}倍）")
            warn_line = f"警告: {warn_text}"

        blocks.append(f"""
        <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;">
          <p style="margin:4px 0;"><strong>{title}</strong></p>
          <table style="border-collapse:collapse;font-size:13px;">
            <tr><td></td><td style="padding:2px 8px;">{t('col_trend', lang)}</td><td style="padding:2px 8px;">{t('col_momentum', lang)}</td></tr>
            <tr><td style="padding:2px 8px;">{t('row_buy', lang)}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_trend'), "🟢")}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('buy_momentum'), "🟢")}</td></tr>
            <tr><td style="padding:2px 8px;">{t('row_sell', lang)}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_trend'), "🔴")}</td>
                <td style="padding:2px 8px;">{_signal_dot(sig.get('sell_momentum'), "🔴")}</td></tr>
          </table>
          <p style="margin:4px 0;">{volume_line}</p>
          <p style="margin:4px 0;">{warn_line}</p>
          <p style="margin:4px 0;">{t('peg_reference', lang)}: {r.get('peg','―')}</p>
          {_broker_links(r['ticker'], lang)}
          {_ai_prompt_link(register_base_url, r['ticker'], lang) if register_base_url else ''}
        </div>
        """)

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
    guide_link = ""
    if register_base_url:
        guide_link = _guide_link(register_base_url, lang)
    if register_base_url and register_token:
        register_link = f'<p><a href="{register_base_url}?token={register_token}">{t("register_link", lang)}</a></p>'
        unsubscribe_link = _unsubscribe_link(register_base_url, register_token, lang)

    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2>{t('monitoring_heading', lang)}</h2>
      {_mode_note(mode, lang)}
      <p>{t('summary_label', lang)}<br>{summary}</p>
      {''.join(blocks)}
      {skipped_note}
      {register_link}
      {guide_link}
      <p style="color:#888;font-size:12px;">{t('disclaimer', lang)}</p>
      {_as_of_note(as_of, lang)}
      {unsubscribe_link}
    </body></html>
    """
