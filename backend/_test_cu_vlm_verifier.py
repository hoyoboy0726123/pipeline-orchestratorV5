"""e2e 測試 cu_vlm_verifier.verify_action_outcome — 用合成 UI 截圖測 4 種情境。

跑法:
    cd backend
    .\.venv\Scripts\python.exe _test_cu_vlm_verifier.py

不需 backend service 跑著、直接呼叫 verifier。
但需 settings.model 是 vision-capable(看 settings.json)。
"""
from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

# 把 backend dir 加 sys.path
backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cu_vlm_test")


def make_test_image(out_path: Path, scene: str) -> str:
    """合成一張 UI-like 截圖、放文字模擬不同狀態。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 480), color="white")
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("arial.ttf", 32)
        font_med = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font_big = ImageFont.load_default()
        font_med = ImageFont.load_default()

    if scene == "main_window_idle":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad - Untitled", fill="white", font=font_big)
        draw.text((10, 60), "[File] [Edit] [View] [Help]", fill="black", font=font_med)
        draw.text((10, 200), "(empty document)", fill="gray", font=font_med)

    elif scene == "file_menu_opened":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad - Untitled", fill="white", font=font_big)
        draw.text((10, 60), "[File] [Edit] [View] [Help]", fill="black", font=font_med)
        # 模擬開啟的檔案選單
        draw.rectangle([0, 90, 200, 270], fill="lightyellow", outline="black")
        draw.text((10, 100), "New                Ctrl+N", fill="black", font=font_med)
        draw.text((10, 130), "Open...            Ctrl+O", fill="black", font=font_med)
        draw.text((10, 160), "Save               Ctrl+S", fill="black", font=font_med)
        draw.text((10, 190), "Save As...", fill="black", font=font_med)
        draw.text((10, 220), "Exit", fill="black", font=font_med)

    elif scene == "save_dialog_opened":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad - Untitled", fill="white", font=font_big)
        # 模擬另存新檔對話框
        draw.rectangle([100, 80, 540, 380], fill="lightgray", outline="black", width=2)
        draw.rectangle([100, 80, 540, 110], fill="navy")
        draw.text((110, 86), "Save As", fill="white", font=font_big)
        draw.text((120, 130), "File name:", fill="black", font=font_med)
        draw.rectangle([220, 128, 510, 158], fill="white", outline="black")
        draw.text((230, 134), "untitled.txt", fill="black", font=font_med)
        draw.rectangle([350, 320, 430, 360], fill="lightblue", outline="black")
        draw.text((360, 330), "Save", fill="black", font=font_med)
        draw.rectangle([440, 320, 520, 360], fill="white", outline="black")
        draw.text((450, 330), "Cancel", fill="black", font=font_med)

    elif scene == "error_popup":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad", fill="white", font=font_big)
        # 紅色錯誤對話框
        draw.rectangle([150, 150, 490, 320], fill="white", outline="red", width=3)
        draw.rectangle([150, 150, 490, 180], fill="red")
        draw.text((160, 154), "❌ Error", fill="white", font=font_big)
        draw.text((170, 200), "Cannot save file:", fill="black", font=font_med)
        draw.text((170, 230), "Permission denied", fill="black", font=font_med)
        draw.rectangle([300, 270, 380, 300], fill="lightgray", outline="black")
        draw.text((320, 277), "OK", fill="black", font=font_med)

    img.save(str(out_path), format="PNG")
    return str(out_path)


async def run_test(case_name: str, before: str, after: str, expected: str, expect_ok: bool):
    """跑一個 case、印 verdict、跟期望比對。"""
    from pipeline.cu_vlm_verifier import verify_action_outcome
    print(f"\n{'='*70}")
    print(f"CASE: {case_name}")
    print(f"  expected: {expected}")
    print(f"  expect_ok={expect_ok}")
    verdict = await verify_action_outcome(
        before_path=before, after_path=after,
        expected=expected, logger=log, timeout_sec=45.0,
    )
    print(f"  → ok={verdict['ok']} type={verdict['mismatch_type']!r}")
    print(f"    reason: {verdict['reason']}")
    if verdict.get("unexpected"):
        print(f"    unexpected: {verdict['unexpected']}")
    print(f"    confidence: {verdict.get('confidence', 0):.2f}")

    match = (verdict["ok"] == expect_ok)
    print(f"  RESULT: {'✅ PASS' if match else '❌ FAIL'} (expect_ok={expect_ok}, got={verdict['ok']})")
    return match


async def main():
    test_dir = Path(__file__).parent / "_test_cu_vlm_images"
    test_dir.mkdir(exist_ok=True)

    print(f"Generating test images in {test_dir} ...")
    img_idle = make_test_image(test_dir / "01_idle.png", "main_window_idle")
    img_menu = make_test_image(test_dir / "02_menu.png", "file_menu_opened")
    img_save = make_test_image(test_dir / "03_save.png", "save_dialog_opened")
    img_err = make_test_image(test_dir / "04_err.png", "error_popup")

    # Case 1: idle → menu、expected "檔案選單已開啟" → 應 ok=True
    case1_ok = await run_test(
        "1. 點檔案選單成功(idle → menu opened)",
        img_idle, img_menu,
        expected="檔案選單已開啟、可以看到「Save As...」選項",
        expect_ok=True,
    )
    # Case 2: idle → idle、expected "選單已開啟" → 應 ok=False (no_effect)
    case2_ok = await run_test(
        "2. 動作沒生效(idle → idle、預期選單應開但沒開)",
        img_idle, img_idle,
        expected="檔案選單已開啟、可以看到「Save As...」選項",
        expect_ok=False,
    )
    # Case 3: menu → save dialog、expected "另存新檔對話框已開" → 應 ok=True
    case3_ok = await run_test(
        "3. 點 Save As 成功(menu → save dialog)",
        img_menu, img_save,
        expected="另存新檔對話框已開啟、含 File name 輸入框跟 Save / Cancel 按鈕",
        expect_ok=True,
    )
    # Case 4: idle → error popup、expected "另存新檔已開" → 應 ok=False (unexpected_popup)
    case4_ok = await run_test(
        "4. 出現非預期錯誤對話框(idle → error popup)",
        img_idle, img_err,
        expected="另存新檔對話框已開啟、含 File name 輸入框",
        expect_ok=False,
    )

    results = [case1_ok, case2_ok, case3_ok, case4_ok]
    print(f"\n{'='*70}")
    print(f"TOTAL: {sum(results)}/{len(results)} passed")
    if all(results):
        print("✅ ALL CASES PASS — VLM verifier 工作正常")
    else:
        print("⚠️ 有 case 不符預期、請看上面 verdict")


if __name__ == "__main__":
    asyncio.run(main())
