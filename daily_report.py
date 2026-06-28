"""毎朝メール：高市政権の国策テーマ × スコア上位銘柄（過熱株を除く）

対象テーマ：核融合・レアアース・フィジカルAI・ロボット・宇宙・創薬・
半導体・量子コンピューター など、高市政権の重点17分野ベースの国策テーマ。

ロジック：
  各国策テーマの銘柄を収集 → 財務スコア計算 → 🔴過熱を除外 →
  🟢買い場を上位に、スコア順でメール送信。
"""
import os
import ssl
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Windowsコンソール(cp932)でも絵文字ログが出せるようUTF-8に
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from modules.theme_search import get_trending_themes, get_theme_stocks_by_name
from modules.financial_data import get_financial_data, score_stock
from modules.price_signal import buy_timing

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
# ──────────────────────────────────────────────────


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
        avg_score = sum(r["score"] for r in rs) / n
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
            "avg_change": avg_change, "avg_score": avg_score,
            "momentum": momentum, "status": status,
        })

    stats.sort(key=lambda x: x["momentum"], reverse=True)
    return stats


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

    # ② 各テーマから銘柄を収集（重複除去）
    seen_codes, raw_stocks = set(), []
    for theme in todays_themes:
        for s in get_theme_stocks_by_name(theme)[:STOCKS_PER_THEME]:
            if s["code"] not in seen_codes:
                seen_codes.add(s["code"])
                raw_stocks.append({**s, "theme": theme})
    print(f"銘柄収集: {len(raw_stocks)} 社")

    # ③ 財務データ＋スコア＋買い時シグナルを全銘柄で取得
    all_rows = []
    for s in raw_stocks:
        fin = get_financial_data(s["code"])
        if not fin:
            time.sleep(0.1)
            continue
        fin["name"]  = s["name"]
        fin["theme"] = s["theme"]
        row = {**fin, **score_stock(fin)}

        bt = buy_timing(s["code"])
        if bt:
            row.update({
                "signal":      bt["signal"],
                "buy_label":   bt["label"],
                "buy_reason":  bt["reasons"],
                "prev_change": bt["prev_change"],
                "rsi":         bt["rsi"],
                "dev25":       bt["dev25"],
            })
        else:
            row["signal"] = None
            row["buy_label"] = "－"
        all_rows.append(row)
        time.sleep(0.15)

    print(f"財務＋シグナル取得: {len(all_rows)} 社")

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

    # ⑥ 銘柄一覧：過熱を除外、🟢を先に、スコア順、上位TOP_N
    table = [r for r in all_rows if r.get("signal") != "hot"]
    table.sort(key=lambda x: (0 if x.get("signal") == "buy" else 1, -x["score"]))
    table = table[:TOP_N]

    return table, todays_themes, theme_ranking, highlight


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


def render_html(rows, todays_themes, theme_ranking=None, highlight=None) -> str:
    today = datetime.now().strftime("%Y/%m/%d (%a)")

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
          {theme_section}
          <p style="background:#f1f5f9;padding:12px;border-radius:8px;">
            本日は上記テーマ内に表示できる銘柄がありませんでした（データ取得エラーの可能性）。
          </p>
          <p style="color:#94a3b8;font-size:11px;">※ 自動生成。投資助言ではありません。</p>
        </div>"""

    # 🟢の件数を数えてタイトルを変える
    buy_count = sum(1 for r in rows if r.get("buy_label", "").startswith("🟢"))
    if buy_count > 0:
        subtitle = f"🟢買い場候補 <b>{buy_count}銘柄</b>を含む国策 {len(rows)}銘柄"
    else:
        subtitle = f"国策テーマから過熱を除いたスコア上位 {len(rows)}銘柄"

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

        trs.append(f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:8px 6px;text-align:center;color:#64748b;font-size:12px;">{i}</td>
          <td style="padding:8px 6px;text-align:center;">
            <span style="font-weight:bold;color:{_score_color(r['score'])};">{r['score']}点</span><br>
            <span style="font-size:11px;color:#f59e0b;">{r['stars']}</span>
          </td>
          <td style="padding:6px 8px;text-align:center;background:{lbl_bg};border-radius:6px;font-size:12px;white-space:nowrap;">{lbl}</td>
          <td style="padding:8px 6px;">
            <a href="{link}" style="color:#2563eb;text-decoration:none;font-weight:bold;">{r['name']}</a><br>
            <span style="font-size:11px;color:#64748b;">{r['code']} ／ {r.get('theme','')}</span>
            {"<br><span style='font-size:11px;color:#16a34a;'>" + reason + "</span>" if reason else ""}
          </td>
          <td style="padding:8px 6px;text-align:right;color:{_chg_color(pc)};font-weight:bold;">{_fmt(pc,'%',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('per'),'倍')}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('pbr'),'倍',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('equity_ratio'),'%')}</td>
        </tr>""")

    return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;max-width:720px;">
      <h2 style="color:#2563eb;margin-bottom:4px;">📈 本日の国策銘柄レポート（{today}）</h2>
      <p style="color:#64748b;font-size:12px;margin-top:0;">{subtitle}</p>
      {_render_highlight(highlight)}
      {_render_theme_ranking(theme_ranking or [])}
      {theme_section}
      <h3 style="color:#16a34a;margin-bottom:6px;">📋 国策銘柄リスト（過熱を除くスコア順）</h3>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f1f5f9;font-size:12px;">
            <th style="padding:6px;">#</th>
            <th style="padding:6px;">スコア</th>
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
        <b>買い時シグナルの見方：</b><br>
        🟢 買い場 ＝ 上昇トレンド（株価＞75日線）＋ 押し目 or ゴールデンクロス or RSI健全<br>
        ⬜ 中立 ＝ 条件は揃っていないが過熱でもない（自分で判断）<br>
        🔴 過熱 ＝ このメールでは除外（RSI75超 or 25日線から15%以上上昇）
      </div>
      <p style="color:#94a3b8;font-size:11px;margin-top:12px;">※ 自動生成。投資助言ではありません。最終判断はご自身で。</p>
    </div>"""


# ── 送信 ──────────────────────────────────────────

def send(html: str):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw   = os.environ.get("GMAIL_APP_PASSWORD")
    to   = os.environ.get("MAIL_TO", addr)

    if not (addr and pw):
        with open("daily_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[DRY RUN] daily_report.html に出力しました。")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 本日の国策銘柄レポート {datetime.now():%m/%d}"
    msg["From"]    = addr
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(addr, pw)
        srv.sendmail(addr, [a.strip() for a in to.split(",")], msg.as_string())
    print("メール送信完了:", to)


if __name__ == "__main__":
    rows, themes, ranking, highlight = build_todays_report()
    print(f"送信銘柄: {len(rows)} 件")
    send(render_html(rows, themes, ranking, highlight))
