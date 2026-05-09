"""UIA Live Picker — 滑鼠 hover 到的 UI 元素自動抓 + 紅框跟隨 + F8 確認。

設計參照 Microsoft Inspect.exe 的 Live Inspect 模式:
- 進入 picker 後、滑鼠移到桌面任意 UI 元素、後台輪詢 cursor 位置 →
  uiautomation.ControlFromPoint() → 在該元素 rect 畫紅框
- 按 F8 確認:把當下 hover 元素資訊鎖進 confirmed、停止 picker
- 按 F9 取消:不確認、停止 picker
- frontend 用輪詢拿狀態(running / hovered / confirmed)、確認後用該元素加 action

跟原本「樹狀挑」並用、不衝突;純加便利路徑、現有 inspect / list windows 仍 work。
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

_log = logging.getLogger(__name__)


class _UiaPicker:
    """單例:只允許同時一個 picker session。"""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._hotkey_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_element: Optional[dict] = None
        self._confirmed: Optional[dict] = None
        self._error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """啟動 picker thread + 全域 F8/F9 hotkey listener。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._last_element = None
            self._confirmed = None
            self._error = None
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="uia-picker")
            self._thread.start()
            self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True, name="uia-picker-hotkey")
            self._hotkey_thread.start()
            return True

    def stop(self):
        """停 picker、清紅框、保留 confirmed 給 frontend 拿一次。"""
        was_running = self._running
        self._running = False
        try:
            from .cu_highlight_overlay import clear_highlight
            clear_highlight()
        except Exception:
            pass
        return was_running

    def poll(self) -> dict:
        """frontend 輪詢:拿當下 hover 元素 + 是否確認。"""
        with self._lock:
            return {
                "running": self._running,
                "hovered": dict(self._last_element) if self._last_element else None,
                "confirmed": dict(self._confirmed) if self._confirmed else None,
                "error": self._error,
            }

    def consume_confirmed(self) -> Optional[dict]:
        """frontend 拿完 confirmed 之後 reset、避免重複處理。"""
        with self._lock:
            c = self._confirmed
            self._confirmed = None
            return c

    def _run_loop(self):
        """背景 loop:輪詢滑鼠 → ControlFromPoint → 紅框。"""
        try:
            import uiautomation as auto
            from ctypes import windll, Structure, c_long, byref

            class POINT(Structure):
                _fields_ = [("x", c_long), ("y", c_long)]

            from .cu_highlight_overlay import highlight as draw_highlight
        except Exception as e:
            self._error = f"picker 初始化失敗:{e}"
            self._running = False
            return

        last_pos = (-99999, -99999)
        last_handle = None
        consecutive_fail = 0

        while self._running:
            try:
                p = POINT()
                windll.user32.GetCursorPos(byref(p))
                pos = (p.x, p.y)

                # 滑鼠沒動 → 不重 query(避免 spam UIA)
                if pos == last_pos:
                    time.sleep(0.07)
                    continue
                last_pos = pos

                ctrl = auto.ControlFromPoint(pos[0], pos[1])
                if ctrl is None:
                    time.sleep(0.1)
                    continue

                # 同一個 element handle → 跳過(只更新時不重 highlight)
                handle = getattr(ctrl, "NativeWindowHandle", 0) or 0
                key = (handle, ctrl.ControlTypeName, ctrl.Name)
                if key == last_handle:
                    time.sleep(0.07)
                    continue
                last_handle = key

                rect = ctrl.BoundingRectangle
                rw = int(rect.right - rect.left)
                rh = int(rect.bottom - rect.top)
                el_info = {
                    "type": str(ctrl.ControlTypeName or ""),
                    "name": str(ctrl.Name or "")[:200],
                    "auto_id": str(getattr(ctrl, "AutomationId", "") or ""),
                    "rect": [int(rect.left), int(rect.top), rw, rh],
                    "enabled": bool(getattr(ctrl, "IsEnabled", True)),
                }
                with self._lock:
                    self._last_element = el_info

                if rw > 0 and rh > 0:
                    # 短 TTL 讓紅框跟隨滑鼠、不要殘留
                    draw_highlight(int(rect.left), int(rect.top), rw, rh, ttl_ms=400)

                consecutive_fail = 0
                time.sleep(0.07)
            except Exception as e:
                consecutive_fail += 1
                if consecutive_fail > 5:
                    self._error = f"picker loop 異常 5 次:{e}"
                    self._running = False
                    return
                time.sleep(0.2)

        # 結束:清紅框
        try:
            from .cu_highlight_overlay import clear_highlight
            clear_highlight()
        except Exception:
            pass

    def _hotkey_loop(self):
        """全域 F8 = 確認、F9 = 取消。用 pynput.keyboard listener。"""
        try:
            from pynput import keyboard
        except ImportError:
            self._error = "pynput 未安裝、不能監聽 F8/F9"
            return

        def on_press(key):
            if not self._running:
                return False
            try:
                if key == keyboard.Key.f8:
                    with self._lock:
                        if self._last_element:
                            self._confirmed = dict(self._last_element)
                            self._log_confirm()
                            self._running = False
                            return False
                elif key == keyboard.Key.f9:
                    _log.info("[uia-picker] 使用者按 F9 取消")
                    self._running = False
                    return False
            except Exception:
                pass

        try:
            with keyboard.Listener(on_press=on_press) as listener:
                # 等到 picker 結束才退;listener.stop() 也會結束
                while self._running:
                    time.sleep(0.1)
                listener.stop()
        except Exception as e:
            _log.warning(f"[uia-picker] hotkey listener 失敗(可能已被其他 process 佔用):{e}")

    def _log_confirm(self):
        if self._confirmed:
            el = self._confirmed
            _log.info(f"[uia-picker] F8 確認 type={el.get('type')!r} name={el.get('name', '')[:60]!r} rect={el.get('rect')}")


_picker_singleton: Optional[_UiaPicker] = None
_picker_lock = threading.Lock()


def get_picker() -> _UiaPicker:
    global _picker_singleton
    with _picker_lock:
        if _picker_singleton is None:
            _picker_singleton = _UiaPicker()
        return _picker_singleton
