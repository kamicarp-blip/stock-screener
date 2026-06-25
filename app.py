import time
import streamlit as st
from modules.theme_search import search_kabutan_themes, get_theme_stocks
from modules.financial_data import get_financial_data, apply_filters
from modules.news_fetcher import get_stock_news

st.set_page_config(
    page_title="株テーマスクリーナー",
    page_icon="📈",
    layout="wide",
)

st.title("📈 株テーマ × 財務スクリーナー")
st.caption("テーマキーワードと財務条件で割安株を絞り込む　|　データ：株探・Yahoo Finance・Google News")

# ──────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────
with st.sidebar:
    st.header("🔍 検索条件")

    theme_input = st.text_input(
        "テーマキーワード",
        placeholder="例：核融合、フィジカルAI、量子コンピュータ、水素",
    )

    st.divider()
    st.subheader("財務フィルター")
    st.caption("チェックを外すと条件なし")

    per_on = st.checkbox("PER（割安度）", value=True)
    per_max = st.number_input("PER 上限（倍）", 1.0, 500.0, 30.0, 1.0, disabled=not per_on)

    pbr_on = st.checkbox("PBR（純資産比）", value=True)
    pbr_max = st.number_input("PBR 上限（倍）", 0.1, 50.0, 2.0, 0.1, disabled=not pbr_on)

    eq_on = st.checkbox("自己資本比率", value=True)
    eq_min = st.number_input("自己資本比率 下限（%）", 0, 100, 30, disabled=not eq_on)

    mc_on = st.checkbox("時価総額", value=True)
    mc_min = st.number_input("時価総額 下限（億円）", 0, 100000, 50, disabled=not mc_on)

    rg_on = st.checkbox("増収率（売上成長）", value=False)
    rg_min = st.number_input("増収率 下限（%）", -100, 1000, 5, disabled=not rg_on)

    eg_on = st.checkbox("増益率（利益成長）", value=False)
    eg_min = st.number_input("増益率 下限（%）", -100, 1000, 5, disabled=not eg_on)

    om_on = st.checkbox("営業利益率", value=False)
    om_min = st.number_input("営業利益率 下限（%）", 0, 100, 10, disabled=not om_on,
                             help="10%以上が成長企業の目安")

    st.divider()
    show_news = st.checkbox("ニュースを表示", value=True)
    run_btn = st.button("🔍 スクリーニング実行", type="primary", use_container_width=True)

# ──────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────
if not run_btn:
    st.info("👈 左サイドバーにテーマを入力して「スクリーニング実行」を押してください")
    with st.expander("💡 使い方"):
        st.markdown(
            """
            1. **テーマキーワード**を入力（日本語で OK）
            2. **財務フィルター**を好みに設定
            3. **スクリーニング実行** を押す
            4. 条件に合う銘柄一覧 ＋ 最新ニュースが表示されます

            | データ | ソース | 費用 |
            |---|---|---|
            | テーマ別銘柄 | 株探 | 無料 |
            | 財務・株価 | Yahoo Finance | 無料 |
            | ニュース | Google News | 無料 |
            """
        )
    st.stop()

if not theme_input.strip():
    st.warning("テーマキーワードを入力してください")
    st.stop()

# Step 1: テーマ検索
with st.spinner(f"「{theme_input}」のテーマを検索中..."):
    themes = search_kabutan_themes(theme_input.strip())

if not themes:
    st.error(f"「{theme_input}」に一致する株探テーマが見つかりませんでした。")
    st.info("別のキーワードを試してください（例：AI、半導体、再生エネルギー、ロボット、宇宙）")
    st.stop()

# 複数テーマがあれば選択させる
if len(themes) == 1:
    selected = themes[0]
    st.success(f"テーマ「{selected['name']}」が見つかりました")
else:
    name = st.selectbox(
        f"{len(themes)} 件のテーマが見つかりました。選んでください：",
        [t["name"] for t in themes],
    )
    selected = next(t for t in themes if t["name"] == name)

# Step 2: 銘柄リスト取得
with st.spinner("テーマ銘柄リストを取得中..."):
    raw_stocks = get_theme_stocks(selected["code"])

if not raw_stocks:
    st.error("テーマ銘柄が取得できませんでした。時間をおいて再試行してください。")
    st.stop()

st.info(f"テーマ「{selected['name']}」: **{len(raw_stocks)} 社** の財務データを取得します（最大40社）")

# Step 3: 財務データ一括取得
progress = st.progress(0, text="財務データ取得中...")
financial_data = []

for i, stock in enumerate(raw_stocks):
    data = get_financial_data(stock["code"])
    if data:
        if not data["name"]:
            data["name"] = stock["name"]
        financial_data.append(data)
    progress.progress((i + 1) / len(raw_stocks), text=f"取得中... {i + 1}/{len(raw_stocks)}")
    time.sleep(0.25)

progress.empty()

# Step 4: フィルタリング
df = apply_filters(
    financial_data,
    per_max=per_max if per_on else None,
    pbr_max=pbr_max if pbr_on else None,
    equity_ratio_min=eq_min if eq_on else None,
    market_cap_min_oku=mc_min if mc_on else None,
    revenue_growth_min=rg_min if rg_on else None,
    earnings_growth_min=eg_min if eg_on else None,
    operating_margin_min=om_min if om_on else None,
)

st.divider()

if df.empty:
    st.warning("条件に合う銘柄が見つかりませんでした。フィルターを緩めてみてください。")
    st.stop()

st.success(f"✅ **{len(df)} 社** が条件に一致しました（{len(raw_stocks)} 社中）")

# ──────────────────────────────────────────
# 結果テーブル
# ──────────────────────────────────────────
cols = ["code", "name", "current_price", "per", "pbr", "equity_ratio",
        "market_cap_oku", "revenue_growth", "earnings_growth", "operating_margin", "sector"]
display_df = df[[c for c in cols if c in df.columns]].copy()
display_df["株探"] = display_df["code"].map(lambda c: f"https://kabutan.jp/stock/?code={c}")

st.dataframe(
    display_df,
    column_config={
        "code": st.column_config.TextColumn("コード", width=80),
        "name": st.column_config.TextColumn("銘柄名"),
        "current_price": st.column_config.NumberColumn("株価（円）", format="%.0f"),
        "per": st.column_config.NumberColumn("PER（倍）", format="%.1f"),
        "pbr": st.column_config.NumberColumn("PBR（倍）", format="%.2f"),
        "equity_ratio": st.column_config.NumberColumn("自己資本比率（%）", format="%.1f"),
        "market_cap_oku": st.column_config.NumberColumn("時価総額（億円）", format="%.0f"),
        "revenue_growth": st.column_config.NumberColumn("増収率（%）", format="%.1f"),
        "earnings_growth": st.column_config.NumberColumn("増益率（%）", format="%.1f"),
        "operating_margin": st.column_config.NumberColumn("営業利益率（%）", format="%.1f"),
        "sector": st.column_config.TextColumn("セクター"),
        "株探": st.column_config.LinkColumn("株探リンク"),
    },
    use_container_width=True,
    hide_index=True,
)

# ──────────────────────────────────────────
# ニュース
# ──────────────────────────────────────────
if show_news:
    st.divider()
    st.subheader("📰 各銘柄の最新ニュース")
    for _, row in df.iterrows():
        with st.expander(f"📌 {row['name']}（{row['code']}）"):
            news = get_stock_news(row["name"], row["code"])
            if news:
                for n in news:
                    st.markdown(f"- [{n['title']}]({n['link']})")
            else:
                st.caption("ニュースが見つかりませんでした")
