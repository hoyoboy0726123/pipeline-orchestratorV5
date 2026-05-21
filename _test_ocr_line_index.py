"""針對「檔案總管」4 字的 OCR line_index + bbox 診斷。

打開 Start Menu、跑這個。會印出:
  1. '檔', '案', '總', '管' 各自的 line_index 跟 bbox
  2. 是否同行(同 line_index)
  3. 空間距離 (Y 差、X 差),驗證「即使不同 line_index 也能靠空間鄰近合併」假設
  4. V5 現行匹配是否會 work
"""
import asyncio
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np


def main():
    if sys.platform == "win32":
        import ctypes
        u32 = ctypes.windll.user32
        u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    print("=" * 70)
    print("OCR line_index + bbox 診斷 (針對「檔案總管」4 字)")
    print("=" * 70)
    print("\n按 Win 鍵打開 Start Menu、確認彈出來、放著別動。10 秒倒數...\n")
    for i in range(10, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)

    with mss.mss() as sct:
        img = np.array(sct.grab(sct.monitors[0]))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    cv2.imwrite("_test_line_index.png", bgr)
    print(f"\n截圖存到 _test_line_index.png  尺寸: {bgr.shape[1]}x{bgr.shape[0]}\n")

    sys.path.insert(0, str(Path(__file__).parent / "backend"))
    from pipeline.ocr import _recognize

    words = asyncio.run(_recognize(bgr, "zh-Hant-TW"))
    print(f"OCR 共讀到 {len(words)} 個 word\n")

    target_chars = ["檔", "案", "總", "管"]
    print("=" * 70)
    print("關鍵字逐一定位")
    print("=" * 70)
    found = {}
    for ch in target_chars:
        hits = [w for w in words if ch in w["text"]]
        print(f"\n字「{ch}」共 {len(hits)} 個 OCR word 含它:")
        for w in hits:
            print(f"  text={w['text']!r:<8}  line={w['line_index']:>3}  bbox=({w['x']:>5},{w['y']:>5}) {w['w']:>3}x{w['h']:<3}  line_text={w['line_text']!r}")
        # 取第一個(假設那是 Start Menu 的)做關係分析
        if hits:
            found[ch] = hits[0]

    if len(found) >= 2:
        print("\n" + "=" * 70)
        print("空間關係分析(取每個字第一個命中)")
        print("=" * 70)
        chars_in_order = [c for c in target_chars if c in found]
        for i in range(len(chars_in_order) - 1):
            a = found[chars_in_order[i]]
            b = found[chars_in_order[i + 1]]
            same_line = a["line_index"] == b["line_index"]
            dx = b["x"] - (a["x"] + a["w"])
            dy = abs((a["y"] + a["h"] // 2) - (b["y"] + b["h"] // 2))
            print(f"\n「{chars_in_order[i]}」 → 「{chars_in_order[i + 1]}」")
            print(f"  line_index 相同? {same_line}  ({a['line_index']} vs {b['line_index']})")
            print(f"  水平間距 dx = {dx}px  ({'相鄰' if dx < 30 else '遠'})")
            print(f"  垂直中心差 dy = {dy}px  ({'同行' if dy < 15 else '不同行'})")

    print("\n" + "=" * 70)
    print("V5 現行 Phase 3 跨詞匹配模擬")
    print("=" * 70)
    by_line = {}
    for w in words:
        by_line.setdefault(w["line_index"], []).append(w)
    target = "檔案總管"
    matched = False
    for idx, line_words in by_line.items():
        joined = "".join(w["text"] for w in line_words).replace(" ", "")
        if target in joined:
            matched = True
            print(f"\n✓ 命中! line_index={idx}, joined='{joined}', 屬於 {len(line_words)} 個 word 拼起來")
            break
    if not matched:
        print(f"\n✗ Phase 3 跨詞匹配失敗 — 「{target}」沒有任何一條 line 拼起來含它")
        print("\n各 line 含至少一個目標字的 joined 內容(line_index 排序):")
        target_set = set(target)
        relevant_lines = []
        for idx, line_words in by_line.items():
            joined = "".join(w["text"] for w in line_words).replace(" ", "")
            if any(c in joined for c in target_set):
                relevant_lines.append((idx, joined, len(line_words)))
        for idx, joined, n in sorted(relevant_lines)[:20]:
            print(f"  line={idx:>3} ({n:>2} words): {joined[:80]!r}")


if __name__ == "__main__":
    main()
