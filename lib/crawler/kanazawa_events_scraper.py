# -*- coding: utf-8 -*-
"""
kanazawa_events_scraper.py
----------------------------------------
②金沢市イベント情報：金沢市公式サイトのイベント一覧ページから、
「来月」開催のイベントを抽出・可視化用データに整形するモジュール。

対象ページ: https://www4.city.kanazawa.lg.jp/event.html

【事前確認したページ構造】
このページは「今日/今週/今月/来月」の絞り込みボタンを持つが、
それらはJS/フォーム送信による絞り込みの可能性が高く、単純な
GETパラメータでの再現ができるか不確実だった。
一方で、ページ本体には年間を通した全イベントが以下のような
繰り返しブロックとして列挙されていることを確認済み:

    ### [イベント名](https://www4.city.kanazawa.lg.jp/event/XXXXX.html)
    ![](画像URL)
    - 説明文(ある場合)
    - 【分野】
      イベント
      【開催場所・会場】
      会場名
      【開催日・期間】
      MM月DD日(曜日) から MM月DD日(曜日)  ※範囲の場合
      または
      MM月DD日(曜日)ほか
      令和N年M月D日... (自由記述の補足が続くこともある)

そのため本モジュールは、
  1. イベント個別ページへのリンク(/event/数字.html 等)を手がかりに
     各イベントの祖先ブロックを特定し、
  2. そのブロック内のテキストから【分野】【開催場所・会場】
     【開催日・期間】をラベルベースで正規表現抽出し、
  3. 【開催日・期間】の文字列から開始日(・可能なら終了日)を
     日付にパースして「来月」判定に使う
という設計にしている。

※ 実サイトのCSSクラス名までは確認できていないため、
  本番投入前に実際のページソースを一度ブラウザの「検証」で確認し、
  BeautifulSoupのセレクタ(特に `_find_event_links` 内の href判定と
  `_climb_to_event_block` の階層数)を微調整することを推奨する。
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 10
EVENT_LIST_URL = "https://www4.city.kanazawa.lg.jp/event.html"

# イベント個別ページへのリンクとみなすパス条件(実測したhrefパターンに基づく)
EVENT_LINK_PATTERNS = [
    re.compile(r"/event/\d+\.html"),
    re.compile(r"/soshikikarasagasu/.+/\d+\.html"),
    re.compile(r"/news/\d+\.html"),
]

REIWA_EPOCH_YEAR = 2018  # 令和1年 = 2019年 なので 西暦 = 令和年 + 2018


@dataclass
class KanazawaEvent:
    title: str
    url: str
    image_url: Optional[str]
    description: Optional[str]
    category: Optional[str]      # 【分野】
    venue: Optional[str]         # 【開催場所・会場】
    period_raw: Optional[str]    # 【開催日・期間】の生テキスト
    start_date: Optional[date]   # パースできた開催開始日
    end_date: Optional[date]     # パースできた開催終了日(単発の場合はstartと同じ)


def _is_event_link(href: str) -> bool:
    return any(p.search(href) for p in EVENT_LINK_PATTERNS)


def _extract_label_value(block_text: str, label: str, next_labels: List[str]) -> Optional[str]:
    """
    "【label】\n値...\n【次のラベル】" というテキストブロックから
    label直後から次のラベル(のいずれか)手前までを取り出す。
    """
    start_marker = f"【{label}】"
    idx = block_text.find(start_marker)
    if idx == -1:
        return None
    after = block_text[idx + len(start_marker):]

    end_idx = len(after)
    for nl in next_labels:
        m = re.search(re.escape(f"【{nl}】"), after)
        if m and m.start() < end_idx:
            end_idx = m.start()
    value = after[:end_idx].strip()
    return value or None


def _parse_period(period_raw: str, today: date) -> Tuple[Optional[date], Optional[date]]:
    """
    【開催日・期間】の生テキストから (開始日, 終了日) をベストエフォートで抽出する。

    このページは「今年度の実施予定を通年で列挙したもの」で、開始日が
    今日より前の"開催中の企画展"なども混在する(例:5月開始〜11月終了の
    展示を9月に見ている、というケース)。そのため単純に「今日の月より
    前なら来年」と決め打ちすると、開催中の展示まで来年扱いになってしまう。

    ここでは以下の方針にする:
      1. "令和N年" の明記があれば最優先でそれを年として使う。
      2. 範囲(「から」「〜」「~」を含む)の場合は、まず今年の年で
         開始日・終了日を組み立ててみて、終了日が「今日から大きく
         過去(60日超)」になる場合だけ、開始・終了とも1年後ろに
         ずらす(=年をまたいだ来年度の予定と判断する)。
         これにより「開催中〜未来に終わる展示」は今年のまま扱われる。
      3. 単発日の場合は、その日付が「今日から60日超過去」なら
         来年とみなす。
    """
    if not period_raw:
        return None, None

    def month_day_pairs(text: str):
        return re.findall(r"(\d{1,2})月(\d{1,2})日", text)

    # 令和N年 の明示があれば拾っておく(複数ある場合は最初のものを使う簡易対応)
    reiwa_match = re.search(r"令和(\d{1,2})年", period_raw)
    explicit_reiwa = int(reiwa_match.group(1)) if reiwa_match else None
    explicit_year = explicit_reiwa + REIWA_EPOCH_YEAR if explicit_reiwa is not None else None

    PAST_TOLERANCE_DAYS = 60

    def build_date(month: int, day: int, year: int) -> Optional[date]:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    pairs = month_day_pairs(period_raw)
    if not pairs:
        return None, None

    is_range = "から" in period_raw or "〜" in period_raw or "~" in period_raw

    try:
        m1, d1 = map(int, pairs[0])
    except ValueError:
        return None, None

    base_year = explicit_year if explicit_year is not None else today.year
    start = build_date(m1, d1, base_year)
    if start is None:
        return None, None

    end = start
    if is_range and len(pairs) >= 2:
        try:
            m2, d2 = map(int, pairs[1])
        except ValueError:
            m2, d2 = None, None
        if m2 is not None:
            y2 = base_year
            if (m2, d2) < (m1, d1):
                y2 += 1  # 年またぎ(例: 12月〜翌1月)
            end = build_date(m2, d2, y2) or start

    if explicit_year is None:
        # 終了日(単発の場合は開始日)が「今日より大きく過去」なら、
        # 年またぎの来年度予定と判断してまとめてスライドさせる。
        reference_end = end if is_range else start
        if (today - reference_end).days > PAST_TOLERANCE_DAYS:
            start = date(start.year + 1, start.month, start.day)
            end = date(end.year + 1, end.month, end.day)

    return start, end


def _climb_to_event_block(anchor: Tag, max_levels: int = 6) -> Tag:
    """
    イベントリンク(<a>)から親をたどり、
    【開催日・期間】というラベルを含むテキストブロックが見つかる階層まで登る。
    見つからない場合は max_levels 分登った時点の要素を返す。
    """
    node = anchor
    for _ in range(max_levels):
        if node.parent is None:
            break
        node = node.parent
        if isinstance(node, Tag) and "【開催日・期間】" in node.get_text():
            return node
    return node


def fetch_all_events(list_url: str = EVENT_LIST_URL) -> List[KanazawaEvent]:
    resp = requests.get(list_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # 金沢市サイトはUTF-8で書かれているが、レスポンスヘッダーにcharset指定が
    # 無いためrequestsがISO-8859-1と誤判定し、日本語が文字化けすることがある。
    # 明示的にUTF-8を指定して上書きする。
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    today = datetime.now().date()

    # href(正規化前)をキーにしたdict。同じイベントが「注目のイベント/今日のイベント」の
    # 簡易リストと、「イベントを探す」の詳細リストの両方に重複して登場するため、
    # 単純な「最初に見つけた方を採用」だと詳細情報のない方を拾ってしまう。
    # そのため、【開催日・期間】が取れた方を優先して上書きする方式にする。
    events_by_href: dict = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _is_event_link(href):
            continue

        title = a.get_text(strip=True)
        if not title:
            continue  # 見出し以外(画像だけのリンク等)はスキップ

        block = _climb_to_event_block(a)
        block_text = block.get_text(separator="\n")

        img_tag = block.find("img")
        image_url = img_tag["src"] if img_tag and img_tag.has_attr("src") else None

        category = _extract_label_value(block_text, "分野", ["開催場所・会場", "開催日・期間"])
        venue = _extract_label_value(block_text, "開催場所・会場", ["開催日・期間"])
        period_raw = _extract_label_value(block_text, "開催日・期間", [])

        # 説明文: タイトル行の次に来る短い文(ラベル行より前のテキスト)をベストエフォートで取得
        description = None
        text_lines = [l.strip() for l in block_text.split("\n") if l.strip()]
        for line in text_lines:
            if line == title or line.startswith("【"):
                continue
            if len(line) > 3:
                description = line
                break

        start_date, end_date = _parse_period(period_raw or "", today)

        candidate = KanazawaEvent(
            title=title,
            url=href if href.startswith("http") else f"https://www4.city.kanazawa.lg.jp{href}",
            image_url=image_url,
            description=description,
            category=category,
            venue=venue,
            period_raw=period_raw,
            start_date=start_date,
            end_date=end_date,
        )

        existing = events_by_href.get(href)
        if existing is None:
            events_by_href[href] = candidate
        elif existing.period_raw is None and candidate.period_raw is not None:
            # 既存(簡易リスト由来)が期間情報を持たず、今回(詳細リスト由来)が
            # 持っている場合は、詳細情報付きの方で上書きする。
            events_by_href[href] = candidate
        # それ以外(既に詳細情報を持っている場合)は最初の情報を維持する。

    return list(events_by_href.values())


def filter_next_month(events: List[KanazawaEvent], today: Optional[date] = None) -> List[KanazawaEvent]:
    """
    「来月」開催のイベントだけを抽出する。
    来月の月初〜月末と、イベントの[start_date, end_date]が重なるものを対象にする。
    """
    if today is None:
        today = datetime.now().date()

    if today.month == 12:
        next_month_year, next_month = today.year + 1, 1
    else:
        next_month_year, next_month = today.year, today.month + 1

    range_start = date(next_month_year, next_month, 1)
    if next_month == 12:
        range_end = date(next_month_year + 1, 1, 1)
    else:
        range_end = date(next_month_year, next_month + 1, 1)

    result = []
    for e in events:
        if e.start_date is None:
            continue
        e_start = e.start_date
        e_end = e.end_date or e.start_date
        # 期間が来月と重なっているか
        if e_start < range_end and e_end >= range_start:
            result.append(e)
    return result


def to_records(events: List[KanazawaEvent]) -> List[dict]:
    """Streamlitやpandasで扱いやすいdictのリストに変換"""
    return [
        {
            "イベント名": e.title,
            "日時": e.period_raw,
            "開始日": e.start_date.isoformat() if e.start_date else None,
            "終了日": e.end_date.isoformat() if e.end_date else None,
            "場所・会場": e.venue,
            "分野": e.category,
            "説明": e.description,
            "画像URL": e.image_url,
            "URL": e.url,
        }
        for e in events
    ]


def main():
    logger.info("金沢市イベント一覧を取得中...")
    all_events = fetch_all_events()
    logger.info("取得件数: %d 件", len(all_events))

    next_month_events = filter_next_month(all_events)
    logger.info("来月開催のイベント: %d 件", len(next_month_events))

    for r in to_records(next_month_events):
        print(r)

    return next_month_events


if __name__ == "__main__":
    main()
