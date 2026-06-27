import re
import difflib
import urllib.parse
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
MAX_STOCKS_PER_THEME = 40

# よく使う英語略称・別名 → 株探の正式テーマ名
SYNONYMS = {
    # AI・ソフト
    "ai": "人工知能", "エーアイ": "人工知能", "生成ai": "人工知能",
    "生成AI": "人工知能", "chatgpt": "人工知能", "llm": "人工知能",
    "機械学習": "人工知能",
    "saas": "SaaS", "クラウドサービス": "SaaS",
    "dx": "DX", "デジタル化": "DX",
    "iot": "IoT", "つながる": "IoT",
    "5g": "5G", "6g": "6G",
    "サイバー": "サイバーセキュリティ", "セキュリティ": "サイバーセキュリティ",
    # 半導体・電子
    "chip": "半導体", "チップ": "半導体",
    "半導体装置": "半導体製造装置",
    "パワー半導体": "パワー半導体",
    "データセ": "データセンター", "クラウド": "データセンター",
    "サーバー": "データセンター",
    # ロボット・自動化
    "ロボティクス": "ロボット", "自動化": "ロボット",
    "physical ai": "フィジカルAI", "物理ai": "フィジカルAI",
    "自動運転": "自動運転", "無人": "ドローン", "ドローン": "ドローン",
    # EV・モビリティ
    "ev": "電気自動車", "電動車": "電気自動車",
    # エネルギー・環境
    "核融合": "核融合発電", "原子力": "核融合発電",
    "量子コンピュータ": "量子コンピューター", "量子": "量子コンピューター",
    "クリーンエネルギー": "再生可能エネルギー", "再エネ": "再生可能エネルギー",
    "太陽光": "ペロブスカイト太陽電池", "ソーラー": "ペロブスカイト太陽電池",
    "風力": "洋上風力",
    "電池": "全固体電池", "バッテリー": "全固体電池", "蓄電": "全固体電池",
    "水素発電": "水素", "fc": "燃料電池",
    "カーボン": "GX", "脱炭素": "GX", "カーボンニュートラル": "GX",
    # バイオ・医療
    "バイオテック": "バイオ", "創薬": "創薬", "新薬": "創薬",
    "遺伝子": "ゲノム編集", "再生医療": "再生医療", "認知症": "認知症",
    # 宇宙・防衛
    "宇宙": "宇宙開発関連", "ロケット": "宇宙開発関連",
    "国防": "防衛", "軍事": "防衛", "安全保障": "防衛",
    # 金融・暗号
    "フィンテック": "ブロックチェーン",
    "仮想通貨": "暗号資産", "暗号通貨": "暗号資産", "ビットコイン": "暗号資産",
    # 素材
    "レアメタル": "レアアース", "希少金属": "レアアース",
    # 観光・消費
    "観光": "インバウンド", "訪日": "インバウンド",
    "vr": "メタバース", "ar": "メタバース", "xr": "メタバース",
}

# 株探に存在しそうな主要テーマ名（候補。実際に叩いて銘柄が返るものだけ採用される）
CURATED_THEMES = [
    "核融合発電", "フィジカルAI", "人工知能", "量子コンピューター",
    "半導体", "半導体製造装置", "半導体部材・部品", "パワー半導体",
    "データセンター", "サーバー冷却", "宇宙開発関連", "防衛", "ロボット",
    "ドローン", "蓄電池", "全固体電池", "ペロブスカイト太陽電池", "水素",
    "燃料電池", "アンモニア", "洋上風力", "再生可能エネルギー",
    "サイバーセキュリティ", "ステーブルコイン", "暗号資産", "ブロックチェーン",
    "メタバース", "自動運転", "電気自動車", "創薬", "バイオ", "ゲノム編集",
    "再生医療", "認知症", "レアアース", "5G", "6G", "IoT", "DX", "SaaS",
    "インバウンド", "防災", "GX", "海運", "銀行", "地方銀行",
]


def _theme_url(name: str) -> str:
    return "https://kabutan.jp/themes/?theme=" + urllib.parse.quote(name)


def _norm(s: str) -> str:
    """長音符・中黒・空白を除いて小文字化（表記ゆれ吸収）"""
    return (
        s.lower()
        .replace("ー", "").replace("・", "")
        .replace(" ", "").replace("　", "")
    )


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_popular_themes() -> list[str]:
    """株探アクセスランキングから人気テーマ名を取得（24時間キャッシュ）"""
    out = set()
    for period in (1, 2, 3):
        try:
            r = requests.get(
                f"https://kabutan.jp/info/accessranking/{period}_2",
                headers=HEADERS, timeout=15,
            )
            r.encoding = "utf-8"
            out.update(
                urllib.parse.unquote(t)
                for t in re.findall(r'/themes/\?theme=([^"&\']+)', r.text)
            )
        except Exception:
            pass
    return sorted(out)


def _theme_pool() -> list[str]:
    """照合用テーマ名プール（人気 + 主要）"""
    pool = list(dict.fromkeys(_fetch_popular_themes() + CURATED_THEMES))
    return pool


def _candidates(keyword: str) -> list[str]:
    """入力語から、株探で試すテーマ名候補を生成"""
    kw = keyword.strip()
    cands = [kw]

    syn = SYNONYMS.get(kw.lower())
    if syn:
        cands.append(syn)

    # 人気・主要テーマとの表記ゆれ照合（例：核融合→核融合発電）
    nkw = _norm(kw)
    if nkw:
        for t in _theme_pool():
            if nkw in _norm(t):
                cands.append(t)

    # 接尾辞バリエーション
    for suf in ("関連", "発電", "技術", "開発関連", "関連株"):
        cands.append(kw + suf)
    cands.append(kw + "ー")
    cands.append(kw.rstrip("ー"))

    # ファジーマッチ（表記ゆれ・ニュアンス：SequenceMatcher で近いテーマ名を補完）
    pool = _theme_pool()
    npool = {}
    for t in pool:
        nt = _norm(t)
        if nt not in npool:
            npool[nt] = t
    if nkw:
        for cn in difflib.get_close_matches(nkw, npool.keys(), n=3, cutoff=0.55):
            cands.append(npool[cn])

    # 重複除去（順序維持）
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:10]


@st.cache_data(ttl=3600, show_spinner=False)
def get_theme_stocks_by_name(name: str) -> list[dict]:
    """テーマ名から銘柄リスト（コード・銘柄名）を取得"""
    try:
        r = requests.get(_theme_url(name), headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")

        stocks, seen = [], set()
        for tr in soup.find_all("tr"):
            a = tr.find("a", href=lambda x: x and "/stock/?code=" in x)
            if not a:
                continue
            m = re.search(r"code=(\d{4})", a["href"])
            if not m:
                continue
            code = m.group(1)
            # 0xxx は指数・為替。実際の個別銘柄は 1300 以降
            if code < "1300" or code in seen:
                continue
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            nm = cells[1].get_text(strip=True)
            if not nm or nm.isdigit():
                continue
            seen.add(code)
            stocks.append({"code": code, "name": nm})
            if len(stocks) >= MAX_STOCKS_PER_THEME:
                break
        return stocks
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_themes(keyword: str) -> list[dict]:
    """入力語に対応する『実際に銘柄が返る』株探テーマ名を解決して返す"""
    valid, seen = [], set()
    for name in _candidates(keyword):
        if name in seen:
            continue
        stocks = get_theme_stocks_by_name(name)
        if stocks:
            seen.add(name)
            valid.append({"name": name, "count": len(stocks)})
    return valid


def suggest_themes(limit: int = 15) -> list[str]:
    """候補が見つからないとき用：人気テーマ名のサンプル"""
    pool = _fetch_popular_themes() or CURATED_THEMES
    return pool[:limit]


@st.cache_data(ttl=86400, show_spinner=False)
def get_trending_themes(limit: int = 8) -> list[str]:
    """株探アクセスランキング順（人気順）でテーマ名を返す ＝『今注目のテーマ』"""
    try:
        r = requests.get(
            "https://kabutan.jp/info/accessranking/3_2", headers=HEADERS, timeout=15
        )
        r.encoding = "utf-8"
        names = [
            urllib.parse.unquote(t)
            for t in re.findall(r'/themes/\?theme=([^"&\']+)', r.text)
        ]
        seen, out = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out[:limit]
    except Exception:
        return CURATED_THEMES[:limit]


@st.cache_data(ttl=3600, show_spinner=False)
def get_all_theme_stocks(limit: int = 150) -> list[dict]:
    """全テーマ（人気＋主要）を横断して銘柄を集約。コード重複は除外、最大 limit 件"""
    seen, out = set(), []
    for theme in _theme_pool():
        for s in get_theme_stocks_by_name(theme):
            if s["code"] in seen:
                continue
            seen.add(s["code"])
            out.append({**s, "theme": theme})
            if len(out) >= limit:
                return out
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def get_kabutan_name(code: str) -> str:
    """株探の銘柄ページから日本語社名を取得"""
    try:
        r = requests.get(
            f"https://kabutan.jp/stock/?code={code}", headers=HEADERS, timeout=10
        )
        if r.status_code != 200:
            return code
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.find("title")
        if title:
            t = title.get_text(strip=True)
            for sep in ("【", "（", "の株価", " 株価"):
                if sep in t:
                    return t.split(sep)[0].strip()
        return code
    except Exception:
        return code


@st.cache_data(ttl=300, show_spinner=False)
def search_stocks(query: str) -> list[dict]:
    """銘柄コード（4桁）または会社名で検索。Yahoo Finance API + 株探で日本語名を補完"""
    query = query.strip()
    if not query:
        return []

    # 4桁コード直接指定
    if re.match(r"^\d{4}$", query):
        name = get_kabutan_name(query)
        return [{"code": query, "name": name}]

    # Yahoo Finance 検索 API
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "lang": "ja", "region": "JP",
                    "quotesCount": 10, "newsCount": 0},
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        results = []
        for q in r.json().get("quotes", []):
            sym = q.get("symbol", "")
            if not sym.endswith(".T"):
                continue
            code = sym[:-2]
            if code < "1300":
                continue
            # 株探から日本語名を取得
            name = get_kabutan_name(code)
            if name == code:
                name = q.get("shortname") or q.get("longname") or code
            results.append({"code": code, "name": name})
        return results[:8]
    except Exception:
        return []
