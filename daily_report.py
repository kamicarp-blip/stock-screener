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
    resolve_themes, get_theme_stocks_by_name, get_all_theme_stocks,
)
from modules.financial_data import get_financial_data, apply_filters, score_stock

# ===== 設定（環境変数で上書き可。空欄なら全テーマ横断）=====
THEME = os.environ.get("REPORT_THEME", "")
ALL_LIMIT = int(os.environ.get("REPORT_ALL_LIMIT", "80"))
TOP_N = int(os.environ.get("REPORT_TOP_N", "10"))
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
    return rows[:TOP_N]


def _fmt(v, suffix="", nd=1):
    if v is None or v != v:  # None or NaN
        return "－"
    return f"{v:.{nd}f}{suffix}"


def _score_color(score):
    return "#16a34a" if score >= 65 else "#dc2626" if score < 40 else "#64748b"


def render_html(rows) -> str:
    today = datetime.now().strftime("%Y/%m/%d")
    target = f"テーマ「{THEME}」" if THEME.strip() else "全テーマ横断"

    if not rows:
        return f"<p>{today} 本日は条件に合う銘柄がありませんでした（{target}）。</p>"

    trs = []
    for i, r in enumerate(rows, 1):
        link = f"https://kabutan.jp/stock/?code={r['code']}"
        trs.append(f"""
        <tr>
          <td style="padding:6px 8px;text-align:center;">{i}</td>
          <td style="padding:6px 8px;text-align:center;font-weight:bold;color:{_score_color(r['score'])};">{r['score']}点</td>
          <td style="padding:6px 8px;text-align:center;color:#f59e0b;">{r['stars']}</td>
          <td style="padding:6px 8px;"><a href="{link}" style="color:#2563eb;text-decoration:none;">{r['name']}</a></td>
          <td style="padding:6px 8px;text-align:center;color:#64748b;">{r['code']}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('per'), '倍')}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('pbr'), '倍', 2)}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('psr'), '倍', 2)}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('equity_ratio'), '%')}</td>
          <td style="padding:6px 8px;text-align:right;">{_fmt(r.get('operating_margin'), '%')}</td>
        </tr>""")

    return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;color:#1e293b;">
      <h2 style="color:#2563eb;">📈 本日の注目銘柄（{today}）</h2>
      <p style="color:#64748b;">{target}／総合スコア上位 {len(rows)} 件</p>
      <table style="border-collapse:collapse;font-size:13px;width:100%;">
        <thead>
          <tr style="background:#f1f5f9;">
            <th style="padding:6px 8px;">順位</th>
            <th style="padding:6px 8px;">スコア</th>
            <th style="padding:6px 8px;">評価</th>
            <th style="padding:6px 8px;text-align:left;">銘柄</th>
            <th style="padding:6px 8px;">コード</th>
            <th style="padding:6px 8px;">PER</th>
            <th style="padding:6px 8px;">PBR</th>
            <th style="padding:6px 8px;">PSR</th>
            <th style="padding:6px 8px;">自己資本</th>
            <th style="padding:6px 8px;">営業利益率</th>
          </tr>
        </thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
      <p style="color:#94a3b8;font-size:11px;margin-top:16px;">
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
    rows = build_rows()
    print(f"対象 {len(rows)} 銘柄")
    send(render_html(rows))
