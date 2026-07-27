"""yfinance呼び出し用の共通リトライラッパー。

GitHub Actionsのクラウド出口IPからの大量連続リクエストがYahoo Finance側で
レート制限/ブロックされることがあるため、指数バックオフ＋ジッターで
再試行してから諦める。
"""
import time
import random


def with_retry(fn, *args, max_attempts=3, base_delay=1.5, **kwargs):
    """yfinance呼び出し用の指数バックオフ＋ジッター付きリトライ。

    Noneや例外を「失敗」とみなし、成功（Noneでない返り値）するまで再試行する。
    最終的に失敗したらNoneを返す（呼び出し元は既存のNoneハンドリングをそのまま使える）。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(*args, **kwargs)
            if result is not None:
                return result
        except Exception:
            result = None
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
            time.sleep(delay)
    return None
