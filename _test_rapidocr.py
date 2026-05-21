"""RapidOCR vs Windows OCR 對 Start Menu 中文整詞識別比較。

RapidOCR 是 PaddleOCR 模型轉 ONNX 版,依賴只有 onnxruntime ~50MB,跟
paddlepaddle 750MB 的肥胖框架完全不同。Python 3.13 可直接用、不需遷移 venv。

跑法 (D: 125% scaling、Start Menu 打開狀態):
    cd D:\\Atlas\\pipeline-orchestratorV5
    # 裝 rapidocr (~100MB、首次跑下模型再 ~50MB)
    & "backend\\.venv\\Scripts\\python.exe" -m pip install rapidocr onnxruntime
    # 跑測試
    & "backend\\.venv\\Scripts\\python.exe" _test_rapidocr.py

預期:RapidOCR 對「檔案總管」「時鐘」「記事本」全部整詞命中。
"""
import asyncio
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        u32 = ctypes.windll.user32
        u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    print("=" * 72)
    print("RapidOCR vs Windows.Media.Ocr 比較")
    print("=" * 72)
    print()
    print("按 Win 鍵打開 Start Menu、放著別動。10 秒倒數...\n")
    for i in range(10, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)

    with mss.mss() as sct:
        img = np.array(sct.grab(sct.monitors[0]))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    cv2.imwrite("_test_rapidocr_capture.png", bgr)
    print(f"\n截圖: {bgr.shape[1]}x{bgr.shape[0]}\n")

    # === Round 1: Windows OCR (V5 現用)
    print("=" * 72)
    print("Round 1: Windows.Media.Ocr")
    print("=" * 72)
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from pipeline.ocr import _recognize
    t0 = time.time()
    win_words = asyncio.run(_recognize(bgr, "zh-Hant-TW"))
    t_win = time.time() - t0
    print(f"耗時 {t_win:.2f}s, 讀到 {len(win_words)} 個 word")

    # === Round 2: RapidOCR
    print("\n" + "=" * 72)
    print("Round 2: RapidOCR (ONNX)")
    print("=" * 72)
    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        print(f"[X] rapidocr 沒裝: {e}")
        print('    先跑: & "backend\\.venv\\Scripts\\python.exe" -m pip install rapidocr onnxruntime')
        return

    print("初始化 RapidOCR (首次會下載 ONNX 模型 ~50MB)...")
    engine = RapidOCR()
    print("跑 OCR...")
    t0 = time.time()
    result = engine(bgr)
    t_rapid = time.time() - t0
    print(f"耗時 {t_rapid:.2f}s")

    # RapidOCR 結果格式 (新版): RapidOCROutput 物件,有 .boxes / .txts / .scores
    # 舊版 (rapidocr-onnxruntime): 回 (list_of_results, time_info)
    rapid_items: list[dict] = []
    try:
        if hasattr(result, "txts"):
            # 新版
            for i, txt in enumerate(result.txts):
                if not txt:
                    continue
                conf = float(result.scores[i]) if i < len(result.scores) else 0.0
                box = result.boxes[i] if i < len(result.boxes) else None
                if box is not None:
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    x, y = int(min(xs)), int(min(ys))
                    w, h = int(max(xs) - x), int(max(ys) - y)
                else:
                    x = y = w = h = 0
                rapid_items.append({"text": txt, "x": x, "y": y, "w": w, "h": h, "confidence": conf})
        elif isinstance(result, tuple) and len(result) >= 1 and result[0]:
            # 舊版 (box, text, score) tuple list
            for line in result[0]:
                box, text, conf = line[0], line[1], line[2]
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                rapid_items.append({"text": text, "x": x, "y": y, "w": w, "h": h, "confidence": float(conf)})
    except Exception as e:
        print(f"[!] 解析 RapidOCR 結果失敗: {e}")
        print(f"[!] 原始結果: type={type(result)}, 前 300 字: {str(result)[:300]}")

    print(f"RapidOCR 讀到 {len(rapid_items)} 個 word/line")

    # === 比對
    targets_zh = ["檔案總管", "時鐘", "記事本", "設定", "相片", "小算盤", "小畫家", "剪取工具"]
    targets_en = ["Microsoft Edge", "Outlook", "Microsoft 365", "Microsoft Store", "Copilot"]

    def hits(words: list[dict], t: str) -> list[str]:
        return [w["text"] for w in words if t in w["text"]]

    print("\n" + "=" * 72)
    print("整詞匹配比對")
    print("=" * 72)
    print(f"{'目標':<20} {'Windows OCR':<14} {'RapidOCR':<14}")
    print("-" * 72)
    win_count = 0
    rapid_count = 0
    for t in targets_zh + targets_en:
        h_win = hits(win_words, t)
        h_rapid = hits(rapid_items, t)
        s_win = "OK" if h_win else "X"
        s_rapid = "OK" if h_rapid else "X"
        marker = " ★" if not h_win and h_rapid else ""
        print(f"  {t:<18} {s_win:<14} {s_rapid:<14}{marker}")
        if h_win:
            win_count += 1
        if h_rapid:
            rapid_count += 1

    print("\n" + "=" * 72)
    print("結論")
    print("=" * 72)
    print(f"  Windows OCR: {win_count}/13 整詞命中, 耗時 {t_win:.2f}s")
    print(f"  RapidOCR:    {rapid_count}/13 整詞命中, 耗時 {t_rapid:.2f}s ({t_rapid/t_win:.1f}x 慢)")
    if rapid_count > win_count + 3:
        print(f"\n→ RapidOCR 大勝、值得整合進 V5 (依賴 ~100MB、無 paddle 框架)")
    elif rapid_count > win_count:
        print(f"\n→ RapidOCR 有改善、但提升幅度小")
    else:
        print(f"\n→ RapidOCR 沒比 Windows OCR 好,可能 Mica 對任何 CPU OCR 都難搞")

    # 顯示 RapidOCR 讀到的中文(前 30 個)
    if rapid_items:
        print("\nRapidOCR 讀到的中文 (前 30 個):")
        zh_items = [w for w in rapid_items if any('一' <= c <= '鿿' for c in w["text"])][:30]
        for w in zh_items:
            print(f"  conf={w.get('confidence', 0):.2f}  text={w['text']!r}")


if __name__ == "__main__":
    main()
