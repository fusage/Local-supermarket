# -*- coding: utf-8 -*-
"""スギ薬局 金沢駅西店のチラシ・特売情報を「トクバイ」から取得する。

スギ薬局の店舗ページには、チラシ配信サービス「トクバイ」のウィジェットが埋め込まれている。
取得の流れ:
  1. スギ薬局の店舗ページ   → 埋め込みURLから、トクバイ側の店舗IDを見つける
  2. トクバイのウィジェット → チラシ一覧(タイトル・掲載期間・詳細ページへのリンク)
  3. チラシの印刷用ページ   → チラシ画像のURL
  4. トクバイの店舗ページ   → 「今週のおすすめ/イチオシ」商品(今日・明日・明後日の3日分)
                              商品名・内容量・価格を文字データのまま取れる

戻り値の dict のキーは、DB(crawl_store.py)の列名とそろえてある。
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import date, timedelta
from typing import List, Optional, Tuple
from urllib.parse import urljoin

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
INTERVAL_SEC = 1.5  # 相手サイトに負担をかけないよう、アクセスごとに待つ秒数

WIDGET_URL = "https://widgets.tokubai.co.jp/{store_id}/leaflet_widget"
PRINT_URL = "https://tokubai.co.jp/{store_id}/leaflets/{leaflet_id}/print"

# 例: "2026年9月22日〜28日まで" / "2026年9月29日〜10月5日まで"
PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[〜～~-]\s*(?:(\d{1,2})月)?(\d{1,2})日")
# 例: "898円987円(税込)" / "498 円 547円(税込)"(本体価格+税込価格) / "987円(税込)"(税込価格のみ)
PRICE_RE = re.compile(r"(?:(?P<base>\d[\d,]*)\s*円\s*)?(?P<tax>\d[\d,]*)\s*円\s*\(税込\)")


# ---------------------------------------------------------------------------
# 共通の小さな関数
# ---------------------------------------------------------------------------
def _get(url: str, params: Optional[dict] = None) -> requests.Response:
    """少し待ってからGETし、UTF-8として読む(charset未指定のサイトで文字化けするため)。"""
    time.sleep(INTERVAL_SEC)
    resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT_SEC)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "lxml")


def _nfkc(text: str) -> str:
    """全角英数を半角にそろえ、連続する空白を1つにまとめる。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _yen(text: Optional[str]) -> Optional[int]:
    return int(text.replace(",", "")) if text else None


def parse_period(text: str) -> Tuple[Optional[str], Optional[str]]:
    """掲載期間の文字列から (開始日, 終了日) を "YYYY-MM-DD" で返す。読めなければ (None, None)。"""
    m = PERIOD_RE.search(text)
    if not m:
        return None, None
    y, m1, d1, m2, d2 = m.groups()
    y, m1, d1, d2 = int(y), int(m1), int(d1), int(d2)
    m2 = int(m2) if m2 else m1                     # 終了側に月が無ければ同じ月
    y2 = y + 1 if (m2, d2) < (m1, d1) else y       # 12月28日〜1月3日 のような年またぎ
    return f"{y}-{m1:02d}-{d1:02d}", f"{y2}-{m2:02d}-{d2:02d}"


def short_title(full_text: str) -> str:
    """"〇〇店のチラシ・特売情報 チラシのご案内 2026年9月29日〜…" から "チラシのご案内" を取り出す。"""
    t = re.sub(r".*?チラシ・特売情報\s*", "", full_text)
    t = re.sub(r"\s*\d{4}年\d{1,2}月\d{1,2}日.*$", "", t)
    return t.strip() or full_text


def parse_featured_anchor(a, page_url: str) -> Optional[dict]:
    """
    「今週のおすすめ」1商品分のリンク(<a>)から商品情報を取り出す。

    リンク内の文字はおおむね「商品名 内容量 本体価格 税込価格」の順に並ぶ。
    表示上の商品名は「…」で省略されることがあるため、完全な商品名は画像のaltから取る。
    内容量は「価格より前の文字」から「商品名と共通する先頭部分」を除いた残りとする。
    """
    lines = [_nfkc(s) for s in a.get_text("\n", strip=True).split("\n")]
    lines = [s for s in lines if s and not re.fullmatch(r"イチオシ\s*!?", s)]  # バッジは除く
    text = " ".join(lines)

    price = PRICE_RE.search(text)
    if not price:
        return None
    before_price = text[: price.start()].strip()

    img = a.find("img")
    alt = _nfkc(img.get("alt") or "") if img is not None else ""
    name = re.sub(r"\s*[\d,]+円\s*\(税込\).*$", "", alt).strip() or (lines[0] if lines else "")
    if not name:
        return None

    common = os.path.commonprefix([before_price, name])
    spec = re.sub(r"^(?:\.{2,}|…|\s)+", "", before_price[len(common):]).strip() or None
    if spec == name:
        spec = None

    # 画像は遅延読み込みのため、src ではなく data-src 等に本当のURLが入っていることがある
    img_src = None
    if img is not None:
        img_src = next((img.get(k) for k in ("data-src", "data-original", "src")
                        if img.get(k) and not img.get(k).startswith("data:")), None)

    return {
        "product_name": name,
        "spec": spec,
        "base_price": _yen(price.group("base")),
        "tax_price": _yen(price.group("tax")),
        "is_featured": "イチオシ" in a.get_text(),
        "image_url": urljoin(page_url, img_src) if img_src else None,
        "product_url": urljoin(page_url, a.get("href") or ""),
    }


# ---------------------------------------------------------------------------
# 取得の本体
# ---------------------------------------------------------------------------
class TokubaiFlyerScraper:
    """
    使い方:
        scraper = TokubaiFlyerScraper.from_store_page("https://www.sugi-net.jp/stores/001612")
        flyers = scraper.fetch_flyers(count=6)
        items = scraper.fetch_featured_items(flyers[0]["detail_url"])
    """

    def __init__(self, store_id: str):
        self.store_id = store_id  # トクバイ側の店舗ID

    @classmethod
    def from_store_page(cls, store_page_url: str) -> "TokubaiFlyerScraper":
        """スギ薬局の店舗ページに埋め込まれたウィジェットURLから、トクバイの店舗IDを見つける。"""
        resp = _get(store_page_url)
        m = re.search(r"widgets\.tokubai\.co\.jp/(\d+)/leaflet_widget", resp.text)
        if not m:
            raise ValueError(f"{store_page_url} にトクバイのウィジェットが見つかりません(サイト構造が変わった可能性)")
        return cls(m.group(1))

    def fetch_flyers(self, count: int = 6) -> List[dict]:
        """チラシ一覧を返す: [{title, period_start, period_end, image_url, detail_url, leaflet_id}, ...]"""
        resp = _get(WIDGET_URL.format(store_id=self.store_id), {"count": count, "type": "spweb"})

        flyers, seen_ids = [], set()
        for a in _soup(resp).find_all("a", href=True):
            href = a["href"]
            if "leaflet_widget/click" not in href:
                continue
            m = re.search(r"[?&]id=(\d+)", href)
            leaflet_id = m.group(1) if m else None
            if leaflet_id in seen_ids:
                continue
            seen_ids.add(leaflet_id)

            full_text = a.get_text(strip=True)
            period_start, period_end = parse_period(full_text)

            # 画像は印刷用ページの原寸画像を使う。取れなければ一覧の小さいサムネイルで代用する。
            image_url = self._leaflet_image_url(leaflet_id) if leaflet_id else None
            if image_url is None:
                thumb = a.find("img")
                image_url = thumb.get("src") if thumb is not None else None

            flyers.append({
                "title": short_title(full_text),
                "period_start": period_start,
                "period_end": period_end,
                "image_url": image_url,
                "detail_url": urljoin(resp.url, href),
                "leaflet_id": leaflet_id,
            })
        return flyers

    def _leaflet_image_url(self, leaflet_id: str) -> Optional[str]:
        """印刷用ページ(チラシ画像が1枚だけ載っている)から画像URLを取る。失敗したら None。"""
        try:
            resp = _get(PRINT_URL.format(store_id=self.store_id, leaflet_id=leaflet_id))
        except requests.RequestException as e:
            logger.warning("チラシ画像の取得に失敗 (id=%s): %s", leaflet_id, e)
            return None
        for img in _soup(resp).find_all("img"):
            src = urljoin(resp.url, img.get("src") or img.get("data-src") or "")
            if "image.tokubai.co.jp" in src and "leaflet" in src:
                return src
        return None

    def fetch_featured_items(self, detail_url: str) -> List[dict]:
        """
        トクバイの店舗ページ(今日・明日・明後日)から「今週のおすすめ/イチオシ」を取得する。
        detail_url はチラシ詳細へのリンク。リダイレクト先のURLから店舗ページのURLを割り出す。
        戻り値: [{sale_date, product_name, spec, base_price, tax_price, is_featured, image_url, product_url}, ...]
        """
        final_url = _get(detail_url).url  # 例: https://tokubai.co.jp/<店名>/<店舗ID>/leaflets/<チラシID>/...
        m = re.match(r"(https://tokubai\.co\.jp/.*?\d+)/leaflets/", final_url)
        if not m:
            raise ValueError(f"店舗ページのURLを特定できませんでした: {final_url}")
        shop_url = m.group(1)

        items, seen = [], set()
        for offset, view in enumerate((None, "tomorrow", "day_after_tomorrow")):
            sale_date = (date.today() + timedelta(days=offset)).isoformat()
            try:
                resp = _get(shop_url, {"available_for": view} if view else None)
            except requests.RequestException as e:
                logger.warning("店舗ページの取得に失敗 (%s): %s", sale_date, e)
                continue

            for a in _soup(resp).find_all("a", href=True):
                if "office_featured_product_" not in a["href"]:
                    continue
                item = parse_featured_anchor(a, resp.url)
                if item is None:
                    continue
                key = (sale_date, item["product_url"].split("?")[0])
                if key in seen:  # 同じ商品へのリンクがページ内に複数あることがある
                    continue
                seen.add(key)
                items.append({"sale_date": sale_date, **item})
        return items


if __name__ == "__main__":  # 単体での動作確認用: python -m lib.crawler.competitor_flyer_scraper
    logging.basicConfig(level=logging.INFO)
    s = TokubaiFlyerScraper.from_store_page("https://www.sugi-net.jp/stores/001612")
    fl = s.fetch_flyers()
    for f in fl:
        print(f)
    if fl:
        for it in s.fetch_featured_items(fl[0]["detail_url"]):
            print(it)
