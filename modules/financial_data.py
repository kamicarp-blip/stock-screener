import streamlit as st
import pandas as pd
import yfinance as yf


@st.cache_data(ttl=3600, show_spinner=False)
def get_financial_data(code: str) -> dict | None:
    """yfinance で日本株の財務データを取得（コード.T 形式）"""
    try:
        ticker = yf.Ticker(f"{code}.T")
        info = ticker.info
        if not info or not info.get("quoteType"):
            return None

        equity_ratio = _calc_equity_ratio(ticker)
        market_cap = info.get("marketCap") or 0

        rev_growth = info.get("revenueGrowth")
        earn_growth = info.get("earningsGrowth")
        op_margins = info.get("operatingMargins")

        # エミン流指標：PSR と ネットキャッシュ
        psr = info.get("priceToSalesTrailing12Months")
        total_cash = info.get("totalCash") or 0
        total_debt = info.get("totalDebt") or 0
        net_cash = total_cash - total_debt
        # ネットキャッシュ（現金 − 有利子負債）が時価総額を上回る ＝「タダ株」
        net_cash_over_mcap = bool(market_cap > 0 and net_cash >= market_cap)

        return {
            "code": code,
            "name": info.get("longName") or info.get("shortName", ""),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "per": _safe(info.get("trailingPE")),
            "pbr": _safe(info.get("priceToBook")),
            "psr": _safe(psr),
            "equity_ratio": equity_ratio,
            "market_cap_oku": round(market_cap / 1e8, 1) if market_cap else None,
            "net_cash_oku": round(net_cash / 1e8, 1) if net_cash else None,
            "net_cash_over_mcap": net_cash_over_mcap,
            "sector": info.get("sector", ""),
            "revenue_growth": round(rev_growth * 100, 1) if rev_growth is not None else None,
            "earnings_growth": round(earn_growth * 100, 1) if earn_growth is not None else None,
            "operating_margin": round(op_margins * 100, 1) if op_margins is not None else None,
        }
    except Exception:
        return None


def _safe(val) -> float | None:
    """無効値（負・極端に大きい）を除外"""
    if val is None:
        return None
    try:
        v = float(val)
        return v if 0 < v < 10000 else None
    except Exception:
        return None


def _calc_equity_ratio(ticker) -> float | None:
    """貸借対照表から自己資本比率を計算"""
    try:
        bs = ticker.balance_sheet
        if bs is None or bs.empty:
            return None
        col = bs.iloc[:, 0]

        equity_keys = [
            "Stockholders Equity",
            "Total Stockholder Equity",
            "Total Equity Gross Minority Interest",
        ]
        equity = next((col.get(k) for k in equity_keys if col.get(k) is not None), None)
        total_assets = col.get("Total Assets")

        if equity and total_assets and total_assets > 0:
            return round(float(equity) / float(total_assets) * 100, 1)
    except Exception:
        pass
    return None


def apply_filters(
    stocks: list[dict],
    per_max: float | None = None,
    pbr_max: float | None = None,
    equity_ratio_min: float | None = None,
    market_cap_min_oku: float | None = None,
    revenue_growth_min: float | None = None,
    earnings_growth_min: float | None = None,
    operating_margin_min: float | None = None,
    psr_max: float | None = None,
    net_cash_required: bool = False,
) -> pd.DataFrame:
    """財務フィルターを適用して DataFrame を返す"""
    filtered = []
    for s in stocks:
        if s is None:
            continue
        if per_max is not None and (s["per"] is None or s["per"] > per_max):
            continue
        if pbr_max is not None and (s["pbr"] is None or s["pbr"] > pbr_max):
            continue
        if equity_ratio_min is not None and (s["equity_ratio"] is None or s["equity_ratio"] < equity_ratio_min):
            continue
        if market_cap_min_oku is not None and (s["market_cap_oku"] is None or s["market_cap_oku"] < market_cap_min_oku):
            continue
        if revenue_growth_min is not None and (s["revenue_growth"] is None or s["revenue_growth"] < revenue_growth_min):
            continue
        if earnings_growth_min is not None and (s["earnings_growth"] is None or s["earnings_growth"] < earnings_growth_min):
            continue
        if operating_margin_min is not None and (s["operating_margin"] is None or s["operating_margin"] < operating_margin_min):
            continue
        if psr_max is not None and (s.get("psr") is None or s["psr"] > psr_max):
            continue
        if net_cash_required and not s.get("net_cash_over_mcap"):
            continue
        filtered.append(s)

    if not filtered:
        return pd.DataFrame()

    df = pd.DataFrame(filtered)
    df = df.sort_values("per", ascending=True, na_position="last")
    return df.reset_index(drop=True)
