"""檔案 OCR — 直接對圖檔 / PDF 做辨識,不經過螢幕。

為什麼要有這支(而不是叫使用者把檔案打開再用螢幕 OCR):
  · 不必先把檔案開起來、捲到正確位置,少一堆易碎的 GUI 步驟
  · 不受螢幕解析度、視窗縮放、其他視窗遮擋影響
  · 原檔解析度通常遠高於螢幕呈現 → 辨識更準(PDF 可放大再 render)

主要用途:發票 / 憑證 / 單據抓欄位值(找「總計金額」旁邊的數字)。

對外:
  ocr_file(path)                 → 所有詞 [{text,x,y,w,h,page,...}]
  read_field(words, label, ...)  → 指定標籤旁邊的值
  read_fields(path, spec)        → 一次抓多個欄位(只跑一次 OCR)
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# PDF render 倍率:1 倍常糊到小字辨識不出,2 倍是準確度/耗時的平衡點
PDF_RENDER_SCALE = 2.0
MAX_PDF_PAGES = 20

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

# 金額:允許千分位、小數、前後貨幣符號
AMOUNT_RE = re.compile(r"^[$NT￥¥元\s]*-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[元]?$")
# 統編 / 單號:英數混合、不含空白
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{3,}$")
_HAS_DIGIT = re.compile(r"\d")


def _pages_as_bgr(path: Path) -> list[Any]:
    """把檔案轉成 list[np.ndarray(BGR)]。圖檔 1 張、PDF 每頁 1 張。"""
    import cv2
    import numpy as np

    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        # 用 imdecode 不用 imread —— imread 遇到非 ASCII 路徑(中文檔名)在 Windows 回 None
        buf = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"讀不出影像:{path}")
        return [img]

    if ext == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            out = []
            for i in range(min(len(pdf), MAX_PDF_PAGES)):
                page = pdf[i]
                try:
                    pil = page.render(scale=PDF_RENDER_SCALE).to_pil().convert("RGB")
                    out.append(cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
                finally:
                    page.close()
            return out
        finally:
            # 不 close 的話 Windows 會 hold file handle、後續刪檔失敗(沿用 file_preview 的教訓)
            pdf.close()

    raise ValueError(f"不支援的副檔名 {ext}（支援 {sorted(IMAGE_EXTS)} 與 .pdf）")


def ocr_file(path: str | Path, lang_tag: Optional[str] = "zh-Hant-TW") -> list[dict]:
    """對檔案做 OCR、回所有詞。每個詞多帶 page 欄位(從 1 起算)。"""
    from pipeline.ocr import _recognize

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"檔案不存在:{p}")

    words: list[dict] = []
    for idx, img in enumerate(_pages_as_bgr(p), start=1):
        try:
            page_words = asyncio.run(_recognize(img, lang_tag))
        except RuntimeError as e:
            # 已有 event loop 的 thread 不能 asyncio.run(沿用 ocr.py 的處理)
            if "running event loop" not in str(e).lower():
                raise
            loop = asyncio.new_event_loop()
            try:
                page_words = loop.run_until_complete(_recognize(img, lang_tag))
            finally:
                loop.close()
        for w in page_words:
            w["page"] = idx
        words.extend(page_words)
    log.info(f"[ocr_file] {p.name}:{len(words)} 個詞")
    return words


def _norm(s: str) -> str:
    from pipeline.ocr import _normalize_cjk
    return _normalize_cjk(s or "")


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """兩區間重疊比例(以較短者為分母)。"""
    lo, hi = max(a0, b0), min(a1, b1)
    return 0.0 if hi <= lo else (hi - lo) / max(1, min(a1 - a0, b1 - b0))


def find_label(words: list[dict], label: str, min_score: float = 0.6):
    """模糊找標籤框。回 (word, score) 或 None。

    ⚠ 一定要走 _normalize_cjk:OCR 模型輸出簡繁混雜(實測「總計金額」→「總計金额」),
      直接 == 比對永遠比不到。
    """
    tgt = _norm(label)
    if not tgt:
        return None
    best, best_s = None, 0.0
    for w in words:
        t = _norm(w.get("text", ""))
        if not t:
            continue
        if t == tgt:
            s = 1.0
        elif tgt in t:
            s = 0.9
        elif tgt.startswith(t) or tgt.endswith(t):
            # OCR 常漏字(實測「賣方統編」被讀成「賣方統」)。前後綴命中是強證據,
            # 分數要比「零散字元命中」高,否則漏一個字就抓不到標籤。
            s = 0.9 * len(t) / len(tgt)
        elif t in tgt:
            s = 0.75 * len(t) / len(tgt)
        else:
            s = 0.7 * sum(1 for c in tgt if c in t) / len(tgt)
        if s > best_s:
            best, best_s = w, s
    return (best, best_s) if best_s >= min_score else None


def read_field(words: list[dict], label: str, direction: str = "right",
               value_re: Optional[re.Pattern] = AMOUNT_RE,
               max_gap: int = 600, min_overlap: float = 0.3) -> Optional[dict]:
    """找 label 旁邊的值。

    direction: right(同列右側,最常見) / below(表格欄位、標題在上) / auto
    value_re : 值的格式約束。None = 只要含數字就算。**強烈建議給** ——
               否則右邊的日期、單號都可能被誤抓,而金額抓錯比抓不到嚴重得多。
    回 dict 或 None(抓不到就是 None,不猜)。
    """
    hit = find_label(words, label)
    if not hit:
        return None
    lw, lscore = hit
    page = lw.get("page", 1)
    l_top, l_bot = lw["y"], lw["y"] + lw["h"]
    l_left, l_right = lw["x"], lw["x"] + lw["w"]
    l_cx = l_left + lw["w"] / 2

    def usable(w: dict) -> bool:
        t = (w.get("text") or "").strip()
        if w is lw or not t or w.get("page", 1) != page:
            return False
        if value_re is not None:
            return bool(value_re.match(t))
        return bool(_HAS_DIGIT.search(t))

    cands: list[tuple[float, str, dict]] = []
    for d in (("right", "below") if direction == "auto" else (direction,)):
        for w in words:
            if not usable(w):
                continue
            if d == "right":
                if _overlap(l_top, l_bot, w["y"], w["y"] + w["h"]) < min_overlap:
                    continue
                gap = w["x"] - l_right
            else:
                w_l, w_r = w["x"], w["x"] + w["w"]
                if (_overlap(l_left, l_right, w_l, w_r) < min_overlap
                        and abs((w_l + w["w"] / 2) - l_cx) > max(60, lw["w"])):
                    continue
                gap = w["y"] - l_bot
            if 0 <= gap <= max_gap:
                cands.append((gap, d, w))
        if cands:
            break
    if not cands:
        return None
    gap, d, w = min(cands, key=lambda c: c[0])
    return {"label_text": lw.get("text", ""), "label_score": round(lscore, 2),
            "value": (w.get("text") or "").strip(), "distance": gap,
            "direction": d, "page": page,
            "value_box": (w["x"], w["y"], w["w"], w["h"])}


def read_fields(path: str | Path, spec: dict[str, Any],
                lang_tag: Optional[str] = "zh-Hant-TW") -> dict[str, Optional[str]]:
    """一次抓多個欄位。只跑一次 OCR、比逐欄呼叫快很多。

    spec 兩種寫法:
        {"金額": "總計金額"}                               # 預設 right + 金額格式
        {"數量": {"label": "數量", "direction": "below"}}   # 完整指定
    回 {欄位名: 值或 None}。抓不到就是 None —— 不猜、不亂填。
    """
    words = ocr_file(path, lang_tag)
    out: dict[str, Optional[str]] = {}
    for key, cfg in spec.items():
        if isinstance(cfg, str):
            cfg = {"label": cfg}
        r = read_field(words, cfg.get("label", key),
                       direction=cfg.get("direction", "right"),
                       value_re=cfg.get("value_re", AMOUNT_RE),
                       max_gap=cfg.get("max_gap", 600))
        out[key] = r["value"] if r else None
    return out


def to_number(s: Optional[str]) -> Optional[float]:
    """'38,500' / 'NT$ 40,425 元' → 40425.0。解析不出回 None。

    ⚠ 只適用金額。拿去解單號會抓到錯的片段(例 'AB-12345678' → -123)。
    """
    if not s:
        return None
    m = re.search(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", s)
    return float(m.group(0).replace(",", "")) if m else None
