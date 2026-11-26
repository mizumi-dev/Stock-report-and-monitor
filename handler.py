"""
daily-batch Lambda ハンドラ
毎日1回、EventBridge Schedulerから起動される想定

マルチテナント方針: 利用者の識別は招待トークンそのものを使う(別途user_idは発行しない)。
approved_tokensのstatus="approved"の利用者全員に、各自のwatchlist・期間モードに応じた
レポートを個別に送る。選定レポートはNASDAQ100全体のスキャンが重いため、利用者ごとではなく
「使われているモードごと」に1回だけスキャンし、そのモードの利用者全員に配る。
"""
import html
import math
import os
import traceback
import boto3
from boto3.dynamodb.conditions import Key
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config import (
    DEFAULT_PERIOD_MODE,
    DEFAULT_UNIVERSE,
    FETCH_MAX_WORKERS,
    MIN_FETCH_SUCCESS_RATIO,
    SETTINGS_TABLE_DEFAULT,
    TOKEN_STATUS_APPROVED,
    get_preset,
    normalize_mode,
    normalize_universe,
    normalize_universes,
    selection_snapshot_key,
    signal_history_date_key,
    universe_cache_key,
    user_period_mode_key,
    user_universe_key,
)
from data_sources import get_nasdaq100_tickers, get_sp500_tickers, get_stock_data
from mailer import ResendMailer
from signals import calc_all_signals
from ranking import rank_stocks
from report import build_selection_report, build_selection_report_multi, build_monitoring_report, build_weekly_tweet_text, selection_subject
from social import XPoster

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
SIGNAL_HISTORY_TABLE = os.environ.get("SIGNAL_HISTORY_TABLE", "signal_history")
SETTINGS_TABLE = os.environ.get("SETTINGS_TABLE", SETTINGS_TABLE_DEFAULT)
APPROVED_TOKENS_TABLE = os.environ.get("APPROVED_TOKENS_TABLE", "approved_tokens")
SES_SENDER = os.environ["SES_SENDER"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL") or os.environ.get("SES_RECIPIENT", "")
REGISTER_URL = os.environ["REGISTER_FUNCTION_URL"]
IS_WEEKLY_REPORT_DAY = os.environ.get("WEEKLY_REPORT_WEEKDAY", "0")  # 0=月曜
TOP_N = int(os.environ.get("TOP_N", "3"))

# 集客の自動化(2026-08-10)。週1回、選定レポートと同じ曜日にランキング上位を
# Xへ自動投稿する。X_POST_ENABLEDが"true"の環境でのみ動作させる
# (developでの手動起動時に誤って実アカウントへ投稿してしまうのを防ぐための
# 明示的なオプトイン。SIGNUPS_ENABLEDと同じ考え方)。
X_POST_ENABLED = os.environ.get("X_POST_ENABLED", "false").lower() == "true"
X_POST_MODE = os.environ.get("X_POST_MODE", "MID")
X_POST_UNIVERSE = os.environ.get("X_POST_UNIVERSE", DEFAULT_UNIVERSE)
PEGSIGNAL_URL = os.environ.get("PEGSIGNAL_URL", "https://pegsignal.com/")

dynamodb = boto3.resource("dynamodb")
# SESの本番アクセス審査がus-east-1・大阪どちらでも即時却下となり解決不能と
# 判断したため、Resend(mailer.py)に乗り換えた。ses変数名・呼び出し方は
# 既存コードとの互換のためそのまま残している。
ses = ResendMailer()
x_poster = XPoster()


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def get_approved_users() -> list[dict]:
    """status=approvedの利用者一覧({"token":..., "email":..., "lang":...})を返す。
    langは申込み・言語切替時にregister-stock側が保存したもの(未設定時は"ja")。"""
    table = dynamodb.Table(APPROVED_TOKENS_TABLE)
    resp = table.scan()
    return [
        {"token": item["token"], "email": item["email"], "lang": item.get("lang") or "ja"}
        for item in resp.get("Items", [])
        if item.get("status") == TOKEN_STATUS_APPROVED
    ]


def get_user_period_mode(owner_token: str) -> str:
    """利用者が画面で選んだ期間モードを読む。未設定・取得失敗時は既定モードで動かす
    (設定が読めないだけでレポート全体を止める必要はないため)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": user_period_mode_key(owner_token)})
        return normalize_mode(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] 期間モードの取得に失敗、既定値({DEFAULT_PERIOD_MODE})を使用: {e}")
        return DEFAULT_PERIOD_MODE


def get_user_universes(owner_token: str) -> list[str]:
    """利用者が画面で選んだ対象指数(ユニバース、複数可)を読む。未設定・取得失敗時は
    既定(NASDAQ100のみ)で動かす(設定が読めないだけでレポート全体を止める必要は
    ないため、get_user_period_modeと同じ方針)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": user_universe_key(owner_token)})
        return normalize_universes(resp.get("Item", {}).get("value"))
    except Exception as e:
        print(f"[WARN] ユニバースの取得に失敗、既定値({DEFAULT_UNIVERSE})を使用: {e}")
        return [DEFAULT_UNIVERSE]


def get_cached_tickers(universe: str = DEFAULT_UNIVERSE) -> list[str] | None:
    """前回成功時に保存した構成銘柄リスト(ユニバースごと)。Nasdaq API/Wikipedia
    障害時のフォールバック用。利用者に依存しない共有データなのでキーはそのまま。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": universe_cache_key(universe)})
        tickers = resp.get("Item", {}).get("value")
        return list(tickers) if tickers else None
    except Exception as e:
        print(f"[WARN] 銘柄キャッシュの取得に失敗: {e}")
        return None


def save_cached_tickers(universe: str, tickers: list[str]):
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        table.put_item(Item={
            "key": universe_cache_key(universe),
            "value": tickers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        # キャッシュ更新の失敗でレポート送信自体を止める必要はない
        print(f"[WARN] 銘柄キャッシュの保存に失敗: {e}")


def get_user_watchlist(owner_token: str) -> list[str]:
    table = dynamodb.Table(WATCHLIST_TABLE)
    resp = table.query(KeyConditionExpression=Key("owner_token").eq(owner_token))
    return [item["ticker"] for item in resp.get("Items", [])]


def _get_signal_snapshot(ticker: str, mode: str, date_str: str) -> dict | None:
    """指定日付分の監視データ(シグナル・価格・前日比較用データ)を1銘柄分読む。
    モードが同じ利用者同士で共有するキャッシュ(利用者ごとではない)。"""
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    resp = table.get_item(Key={"ticker": ticker, "date": signal_history_date_key(date_str, mode)})
    return resp.get("Item")


def get_prev_signals(ticker: str, mode: str) -> dict | None:
    """前日分のシグナル(「買いシグナル増加」判定の比較対象)。"""
    snapshot = _get_signal_snapshot(ticker, mode, _yesterday_str())
    return (snapshot or {}).get("signals")


def get_latest_signal_snapshot(ticker: str, mode: str) -> dict | None:
    """レポート即時発行ボタン用。当日分があればそれを、無ければ前日分を返す
    (バッチ未実行の時間帯に押された場合の保険)。どちらも無ければNone
    (登録直後でまだ誰の分も計算されていない銘柄)。"""
    return _get_signal_snapshot(ticker, mode, _today_str()) or _get_signal_snapshot(ticker, mode, _yesterday_str())


def _to_dynamodb_safe(value):
    """DynamoDBはPythonのfloatを受け付けないためDecimalに変換する。
    NaN・無限大はDynamoDBが受け付けず(TypeError: Infinity and NaN not supported)、
    1銘柄の値が欠けただけでバッチ全体が止まっていたため、None(値なし)として保存する
    (2026-11-25の本番障害: 監視レポートの保存で落ち、自動再試行で選定レポートが3通届いた)。"""
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_safe(v) for v in value]
    return value


def save_signal_snapshot(ticker: str, mode: str, entry: dict):
    """本日分の監視データ(シグナル・価格・前日比較用データ)をまとめて保存する。
    レポート即時発行ボタンはこのスナップショットを読むだけで、新規のyfinance取得は
    行わない(定時実行結果と常に一致させ、ボタン連打によるコスト増・利用者間で共有する
    シグナル履歴の汚染を防ぐため)。"""
    table = dynamodb.Table(SIGNAL_HISTORY_TABLE)
    table.put_item(Item={
        "ticker": ticker,
        "date": signal_history_date_key(_today_str(), mode),
        "as_of_date": _today_str(),
        **_to_dynamodb_safe(entry),
    })


def send_email(subject: str, html_body: str, to_address: str):
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to_address]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
        },
    )


# report.py が実際に使うフィールドのみ(ranked各要素にはdata_sources.get_stock_data由来の
# "history"(pandas DataFrame、DynamoDBに保存できない)や current_price/prev_close/error等、
# レポート表示に不要なフィールドも混ざっているため、保存前に削ぎ落とす)
_SELECTION_CACHE_FIELDS = ("ticker", "company_name", "peg", "earnings_growth", "market_cap")


def save_selection_snapshot(mode: str, ranked: list[dict], signals_map: dict,
                            universe: str = DEFAULT_UNIVERSE):
    """選定レポートのランキング+シグナルを(モード,ユニバース)ごとに保存する。レポート
    即時発行ボタンはこれを読むだけで新規スキャンを行わない(定時実行結果と常に一致させ、
    ボタン連打によるコスト増を防ぐため)。signals_mapはランキングに載る銘柄分だけに
    刈り込んでから保存する(全銘柄分は不要で、無駄にDynamoDBの項目サイズを使うため)。"""
    cacheable_ranked = [{k: s.get(k) for k in _SELECTION_CACHE_FIELDS} for s in ranked]
    tickers = [s["ticker"] for s in ranked]
    trimmed_signals = {t: signals_map[t] for t in tickers if t in signals_map}
    table = dynamodb.Table(SETTINGS_TABLE)
    table.put_item(Item={
        "key": selection_snapshot_key(mode, universe),
        "ranked": _to_dynamodb_safe(cacheable_ranked),
        "signals_map": _to_dynamodb_safe(trimmed_signals),
        "as_of_date": _today_str(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_selection_snapshot(mode: str, universe: str = DEFAULT_UNIVERSE) -> dict | None:
    """レポート即時発行ボタン用。保存済みスナップショットが無ければNone
    (サービス開始直後など、その(モード,ユニバース)でまだ一度もバッチが走っていない場合)。"""
    try:
        table = dynamodb.Table(SETTINGS_TABLE)
        resp = table.get_item(Key={"key": selection_snapshot_key(mode, universe)})
        item = resp.get("Item")
        if not item or "ranked" not in item:
            return None
        return {"ranked": list(item["ranked"]), "signals_map": dict(item["signals_map"]),
                "as_of_date": item.get("as_of_date")}
    except Exception as e:
        print(f"[WARN] 選定スナップショットの取得に失敗: {e}")
        return None


def _scan_universe(mode: str, universe: str = DEFAULT_UNIVERSE) -> tuple[list, dict]:
    """対象指数(NASDAQ100またはS&P500)全銘柄をスキャンし、ランキングと全銘柄分の
    シグナルを返す。利用者ごとではなく、同じ(モード,ユニバース)の組み合わせを使う
    利用者全員で使い回す(スキャンが重いため)。結果はそのままその組み合わせの
    「本日のスナップショット」として保存する(定時実行・即時発行ボタンのフォールバック、
    いずれから呼ばれても同じ扱いにする)。"""
    preset = get_preset(mode)
    universe = normalize_universe(universe)

    cached = get_cached_tickers(universe)
    if universe == "SP500":
        tickers = get_sp500_tickers(fallback=cached)
    else:
        tickers = get_nasdaq100_tickers(fallback=cached)
    save_cached_tickers(universe, tickers)

    # I/O待ちが支配的な逐次取得がボトルネックのため並列化する。非公式APIへの
    # 負荷を抑えるため全並列ではなくFETCH_MAX_WORKERSで頭打ちにする。
    stocks, signals_map = [], {}
    with ThreadPoolExecutor(max_workers=FETCH_MAX_WORKERS) as executor:
        fetched = executor.map(
            lambda t: get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"]),
            tickers,
        )
        for d in fetched:
            if d.get("history") is None:
                continue
            stocks.append(d)
            signals_map[d["ticker"]] = calc_all_signals(d["history"], mode)

    # 個別銘柄のたまたまの失敗ではなく外部API自体が落ちている可能性が高い場合は、
    # 中身がほぼ空のレポートを送らずエラー通知に切り替える(handler()のtry/exceptに乗せる)
    success_ratio = len(stocks) / len(tickers) if tickers else 0
    if success_ratio < MIN_FETCH_SUCCESS_RATIO:
        raise RuntimeError(
            f"銘柄データの取得成功率が低すぎます({len(stocks)}/{len(tickers)}件、"
            f"閾値{MIN_FETCH_SUCCESS_RATIO:.0%}未満)。外部APIの障害の可能性があります。"
        )

    ranked = rank_stocks(stocks, top_n=TOP_N)
    save_selection_snapshot(mode, ranked, signals_map, universe)
    return ranked, signals_map


def _sent_key(owner_token: str, kind: str) -> str:
    return f"{owner_token}#last_sent#{kind}"


def _already_sent_today(owner_token: str, kind: str) -> bool:
    """定時実行の自動再試行で同じレポートを二重に送らないための確認。
    確認自体に失敗した場合は「未送信」とみなす(送れないよりは重複の方がまし)。"""
    try:
        item = dynamodb.Table(SETTINGS_TABLE).get_item(Key={"key": _sent_key(owner_token, kind)}).get("Item")
        return bool(item) and item.get("value") == _today_str()
    except Exception as e:
        print(f"[WARN] 送信済み記録の確認に失敗: {e}")
        return False


def _mark_sent_today(owner_token: str, kind: str):
    try:
        dynamodb.Table(SETTINGS_TABLE).put_item(Item={
            "key": _sent_key(owner_token, kind),
            "value": _today_str(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f"[WARN] 送信済み記録の保存に失敗: {e}")


def run_selection_reports(users: list[dict], dedupe: bool = False):
    """usersに含まれる利用者全員へ選定レポートを送る(定時実行用)。(モード,ユニバース)の
    組み合わせごとに1回だけスキャンし、そのスキャン結果がそのままその日のスナップショットになる。
    対象指数を複数選んでいる利用者には、指数ごとのランキングを1通にまとめて送る
    (2026-11-25)。スキャンは(モード,ユニバース)単位で使い回すので、両方選んだ利用者が
    いても同じ組み合わせを二重にスキャンすることはない。
    dedupe=True(定時実行)のときは、同じ日にすでに送った利用者には送らない。Lambdaは
    エラー終了すると自動で2回再実行するため、後続処理で落ちると同じメールが最大3通
    届いていた(2026-11-25の本番障害)。"""
    if dedupe:
        users = [u for u in users if not _already_sent_today(u["token"], "selection")]
    by_key: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    for u in users:
        mode = get_user_period_mode(u["token"])
        universes = tuple(get_user_universes(u["token"]))
        by_key.setdefault((mode, universes), []).append(u)

    scans: dict[tuple[str, str], tuple[list, dict]] = {}
    for (mode, universes), group in by_key.items():
        for universe in universes:
            if (mode, universe) not in scans:
                scans[(mode, universe)] = _scan_universe(mode, universe)
        for u in group:
            lang = u.get("lang") or "ja"
            body = _selection_body(
                [(universe, *scans[(mode, universe)], _today_str()) for universe in universes],
                u["token"], mode, lang)
            subject = selection_subject(mode, lang)
            send_email(subject, body, u["email"])
            if dedupe:
                _mark_sent_today(u["token"], "selection")


def _selection_body(results: list[tuple], token: str, mode: str, lang: str) -> str:
    """resultsは(universe, ranked, signals_map, as_of)のリスト。対象指数が1つなら
    従来どおりの本文、複数なら指数ごとのセクションを並べた本文を返す。"""
    if len(results) == 1:
        universe, ranked, signals_map, as_of = results[0]
        return build_selection_report(ranked, signals_map, REGISTER_URL, token, mode,
                                      lang=lang, as_of=as_of, universe=universe)
    sections = [{"universe": universe, "ranked": ranked, "signals_map": signals_map, "as_of": as_of}
                for universe, ranked, signals_map, as_of in results]
    return build_selection_report_multi(sections, REGISTER_URL, token, mode, lang=lang)


def send_selection_report_now(user: dict):
    """レポート即時発行ボタン用。保存済みスナップショットを読んで送るだけで、
    新規スキャンは行わない(その(モード,ユニバース)で一度もバッチが走っていない場合のみ、
    その場で1回スキャンする)。"""
    mode = get_user_period_mode(user["token"])
    results = []
    for universe in get_user_universes(user["token"]):
        snapshot = get_selection_snapshot(mode, universe)
        if snapshot is None:
            ranked, signals_map = _scan_universe(mode, universe)  # 内部でスナップショットも保存される
            as_of = _today_str()
        else:
            ranked, signals_map = snapshot["ranked"], snapshot["signals_map"]
            as_of = snapshot["as_of_date"]
        results.append((universe, ranked, signals_map, as_of))

    lang = user.get("lang") or "ja"
    body = _selection_body(results, user["token"], mode, lang)
    subject = selection_subject(mode, lang)
    send_email(subject, body, user["email"])


def run_monitoring_reports(users: list[dict], dedupe: bool = False):
    """usersに含まれる利用者全員へ、それぞれの監視リストに基づくレポートを送る
    (定時実行用)。watchlistが空の利用者はスキップする。利用者ごとの監視銘柄を
    モードごとに集約し、複数人が同じ銘柄を登録していても取得・計算は1回だけにする。"""
    watchlists = {u["token"]: get_user_watchlist(u["token"]) for u in users}
    modes = {u["token"]: get_user_period_mode(u["token"]) for u in users if watchlists[u["token"]]}

    tickers_by_mode: dict[str, set[str]] = {}
    for token, mode in modes.items():
        tickers_by_mode.setdefault(mode, set()).update(watchlists[token])

    # (mode, ticker) -> スナップショット、または取得失敗を表すNone
    snapshots: dict[tuple[str, str], dict | None] = {}
    for mode, tickers in tickers_by_mode.items():
        preset = get_preset(mode)
        for t in tickers:
            # 1銘柄の想定外のデータでバッチ全体(他の銘柄・他の利用者)を止めないよう、
            # 銘柄単位で失敗を隔離する。計算に失敗した銘柄は「データ不足」と同じ扱い
            try:
                d = get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"])
                if d.get("history") is None:
                    snapshots[(mode, t)] = None
                    continue
                sig = calc_all_signals(d["history"], mode)
                entry = {
                    "signals": sig,
                    "price": d["current_price"],
                    "prev_close": d["prev_close"],
                    "peg": d.get("peg"),
                    "prev_signals": get_prev_signals(t, mode),
                }
            except Exception as e:
                print(f"[WARN] {t}({mode})の監視データ計算に失敗、スキップ: {e}")
                snapshots[(mode, t)] = None
                continue
            try:
                save_signal_snapshot(t, mode, entry)
            except Exception as e:
                # 保存できなくても、本日のレポートには載せる
                print(f"[WARN] {t}({mode})の監視データ保存に失敗: {e}")
            snapshots[(mode, t)] = entry

    for u in users:
        watchlist = watchlists[u["token"]]
        if not watchlist:
            continue
        if dedupe and _already_sent_today(u["token"], "monitoring"):
            continue
        mode = modes[u["token"]]
        results, skipped = [], []
        for t in watchlist:
            entry = snapshots.get((mode, t))
            if entry is None:
                # 上場間もない等でデータが足りない銘柄。黙って消すと利用者が混乱するため
                # レポートに理由付きで載せる
                skipped.append(t)
                continue
            results.append({
                "ticker": t,
                "price": entry["price"],
                "prev_close": entry["prev_close"],
                "signals": entry["signals"],
                "prev_signals": entry.get("prev_signals"),
                "peg": entry.get("peg"),
            })

        lang = u.get("lang") or "ja"
        body = build_monitoring_report(results, mode, skipped, REGISTER_URL, u["token"],
                                       lang=lang, as_of=_today_str())
        subject = "📊 Today's monitoring report" if lang == "en" else "📊 本日の監視レポート"
        send_email(subject, body, u["email"])
        if dedupe:
            _mark_sent_today(u["token"], "monitoring")


def send_monitoring_report_now(user: dict):
    """レポート即時発行ボタン用。保存済みスナップショット(当日分、無ければ前日分)を
    読んで送るだけで、新規のyfinance取得は行わない(登録直後でまだ誰の分も計算されて
    いない銘柄のみ、その場で1回取得する)。"""
    watchlist = get_user_watchlist(user["token"])
    if not watchlist:
        return

    mode = get_user_period_mode(user["token"])
    preset = get_preset(mode)
    results, skipped, as_of_dates = [], [], []
    for t in watchlist:
        snapshot = get_latest_signal_snapshot(t, mode)
        if snapshot is None:
            d = get_stock_data(t, period=preset["history_period"], min_rows=preset["min_rows"])
            if d.get("history") is None:
                skipped.append(t)
                continue
            sig = calc_all_signals(d["history"], mode)
            snapshot = {
                "signals": sig,
                "price": d["current_price"],
                "prev_close": d["prev_close"],
                "peg": d.get("peg"),
                "prev_signals": get_prev_signals(t, mode),
                "as_of_date": _today_str(),
            }
            try:
                save_signal_snapshot(t, mode, snapshot)
            except Exception as e:
                print(f"[WARN] {t}({mode})の監視データ保存に失敗: {e}")

        results.append({
            "ticker": t,
            "price": snapshot["price"],
            "prev_close": snapshot["prev_close"],
            "signals": snapshot["signals"],
            "prev_signals": snapshot.get("prev_signals"),
            "peg": snapshot.get("peg"),
        })
        as_of_dates.append(snapshot.get("as_of_date", _today_str()))

    lang = user.get("lang") or "ja"
    as_of = min(as_of_dates) if as_of_dates else _today_str()
    body = build_monitoring_report(results, mode, skipped, REGISTER_URL, user["token"], lang=lang, as_of=as_of)
    subject = "📊 Today's monitoring report" if lang == "en" else "📊 本日の監視レポート"
    send_email(subject, body, user["email"])


def should_send_selection(mode: str, weekday: str, day_of_month: int, weekly_report_day: str) -> bool:
    """選定レポートを送るべきかを判定する純粋関数(テスト容易性のため副作用を持たせない)。
    短期=毎日、中期=毎週指定曜日、長期=月初(1〜7日)の指定曜日のみ(月1回)。"""
    is_report_weekday = weekday == weekly_report_day
    return (
        mode == "SHORT"
        or (mode == "MID" and is_report_weekday)
        or (mode == "LONG" and is_report_weekday and day_of_month <= 7)
    )


def _dispatch(event: dict) -> dict:
    # TODO#7b: 例外捕捉→通知の経路を本番に影響を与えずテストするためのフック。
    # 実データには一切触れず即座に例外を送出するだけなので、実AWS環境で安全に
    # 障害通知の実地確認ができる(test-skip-reviewスキルでの検討結果を反映)。
    if event.get("_test_trigger_failure"):
        raise RuntimeError("これはTODO#7bのテスト用に意図的に発生させた例外です。実際の障害ではありません。")

    owner_token = event.get("owner_token")

    # 画面の「監視レポートを送る」ボタンから来た場合は、押した本人の分だけ、保存済み
    # スナップショットから即座に組み立てて送る(新規のyfinance取得は行わない)。
    # owner_token無しでの呼び出しはテスト・運用目的の一斉送信用(定時実行と同じ経路)
    if event.get("monitoring_only"):
        if owner_token:
            target = next((u for u in get_approved_users() if u["token"] == owner_token), None)
            if target:
                send_monitoring_report_now(target)
        else:
            run_monitoring_reports(get_approved_users())
        return {"statusCode": 200, "body": "OK"}

    # 画面の「選定レポートを送る」ボタンから来た場合も同様、保存済みスナップショットを
    # 読むだけで送る(そのモードで一度もバッチが走っていない場合のみその場でスキャンする)
    if event.get("force_weekly"):
        if owner_token:
            target = next((u for u in get_approved_users() if u["token"] == owner_token), None)
            if target:
                send_selection_report_now(target)
        else:
            run_selection_reports(get_approved_users())
        return {"statusCode": 200, "body": "OK"}

    # EventBridge Schedulerからの定期実行。TODO#7a: simulate_dateがあればそれを使い、
    # 通常は実行時刻を使う(本番動作に影響を与えずテスト時だけ日付を差し替えられる)
    simulate_date = event.get("simulate_date")
    now = datetime.fromisoformat(simulate_date) if simulate_date else datetime.now(timezone.utc)
    weekday = str(now.weekday())
    day_of_month = now.day

    users = get_approved_users()
    selection_targets = [u for u in users if should_send_selection(
        get_user_period_mode(u["token"]), weekday, day_of_month, IS_WEEKLY_REPORT_DAY)]
    if selection_targets:
        run_selection_reports(selection_targets, dedupe=True)
    run_monitoring_reports(users, dedupe=True)
    if weekday == IS_WEEKLY_REPORT_DAY:
        post_weekly_tweet()
    return {"statusCode": 200, "body": "OK"}


def post_weekly_tweet():
    """週次でランキング上位をXへ投稿する(集客の自動化、2026-08-10)。保存済み
    スナップショットを読むだけで新規スキャンは行わない(その組み合わせでまだ
    一度もバッチが走っていない場合のみ、その場で1回スキャンする、既存の
    send_selection_report_nowと同じフォールバック)。投稿の失敗はメール配信
    (本来の役務)を止める理由にはならないため、ここで例外を握りつぶす。"""
    if not X_POST_ENABLED:
        return
    try:
        snapshot = get_selection_snapshot(X_POST_MODE, X_POST_UNIVERSE)
        if snapshot is None:
            ranked, _ = _scan_universe(X_POST_MODE, X_POST_UNIVERSE)
        else:
            ranked = snapshot["ranked"]
        text = build_weekly_tweet_text(ranked, X_POST_MODE, X_POST_UNIVERSE,
                                       url=f"{PEGSIGNAL_URL}?src=x_auto")
        x_poster.post_tweet(text)
    except Exception as e:
        print(f"[WARN] 週次Xポストに失敗しました: {e}")


def _notify_failure(event: dict, exc: Exception):
    """失敗を運用者(あなた)にメールで知らせる。通知自体が失敗しても呼び出し元の例外送出は
    止めない(元の例外を握りつぶすと、CloudWatchのErrorsメトリクスに記録されず
    無人実行が黙って失敗し続けるという、対応しようとしている問題そのものを再発させるため)。"""
    try:
        body = (
            f"<p>daily-batchの実行中にエラーが発生しました。</p>"
            f"<p>event: {html.escape(str(event))}</p>"
            f"<pre>{html.escape(traceback.format_exc())}</pre>"
        )
        send_email("🚨 daily-batchが失敗しました", body, ADMIN_EMAIL)
    except Exception as notify_exc:
        print(f"[ERROR] 失敗通知メールの送信にも失敗しました: {notify_exc}")


def handler(event, context):
    event = event or {}
    try:
        return _dispatch(event)
    except Exception as exc:
        print(f"[ERROR] handler failed: {exc}")
        _notify_failure(event, exc)
        raise
