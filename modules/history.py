"""💎仕込み推薦の記録＆読み出し。

毎朝の💎仕込み候補を history/recommendations.jsonl（JSON Lines）に記録し、
1ヶ月後に「答え合わせ」で読み出すためのモジュール。

1レコード＝1行のJSON：
  {"date": "2026-07-28", "code": "3443", "name": "川田テク", "theme": "フィジカルAI",
   "shikomi": 85, "score": 72, "price": 1234.5, "signal": "buy"}
"""
import json
import os
from datetime import timedelta

# daily_report.py と同じJST定義を使う（循環importを避けるためここでも定義）
from datetime import datetime, timezone
JST = timezone(timedelta(hours=9), "JST")


def _today_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def record_recommendations(shikomi_list: list[dict], path: str = "history/recommendations.jsonl") -> int:
    """💎仕込み候補を1行ずつ追記する。

    同じ日付・同じcodeのレコードが既にあればスキップ（手動再実行での重複防止）。
    price（推薦時株価）がNoneの銘柄は、後で騰落率が計算できないため記録しない。

    戻り値: 実際に書き込んだ件数
    """
    existing = load_recommendations(path)
    seen = {(r.get("date"), r.get("code")) for r in existing}

    today = _today_jst_str()
    new_lines = []
    for r in shikomi_list or []:
        code = r.get("code")
        price = r.get("current_price")
        if price is None:
            continue
        if (today, code) in seen:
            continue
        record = {
            "date": today,
            "code": code,
            "name": r.get("name", ""),
            "theme": r.get("theme", ""),
            "shikomi": r.get("shikomi"),
            "score": r.get("score"),
            "price": price,
            "signal": r.get("signal"),
        }
        new_lines.append(json.dumps(record, ensure_ascii=False))
        seen.add((today, code))

    if not new_lines:
        return 0

    dir_name = os.path.dirname(path)
    if dir_name and not os.path.isdir(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        for line in new_lines:
            f.write(line + "\n")

    return len(new_lines)


def load_recommendations(path: str = "history/recommendations.jsonl") -> list[dict]:
    """記録済みの推薦レコードを全件読み出す。壊れた行はスキップする。"""
    if not os.path.isfile(path):
        return []

    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def pick_review_targets(records: list[dict], days_ago: int = 30, window: int = 10) -> list[dict]:
    """答え合わせ対象を選ぶ：days_ago日前を中心に前後windowの範囲、同一codeは最古の1件のみ。"""
    if not records:
        return []

    center = datetime.now(JST).date() - timedelta(days=days_ago)
    lo = center - timedelta(days=window)
    hi = center + timedelta(days=window)

    in_range = []
    for r in records:
        d = r.get("date")
        if not d:
            continue
        try:
            rd = datetime.strptime(d, "%Y-%m-%d").date()
        except Exception:
            continue
        if lo <= rd <= hi:
            in_range.append((rd, r))

    if not in_range:
        return []

    # 同じcodeは最も古い1件だけ残す
    oldest_by_code: dict[str, tuple] = {}
    for rd, r in in_range:
        code = r.get("code")
        if code not in oldest_by_code or rd < oldest_by_code[code][0]:
            oldest_by_code[code] = (rd, r)

    return [r for _, r in sorted(oldest_by_code.values(), key=lambda x: x[0])]
