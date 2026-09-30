# -*- coding: utf-8 -*-
"""
competitor_flyer_scraper.py
----------------------------------------
①競合情報：特定店舗のLPから特売・チラシ情報を取得するモジュール。

【事前確認した各サイトの構造と、この実装の前提】
- イオン金沢店 (https://www.aeon.com/store/.../flyer/)
    チラシは自社ページに直接HTMLとして載らず、「Shufoo!」という外部チラシ
    配信サービスのウィジェット/JSで表示される方式。取得時点では
    「ただいまチラシの掲載はございません」という空表示だった。
    → 静的HTML取得(requests)だけではチラシ本体を取れない可能性が高いため、
      このモジュールでは (a) チラシ掲載の有無と、掲載がある場合の
      画像URL/リンクをベストエフォートで拾う関数を用意しつつ、
      (b) 本格的に取得したい場合はShufoo!側のAPI/埋め込みJSを追う必要が
      ある旨をコード内に明記する。

- スギ薬局 金沢駅西店 (https://www.sugi-net.jp/stores/001612)
    店舗ページ内に「トクバイ」というチラシ配信サービスのウィジェットが
    埋め込まれており、実体は
      https://widgets.tokubai.co.jp/<店舗ID>/leaflet_widget?count=N&type=spweb
    という別ドメインのURLになっている。ここは静的HTMLで
      ・チラシタイトル(店舗名+チラシ種別)
      ・掲載期間(例:「2026年9月22日〜28日まで」)
      ・チラシ詳細への遷移リンク(クリックURL)
    まで取得できることを確認済み。
    ただし個別の「商品名・数量・価格」はチラシ"画像"の中の文字として
    描画されているため、画像だけからは構造化テキストを取得できない。
    → 本モジュールでは、まずチラシ画像・掲載期間などのメタ情報を
      リスト化するところまでを実装し、画像からの商品明細抽出は
      OCR(pytesseract)を使う関数を別途用意する(店舗ごとにチラシの
      レイアウトが異なるため、抽出精度は実運用で要チューニング)。

このファイルは3つの部分から成る:
  1. AeonFlyerScraper   : イオン金沢店のチラシ掲載有無・画像URL取得
  2. TokubaiFlyerScraper: スギ薬局(トクバイ経由)のチラシ一覧取得
  3. extract_items_from_flyer_image(): チラシ画像 → 商品明細(日付・時間帯・
     商品名・内容量・価格)をOCRでベストエフォート抽出するユーティリティ
"""

from __future__ import annotations

import io
import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    # 一般的なブラウザとして振る舞う。過度な高頻度アクセスは避けること。
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 10
REQUEST_INTERVAL_SEC = 1.5  # 対象サイトへの配慮として最低限のスリープを入れる


@dataclass
class FlyerEntry:
    """1件のチラシ(掲載期間の1枚)を表す共通データ構造"""
    store_name: str
    title: str
    period_text: Optional[str] = None       # 例: "2026年9月22日〜28日まで"
    period_start: Optional[str] = None      # YYYY-MM-DD (パースできた場合)
    period_end: Optional[str] = None        # YYYY-MM-DD (パースできた場合)
    image_url: Optional[str] = None
    detail_url: Optional[str] = None
    leaflet_id: Optional[str] = None        # トクバイ側のチラシID(重複判定用)
    image_width: Optional[int] = None       # 取得した画像の実サイズ(px)
    image_height: Optional[int] = None
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _sleep():
    time.sleep(REQUEST_INTERVAL_SEC)


def _parse_period_text(text: str, base_year: Optional[int] = None):
    """
    "2026年9月22日〜28日まで" のような期間表記から (start, end) を
    YYYY-MM-DD文字列としてベストエフォートで抽出する。
    パースできない場合は (None, None) を返す。
    """
    if base_year is None:
        base_year = datetime.now().year

    # パターン1: YYYY年M月D日〜D日まで  (終了日は同月)
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[〜~-]\s*(\d{1,2})日", text)
    if m:
        y, mo, d1, d2 = m.groups()
        try:
            start = f"{y}-{int(mo):02d}-{int(d1):02d}"
            end = f"{y}-{int(mo):02d}-{int(d2):02d}"
            return start, end
        except ValueError:
            pass

    # パターン2: YYYY年M月D日〜M月D日まで (月をまたぐ)
    m = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[〜~-]\s*(\d{1,2})月(\d{1,2})日", text
    )
    if m:
        y, mo1, d1, mo2, d2 = m.groups()
        try:
            end_year = int(y)
            if (int(mo2), int(d2)) < (int(mo1), int(d1)):
                end_year += 1  # 例: 2026年12月28日〜1月3日 → 終了は2027年
            start = f"{y}-{int(mo1):02d}-{int(d1):02d}"
            end = f"{end_year}-{int(mo2):02d}-{int(d2):02d}"
            return start, end
        except ValueError:
            pass

    return None, None


class TokubaiFlyerScraper:
    """
    トクバイのウィジェットURL経由でチラシ一覧(メタ情報)を取得する。
    スギ薬局 金沢駅西店の店舗ページから埋め込みURLを特定して利用する想定。

    使い方:
        scraper = TokubaiFlyerScraper(store_widget_id="187997", store_name="スギ薬局 金沢駅西店")
        flyers = scraper.fetch(count=5)
    """

    WIDGET_URL_TEMPLATE = "https://widgets.tokubai.co.jp/{store_id}/leaflet_widget"

    def __init__(self, store_widget_id: str, store_name: str):
        self.store_widget_id = store_widget_id
        self.store_name = store_name

    @classmethod
    def from_store_page(cls, store_page_url: str, store_name: str) -> "TokubaiFlyerScraper":
        """
        店舗ページ(例: https://www.sugi-net.jp/stores/001612)を取得し、
        埋め込まれている widgets.tokubai.co.jp のURLから店舗IDを自動抽出する。
        """
        resp = requests.get(store_page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # charset未指定サイトでの文字化け対策
        m = re.search(r"widgets\.tokubai\.co\.jp/(\d+)/leaflet_widget", resp.text)
        if not m:
            raise ValueError(
                f"{store_page_url} 内にトクバイのウィジェットURLが見つかりませんでした。"
                "サイト構造が変わっている可能性があります。"
            )
        store_id = m.group(1)
        logger.info("トクバイ店舗ID を検出: %s", store_id)
        return cls(store_widget_id=store_id, store_name=store_name)

    # チラシ詳細ページ内で、チラシ本体画像を指す<img>のURLに含まれる目印
    LEAFLET_IMG_MARKER = "bargain_office_leaflets"

    @staticmethod
    def _short_title(full_text: str) -> str:
        """
        "スギドラッグ 金沢駅西店のチラシ・特売情報 チラシのご案内 2026年9月29日〜10月5日まで"
        から、店舗名と末尾の期間表記を除いた "チラシのご案内" 部分を取り出す(ベストエフォート)。
        """
        t = re.sub(r".*?チラシ・特売情報\s*", "", full_text)
        t = re.sub(r"\s*\d{4}年\d{1,2}月\d{1,2}日.*$", "", t)
        return t.strip() or full_text

    @staticmethod
    def _is_leaflet_image_url(url: str) -> bool:
        """チラシ画像のURLか判定する(ロゴや操作アイコンは除外)。"""
        parsed = urlparse(url)
        return parsed.netloc.endswith("image.tokubai.co.jp") and "leaflet" in parsed.path

    def _collect_image_candidates(self, soup: BeautifulSoup, base_url: str) -> List[dict]:
        """
        ページ内の「チラシ画像らしいURL」をすべて集める。
        ページには本体画像のほかに「他のチラシ」のサムネイル等も含まれるため、
        最初の1件だけを採用すると小さい画像を拾ってしまうことがある。
        (遅延読み込み用の data-src や srcset も対象にする)
        """
        cands, seen = [], set()
        for img in soup.find_all("img"):
            urls = [img.get(a) for a in ("src", "data-src", "data-original", "data-lazy-src")]
            for attr in ("srcset", "data-srcset"):
                if img.get(attr):
                    urls += [part.strip().split(" ")[0] for part in img[attr].split(",") if part.strip()]
            for u in urls:
                if not u:
                    continue
                full = urljoin(base_url, u)
                if self._is_leaflet_image_url(full) and full not in seen:
                    seen.add(full)
                    cands.append({"url": full, "alt": img.get("alt") or ""})
        return cands

    @staticmethod
    def _page_variants(final_url: str) -> List[str]:
        """
        ウィジェット用ページ(show_for_widget)のURLから、同じチラシの別ページ
        (印刷用・通常表示)のURLを組み立てる。ウィジェット用は小さい画像しか
        置かれていないことがあるため、より大きな画像を探す目的で使う。
        """
        m = re.search(r"/(\d+)/leaflets/(\d+)", final_url)
        if not m:
            return []
        store_id, leaflet_id = m.groups()
        plain = final_url.split("/show_for_widget")[0]
        return [
            f"https://tokubai.co.jp/{store_id}/leaflets/{leaflet_id}/print",
            plain,
        ]

    @staticmethod
    def _measure_image(url: str) -> Optional[Tuple[int, int]]:
        """画像を実際にダウンロードして、ピクセルサイズ(幅, 高さ)を返す。"""
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).size

    @staticmethod
    def _pick_largest(cands: List[dict]) -> Optional[Tuple[str, int, int]]:
        """
        候補の中から本体画像を選ぶ。
        URLに o=true(原寸)を含むものを優先し、無ければ全候補を実測して面積が最大のものを選ぶ。
        (w=180,h=135 のようなサイズ指定つきURLはサムネイルなので、原寸があれば測定しない)
        """
        originals = [c for c in cands if "o=true" in c["url"]]
        pool = originals[:3] if originals else cands[:6]
        measured = []
        for c in pool:
            try:
                size = TokubaiFlyerScraper._measure_image(c["url"])
            except Exception as e:  # noqa: BLE001
                logger.warning("画像サイズの測定に失敗: %s (%s)", c["url"], e)
                continue
            measured.append((c["url"], size[0], size[1]))
            logger.info("チラシ画像候補: %s  %dx%d", c["url"], size[0], size[1])
        if not measured:
            return None
        return max(measured, key=lambda m: m[1] * m[2])

    def _fetch_leaflet_image(
        self, detail_url: str, leaflet_id: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """
        チラシ本体画像(原寸)を特定して (URL, 幅px, 高さpx) を返す。

        1. 印刷用ページ (tokubai.co.jp/<店舗ID>/leaflets/<チラシID>/print) を最優先で使う。
           このページは<img>がチラシ本体1枚だけで、曖昧さがなく通信も少ない。
        2. 取得できなければ、ウィジェット用ページ→通常ページの順に調べ、
           候補の中から原寸(o=true)/最大サイズの画像を選ぶ。
        """
        if leaflet_id:
            print_url = f"https://tokubai.co.jp/{self.store_widget_id}/leaflets/{leaflet_id}/print"
            try:
                r = requests.get(print_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                r.encoding = "utf-8"
                picked = self._pick_largest(self._collect_image_candidates(BeautifulSoup(r.text, "lxml"), r.url))
                if picked:
                    return picked
            except Exception as e:  # noqa: BLE001
                logger.info("印刷用ページからの取得に失敗、他のページを試します: %s (%s)", print_url, e)
            _sleep()

        resp = requests.get(detail_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        cands = self._collect_image_candidates(BeautifulSoup(resp.text, "lxml"), resp.url)

        seen = {c["url"] for c in cands}
        for variant_url in self._page_variants(resp.url):
            _sleep()
            try:
                r = requests.get(variant_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                r.encoding = "utf-8"
                for c in self._collect_image_candidates(BeautifulSoup(r.text, "lxml"), r.url):
                    if c["url"] not in seen:
                        seen.add(c["url"])
                        cands.append(c)
            except Exception as e:  # noqa: BLE001
                logger.info("別ページの取得をスキップ: %s (%s)", variant_url, e)

        picked = self._pick_largest(cands)
        return picked if picked else (None, None, None)

    # ---- トクバイに店舗が掲載している「特売商品」(文字データ) ----
    @staticmethod
    def _parse_pickup_alt(alt: str) -> Optional[dict]:
        """
        画像のalt文字列 "アリナミン7 987円(税込)" から 商品名・価格・税区分 を取り出す。
        価格が読めないaltは対象外(None)。
        """
        import unicodedata

        from .flyer_item_extractor import QTY_RE

        # altは正確な文字列なので、OCR用の正規化(日本語間の空白除去)は使わず、
        # 全角→半角の統一と連続空白の整理だけを行う
        t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", alt)).strip()
        m = re.search(r"(?P<price>\d{1,3}(?:,\d{3})+|\d+)\s*円\s*(?:\((?P<label>税込|税抜|本体)\))?\s*$", t)
        if not m:
            return None
        name = t[: m.start()].strip()
        if not name:
            return None
        qty = QTY_RE.search(name)
        return {
            "商品名": name,
            "内容": qty.group(0).replace(" ", "") if qty else None,
            "価格": int(m.group("price").replace(",", "")),
            "価格注記": m.group("label"),
        }

    # ---- 店舗ページの「今週のおすすめ/イチオシ」(商品名・内容量・本体価格・税込価格) ----
    # 店舗ページには日付の切り替え(今日/明日/明後日)があり、日ごとに掲載商品が変わる。
    DATE_VIEWS = (("today", 0), ("tomorrow", 1), ("day_after_tomorrow", 2))
    _PRICE_PAIR_RE = re.compile(
        r"(?P<base>\d{1,3}(?:,\d{3})*|\d+)\s*円\s*(?P<tax>\d{1,3}(?:,\d{3})*|\d+)\s*円\s*\(税込\)"
    )
    _PRICE_TAX_ONLY_RE = re.compile(r"(?P<tax>\d{1,3}(?:,\d{3})*|\d+)\s*円\s*\(税込\)")

    @staticmethod
    def _shop_url_from(final_url: str) -> Optional[str]:
        """チラシ詳細ページのURLから、店舗トップページのURLを組み立てる。"""
        m = re.match(r"(https://tokubai\.co\.jp/[^?#]*?/\d+)(?:/leaflets/\d+.*)?(?:[?#].*)?$", final_url)
        return m.group(1) if m else None

    def _parse_featured_anchor(self, a, page_url: str) -> Optional[dict]:
        """
        「今週のおすすめ」1商品分のリンク(<a>)から、商品名・内容量・本体価格・税込価格を取り出す。

        リンク内の文字は概ね「商品名 / 内容量 / 本体価格+税込価格」の並びで、
        商品名は画像のalt属性のほうが省略されていない(表示側は「…」で省略されることがある)。
        DOM構造の細部に依存しないよう、次の手順で取り出す:
          1. 表示テキスト全体から、末尾の価格(「898円987円(税込)」)を正規表現で抽出
          2. 価格より前の部分と、alt由来の完全な商品名の「共通する先頭部分」を除いた残りを内容量とする
        """
        import unicodedata

        def nfkc(x: str) -> str:
            return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", x)).strip()

        lines = [nfkc(l) for l in a.get_text("\n", strip=True).split("\n") if l.strip()]
        lines = [l for l in lines if not re.fullmatch(r"イチオシ\s*!?", l)]   # バッジ表記は除く
        text = " ".join(lines)

        m = self._PRICE_PAIR_RE.search(text)
        base_price = tax_price = None
        if m:
            base_price = int(m.group("base").replace(",", ""))
            tax_price = int(m.group("tax").replace(",", ""))
        else:
            m = self._PRICE_TAX_ONLY_RE.search(text)
            if not m:
                return None
            tax_price = int(m.group("tax").replace(",", ""))
        before = text[: m.start()].strip()

        # 完全な商品名: 画像のaltから末尾の価格・バッジを除いたもの(無ければ表示テキストの先頭)
        img = a.find("img")
        alt = nfkc(img.get("alt") or "") if img is not None else ""
        alt = re.sub(r"\s*[\d,]+円\s*\(税込\)(\s*イチオシ\s*!?)?\s*$", "", alt).strip()
        name = alt or (lines[0] if lines else before)
        if not name:
            return None

        # 内容量: 表示テキストのうち、商品名と共通する先頭部分を除いた残り
        common = 0
        for c1, c2 in zip(before, name):
            if c1 != c2:
                break
            common += 1
        # 省略記号(正規化で「…」が「...」になる場合もある)と空白を先頭から除く
        spec = re.sub(r"^(?:\.{2,}|…|\s)+", "", before[common:]).strip() or None
        if spec == name:   # 万一、商品名そのものが残った場合は内容量なしとみなす
            spec = None

        href = a.get("href") or ""
        img_src = urljoin(page_url, img.get("src")) if img is not None and img.get("src") else None
        return {
            "商品名": name,
            "内容": spec,
            "本体価格": base_price,
            "税込価格": tax_price,
            "イチオシ": "イチオシ" in a.get_text(),
            "画像URL": img_src,
            "商品URL": urljoin(page_url, href),
        }

    def fetch_featured_items(self, detail_url: str) -> List[dict]:
        """
        トクバイの店舗ページ(今日・明日・明後日の3日分)から「今週のおすすめ/イチオシ」を取得する。
        OCRを使わず、商品名・内容量・本体価格・税込価格を文字データのまま取得できる。

        detail_url: チラシ詳細ページのURL(ここから店舗ページのURLを組み立てる)
        戻り値: [{日付, 商品名, 内容, 本体価格, 税込価格, イチオシ, 画像URL, 商品URL}, ...]
        """
        import unicodedata
        from datetime import date, timedelta

        resp = requests.get(detail_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        shop_url = self._shop_url_from(self._page_variants(resp.url)[1] if self._page_variants(resp.url) else resp.url)
        if not shop_url:
            raise ValueError(f"店舗ページのURLを特定できませんでした: {resp.url}")

        today = date.today()
        items: List[dict] = []
        seen = set()
        for view, offset in self.DATE_VIEWS:
            _sleep()
            url = shop_url if view == "today" else f"{shop_url}?available_for={view}"
            try:
                r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                r.encoding = "utf-8"
            except Exception as e:  # noqa: BLE001
                logger.warning("店舗ページの取得に失敗 (%s): %s", view, e)
                continue
            soup = BeautifulSoup(r.text, "lxml")

            # ページ上の選択中の日付を優先(無ければ 今日+offset)
            page_date = today + timedelta(days=offset)
            dm = re.search(r"(\d{1,2})月(\d{1,2})日\s*[\(（][月火水木金土日][\)）]", unicodedata.normalize("NFKC", soup.get_text()))
            if dm:
                mo, dd = int(dm.group(1)), int(dm.group(2))
                try:
                    page_date = date(today.year + (1 if mo < today.month - 6 else 0), mo, dd)
                except ValueError:
                    pass

            for a in soup.find_all("a", href=True):
                if "office_featured_product_" not in a["href"]:
                    continue
                parsed = self._parse_featured_anchor(a, r.url)
                if not parsed:
                    continue
                key = (page_date, parsed["商品URL"].split("?")[0])
                if key in seen:
                    continue
                seen.add(key)
                parsed["日付"] = page_date.isoformat()
                items.append(parsed)
        return items

    def fetch(self, count: int = 5, with_images: bool = True) -> List[FlyerEntry]:
        """
        チラシ一覧を取得する。
        with_images=True の場合、各チラシの詳細ページにも1件ずつアクセスして
        画像URLを取得する(件数分のリクエストが増えるため、間に待機を挟む)。
        """
        url = self.WIDGET_URL_TEMPLATE.format(store_id=self.store_widget_id)
        params = {"count": count, "type": "spweb"}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # charset未指定サイトでの文字化け対策
        soup = BeautifulSoup(resp.text, "lxml")

        entries: List[FlyerEntry] = []
        seen_ids = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "leaflet_widget/click" not in href:
                continue

            id_match = re.search(r"[?&]id=(\d+)", href)
            leaflet_id = id_match.group(1) if id_match else None
            if leaflet_id and leaflet_id in seen_ids:
                continue
            if leaflet_id:
                seen_ids.add(leaflet_id)

            full_text = a.get_text(strip=True)
            detail_url = href if href.startswith("http") else f"https://widgets.tokubai.co.jp{href}"

            # 掲載期間はテキスト末尾に含まれる
            # 例: "...チラシのご案内 2026年9月22日〜28日まで"
            period_start, period_end = _parse_period_text(full_text)

            # ウィジェット一覧のリンク内にある画像は、小さいサムネイルのため本体としては使わない。
            # (以前はこれを採用していたため、OCRに小さい画像が渡ってしまっていた)
            # 本体画像は詳細ページから取得し、失敗した場合に限ってサムネイルで代用する。
            thumb_tag = a.find("img")
            thumbnail_url = thumb_tag["src"] if thumb_tag is not None and thumb_tag.has_attr("src") else None

            image_url, image_w, image_h = None, None, None
            if with_images:
                try:
                    image_url, image_w, image_h = self._fetch_leaflet_image(detail_url, leaflet_id)
                except Exception as e:
                    logger.warning("チラシ画像の取得に失敗 (id=%s): %s", leaflet_id, e)
                _sleep()
            if image_url is None:
                image_url = thumbnail_url

            entries.append(
                FlyerEntry(
                    store_name=self.store_name,
                    title=self._short_title(full_text),
                    period_text=full_text,
                    period_start=period_start,
                    period_end=period_end,
                    image_url=image_url,
                    detail_url=detail_url,
                    leaflet_id=leaflet_id,
                    image_width=image_w,
                    image_height=image_h,
                )
            )
        return entries


class AeonFlyerScraper:
    """
    イオン金沢店のチラシページから、掲載有無と(掲載がある場合の)
    画像URL/リンクを取得する。

    注意:
      イオンのチラシはShufoo!というサービスのJSウィジェットで描画されるため、
      requestsで取得した素のHTMLには反映されていないケースが多い
      (実際に確認した時点でも「ただいまチラシの掲載はございません」の
      固定テキストのみだった)。
      本格的に取得したい場合は、以下いずれかの対応が必要になる:
        (a) Shufoo!側が提供する店舗別チラシAPI/フィードを直接叩く
            (店舗コードの特定が必要)
        (b) Selenium/Playwright等でヘッドレスブラウザによりJS実行後のDOMを取得する
      本関数はまず「掲載されているかどうか」の判定と、
      掲載時の素朴な抽出(og:imageやimgタグ)をベストエフォートで行う。
    """

    NO_FLYER_TEXT = "ただいまチラシの掲載はございません"

    def __init__(self, flyer_page_url: str, store_name: str):
        self.flyer_page_url = flyer_page_url
        self.store_name = store_name

    def fetch(self) -> List[FlyerEntry]:
        resp = requests.get(self.flyer_page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # charset未指定サイトでの文字化け対策
        soup = BeautifulSoup(resp.text, "lxml")
        page_text = soup.get_text()

        if self.NO_FLYER_TEXT in page_text:
            logger.info("[%s] 現在チラシの掲載はありません。", self.store_name)
            return []

        # 掲載がある場合の簡易抽出(サイト構造変更時は要調整)
        entries: List[FlyerEntry] = []
        # チラシ画像は "flyer" や "chirashi" を含むimgタグ配下にあることが多い
        candidate_imgs = [
            img for img in soup.find_all("img")
            if img.has_attr("src") and re.search(r"flyer|chirashi", img["src"], re.I)
        ]
        for img in candidate_imgs:
            parent_a = img.find_parent("a")
            entries.append(
                FlyerEntry(
                    store_name=self.store_name,
                    title=img.get("alt", "イオン金沢店チラシ"),
                    image_url=img["src"],
                    detail_url=parent_a["href"] if parent_a and parent_a.has_attr("href") else None,
                )
            )
        if not entries:
            logger.warning(
                "[%s] チラシ掲載ありと判定したが、画像を自動抽出できなかった。"
                "サイト構造が変わっている可能性あり。手動確認を推奨。",
                self.store_name,
            )
        return entries


def extract_items_from_flyer_image(image_source, period_text: Optional[str] = None):
    """
    チラシ画像から商品明細を抽出する。実体は flyer_item_extractor.extract_items に移した
    (価格札の切り出しOCRなど、チラシ向けの処理を含む)。旧関数との互換のために残している。
    戻り値: (items, raw_lines)
    """
    from .flyer_item_extractor import extract_items

    return extract_items(image_source, period_text=period_text)


def main():
    all_flyers: List[FlyerEntry] = []

    # --- イオン金沢店 ---
    aeon = AeonFlyerScraper(
        flyer_page_url="https://www.aeon.com/store/イオン/イオン金沢店/flyer/",
        store_name="イオン金沢店",
    )
    try:
        all_flyers.extend(aeon.fetch())
    except Exception as e:
        logger.error("イオン金沢店の取得に失敗: %s", e)
    _sleep()

    # --- スギ薬局 金沢駅西店 (トクバイ経由) ---
    try:
        sugi_scraper = TokubaiFlyerScraper.from_store_page(
            store_page_url="https://www.sugi-net.jp/stores/001612",
            store_name="スギ薬局 金沢駅西店",
        )
        all_flyers.extend(sugi_scraper.fetch(count=5))
    except Exception as e:
        logger.error("スギ薬局 金沢駅西店の取得に失敗: %s", e)

    for f in all_flyers:
        print(f)

    return all_flyers


if __name__ == "__main__":
    main()
