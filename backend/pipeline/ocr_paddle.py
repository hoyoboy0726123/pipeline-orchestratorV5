"""PaddleOCR 引擎包裝層 — 對外 API 跟 `ocr.py` 的 _recognize() 完全一致。

設計目標:
- 整個檔案是「optional」— paddleocr 沒裝就 graceful fail、不影響 V5 主流程
- import 延遲(paddle 首次 import 30s+、不該在 backend 啟動時就吃這個成本)
- 全域 cache OcrEngine instance(每次新建會重 load 模型、太慢)
- 回傳格式跟 Windows OCR 路徑同樣的 [{text, x, y, w, h, line_text, line_index}]

切換方式:
- 環境變數 OCR_ENGINE=paddle 啟動 backend
- 或 backend settings 加 ocr_engine 欄位(後續整合)
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# paddlepaddle 3.3.x 有 PIR + oneDNN attribute 轉換 bug (Paddle issue #77340),
# 官方建議降到 3.2.2 規避。若使用者裝到 3.3.x、這幾個 env var 嘗試走 legacy executor 救。
# 必須在 import paddle 之前設定。
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

# 全域 cache:PaddleOCR 實例第一次建要 30s+(載多個模型),不能每次呼叫都重建
_PADDLE_OCR_INSTANCE = None
_PADDLE_OCR_LOCK = threading.Lock()


def is_available() -> bool:
    """檢測 paddleocr 是不是裝得起來(不要真的 init engine、那太慢)。"""
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_paddle_engine():
    """Lazy init 全域 PaddleOCR instance。Thread-safe。"""
    global _PADDLE_OCR_INSTANCE
    if _PADDLE_OCR_INSTANCE is not None:
        return _PADDLE_OCR_INSTANCE
    with _PADDLE_OCR_LOCK:
        if _PADDLE_OCR_INSTANCE is not None:
            return _PADDLE_OCR_INSTANCE
        log.info("[ocr_paddle] 首次初始化 PaddleOCR — 載入模型可能花 30-60 秒...")
        from paddleocr import PaddleOCR
        # lang='ch' = 簡繁通吃中文模型;不開角度分類(Start Menu / UI 不會旋轉),省時間
        _PADDLE_OCR_INSTANCE = PaddleOCR(lang="ch")
        log.info("[ocr_paddle] PaddleOCR ready")
    return _PADDLE_OCR_INSTANCE


def recognize_paddle(img_bgr: np.ndarray, lang_tag: Optional[str] = None) -> list[dict]:
    """跑 PaddleOCR、回傳跟 Windows OCR _recognize() 同樣的 list[dict] 格式。

    PaddleOCR 的 line 概念跟 Windows OCR 不同:它每個 detected text region 直接是
    一個獨立 line/word(整詞、不像 Win OCR 把 CJK 拆單字),所以 line_index 用
    它自己的順序 index,text 跟 line_text 一樣(沒有「word 屬於某 line」概念)。
    """
    ocr = _get_paddle_engine()

    # 新版 PaddleOCR 3.x 用 .predict(),舊版 2.x 用 .ocr()
    if hasattr(ocr, "predict"):
        result = ocr.predict(img_bgr)
    else:
        result = ocr.ocr(img_bgr)

    items: list[dict] = []

    if not result:
        return items

    try:
        first = result[0] if isinstance(result, list) else result
        # paddleocr 3.x 新格式:dict with rec_texts / rec_scores / rec_polys
        if isinstance(first, dict) and "rec_texts" in first:
            texts = first.get("rec_texts", [])
            scores = first.get("rec_scores", [])
            polys = first.get("rec_polys") or first.get("dt_polys", [])
            for i, text in enumerate(texts):
                if not text:
                    continue
                conf = float(scores[i]) if i < len(scores) else 0.0
                if i < len(polys):
                    poly = polys[i]
                    xs = [float(p[0]) for p in poly]
                    ys = [float(p[1]) for p in poly]
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
        # paddleocr 2.x 舊格式:list[ [box, (text, conf)] ]
        elif isinstance(first, list):
            for i, line in enumerate(first):
                if not line or len(line) < 2:
                    continue
                box, payload = line[0], line[1]
                text, conf = (payload[0], payload[1]) if isinstance(payload, (tuple, list)) else (str(payload), 0.0)
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
        log.warning(f"[ocr_paddle] 解析 result 結構失敗:{type(e).__name__}: {e}")
        log.warning(f"[ocr_paddle] 原始 result type={type(result)} 前 500 字:{str(result)[:500]}")

    return items
