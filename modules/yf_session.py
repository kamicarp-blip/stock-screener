"""yfinanceのyf.Tickerに渡す、ブラウザのTLSフィンガープリントを模倣した
共有セッション。

Yahoo Financeはクラウドデータセンターのアクセス（GitHub Actions等）を
素のrequestsライブラリのフィンガープリントでボット判定しブロックする
ことがある。curl_cffiでChromeを偽装したセッションを使うことで、
これを回避する。
"""
import functools

try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CURL_CFFI = True
except Exception:
    _HAS_CURL_CFFI = False


@functools.lru_cache(maxsize=1)
def get_yf_session():
    """Chrome偽装セッションを1つだけ作って使い回す。

    curl_cffiが使えない環境（インストール失敗等）ではNoneを返し、
    呼び出し側はセッション無し（従来通り）にフォールバックする。
    """
    if not _HAS_CURL_CFFI:
        return None
    try:
        return _cffi_requests.Session(impersonate="chrome")
    except Exception:
        return None
