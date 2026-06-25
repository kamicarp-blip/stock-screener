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
st.caption("テーマキーワードと財務条件で銘柄を絞り込む　|　データ：株探・Yahoo Finance・Google News")

# ──────────────────────────────────────────
# フィルター項目の初期値
# ──────────────────────────────────────────
DEFAULTS = {
    "per_on": True, "per_max": 30.0,
    "pbr_on": True, "pbr_max": 2.0,
    "eq_on": True, "eq_min": 30,
    "mc_on": True, "mc_min": 50,
    "rg_on": False, "rg_min": 5,
    "eg_on": False, "eg_min": 5,
    "om_on": False, "om_min": 10,
    "psr_on": False, "psr_max": 1.0,
    "nc_on": False,
    "theme_box": "",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ──────────────────────────────────────────
# プリセット定義（根拠・出典つき）
# ──────────────────────────────────────────
PRESETS = {
    "🔧 カスタム（手動設定）": None,
    "💎 割安株（バリュー）": {
        "per_on": True, "per_max": 15.0,
        "pbr_on": True, "pbr_max": 1.0,
        "eq_on": True, "eq_min": 40,
        "mc_on": True, "mc_min": 100,
        "rg_on": False, "eg_on": False, "om_on": False,
        "psr_on": False, "nc_on": False,
    },
    "🚀 成長株（グロース）": {
        "per_on": False,
        "pbr_on": False,
        "eq_on": True, "eq_min": 30,
        "mc_on": True, "mc_min": 50,
        "rg_on": True, "rg_min": 10,
        "eg_on": True, "eg_min": 10,
        "om_on": True, "om_min": 10,
        "psr_on": False, "nc_on": False,
    },
    "🏦 安定・配当株": {
        "per_on": True, "per_max": 20.0,
        "pbr_on": True, "pbr_max": 1.5,
        "eq_on": True, "eq_min": 50,
        "mc_on": True, "mc_min": 500,
        "rg_on": False, "eg_on": False,
        "om_on": True, "om_min": 10,
        "psr_on": False, "nc_on": False,
    },
    "🇹🇷 エミン流（割安×クオリティ）": {
        "per_on": False,
        "pbr_on": False,
        "eq_on": True, "eq_min": 50,
        "mc_on": True, "mc_min": 30,
        "rg_on": False, "eg_on": False, "om_on": False,
        "psr_on": True, "psr_max": 1.0,
        "nc_on": True,
    },
    "🔮 中島流（メタトレンド・テーマ）": {
        "per_on": False, "pbr_on": False, "eq_on": False,
        "mc_on": False, "rg_on": False, "eg_on": False,
        "om_on": False, "psr_on": False, "nc_on": False,
    },
}

# プリセットごとの根拠・出典
PRESET_NOTES = {
    "💎 割安株（バリュー）": (
        "**根拠：** ベンジャミン・グレアムの古典的バリュー基準。"
        "PER15倍以下・PBR1倍前後を「割安」とし、自己資本比率で財務の健全さも担保する。\n\n"
        "出典：グレアム『賢明なる投資家』の割安株基準（PER×PBR＜22.5 など）"
    ),
    "🚀 成長株（グロース）": (
        "**根拠：** 売上・利益がともに伸び（増収増益）、かつ営業利益率10%以上＝高収益の"
        "優良企業という一般的な成長株の目安。割安度（PER/PBR）はあえて外す。\n\n"
        "出典：営業利益率10%は「優良企業の目安」として多くの投資指南で共通"
    ),
    "🏦 安定・配当株": (
        "**根拠：** 時価総額500億円以上の大型かつ自己資本比率50%以上で財務が堅い企業。"
        "長期保有・配当狙い向き。\n\n"
        "出典：ディフェンシブ投資の一般的な財務健全性基準"
    ),
    "🇹🇷 エミン流（割安×クオリティ）": (
        "**根拠：** エミン・ユルマズ氏が重視する2指標。①**PSR（株価売上高倍率）1倍以下** "
        "＝売上に対して株価が割安、②**ネットキャッシュ＞時価総額**（現金が時価総額より多い"
        "「タダ株」）。安いだけでなく財務の質（高い自己資本比率）も求める。\n\n"
        "出典：東洋経済・会社四季報オンライン「PBR革命の次はPSR革命がやってくる」、"
        "『エミン流 会社四季報の読み方』"
    ),
    "🔮 中島流（メタトレンド・テーマ）": (
        "**根拠：** 中島聡氏の「メタトレンド投資」。"
        "「財務諸表はプロが見てる。自分が見るのは10年後の未来だ」という考えで、"
        "**財務フィルターはあえて全てOFF**にし、今後10年で社会を変える未来テーマに乗る。"
        "有望10〜20社に少額分散（数撃ちゃ当たる）。\n\n"
        "出典：メルマガ「週刊 Life is Beautiful」、中島聡『メタトレンド投資』"
    ),
}

# 中島流：今後10年の未来メタトレンド候補テーマ
MEGATRENDS = [
    "生成AI", "フィジカルAI", "ヒューマノイド", "核融合",
    "量子コンピュータ", "宇宙", "半導体", "水素",
    "全固体電池", "創薬", "サイバーセキュリティ", "防衛",
]


def apply_preset():
    cfg = PRESETS.get(st.session_state["preset_sel"])
    if cfg:
        for k, v in cfg.items():
            st.session_state[k] = v


def pick_theme(keyword: str):
    """中島流のテーマボタン用：テーマをセットして実行をトリガー"""
    st.session_state["theme_box"] = keyword
    st.session_state["trigger_run"] = True


# ──────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────
with st.sidebar:
    st.header("🔍 検索条件")

    st.selectbox(
        "プリセット（投資スタイル）",
        list(PRESETS.keys()),
        key="preset_sel",
        on_change=apply_preset,
    )

    theme_input = st.text_input(
        "テーマキーワード",
        key="theme_box",
        placeholder="例：核融合、フィジカルAI、量子コンピュータ、水素",
    )

    # 中島流のときは未来テーマ候補をボタン表示
    if st.session_state["preset_sel"].startswith("🔮"):
        st.caption("👇 今後10年の未来テーマ候補（押すと検索）")
        cols = st.columns(2)
        for i, t in enumerate(MEGATRENDS):
            cols[i % 2].button(
                t, key=f"mt_{t}", use_container_width=True,
                on_click=pick_theme, args=(t,),
            )

    st.divider()
    st.subheader("財務フィルター")
    st.caption("チェックを外すと条件なし")

    st.checkbox("PER（割安度）", key="per_on")
    st.number_input("PER 上限（倍）", 1.0, 500.0, step=1.0, key="per_max",
                    disabled=not st.session_state["per_on"])

    st.checkbox("PBR（純資産比）", key="pbr_on")
    st.number_input("PBR 上限（倍）", 0.1, 50.0, step=0.1, key="pbr_max",
                    disabled=not st.session_state["pbr_on"])

    st.checkbox("PSR（株価売上高倍率）", key="psr_on")
    st.number_input("PSR 上限（倍）", 0.1, 50.0, step=0.1, key="psr_max",
                    disabled=not st.session_state["psr_on"],
                    help="エミン流：1倍以下が割安の目安")

    st.checkbox("ネットキャッシュ > 時価総額（タダ株）", key="nc_on",
                help="エミン流：現金が時価総額より多い超割安企業")

    st.checkbox("自己資本比率", key="eq_on")
    st.number_input("自己資本比率 下限（%）", 0, 100, key="eq_min",
                    disabled=not st.session_state["eq_on"])

    st.checkbox("時価総額", key="mc_on")
    st.number_input("時価総額 下限（億円）", 0, 100000, key="mc_min",
                    disabled=not st.session_state["mc_on"])

    st.checkbox("増収率（売上成長）", key="rg_on")
    st.number_input("増収率 下限（%）", -100, 1000, key="rg_min",
                    disabled=not st.session_state["rg_on"])

    st.checkbox("増益率（利益成長）", key="eg_on")
    st.number_input("増益率 下限（%）", -100, 1000, key="eg_min",
                    disabled=not st.session_state["eg_on"])

    st.checkbox("営業利益率", key="om_on")
    st.number_input("営業利益率 下限（%）", 0, 100, key="om_min",
                    disabled=not st.session_state["om_on"],
                    help="10%以上が成長企業の目安")

    st.divider()
    show_news = st.checkbox("ニュースを表示", value=True)
    run_btn = st.button("🔍 スクリーニング実行", type="primary", use_container_width=True)

# プリセットの根拠を本文上部に表示
note = PRESET_NOTES.get(st.session_state["preset_sel"])
if note:
    with st.expander(f"📖 {st.session_state['preset_sel']} の根拠・出典", expanded=False):
        st.markdown(note)
        st.caption("※ これは各投資家の公開手法の紹介であり、投資助言ではありません。最終判断はご自身で。")

# テーマボタン経由の実行トリガー
trigger = st.session_state.pop("trigger_run", False)
run = run_btn or trigger

# ──────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────
if not run:
    st.info("👈 左サイドバーでプリセットを選び、テーマを入力して「スクリーニング実行」を押してください")
    with st.expander("💡 使い方"):
        st.markdown(
            """
            1. **プリセット**で投資スタイルを選ぶ（割安株・成長株・エミン流・中島流 など）
            2. **テーマキーワード**を入力（日本語で OK）。中島流ではボタンで未来テーマを選べます
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

theme_input = st.session_state["theme_box"]
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
ss = st.session_state
df = apply_filters(
    financial_data,
    per_max=ss["per_max"] if ss["per_on"] else None,
    pbr_max=ss["pbr_max"] if ss["pbr_on"] else None,
    equity_ratio_min=ss["eq_min"] if ss["eq_on"] else None,
    market_cap_min_oku=ss["mc_min"] if ss["mc_on"] else None,
    revenue_growth_min=ss["rg_min"] if ss["rg_on"] else None,
    earnings_growth_min=ss["eg_min"] if ss["eg_on"] else None,
    operating_margin_min=ss["om_min"] if ss["om_on"] else None,
    psr_max=ss["psr_max"] if ss["psr_on"] else None,
    net_cash_required=ss["nc_on"],
)

st.divider()

if df.empty:
    st.warning("条件に合う銘柄が見つかりませんでした。フィルターを緩めてみてください。")
    st.stop()

st.success(f"✅ **{len(df)} 社** が条件に一致しました（{len(raw_stocks)} 社中）")

# ──────────────────────────────────────────
# 結果テーブル
# ──────────────────────────────────────────
cols = ["code", "name", "current_price", "per", "pbr", "psr", "equity_ratio",
        "market_cap_oku", "net_cash_oku", "revenue_growth", "earnings_growth",
        "operating_margin", "sector"]
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
        "psr": st.column_config.NumberColumn("PSR（倍）", format="%.2f"),
        "equity_ratio": st.column_config.NumberColumn("自己資本比率（%）", format="%.1f"),
        "market_cap_oku": st.column_config.NumberColumn("時価総額（億円）", format="%.0f"),
        "net_cash_oku": st.column_config.NumberColumn("ネットキャッシュ（億円）", format="%.0f"),
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
