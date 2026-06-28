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


def build_todays_report() -> tuple[list[dict], list[str]]:
    """国策テーマから、過熱していないスコア上位銘柄を返す。"""

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

    # ③ 財務データ取得 + スコア計算
    scored = []
    for s in raw_stocks:
        fin = get_financial_data(s["code"])
        if not fin:
            time.sleep(0.1)
            continue
        fin["name"]  = s["name"]
        fin["theme"] = s["theme"]
        scored.append({**fin, **score_stock(fin)})
        time.sleep(0.15)

    # スコア上位から評価
    scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"財務データ取得: {len(scored)} 社（スコア計算済み）")

    # ④ スコア上位から買い時シグナルを判定（上位 N*4 件まで）
    results = []
    eval_limit = min(len(scored), TOP_N * 4)

    for d in scored[:eval_limit]:
        bt = buy_timing(d["code"])
        if not bt:
            time.sleep(0.1)
            continue

        if bt["signal"] == "hot":
            print(f"  🔴 除外（過熱）: {d['name']}（{d['code']}）")
            time.sleep(0.1)
            continue

        row = {**d,
               "buy_label":   bt["label"],
               "buy_reason":  bt["reasons"],
               "prev_change": bt["prev_change"],
               "rsi":         bt["rsi"],
               "dev25":       bt["dev25"],
               "_sig_order":  0 if bt["signal"] == "buy" else 1}
        results.append(row)
        print(f"  {bt['label']} {d['name']}（{d['code']}）score={d['score']}")
        time.sleep(0.15)

        if len(results) >= TOP_N * 2:
            break

    # ⑤ 🟢を先に、同シグナル内はスコア順
    results.sort(key=lambda x: (x["_sig_order"], -x["score"]))
    return results[:TOP_N], todays_themes


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


def render_html(rows: list[dict], todays_themes: list[str]) -> str:
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
      {theme_section}
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
    rows, themes = build_todays_report()
    print(f"送信銘柄: {len(rows)} 件")
    send(render_html(rows, themes))
