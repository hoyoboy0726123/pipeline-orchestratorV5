"""RapidOCR 引擎包裝層 — V5 OCR 引擎可選版本。

RapidOCR 是 PaddleOCR 訓練好的模型轉成 ONNX 版,只依賴 onnxruntime
(~50MB),沒有 PaddlePaddle 750MB 框架的肥胖、跟 paddle 3.3 PIR/oneDNN
bug 那些。Python 3.13 直接可用,不需要遷移 backend venv。

對中文整詞識別準確率跟 paddle 同級(同一套訓練好的模型),適合 Mica
半透明背景、小字、跨機台場景 — 對 Windows.Media.Ocr 在 < 150% scaling
下複雜中文字漏字的問題是直接解。

切換:
    export OCR_ENGINE=rapid    (linux/mac)
    $env:OCR_ENGINE = "rapid"  (powershell)
    SET OCR_ENGINE=rapid       (cmd)

安裝:
    pip install rapidocr onnxruntime
    # 可選:簡繁歸一化(RapidOCR 模型是簡體訓練、target 通常是繁體)
    pip install opencc-python-reimplemented

設計目標:
- 整個檔案 optional — rapidocr 沒裝就 graceful fail、不影響 V5 主流程
- Engine 全域 cache(每次新建模型載入要 5-10s)
- 回傳格式跟 Windows OCR _recognize() 同 schema: [{text, x, y, w, h, line_text, line_index, confidence}]
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


# 全域 cache:RapidOCR 第一次建要載模型 ~5-10s,不能每次呼叫都重建
_RAPID_OCR_INSTANCE = None
_RAPID_OCR_LOCK = threading.Lock()


def is_available() -> bool:
    """檢測 rapidocr 是不是裝得起來(不真的 init 引擎、那很慢)。"""
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_rapid_engine():
    """Lazy init 全域 RapidOCR instance。Thread-safe。"""
    global _RAPID_OCR_INSTANCE
    if _RAPID_OCR_INSTANCE is not None:
        return _RAPID_OCR_INSTANCE
    with _RAPID_OCR_LOCK:
        if _RAPID_OCR_INSTANCE is not None:
            return _RAPID_OCR_INSTANCE
        log.info("[ocr_rapid] 首次初始化 RapidOCR、載入 ONNX 模型 (~5-10s)...")
        from rapidocr import RapidOCR
        _RAPID_OCR_INSTANCE = RapidOCR()
        log.info("[ocr_rapid] RapidOCR ready")
    return _RAPID_OCR_INSTANCE


def recognize_rapid(img_bgr: np.ndarray, lang_tag: Optional[str] = None) -> list[dict]:
    """跑 RapidOCR、回傳跟 Windows OCR _recognize() 同樣的 list[dict] schema。

    lang_tag 參數忽略(RapidOCR 模型 ch 多語通用、不挑語言)。
    RapidOCR 每個 detected region 直接是整詞、不像 Win OCR 把 CJK 拆單字,
    所以 line_text 跟 text 一樣,line_index 用 detection 順序當 index。
    """
    engine = _get_rapid_engine()
    result = engine(img_bgr)

    items: list[dict] = []

    # RapidOCR 新版 (>= 3.x):RapidOCROutput 物件,有 .boxes / .txts / .scores
    # RapidOCR 舊版 (rapidocr-onnxruntime ~1.x):回 (list[(box, text, score)], info_dict)
    try:
        if hasattr(result, "txts"):
            # 新版
            texts = getattr(result, "txts", []) or []
            scores = getattr(result, "scores", []) or []
            boxes = getattr(result, "boxes", []) or []
            for i, text in enumerate(texts):
                if not text:
                    continue
                conf = float(scores[i]) if i < len(scores) else 0.0
                if i < len(boxes):
                    box = boxes[i]
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    x, y = int(min(xs)), int(min(ys))
                    w, h = int(max(xs) - x), int(max(ys) - y)
                else:
                    x = y = w = h = 0
                items.append({
                    "text": text,
                    "x": x, "y": y, "w": w, "h": h,
                    "line_text": text,
                    "line_index": i,
                    "confidence": conf,
                })
        elif isinstance(result, tuple) and len(result) >= 1 and result[0]:
            # 舊版
            for i, line in enumerate(result[0]):
                if not line or len(line) < 3:
                    continue
                box, text, conf = line[0], line[1], line[2]
                if not text:
                    continue
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                items.append({
                    "text": text,
                    "x": x, "y": y, "w": w, "h": h,
                    "line_text": text,
                    "line_index": i,
                    "confidence": float(conf),
                })
    except Exception as e:
        log.warning(f"[ocr_rapid] 解析 result 結構失敗:{type(e).__name__}: {e}")
        log.warning(f"[ocr_rapid] 原始 result type={type(result)} 前 500 字:{str(result)[:500]}")

    return items
