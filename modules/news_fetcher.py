import urllib.parse
import feedparser
import streamlit as st


@st.cache_data(ttl=1800, show_spinner=False)
def get_stock_news(company_name: str, stock_code: str, max_items: int = 3) -> list[dict]:
    """Google News RSS で銘柄の最新ニュースを取得"""
    query = urllib.parse.quote(f"{company_name} {stock_code}")
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        feed = feedparser.parse(url)
        return [
            {"title": e.get("title", ""), "link": e.get("link", "")}
            for e in feed.entries[:max_items]
        ]
    except Exception:
        return []
