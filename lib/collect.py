# -*- coding: utf-8 -*-
"""外部データの収集（毎朝の自動収集と、画面の「今すぐ取得」の両方から使う）

  競合チラシ・特売商品 … lib/crawler/competitor_flyer_scraper.py
  地域のイベント       … lib/crawler/kanazawa_events_scraper.py
  天気                 … Open-Meteo（無料・APIキー不要）https://open-meteo.com/
  卸売市場の相場       … 農林水産省「青果物卸売市場調査（日別調査）」金沢市中央卸売市場（e-Stat・登録不要）

集めた結果は data/raw/<種類>/<日付>.csv に書き出します（write_raw）。
卸売相場だけは、取得した日ではなく「市場の取引日」ごとに1ファイルです（公表が約1か月遅れるため）。
GCPの設定があるとき（Cloud Run ジョブ）は、同じファイルを Cloud Storage の raw/<種類>/ にも置きます。
この生データが「正」で、DBは lib/integrate.py がここから組み立てる派生物です。
同じ日に取り直したときは、その日のファイルだけが置き換わります（過去の日のファイルは触りません）。

このファイルは streamlit を読み込まないので、scripts/collect.py（GitHub Actions）からも使えます。
"""
from __future__ import annotations

import csv
import html
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests

from lib import crawl_store, gcp
from lib.crawl_store import EVENT_COLS, FEATURED_COLS, FLYER_COLS, JST
from lib.paths import RAW_DIR

# --------------------------------------------------------------------------
# 生データの種類ごとの列と、最低限のチェック
# --------------------------------------------------------------------------
WEATHER_COLS = ["date", "region_cd", "region_name", "weather", "weather_code",
                "max_temp", "min_temp", "precipitation", "is_forecast", "fetched_at"]
MARKET_COLS = ["date", "market", "category", "item", "item_code", "quantity_kg", "price_per_kg",
               "quantity_ratio", "price_ratio", "stat_inf_id", "fetched_at"]

SCHEMAS = {
    # 種類: (列, 空ではいけない列, 数値でなければいけない列)
    "flyers": (FLYER_COLS, ["store_name", "title"], ["image_width", "image_height"]),
    "featured": (FEATURED_COLS, ["store_name", "product_name"], ["base_price", "tax_price"]),
    "events": (EVENT_COLS, ["title"], []),
    "weather": (WEATHER_COLS, ["date", "region_cd", "max_temp", "min_temp"], ["max_temp", "min_temp"]),
    "market": (MARKET_COLS, ["date", "market", "category", "item"],
               ["quantity_kg", "price_per_kg", "quantity_ratio", "price_ratio"]),
}


class CheckError(Exception):
    """取得結果がおかしい（0件・必須の列が空・数値でない）。生データには書き出さない。"""


def check(source: str, rows: list[dict]) -> None:
    cols, required, numeric = SCHEMAS[source]
    if not rows:
        raise CheckError(f"{source}: 0件でした（サイトの構造が変わった可能性があります）")
    for i, r in enumerate(rows):
        missing = [c for c in required if r.get(c) in (None, "")]
        if missing:
            raise CheckError(f"{source}: {i + 1}行目の {', '.join(missing)} が空です")
        for c in numeric:
            v = r.get(c)
            if v not in (None, ""):
                try:
                    float(v)
                except (TypeError, ValueError):
                    raise CheckError(f"{source}: {i + 1}行目の {c} が数値ではありません（{v!r}）") from None


def write_raw(source: str, rows: list[dict], day: date | None = None) -> str:
    """チェックしてから data/raw/<種類>/<日付>.csv に書き出す。GCPの設定があれば Cloud Storage にも置く。

    戻り値は置いた場所（data/raw/... または gs://...）。
    """
    check(source, rows)
    cols = SCHEMAS[source][0]
    day = day or datetime.now(JST).date()
    path = RAW_DIR / source / f"{day.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    if gcp.enabled():
        return gcp.upload_raw(path, source)
    return path.relative_to(RAW_DIR.parent.parent).as_posix()


# --------------------------------------------------------------------------
# 競合チラシ・特売商品
# --------------------------------------------------------------------------
AEON = "イオン金沢店"
SUGI = "スギ薬局 金沢駅西店"


def _fetch_aeon() -> tuple[list[dict], list[dict]]:
    from lib.crawler.competitor_flyer_scraper import AeonFlyerScraper
    aeon = AeonFlyerScraper(
        shop_code="0058040",
        store_name=AEON,
    )
    return [vars(f) for f in aeon.fetch()], []


def _fetch_sugi() -> tuple[list[dict], list[dict]]:
    """スギ薬局 金沢駅西店（トクバイ経由）。特売商品は補助情報なので、失敗してもチラシは返す。"""
    from lib.crawler.competitor_flyer_scraper import TokubaiFlyerScraper, logger
    sugi = TokubaiFlyerScraper.from_store_page(
        store_page_url="https://www.sugi-net.jp/stores/001612",
        store_name=SUGI,
    )
    flyers = sugi.fetch(count=6)
    pickup = []
    if flyers:
        try:
            pickup = sugi.fetch_featured_items(flyers[0].detail_url)
        except Exception as e:  # noqa: BLE001
            logger.warning("特売商品の取得に失敗: %s", e)
    return [vars(f) for f in flyers], pickup


# 競合店 → 取得する関数（戻り値は (チラシ, 特売商品)。どちらもクローラーが返す形のまま）
COMPETITORS = {AEON: _fetch_aeon, SUGI: _fetch_sugi}


# --------------------------------------------------------------------------
# 地域のイベント（来月ぶんに絞らず、サイトに載っている全件を貯める）
# --------------------------------------------------------------------------
def fetch_events() -> list[dict]:
    """金沢市のイベント一覧（クローラーが返す日本語の列名のまま）。"""
    from lib.crawler.kanazawa_events_scraper import fetch_all_events, to_records
    return to_records(fetch_all_events())


def next_month(records: list[dict], today: date | None = None) -> list[dict]:
    """来月と期間が重なるイベントだけにする（kanazawa_events_scraper.filter_next_month と同じ条件）。"""
    today = today or datetime.now(JST).date()
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    start = date(y, m, 1).isoformat()
    end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)).isoformat()
    return [r for r in records
            if r.get("開始日") and r["開始日"] < end and (r.get("終了日") or r["開始日"]) >= start]


# --------------------------------------------------------------------------
# 天気（Open-Meteo）
#   過去7日（実績に近い値）＋今日から3日（予報）を毎日取る。同じ日を何度も取ることになるが、
#   DBでは日ごとに一番新しい取得を採用する（crawl_weather_latest）ので、予報はやがて実績で置き換わる。
# --------------------------------------------------------------------------
WEATHER_POINTS = {
    # 地域コード: (地域名, 緯度, 経度)。REG01 はデータ班の M_WEATHER と同じコード（店舗のある富山市）
    "REG01": ("富山市", 36.6953, 137.2113),
    "KNZ": ("金沢市", 36.5613, 136.6562),      # 競合店・イベントの地域
}
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def weather_label(code: int | None, precipitation: float | None = None) -> str | None:
    """WMOの天気コード → データ班の M_WEATHER と同じ言葉（晴れ／曇り／雨／雪）。"""
    if code is None:
        return None
    if code <= 1:
        return "晴れ"
    if code <= 48 or (51 <= code <= 57 and (precipitation or 0) < 1):   # 1mm未満の霧雨は曇り
        return "曇り"
    if 71 <= code <= 77 or code in (85, 86):
        return "雪"
    return "雨"


def fetch_weather(fetched_at: str, past_days: int = 7, forecast_days: int = 3) -> list[dict]:
    today = datetime.now(JST).date().isoformat()
    rows = []
    for region_cd, (name, lat, lon) in WEATHER_POINTS.items():
        resp = requests.get(OPEN_METEO_URL, timeout=20, params={
            "latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "past_days": past_days, "forecast_days": forecast_days,
        })
        resp.raise_for_status()
        d = resp.json()["daily"]
        for i, day in enumerate(d["time"]):
            if d["temperature_2m_max"][i] is None:
                continue
            code, rain = d["weather_code"][i], d["precipitation_sum"][i]
            rows.append({
                "date": day, "region_cd": region_cd, "region_name": name,
                "weather": weather_label(code, rain), "weather_code": code,
                "max_temp": d["temperature_2m_max"][i], "min_temp": d["temperature_2m_min"][i],
                "precipitation": rain,
                "is_forecast": int(day >= today), "fetched_at": fetched_at,
            })
    return rows


# --------------------------------------------------------------------------
# 卸売市場の相場（農林水産省「青果物卸売市場調査（日別調査）」・金沢市中央卸売市場）
#   e-Stat に取引日ごと・市場ごとのCSVが公表される（登録不要）。公表は約1か月遅れなので
#   「今朝の相場」ではなく「相場の傾向」として使う。
#   e-Stat の検索結果（公開日の新しい順）から「日別調査(令和◯年◯月◯日) 果実 金沢市」を探し、
#   同じ日の野菜のCSV（果実のIDの15前。中身で市場と日付を確かめる）も取る。
# --------------------------------------------------------------------------
MARKET = "金沢市"
ESTAT_SEARCH = ("https://www.e-stat.go.jp/stat-search/files?page={page}&layout=dataset&toukei=00500226"
                "&tstat=000001015623&tclass1=000001032194&tclass2=000001046984"
                "&query=%E9%87%91%E6%B2%A2%E5%B8%82&sort=open_date%20desc")
ESTAT_CSV = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={id}&fileKind=1"
ESTAT_DATASET = "https://www.e-stat.go.jp/stat-search/files?layout=dataset&toukei=00500226&stat_infid={id}"
ESTAT_DAY = ("https://www.e-stat.go.jp/stat-search/files?page=1&layout=datalist&cycle=0&toukei=00500226"
             "&tstat=000001015623&tclass1=000001032194&tclass2=000001046984"
             "&tclass3={t3}&tclass4={t4}&tclass5={t5}")
VEG_OFFSET = 15                      # 野菜のIDは果実のIDの15前のことが多い（違えば、その日の一覧から探す）
HEADERS = {"User-Agent": "Mozilla/5.0 (sunfuji-dashboard; class project)"}


def _estat_days(pages: int = 1) -> list[tuple[str, str]]:
    """公表されている金沢市の日別調査（果実）の (取引日, ファイルID)。新しい順。"""
    out = []
    for page in range(1, pages + 1):
        resp = requests.get(ESTAT_SEARCH.format(page=page), headers=HEADERS, timeout=30)
        resp.raise_for_status()
        parts = re.split(r'data-key="uid" data-value="(\d+)"', resp.text)
        for i in range(1, len(parts), 2):
            text = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", parts[i + 1])))
            m = re.search(r"日別調査\(令和(\d+)年(\d+)月(\d+)日\)果実" + MARKET, text)
            if m:
                day = date(2018 + int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
                out.append((day, parts[i]))
        time.sleep(1)
    return list(dict.fromkeys(out))


def _estat_rows(stat_id: str, day: str, category: str, fetched_at: str) -> list[dict]:
    """1つのCSV（ある日・ある市場・野菜または果実）から、品目ごとの合計（産地の内訳は除く）を取り出す。"""
    resp = requests.get(ESTAT_CSV.format(id=stat_id), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    rows = list(csv.reader(resp.content.decode("cp932").splitlines()))
    if not rows or rows[0][:5] != ["年", "月", "日", "曜日", "都市名"]:
        raise CheckError(f"market: {stat_id} は日別調査のCSVではありません")
    out = []
    for r in rows[1:]:
        if len(r) < 14:
            continue
        r_day = date(int(r[0]), int(r[1]), int(r[2])).isoformat()
        if r[4] != MARKET or r_day != day:
            raise CheckError(f"market: {stat_id} は {r[4]} {r_day} のデータでした（{MARKET} {day} のはず）")
        if r[8].strip():                     # 産地別の行は除く（品目ごとの合計だけ残す）
            continue
        out.append({"date": day, "market": MARKET, "category": category, "item": r[6],
                    "item_code": r[7], "quantity_kg": _num(r[10]), "price_per_kg": _num(r[11]),
                    "quantity_ratio": _num(r[12]), "price_ratio": _num(r[13]), "stat_inf_id": stat_id,
                    "fetched_at": fetched_at})
    return out


def _num(v: str) -> str:
    """数値でないもの（統計表の「－」「…」「x」など＝該当なし・秘匿）は空にする。"""
    v = v.strip().replace(",", "")
    try:
        float(v)
        return v
    except ValueError:
        return ""


def _veg_id_from_listing(fruit_id: str) -> str | None:
    """果実のファイルと同じ日の一覧ページから「野菜 金沢市」のファイルIDを探す。"""
    page = requests.get(ESTAT_DATASET.format(id=fruit_id), headers=HEADERS, timeout=30).text
    t = {k: re.findall(rf"tclass{k}=(\d+)", page) for k in (3, 4, 5)}
    if not all(t.values()):
        return None
    time.sleep(0.5)
    listing = requests.get(ESTAT_DAY.format(t3=t[3][0], t4=t[4][0], t5=t[5][0]),
                           headers=HEADERS, timeout=30).text
    for m in re.finditer(r"statInfId=(\d+)(?:&amp;|&)fileKind=1", listing):
        before = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", listing[max(0, m.start() - 2500):m.start()])))
        names = re.findall(r"(野菜|果実)\s+(\S+?)\s", before[-300:])
        if names and names[-1] == ("野菜", MARKET):
            return m.group(1)
    return None


def _veg_rows(fruit_id: str, day: str, fetched_at: str) -> list[dict]:
    """同じ日の野菜。まずIDの並びから当て、外れたら一覧から探す（どちらも中身で市場と日付を確かめる）。"""
    guess = f"{int(fruit_id) - VEG_OFFSET:0{len(fruit_id)}d}"
    try:
        return _estat_rows(guess, day, "野菜", fetched_at)
    except Exception:  # noqa: BLE001
        time.sleep(0.5)
        found = _veg_id_from_listing(fruit_id)
        if not found:
            raise CheckError(f"market: {day} の野菜のファイルが一覧に見つかりません") from None
        time.sleep(0.5)
        return _estat_rows(found, day, "野菜", fetched_at)


def _collected(source: str, day: str) -> bool:
    if (RAW_DIR / source / f"{day}.csv").exists():
        return True
    return gcp.enabled() and gcp.raw_exists(source, f"{day}.csv")


def collect_market(fetched_at: str, max_days: int = 10, pages: int = 1, log=print) -> list[str]:
    """まだ取っていない取引日を、新しい順に最大 max_days 日ぶん取って書き出す。戻り値は書いた場所。"""
    written = []
    for day, fruit_id in _estat_days(pages):
        if len(written) >= max_days:
            break
        if _collected("market", day):
            continue
        rows = _estat_rows(fruit_id, day, "果実", fetched_at)
        time.sleep(0.5)
        try:
            rows = _veg_rows(fruit_id, day, fetched_at) + rows
        except Exception as e:  # noqa: BLE001  野菜が見つからない日も、果実は残す
            log(f"[market] {day} の野菜は取れませんでした: {e}")
        time.sleep(0.5)
        written.append(write_raw("market", rows, date.fromisoformat(day)))
        log(f"[market] {day}: {len(rows)}品目")
    return written


# --------------------------------------------------------------------------
# まとめて収集（scripts/collect.py から呼ぶ）
# --------------------------------------------------------------------------
JOBS = ("weather", "events", "competitors", "market")


def collect(jobs: tuple[str, ...] = JOBS, log=print, market_days: int = 10) -> dict[str, str]:
    """各ジョブを実行して生データに書き出す。1つ失敗しても、ほかは続ける。

    戻り値は {種類: "OK 12件 → data/raw/..." または "NG 理由"}。
    market_days は、卸売相場をまだ取っていない取引日から最大何日ぶん取るか（初回にまとめて取るときに増やす）。
    """
    fetched_at = crawl_store.now()
    result: dict[str, str] = {}

    def _write(source: str, rows: list[dict]) -> None:
        try:
            result[source] = f"OK {len(rows)}件 → {write_raw(source, rows)}"
        except Exception as e:  # noqa: BLE001
            result[source] = f"NG {e}"
        log(f"[{source}] {result[source]}")

    if "weather" in jobs:
        try:
            _write("weather", fetch_weather(fetched_at))
        except Exception as e:  # noqa: BLE001
            result["weather"] = f"NG {e}"
            log(f"[weather] {result['weather']}")

    if "events" in jobs:
        try:
            _write("events", crawl_store.event_rows(fetch_events(), fetched_at))
        except Exception as e:  # noqa: BLE001
            result["events"] = f"NG {e}"
            log(f"[events] {result['events']}")

    if "competitors" in jobs:
        flyers, featured, errors = [], [], []
        for name, fetch in COMPETITORS.items():
            try:
                f, p = fetch()
                flyers += crawl_store.flyer_rows(name, f, fetched_at)
                featured += crawl_store.featured_rows(name, p, fetched_at)
                log(f"[competitors] {name}: チラシ{len(f)}件・特売商品{len(p)}件")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                log(f"[competitors] {name}: NG {e}")
        _write("flyers", flyers)
        if featured:
            _write("featured", featured)
        if errors:
            result["competitors"] = "NG " + " / ".join(errors)

    if "market" in jobs:
        try:
            written = collect_market(fetched_at, max_days=market_days, pages=1 + market_days // 40, log=log)
            result["market"] = (f"OK {len(written)}日ぶん" if written
                                else "OK 新しく公表された取引日はありませんでした")
        except Exception as e:  # noqa: BLE001
            result["market"] = f"NG {e}"
        log(f"[market] {result['market']}")

    return result
