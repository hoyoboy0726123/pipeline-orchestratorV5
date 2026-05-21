"""mss 截圖 + Windows OCR 一站式診斷

跑法:
    cd D:\\Atlas\\pipeline-orchestratorV5
    & "backend\\.venv\\Scripts\\python.exe" _test_capture_and_ocr.py

跑之前手動把 Start Menu 打開、保持在前景、滑鼠別亂動。
跑完看:
    1. _test_capture.png — 裡面 Start Menu 在不在?
    2. console 印出的 OCR 結果 — 「時鐘 / 記事本 / 設定」有沒有被讀到?
"""
import asyncio
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np


def main() -> None:
    # 強制 DPI-aware 跟 backend 對齊(以免這支 script 是 unaware 抓到邏輯像素影像)
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass

    print("=" * 60)
    print("mss + Windows OCR 一站式診斷")
    print("=" * 60)
    print()
    print("3 秒後抓圖。請現在按 Win 鍵打開 Start Menu、放著別動。")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    # 抓圖
    with mss.mss() as sct:
        mon = sct.monitors[0]
        img = np.array(sct.grab(mon))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    out_png = Path(__file__).with_name("_test_capture.png")
    cv2.imwrite(str(out_png), bgr)
    print(f"\n截圖存到: {out_png}")
    print(f"  尺寸: {bgr.shape[1]}x{bgr.shape[0]} (W x H)")
    print(f"  虛擬桌面範圍: left={mon['left']} top={mon['top']} w={mon['width']} h={mon['height']}")

    # OCR — 直接 import backend 的 ocr 模組(已修好 zh-Hant-TW fuzzy fallback)
    print("\n跑 Windows OCR (zh-Hant-TW)...")
    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    try:
        from pipeline.ocr import _recognize
    except Exception as e:
        print(f"!! 載入 pipeline.ocr 失敗: {e}")
        return

    try:
        words = asyncio.run(_recognize(bgr, "zh-Hant-TW"))
    except Exception as e:
        print(f"!! OCR 跑掛: {e}")
        return

    print(f"OCR 讀到 {len(words)} 個詞")

    # 鎖定目標詞做匹配
    print("\n關鍵 Start Menu 詞匹配:")
    targets = ["時鐘", "記事本", "Microsoft", "Outlook", "設定", "Edge", "Copilot", "小算盤"]
    for t in targets:
        hits = [w["text"] for w in words if w["text"] and (t in w["text"] or w["text"] in t)]
        status = "✓" if hits else "✗"
        print(f"  {status} '{t}': {hits if hits else '(無)'}")

    # 整段印出來方便看
    print("\n所有 OCR 讀到的非空文字 (順序依 OCR 引擎):")
    texts = [w["text"] for w in words if w["text"].strip()]
    # 每 8 個一行
    for i in range(0, len(texts), 8):
        print("  " + " | ".join(texts[i:i + 8]))

    print()
    print("=" * 60)
    print("判讀:")
    print(" 1. 開 _test_capture.png 看 Start Menu 在不在裡面")
    print(" 2. 上面「✓ '時鐘'」有沒有命中")
    print(" 3. 兩個都對 → 執行時是時機問題(workflow 跑時 Start Menu 已關)")
    print(" 4. PNG 有 Start Menu 但 OCR 沒命中 → OCR 讀不出 Mica 透明上的中文")
    print(" 5. PNG 沒 Start Menu → mss 對 Win 11 Start Menu 抓取失敗(已知問題)")
    print("=" * 60)


if __name__ == "__main__":
    main()
