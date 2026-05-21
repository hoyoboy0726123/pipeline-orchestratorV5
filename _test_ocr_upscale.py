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

    # 4 種 preprocessing 變體
    variants: dict[str, np.ndarray] = {}
    variants["原圖"] = bgr

    # 變體 A: 2x Lanczos
    variants["2x_Lanczos"] = cv2.resize(bgr, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)

    # 變體 B: 2x Cubic (比 Lanczos 平滑、無 ringing 偽影)
    variants["2x_Cubic"] = cv2.resize(bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

    # 變體 C: 2x Cubic + Unsharp Mask 銳化(放大後增強邊緣)
    cubic = variants["2x_Cubic"]
    blurred = cv2.GaussianBlur(cubic, (0, 0), sigmaX=1.5)
    variants["2x_Cubic+Sharp"] = cv2.addWeighted(cubic, 1.6, blurred, -0.6, 0)

    # 變體 D: 灰階 + 2x Cubic + Unsharp Mask(把 Mica 色彩干擾砍掉)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray_2x = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    gray_blur = cv2.GaussianBlur(gray_2x, (0, 0), sigmaX=1.5)
    gray_sharp = cv2.addWeighted(gray_2x, 1.6, gray_blur, -0.6, 0)
    variants["灰階+2x+Sharp"] = cv2.cvtColor(gray_sharp, cv2.COLOR_GRAY2BGR)

    # 變體 E: 2x Cubic + 強對比拉伸 (對 Mica 半透明特別有效)
    contrast = cv2.convertScaleAbs(cubic, alpha=1.4, beta=-30)  # alpha 對比、beta 亮度
    variants["2x_Cubic+Contrast"] = contrast

    for name, img_v in variants.items():
        if name != "原圖":
            cv2.imwrite(f"_test_{name}.png", img_v)

    print("\n生成 5 個 variant:", list(variants.keys()))

    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from pipeline.ocr import _recognize

    # 跑每個 variant 的 OCR
    results: dict[str, list[dict]] = {}
    times: dict[str, float] = {}
    for name, img_v in variants.items():
        print(f"\n--- OCR variant: {name} ---")
        t0 = time.time()
        words = asyncio.run(_recognize(img_v, "zh-Hant-TW"))
        elapsed = time.time() - t0
        results[name] = words
        times[name] = elapsed
        print(f"耗時 {elapsed:.2f}s, 讀到 {len(words)} 個 word")

    # 把 variants 也存成 alias 方便後面用
    words_orig = results["原圖"]
    words_2x = results["2x_Lanczos"]
    t_orig = times["原圖"]
    t_2x = times["2x_Lanczos"]

    # 比對
    targets_zh = ["檔案總管", "時鐘", "記事本", "設定", "相片", "小算盤", "小畫家", "剪取工具"]
    targets_en = ["Microsoft Edge", "Outlook", "Microsoft 365", "Microsoft Store", "Copilot"]

    def hits_for(words: list[dict], t: str) -> list[str]:
        return [w["text"] for w in words if t in w["text"]]

    def hits_chars(words: list[dict], t: str) -> set[str]:
        """寬鬆:目標的任一字出現在 OCR words 裡(去重)。"""
        return sorted({w["text"] for w in words if w["text"] in t and len(w["text"]) >= 1})

    # 整詞匹配表 — 5 個 variant 並排
    print("\n" + "=" * 100)
    print("整詞匹配(OCR 一個 word 內必須含完整目標)")
    print("=" * 100)
    cols = list(results.keys())
    header = f"{'目標':<18}" + "".join(f"{c:<18}" for c in cols)
    print(header)
    print("-" * 100)
    integer_winners: dict[str, list[str]] = {c: [] for c in cols}
    for t in targets_zh + targets_en:
        row = f"  {t:<16}"
        for c in cols:
            h = hits_for(results[c], t)
            sym = "OK" if h else "X"
            row += f"{sym:<18}"
            if h:
                integer_winners[c].append(t)
        print(row)

    # 拆字級表
    print("\n" + "=" * 100)
    print("拆字級寬鬆匹配(目標各字被哪些 variant 偵測到)")
    print("=" * 100)
    print(f"{'目標':<16}" + "".join(f"{c:<18}" for c in cols))
    print("-" * 100)
    for t in targets_zh:
        row = f"  {t:<14}"
        for c in cols:
            cs = "".join(hits_chars(results[c], t))
            row += f"{cs:<18}"
        print(row)

    # 結論
    print("\n" + "=" * 100)
    print("結論:每個 variant 各救到幾個整詞目標 + OCR 耗時")
    print("=" * 100)
    for c in cols:
        n = len(integer_winners[c])
        won = integer_winners[c]
        print(f"  {c:<22}  整詞命中 {n:>2} / 13  耗時 {times[c]:>5.2f}s  目標: {won}")

    best = max(cols, key=lambda c: len(integer_winners[c]))
    print(f"\n→ 最佳 variant: 【{best}】、命中 {len(integer_winners[best])} 個整詞")
    if best == "原圖":
        print("  原圖本身就最好、preprocessing 沒幫助")
    else:
        print(f"  建議:V5 OCR 加入此預處理路徑,代價慢了 {times[best]/times['原圖']:.1f}x")


if __name__ == "__main__":
    main()
