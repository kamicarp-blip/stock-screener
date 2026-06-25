import streamlit as st
import yfinance as yf


def _rsi(close, period: int = 14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


@st.cache_data(ttl=3600, show_spinner=False)
def buy_timing(code: str) -> dict | None:
    """前日終値までの値動きから『買い時』を判定。

    戻り値: {signal, label, reasons, rsi, dev25, prev_change}
    signal: 'buy' / 'neutral' / 'hot'
    """
    try:
        hist = yf.Ticker(f"{code}.T").history(period="6mo")
        if hist is None or len(hist) < 80:
            return None
        close = hist["Close"]
        c = float(close.iloc[-1])
        ma5 = close.rolling(5).mean()
        ma25 = close.rolling(25).mean()
        ma75 = close.rolling(75).mean()
        m5, m25, m75 = float(ma5.iloc[-1]), float(ma25.iloc[-1]), float(ma75.iloc[-1])

        rsi_series = _rsi(close)
        rsi = float(rsi_series.iloc[-1])
        dev25 = (c - m25) / m25 * 100  # 25日線からの乖離率(%)
        prev_change = (c - float(close.iloc[-2])) / float(close.iloc[-2]) * 100

        # 直近5営業日でゴールデンクロス（5日線が25日線を上抜け）したか
        gc = bool(
            (ma5.iloc[-1] > ma25.iloc[-1])
            and (ma5.iloc[-6] <= ma25.iloc[-6])
        )

        uptrend = c > m75
        reasons = []

        # 過熱判定が最優先
        if rsi >= 75 or dev25 >= 15:
            signal, label = "hot", "🔴 過熱"
            if rsi >= 75:
                reasons.append(f"RSI {rsi:.0f}（買われすぎ）")
            if dev25 >= 15:
                reasons.append(f"25日線から+{dev25:.0f}%乖離")
        elif uptrend and (gc or abs(dev25) <= 4 or 30 <= rsi <= 55):
            signal, label = "buy", "🟢 買い場"
            if gc:
                reasons.append("ゴールデンクロス発生")
            if abs(dev25) <= 4:
                reasons.append("25日線まで押し目")
            if 30 <= rsi <= 55:
                reasons.append(f"RSI {rsi:.0f}（過熱せず）")
            reasons.append("上昇トレンド(株価>75日線)")
        else:
            signal, label = "neutral", "⬜ 中立"
            if not uptrend:
                reasons.append("75日線より下（トレンド弱い）")
            else:
                reasons.append(f"RSI {rsi:.0f}")

        return {
            "signal": signal,
            "label": label,
            "reasons": "・".join(reasons),
            "rsi": round(rsi, 1),
            "dev25": round(dev25, 1),
            "prev_change": round(prev_change, 2),
        }
    except Exception:
        return None
