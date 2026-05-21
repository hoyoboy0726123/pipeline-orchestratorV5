"""驗證 2x upscale (Lanczos) 對 Windows OCR Chinese Start Menu 識別率的影響。

用法 (在 D: 125% scaling 下、Start Menu 打開狀態跑):
    cd D:\\Atlas\\pipeline-orchestratorV5
    & "backend\\.venv\\Scripts\\python.exe" _test_ocr_upscale.py

輸出 side-by-side 比較:
  原圖 OCR(現行 V5 行為)
  vs
  2x Lanczos upscale OCR(新預處理)

對 12 個 Start Menu 中文目標逐一檢查命中率,看上採樣能不能救回複雜字。
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
    print("Windows OCR: 原圖 vs 2x Lanczos upscale 比較")
    print("=" * 72)
    print()
    print("步驟:")
    print("  1. 確認 Windows 顯示縮放是 125% (這是要驗證的情境)")
    print("  2. 等下倒數開始,按 Win 鍵打開 Start Menu")
    print("  3. 10 秒倒數結束自動截圖 + 兩輪 OCR")
    print()
    for i in range(10, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)

    with mss.mss() as sct:
        img = np.array(sct.grab(sct.monitors[0]))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    h, w = bgr.shape[:2]
    print(f"\n原圖尺寸: {w}x{h}")

    # 2x Lanczos 上採樣
    print("做 2x Lanczos 上採樣...")
    upscaled = cv2.resize(bgr, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
    print(f"上採樣後尺寸: {upscaled.shape[1]}x{upscaled.shape[0]}")
    cv2.imwrite("_test_orig.png", bgr)
    cv2.imwrite("_test_upscaled.png", upscaled)

    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from pipeline.ocr import _recognize

    # Round 1: 原圖
    print("\n" + "=" * 72)
    print("Round 1: 原圖 OCR")
    print("=" * 72)
    t0 = time.time()
    words_orig = asyncio.run(_recognize(bgr, "zh-Hant-TW"))
    t_orig = time.time() - t0
    print(f"耗時 {t_orig:.2f}s,共讀到 {len(words_orig)} 個 word")

    # Round 2: 2x 上採樣
    print("\n" + "=" * 72)
    print("Round 2: 2x Lanczos Upscale OCR")
    print("=" * 72)
    t0 = time.time()
    words_2x = asyncio.run(_recognize(upscaled, "zh-Hant-TW"))
    t_2x = time.time() - t0
    print(f"耗時 {t_2x:.2f}s,共讀到 {len(words_2x)} 個 word")

    # 比對
    targets_zh = ["檔案總管", "時鐘", "記事本", "設定", "相片", "小算盤", "小畫家", "剪取工具"]
    targets_en = ["Microsoft Edge", "Outlook", "Microsoft 365", "Microsoft Store", "Copilot"]

    def hits_for(words: list[dict], t: str) -> list[str]:
        return [w["text"] for w in words if t in w["text"]]

    def hits_chars(words: list[dict], t: str) -> set[str]:
        """寬鬆:目標的任一字出現在 OCR words 裡(去重)。"""
        return sorted({w["text"] for w in words if w["text"] in t and len(w["text"]) >= 1})

    print("\n" + "=" * 72)
    print("比對結果(整詞匹配 = OCR 一個 word 內必須含完整目標)")
    print("=" * 72)
    print(f"{'目標':<20} {'原圖':<10} {'2x upscale':<10}")
    print("-" * 72)
    improved = []
    for t in targets_zh + targets_en:
        h1 = hits_for(words_orig, t)
        h2 = hits_for(words_2x, t)
        s1 = "OK" if h1 else "X"
        s2 = "OK" if h2 else "X"
        marker = " ★" if not h1 and h2 else ("" if h1 == h2 else " ~")
        print(f"  {t:<18} {s1:<8} {s2:<8}{marker}")
        if not h1 and h2:
            improved.append(t)

    print("\n" + "=" * 72)
    print("拆字寬鬆比對(目標的任一字是否出現,看複雜字被吞的情況)")
    print("=" * 72)
    print(f"{'目標':<20} {'原圖讀到字':<30} {'2x upscale 讀到字':<30}")
    print("-" * 72)
    for t in targets_zh:
        c1 = hits_chars(words_orig, t)
        c2 = hits_chars(words_2x, t)
        new = [c for c in c2 if c not in c1]
        marker = f"  +{'/'.join(new)}" if new else ""
        print(f"  {t:<18} {''.join(c1):<28} {''.join(c2):<28}{marker}")

    print("\n" + "=" * 72)
    print("結論")
    print("=" * 72)
    if improved:
        print(f"OK 2x upscale 救回 {len(improved)} 個目標: {improved}")
        print(f"   原圖 OCR {t_orig:.2f}s → 2x upscale {t_2x:.2f}s (慢了 {t_2x/t_orig:.1f}x)")
        print(f"   建議:V5 OCR 預處理層加入 2x Lanczos upscale")
    else:
        print("X 2x upscale 沒救回任何目標 - 純放大不夠、可能還需要 sharpening / contrast")
        print(f"   原圖讀 {len(words_orig)} 詞、2x 讀 {len(words_2x)} 詞")


if __name__ == "__main__":
    main()
