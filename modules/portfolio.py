"""保有銘柄（ポートフォリオ）機能：登録銘柄の健康診断を行う。

holdings.json に登録した保有銘柄について、財務データ・行動シグナル・
損益・配当利回り・次回決算日をまとめて取得する。
"""
import json
import time
from datetime import datetime, date

import yfinance as yf

from modules.financial_data import _get_financial_data_uncached
from modules.theme_search import get_kabutan_name
from modules.price_signal import _holding_signal_uncached
from modules.yf_retry import with_retry

EARNINGS_SOON_DAYS = 14


def load_holdings(path: str = "holdings.json") -> list[dict]:
    """holdings.json を読み込み、保有銘柄リストを返す。

    ファイルが無い・壊れている場合は空リストを返す（アプリを壊さない）。
    code は4桁の文字列に正規化する。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        holdings = data.get("holdings", [])
        out = []
        for h in holdings:
            if not isinstance(h, dict) or not h.get("code"):
                continue
            code = str(h["code"]).strip().zfill(4)
            out.append({**h, "code": code})
        return out
    except Exception:
        return []


def _get_dividend_yield(ticker, price) -> float | None:
    """1株あたり年間配当 ÷ 現在値 で配当利回り(%)を計算"""
    try:
        info = ticker.info
        div = info.get("dividendRate")
        if div is None:
            div = info.get("trailingAnnualDividendRate")
        if div is None or not price:
            return None
        return round(float(div) / float(price) * 100, 2)
    except Exception:
        return None


def _get_earnings_date(ticker):
    """次回決算発表日（date型）を取得。取得できなければNone"""
    try:
        cal = ticker.calendar
        if cal is None:
            return None

        raw = None
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date")
        else:
            # 古いyfinanceはDataFrame（index="Earnings Date"の行）を返す
            try:
                if "Earnings Date" in getattr(cal, "index", []):
                    row = cal.loc["Earnings Date"]
                    raw = row.iloc[0] if hasattr(row, "iloc") else row
            except Exception:
                raw = None

        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            return None

        if isinstance(raw, date) and not isinstance(raw, datetime):
            return raw
        if isinstance(raw, datetime):
            return raw.date()
        # 文字列・Timestampなど
        return pd_to_date(raw)
    except Exception:
        return None


def pd_to_date(raw):
    try:
        import pandas as pd
        ts = pd.Timestamp(raw)
        return ts.date()
    except Exception:
        return None


def analyze_holding(h: dict) -> dict | None:
    """1銘柄分の健康診断データを組み立てる。

    h: {"code": "8088", "buy_price": 2000, "shares": 100} などholdings.json の1件
    """
    code = str(h.get("code", "")).strip().zfill(4)
    if not code:
        return None

    # with_retryには必ず_uncached版を渡す。st.cache_data付きの関数を渡すと
    # 1回目の失敗(None)がキャッシュされ、リトライしても再実行されず
    # 同じNoneが即返るだけになる。
    fin = with_retry(_get_financial_data_uncached, code) or {}

    name = h.get("name") or get_kabutan_name(code)

    price = fin.get("current_price")

    ticker = None
    try:
        ticker = yf.Ticker(f"{code}.T")
        if price is None:
            info = ticker.info
            price = info.get("currentPrice") or info.get("regularMarketPrice")
    except Exception:
        pass

    row = {
        **fin,
        "code": code,
        "name": name,
        "current_price": price,
        "buy_price": h.get("buy_price"),
        "shares": h.get("shares"),
    }

    # 行動シグナル
    sig = with_retry(_holding_signal_uncached, code)
    if sig:
        row.update({
            "action": sig["action"],
            "label": sig["label"],
            "reasons": sig["reasons"],
            "prev_change": sig["prev_change"],
            "week_change": sig.get("week_change"),
            "rsi": sig["rsi"],
            "dev25": sig["dev25"],
            "pos6m": sig["pos6m"],
        })
        if price is None:
            price = sig.get("price")
            row["current_price"] = price
    else:
        row["action"] = None
        row["label"] = "－"
        row["reasons"] = ""

    # 🎯目標株価（target_buy=買い増し目標・以下で通知 / target_sell=売却目標・以上で通知）
    tb, ts = h.get("target_buy"), h.get("target_sell")
    row["target_buy"], row["target_sell"] = tb, ts
    row["target_status"] = None
    if price:
        try:
            p = float(price)
            if tb and p <= float(tb):
                row["target_status"] = "hit_buy"       # 目標買値に到達
            elif ts and p >= float(ts):
                row["target_status"] = "hit_sell"      # 目標売値に到達
            elif tb and p <= float(tb) * 1.03:
                row["target_status"] = "near_buy"      # 目標買値まであと3%以内
            elif ts and p >= float(ts) * 0.97:
                row["target_status"] = "near_sell"     # 目標売値まであと3%以内
        except Exception:
            pass

    # 損益
    buy_price = h.get("buy_price")
    if buy_price and price:
        try:
            pl_pct = (float(price) - float(buy_price)) / float(buy_price) * 100
            row["pl_pct"] = round(pl_pct, 2)
            shares = h.get("shares")
            if shares:
                row["pl_amount"] = round((float(price) - float(buy_price)) * float(shares))
            else:
                row["pl_amount"] = None
        except Exception:
            row["pl_pct"] = None
            row["pl_amount"] = None
    else:
        row["pl_pct"] = None
        row["pl_amount"] = None

    # 配当利回り
    row["dividend_yield"] = None
    if ticker is not None and price:
        row["dividend_yield"] = _get_dividend_yield(ticker, price)

    # 次回決算日
    row["earnings_date"] = None
    row["earnings_soon"] = False
    if ticker is not None:
        edate = _get_earnings_date(ticker)
        if edate:
            row["earnings_date"] = edate
            try:
                delta_days = (edate - date.today()).days
                row["earnings_soon"] = 0 <= delta_days <= EARNINGS_SOON_DAYS
            except Exception:
                pass

    return row


def analyze_portfolio(holdings: list[dict]) -> list[dict]:
    """保有銘柄をすべて分析してリストで返す"""
    results = []
    for h in holdings:
        try:
            r = analyze_holding(h)
            if r:
                results.append(r)
        except Exception:
            pass
        time.sleep(0.3)
    return results
