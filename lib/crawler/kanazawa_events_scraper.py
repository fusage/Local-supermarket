# -*- coding: utf-8 -*-
"""金沢市公式サイトのイベント一覧から、来月開催のイベントを取り出す。

対象ページ: https://www4.city.kanazawa.lg.jp/event.html
このページには1年分のイベントが、次のようなブロックの繰り返しで並んでいる。

    [イベント名](個別ページへのリンク)
    画像 / 説明文
    【分野】イベント
    【開催場所・会場】会場名
    【開催日・期間】11月3日(月曜日) から 11月24日(月曜日)

処理の流れ:
  1. 個別ページへのリンク(<a>)を探し、親要素をたどって1イベント分のブロックを特定する
  2. ブロックの文字から【分野】【開催場所・会場】【開催日・期間】を取り出す
  3. 【開催日・期間】から開始日・終了日を推定し、来月と重なるものだけ残す

戻り値の dict のキーは、DB(crawl_store.py)の列名とそろえてある。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT_SEC = 10
EVENT_LIST_URL = "https://www4.city.kanazawa.lg.jp/event.html"
SITE_ROOT = "https://www4.city.kanazawa.lg.jp"

# イベント個別ページへのリンクとみなすURL
EVENT_LINK_RE = re.compile(r"/event/\d+\.html|/soshikikarasagasu/.+/\d+\.html|/news/\d+\.html")
LABELS = ["分野", "開催場所・会場", "開催日・期間"]  # ブロック内に出てくる順


# ---------------------------------------------------------------------------
# 文字の読み取り
# ---------------------------------------------------------------------------
def extract_label(block_text: str, label: str) -> Optional[str]:
    """"【label】値【次のラベル】" から値を取り出す。"""
    m = re.search(rf"【{re.escape(label)}】(.*?)(?=【(?:{'|'.join(map(re.escape, LABELS))})】|$)",
                  block_text, re.S)
    return (m.group(1).strip() or None) if m else None


def parse_period(text: Optional[str], today: date) -> Tuple[Optional[date], Optional[date]]:
    """
    【開催日・期間】の文字から (開始日, 終了日) を推定する。年が書かれていないことが多いので、
      - 「令和N年」があれば、その年を使う
      - なければ今年とみなす。ただし終了日が60日以上前になるなら、来年の予定とみなす
        (開催中の長期展示は今年のまま、すでに終わった時期の日付は来年に回す)
    """
    if not text:
        return None, None
    pairs = [(int(m), int(d)) for m, d in re.findall(r"(\d{1,2})月(\d{1,2})日", text)]
    if not pairs:
        return None, None

    reiwa = re.search(r"令和(\d{1,2})年", text)
    year = int(reiwa.group(1)) + 2018 if reiwa else today.year  # 令和1年 = 2019年

    try:
        (m1, d1) = pairs[0]
        start = end = date(year, m1, d1)
        is_range = any(s in text for s in ("から", "〜", "~"))
        if is_range and len(pairs) >= 2:
            m2, d2 = pairs[1]
            end = date(year + 1 if (m2, d2) < (m1, d1) else year, m2, d2)  # 12月〜翌1月
        if not reiwa and (today - end).days > 60:
            start, end = start.replace(year=start.year + 1), end.replace(year=end.year + 1)
    except ValueError:  # 2月30日のような存在しない日付
        return None, None
    return start, end


# ---------------------------------------------------------------------------
# 取得・絞り込み
# ---------------------------------------------------------------------------
def _event_block(a, max_levels: int = 6):
    """リンクから親をたどり、【開催日・期間】を含む要素(=1イベント分のブロック)を返す。"""
    node = a
    for _ in range(max_levels):
        if node.parent is None:
            break
        node = node.parent
        if "【開催日・期間】" in node.get_text():
            break
    return node


def fetch_all_events(list_url: str = EVENT_LIST_URL) -> List[dict]:
    """一覧ページの全イベントを返す: [{title, period_raw, start_date, end_date, venue, ...}, ...]"""
    resp = requests.get(list_url, headers=HEADERS, timeout=TIMEOUT_SEC)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # charset指定が無く文字化けするため
    soup = BeautifulSoup(resp.text, "lxml")
    today = date.today()

    events = {}  # URL → イベント
    for a in soup.find_all("a", href=True):
        href, title = a["href"], a.get_text(strip=True)
        if not EVENT_LINK_RE.search(href) or not title:
            continue

        block = _event_block(a)
        text = block.get_text("\n")
        period_raw = extract_label(text, "開催日・期間")

        # 同じイベントが「注目のイベント」などの簡易リストにも出てくる。
        # 期間が書かれていない簡易版より、詳細版を優先する。
        url = href if href.startswith("http") else SITE_ROOT + href
        if url in events and (events[url]["period_raw"] or not period_raw):
            continue

        lines = [s.strip() for s in text.split("\n") if s.strip()]
        description = next((s for s in lines if s != title and not s.startswith("【") and len(s) > 3), None)
        img = block.find("img")
        start, end = parse_period(period_raw, today)

        events[url] = {
            "title": title,
            "period_raw": period_raw,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "venue": extract_label(text, "開催場所・会場"),
            "category": extract_label(text, "分野"),
            "description": description,
            "image_url": img.get("src") if img is not None else None,
            "url": url,
        }
    return list(events.values())


def filter_next_month(events: List[dict], today: Optional[date] = None) -> List[dict]:
    """開催期間が来月(1日〜末日)と1日でも重なるイベントだけを返す。"""
    today = today or date.today()
    first = date(today.year + today.month // 12, today.month % 12 + 1, 1)          # 来月1日
    after = date(first.year + first.month // 12, first.month % 12 + 1, 1)          # 再来月1日
    first_s, after_s = first.isoformat(), after.isoformat()  # "YYYY-MM-DD" は文字列のまま大小比較できる
    return [
        e for e in events
        if e["start_date"] and e["start_date"] < after_s and (e["end_date"] or e["start_date"]) >= first_s
    ]


def fetch_next_month_events() -> List[dict]:
    return filter_next_month(fetch_all_events())


if __name__ == "__main__":  # 単体での動作確認用: python -m lib.crawler.kanazawa_events_scraper
    logging.basicConfig(level=logging.INFO)
    for e in fetch_next_month_events():
        print(e)
