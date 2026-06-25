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


# 色分けと同じ基準（lower=低いほど良い / higher=高いほど良い）
_LOWER_TH = {"per": (15, 25), "pbr": (1.0, 3.0), "psr": (1.0, 4.0)}
_HIGHER_TH = {"equity_ratio": (50, 30), "operating_margin": (10, 5),
              "revenue_growth": (10, 0), "earnings_growth": (10, 0)}


def _point(key, value) -> int | None:
    """1指標を 0(悪) / 1(ふつう) / 2(良) で採点。データ無しは None"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if key in _LOWER_TH:
        good, bad = _LOWER_TH[key]
        return 2 if value <= good else (0 if value > bad else 1)
    if key in _HIGHER_TH:
        good, bad = _HIGHER_TH[key]
        return 2 if value >= good else (0 if value < bad else 1)
    return None


def _cat_score(stock, keys) -> float | None:
    """カテゴリ内の指標を平均して 0〜100 に正規化"""
    pts = [p for p in (_point(k, stock.get(k)) for k in keys) if p is not None]
    if not pts:
        return None
    return sum(pts) / (2 * len(pts)) * 100


def score_stock(stock) -> dict:
    """銘柄を 0〜100 で総合採点。割安・財務・成長の3カテゴリ平均"""
    value = _cat_score(stock, ["per", "pbr", "psr"])           # 割安
    safety = _cat_score(stock, ["equity_ratio"])               # 財務
    growth = _cat_score(stock, ["operating_margin", "revenue_growth", "earnings_growth"])

    # ネットキャッシュ>時価総額なら財務に加点（タダ株ボーナス）
    if safety is not None and stock.get("net_cash_over_mcap"):
        safety = min(100, safety + 25)

    cats = [c for c in (value, safety, growth) if c is not None]
    score = round(sum(cats) / len(cats)) if cats else 0

    # データが乏しい（3カテゴリ中1つ以下）銘柄は信頼できないので上位に来ないよう抑える
    low_data = len(cats) < 2
    if low_data:
        score = min(score, 55)

    stars = 5 if score >= 80 else 4 if score >= 65 else 3 if score >= 50 else 2 if score >= 35 else 1

    return {
        "score": score,
        "stars": "★" * stars + "☆" * (5 - stars),
        "value_score": None if value is None else round(value),
        "safety_score": None if safety is None else round(safety),
        "growth_score": None if growth is None else round(growth),
        "low_data": low_data,
    }
