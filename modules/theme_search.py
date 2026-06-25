import re
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
    "ai": "人工知能",
    "エーアイ": "人工知能",
    "生成ai": "人工知能",
    "ev": "電気自動車",
    "iot": "IoT",
    "dx": "DX",
    "vr": "メタバース",
    "核融合": "核融合発電",
    "量子コンピュータ": "量子コンピューター",
    "宇宙": "宇宙開発関連",
    "太陽光": "ペロブスカイト太陽電池",
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
