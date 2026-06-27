"""毎朝メール：今日の注目テーマ × 今日が買い場の株

流れ：
  1. 株探アクセスランキングから「今日の注目テーマ」を取得（毎日変わる）
  2. 各テーマの銘柄を収集
  3. 買い時シグナル（移動平均・RSI）で🟢買い場の株だけに絞る
  4. 総合スコア順にメール送信

GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定の場合は
daily_report.html に書き出すDRY RUNモード。
"""
import os
import ssl
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from modules.theme_search import get_trending_themes, get_theme_stocks_by_name
from modules.financial_data import get_financial_data, score_stock
from modules.price_signal import buy_timing

# ── 設定 ──────────────────────────────────────────
TOP_THEMES = int(os.environ.get("TOP_THEMES", "6").strip() or "6")   # 使うテーマ数
STOCKS_PER_THEME = int(os.environ.get("STOCKS_PER_THEME", "15").strip() or "15")  # 各テーマの銘柄数
TOP_N = int(os.environ.get("REPORT_TOP_N", "10").strip() or "10")    # メールに載せる上位件数
# ──────────────────────────────────────────────────


def build_todays_report() -> tuple[list[dict], list[str]]:
    """
    今日の注目テーマを取得し、🟢買い場の株だけ返す。
    戻り値: (買い場銘柄リスト, 今日のテーマ名リスト)
    """
    # ① 今日の人気テーマを取得
    todays_themes = get_trending_themes(TOP_THEMES)
    print(f"今日のテーマ: {todays_themes}")

    # ② 各テーマから銘柄を収集（重複除去）
    seen_codes, raw_stocks = set(), []
    for theme in todays_themes:
        for s in get_theme_stocks_by_name(theme)[:STOCKS_PER_THEME]:
            if s["code"] not in seen_codes:
                seen_codes.add(s["code"])
                raw_stocks.append({**s, "theme": theme})

    print(f"銘柄収集: {len(raw_stocks)} 社")

    # ③ 財務データ取得 + 買い時判定 → 🟢のみ抽出
    buy_candidates = []
    for s in raw_stocks:
        # 買い時シグナルを先に判定（🟢でなければスキップ → 財務取得を省略）
        bt = buy_timing(s["code"])
        if not bt or bt["signal"] != "buy":
            time.sleep(0.1)
            continue

        fin = get_financial_data(s["code"])
        if not fin:
            time.sleep(0.1)
            continue

        fin["name"] = s["name"]  # 株探の日本語名を優先
        fin["theme"] = s["theme"]
        row = {**fin, **score_stock(fin),
               "buy_label": bt["label"],
               "buy_reason": bt["reasons"],
               "prev_change": bt["prev_change"],
               "rsi": bt["rsi"],
               "dev25": bt["dev25"]}
        buy_candidates.append(row)
        print(f"  🟢 {s['name']}（{s['code']}）{bt['reasons']}")
        time.sleep(0.2)

    # ④ スコア順に並べてトップN件
    buy_candidates.sort(key=lambda x: x["score"], reverse=True)
    return buy_candidates[:TOP_N], todays_themes


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

    # テーマリスト
    theme_badges = "".join(
        f'<span style="display:inline-block;margin:3px;padding:3px 10px;'
        f'background:#fef3c7;border-radius:12px;font-size:12px;color:#92400e;">'
        f'{t}</span>'
        for t in todays_themes
    )
    theme_section = f"""
      <h2 style="color:#ea580c;margin-top:0;">🔥 今日の注目テーマ</h2>
      <p style="color:#64748b;font-size:12px;margin-top:-8px;">株探アクセスランキング（本日の人気順）</p>
      <div style="margin-bottom:16px;">{theme_badges}</div>"""

    # 銘柄なし
    if not rows:
        return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;
                               color:#1e293b;max-width:700px;">
          <h2 style="color:#2563eb;">📈 本日の買い場レポート（{today}）</h2>
          {theme_section}
          <p style="background:#f1f5f9;padding:12px;border-radius:8px;">
            本日は上記テーマ内に<b>🟢買い場の銘柄がありませんでした</b>。<br>
            <span style="font-size:12px;color:#64748b;">相場が落ち着いている、または過熱ぎみの可能性があります。</span>
          </p>
          <p style="color:#94a3b8;font-size:11px;">※ 自動生成。投資助言ではありません。</p>
        </div>"""

    # 銘柄テーブル
    trs = []
    for i, r in enumerate(rows, 1):
        link = f"https://kabutan.jp/stock/?code={r['code']}"
        pc = r.get("prev_change")
        trs.append(f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:8px 6px;text-align:center;color:#64748b;font-size:12px;">{i}</td>
          <td style="padding:8px 6px;text-align:center;">
            <span style="font-weight:bold;color:{_score_color(r['score'])};">{r['score']}点</span><br>
            <span style="font-size:11px;color:#f59e0b;">{r['stars']}</span>
          </td>
          <td style="padding:8px 6px;">
            <a href="{link}" style="color:#2563eb;text-decoration:none;font-weight:bold;">{r['name']}</a><br>
            <span style="font-size:11px;color:#64748b;">{r['code']} ／ {r.get('theme','')}</span><br>
            <span style="font-size:11px;color:#16a34a;">{r.get('buy_reason','')}</span>
          </td>
          <td style="padding:8px 6px;text-align:right;color:{_chg_color(pc)};font-weight:bold;">{_fmt(pc,'%',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('per'),'倍')}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('pbr'),'倍',2)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:12px;">{_fmt(r.get('equity_ratio'),'%')}</td>
        </tr>""")

    return f"""<div style="font-family:'Hiragino Kaku Gothic Pro','Yu Gothic',sans-serif;
                           color:#1e293b;max-width:700px;">
      <h2 style="color:#2563eb;margin-bottom:4px;">📈 本日の買い場レポート（{today}）</h2>
      <p style="color:#64748b;font-size:12px;margin-top:0;">
        今日の注目テーマ × 🟢買い場シグナルで絞った銘柄／スコア順
      </p>
      {theme_section}
      <h3 style="color:#16a34a;">🟢 本日の買い場候補 {len(rows)} 銘柄</h3>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f1f5f9;font-size:12px;">
            <th style="padding:6px;">#</th>
            <th style="padding:6px;">スコア</th>
            <th style="padding:6px;text-align:left;">銘柄 ／ テーマ ／ 買い場の理由</th>
            <th style="padding:6px;">前日比</th>
            <th style="padding:6px;">PER</th>
            <th style="padding:6px;">PBR</th>
            <th style="padding:6px;">自己資本</th>
          </tr>
        </thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
      <div style="background:#f0fdf4;border-left:3px solid #16a34a;padding:8px 12px;margin-top:12px;font-size:12px;">
        <b>🟢買い場の判断基準：</b>上昇トレンド（株価＞75日線）かつ、
        押し目（25日線付近）またはゴールデンクロスまたはRSI健全（30〜55）
      </div>
      <p style="color:#94a3b8;font-size:11px;margin-top:12px;">
        ※ 自動生成。投資助言ではありません。最終判断はご自身で。
      </p>
    </div>"""


# ── 送信 ──────────────────────────────────────────

def send(html: str):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO", addr)

    if not (addr and pw):
        with open("daily_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("[DRY RUN] daily_report.html に出力しました。")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 本日の買い場レポート {datetime.now():%m/%d}"
    msg["From"] = addr
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(addr, pw)
        srv.sendmail(addr, [a.strip() for a in to.split(",")], msg.as_string())
    print("メール送信完了:", to)


if __name__ == "__main__":
    rows, themes = build_todays_report()
    print(f"買い場銘柄: {len(rows)} 件")
    send(render_html(rows, themes))
