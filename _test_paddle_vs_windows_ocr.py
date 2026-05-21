"""PaddleOCR vs Windows.Media.Ocr 對 Start Menu 的識別能力比較

跑法 (D: 端):
    cd D:\\Atlas\\pipeline-orchestratorV5
    # 第一次跑前先安裝 paddleocr (~5-10 分鐘、會下載 ~750MB 框架 + 模型)
    & "backend\\.venv\\Scripts\\python.exe" -m pip install paddleocr
    # 安裝完跑這個
    & "backend\\.venv\\Scripts\\python.exe" _test_paddle_vs_windows_ocr.py

跑的時候會 10 秒倒數、你按 Win 鍵打開 Start Menu 別動、script 自動 capture + 跑兩個 OCR 比較。

目的:確認 PaddleOCR 是不是真的能讀整詞「時鐘 / 記事本 / Outlook」、
     而不像 Windows OCR 拆成單字或漏字。確認可行才花時間整合進 V5。
"""
import os

# paddlepaddle 3.x 引入 PIR (Paddle Intermediate Representation) 新執行器,
# 跟 oneDNN 組合時某些 attribute 還沒實作 (ConvertPirAttribute2RuntimeAttribute
# not support pir::ArrayAttribute<pir::DoubleAttribute>)。要關掉的是 PIR、
# 而不是只關 mkldnn。三條同時設,確保走 legacy executor。
os.environ["FLAGS_enable_pir_in_executor"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"

import asyncio
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np


def _set_dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            u32 = ctypes.windll.user32
            u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
            u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass


def capture_screen() -> np.ndarray:
    print("=" * 70)
    print("PaddleOCR vs Windows.Media.Ocr 比較測試")
    print("=" * 70)
    print()
    print("步驟:")
    print("  1. 等下倒數開始,按 Win 鍵打開 Start Menu")
    print("  2. 確認 Start Menu 真的彈出來、不要動滑鼠")
    print("  3. 10 秒後自動截圖")
    print()
    for i in range(10, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)

    print("\n>> 截圖")
    with mss.mss() as sct:
        mon = sct.monitors[0]
        img = np.array(sct.grab(mon))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    out_png = Path(__file__).with_name("_paddle_test_capture.png")
    cv2.imwrite(str(out_png), bgr)
    print(f"截圖存到 {out_png}  尺寸: {bgr.shape[1]}x{bgr.shape[0]}")
    return bgr


def run_windows_ocr(bgr: np.ndarray) -> list[dict]:
    """跑 Windows.Media.Ocr。回傳 [{text, x, y, w, h}]。

    繞過 V5 backend (pipeline.ocr 會把整串 telegram/runner 都拖進來),
    直接呼叫底層 winrt API,避免 .venv_paddle 沒裝 V5 backend deps 時 crash。
    """
    try:
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    except ImportError as e:
        print(f"  [skip] winrt 模組沒裝在這個 venv (預期): {e}")
        return []

    async def _do():
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            return []
        png_bytes = bytes(buf.tobytes())
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        # fuzzy match zh-Hant-TW → zh-TW etc.
        engine = None
        for tag in ("zh-Hant-TW", "zh-TW", "zh-Hant"):
            try:
                engine = OcrEngine.try_create_from_language(Language(tag))
                if engine:
                    break
            except Exception:
                pass
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return []

        result = await engine.recognize_async(bitmap)
        items: list[dict] = []
        for i, line in enumerate(result.lines):
            for word in line.words:
                r = word.bounding_rect
                items.append({
                    "text": word.text or "",
                    "x": int(r.x), "y": int(r.y),
                    "w": int(r.width), "h": int(r.height),
                    "line_index": i,
                    "line_text": line.text or "",
                })
        return items

    return asyncio.run(_do())


def run_paddle_ocr(bgr: np.ndarray) -> list[dict]:
    """跑 PaddleOCR。轉成同樣 [{text, x, y, w, h}] schema 方便比對。
    第一次跑會 lazy download 模型(~250MB)、可能要 1-3 分鐘。"""
    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        print(f"\n[X] paddleocr import 失敗: {type(e).__name__}")
        print(f"    錯誤訊息: {e}")
        print("\n完整 traceback:")
        import traceback
        traceback.print_exc()
        print("\n額外診斷:嘗試 import paddle 看是否 paddle 本體有問題...")
        try:
            import paddle
            print(f"  paddle 可 import,版本: {paddle.__version__}")
        except Exception as e2:
            print(f"  paddle import 也失敗: {type(e2).__name__}: {e2}")
            traceback.print_exc()
        sys.exit(1)

    print("\n初始化 PaddleOCR (首次會下載模型、可能等 1-3 分鐘)...")
    # paddleocr 3.x 把舊參數刪掉了,用最小設定:lang="ch" 繁簡通吃
    ocr = PaddleOCR(lang="ch")
    print("PaddleOCR ready")

    print("跑 OCR...")
    t0 = time.time()
    # 新版用 .predict(),舊版用 .ocr() — 兩個都試
    if hasattr(ocr, "predict"):
        result = ocr.predict(bgr)
    else:
        result = ocr.ocr(bgr)
    print(f"PaddleOCR 完成 ({time.time() - t0:.1f}s)")

    # 結果格式新舊不同,做兩種解析嘗試
    items: list[dict] = []
    try:
        # 新版 (paddleocr 3.x):result 是 list[dict],每個 dict 有 'rec_texts'、'rec_scores'、'rec_polys'
        if result and isinstance(result, list) and len(result) > 0:
            first = result[0]
            if isinstance(first, dict) and "rec_texts" in first:
                texts = first.get("rec_texts", [])
                scores = first.get("rec_scores", [])
                polys = first.get("rec_polys") or first.get("dt_polys", [])
                for i, text in enumerate(texts):
                    conf = float(scores[i]) if i < len(scores) else 0.0
                    if i < len(polys):
                        poly = polys[i]
                        xs = [float(p[0]) for p in poly]
                        ys = [float(p[1]) for p in poly]
                        x = int(min(xs)); y = int(min(ys))
                        w = int(max(xs) - x); h = int(max(ys) - y)
                    else:
                        x = y = w = h = 0
                    items.append({"text": text, "x": x, "y": y, "w": w, "h": h, "confidence": conf})
            elif isinstance(first, list):
                # 舊版格式:[[box, (text, conf)], ...]
                for line in first:
                    if not line: continue
                    box, (text, conf) = line[0], line[1]
                    xs = [p[0] for p in box]; ys = [p[1] for p in box]
                    x = int(min(xs)); y = int(min(ys))
                    w = int(max(xs) - x); h = int(max(ys) - y)
                    items.append({"text": text, "x": x, "y": y, "w": w, "h": h, "confidence": conf})
    except Exception as e:
        print(f"[!] 解析 paddle 結果失敗: {e}")
        print(f"[!] 原始結果類型: {type(result)}, 內容前 500 字: {str(result)[:500]}")
    return items


def compare(targets: list[str], words: list[dict], engine: str) -> None:
    print(f"\n== {engine} - 整詞匹配(target 在某個 word.text 裡) ==")
    for t in targets:
        hits = [w["text"] for w in words if t in w["text"]]
        status = "OK " if hits else "X  "
        print(f"  {status} '{t}': {hits if hits else '(無整詞)'}")


def main() -> None:
    _set_dpi_aware()
    bgr = capture_screen()

    print("\n" + "=" * 70)
    print("Round 1: Windows.Media.Ocr (V5 當前用的)")
    print("=" * 70)
    t0 = time.time()
    try:
        win_words = run_windows_ocr(bgr)
        print(f"Windows OCR 完成 ({time.time() - t0:.1f}s),共 {len(win_words)} 個 word")
    except Exception as e:
        print(f"Windows OCR 失敗: {e}")
        win_words = []

    print("\n" + "=" * 70)
    print("Round 2: PaddleOCR")
    print("=" * 70)
    try:
        paddle_words = run_paddle_ocr(bgr)
        print(f"PaddleOCR 完成,共 {len(paddle_words)} 個 word/line")
    except Exception as e:
        print(f"PaddleOCR 失敗: {e}")
        import traceback
        traceback.print_exc()
        paddle_words = []

    targets_zh = ["時鐘", "記事本", "設定", "相片", "小算盤", "小畫家", "剪取工具", "檔案總管"]
    targets_en = ["Microsoft Edge", "Outlook", "Microsoft 365", "Microsoft Store", "Copilot"]

    print("\n" + "=" * 70)
    print("結果對比")
    print("=" * 70)

    if win_words:
        compare(targets_zh, win_words, "Windows OCR (中文)")
        compare(targets_en, win_words, "Windows OCR (英文)")

    if paddle_words:
        compare(targets_zh, paddle_words, "PaddleOCR (中文)")
        compare(targets_en, paddle_words, "PaddleOCR (英文)")

    if paddle_words:
        print("\n== PaddleOCR 讀到的所有文字(前 50 個) ==")
        for w in paddle_words[:50]:
            print(f"  conf={w.get('confidence', 0):.2f}  text={w['text']!r}")

    print("\n" + "=" * 70)
    print("判讀:")
    print(" - PaddleOCR 對中文 Start Menu 都能整詞讀 → 值得整合進 V5")
    print(" - PaddleOCR 也讀不到 Outlook / 時鐘 → 是 Mica 透明背景本身的硬傷,換引擎也救不了")
    print("=" * 70)


if __name__ == "__main__":
    main()
