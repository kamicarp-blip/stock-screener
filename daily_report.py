"""毎朝、条件に合うトップ銘柄をメールで送るスクリプト（GitHub Actions で実行）。

ローカルで GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定の場合は
メール送信せず daily_report.html にプレビューを書き出す（DRY RUN）。
"""
import os
import ssl
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from modules.theme_search import (
    resolve_themes, get_theme_stocks_by_name, get_all_theme_stocks, get_trending_themes,
)
from modules.financial_data import get_financial_data, apply_filters, score_stock
from modules.price_signal import buy_timing

# ===== 設定（環境変数で上書き可。空欄なら全テーマ横断）=====
def _int_env(key, default):
    """環境変数を整数で取得。未設定や空文字ならデフォルト"""
    v = os.environ.get(key, "").strip()
    try:
        return int(v) if v else default
    except ValueError:
        return default


THEME = os.environ.get("REPORT_THEME", "").strip()
ALL_LIMIT = _int_env("REPORT_ALL_LIMIT", 80)
TOP_N = _int_env("REPORT_TOP_N", 10)
# 軽い足切り（緩め。スコアで並べ替えるのが主役）
FILTERS = dict(market_cap_min_oku=30)
# =========================================================


def collect_stocks() -> list[dict]:
    if THEME.strip():
        themes = resolve_themes(THEME.strip())
        if not themes:
            return []
        name = max(themes, key=lambda t: t["count"])["name"]
        return get_theme_stocks_by_name(name)
    return get_all_theme_stocks(ALL_LIMIT)


def build_rows() -> list[dict]:
    raw = collect_stocks()
    data = []
    for s in raw:
        d = get_financial_data(s["code"])
        if d:
            d["name"] = s["name"]  # 株探の日本語名を優先
            data.append(d)
        time.sleep(0.2)

    df = apply_filters(data, **FILTERS)
    if df.empty:
        return []

    rows = []
    for _, r in df.iterrows():
        rows.append({**r.to_dict(), **score_stock(r)})
    rows.sort(key=lambda x: x["score"], reverse=True)
    top = rows[:TOP_N]

    # 上位銘柄に「買い時シグナル」を付与
    for r in top:
        bt = buy_timing(r["code"])
        r["buy_label"] = bt["label"] if bt else "－"
        r["buy_reason"] = bt["reasons"] if bt else ""
        r["prev_change"] = bt["prev_change"] if bt else None
    return top


def hot_themes(n: int = 5, stocks_each: int = 4) -> list[dict]:
    """今注目のテーマ（株探ランキング順）とその主要銘柄"""
    out = []
    for t in get_trending_themes(n):
        stocks = get_theme_stocks_by_name(t)[:stocks_each]
        if stocks:
            out.append({"theme": t, "stocks": stocks})
    return out


def _fmt(v, suffix="", nd=1):
    if v is None or v != v:  # None or NaN
        return "－"
    return f"{v:.{nd}f}{suffix}"


def _score_color(score):
    return "#16a34a" if score >= 65 else "#dc2626" if score < 40 else "#64748b"


def _chg_color(v):
    if v is None:
        return "#64748b"
    return "#16a34a" if v > 0 else "#dc2626" if v < 0 else "#64748b"


def render_themes_section(themes) -> str:
    if not themes:
        return ""
    blocks = []
    for i, t in enumerate(themes, 1):
        names = "、".join(
            f'<a href="https://kabutan.jp/stock/?code={s["code"]}" '
            f'style="color:#2563eb;text-decoration:none;">{s["name"]}</a>'
            for s in t["stocks"]
        )
        blocks.append(
            f'<li style="margin-bottom:6px;"><b>{i}. {t["theme"]}</b>'
            f'<br><span style="color:#475569;font-size:12px;">{names}</span></li>'
        )
    return f"""
      <h2 style="color:#ea580c;margin-top:24px;">🔥 今注目のテーマ TOP{len(themes)}</h2>
      <p style="color:#64748b;font-size:12px;">株探アクセスランキング（人気順）</p>
      <ol style="padding-left:20px;">{''.join(blocks)}</ol>"""


def render_html(rows, themes=None) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    target = f"テーマ「{THEME}」" if THEME.strip() else "全テーマ横断"
    theme_html = render_themes_section(themes or [])

    if not rows:
        return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;">
          {theme_html}
          <p style="margin-top:16px;">{today} 本日は条件に合う銘柄がありませんでした（{target}）。</p>
        </div>"""

    n_buy = sum(1 for r in rows if r.get("buy_label", "").startswith("🟢"))

    trs = []
    for i, r in enumerate(rows, 1):
        link = f"https://kabutan.jp/stock/?code={r['code']}"
        pc = r.get("prev_change")
        trs.append(f"""
        <tr>
          <td style="padding:6px 8px;text-align:center;">{i}</td>
          <td style="padding:6px 8px;text-align:center;font-weight:bold;color:{_score_color(r['score'])};">{r['score']}点</td>
          <td style="padding:6px 8px;text-align:center;color:#f59e0b;">{r['stars']}</td>
          <td style="padding:6px 8px;"><a href="{link}" style="color:#2563eb;text-decoration:none;">{r['name']}</a></td>
          <td style="padding:6px 8px;text-align:center;white-space:nowrap;">{r.get('buy_label', '－')}</td>
          <td style="padding:6px 8px;text-align:right;color:{_chg_color(pc)};">{_fmt(pc, '%', 2)}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('per'), '倍')}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('pbr'), '倍', 2)}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('psr'), '倍', 2)}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('equity_ratio'), '%')}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('operating_margin'), '%')}</td>
        </tr>""")

    return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;">
      <h2 style="color:#2563eb;">📈 本日の注目銘柄（{today}）</h2>
      {theme_html}
      <h2 style="color:#2563eb;margin-top:24px;">⭐ 総合スコア上位＋買い時シグナル</h2>
      <p style="color:#64748b;">{target}／スコア上位 {len(rows)} 件　うち <b style="color:#16a34a;">🟢買い場 {n_buy} 件</b></p>
      <table style="border-collapse:collapse;font-size:13px;width:100%;">
        <thead>
          <tr style="background:#f1f5f9;">
            <th style="padding:6px 8px;">順位</th>
            <th style="padding:6px 8px;">スコア</th>
            <th style="padding:6px 8px;">評価</th>
            <th style="padding:6px 8px;text-align:left;">銘柄</th>
            <th style="padding:6px 8px;">買い時</th>
            <th style="padding:6px 8px;">前日比</th>
            <th style="padding:6px 8px;">PER</th>
            <th style="padding:6px 8px;">PBR</th>
            <th style="padding:6px 8px;">PSR</th>
            <th style="padding:6px 8px;">自己資本</th>
            <th style="padding:6px 8px;">営業利益率</th>
          </tr>
        </thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
      <p style="color:#94a3b8;font-size:11px;margin-top:8px;">
        🟢買い場＝上昇トレンドで押し目/ゴールデンクロス　🔴過熱＝買われすぎ（高値づかみ注意）
      </p>
      <p style="color:#94a3b8;font-size:11px;margin-top:12px;">
        ※ 自動生成。投資助言ではありません。最終判断はご自身で。
      </p>
    </div>"""


def send(html: str):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO", addr)

    if not (addr and pw):
        with open("daily_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[DRY RUN] GMAIL_ADDRESS/GMAIL_APP_PASSWORD 未設定。daily_report.html に出力しました。")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 本日の注目銘柄 {datetime.now():%m/%d}"
    msg["From"] = addr
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(addr, pw)
        srv.sendmail(addr, [a.strip() for a in to.split(",")], msg.as_string())
    print("メール送信完了:", to)


if __name__ == "__main__":
    themes = hot_themes()
    rows = build_rows()
    print(f"注目テーマ {len(themes)} 件 / 対象 {len(rows)} 銘柄")
    send(render_html(rows, themes))
