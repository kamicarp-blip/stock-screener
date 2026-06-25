import re
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


@st.cache_data(ttl=3600, show_spinner=False)
def search_kabutan_themes(keyword: str) -> list[dict]:
    """株探テーマ一覧からキーワードに一致するテーマを検索"""
    url = "https://kabutan.jp/themes/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.content, "html.parser")

        themes = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "theme_code=" not in href or not text:
                continue
            if keyword.lower() not in text.lower():
                continue
            m = re.search(r"theme_code=(\d+)", href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                themes.append({"name": text, "code": m.group(1)})
        return themes
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def get_theme_stocks(theme_code: str) -> list[dict]:
    """テーマページから銘柄コードと銘柄名を取得"""
    url = f"https://kabutan.jp/themes/?theme_code={theme_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.content, "html.parser")

        stocks = []
        seen = set()
        for a in soup.find_all("a", href=True):
            if "/stock/?code=" not in a["href"]:
                continue
            m = re.search(r"code=(\d{4})", a["href"])
            if not m:
                continue
            code = m.group(1)
            if code in seen:
                continue
            seen.add(code)
            name = a.get_text(strip=True)
            if name and not name.isdigit():
                stocks.append({"code": code, "name": name})
            if len(stocks) >= MAX_STOCKS_PER_THEME:
                break
        return stocks
    except Exception:
        return []
