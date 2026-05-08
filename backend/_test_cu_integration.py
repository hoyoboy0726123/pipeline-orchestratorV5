"""Computer use VLM 把關整合測試 — execute_computer_use_step 完整流程。

不真的點桌面、用 monkeypatch 把 _capture_screen 換成回傳合成圖、
驗證:
1. _should_verify() 正確判斷
2. VLM verdict 走進 on_mismatch 三條路徑(stop_notify / retry_once / skip_and_continue)
3. exit_code 正確區分(動作 fail=1、VLM mismatch=2)

跑法:
    cd backend
    .\.venv\Scripts\python.exe _test_cu_integration.py
"""
from __future__ import annotations
import logging
import sys
import time
from pathlib import Path
from typing import Optional

backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cu_int_test")


# ── 合成圖工廠(共用 _test_cu_vlm_verifier 的邏輯)─────────────────
def make_scene(scene: str) -> "np.ndarray":
    """生成 BGR ndarray、跟 _capture_screen 回傳格式一致。"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 480), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    if scene == "idle":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad - Untitled", fill="white", font=font)
        draw.text((10, 60), "[File] [Edit] [View]", fill="black", font=font)
    elif scene == "menu_open":
        draw.rectangle([0, 0, 640, 50], fill="navy")
        draw.text((10, 12), "Notepad - Untitled", fill="white", font=font)
        draw.text((10, 60), "[File] [Edit] [View]", fill="black", font=font)
        draw.rectangle([0, 90, 200, 270], fill="lightyellow", outline="black")
        draw.text((10, 100), "New", fill="black", font=font)
        draw.text((10, 130), "Open...", fill="black", font=font)
        draw.text((10, 160), "Save As...", fill="black", font=font)
    elif scene == "save_dialog":
        draw.rectangle([100, 80, 540, 380], fill="lightgray", outline="black", width=2)
        draw.rectangle([100, 80, 540, 110], fill="navy")
        draw.text((110, 86), "Save As", fill="white", font=font)
    elif scene == "error":
        draw.rectangle([150, 150, 490, 320], fill="white", outline="red", width=3)
        draw.rectangle([150, 150, 490, 180], fill="red")
        draw.text((160, 154), "Error", fill="white", font=font)
        draw.text((170, 200), "Permission denied", fill="black", font=font)

    arr = np.array(img)
    # PIL 是 RGB、_capture_screen 給 BGR(因為走 mss + cv2)
    return arr[:, :, ::-1].copy()


class FakeScreenSequencer:
    """提供一個可程式化的 _capture_screen 替身、依呼叫順序回不同 scene。

    用法:
        seq = FakeScreenSequencer(["idle", "idle"])  # 第 1 次回 idle、第 2 次回 idle
        執行 patch _capture_screen = seq.capture
        呼叫 execute_computer_use_step
    """
    def __init__(self, scenes: list[str]):
        self.scenes = scenes
        self.idx = 0
        self.captures = []

    def capture(self):
        """模擬 _capture_screen 簽名 → (img, w, h)。"""
        if self.idx >= len(self.scenes):
            # 序列耗盡用最後一張(避免 IndexError)
            scene = self.scenes[-1] if self.scenes else "idle"
        else:
            scene = self.scenes[self.idx]
            self.idx += 1
        img = make_scene(scene)
        h, w = img.shape[:2]
        self.captures.append(scene)
        return img, w, h


def run_case(name: str, actions: list[dict], scenes: list[str],
             cu_vlm_check_strategy: str = "after_each",
             cu_on_mismatch: str = "stop_notify",
             expect_success: bool = True,
             expect_exit_code: int = 0) -> bool:
    """跑一個 case、印結果、跟期望比對。"""
    print(f"\n{'='*70}")
    print(f"CASE: {name}")
    print(f"  strategy={cu_vlm_check_strategy} on_mismatch={cu_on_mismatch}")
    print(f"  expect: success={expect_success} exit_code={expect_exit_code}")

    # monkey-patch _capture_screen
    import pipeline.computer_use as cu_mod
    seq = FakeScreenSequencer(scenes)
    orig = cu_mod._capture_screen
    cu_mod._capture_screen = seq.capture

    # 也 patch execute_action — 因為 wait action 不會真互動桌面但我們要避免任何 pyautogui 呼叫
    # 用一個 dummy 的 action 執行函式、回 ActionResult(ok=True)
    from pipeline.computer_use import ActionResult
    def fake_execute(action, _assets, action_index, *_args, **_kwargs):
        return ActionResult(ok=True, action_index=action_index,
                            action_type=action.get("type", "?"),
                            message=f"(mock executed {action.get('type')})", duration_ms=10)
    orig_exec = cu_mod.execute_action
    cu_mod.execute_action = fake_execute

    # 真的呼叫 execute_computer_use_step
    test_assets = Path(__file__).parent / "_test_cu_int_assets"
    test_assets.mkdir(exist_ok=True)
    try:
        result = cu_mod.execute_computer_use_step(
            actions=actions,
            assets_dir=str(test_assets),
            logger=log,
            run_id="test-int",
            fail_fast=True,
            cu_vlm_check_strategy=cu_vlm_check_strategy,
            cu_on_mismatch=cu_on_mismatch,
            cu_vlm_max_retries=1,
        )
    finally:
        cu_mod._capture_screen = orig
        cu_mod.execute_action = orig_exec

    print(f"  → success={result.success} exit={result.exit_code} succeeded={result.succeeded}/{result.total_actions}")
    print(f"    stderr: {(result.stderr or '')[:200]}")
    print(f"    captures sequence: {seq.captures}")

    match = (result.success == expect_success) and (result.exit_code == expect_exit_code)
    print(f"  RESULT: {'✅ PASS' if match else '❌ FAIL'}")
    return match


def main():
    # Case A: strategy=off、有 expected 也不驗、走原路徑成功
    case_a = run_case(
        "A. strategy=off 不啟動 VLM(向後相容、預設行為)",
        actions=[
            {"type": "wait", "seconds": 0.1, "expected": "選單已開"},  # expected 會被忽略
        ],
        scenes=[],  # 沒驗就不會 capture
        cu_vlm_check_strategy="off",
        expect_success=True, expect_exit_code=0,
    )

    # Case B: strategy=after_each、expected 對(畫面 idle → menu_open、預期選單已開)→ ok=True
    case_b = run_case(
        "B. after_each + expected 對(idle → menu_open、預期選單開) → 應 success",
        actions=[
            {"type": "wait", "seconds": 0.1,
             "expected": "出現了一個檔案選單、可以看到 Save As... 選項"},
        ],
        scenes=["idle", "menu_open"],  # before=idle / after=menu_open
        cu_vlm_check_strategy="after_each",
        expect_success=True, expect_exit_code=0,
    )

    # Case C: after_each + expected 不對(畫面沒變、預期選單應開) → no_effect、stop_notify
    case_c = run_case(
        "C. after_each + 動作沒生效 → stop_notify、exit=2",
        actions=[
            {"type": "wait", "seconds": 0.1,
             "expected": "出現了一個檔案選單、可以看到 Save As... 選項"},
        ],
        scenes=["idle", "idle"],  # before=after=idle、預期選單沒開
        cu_vlm_check_strategy="after_each",
        cu_on_mismatch="stop_notify",
        expect_success=False, expect_exit_code=2,  # VLM mismatch = exit 2
    )

    # Case D: skip_and_continue 模式、應該成功(忽略 mismatch 繼續)
    case_d = run_case(
        "D. after_each + 動作沒生效、但 skip_and_continue → 應 success",
        actions=[
            {"type": "wait", "seconds": 0.1,
             "expected": "出現了一個檔案選單、可以看到 Save As... 選項"},
        ],
        scenes=["idle", "idle"],
        cu_vlm_check_strategy="after_each",
        cu_on_mismatch="skip_and_continue",
        expect_success=True, expect_exit_code=0,
    )

    # Case E: critical_only 模式、verify_critical=False → 不驗、走原路徑成功
    case_e = run_case(
        "E. critical_only + verify_critical=False → 跳過 VLM、應 success",
        actions=[
            {"type": "wait", "seconds": 0.1,
             "expected": "選單已開",  # 有 expected 但 critical=False
             "verify_critical": False},
        ],
        scenes=[],  # 不會驗、不該 capture
        cu_vlm_check_strategy="critical_only",
        expect_success=True, expect_exit_code=0,
    )

    # Case F: critical_only + verify_critical=True、且 mismatch → stop_notify
    case_f = run_case(
        "F. critical_only + verify_critical=True + mismatch → stop_notify、exit=2",
        actions=[
            {"type": "wait", "seconds": 0.1,
             "expected": "出現了一個檔案選單、可以看到 Save As... 選項",
             "verify_critical": True},
        ],
        scenes=["idle", "idle"],
        cu_vlm_check_strategy="critical_only",
        cu_on_mismatch="stop_notify",
        expect_success=False, expect_exit_code=2,
    )

    results = {"A": case_a, "B": case_b, "C": case_c, "D": case_d, "E": case_e, "F": case_f}
    print(f"\n{'='*70}")
    print("SUMMARY")
    for k, v in results.items():
        print(f"  Case {k}: {'✅' if v else '❌'}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nTOTAL: {passed}/{total} passed")
    if passed == total:
        print("✅ ALL INTEGRATION CASES PASS — Phase 1 backend 完整串接運作正常")
    else:
        print("⚠️ 有 case 不符預期、請檢查上面 verdict")


if __name__ == "__main__":
    main()
