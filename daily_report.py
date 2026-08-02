"""毎朝メール：高市政権の国策テーマ × 仕込み候補（未来×割安）

対象テーマ：核融合・レアアース・フィジカルAI・ロボット・宇宙・創薬・
半導体・量子コンピューター など、高市政権の重点17分野ベースの国策テーマ。

主役は「💎仕込み度」＝ 未来テーマなのに、まだ安い株 を検出する採点：
  ・割安（PER/PBR/PSR/ネットキャッシュ）        …最大45点
  ・財務の堅さ（自己資本比率）                  …最大15点
  ・まだ安い位置（6ヶ月レンジの安値圏・低RSI）  …最大30点
  ・成長のタネ（増収）                          …最大10点
勢い（モメンタム）分析・買い時シグナルは補助情報として併載。
"""
import os
import ssl
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

# GitHub Actions は UTC で動くため、日付・曜日判定は必ず日本時間で行う
JST = timezone(timedelta(hours=9), "JST")


def now_jst() -> datetime:
    return datetime.now(JST)

# Windowsコンソール(cp932)でも絵文字ログが出せるようUTF-8に
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import yfinance as yf

from modules.theme_search import get_trending_themes, get_theme_stocks_by_name
from modules.financial_data import get_financial_data, score_stock, _get_financial_data_uncached
from modules.price_signal import buy_timing, _buy_timing_uncached
from modules import portfolio
from modules import history
from modules.news_fetcher import get_stock_news
from modules.yf_retry import with_retry

# ── 国策テーマ（高市政権の重点分野ベース。株探で銘柄が返る正式名）──
KOKUSAKU_THEMES = [
    "核融合発電", "レアアース", "フィジカルAI", "ロボット",
    "宇宙開発関連", "創薬", "半導体", "半導体製造装置",
    "パワー半導体", "量子コンピューター", "人工知能",
    "防衛", "サイバーセキュリティ",
]

# ── 設定 ──────────────────────────────────────────
STOCKS_PER_THEME  = int(os.environ.get("STOCKS_PER_THEME",  "15").strip() or "15")
TOP_N             = int(os.environ.get("REPORT_TOP_N",      "15").strip() or "15")
# 株探の「今日の人気テーマ」も国策テーマに混ぜるか（1で有効）
USE_TRENDING      = os.environ.get("USE_TRENDING", "0").strip() == "1"
# 週間サマリーを強制する（テスト用。通常は土曜朝に自動で週間モード）
FORCE_WEEKLY      = os.environ.get("FORCE_WEEKLY", "0").strip() == "1"
# 📊月次レビュー（答え合わせ）を強制する（テスト用。通常は毎月1日の朝に自動表示）
FORCE_REVIEW      = os.environ.get("FORCE_REVIEW", "0").strip() == "1"
# ──────────────────────────────────────────────────

# 診断用：直近実行の結果サマリー（history/last_run_debug.txt に保存して原因究明に使う）
_RUN_STATS: dict = {}


def shikomi_score(r: dict) -> tuple[int, list[str], list[str]]:
    """💎仕込み度（0〜100）：未来テーマ×割安×まだ安値圏 を採点。

    「良いテーマなのにまだ上がっていない株」ほど高得点。
    すでに急騰した株（過熱）は減点して弾く。

    ただし割安には「安いなりの理由」があることも多い（バリュートラップ）。
    減収・減益・営業赤字・債務過多は減点し、⚠️警告として返す。

    戻り値: (仕込み度0〜100, 加点理由リスト, ⚠️警告リスト)
    """
    pts, reasons, warnings = 0, [], []

    # ── 割安（最大45点）──
    per, pbr, psr = r.get("per"), r.get("pbr"), r.get("psr")
    if per is not None:
        if per <= 15:
            pts += 15
            reasons.append(f"PER{per:.1f}倍と割安")
        elif per <= 20:
            pts += 8
    if pbr is not None:
        if pbr <= 1.0:
            pts += 15
            reasons.append(f"PBR{pbr:.2f}倍（解散価値以下）")
        elif pbr <= 1.5:
            pts += 8
    if psr is not None and psr <= 1.0:
        pts += 10
        reasons.append("PSR1倍以下（エミン基準）")
    if r.get("net_cash_over_mcap"):
        pts += 5
        reasons.append("ネットキャッシュ＞時価総額（タダ株級）")

    # ── 財務の堅さ（最大15点）──
    eq = r.get("equity_ratio")
    if eq is not None:
        if eq >= 50:
            pts += 15
            reasons.append(f"自己資本比率{eq:.0f}%と堅い")
        elif eq >= 40:
            pts += 8

    # ── まだ安い位置＝出遅れ（最大30点）──
    pos = r.get("pos6m")
    if pos is not None:
        if pos <= 35:
            pts += 20
            reasons.append("直近6ヶ月の安値圏（出遅れ）")
        elif pos <= 55:
            pts += 10
            reasons.append("6ヶ月レンジの中位以下")
    rsi = r.get("rsi")
    if rsi is not None and rsi <= 55:
        pts += 10

    # ── 成長のタネ（最大10点）──
    rg = r.get("revenue_growth")
    if rg is not None and rg >= 5:
        pts += 10
        reasons.append(f"増収{rg:.0f}%")

    # ── バリュートラップ減点（安いには理由がある、を見逃さない）──
    if rg is not None:
        if rg < -10:
            pts -= 20
            warnings.append(f"大幅減収（{rg:.0f}%）")
        elif rg < 0:
            pts -= 10
            warnings.append(f"減収中（{rg:.0f}%）")

    eg = r.get("earnings_growth")
    if eg is not None:
        if eg < -20:
            pts -= 20
            warnings.append(f"大幅減益（{eg:.0f}%）")
        elif eg < 0:
            pts -= 10
            warnings.append(f"減益中（{eg:.0f}%）")

    om = r.get("operating_margin")
    if om is not None and om < 0:
        pts -= 20
        warnings.append(f"営業赤字（{om:.1f}%）")

    if eq is not None:
        if eq < 20:
            pts -= 25
            warnings.append(f"自己資本比率{eq:.0f}%と債務過多")
        elif eq < 30:
            pts -= 15
            warnings.append(f"自己資本比率{eq:.0f}%と低め")

    # すでに急騰した株は仕込み対象から外す
    if r.get("signal") == "hot":
        pts = max(0, pts - 40)

    return max(0, min(100, pts)), reasons[:4], warnings


def _theme_momentum(all_rows: list[dict]) -> list[dict]:
    """テーマごとに勢い（モメンタム）を集計してランキングを返す。

    各テーマについて：
      buy_ratio  … 🟢買い場の割合（健全に上昇＝これから来る目安）
      hot_ratio  … 🔴過熱の割合（上がりきり注意）
      avg_change … 前日比の平均（足元の強さ）
      avg_score  … 財務スコアの平均（中身の良さ）
    """
    by_theme: dict[str, list[dict]] = {}
    for r in all_rows:
        if r.get("signal"):  # buy_timingが取れた銘柄のみ
            by_theme.setdefault(r["theme"], []).append(r)

    stats = []
    for theme, rs in by_theme.items():
        n = len(rs)
        if n == 0:
            continue
        buy_n = sum(1 for r in rs if r["signal"] == "buy")
        hot_n = sum(1 for r in rs if r["signal"] == "hot")
        avg_change = sum(r.get("prev_change", 0) or 0 for r in rs) / n
        avg_week = sum(r.get("week_change", 0) or 0 for r in rs) / n
        avg_score = sum(r["score"] for r in rs) / n
        avg_shikomi = sum(r.get("shikomi", 0) for r in rs) / n
        buy_ratio = buy_n / n
        hot_ratio = hot_n / n

        # 勢いスコア：買い場が多いほど＋、過熱が多いほど−、足元プラスを少し加点
        momentum = (buy_ratio * 60
                    - hot_ratio * 25
                    + max(-1.0, min(1.0, avg_change / 3.0)) * 15
                    + (avg_score - 50) * 0.2)

        # 状態ラベル
        if hot_ratio >= 0.4:
            status = "🔴 過熱気味（上がりきり注意）"
        elif buy_ratio >= 0.3:
            status = "🟢 上昇の勢い（狙い目）"
        elif avg_change >= 1.0:
            status = "📈 動意づく"
        else:
            status = "⬜ 様子見"

        stats.append({
            "theme": theme, "n": n, "buy_n": buy_n, "hot_n": hot_n,
            "buy_ratio": buy_ratio, "hot_ratio": hot_ratio,
            "avg_change": avg_change, "avg_week": avg_week,
            "avg_score": avg_score, "avg_shikomi": avg_shikomi,
            "momentum": momentum, "status": status,
        })

    stats.sort(key=lambda x: x["momentum"], reverse=True)
    return stats


def recommend_next_week(theme_stats: list[dict], all_rows: list[dict]) -> list[dict]:
    """🔮 次週のおすすめ国策テーマ TOP3（週間サマリー用）。

    長期・仕込み派向けの基準：
      おすすめ度 = 平均仕込み度×0.5（割安×出遅れの余地が主役）
                 ＋ 🟢買い場比率×30（動き始めの兆し）
                 − 🔴過熱比率×30（もう遅いテーマは下げる）
                 ± 週間騰落ボーナス（0〜+5%=底打ちの動き出し+10、
                    +8%超=急騰済み-10、-5%超の下落=下げ止まり待ち-5）
    「安いのに、静かに動き始めたテーマ」が1位に来る設計。
    """
    recs = []
    for t in theme_stats:
        aw = t.get("avg_week", 0)
        if 0 <= aw <= 5:
            week_bonus = 10
        elif -2 <= aw < 0:
            week_bonus = 5
        elif aw > 8:
            week_bonus = -10
        elif aw < -5:
            week_bonus = -5
        else:
            week_bonus = 0

        rec_score = (t.get("avg_shikomi", 0) * 0.5
                     + t["buy_ratio"] * 30
                     - t["hot_ratio"] * 30
                     + week_bonus)

        # 理由文（ユーザーが読んで納得できる言葉で）
        reasons = []
        if t.get("avg_shikomi", 0) >= 50:
            reasons.append(f"平均仕込み度{t['avg_shikomi']:.0f}点＝割安・出遅れの余地が大きい")
        if t["buy_ratio"] >= 0.3:
            reasons.append(f"🟢買い場が{t['buy_n']}/{t['n']}社と動き始めの兆し")
        if 0 <= aw <= 5:
            reasons.append(f"週間{aw:+.1f}%と急騰前の水準")
        elif aw > 8:
            reasons.append(f"週間{aw:+.1f}%と急騰済み・押し目待ち")
        elif aw < -5:
            reasons.append(f"週間{aw:+.1f}%と調整中・下げ止まり確認を")
        if t["hot_ratio"] >= 0.3:
            reasons.append("過熱銘柄が多め")
        if not reasons:
            reasons.append(f"平均仕込み度{t.get('avg_shikomi', 0):.0f}点・週間{aw:+.1f}%")

        # テーマ内の代表仕込み銘柄（過熱を除く仕込み度トップ）
        cands = [r for r in all_rows
                 if r.get("theme") == t["theme"] and r.get("signal") not in (None, "hot")
                 and not r.get("trap_excluded")]
        cands.sort(key=lambda x: (-x.get("shikomi", 0), -x["score"]))
        best = cands[0] if cands else None

        recs.append({**t, "rec_score": rec_score,
                     "rec_reasons": "／".join(reasons[:3]), "best_stock": best})

    recs.sort(key=lambda x: x["rec_score"], reverse=True)
    return recs[:3]


def build_todays_report():
    """国策テーマから、テーマ勢い・一押し銘柄・銘柄一覧を返す。

    戻り値: (table_rows, todays_themes, theme_ranking, highlight)
    """
    # ① 対象テーマ＝国策テーマ（必要なら今日の人気テーマも追加）
    todays_themes = list(KOKUSAKU_THEMES)
    if USE_TRENDING:
        for t in get_trending_themes(6):
            if t not in todays_themes:
                todays_themes.append(t)
    print(f"対象テーマ（{len(todays_themes)}件）: {todays_themes}")

    # ② 各テーマから銘柄を収集（重複除去）。
    #    株探スクレイピングが失敗すると銘柄ゼロの空メールになりかねないため、
    #    テーマ間にウェイトを入れつつ、収集数が少なければ再試行する。
    MIN_STOCKS = 20
    MAX_ATTEMPTS = 3
    raw_stocks = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        seen_codes, raw_stocks = set(), []
        for theme in todays_themes:
            for s in get_theme_stocks_by_name(theme)[:STOCKS_PER_THEME]:
                if s["code"] not in seen_codes:
                    seen_codes.add(s["code"])
                    raw_stocks.append({**s, "theme": theme})
            time.sleep(0.7)
        print(f"銘柄収集（{attempt}回目）: {len(raw_stocks)} 社")

        if len(raw_stocks) >= MIN_STOCKS:
            break
        if attempt < MAX_ATTEMPTS:
            print(f"収集数が{MIN_STOCKS}社未満のため60秒待って再収集します...")
            time.sleep(60)
    print(f"銘柄収集（確定）: {len(raw_stocks)} 社")
    _RUN_STATS["theme_collect_attempts"] = attempt
    _RUN_STATS["theme_collect_total"] = len(raw_stocks)

    # ③ 財務データ＋スコア＋買い時シグナルを全銘柄で取得
    #    yfinance呼び出し（get_financial_data / buy_timing）はGitHub Actionsの
    #    クラウド出口IPからの大量連続アクセスでYahoo Finance側にレート制限/
    #    ブロックされることがあるため、各呼び出しを指数バックオフ付きリトライで
    #    ラップし、さらに成功率が極端に低い場合は失敗銘柄だけ全体を再試行する。
    def _fetch_row(s: dict) -> dict | None:
        """1銘柄分の財務データ＋買い時シグナルを取得する（失敗時はNone）。

        with_retry には必ず _uncached 版を渡すこと。st.cache_data 付きの
        get_financial_data / buy_timing をそのまま渡すと、1回目の失敗（None）
        がキャッシュされ、リトライしても実際には再実行されず同じNoneが
        即座に返るだけになり、リトライが機能しない。
        """
        fin = with_retry(_get_financial_data_uncached, s["code"])
        if not fin:
            return None
        fin["name"]  = s["name"]
        fin["theme"] = s["theme"]
        row = {**fin, **score_stock(fin)}

        bt = with_retry(_buy_timing_uncached, s["code"])
        if bt:
            row.update({
                "signal":      bt["signal"],
                "buy_label":   bt["label"],
                "buy_reason":  bt["reasons"],
                "prev_change": bt["prev_change"],
                "week_change": bt.get("week_change"),
                "rsi":         bt["rsi"],
                "dev25":       bt["dev25"],
                "pos6m":       bt.get("pos6m"),
            })
        else:
            row["signal"] = None
            row["buy_label"] = "－"

        # 💎仕込み度（未来×割安×出遅れ）／⚠️バリュートラップ警告
        row["shikomi"], row["shikomi_reasons"], row["trap_warnings"] = shikomi_score(row)
        row["trap_excluded"] = bool(
            (row.get("operating_margin") is not None and row["operating_margin"] < 0)
            or len(row["trap_warnings"]) >= 2
        )
        return row

    all_rows = []
    failed_stocks = []
    for s in raw_stocks:
        row = _fetch_row(s)
        if row is None:
            failed_stocks.append(s)
        else:
            all_rows.append(row)
        time.sleep(0.35)

    total = len(raw_stocks)
    success_rate = (len(all_rows) / total) if total else 0.0

    # 成功率が極端に低い（30%未満）かつ母数が十分ある場合は、Yahoo Finance側の
    # レート制限を疑い、90秒待ってから失敗した銘柄だけもう一度取得し直す（最大1回）。
    if total >= 20 and success_rate < 0.3 and failed_stocks:
        print(f"財務データ取得成功率が低い（{success_rate*100:.0f}%）ため90秒待って再試行します...")
        time.sleep(90)
        still_failed = []
        for s in failed_stocks:
            row = _fetch_row(s)
            if row is None:
                still_failed.append(s)
            else:
                all_rows.append(row)
            time.sleep(0.35)
        failed_stocks = still_failed

    final_rate = (len(all_rows) / total * 100) if total else 0.0
    print(f"財務データ取得成功率: {final_rate:.0f}% ({len(all_rows)}/{total}社)")
    print(f"財務＋シグナル取得: {len(all_rows)} 社")
    _RUN_STATS["fin_success_rate"] = final_rate
    _RUN_STATS["fin_success_n"] = len(all_rows)
    _RUN_STATS["fin_total"] = total

    # ④ テーマ別の勢いランキング
    theme_ranking = _theme_momentum(all_rows)
    for t in theme_ranking[:5]:
        print(f"  テーマ {t['theme']}: 勢い{t['momentum']:.0f} {t['status']} "
              f"(🟢{t['buy_n']}/{t['n']} 前日比{t['avg_change']:+.1f}%)")

    # ⑤ 一押し：勢い上位テーマの中で、過熱でないスコア最上位（🟢優先）
    highlight = None
    for t in theme_ranking:
        cands = [r for r in all_rows
                 if r["theme"] == t["theme"] and r.get("signal") != "hot"]
        if not cands:
            continue
        cands.sort(key=lambda x: (0 if x.get("signal") == "buy" else 1, -x["score"]))
        highlight = {"theme_stat": t, "stock": cands[0]}
        print(f"  ⭐ 一押し: {cands[0]['name']}（{cands[0]['code']}）"
              f"テーマ={t['theme']}")
        break

    # ⑥ 💎仕込み候補：仕込み度の高い順 TOP5（過熱は除外・買い時取得済みのみ）
    shikomi_list = [r for r in all_rows
                    if r.get("signal") not in (None, "hot") and r["shikomi"] >= 45
                    and not r.get("trap_excluded")]
    shikomi_list.sort(key=lambda x: (-x["shikomi"], -x["score"]))
    shikomi_list = shikomi_list[:5]
    for r in shikomi_list:
        print(f"  💎 仕込み{r['shikomi']}点: {r['name']}（{r['code']}）{r['theme']}")

    # ⑦ 銘柄一覧：過熱を除外、💎仕込み度順（同点はスコア順）、上位TOP_N
    table = [r for r in all_rows if r.get("signal") != "hot"]
    table.sort(key=lambda x: (-x.get("shikomi", 0), -x["score"]))
    table = table[:TOP_N]

    # ⑧ 🔮 次週のおすすめテーマ（週間サマリー用）
    weekly_recs = recommend_next_week(theme_ranking, all_rows)

    return table, todays_themes, theme_ranking, highlight, shikomi_list, weekly_recs


def build_holdings_report() -> list[dict]:
    """holdings.json の保有銘柄を分析し、ニュースも付与して返す。"""
    holdings = portfolio.load_holdings()
    if not holdings:
        return []

    rows = portfolio.analyze_portfolio(holdings)
    for r in rows:
        try:
            r["news"] = get_stock_news(r.get("name", ""), r.get("code", ""), max_items=2)
        except Exception:
            r["news"] = []
    return rows


def build_review_report() -> dict | None:
    """📊 1ヶ月前の💎仕込み候補の答え合わせデータを組み立てる。

    history.jsonlから約30日前の推薦を拾い、現在株価と比較して騰落率・勝率を出す。
    ベンチマークとして同期間の日経平均騰落率も添える。
    対象銘柄が無ければNoneを返す（メールではセクションごと非表示）。
    """
    records = history.load_recommendations()
    targets = history.pick_review_targets(records, days_ago=30)
    if not targets:
        return None

    results = []
    for t in targets:
        fin = with_retry(_get_financial_data_uncached, t["code"])
        time.sleep(0.3)
        if not fin or fin.get("current_price") is None:
            continue
        price = t.get("price")
        if not price:
            continue
        now_price = fin["current_price"]
        change_pct = (now_price - price) / price * 100
        results.append({
            "code": t.get("code"),
            "name": t.get("name", ""),
            "theme": t.get("theme", ""),
            "shikomi": t.get("shikomi"),
            "price": price,
            "now_price": now_price,
            "change_pct": change_pct,
            "date": t.get("date"),
        })

    if not results:
        return None

    results.sort(key=lambda x: -x["change_pct"])
    n = len(results)
    avg_change = sum(r["change_pct"] for r in results) / n
    win_rate = sum(1 for r in results if r["change_pct"] > 0) / n * 100
    from_date = min(t.get("date") for t in targets)

    # ── ベンチマーク：同期間の日経平均騰落率 ──
    benchmark = None
    try:
        n225 = yf.Ticker("^N225").history(period="3mo")
        time.sleep(0.3)
        if n225 is not None and len(n225):
            target_date = datetime.strptime(from_date, "%Y-%m-%d").date()
            dates = [ts.date() for ts in n225.index]
            closest_i = min(range(len(dates)), key=lambda i: abs((dates[i] - target_date).days))
            base_close = float(n225["Close"].iloc[closest_i])
            recent_close = float(n225["Close"].iloc[-1])
            benchmark = (recent_close - base_close) / base_close * 100
    except Exception:
        benchmark = None

    return {
        "targets": results,
        "avg_change": avg_change,
        "win_rate": win_rate,
        "n": n,
        "benchmark": benchmark,
        "from_date": from_date,
    }


# ── HTML生成 ──────────────────────────────────────

def _fmt(v, suffix="", nd=1):
    if v is None or v != v:
        return "－"
    return f"{v:.{nd}f}{suffix}"


def _score_color(score):
    return "#16a34a" if score >= 65 else "#dc2626" if score < 40 else "#64748b"


def _chg_color(v):
    if v is None:
        return "#64748b"
    return "#16a34a" if v > 0 else "#dc2626" if v < 0 else "#64748b"


_ACTION_BG = {
    "plunge": "#fee2e2",
    "take_profit": "#fee2e2",
    "trend_warning": "#fef9c3",
    "buy_more": "#dcfce7",
    "hold": "#f1f5f9",
}


def _render_holdings(hrows: list[dict]) -> str:
    """📁 保有銘柄の健康診断セクション（メール最上部）。

    holdings.jsonが空（未登録）のときはセクションごと非表示にする。
    """
    if not hrows:
        return ""

    cards = []
    for r in hrows:
        code = r.get("code", "")
        name = r.get("name", "")
        link = f"https://kabutan.jp/stock/?code={code}"
        price = r.get("current_price")
        pc = r.get("prev_change")

        action = r.get("action")
        bg = _ACTION_BG.get(action, "#f1f5f9")
        label = r.get("label", "－")
        reasons = r.get("reasons", "")

        pl_pct = r.get("pl_pct")
        pl_html = ""
        if pl_pct is not None:
            pl_color = "#16a34a" if pl_pct >= 0 else "#dc2626"
            pl_amount = r.get("pl_amount")
            amount_html = f"（{pl_amount:+,}円）" if pl_amount is not None else ""
            pl_html = (
                f'<div style="font-size:18px;font-weight:bold;color:{pl_color};margin-top:2px;">'
                f'損益 {pl_pct:+.1f}%{amount_html}</div>'
            )

        div_yield = r.get("dividend_yield")
        div_html = ""
        if div_yield is not None:
            div_html = (
                f'<span style="font-size:12px;color:#64748b;margin-left:8px;">'
                f'配当利回り {div_yield:.2f}%</span>'
            )

        # 🎯 目標株価の状態
        target_html = ""
        tstat = r.get("target_status")
        tb, ts = r.get("target_buy"), r.get("target_sell")
        if tstat == "hit_buy":
            target_html = (f'<div style="background:#dcfce7;border:2px solid #16a34a;border-radius:8px;'
                           f'padding:6px 10px;margin-top:6px;font-size:13px;font-weight:bold;color:#166534;">'
                           f'🎯 目標買値 {tb:,.0f}円 に到達！買い増し検討のタイミングです</div>')
        elif tstat == "hit_sell":
            target_html = (f'<div style="background:#fee2e2;border:2px solid #dc2626;border-radius:8px;'
                           f'padding:6px 10px;margin-top:6px;font-size:13px;font-weight:bold;color:#991b1b;">'
                           f'🎯 目標売値 {ts:,.0f}円 に到達！利益確定検討のタイミングです</div>')
        elif tstat == "near_buy" and price:
            pct_left = (float(price) - float(tb)) / float(tb) * 100
            target_html = (f'<div style="font-size:12px;color:#16a34a;margin-top:4px;">'
                           f'🎯 目標買値 {tb:,.0f}円 まであと{pct_left:.1f}%</div>')
        elif tstat == "near_sell" and price:
            pct_left = (float(ts) - float(price)) / float(ts) * 100
            target_html = (f'<div style="font-size:12px;color:#dc2626;margin-top:4px;">'
                           f'🎯 目標売値 {ts:,.0f}円 まであと{pct_left:.1f}%</div>')
        elif tb or ts:
            parts = []
            if tb:
                parts.append(f"買い{tb:,.0f}円")
            if ts:
                parts.append(f"売り{ts:,.0f}円")
            target_html = (f'<div style="font-size:11px;color:#94a3b8;margin-top:4px;">'
                           f'🎯 目標株価：{"／".join(parts)}</div>')

        earnings_html = ""
        if r.get("earnings_soon") and r.get("earnings_date"):
            ed = r["earnings_date"]
            try:
                ed_label = f"{ed.month}月{ed.day}日"
            except Exception:
                ed_label = str(ed)
            earnings_html = (
                f'<span style="display:inline-block;margin-top:6px;padding:2px 8px;'
                f'background:#fef3c7;color:#92400e;border-radius:10px;font-size:11px;">'
                f'⚠️ 決算発表接近（{ed_label}）</span>'
            )

        news_items = r.get("news") or []
        news_html = ""
        if news_items:
            links = "".join(
                f'<div style="margin-top:2px;"><a href="{n["link"]}" '
                f'style="font-size:11px;color:#2563eb;text-decoration:none;">・{n["title"]}</a></div>'
                for n in news_items
            )
            news_html = f'<div style="margin-top:6px;">{links}</div>'

        cards.append(f"""
        <div style="background:{bg};border-radius:10px;padding:10px 14px;margin-bottom:10px;">
          <div style="font-size:15px;font-weight:bold;">
            <a href="{link}" style="color:#2563eb;text-decoration:none;">{name}（{code}）</a>
            <span style="font-size:13px;color:{_chg_color(pc)};margin-left:8px;">{_fmt(price,'円',0) if price is not None else '－'}　前日比{_fmt(pc,'%',2)}</span>
            <span style="font-size:12px;color:{_chg_color(r.get('week_change'))};margin-left:6px;">週間{_fmt(r.get('week_change'),'%',1)}</span>
          </div>
          {pl_html}
          <div style="font-size:13px;margin-top:4px;">{label}　<span style="font-size:12px;color:#64748b;">{reasons}</span></div>
          {target_html}
          <div style="margin-top:2px;">{div_html}</div>
          {earnings_html}
          {news_html}
        </div>""")

    return f"""
      <h2 style="color:#9333ea;margin-top:0;">📁 保有銘柄の健康診断</h2>
      {''.join(cards)}"""


def _render_shikomi(shikomi_list: list[dict]) -> str:
    """💎 未来×割安 仕込み候補（メール最上部の主役セクション）"""
    if not shikomi_list:
        return """
      <div style="background:#faf5ff;border:2px solid #9333ea;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:13px;color:#9333ea;font-weight:bold;">💎 未来×割安の仕込み候補</div>
        <div style="font-size:13px;color:#64748b;margin-top:4px;">
          本日は仕込み度45点以上の銘柄がありませんでした。国策テーマ全体が高値圏の可能性があります。無理に買わず待つのも投資のうち。
        </div>
      </div>"""

    top = shikomi_list[0]
    top_link = f"https://kabutan.jp/stock/?code={top['code']}"
    top_reasons = "／".join(top.get("shikomi_reasons", []))

    others = []
    for r in shikomi_list[1:]:
        link = f"https://kabutan.jp/stock/?code={r['code']}"
        rsn = "／".join(r.get("shikomi_reasons", [])[:2])
        others.append(f"""
        <tr style="border-bottom:1px solid #e9d5ff;">
          <td style="padding:6px 8px;font-weight:bold;color:#9333ea;white-space:nowrap;">💎{r['shikomi']}点</td>
          <td style="padding:6px 8px;"><a href="{link}" style="color:#2563eb;text-decoration:none;font-weight:bold;">{r['name']}</a>
            <span style="font-size:11px;color:#64748b;">{r['code']}・{r.get('theme','')}</span></td>
          <td style="padding:6px 8px;font-size:11px;color:#64748b;">{rsn}</td>
          <td style="padding:6px 8px;text-align:center;font-size:12px;white-space:nowrap;">{r.get('buy_label','')}</td>
        </tr>""")
    others_html = (
        f'<table style="border-collapse:collapse;width:100%;font-size:13px;margin-top:10px;">'
        f'<tbody>{"".join(others)}</tbody></table>'
    ) if others else ""

    return f"""
      <div style="background:#faf5ff;border:2px solid #9333ea;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:13px;color:#9333ea;font-weight:bold;margin-bottom:6px;">
          💎 未来×割安の仕込み候補（国策テーマなのに、まだ安い株）
        </div>
        <div style="font-size:17px;font-weight:bold;">
          <a href="{top_link}" style="color:#2563eb;text-decoration:none;">{top['name']}（{top['code']}）</a>
          <span style="color:#9333ea;">仕込み度 {top['shikomi']}点</span>
          <span style="font-size:12px;color:#64748b;">{top.get('theme','')}／財務スコア{top['score']}点 {top['stars']}</span>
        </div>
        <div style="font-size:12px;color:#7c3aed;margin-top:4px;">{top_reasons}</div>
        <div style="font-size:12px;color:#16a34a;margin-top:2px;">{top.get('buy_label','')}　{top.get('buy_reason','')}</div>
        {others_html}
      </div>"""


def _render_highlight(highlight: dict) -> str:
    """⭐ 今の一押し銘柄ボックス（メール冒頭）"""
    if not highlight:
        return ""
    st = highlight["theme_stat"]
    s  = highlight["stock"]
    link = f"https://kabutan.jp/stock/?code={s['code']}"
    pc = s.get("prev_change")
    return f"""
      <div style="background:#eff6ff;border:2px solid #2563eb;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:13px;color:#2563eb;font-weight:bold;margin-bottom:6px;">
          ⭐ 今の一押し（勢いのあるテーマ × その中の本命）
        </div>
        <div style="font-size:14px;margin-bottom:4px;">
          狙い目テーマ：<b style="color:#ea580c;">{st['theme']}</b>
          <span style="font-size:12px;color:#64748b;">（{st['status']}／🟢{st['buy_n']}社・前日比平均{st['avg_change']:+.1f}%）</span>
        </div>
        <div style="font-size:16px;font-weight:bold;">
          一押し銘柄：<a href="{link}" style="color:#2563eb;text-decoration:none;">{s['name']}（{s['code']}）</a>
          <span style="font-size:13px;color:{_score_color(s['score'])};">{s['score']}点 {s['stars']}</span>
        </div>
        <div style="font-size:12px;color:#16a34a;margin-top:4px;">
          {s.get('buy_label','')}　{s.get('buy_reason','')}　前日比{_fmt(pc,'%',2)}
        </div>
      </div>"""


def _render_theme_ranking(theme_ranking: list[dict]) -> str:
    """🔥 今、狙い目のテーマ TOP3"""
    if not theme_ranking:
        return ""
    rows = []
    for i, t in enumerate(theme_ranking[:3], 1):
        rows.append(f"""
        <tr style="border-bottom:1px solid #fde68a;">
          <td style="padding:6px 8px;font-weight:bold;color:#ea580c;">{i}位</td>
          <td style="padding:6px 8px;font-weight:bold;">{t['theme']}</td>
          <td style="padding:6px 8px;font-size:12px;">{t['status']}</td>
          <td style="padding:6px 8px;text-align:right;font-size:12px;">🟢{t['buy_n']}/{t['n']}社</td>
          <td style="padding:6px 8px;text-align:right;font-size:12px;color:{_chg_color(t['avg_change'])};">前日比{t['avg_change']:+.1f}%</td>
        </tr>""")
    return f"""
      <h3 style="color:#ea580c;margin-bottom:4px;">🔥 今、勢いのある狙い目テーマ TOP3</h3>
      <p style="color:#64748b;font-size:11px;margin-top:0;">買い場の銘柄が多く・過熱しきっていないテーマほど上位（＝これから来る目安）</p>
      <table style="border-collapse:collapse;width:100%;background:#fffbeb;border-radius:8px;font-size:13px;margin-bottom:18px;">
        <tbody>{''.join(rows)}</tbody>
      </table>"""


def _render_weekly(weekly_recs: list[dict]) -> str:
    """🔮 次週の狙い目国策テーマ TOP3（土曜の週間サマリー限定）"""
    if not weekly_recs:
        return ""
    cards = []
    medals = ["🥇", "🥈", "🥉"]
    for i, t in enumerate(weekly_recs):
        best = t.get("best_stock")
        best_html = ""
        if best:
            blink = f"https://kabutan.jp/stock/?code={best['code']}"
            best_html = (
                f'<div style="font-size:12px;margin-top:4px;">代表仕込み銘柄：'
                f'<a href="{blink}" style="color:#2563eb;text-decoration:none;font-weight:bold;">'
                f'{best["name"]}（{best["code"]}）</a>'
                f'<span style="color:#9333ea;"> 💎{best.get("shikomi", 0)}点</span></div>'
            )
        cards.append(f"""
        <div style="background:#ffffff;border:1px solid #c7d2fe;border-radius:10px;padding:10px 14px;margin-bottom:8px;">
          <div style="font-size:15px;font-weight:bold;">
            {medals[i] if i < 3 else ''} {t['theme']}
            <span style="font-size:12px;color:#64748b;margin-left:6px;">おすすめ度 {t['rec_score']:.0f}</span>
            <span style="font-size:12px;color:{_chg_color(t.get('avg_week'))};margin-left:6px;">週間{t.get('avg_week', 0):+.1f}%</span>
          </div>
          <div style="font-size:12px;color:#4338ca;margin-top:2px;">{t['rec_reasons']}</div>
          {best_html}
        </div>""")
    return f"""
      <div style="background:#eef2ff;border:2px solid #4f46e5;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:14px;color:#4f46e5;font-weight:bold;margin-bottom:8px;">
          🔮 次週の狙い目 国策テーマ TOP3（週間サマリー）
        </div>
        <div style="font-size:11px;color:#64748b;margin-bottom:8px;">
          基準：割安・出遅れの余地（平均仕込み度）を主役に、🟢買い場の増え方＝動き始めの兆しを加点、
          急騰済み・過熱テーマは減点。「安いのに静かに動き始めたテーマ」が上位。
        </div>
        {''.join(cards)}
      </div>"""


def _render_action_summary(hrows: list[dict], shikomi_list: list[dict]) -> str:
    """📌 今日やること：メール最上部の要約バッジ。

    保有銘柄の要対応件数と💎新規仕込み候補の件数を、開いた瞬間に
    判断できるようバッジで並べる。保有銘柄側が全て0件なら
    「対応不要」であることを明示し、安心して閉じられるようにする。
    """
    hrows = hrows or []
    shikomi_list = shikomi_list or []

    target_n = sum(1 for r in hrows if r.get("target_status") in ("hit_buy", "hit_sell"))
    plunge_n = sum(1 for r in hrows if r.get("action") == "plunge")
    profit_n = sum(1 for r in hrows if r.get("action") == "take_profit")
    buymore_n = sum(1 for r in hrows if r.get("action") == "buy_more")
    earnings_n = sum(1 for r in hrows if r.get("earnings_soon"))
    shikomi_n = len(shikomi_list)

    items = [
        ("🎯", "目標到達", target_n, "#dcfce7", "#166534"),
        ("📉", "急落", plunge_n, "#fee2e2", "#991b1b"),
        ("🔴", "利益確定検討", profit_n, "#fee2e2", "#991b1b"),
        ("💰", "買い増しチャンス", buymore_n, "#dcfce7", "#166534"),
        ("⚠️", "決算発表が近い", earnings_n, "#fef3c7", "#92400e"),
        ("💎", "新しい仕込み候補", shikomi_n, "#faf5ff", "#7c3aed"),
    ]

    badges = "".join(
        f'<span style="display:inline-block;margin:3px 6px 3px 0;padding:4px 12px;'
        f'background:{bg};border-radius:14px;font-size:13px;font-weight:bold;color:{fg};">'
        f'{icon} {label} {n}件</span>'
        for icon, label, n, bg, fg in items if n
    )

    holdings_empty = (target_n + plunge_n + profit_n + buymore_n + earnings_n) == 0
    empty_note = (
        '<div style="font-size:13px;color:#64748b;margin-top:4px;">'
        '本日：保有銘柄で対応が必要なものはありません</div>'
        if holdings_empty else ""
    )

    return f"""
      <div style="background:#f8fafc;border:2px solid #2563eb;border-radius:12px;padding:12px 16px;margin-bottom:16px;">
        <div style="font-size:13px;color:#2563eb;font-weight:bold;margin-bottom:4px;">📌 今日やること</div>
        <div>{badges}</div>
        {empty_note}
      </div>"""


def _render_review(review: dict | None) -> str:
    """📊 1ヶ月前の仕込み候補の答え合わせセクション（毎月1日 or FORCE_REVIEW限定）。

    reviewがNone（対象銘柄なし）ならセクションごと非表示にする。
    """
    if not review:
        return ""

    avg = review["avg_change"]
    bench = review.get("benchmark")
    n = review["n"]
    win_rate = review["win_rate"]
    win_n = sum(1 for t in review["targets"] if t["change_pct"] > 0)

    if bench is not None:
        diff = avg - bench
        emphasis = "#16a34a" if diff >= 0 else "#dc2626"
        bench_html = f"日経平均 {bench:+.1f}% → 差 <b>{diff:+.1f}%</b>"
    else:
        emphasis = "#16a34a" if avg >= 0 else "#dc2626"
        bench_html = "日経平均 －"

    rows = []
    for t in review["targets"]:
        link = f"https://kabutan.jp/stock/?code={t['code']}"
        cp = t["change_pct"]
        rows.append(f"""
        <tr style="border-bottom:1px solid #bae6fd;">
          <td style="padding:6px 8px;font-size:11px;color:#64748b;white-space:nowrap;">{t.get('date','')}</td>
          <td style="padding:6px 8px;">
            <a href="{link}" style="color:#2563eb;text-decoration:none;font-weight:bold;">{t['name']}</a>
            <span style="font-size:11px;color:#64748b;">{t['code']}</span>
          </td>
          <td style="padding:6px 8px;text-align:center;color:#9333ea;font-weight:bold;white-space:nowrap;">💎{t.get('shikomi', 0)}点</td>
          <td style="padding:6px 8px;text-align:right;font-size:12px;white-space:nowrap;">{t['price']:,.0f}円</td>
          <td style="padding:6px 8px;text-align:right;font-size:12px;white-space:nowrap;">{t['now_price']:,.0f}円</td>
          <td style="padding:6px 8px;text-align:right;font-weight:bold;color:{_chg_color(cp)};white-space:nowrap;">{cp:+.1f}%</td>
        </tr>""")

    return f"""
      <div style="background:#f0f9ff;border:2px solid #0284c7;border-radius:12px;padding:14px 16px;margin-bottom:18px;">
        <div style="font-size:14px;color:#0284c7;font-weight:bold;margin-bottom:6px;">
          📊 1ヶ月前の仕込み候補の答え合わせ
        </div>
        <div style="font-size:15px;color:{emphasis};margin-bottom:2px;">
          平均 <b>{avg:+.1f}%</b>（{bench_html}）
        </div>
        <div style="font-size:13px;color:#334155;margin-bottom:8px;">
          勝率 {win_n}/{n}銘柄（{win_rate:.0f}%）
        </div>
        <table style="border-collapse:collapse;width:100%;font-size:13px;background:#ffffff;border-radius:8px;overflow:hidden;">
          <thead>
            <tr style="background:#e0f2fe;font-size:11px;">
              <th style="padding:6px;">推薦日</th>
              <th style="padding:6px;text-align:left;">銘柄</th>
              <th style="padding:6px;">推薦時💎</th>
              <th style="padding:6px;">推薦時株価</th>
              <th style="padding:6px;">現在株価</th>
              <th style="padding:6px;">騰落率</th>
            </tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        <div style="font-size:11px;color:#64748b;margin-top:8px;">
          ※ この結果は💎仕込み度の精度を検証するためのものです。長期保有では1ヶ月は短い期間なので、参考程度にご覧ください。
        </div>
      </div>"""


def render_html(rows, todays_themes, theme_ranking=None, highlight=None,
                shikomi_list=None, holdings_html="", weekly_recs=None,
                is_weekly=False, action_html="", review_html="") -> str:
    today = now_jst().strftime("%Y/%m/%d (%a)")
    weekly_html = _render_weekly(weekly_recs or []) if is_weekly else ""

    theme_badges = "".join(
        f'<span style="display:inline-block;margin:3px;padding:3px 10px;'
        f'background:#fef3c7;border-radius:12px;font-size:12px;color:#92400e;">{t}</span>'
        for t in todays_themes
    )
    theme_section = f"""
      <h2 style="color:#ea580c;margin-top:0;">🎯 国策テーマ（高市政権の重点分野）</h2>
      <p style="color:#64748b;font-size:12px;margin-top:-8px;">核融合・レアアース・フィジカルAI・宇宙・創薬・半導体・量子 ほか</p>
      <div style="margin-bottom:16px;">{theme_badges}</div>"""

    if not rows:
        return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;max-width:700px;">
          <h2 style="color:#2563eb;">📈 本日の国策銘柄レポート（{today}）</h2>
          {action_html}
          {review_html}
          {holdings_html}
          {weekly_html}
          {theme_section}
          <p style="background:#f1f5f9;padding:12px;border-radius:8px;">
            本日は上記テーマ内に表示できる銘柄がありませんでした（データ取得エラーの可能性）。
          </p>
          <p style="color:#94a3b8;font-size:11px;">※ 自動生成。投資助言ではありません。</p>
        </div>"""

    # 💎と🟢の件数でサブタイトルを組み立て
    buy_count = sum(1 for r in rows if r.get("buy_label", "").startswith("🟢"))
    dia_count = len(shikomi_list or [])
    parts = []
    if dia_count:
        parts.append(f"💎仕込み候補 <b>{dia_count}銘柄</b>")
    if buy_count:
        parts.append(f"🟢買い場 <b>{buy_count}銘柄</b>")
    subtitle = ("・".join(parts) + f"／国策テーマから過熱を除いた {len(rows)}銘柄（仕込み度順）"
                if parts else f"国策テーマから過熱を除いた {len(rows)}銘柄（仕込み度順）")

    trs = []
    for i, r in enumerate(rows, 1):
        link = f"https://kabutan.jp/stock/?code={r['code']}"
        pc   = r.get("prev_change")
        lbl  = r.get("buy_label", "⬜")
        reason = r.get("buy_reason", "")

        # 買い場ラベルの背景色
        if lbl.startswith("🟢"):
            lbl_bg = "#dcfce7"
        else:
            lbl_bg = "#f1f5f9"

        shk = r.get("shikomi", 0)
        shk_color = "#9333ea" if shk >= 60 else "#64748b"

        trap_warnings = r.get("trap_warnings") or []
        warn_html = (
            "<br><span style='font-size:11px;color:#dc2626;'>⚠️ " + "・".join(trap_warnings) + "</span>"
            if trap_warnings else ""
        )

        trs.append(f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:8px 6px;text-align:center;color:#64748b;font-size:12px;">{i}</td>
          <td style="padding:8px 6px;text-align:center;">
            <span style="font-weight:bold;color:{shk_color};">💎{shk}点</span><br>
            <span style="font-size:11px;color:{_score_color(r['score'])};">財務{r['score']}点</span><br>
            <span style="font-size:11px;color:#f59e0b;">{r['stars']}</span>
          </td>
          <td style="padding:6px 8px;text-align:center;background:{lbl_bg};border-radius:6px;font-size:12px;white-space:nowrap;">{lbl}</td>
          <td style="padding:8px 6px;">
            <a href="{link}" style="color:#2563eb;text-decoration:none;font-weight:bold;">{r['name']}</a><br>
            <span style="font-size:11px;color:#64748b;">{r['code']} ／ {r.get('theme','')}</span>
            {"<br><span style='font-size:11px;color:#16a34a;'>" + reason + "</span>" if reason else ""}
            {warn_html}
          </td>
          <td style="padding:8px 6px;text-align:right;color:{_chg_color(pc)};font-weight:bold;">{_fmt(pc,'%',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('per'),'倍')}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('pbr'),'倍',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('equity_ratio'),'%')}</td>
        </tr>""")

    return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;max-width:720px;">
      <h2 style="color:#2563eb;margin-bottom:4px;">📈 {'週間サマリー＆国策銘柄レポート' if is_weekly else '本日の国策銘柄レポート'}（{today}）</h2>
      <p style="color:#64748b;font-size:12px;margin-top:0;">{subtitle}</p>
      {action_html}
      {review_html}
      {holdings_html}
      {weekly_html}
      {_render_shikomi(shikomi_list or [])}
      {_render_highlight(highlight)}
      {_render_theme_ranking(theme_ranking or [])}
      {theme_section}
      <h3 style="color:#16a34a;margin-bottom:6px;">📋 国策銘柄リスト（💎仕込み度順・過熱除外）</h3>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f1f5f9;font-size:12px;">
            <th style="padding:6px;">#</th>
            <th style="padding:6px;">💎仕込み度</th>
            <th style="padding:6px;">買い時</th>
            <th style="padding:6px;text-align:left;">銘柄 ／ テーマ</th>
            <th style="padding:6px;">前日比</th>
            <th style="padding:6px;">PER</th>
            <th style="padding:6px;">PBR</th>
            <th style="padding:6px;">自己資本</th>
          </tr>
        </thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;margin-top:14px;font-size:12px;">
        <b>💎仕込み度とは：</b>「国策テーマなのに、まだ安い株」の度合い（100点満点）。<br>
        割安（PER15倍以下・PBR1倍以下・PSR1倍以下・ネットキャッシュ）＋財務の堅さ＋
        <b>6ヶ月レンジの安値圏にいる（出遅れ）</b>＋増収、で採点。60点以上が有力候補。<br>
        ⚠️マークは減収・減益・営業赤字・債務過多など「安いには理由がある」サイン。
        該当銘柄は💎仕込み候補から自動的に除外しています。<br><br>
        <b>買い時シグナルの見方：</b><br>
        🟢 買い場 ＝ 上昇トレンド（株価＞75日線）＋ 押し目 or ゴールデンクロス or RSI健全<br>
        ⬜ 中立 ＝ 条件は揃っていないが過熱でもない（仕込みは中立のうちが基本）<br>
        🔴 過熱 ＝ このメールでは除外（すでに上がりきった株は仕込み対象外）
      </div>
      <p style="color:#94a3b8;font-size:11px;margin-top:12px;">※ 自動生成。投資助言ではありません。最終判断はご自身で。</p>
    </div>"""


# ── 送信 ──────────────────────────────────────────

def send(html: str, subject: str | None = None):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw   = os.environ.get("GMAIL_APP_PASSWORD")
    to   = os.environ.get("MAIL_TO", addr)

    if not (addr and pw):
        with open("daily_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[DRY RUN] daily_report.html に出力しました。")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or f"📈 本日の国策銘柄レポート {now_jst():%m/%d}"
    msg["From"]    = addr
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(addr, pw)
        srv.sendmail(addr, [a.strip() for a in to.split(",")], msg.as_string())
    print("メール送信完了:", to)


def _write_run_debug_log(is_weekly: bool, rows: list, shikomi_list: list, error: Exception | None = None) -> None:
    """実行結果のサマリーを history/last_run_debug.txt に上書き保存する。

    次回以降「空メール」等の問題が起きたとき、GitHub Actions環境で実際に
    何が起きたか（テーマ収集は成功したか／財務データ取得成功率は何%だったか）
    をこのファイルだけで確認できるようにするための診断ログ。
    history/ ディレクトリは既に別用途（推薦履歴）で存在している可能性があるため
    os.makedirs(exist_ok=True) で安全に作成する。
    """
    try:
        os.makedirs("history", exist_ok=True)

        attempts = _RUN_STATS.get("theme_collect_attempts")
        collect_total = _RUN_STATS.get("theme_collect_total")
        rate = _RUN_STATS.get("fin_success_rate")
        fin_n = _RUN_STATS.get("fin_success_n")
        fin_total = _RUN_STATS.get("fin_total")

        lines = [
            f"実行日時(JST): {now_jst():%Y-%m-%d %H:%M}",
            f"モード: {'週間サマリー' if is_weekly else '日次'}",
        ]
        if attempts is not None:
            lines.append(f"株探テーマ収集: {collect_total}社（{attempts}回目で確定）")
        else:
            lines.append("株探テーマ収集: 記録なし（収集処理まで到達せず）")
        if rate is not None:
            lines.append(f"財務データ取得成功率: {rate:.0f}% ({fin_n}/{fin_total}社)")
        else:
            lines.append("財務データ取得成功率: 記録なし（取得処理まで到達せず）")
        lines.append(f"送信銘柄数: {len(rows) if rows is not None else 0}")
        lines.append(f"💎仕込み候補数: {len(shikomi_list) if shikomi_list is not None else 0}")
        lines.append(f"エラー: {'なし' if error is None else f'{type(error).__name__}: {error}'}")

        with open("history/last_run_debug.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("🩺 実行ログを history/last_run_debug.txt に保存しました。")
    except Exception as log_err:
        # 診断ログの保存失敗でメール送信自体を止めたくないので握りつぶす
        print(f"実行ログの保存に失敗しました: {log_err}")


if __name__ == "__main__":
    # 土曜の朝（JST）は週間サマリーモード（次週のおすすめテーマ付き）
    is_weekly = FORCE_WEEKLY or now_jst().weekday() == 5
    print(f"モード: {'週間サマリー' if is_weekly else '日次'}（JST {now_jst():%Y-%m-%d %H:%M}）")

    holdings_html, target_hit = "", False
    hrows: list = []
    rows, shikomi_list = [], []
    run_error: Exception | None = None

    try:
        try:
            hrows = build_holdings_report()
            print(f"保有銘柄: {len(hrows)} 件")
            holdings_html = _render_holdings(hrows)
            target_hit = any(r.get("target_status") in ("hit_buy", "hit_sell") for r in hrows)
        except Exception as e:
            print(f"保有銘柄レポートの構築に失敗しました（テーマレポートは続行します）: {e}")

        rows, themes, ranking, highlight, shikomi_list, weekly_recs = build_todays_report()
        print(f"送信銘柄: {len(rows)} 件（💎仕込み候補 {len(shikomi_list)} 件）")

        action_html = _render_action_summary(hrows, shikomi_list)

        # 💎仕込み候補を推薦履歴に記録（失敗してもメール送信は続行）
        try:
            recorded_n = history.record_recommendations(shikomi_list)
            print(f"推薦履歴に記録: {recorded_n} 件")
        except Exception as e:
            print(f"推薦履歴の記録に失敗しました（メール送信は続行します）: {e}")

        # 📊 月次レビュー（答え合わせ）：毎月1日の朝 or FORCE_REVIEW=1（失敗してもメール送信は続行）
        show_review = FORCE_REVIEW or now_jst().day == 1
        review_html = ""
        if show_review:
            try:
                review = build_review_report()
                review_html = _render_review(review)
                if review:
                    print(f"月次レビュー: {review['n']}銘柄 平均{review['avg_change']:+.1f}% "
                          f"勝率{review['win_rate']:.0f}%（日経平均{'{:+.1f}%'.format(review['benchmark']) if review['benchmark'] is not None else '－'}）")
                else:
                    print("月次レビュー: 対象銘柄なし（1ヶ月前の推薦記録がありません）")
            except Exception as e:
                print(f"月次レビューの構築に失敗しました（メール送信は続行します）: {e}")
                review_html = ""

        # 件名：🎯目標到達が最優先、次に📊月次レビュー、次に週間サマリー
        if is_weekly:
            subject = f"📈 週間サマリー＆次週の狙い目テーマ {now_jst():%m/%d}"
        else:
            subject = f"📈 本日の国策銘柄レポート {now_jst():%m/%d}"
        if review_html:
            subject = f"📊 月次レビュー｜{subject}"
        if target_hit:
            subject = "🎯目標株価に到達！｜" + subject

        send(render_html(rows, themes, ranking, highlight, shikomi_list,
                         holdings_html, weekly_recs, is_weekly, action_html, review_html), subject)
    except Exception as e:
        run_error = e
        print(f"致命的なエラーが発生しました: {e}")
        raise
    finally:
        # 例外で途中終了しても必ず診断ログを残す（次回の原因究明のため）
        _write_run_debug_log(is_weekly, rows, shikomi_list, error=run_error)
