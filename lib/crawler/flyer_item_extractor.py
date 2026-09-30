# -*- coding: utf-8 -*-
"""
flyer_item_extractor.py
----------------------------------------
チラシ画像から「日付・時間帯・商品名・内容量・価格」をOCR(Tesseract)で
ベストエフォート抽出するモジュール。

【処理の流れ】
  1. 画像の前処理  : グレースケール化 → 拡大 → コントラスト補正 → シャープ化
  2. OCR(2段構え): (a) ページ全体を Tesseract の「疎なテキスト」モード(psm 11)で読み取り、
                        単語を行単位にまとめて位置情報つきで保持
                    (b) 色付きの価格札(赤・黄などの塊)を検出して1枚ずつ切り出して読む
                        ※ページ全体OCRは、価格札の中の巨大な文字を落としてしまうため
  3. テキスト正規化: 全角→半角、日本語文字間の余計な空白除去、価格中の
                    数字誤読(O→0, l→1 等)の補正
  4. 意味づけ     : 各行から 価格 / 内容量 / 日付 / 時間帯 を正規表現で検出し、
                    「価格の行」を起点に、位置が近い上側の行を商品名として紐づける

【期待する精度について】
  チラシは装飾文字・斜め文字・写真上の文字が多く、汎用OCRでは誤読や欠落が
  必ず発生する。このモジュールの出力は「下書き」として扱い、画面上で
  元画像と見比べて確認・修正する運用を前提にしている
  (抽出結果と一緒に、OCRが読んだ生の行一覧も返すので突き合わせに使える)。

【必要なもの】
  - Tesseract OCR 本体 + 日本語データ(jpn)
      Windows: https://github.com/UB-Mannheim/tesseract/wiki のインストーラを使い、
               インストール時の「Additional language data」で Japanese にチェック
      Ubuntu : sudo apt-get install tesseract-ocr tesseract-ocr-jpn
  - pip install pytesseract pillow requests
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 20
# これより幅が小さい画像はOCRの精度が大きく落ちる(チラシ1枚の目安)
MIN_RECOMMENDED_WIDTH = 800


class OcrNotAvailableError(RuntimeError):
    """Tesseract本体または日本語データが見つからないときのエラー"""


# ---------------------------------------------------------------------------
# Tesseractの設定
# ---------------------------------------------------------------------------
def _windows_candidates() -> List[str]:
    user = os.environ.get("USERNAME", "")
    return [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        rf"C:\Users\{user}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    ]


def configure_tesseract(cmd: Optional[str] = None, required_lang: str = "jpn") -> str:
    """
    tesseract 実行ファイルを探して pytesseract に設定し、そのパスを返す。
    探索順: 引数 cmd → 環境変数 TESSERACT_CMD → PATH上の tesseract → Windowsの標準インストール先。
    日本語データ(jpn)が入っていない場合も、分かりやすいエラーで知らせる。
    """
    try:
        import pytesseract
    except ImportError as e:
        raise OcrNotAvailableError(
            "pytesseract がインストールされていません。 pip install pytesseract pillow を実行してください。"
        ) from e

    candidates = [cmd, os.environ.get("TESSERACT_CMD"), shutil.which("tesseract")]
    candidates += _windows_candidates()
    found = next((c for c in candidates if c and os.path.exists(c)), None)
    if not found:
        raise OcrNotAvailableError(
            "Tesseract OCR 本体が見つかりません。\n"
            "Windows: https://github.com/UB-Mannheim/tesseract/wiki からインストールし、"
            "「Additional language data」で Japanese を選択してください。\n"
            "インストール後は、PATHに追加するか、環境変数 TESSERACT_CMD に tesseract.exe のフルパスを設定してください。"
        )
    pytesseract.pytesseract.tesseract_cmd = found

    try:
        langs = pytesseract.get_languages(config="")
    except Exception as e:  # noqa: BLE001
        raise OcrNotAvailableError(f"Tesseractの実行に失敗しました: {e}") from e
    if required_lang not in langs:
        raise OcrNotAvailableError(
            f"Tesseractに日本語データ({required_lang})が入っていません(検出された言語: {langs})。\n"
            "インストーラを再実行して Japanese を追加するか、jpn.traineddata を tessdata フォルダに配置してください。"
        )
    return found


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------
@dataclass
class OcrLine:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    conf: float

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)

    @property
    def width(self) -> float:
        return max(self.right - self.left, 1.0)


# ---------------------------------------------------------------------------
# 画像の取得と前処理
# ---------------------------------------------------------------------------
def load_image(source) -> Image.Image:
    """URL文字列 / ファイルパス / PIL.Image / バイト列 のいずれからでも画像を読み込む。"""
    if isinstance(source, Image.Image):
        return source
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source))
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        resp = requests.get(source, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))
    return Image.open(source)


def preprocess(img: Image.Image, target_width: int = 2400, max_scale: float = 3.0) -> Tuple[Image.Image, float]:
    """
    OCR向けの前処理。小さい画像は拡大し(文字が小さいほど誤読が増えるため)、
    コントラストを補正してシャープ化する。戻り値: (処理後のグレー画像, 拡大率)
    """
    gray = ImageOps.grayscale(img.convert("RGB"))
    scale = 1.0
    if gray.width < target_width:
        scale = min(target_width / gray.width, max_scale)
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.LANCZOS)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray, scale


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
def _join_words(words: List[str]) -> str:
    """日本語は単語間にスペースを入れず、英数字同士が隣り合う場合だけスペースを入れる。"""
    out = ""
    for w in words:
        if out and re.search(r"[A-Za-z0-9]$", out) and re.match(r"[A-Za-z0-9]", w):
            out += " "
        out += w
    return out


def _ocr_lines(gray: Image.Image, scale: float, lang: str, psm: int, min_conf: float) -> List[OcrLine]:
    import pytesseract

    data = pytesseract.image_to_data(
        gray, lang=lang, config=f"--oem 1 --psm {psm}", output_type=pytesseract.Output.DICT
    )
    groups: Dict[Tuple[int, int, int], list] = {}
    for i, raw in enumerate(data["text"]):
        txt = (raw or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < min_conf:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(
            (data["left"][i], data["top"][i], data["width"][i], data["height"][i], txt, conf)
        )

    lines: List[OcrLine] = []
    for words in groups.values():
        words.sort(key=lambda w: w[0])
        text = _join_words([w[4] for w in words])
        lines.append(
            OcrLine(
                text=text,
                left=min(w[0] for w in words) / scale,
                top=min(w[1] for w in words) / scale,
                right=max(w[0] + w[2] for w in words) / scale,
                bottom=max(w[1] + w[3] for w in words) / scale,
                conf=sum(w[5] for w in words) / len(words),
            )
        )
    return lines


def _overlap_ratio(a: OcrLine, b: OcrLine) -> float:
    """2つの行の重なり面積 / 小さい方の面積"""
    w = min(a.right, b.right) - max(a.left, b.left)
    h = min(a.bottom, b.bottom) - max(a.top, b.top)
    if w <= 0 or h <= 0:
        return 0.0
    area_a = (a.right - a.left) * (a.bottom - a.top)
    area_b = (b.right - b.left) * (b.bottom - b.top)
    return (w * h) / max(min(area_a, area_b), 1.0)


def _find_color_blocks(
    img: Image.Image, min_area_ratio: float = 0.003, max_area_ratio: float = 0.20, max_blocks: int = 40
) -> List[Tuple[int, int, int, int]]:
    """
    「彩度の高い塊(赤・黄・オレンジ等の価格札)」の外接矩形を元画像の座標で返す。

    なぜ必要か: チラシの価格は、色付きの札の中に巨大な文字で書かれていることが多く、
    Tesseractのページ全体OCRでは、この巨大な文字を「文字領域」と認識できずに
    丸ごと落としてしまう(縮小・二値化でも改善しない)。一方、札だけを切り出して
    読ませると正しく読めるため、札の候補領域を色から探して個別に読む。
    """
    import numpy as np

    small_w = 320
    s = small_w / img.width
    small = img.convert("RGB").resize((small_w, max(int(img.height * s), 1)), Image.BILINEAR)
    hsv = np.asarray(small.convert("HSV"))
    mask = (hsv[..., 1] > 110) & (hsv[..., 2] > 90)   # 彩度が高く、暗すぎない画素
    h, w = mask.shape

    seen = np.zeros_like(mask, dtype=bool)
    blocks = []
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            minx = maxx = x0
            miny = maxy = y0
            area = 0
            while stack:
                y, x = stack.pop()
                area += 1
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            bw, bh = maxx - minx + 1, maxy - miny + 1
            box_area = bw * bh
            if not (min_area_ratio <= box_area / (w * h) <= max_area_ratio):
                continue
            if area / box_area < 0.35:      # スカスカの領域(細い線・散らばった色)は除外
                continue
            if not (0.6 <= bw / bh <= 10):  # 極端に縦長・横長は除外
                continue
            blocks.append((minx, miny, maxx + 1, maxy + 1))

    blocks.sort(key=lambda b: (b[1], b[0]))
    scale_back = 1 / s
    return [
        (int(b[0] * scale_back), int(b[1] * scale_back), int(b[2] * scale_back), int(b[3] * scale_back))
        for b in blocks[:max_blocks]
    ]


def _ocr_price_blocks(img: Image.Image, lang: str) -> List[OcrLine]:
    """色付きの価格札候補を1枚ずつ切り出し、3種類の二値化で読んで、価格が読めたものを行として返す。"""
    import pytesseract

    gray_full = ImageOps.grayscale(img.convert("RGB"))
    lines: List[OcrLine] = []
    for (x0, y0, x1, y1) in _find_color_blocks(img):
        mx, my = int((x1 - x0) * 0.06), int((y1 - y0) * 0.06)
        box = (max(x0 - mx, 0), max(y0 - my, 0), min(x1 + mx, img.width), min(y1 + my, img.height))
        crop = gray_full.crop(box)
        # 文字の高さがTesseractの得意なサイズになるよう、札の高さ≒150pxに揃える
        f = min(max(150 / crop.height, 0.4), 4.0)
        crop = crop.resize((max(int(crop.width * f), 1), max(int(crop.height * f), 1)), Image.LANCZOS)
        crop = ImageOps.autocontrast(crop, cutoff=1)

        variants = [
            crop,
            crop.point(lambda p: 0 if p > 190 else 255),   # 白抜き文字 → 黒文字
            crop.point(lambda p: 0 if p < 120 else 255),   # 濃い文字 → 黒文字
        ]
        best_text = None
        for v in variants:
            for psm in (6, 7):
                try:
                    txt = pytesseract.image_to_string(v, lang=lang, config=f"--oem 1 --psm {psm}")
                except Exception:  # noqa: BLE001
                    continue
                t = normalize_text(" ".join(txt.split()))
                if _parse_price(t) is not None:
                    best_text = t
                    break
            if best_text:
                break
        if best_text:
            lines.append(OcrLine(best_text, box[0], box[1], box[2], box[3], conf=75.0))
    return lines


def run_ocr(
    img: Image.Image, lang: str = "jpn+eng", psm: int = 11, min_conf: float = 30.0, use_price_blocks: bool = True
) -> List[OcrLine]:
    """
    ページ全体のOCR(商品名・内容量・日付・時間帯向け) と、
    価格札の切り出しOCR(大きな価格向け) の結果を統合して、行のリストを返す。
    """
    gray, scale = preprocess(img)
    lines = _ocr_lines(gray, scale, lang, psm, min_conf)

    if use_price_blocks:
        for extra in _ocr_price_blocks(img, lang):
            # 価格札の中身をページ全体OCRが(部分的に)読んでいた場合は、札の結果で置き換える
            lines = [l for l in lines if _overlap_ratio(l, extra) < 0.5]
            lines.append(extra)

    lines.sort(key=lambda l: (round(l.top / 10), l.left))
    return lines


# ---------------------------------------------------------------------------
# テキスト正規化・パターン検出
# ---------------------------------------------------------------------------
_CJK = "぀-ヿ㐀-鿿"


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("〜", "~").replace("～", "~").replace("―", "-").replace("−", "-")
    # 日本語文字どうしの間に入りがちな空白を除去
    s = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", s)
    # よくある誤読の補正(「パック」の「パ」が「バパ」になる 等)
    s = s.replace("バパック", "パック").replace("バパッ", "パッ")
    return s.strip()


def _fix_price_digits(s: str) -> str:
    """「1O8円」「l98円」のような数字の誤読を、円の直前のトークンに限って補正する。"""

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if not any(c.isdigit() for c in tok):
            return tok
        return tok.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))

    return re.sub(r"[0-9OoIl|,]{2,}(?=\s*円)", repl, s)


_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,5})"
PRICE_RE = re.compile(rf"(?P<label>税込|本体|税抜)?\s*(?:¥\s*)?(?P<num>{_NUM})\s*円|¥\s*(?P<num2>{_NUM})")
QTY_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:kg|g|ml|mL|L|l|個|本|枚|袋|パック|パッ|入|錠|包|粒|缶|切|尾|玉|束|cm)"
    r"(?:入り?|当たり|あたり)?(?:\s*[x×]\s*\d+)?|\d+\s*[x×]\s*\d+"
)
_DATE_TOKEN = r"(?:\d{1,2}/\d{1,2}|\d{1,2}月\d{1,2}日)(?:\s*\([月火水木金土日]\))?"
DATE_RE = re.compile(rf"{_DATE_TOKEN}(?:\s*~\s*{_DATE_TOKEN})?")
TIME_RE = re.compile(r"\d{1,2}(?::\d{2}|時(?:\d{2}分?)?)\s*~\s*\d{1,2}(?::\d{2}|時(?:\d{2}分?)?)?")
TIME_KEYWORDS = ("タイムセール", "朝市", "夕市", "夕方", "午前", "午後")
NOISE_WORDS = (
    "税込", "本体", "税抜", "特価", "各", "円", "当たり", "あたり", "数量限定", "限定", "お1人様", "お一人様",
    "1点", "点限り", "まで", "から", "より", "税", "込",
)


def _strip_for_name(text: str) -> str:
    t = PRICE_RE.sub(" ", text)
    t = QTY_RE.sub(" ", t)
    t = DATE_RE.sub(" ", t)
    t = TIME_RE.sub(" ", t)
    for w in NOISE_WORDS + TIME_KEYWORDS:
        t = t.replace(w, " ")
    t = re.sub(r"[()（）\[\]【】「」『』※★☆●■◆▲▼◎○◇□▽△♪!?・:;,、。~\-_/\\|<>]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _has_word_chars(t: str, minimum: int = 2) -> bool:
    return len(re.findall(rf"[{_CJK}A-Za-z]", t)) >= minimum


def _parse_price(text: str) -> Optional[Tuple[int, str]]:
    """行の中の価格を1つ選ぶ(税込表記があれば優先)。戻り値: (円, 注記)"""
    found = []
    for m in PRICE_RE.finditer(_fix_price_digits(text)):
        num = m.group("num") or m.group("num2")
        value = int(num.replace(",", ""))
        if 1 <= value <= 99999:
            found.append((value, m.group("label") or ""))
    if not found:
        return None
    for value, label in found:
        if label == "税込":
            return value, label
    return found[0]


# ---------------------------------------------------------------------------
# 意味づけ(価格を起点に、商品名・内容量・日付・時間帯を紐づける)
# ---------------------------------------------------------------------------
def parse_items(
    lines: List[OcrLine], img_w: float, img_h: float, period_text: Optional[str] = None
) -> List[dict]:
    norm = [normalize_text(l.text) for l in lines]

    price_idx, name_idx, qty_only_idx, date_idx, time_idx = [], [], [], [], []
    for i, (ln, t) in enumerate(zip(lines, norm)):
        has_price = _parse_price(t) is not None
        if DATE_RE.search(t):
            date_idx.append(i)
        if TIME_RE.search(t) or any(k in t for k in TIME_KEYWORDS):
            time_idx.append(i)
        cleaned = _strip_for_name(t)
        if has_price:
            price_idx.append(i)
        elif _has_word_chars(cleaned):
            name_idx.append(i)
        elif QTY_RE.search(t):
            qty_only_idx.append(i)

    max_dy = max(img_h * 0.07, 60.0)    # 価格と商品名の縦方向の許容距離
    max_dx = max(img_w * 0.22, 80.0)    # 横方向の許容距離

    def nearest_name_above(pl: OcrLine, exclude: set) -> Optional[int]:
        best, best_score = None, None
        for j in name_idx:
            if j in exclude:
                continue
            c = lines[j]
            dy = pl.top - c.bottom
            dx = abs(pl.cx - c.cx)
            if dy < -pl.height * 0.5 or dy > max_dy or dx > max_dx:
                continue
            score = max(dy, 0) + 0.5 * dx
            if best_score is None or score < best_score:
                best, best_score = j, score
        return best

    items: List[dict] = []
    for i in price_idx:
        pl, ptext = lines[i], norm[i]
        price, price_note = _parse_price(ptext)

        used = {i}
        name_parts: List[str] = []
        own = _strip_for_name(ptext)
        if _has_word_chars(own, 3):
            name_parts.append(own)
        else:
            j = nearest_name_above(pl, used)
            if j is not None:
                used.add(j)
                name_parts.insert(0, _strip_for_name(norm[j]))
                # 商品名が2行に折り返されているケース: すぐ上の近接行も連結
                k = nearest_name_above(lines[j], used)
                if k is not None and (lines[j].top - lines[k].bottom) < lines[j].height * 1.2 \
                        and abs(lines[j].cx - lines[k].cx) < max_dx * 0.5:
                    used.add(k)
                    name_parts.insert(0, _strip_for_name(norm[k]))

        # 内容量: 価格行 → 商品名行 → 近くの内容量のみの行 の順に探す
        qty = None
        for idx in [i] + sorted(used - {i}):
            m = QTY_RE.search(norm[idx])
            if m:
                qty = m.group(0).replace(" ", "")
                break
        if qty is None:
            for q in qty_only_idx:
                c = lines[q]
                if abs(pl.cx - c.cx) <= max_dx and -pl.height <= (pl.top - c.bottom) <= max_dy:
                    qty = QTY_RE.search(norm[q]).group(0).replace(" ", "")
                    break

        # 日付: 価格より上にある最も近い日付行(ヘッダーの日付は下の全商品に効くため距離制限なし)
        date_txt = None
        for c_i in sorted(date_idx, key=lambda k: pl.top - lines[k].top):
            if pl.top - lines[c_i].bottom < -pl.height:
                continue  # 価格より下にある日付行は対象外
            date_txt = DATE_RE.search(norm[c_i]).group(0)
            break

        # 時間帯: タイムセール等の帯は、その近く(画像の高さの30%以内)の商品にだけ効く
        time_txt = None
        for c_i in sorted(time_idx, key=lambda k: pl.top - lines[k].top):
            dy = pl.top - lines[c_i].bottom
            if dy < -pl.height or dy > img_h * 0.30:
                continue
            tm = TIME_RE.search(norm[c_i])
            time_txt = tm.group(0) if tm else next((k for k in TIME_KEYWORDS if k in norm[c_i]), None)
            break

        conf_vals = [lines[k].conf for k in used]
        items.append(
            {
                "日付": date_txt or period_text,
                "時間帯": time_txt,
                "商品名": " ".join(p for p in name_parts if p) or None,
                "内容": qty,
                "価格": price,
                "価格注記": price_note or None,
                "信頼度": round(sum(conf_vals) / len(conf_vals), 1),
                "元テキスト": " / ".join(norm[k] for k in sorted(used)),
                "_top": pl.top,
                "_left": pl.left,
            }
        )

    # 商品名が見つからなかった価格行(例: 札の下に小さく書かれた「税込214円」)は、
    # 同じ列の直上にある「商品名つきの商品」の別価格として統合する。
    named = [it for it in items if it["商品名"]]
    orphans = [it for it in items if not it["商品名"]]
    for o in orphans:
        cands = [
            n for n in named
            if 0 < o["_top"] - n["_top"] <= img_h * 0.25 and abs(o["_left"] - n["_left"]) <= img_w * 0.25
        ]
        if not cands:
            continue
        target = min(cands, key=lambda n: o["_top"] - n["_top"])
        if o["価格注記"] == "税込" and target["価格注記"] != "税込":
            target["別価格"] = target["価格"]
            target["価格"], target["価格注記"] = o["価格"], "税込"
        elif o["価格"] != target["価格"]:
            target["別価格"] = o["価格"]
        target["元テキスト"] += " / " + o["元テキスト"]
        items.remove(o)

    # 同じ商品の「札の価格」と「税込価格の注記」が別々の行として読まれた場合に1件へ統合する。
    # 判定: 商品名が同じ、または商品名が未検出でも位置が近い(同じ列・縦に近接)。
    # 税込価格があればそれを「価格」にし、もう一方は「別価格」に残す。
    deduped: List[dict] = []
    for it in sorted(items, key=lambda d: (d["_top"], d["_left"])):
        dup = None
        for d in deduped:
            same_name = bool(d["商品名"]) and d["商品名"] == it["商品名"]
            close = (
                abs(d["_top"] - it["_top"]) < img_h * 0.15
                and abs(d["_left"] - it["_left"]) < img_w * 0.25
            )
            if same_name and close:
                dup = d
                break
        if dup is None:
            deduped.append(it)
            continue
        if it["価格注記"] == "税込" and dup["価格注記"] != "税込":
            it["別価格"] = dup["価格"]
            deduped[deduped.index(dup)] = it
        elif dup["価格"] != it["価格"]:
            dup["別価格"] = it["価格"]
    for d in deduped:
        d.pop("_top", None)
        d.pop("_left", None)
        d.setdefault("別価格", None)
    return deduped


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------
def extract_items(
    source,
    period_text: Optional[str] = None,
    lang: str = "jpn+eng",
    psm: int = 11,
    tesseract_cmd: Optional[str] = None,
    use_price_blocks: bool = True,
    return_meta: bool = False,
):
    """
    チラシ画像(URL/パス/PIL画像)から商品明細を抽出する。

    戻り値: (items, raw_lines)   ※ return_meta=True のときは (items, raw_lines, meta)
      items     : [{日付, 時間帯, 商品名, 内容, 価格, 価格注記, 信頼度, 元テキスト}, ...]
      raw_lines : OCRが読み取った生の行 [{テキスト, 信頼度, x, y}, ...]  (突き合わせ確認用)
      meta      : {"width": 画像の幅px, "height": 画像の高さpx, "low_resolution": bool}
    """
    configure_tesseract(tesseract_cmd, required_lang=lang.split("+")[0])
    img = load_image(source)
    lines = run_ocr(img, lang=lang, psm=psm, use_price_blocks=use_price_blocks)
    items = parse_items(lines, img.width, img.height, period_text)
    raw = [
        {"テキスト": normalize_text(l.text), "信頼度": round(l.conf, 1), "x": int(l.left), "y": int(l.top)}
        for l in lines
    ]
    if return_meta:
        meta = {"width": img.width, "height": img.height, "low_resolution": img.width < MIN_RECOMMENDED_WIDTH}
        return items, raw, meta
    return items, raw


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("使い方: python flyer_item_extractor.py <画像パスまたはURL>")
        raise SystemExit(1)
    items, raw, meta = extract_items(sys.argv[1], return_meta=True)
    print(f"--- 画像サイズ: {meta['width']}x{meta['height']}px ---")
    if meta["low_resolution"]:
        print(f"※ 幅が{MIN_RECOMMENDED_WIDTH}px未満です。解像度が低くOCR精度が出ない可能性が高いです。")
    print(f"--- 抽出できた商品: {len(items)} 件 ---")
    for it in items:
        print(it)
    print(f"\n--- OCRが読み取った生の行: {len(raw)} 行 ---")
    for r in raw:
        print(r)
