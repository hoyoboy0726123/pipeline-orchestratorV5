"""
桌面自動化引擎（computer_use 節點專用）。

核心能力：
- L1 basic template matching（cv2.matchTemplate + TM_CCOEFF_NORMED）
- L2 multi-scale matching（對 template 做 ±15% 縮放，解決 DPI/視窗大小差異）
- 動作執行：click_image / click_at / type_text / hotkey / wait / wait_image / screenshot
- Emergency abort：pyautogui.FAILSAFE（滑鼠移到左上角 0,0 立即觸發）+ run_id 中止訊號

不與 skill / recipe 系統共用 — 純 pyautogui + opencv 執行，無 LLM 參與。
"""
from __future__ import annotations
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np


# ── Emergency abort signal（執行中可從外部 set，立即中斷）────────
_abort_flags: dict[str, bool] = {}


# ── 模板圖 LRU 快取 ───────────────────────────────────────────────
# 對同一張錨點圖反覆 read_bytes + imdecode + cvtColor + Canny 是浪費；
# 典型一個 step 會對同一圖做 2~14 次（multi-scale × edge fallback × retry）。
# 以 (abs_path, mtime) 當 key，mtime 變動（使用者重錄）會自動失效。
# 記憶體成本：每個 ~5-50KB，上限 64 張 → < 4MB
_TPL_CACHE_MAX = 64
_tpl_cache: "OrderedDict[tuple[str, float], tuple[np.ndarray, np.ndarray]]" = OrderedDict()


def _load_template(tpl_path: Path):
    """解碼錨點圖 → 回傳 (gray, edge) 灰階/Canny 邊緣陣列，兩者皆用於 find_template 的 mode 切換。
    命中快取直接回；未命中解碼一次存入。失敗回 (None, None, 錯誤訊息)。"""
    import cv2
    try:
        mtime = tpl_path.stat().st_mtime
    except OSError as e:
        return None, None, f"模板 stat 失敗：{e}"
    key = (str(tpl_path), mtime)
    cached = _tpl_cache.get(key)
    if cached is not None:
        _tpl_cache.move_to_end(key)
        return cached[0], cached[1], ""
    try:
        buf = np.frombuffer(tpl_path.read_bytes(), dtype=np.uint8)
        tpl_color = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception as e:
        return None, None, f"模板讀取例外：{e}"
    if tpl_color is None:
        return None, None, f"模板解碼失敗（格式錯誤？）：{tpl_path}"
    tpl_gray = cv2.cvtColor(tpl_color, cv2.COLOR_BGR2GRAY)
    tpl_edge = cv2.Canny(tpl_gray, 50, 150)
    _tpl_cache[key] = (tpl_gray, tpl_edge)
    while len(_tpl_cache) > _TPL_CACHE_MAX:
        _tpl_cache.popitem(last=False)
    return tpl_gray, tpl_edge, ""


def clear_template_cache() -> None:
    """測試或使用者重錄大量錨點後手動清快取用"""
    _tpl_cache.clear()


def request_abort(run_id: str) -> None:
    """標記此 run 需立即中止；computer_use 引擎會在每個動作間檢查"""
    _abort_flags[run_id] = True


def clear_abort(run_id: str) -> None:
    _abort_flags.pop(run_id, None)


def _should_abort(run_id: Optional[str]) -> bool:
    return bool(run_id) and _abort_flags.get(run_id, False)


# ── 螢幕擷取與圖像比對 ──────────────────────────────────────────

def _capture_screen() -> tuple[np.ndarray, int, int]:
    """抓所有螢幕聯集的完整截圖，回傳 (BGR ndarray, 原點 x, 原點 y)。

    關鍵：用 monitors[0]（虛擬桌面聯集）而非 monitors[1]（主螢幕），
    讓 cv2 template matching 能在多螢幕環境下找到任意螢幕上的目標；
    多螢幕時主螢幕左上不一定是 (0,0)，回傳的 origin 用來把比對到的
    相對座標轉回絕對桌面座標（pyautogui.click 接受的就是絕對座標）。
    """
    import mss
    import cv2
    with mss.mss() as sct:
        mon = sct.monitors[0]      # 所有螢幕聯集
        img = np.array(sct.grab(mon))
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return bgr, mon["left"], mon["top"]


def _point_in_any_screen(x: int, y: int) -> tuple[bool, str]:
    """檢查 (x, y) 是否落在目前任一螢幕可見範圍內（支援多螢幕負座標）。
    用途：scroll / click 前避免把滑鼠拉到超出桌面範圍的座標。
    回傳 (是否在範圍內, 目前螢幕配置描述)。"""
    import mss
    try:
        with mss.mss() as sct:
            for mon in sct.monitors[1:]:
                left = mon["left"]
                top = mon["top"]
                if left <= x < left + mon["width"] and top <= y < top + mon["height"]:
                    return True, ""
            layout = "; ".join(
                f"{m['width']}×{m['height']} @ ({m['left']},{m['top']})"
                for m in sct.monitors[1:]
            )
            return False, f"目前螢幕：{layout}"
    except Exception:
        return True, ""  # 抓不到資訊就寬容處理


@dataclass
class MatchResult:
    found: bool
    center: tuple[int, int] = (0, 0)   # (x, y) 螢幕座標
    confidence: float = 0.0
    scale: float = 1.0                  # 命中的縮放比例
    reason: str = ""
    mode: str = "gray"                  # "gray" = 灰階匹配，"edge" = Canny 邊緣匹配


def find_template(
    template_path: str,
    threshold: float = 0.5,
    multi_scale: bool = True,
    near_xy: Optional[tuple[int, int]] = None,
    search_radius: int = 400,
    region: Optional[tuple[int, int, int, int]] = None,
    mode: str = "gray",
) -> MatchResult:
    """在當前螢幕找指定模板圖，回傳中心座標與相似度。

    L1: 單一尺度 matchTemplate（快，~5ms）
    L2: multi_scale=True 時額外跑 0.85/0.9/0.95/1.05/1.1/1.15 倍縮放，
        取最高相似度（~30ms，吸收 DPI 125%/150% 縮放差異）

    mode:
      - "gray"（預設）：灰階像素比對
      - "edge"：Canny 邊緣偵測後再比對 — 兩張圖都先跑 Canny 只留輪廓，
               對色彩/光線/hover 動畫等差異更容忍（conf 通常略低但更穩）

    搜尋範圍優先序（三選一）：
      region 給定 > near_xy 給定 > 全螢幕
      - region: (left, top, width, height) 虛擬桌面絕對座標，使用者明確指定的紅框
      - near_xy: 錄製座標附近 ±search_radius px 的方形範圍（自動退回舊行為）
      - 皆未給：整個虛擬桌面都找（速度最慢、誤匹配風險最高）
    """
    import cv2

    tpl_path = Path(template_path)
    if not tpl_path.is_file():
        return MatchResult(False, reason=f"模板不存在：{template_path}")

    # 從 LRU 快取拿灰階 + Canny 邊緣，避免每次呼叫都重做 decode+cvtColor+Canny
    tpl_gray, tpl_edge, err = _load_template(tpl_path)
    if err:
        return MatchResult(False, reason=err)

    screen_color, origin_x, origin_y = _capture_screen()
    screen_gray_full = cv2.cvtColor(screen_color, cv2.COLOR_BGR2GRAY)

    # Edge 模式：template 在 _load_template 已預算好 Canny；螢幕每次都要重算（畫面會變）。
    # 閾值 50/150 是常用的 hysteresis 組合，對 UI 元素邊緣偵測穩定
    if mode == "edge":
        tpl_proc_full = tpl_edge
        screen_proc_full = cv2.Canny(screen_gray_full, 50, 150)
    else:
        tpl_proc_full = tpl_gray
        screen_proc_full = screen_gray_full

    # 三選一裁切策略：region > near_xy > 全螢幕
    clip_offset_x, clip_offset_y = origin_x, origin_y
    if region is not None:
        # 使用者明確指定的搜尋矩形（絕對桌面座標）
        rl, rt, rw, rh = region
        rel_x = rl - origin_x
        rel_y = rt - origin_y
        H, W = screen_proc_full.shape
        left = max(0, rel_x)
        top = max(0, rel_y)
        right = min(W, rel_x + rw)
        bottom = min(H, rel_y + rh)
        if right - left < 20 or bottom - top < 20:
            return MatchResult(False, reason=f"search_region ({rl},{rt},{rw},{rh}) 與目前桌面範圍重疊不足")
        screen_proc = screen_proc_full[top:bottom, left:right]
        clip_offset_x = origin_x + left
        clip_offset_y = origin_y + top
    elif near_xy is not None:
        nx, ny = near_xy
        # 絕對座標 → 相對截圖的座標
        rel_x = nx - origin_x
        rel_y = ny - origin_y
        H, W = screen_proc_full.shape
        left = max(0, rel_x - search_radius)
        top = max(0, rel_y - search_radius)
        right = min(W, rel_x + search_radius)
        bottom = min(H, rel_y + search_radius)
        if right - left < 20 or bottom - top < 20:
            # 範圍超出螢幕太多（錄製座標根本不在目前桌面範圍內）
            return MatchResult(False, reason=f"錄製座標 ({nx},{ny}) 超出目前桌面範圍")
        screen_proc = screen_proc_full[top:bottom, left:right]
        clip_offset_x = origin_x + left
        clip_offset_y = origin_y + top
    else:
        screen_proc = screen_proc_full

    scales = [1.0]
    if multi_scale:
        # L2：涵蓋常見 DPI 差（100%/125%/150%）
        scales = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15]

    best = MatchResult(False, mode=mode)
    for s in scales:
        if abs(s - 1.0) < 1e-6:
            tpl_scaled = tpl_proc_full
        else:
            new_w = max(1, int(tpl_proc_full.shape[1] * s))
            new_h = max(1, int(tpl_proc_full.shape[0] * s))
            if new_w >= screen_proc.shape[1] or new_h >= screen_proc.shape[0]:
                continue
            tpl_scaled = cv2.resize(tpl_proc_full, (new_w, new_h), interpolation=cv2.INTER_AREA)
        try:
            res = cv2.matchTemplate(screen_proc, tpl_scaled, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            continue
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val > best.confidence:
            h, w = tpl_scaled.shape
            # 比對結果是相對於裁切區域的座標；加上裁切原點換算成桌面絕對座標
            cx = max_loc[0] + w // 2 + clip_offset_x
            cy = max_loc[1] + h // 2 + clip_offset_y
            best = MatchResult(
                found=max_val >= threshold,
                center=(cx, cy),
                confidence=float(max_val),
                scale=s,
                mode=mode,
            )
    if not best.found:
        area = "附近範圍" if near_xy else "整個桌面"
        best.reason = f"最佳相似度 {best.confidence:.3f} 低於門檻 {threshold}（搜尋{area}，{mode} 模式）"
    return best


# ── 動作執行 ────────────────────────────────────────────────────

@dataclass
class ActionResult:
    ok: bool
    action_index: int
    action_type: str
    message: str = ""
    duration_ms: int = 0


def _check_abort(run_id: Optional[str]) -> None:
    if _should_abort(run_id):
        raise RuntimeError("使用者中止（emergency abort）")


def _parse_search_region(action: dict) -> Optional[tuple[int, int, int, int]]:
    """解析 action['search_region'] = [left, top, width, height]（虛擬桌面絕對座標）。
    格式不對或尺寸 <= 0 回 None（代表不限制，走 near_xy / 全螢幕邏輯）。"""
    sr = action.get("search_region") or []
    if not isinstance(sr, (list, tuple)) or len(sr) != 4:
        return None
    try:
        l, t, w, h = int(sr[0]), int(sr[1]), int(sr[2]), int(sr[3])
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (l, t, w, h)


def _vlm_judge_screen(prompt: str, region: Optional[tuple[int, int, int, int]],
                      logger: logging.Logger) -> tuple[bool, str]:
    """vlm_check 用：把當下螢幕送 Settings 主模型，回 (pass, reason)。
    region 給定就先把截圖裁成該矩形再送 VLM（省 token、聚焦關鍵區域）。
    模型不支援視覺時直接回 (False, 錯誤訊息) — 不靜默 fallback。"""
    screen, ox, oy = _capture_screen()
    if region is not None:
        l, t, w, h = region
        x = max(0, l - ox)
        y = max(0, t - oy)
        x_end = min(screen.shape[1], x + w)
        y_end = min(screen.shape[0], y + h)
        if x_end <= x or y_end <= y:
            return (False, f"裁切區域 {(l, t, w, h)} 與目前螢幕無交集")
        screen = screen[y:y_end, x:x_end]

    sys_msg = ("你是 UI 視覺判斷器，看到的圖是螢幕當下狀態。嚴格依使用者描述的條件回 JSON："
               '{"pass": true/false, "reason": "簡短中文說明"}。只回 JSON，不要 markdown、不要其他文字。')
    ok, raw, err = _vlm_call_with_image(f"判斷條件：{prompt}\n請看圖回 JSON。", sys_msg, screen)
    if not ok:
        return (False, err)
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return (False, f"LLM 回應不是 JSON：{raw[:200]}")
    passed = bool(data.get("pass", False))
    reason = str(data.get("reason") or "").strip() or "(無原因說明)"
    logger.info(f"[vlm_check] pass={passed} reason={reason[:120]}")
    return (passed, reason)


def _vlm_call_with_image(messages_text: str, sys_prompt: str,
                         screen_bgr: np.ndarray) -> tuple[bool, str, str]:
    """共用的 VLM 帶圖呼叫。回 (ok, raw_response, error_msg)。"""
    import sys as _sys
    import base64
    import cv2

    backend_dir = str(Path(__file__).resolve().parent.parent)
    if backend_dir not in _sys.path:
        _sys.path.insert(0, backend_dir)
    from llm_factory import build_llm
    from langchain_core.messages import HumanMessage, SystemMessage

    ok, buf = cv2.imencode(".jpg", screen_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not ok:
        return (False, "", "JPEG encode 失敗")
    b64 = base64.b64encode(buf.tobytes()).decode()
    user_content = [
        {"type": "text", "text": messages_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]
    try:
        llm = build_llm(temperature=0)
        result = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=user_content)])
        # 多模態模型（Gemini）回傳 content 是 list[dict]，純文字是 str — 統一抽 text
        raw_content = getattr(result, "content", None)
        if raw_content is None:
            raw = ""
        elif isinstance(raw_content, str):
            raw = raw_content
        elif isinstance(raw_content, list):
            parts: list[str] = []
            for block in raw_content:
                if isinstance(block, dict):
                    t = block.get("text") or ""
                    if t:
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block)
            raw = "".join(parts)
        else:
            raw = str(raw_content)
        raw = raw.strip()
    except Exception as e:
        return (False, "", f"LLM 呼叫失敗（請確認 Settings 主模型支援視覺）：{e.__class__.__name__}: {e}")
    if not raw:
        return (False, "", "LLM 回應為空")
    return (True, raw, "")


def _strip_json_fence(raw: str) -> str:
    """LLM 常用 ```json...``` 包 JSON。剝掉 fence 給 json.loads。"""
    if "```" not in raw:
        return raw.strip()
    parts = raw.split("```")
    if len(parts) >= 2:
        body = parts[1].strip()
        if body.startswith("json"):
            body = body[4:].strip()
        return body
    return raw.strip()


def _vlm_describe_to_text(prompt: str, region: Optional[tuple[int, int, int, int]],
                          logger: logging.Logger) -> tuple[bool, str, str]:
    """vlm_mode='description' 用：把螢幕送 VLM，請它告訴我們目標的「實際文字」。
    座標由後續 OCR 決定（VLM 不負責定位 → 不會點錯位置）。
    回 (found, target_text, reason)。"""
    screen, ox, oy = _capture_screen()
    if region is not None:
        l, t, w, h = region
        x = max(0, l - ox)
        y = max(0, t - oy)
        x_end = min(screen.shape[1], x + w)
        y_end = min(screen.shape[0], y + h)
        if x_end <= x or y_end <= y:
            return (False, "", f"裁切區域 {(l, t, w, h)} 與螢幕無交集")
        screen = screen[y:y_end, x:x_end]

    sys_msg = ("你是 UI 視覺助手。看到的圖是螢幕當下狀態。回 JSON："
               '{"found": true/false, "text": "目標的實際文字", "reason": "簡短說明"}。'
               'text 必須是螢幕上「實際看得到」的字串（含大小寫和標點），不是描述、不是同義詞。'
               "只回 JSON。")
    user_text = (f"使用者描述要點擊的目標：「{prompt}」\n"
                 "請看圖，找出符合此描述的 UI 元素，告訴我它身上實際顯示的文字（OCR 等下會用此文字定位）。"
                 "若畫面上找不到此目標，回 found=false。")

    ok, raw, err = _vlm_call_with_image(user_text, sys_msg, screen)
    if not ok:
        return (False, "", err)
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return (False, "", f"VLM 回應不是 JSON：{raw[:200]}")
    found = bool(data.get("found", False))
    text = str(data.get("text") or "").strip()
    reason = str(data.get("reason") or "").strip() or "(無說明)"
    if found and not text:
        return (False, "", f"VLM 說 found=true 但 text 為空：{reason}")
    logger.info(f"[vlm_describe] found={found} text={text!r} reason={reason[:120]}")
    return (found, text, reason)


def _vlm_pick_anchor(prompt: str, anchor_names: list[str], assets_dir: Path,
                     region: Optional[tuple[int, int, int, int]],
                     logger: logging.Logger) -> tuple[bool, int, str]:
    """vlm_mode='anchor_pick' 用：給 VLM 看當下螢幕 + 數張候選錨點圖，請它選最匹配的索引。
    座標由後續 CV template matching 決定（用 VLM 選出來的那張）。
    回 (ok, picked_index, reason)。picked_index 是 anchor_names 的下標（0-based）。"""
    import cv2
    if not anchor_names:
        return (False, -1, "anchor_names 為空")

    screen, ox, oy = _capture_screen()
    if region is not None:
        l, t, w, h = region
        x = max(0, l - ox)
        y = max(0, t - oy)
        x_end = min(screen.shape[1], x + w)
        y_end = min(screen.shape[0], y + h)
        if x_end <= x or y_end <= y:
            return (False, -1, f"裁切區域 {(l, t, w, h)} 與螢幕無交集")
        screen = screen[y:y_end, x:x_end]

    # 把所有錨點圖橫向拼成一張條狀圖、上方標 [0]、[1]、... 讓 VLM 看圖選號碼
    # （比一次傳多張圖更穩；許多視覺模型對單一 image_url 反應比較可靠）
    anchor_imgs = []
    for name in anchor_names:
        p = assets_dir / name
        if not p.is_file():
            return (False, -1, f"錨點圖不存在：{name}")
        data = p.read_bytes()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return (False, -1, f"錨點圖 decode 失敗：{name}")
        anchor_imgs.append(img)

    # 全部 padded 到同高度然後加 label 條
    LABEL_H = 28
    target_h = max(im.shape[0] for im in anchor_imgs)
    strip_pieces = []
    for i, im in enumerate(anchor_imgs):
        h, w = im.shape[:2]
        if h < target_h:
            pad = np.full((target_h - h, w, 3), 255, dtype=np.uint8)
            im = np.vstack([im, pad])
        label_bar = np.full((LABEL_H, im.shape[1], 3), 30, dtype=np.uint8)
        cv2.putText(label_bar, f"[{i}]", (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2, cv2.LINE_AA)
        strip_pieces.append(np.vstack([label_bar, im]))
        # 每張之間 8 px 白色分隔
        sep = np.full((target_h + LABEL_H, 8, 3), 200, dtype=np.uint8)
        strip_pieces.append(sep)
    strip_pieces.pop()  # 去掉最後一個分隔
    anchors_strip = np.hstack(strip_pieces)

    # 螢幕 + 錨點條垂直拼接，加上「SCREEN」「ANCHORS」標頭
    def _header(text: str, width: int) -> np.ndarray:
        bar = np.full((LABEL_H, width, 3), 60, dtype=np.uint8)
        cv2.putText(bar, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 1, cv2.LINE_AA)
        return bar

    sw = screen.shape[1]
    aw = anchors_strip.shape[1]
    target_w = max(sw, aw)
    if sw < target_w:
        pad = np.full((screen.shape[0], target_w - sw, 3), 255, dtype=np.uint8)
        screen = np.hstack([screen, pad])
    if aw < target_w:
        pad = np.full((anchors_strip.shape[0], target_w - aw, 3), 255, dtype=np.uint8)
        anchors_strip = np.hstack([anchors_strip, pad])
    composite = np.vstack([
        _header("SCREEN", target_w), screen,
        _header("ANCHORS (pick one)", target_w), anchors_strip,
    ])

    sys_msg = ("你是 UI 視覺判斷器。圖分上下兩段：上段是螢幕當下，下段是數個候選錨點（按 [0][1][2]...編號）。"
               '請選一張最像螢幕上「使用者描述目標」當下狀態的錨點。回 JSON：'
               '{"index": 整數, "reason": "簡短說明"}。'
               "若沒有任何錨點符合（全都不像螢幕當下），回 index=-1。只回 JSON。")
    user_text = (f"使用者描述目標：「{prompt}」\n"
                 f"候選錨點數：{len(anchor_names)}（編號 0 到 {len(anchor_names) - 1}）\n"
                 "請選哪一個錨點最接近螢幕當下要點擊的目標。")

    ok, raw, err = _vlm_call_with_image(user_text, sys_msg, composite)
    if not ok:
        return (False, -1, err)
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return (False, -1, f"VLM 回應不是 JSON：{raw[:200]}")
    try:
        idx = int(data.get("index", -1))
    except (TypeError, ValueError):
        return (False, -1, f"VLM 回的 index 不是整數：{data}")
    reason = str(data.get("reason") or "").strip() or "(無說明)"
    if idx < 0:
        return (False, -1, f"VLM 沒有選到任何錨點：{reason}")
    if idx >= len(anchor_names):
        return (False, -1, f"VLM 選的 index={idx} 超出範圍（只有 {len(anchor_names)} 張）")
    logger.info(f"[vlm_pick] picked [{idx}] {anchor_names[idx]} reason={reason[:120]}")
    return (True, idx, reason)


def _pyautogui_with_failsafe():
    """lazy import pyautogui 並設好 failsafe / 節流"""
    import pyautogui
    pyautogui.FAILSAFE = True  # 滑鼠甩到左上角 (0,0) 立即 FailSafeException
    pyautogui.PAUSE = 0.15     # 每個 pyautogui 呼叫後自動等 150ms，防過快
    return pyautogui


def _do_click(pg, x: int, y: int, button: str, clicks: int, hold_sec: float, modifiers: list) -> None:
    """統一的點擊執行器：處理長按 + 修飾鍵。
    modifiers: ["ctrl"], ["ctrl","shift"] 等 — 按下→click→放開。

    跨螢幕瞬移防護：pyautogui.click(x=,y=) 預設是「瞬間 moveTo + click」，
    在多螢幕大跨度移動下（如從主螢幕跳到副螢幕）Windows 事件處理會 race —
    click 事件可能被路由到 cursor 飛過的中間位置或視窗動畫剛好的位置，造成
    使用者看到的「附近又多點一下」幽靈點擊。修法：先做帶 duration 的平滑
    moveTo、短暫等游標到位、再 click（不帶 x/y，用當下位置）。"""
    # 按下修飾鍵
    for mod in (modifiers or []):
        pg.keyDown(mod)
    try:
        # 先平滑移動到目標位置；duration=0.1 對單螢幕無感，跨螢幕避開瞬移 race
        # 拋例外（座標超出螢幕等）就略過 moveTo，讓 click 自己處理
        try:
            pg.moveTo(x, y, duration=0.1)
            time.sleep(0.05)   # 讓 OS 確認游標到位
        except Exception:
            pass
        if hold_sec > 0.1:
            pg.mouseDown(button=button)
            time.sleep(hold_sec)
            pg.mouseUp(button=button)
        else:
            # 不帶 x/y → click 在當下游標位置（就是上面 moveTo 過去的點）
            pg.click(button=button, clicks=clicks)
    finally:
        # 反序放開修飾鍵，即使 click 拋例外也確保按鍵會放
        for mod in reversed(modifiers or []):
            pg.keyUp(mod)


def execute_action(
    action: dict,
    assets_dir: Path,
    index: int,
    logger: logging.Logger,
    run_id: Optional[str] = None,
    allow_coord_fallback: bool = True,
    cv_threshold: float = 0.5,
    cv_search_only_near: bool = False,
    cv_search_radius: int = 400,
    cv_trigger_hover: bool = True,
    cv_hover_wait_ms: int = 200,
    cv_coord_fallback: bool = False,
    ocr_threshold: float = 0.6,
    ocr_cv_fallback: bool = False,
    _depth: int = 0,
) -> ActionResult:
    """執行單一 action。action 是 ComputerUseAction.model_dump() 結果的 dict。
    _depth: 遞迴深度（if_image_found / retry_until 巢狀時累加），防寫爛的 YAML 無限遞迴。"""
    t0 = time.time()
    atype = action.get("type", "")
    desc = action.get("description") or atype
    indent = "  " * _depth if _depth > 0 else ""
    logger.info(f"[computer_use] {indent}動作 #{index + 1} ({atype})：{desc}")

    # 遞迴深度守衛：正常使用者寫不到 10 層，超過 10 層一定是 YAML 爛掉或 copy-paste 出錯
    if _depth > 10:
        return ActionResult(False, index, atype,
            f"動作巢狀超過深度 10（if_image_found / retry_until 遞迴過深），拒絕執行")

    _check_abort(run_id)

    # Bundle all the execution kwargs so nested dispatch (if_image_found / retry_until)
    # 不用手動 re-list 每個參數
    _exec_ctx = {
        "allow_coord_fallback": allow_coord_fallback,
        "cv_threshold": cv_threshold,
        "cv_search_only_near": cv_search_only_near,
        "cv_search_radius": cv_search_radius,
        "cv_trigger_hover": cv_trigger_hover,
        "cv_hover_wait_ms": cv_hover_wait_ms,
        "cv_coord_fallback": cv_coord_fallback,
        "ocr_threshold": ocr_threshold,
        "ocr_cv_fallback": ocr_cv_fallback,
    }

    try:
        pg = _pyautogui_with_failsafe()

        if atype == "click_image":
            img_name = action.get("image", "")
            if not img_name:
                return ActionResult(False, index, atype, "click_image 缺 image 欄位")
            tpl_path = assets_dir / img_name
            # 門檻：action-level confidence 覆蓋 step 層級 cv_threshold，皆缺就用 0.5
            threshold = float(action.get("confidence") or cv_threshold)
            button = action.get("button", "left")
            clicks = int(action.get("clicks", 1))
            fx = action.get("x")
            fy = action.get("y")
            has_coord = isinstance(fx, (int, float)) and isinstance(fy, (int, float))

            hold_sec = float(action.get("hold_sec", 0) or 0)
            modifiers = list(action.get("modifiers", []) or [])
            mods_tag = f"[{'+'.join(modifiers)}]" if modifiers else ""

            # 四種 primary mode（user-feedback 排序，VLM 永遠最高優先因為使用者明確開了它）：
            #   vlm_mode=description → VLM 看圖回「目標的實際文字」→ OCR 找文字 → 點中心
            #   vlm_mode=anchor_pick → VLM 從多張候選錨點挑一張最像螢幕當下的 → 用該張走 CV 比對
            #   use_ocr 勾起 + 有 ocr_text → 走 OCR
            #   use_ocr 沒勾 + use_coord=true → 走絕對座標
            #   use_ocr 沒勾 + use_coord=false → 走 CV 圖像比對
            # VLM 永遠不直接給座標 — 它只負責「決定要找的東西」，避開 VLM 點錯位置的問題
            vlm_mode = (action.get("vlm_mode") or "off").lower()
            use_ocr = bool(action.get("use_ocr", False))
            ocr_text = (action.get("ocr_text") or "").strip()
            ocr_will_run = use_ocr and bool(ocr_text)

            # ── VLM 模式 1：description → OCR ──
            # bug 修補：之前讀錯欄位（讀紅框 search_region），這個模式既然走 OCR，
            # 區域就應該讀使用者在編輯器拉的「藍框」（ocr_box_*），跟純 OCR 路徑一致
            if vlm_mode == "description":
                vlm_prompt_click = (action.get("vlm_prompt") or action.get("description") or "").strip()
                if not vlm_prompt_click:
                    return ActionResult(False, index, atype,
                        "vlm_mode=description 但 vlm_prompt 為空（必填）")
                # 讀藍框（OCR 區域）— 跟下面純 OCR 路徑同邏輯
                _vlm_box_w = int(action.get("ocr_box_width", 0) or 0)
                _vlm_box_h = int(action.get("ocr_box_height", 0) or 0)
                vlm_region: Optional[tuple[int, int, int, int]] = None
                if _vlm_box_w > 0 and _vlm_box_h > 0:
                    vlm_region = (
                        int(action.get("ocr_box_left", 0) or 0),
                        int(action.get("ocr_box_top", 0) or 0),
                        _vlm_box_w,
                        _vlm_box_h,
                    )
                vlm_found, vlm_text, vlm_reason = _vlm_describe_to_text(vlm_prompt_click, vlm_region, logger)
                if not vlm_found:
                    return ActionResult(False, index, atype,
                        f"VLM 在螢幕找不到目標：{vlm_reason}")
                # 用 VLM 給的文字跑 OCR 找實際座標（VLM 不給座標 — 由 OCR 確定性決定）
                find_text_on_screen = None
                try:
                    from pipeline.ocr import find_text_on_screen
                except Exception:
                    try:
                        from .ocr import find_text_on_screen  # type: ignore
                    except Exception as _e:
                        return ActionResult(False, index, atype,
                            f"VLM 給了文字 '{vlm_text}' 但 OCR 模組載不進來：{_e}")
                screen_bgr_v, sxv, syv = _capture_screen()
                near_v = (int(fx), int(fy)) if has_coord else None
                ocr_res_v = find_text_on_screen(
                    screen_bgr_v, vlm_text, origin_x=sxv, origin_y=syv,
                    lang_tag="zh-Hant-TW", near_xy=near_v,
                    search_radius=cv_search_radius, threshold=ocr_threshold,
                    region=vlm_region,
                    strict_region=bool(action.get("ocr_strict_region", False)),
                )
                if not ocr_res_v.found:
                    return ActionResult(False, index, atype,
                        f"VLM 看到目標文字是 '{vlm_text}'（{vlm_reason}），但 OCR 在螢幕上找不到：{ocr_res_v.reason}")
                _do_click(pg, ocr_res_v.center[0], ocr_res_v.center[1],
                          button, clicks, hold_sec, modifiers)
                hold_tag_v = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
                msg = (f"{mods_tag} VLM→OCR 點擊 '{vlm_text}' @ {ocr_res_v.center} "
                       f"(VLM: {vlm_reason[:60]}, OCR conf={ocr_res_v.confidence:.2f}){hold_tag_v}")
                duration = int((time.time() - t0) * 1000)
                logger.info(f"[computer_use]   ✓ {msg}（{duration}ms）")
                return ActionResult(True, index, atype, msg, duration)

            # ── VLM 模式 2：anchor_pick → 用挑出的錨點走 CV ──
            if vlm_mode == "anchor_pick":
                anchor_names = list(action.get("vlm_anchors") or [])
                if not anchor_names:
                    return ActionResult(False, index, atype,
                        "vlm_mode=anchor_pick 但 vlm_anchors 為空（必填，至少 2 張變體錨點圖檔名）")
                vlm_prompt_pick = (action.get("vlm_prompt") or action.get("description") or "").strip()
                if not vlm_prompt_pick:
                    return ActionResult(False, index, atype,
                        "vlm_mode=anchor_pick 但 vlm_prompt 為空（必填，告訴 VLM 要點什麼）")
                region_rect_p = _parse_search_region(action)
                ok_pick, picked_idx, pick_reason = _vlm_pick_anchor(
                    vlm_prompt_pick, anchor_names, assets_dir, region_rect_p, logger)
                if not ok_pick:
                    return ActionResult(False, index, atype,
                        f"VLM 無法挑錨點：{pick_reason}")
                # 用挑出來的錨點走標準 CV template matching（重新指向 tpl_path）
                picked_name = anchor_names[picked_idx]
                tpl_path = assets_dir / picked_name
                logger.info(f"[computer_use]   VLM 挑了錨點 [{picked_idx}] {picked_name}（{pick_reason[:80]}）→ 走 CV 比對")
                # 不 return — 讓控制流繼續往下走 CV 路徑（vlm_mode 短路掉 OCR 模式）
                ocr_will_run = False  # 確定要走 CV 不走 OCR

            # 預設使用絕對座標（快速且穩定）；只有使用者主動切到圖像比對模式才跑 template matching
            # 注意：get 第二引數 True 表示若 action 根本沒 use_coord 欄位，也視為座標模式
            # OCR 有啟用就跳過座標短路，讓 OCR 先試（失敗再依 ocr_cv_fallback 決定退不退）
            # vlm_mode=anchor_pick 要走 CV，必須跳過座標短路
            if vlm_mode != "anchor_pick" and action.get("use_coord", True) and has_coord and not ocr_will_run:
                _do_click(pg, int(fx), int(fy), button, clicks, hold_sec, modifiers)
                hold_tag = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
                msg = f"[強制座標]{mods_tag} 點擊 ({fx},{fy}) button={button} clicks={clicks}{hold_tag}"
                duration = int((time.time() - t0) * 1000)
                logger.info(f"[computer_use]   ✓ {msg}（{duration}ms）")
                return ActionResult(True, index, atype, msg, duration)

            # Hover 預熱：錄製當下游標停在按鈕上、錨點擷取到 Windows hover highlight
            # 狀態；回放用 pyautogui 瞬移沒觸發 hover → 螢幕與錨點不一樣 conf 掉
            # 把游標移到錄製座標附近、等指定 ms 讓 hover 效果渲染後再比對
            # OCR 模式跳過 hover（純文字偵測不受 hover 影響，而且可能反而干擾游標位置）
            if cv_trigger_hover and has_coord and not ocr_will_run:
                try:
                    pg.moveTo(int(fx), int(fy))
                    time.sleep(max(50, int(cv_hover_wait_ms)) / 1000.0)
                except Exception:
                    pass  # 移動失敗就略過（例如座標超出螢幕），後面搜尋仍然照跑

            # ── OCR 模式分支 ──
            # 只有 use_ocr=True 且 ocr_text 有值才跑。OCR 失敗時的後續行為由 ocr_cv_fallback 控制：
            #   ocr_cv_fallback=False（預設）→ 失敗立即 FAIL（符合「選 OCR 就代表 CV 不適用」的直覺）
            #   ocr_cv_fallback=True         → 失敗繼續走下面的 CV 比對鏈（再受 cv_coord_fallback 接棒）
            if ocr_will_run:
                find_text_on_screen = None
                try:
                    from pipeline.ocr import find_text_on_screen
                except Exception:
                    try:
                        from .ocr import find_text_on_screen  # type: ignore
                    except Exception as _e:
                        logger.error(f"[computer_use]   ✗ 無法載入 OCR 模組：{_e}")
                if find_text_on_screen is not None:
                    screen_bgr, sx, sy = _capture_screen()
                    near = (int(fx), int(fy)) if has_coord else None
                    # 藍框：per-action 顯式 OCR 搜尋範圍（絕對桌面座標）
                    ocr_region = None
                    _box_w = int(action.get("ocr_box_width", 0) or 0)
                    _box_h = int(action.get("ocr_box_height", 0) or 0)
                    if _box_w > 0 and _box_h > 0:
                        ocr_region = (
                            int(action.get("ocr_box_left", 0) or 0),
                            int(action.get("ocr_box_top", 0) or 0),
                            _box_w,
                            _box_h,
                        )
                    ocr_res = find_text_on_screen(
                        screen_bgr, ocr_text, origin_x=sx, origin_y=sy,
                        lang_tag="zh-Hant-TW",
                        near_xy=near, search_radius=cv_search_radius,
                        threshold=ocr_threshold,
                        region=ocr_region,
                        strict_region=bool(action.get("ocr_strict_region", False)),
                    )
                    if ocr_res.found:
                        _do_click(pg, ocr_res.center[0], ocr_res.center[1],
                                  button, clicks, hold_sec, modifiers)
                        hold_tag = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
                        msg = (f"{mods_tag} 點擊 OCR 文字 '{ocr_text}' @ {ocr_res.center} "
                               f"(matched='{ocr_res.text[:30]}', conf={ocr_res.confidence:.2f}){hold_tag}")
                        duration = int((time.time() - t0) * 1000)
                        logger.info(f"[computer_use]   ✓ {msg}（{duration}ms）")
                        return ActionResult(True, index, atype, msg, duration)
                    # OCR 失敗
                    if not ocr_cv_fallback:
                        fail_msg = f"{ocr_res.reason}（ocr_cv_fallback=off → 失敗直接 FAIL 不退回 CV/座標）"
                        logger.error(f"[computer_use]   ✗ {fail_msg}")
                        return ActionResult(False, index, atype, fail_msg)
                    logger.info(f"[computer_use]   {ocr_res.reason[:120]}，ocr_cv_fallback=on → 改試 CV 比對")
                elif not ocr_cv_fallback:
                    # OCR 模組載不進來且使用者沒開 fallback → 直接 FAIL（不偷偷走 CV）
                    return ActionResult(False, index, atype, "OCR 模組無法載入且 ocr_cv_fallback=off")

            # 搜尋策略：
            # 1. 有錄製座標 → 先在附近 ±cv_search_radius 範圍搜尋（防跨螢幕假陽性）
            #    首次 match 若 conf 低於門檻，等 150ms 再 retry 一次（最多 2 次）
            #    吸收 hover fade-in / transition 動畫未穩定造成的瞬時誤判
            #    典型 case：Windows 關閉鈕第一次 match 得 0.56、再等 150ms 變 0.97
            # 2. 仍找不到：若 cv_search_only_near=True → 直接 FAIL
            #              否則擴大到整個桌面
            # 3. 全螢幕也找不到 → 退回絕對座標 fallback（下方 else 分支）
            _SETTLE_RETRIES = 2          # 第一次 + 最多 1 次 retry
            _SETTLE_WAIT_MS = 150        # retry 前 sleep

            # 使用者明確指定的搜尋紅框（優先於錄製座標附近搜尋）
            region_rect = _parse_search_region(action)
            cv_strict = bool(action.get("cv_strict_region", False))

            def _search(nx_: Optional[int], ny_: Optional[int],
                        force_region: Optional[tuple[int, int, int, int]] = "use_outer") -> MatchResult:
                """先跑 gray 模式，若 conf < threshold 再跑 edge 模式，取較高 conf。
                edge 對 hover fade / 主題色差異更容忍，代價 +20ms。

                force_region:
                  "use_outer"（預設）→ 用外層 region_rect（紅框）
                  None              → 強制忽略 region_rect，用 nx_/ny_ 或全螢幕
                  tuple             → 強制用這個 region
                """
                if force_region == "use_outer":
                    use_region = region_rect
                else:
                    use_region = force_region

                def _find(m: str) -> MatchResult:
                    if use_region is not None:
                        return find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                             region=use_region, mode=m)
                    if nx_ is not None and ny_ is not None:
                        return find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                             near_xy=(nx_, ny_), search_radius=cv_search_radius, mode=m)
                    return find_template(str(tpl_path), threshold=threshold, multi_scale=True, mode=m)
                gray = _find("gray")
                if gray.found:
                    return gray
                # Gray 沒過門檻 → 試 edge 救一下
                edge = _find("edge")
                # 以 conf 做仲裁，但考量 edge 先天分數偏低，edge 要多給 0.05 才可以勝出
                # 避免 gray 比較接近但仍低、edge 亂抓到邊緣多的位置
                if edge.found or edge.confidence >= gray.confidence + 0.05:
                    logger.info(f"[computer_use]   edge fallback: gray={gray.confidence:.2f}, edge={edge.confidence:.2f} → 採用 edge")
                    return edge
                return gray

            if has_coord:
                # Phase 1：紅框內找（如果 region_rect 設了），或近錄製座標找
                m = MatchResult(False)
                for _attempt in range(_SETTLE_RETRIES):
                    m = _search(int(fx), int(fy))
                    if m.found:
                        break
                    if _attempt + 1 < _SETTLE_RETRIES:
                        logger.info(f"[computer_use]   首次比對 conf={m.confidence:.2f} < {threshold}，等 {_SETTLE_WAIT_MS}ms 讓動畫穩定後 retry")
                        time.sleep(_SETTLE_WAIT_MS / 1000.0)
                # Phase 2：紅框 miss、不嚴格 → 退回錄製座標附近 ±cv_search_radius 找
                if not m.found and region_rect is not None and not cv_strict:
                    logger.info(f"[computer_use]   紅框內找不到 → 退回錄製座標附近 ±{cv_search_radius}px 重試")
                    m = _search(int(fx), int(fy), force_region=None)
                # Phase 3：附近 miss、不嚴格、未鎖定附近 → 擴大全螢幕
                if not m.found and not cv_search_only_near and not cv_strict:
                    logger.info(f"[computer_use]   附近 ±{cv_search_radius}px 找不到（best={m.confidence:.2f}），擴大到整個桌面")
                    m = _search(None, None, force_region=None)
                # 嚴格模式 + 紅框 miss：明確標示原因
                if not m.found and cv_strict and region_rect is not None:
                    m.reason = f"嚴格鎖定範圍：紅框內找不到 {img_name}（{m.reason}）"
            else:
                m = _search(None, None)

            if m.found:
                # 螢幕邊緣擷取時，點擊位置不在錨點影像中心，加上偏移校正
                off_x = int(action.get("anchor_off_x", 0) or 0)
                off_y = int(action.get("anchor_off_y", 0) or 0)
                click_x = m.center[0] + int(off_x * m.scale)
                click_y = m.center[1] + int(off_y * m.scale)
                _do_click(pg, click_x, click_y, button, clicks, hold_sec, modifiers)
                hold_tag = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
                off_tag = f" off=({off_x},{off_y})" if (off_x or off_y) else ""
                msg = f"{mods_tag} 點擊 {img_name} @ ({click_x},{click_y}) (conf={m.confidence:.2f} [{m.mode}], scale={m.scale}){off_tag}{hold_tag}"
            else:
                # 嚴格鎖定範圍：紅框 miss 時連座標 fallback 都禁掉
                if cv_strict and region_rect is not None:
                    fail_msg = m.reason
                    logger.error(f"[computer_use]   ✗ {fail_msg}")
                    return ActionResult(False, index, atype, fail_msg)
                # Fallback 判斷（三個條件皆需 True 才退回座標）：
                #   1. 有錄製座標 (has_coord)
                #   2. allow_coord_fallback：系統層級信心（螢幕解析度跟錄製時相同）
                #   3. cv_coord_fallback：使用者層級意願（panel toggle，預設 On）
                if has_coord and allow_coord_fallback and cv_coord_fallback:
                    logger.warning(f"[computer_use]   ⚠ 圖像比對失敗（{m.reason}），退回錄製座標 ({fx},{fy})")
                    _do_click(pg, int(fx), int(fy), button, clicks, hold_sec, modifiers)
                    hold_tag = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
                    msg = f"[fallback]{mods_tag} 點擊絕對座標 ({fx},{fy}){hold_tag}（原圖 {img_name} 找不到）"
                elif has_coord and not allow_coord_fallback:
                    fail_msg = (f"找不到錨點圖 {img_name}（{m.reason}），且目前螢幕解析度與錄製時不同，"
                        f"絕對座標 ({fx},{fy}) 不可信，請重錄或調整到原螢幕布局")
                    logger.error(f"[computer_use]   ✗ {fail_msg}")
                    return ActionResult(False, index, atype, fail_msg)
                elif has_coord and not cv_coord_fallback:
                    fail_msg = (f"找不到錨點圖 {img_name}（{m.reason}），且使用者關閉了「CV 失敗退回座標」。"
                        f"若要容錯請到 panel 打開該 toggle。")
                    logger.error(f"[computer_use]   ✗ {fail_msg}")
                    return ActionResult(False, index, atype, fail_msg)
                else:
                    fail_msg = f"找不到錨點圖 {img_name}（{m.reason}），且無 fallback 座標可用"
                    logger.error(f"[computer_use]   ✗ {fail_msg}")
                    return ActionResult(False, index, atype, fail_msg)

        elif atype == "click_at":
            x, y = int(action.get("x", 0)), int(action.get("y", 0))
            in_range, layout_info = _point_in_any_screen(x, y)
            if not in_range:
                return ActionResult(False, index, atype,
                    f"座標 ({x},{y}) 超出目前螢幕範圍（{layout_info}）")
            button = action.get("button", "left")
            clicks = int(action.get("clicks", 1))
            hold_sec = float(action.get("hold_sec", 0) or 0)
            modifiers = list(action.get("modifiers", []) or [])
            mods_tag = f"[{'+'.join(modifiers)}]" if modifiers else ""
            _do_click(pg, x, y, button, clicks, hold_sec, modifiers)
            hold_tag = f" hold={hold_sec}s" if hold_sec > 0.1 else ""
            msg = f"{mods_tag} 點擊絕對座標 ({x}, {y}){hold_tag}"

        elif atype == "type_text":
            text = action.get("text", "")
            if not text:
                return ActionResult(False, index, atype, "type_text 缺 text 欄位")
            # interval 控制打字節奏（每個字之間的間隔秒數）；中文用 write 可能失效，改 copy-paste
            if any(ord(c) > 127 for c in text):
                import pyperclip
                try:
                    pyperclip.copy(text)
                    pg.hotkey("ctrl", "v")
                    msg = f"輸入非 ASCII 文字（clipboard）：{text[:30]}"
                except Exception:
                    # 沒 pyperclip 就 fallback
                    pg.write(text, interval=0.03)
                    msg = f"輸入文字（逐字）：{text[:30]}"
            else:
                pg.write(text, interval=0.03)
                msg = f"輸入文字：{text[:30]}"

        elif atype == "hotkey":
            keys = action.get("keys", [])
            if not keys:
                return ActionResult(False, index, atype, "hotkey 缺 keys 欄位")
            # 單獨按修飾鍵（Shift / Ctrl / Alt / Win）要特別處理：
            # pyautogui.hotkey("shift") 底層用老 API keybd_event，Windows IME 的
            # 中英切換 hotkey 常常觸發不到。改用 pynput（SendInput）並明確拉長
            # press→release 間隔，讓 IME 有時間辨識為「獨立按 tap」。
            _MOD_TO_PYNPUT = {"shift": "shift", "ctrl": "ctrl", "alt": "alt",
                              "win": "cmd", "cmd": "cmd"}
            if len(keys) == 1 and keys[0].lower() in _MOD_TO_PYNPUT:
                from pynput.keyboard import Controller as _KC, Key as _K
                _kc = _KC()
                _pk = getattr(_K, _MOD_TO_PYNPUT[keys[0].lower()])
                _kc.press(_pk)
                time.sleep(0.12)
                _kc.release(_pk)
                msg = f"單按 {keys[0]}（pynput tap，IME-safe）"
            else:
                pg.hotkey(*keys)
                msg = f"熱鍵：{'+'.join(keys)}"

        elif atype == "wait":
            sec = float(action.get("seconds", 0.0))
            # 分段 sleep，中間可以 abort
            total, step = sec, 0.2
            while total > 0:
                _check_abort(run_id)
                time.sleep(min(step, total))
                total -= step
            msg = f"等待 {sec}s"

        elif atype == "wait_image":
            img_name = action.get("image", "")
            if not img_name:
                return ActionResult(False, index, atype, "wait_image 缺 image 欄位")
            tpl_path = assets_dir / img_name
            timeout = float(action.get("timeout_sec", 10.0))
            # action.confidence 沒設或為 0 → 退步驟層級 cv_threshold（跟 click_image 一致）
            threshold = float(action.get("confidence") or cv_threshold)
            region_rect = _parse_search_region(action)
            deadline = time.time() + timeout
            last_conf = 0.0
            while time.time() < deadline:
                _check_abort(run_id)
                m = find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                  region=region_rect)
                if m.found:
                    msg = f"{img_name} 出現（conf={m.confidence:.2f}）"
                    break
                last_conf = max(last_conf, m.confidence)
                time.sleep(0.3)
            else:
                return ActionResult(False, index, atype,
                    f"等待 {timeout}s 仍未出現 {img_name}（最佳 {last_conf:.2f} < {threshold}）")

        elif atype == "drag":
            x1 = int(action.get("x", 0))
            y1 = int(action.get("y", 0))
            x2 = int(action.get("x2", 0))
            y2 = int(action.get("y2", 0))
            button = action.get("button", "left")
            # 起點：預設使用絕對座標；只有使用者切到圖像模式（use_coord=False）才嘗試圖像定位校正
            img_name = action.get("image", "")
            if img_name and action.get("use_coord", True) is False:
                tpl_path = assets_dir / img_name
                # drag 也吃 step 層級 cv_threshold / cv_search_radius
                threshold = float(action.get("confidence") or cv_threshold)
                m = find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                  near_xy=(x1, y1), search_radius=cv_search_radius)
                if m.found:
                    dx = m.center[0] - x1
                    dy_shift = m.center[1] - y1
                    x1, y1 = m.center[0], m.center[1]
                    # 終點同步偏移，保持相對位移
                    x2 += dx
                    y2 += dy_shift
                elif cv_search_only_near:
                    return ActionResult(False, index, atype,
                        f"【只搜附近模式】drag 起點在 ({x1},{y1}) ±{cv_search_radius}px 內找不到錨點 {img_name}")
            # 座標防護：超出螢幕就拒絕執行
            for cx, cy, label in [(x1, y1, "起點"), (x2, y2, "終點")]:
                in_range, layout_info = _point_in_any_screen(cx, cy)
                if not in_range:
                    return ActionResult(False, index, atype,
                        f"拖曳{label}座標 ({cx},{cy}) 超出目前螢幕（{layout_info}）")
            # Windows 的 DragDetect 要求 mouseDown 後第一個 move 必須**嚴格超過 SM_CXDRAG (~4px)**
            # 才觸發真正的 OLE Drag-Drop。pyautogui.dragTo + 平順 lerp 常常第一步 < 4px 就被當
            # 普通點擊。解法：press 前從偏移位置抵達產生「pre-move delta」，press 後立刻做一個
            # 6px 的明顯跳躍突破閾值，再開始平滑 lerp。
            # 參考：https://devblogs.microsoft.com/oldnewthing/20100304-00/?p=14733
            from pynput.mouse import Controller as _MC, Button as _Btn
            _mc = _MC()
            _btn_map = {"left": _Btn.left, "right": _Btn.right, "middle": _Btn.middle}
            _btn = _btn_map.get(button, _Btn.left)
            drag_mods = list(action.get("modifiers", []) or [])
            # 修飾鍵在整個拖曳期間都要按著（Shift+drag=移動、Ctrl+drag=複製）
            for mod in drag_mods:
                pg.keyDown(mod)
            try:
                # 計算單位方向（用來做 6px 初始跨閾值跳躍；若起終點距離 < 6px 就固定往右跳）
                dx = x2 - x1
                dy = y2 - y1
                dist = max(1, (dx * dx + dy * dy) ** 0.5)
                nx, ny = dx / dist, dy / dist

                # 1. 先從偏移位置抵達起點，產生真實的 pre-move event
                _mc.position = (int(x1 - nx * 3), int(y1 - ny * 3))
                time.sleep(0.05)
                _mc.position = (x1, y1)
                time.sleep(0.08)
                # 2. 按下
                _mc.press(_btn)
                time.sleep(0.10)
                # 3. 關鍵：press 後第一個 move 必須 > 4px 突破 SM_CXDRAG
                _mc.position = (int(x1 + nx * 6), int(y1 + ny * 6))
                time.sleep(0.06)
                # 4. 剩餘距離分段平滑移動到終點
                steps = 25
                total_move_sec = 0.6
                for i in range(1, steps + 1):
                    t = i / steps
                    mx = int(x1 + nx * 6 + (x2 - (x1 + nx * 6)) * t)
                    my = int(y1 + ny * 6 + (y2 - (y1 + ny * 6)) * t)
                    _mc.position = (mx, my)
                    time.sleep(total_move_sec / steps)
                # 5. 在終點停頓，讓 drop target highlight 起來再放手
                time.sleep(0.25)
                _mc.release(_btn)
            finally:
                # 即使過程拋例外也要放開修飾鍵，避免使用者鍵盤卡在按下狀態
                for mod in reversed(drag_mods):
                    pg.keyUp(mod)
            mods_tag = f"[{'+'.join(drag_mods)}] " if drag_mods else ""
            msg = f"{mods_tag}拖曳 ({x1},{y1}) → ({x2},{y2}) button={button}"

        elif atype == "scroll":
            x = int(action.get("x", 0))
            y = int(action.get("y", 0))
            dy = int(action.get("dy", 0))
            if dy == 0:
                logger.warning(f"[computer_use]   ⚠ scroll action dy=0，略過（action={action}）")
                return ActionResult(False, index, atype, "scroll 缺 dy 欄位或為 0")
            modifiers = list(action.get("modifiers", []) or [])
            # 座標防護：超出螢幕時不移動滑鼠直接在當前位置捲
            in_range, _ = _point_in_any_screen(x, y)
            if in_range:
                pg.moveTo(x, y)
                # Windows 上滑鼠移入新視窗需要短時間觸發 hover，否則後續 scroll 會被吞掉
                time.sleep(0.15)
            # 用 pynput 取代 pyautogui.scroll（pyautogui 在 Windows 有 known bug）
            from pynput.mouse import Controller as _MC
            _mc = _MC()
            # 按下修飾鍵（Ctrl+滾輪 = 縮放）→ scroll → 放開
            for mod in modifiers:
                pg.keyDown(mod)
            try:
                _mc.scroll(0, dy)
            finally:
                for mod in reversed(modifiers):
                    pg.keyUp(mod)
            mods_tag = f"[{'+'.join(modifiers)}] " if modifiers else ""
            msg = f"{mods_tag}在 ({x},{y}) 捲動 dy={dy}"

        elif atype == "activate_window":
            # 將指定標題的視窗帶到前景。解決錄製回放最常見的失敗原因：
            # 目標視窗在背景 → 點擊被其他視窗截去 or hover 作用在錯的視窗。
            # Linux 下 pygetwindow 支援很薄，用 try/except 吞例外並回 FAIL 讓使用者知情。
            title = (action.get("title") or "").strip()
            title_contains = (action.get("title_contains") or "").strip()
            if not title and not title_contains:
                return ActionResult(False, index, atype,
                    "activate_window 缺 title 或 title_contains 欄位")
            timeout = float(action.get("timeout_sec", 3.0))
            try:
                import pygetwindow as gw
            except Exception as e:
                return ActionResult(False, index, atype,
                    f"pygetwindow 無法載入（此平台可能不支援）：{e}")

            def _find_win():
                try:
                    all_wins = gw.getAllWindows()
                except Exception:
                    return []
                if title:
                    wins = [w for w in all_wins if (w.title or "") == title]
                else:
                    needle = title_contains.lower()
                    wins = [w for w in all_wins if needle in (w.title or "").lower()]
                return [w for w in wins if (w.title or "").strip()]

            deadline = time.time() + timeout
            target = None
            while True:
                _check_abort(run_id)
                matched = _find_win()
                if matched:
                    target = matched[0]
                    break
                if time.time() >= deadline:
                    break
                time.sleep(0.2)

            if target is None:
                needle = title or title_contains
                return ActionResult(False, index, atype,
                    f"{timeout}s 內找不到視窗標題 ~= '{needle}'")

            activated = False
            try:
                # 最小化的視窗必須先 restore 才能被 activate（pygetwindow 已實作這邏輯但不保證）
                if getattr(target, "isMinimized", False):
                    try:
                        target.restore()
                    except Exception:
                        pass
                target.activate()
                activated = True
            except Exception as _gw_err:
                # pygetwindow 在 foreground lock 等情境會拋 PyGetWindowException；改用 Win32 直接搶焦點
                try:
                    import ctypes  # type: ignore
                    hwnd = getattr(target, "_hWnd", None)
                    if hwnd:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        activated = True
                except Exception:
                    pass
                if not activated:
                    return ActionResult(False, index, atype,
                        f"找到視窗 '{target.title[:60]}' 但無法 activate：{_gw_err}")
            # 給 Window Manager 時間切換焦點，避免下個動作時視窗還沒完全在前
            time.sleep(0.25)
            msg = f"已將視窗 '{(target.title or '')[:60]}' 切到前景"

        elif atype == "assert_image":
            # 驗證某張錨點圖「當下」必須可見（和 wait_image 相似但語意不同：
            # wait_image 等畫面載入、timeout 較長；assert_image 檢查當前狀態、timeout 較短）。
            # 失敗訊息也更精確，方便排查為什麼流程走到這一步畫面長得不對。
            img_name = action.get("image", "")
            if not img_name:
                return ActionResult(False, index, atype, "assert_image 缺 image 欄位")
            tpl_path = assets_dir / img_name
            timeout = float(action.get("timeout_sec", 2.0))
            threshold = float(action.get("confidence") or cv_threshold)
            region_rect = _parse_search_region(action)
            deadline = time.time() + timeout
            last_conf = 0.0
            found_m: Optional[MatchResult] = None
            while True:
                _check_abort(run_id)
                m = find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                  region=region_rect)
                if m.found:
                    found_m = m
                    break
                last_conf = max(last_conf, m.confidence)
                if time.time() >= deadline:
                    break
                time.sleep(0.2)
            if found_m is None:
                return ActionResult(False, index, atype,
                    f"assert 失敗：{timeout}s 內 {img_name} 未出現（最佳 {last_conf:.2f} < {threshold}）")
            msg = f"assert 通過：{img_name} 可見（conf={found_m.confidence:.2f}）"

        elif atype == "assert_text":
            # OCR 版本的 assert：驗證螢幕上應該有某段文字。
            # 常見用途：登入成功後檢查「歡迎回來」、錯誤訊息檢查、狀態列文字等。
            text = (action.get("text") or action.get("ocr_text") or "").strip()
            if not text:
                return ActionResult(False, index, atype, "assert_text 缺 text 欄位")
            find_text_on_screen = None
            try:
                from pipeline.ocr import find_text_on_screen
            except Exception:
                try:
                    from .ocr import find_text_on_screen  # type: ignore
                except Exception as _e:
                    return ActionResult(False, index, atype, f"無法載入 OCR 模組：{_e}")
            timeout = float(action.get("timeout_sec", 2.0))
            threshold = float(action.get("ocr_threshold") or ocr_threshold)
            # 沿用既有 ocr_box_* 欄位做 OCR 搜尋範圍（藍框）
            _box_w = int(action.get("ocr_box_width", 0) or 0)
            _box_h = int(action.get("ocr_box_height", 0) or 0)
            region = None
            if _box_w > 0 and _box_h > 0:
                region = (
                    int(action.get("ocr_box_left", 0) or 0),
                    int(action.get("ocr_box_top", 0) or 0),
                    _box_w, _box_h,
                )
            deadline = time.time() + timeout
            last_reason = ""
            found_ocr = None
            while True:
                _check_abort(run_id)
                screen_bgr, sx, sy = _capture_screen()
                ocr_res = find_text_on_screen(
                    screen_bgr, text, origin_x=sx, origin_y=sy,
                    lang_tag="zh-Hant-TW",
                    threshold=threshold, region=region,
                    strict_region=bool(action.get("ocr_strict_region", False)),
                )
                if ocr_res.found:
                    found_ocr = ocr_res
                    break
                last_reason = ocr_res.reason
                if time.time() >= deadline:
                    break
                time.sleep(0.3)
            if found_ocr is None:
                return ActionResult(False, index, atype,
                    f"assert 失敗：{timeout}s 內未偵測到文字 '{text}'（{last_reason}）")
            msg = (f"assert 通過：文字 '{text}' 可見 @ {found_ocr.center} "
                   f"(matched='{found_ocr.text[:30]}', conf={found_ocr.confidence:.2f})")

        elif atype == "if_image_found":
            # 條件分支：根據錨點圖是否可見，選擇執行 then[] 或 else[]
            # 不叫 LLM、不燒 token — 純 CV template matching（跟 click_image 同一套）
            # 常見用途：
            #   1. 處理偶爾跳出的對話框（密碼過期、更新提示、網路錯誤）
            #   2. 登入狀態判斷（session 在 / 過期 兩種畫面）
            img_name = action.get("image", "")
            if not img_name:
                return ActionResult(False, index, atype, "if_image_found 缺 image 欄位")
            tpl_path = assets_dir / img_name
            timeout = float(action.get("timeout_sec", 2.0))
            threshold = float(action.get("confidence") or cv_threshold)
            region_rect = _parse_search_region(action)

            # 在 timeout 內等錨點出現；可能 0.3s 就找到、也可能等到 deadline
            deadline = time.time() + timeout
            found = False
            best_conf = 0.0
            while True:
                _check_abort(run_id)
                m = find_template(str(tpl_path), threshold=threshold, multi_scale=True,
                                  region=region_rect)
                if m.found:
                    found = True
                    best_conf = m.confidence
                    break
                best_conf = max(best_conf, m.confidence)
                if time.time() >= deadline:
                    break
                time.sleep(0.2)

            branch = action.get("then", []) if found else action.get("else", [])
            branch = branch or []
            branch_label = "then" if found else "else"
            logger.info(f"[computer_use] {indent}  → {img_name} "
                        f"{'found' if found else 'not found'} (conf={best_conf:.2f}) "
                        f"→ 走 {branch_label} 分支（{len(branch)} 個子動作）")

            for sub_i, sub_action in enumerate(branch):
                if not isinstance(sub_action, dict):
                    return ActionResult(False, index, atype,
                        f"{branch_label}[{sub_i}] 不是 dict，YAML 格式錯誤")
                sub_res = execute_action(
                    sub_action, assets_dir, sub_i, logger, run_id,
                    _depth=_depth + 1, **_exec_ctx,
                )
                if not sub_res.ok:
                    return ActionResult(False, index, atype,
                        f"if_image_found/{branch_label}[{sub_i+1}] "
                        f"({sub_res.action_type}) 失敗：{sub_res.message}")
            msg = (f"if {img_name}: {'match' if found else 'no-match'} "
                   f"→ 執行 {branch_label}（{len(branch)} 個子動作皆 OK）")

        elif atype == "retry_until":
            # 重複動作直到條件滿足：按鈕沒反應再按一次、網路抖動後重試
            # do[]  = 每輪要執行的動作清單
            # until = 檢查是否完成的單一動作（建議 wait_image / assert_image / assert_text）
            do_list = action.get("do", []) or []
            until_action = action.get("until", None)
            if not do_list:
                return ActionResult(False, index, atype, "retry_until 缺 do: 動作清單")
            if until_action is None or not isinstance(until_action, dict):
                return ActionResult(False, index, atype,
                    "retry_until 缺 until: 檢查條件（必須是單一動作 dict）")
            max_attempts = int(action.get("max_attempts", 3) or 3)
            wait_between = float(action.get("wait_between_sec", 1.0) or 1.0)
            if max_attempts < 1:
                max_attempts = 1

            last_fail_reason = ""
            success = False
            for attempt in range(1, max_attempts + 1):
                _check_abort(run_id)
                logger.info(f"[computer_use] {indent}  retry_until 第 {attempt}/{max_attempts} 輪")
                # 1. 跑 do[] 裡所有動作
                attempt_do_ok = True
                for sub_i, sub_a in enumerate(do_list):
                    if not isinstance(sub_a, dict):
                        return ActionResult(False, index, atype,
                            f"do[{sub_i}] 不是 dict，YAML 格式錯誤")
                    sub_res = execute_action(
                        sub_a, assets_dir, sub_i, logger, run_id,
                        _depth=_depth + 1, **_exec_ctx,
                    )
                    if not sub_res.ok:
                        attempt_do_ok = False
                        last_fail_reason = (f"第 {attempt} 輪 do[{sub_i+1}] "
                                            f"({sub_res.action_type}) 失敗：{sub_res.message}")
                        logger.info(f"[computer_use] {indent}    {last_fail_reason[:160]}")
                        break
                # 2. 跑 until 檢查
                if attempt_do_ok:
                    until_res = execute_action(
                        until_action, assets_dir, 0, logger, run_id,
                        _depth=_depth + 1, **_exec_ctx,
                    )
                    if until_res.ok:
                        success = True
                        msg = f"retry_until 成功於第 {attempt}/{max_attempts} 輪（{until_res.message[:80]}）"
                        break
                    last_fail_reason = f"第 {attempt} 輪 until 未通過：{until_res.message}"
                    logger.info(f"[computer_use] {indent}    {last_fail_reason[:160]}")
                # 3. 還有輪次就等一下再重試
                if attempt < max_attempts:
                    # sleep 分段好讓 abort 能及時生效
                    remaining = wait_between
                    while remaining > 0:
                        _check_abort(run_id)
                        chunk = min(0.3, remaining)
                        time.sleep(chunk)
                        remaining -= chunk

            if not success:
                return ActionResult(False, index, atype,
                    f"retry_until {max_attempts} 輪仍未成功：{last_fail_reason}")

        elif atype == "vlm_check":
            # 純判斷不點擊：把當下畫面送 Settings 主模型（必須支援視覺）
            # 用途：登入後確認成功訊息、確認對話框出現、檢查表單填好等
            # pass=false 步驟即失敗，VLM 寫的 reason 會出現在錯誤訊息中
            prompt = (action.get("vlm_prompt") or action.get("description") or "").strip()
            if not prompt:
                return ActionResult(False, index, atype,
                    "vlm_check 缺 vlm_prompt（判斷條件必填）")
            region_rect = _parse_search_region(action)
            passed, reason = _vlm_judge_screen(prompt, region_rect, logger)
            if not passed:
                return ActionResult(False, index, atype,
                    f"VLM 判斷未通過：{reason}")
            msg = f"VLM 判斷通過：{reason[:120]}"

        elif atype == "screenshot":
            import cv2
            img, _ox, _oy = _capture_screen()
            ts = int(time.time())
            out = assets_dir / f"debug_screenshot_{ts}.png"
            # 用 imencode + write_bytes 避免中文路徑問題
            ok, buf = cv2.imencode(".png", img)
            if ok:
                out.write_bytes(buf.tobytes())
                msg = f"已存 screenshot：{out.name}"
            else:
                msg = "screenshot imencode 失敗"

        else:
            return ActionResult(False, index, atype, f"未知動作類型：{atype}")

        duration = int((time.time() - t0) * 1000)
        logger.info(f"[computer_use]   ✓ {msg}（{duration}ms）")
        return ActionResult(True, index, atype, msg, duration)

    except RuntimeError as e:
        # abort signal
        raise
    except Exception as e:
        # pyautogui.FailSafeException / 其他意外
        import traceback
        logger.error(f"[computer_use]   ✗ {atype} 失敗：{e}")
        logger.debug(traceback.format_exc())
        return ActionResult(False, index, atype, f"{type(e).__name__}: {e}",
                            int((time.time() - t0) * 1000))


# ── 對外入口：執行一整個 computer_use 步驟 ─────────────────────────

@dataclass
class StepResult:
    success: bool
    total_actions: int
    succeeded: int
    failed_at: int = -1        # 首次失敗的 index；-1 = 全部成功
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    # UIA / Computer Use 透過 save_as 累積的步驟變數(uia_get_text 等存入)。
    # runner 拿到後寫進 PipelineRun.step_results[i].step_vars,後續 step 可用
    # `{{ steps.<name>.output.<key> }}` 引用。
    step_variables: dict = field(default_factory=dict)


MAX_ACTIONS_PER_STEP = 500  # 單步動作數上限，防止失控腳本無限循環


def validate_action_assets(actions: list[dict], assets_dir: Path) -> list[str]:
    """Preflight：掃一遍 actions 裡引用到的所有錨點圖是否存在（含巢狀 then/else/do/until）。
    提早 FAIL 比回放跑到一半才發現圖不見好太多，也讓使用者錯誤訊息更集中。
    回傳缺失檔名 list（保留順序、去重）。"""
    missing: list[str] = []
    seen: set[str] = set()

    def _scan(acts: list) -> None:
        for a in acts:
            if not isinstance(a, dict):
                continue
            for key in ("image", "image2"):
                name = a.get(key) or ""
                if not name or name in seen:
                    continue
                seen.add(name)
                if not (assets_dir / name).is_file():
                    missing.append(name)
            # vlm_mode=anchor_pick 用的多張候選錨點圖也要檢查
            for name in (a.get("vlm_anchors") or []):
                if not name or name in seen:
                    continue
                seen.add(name)
                if not (assets_dir / name).is_file():
                    missing.append(name)
            # 遞迴掃 if_image_found / retry_until 的巢狀動作
            for sub_key in ("then", "else", "do"):
                sub = a.get(sub_key)
                if isinstance(sub, list):
                    _scan(sub)
            until_a = a.get("until")
            if isinstance(until_a, dict):
                _scan([until_a])

    _scan(actions)
    return missing


def _screen_layout_match(meta_path: Path, logger: logging.Logger) -> bool:
    """比對錄製時與回放時的螢幕解析度。
    True = 一致（絕對座標 fallback 仍可靠）；False = 已改變（座標 fallback 不可信，應禁用）"""
    if not meta_path.is_file():
        return True  # 沒 meta 就寬容處理
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rec_w, rec_h = meta.get("screen_width"), meta.get("screen_height")
        if not rec_w or not rec_h:
            return True
        import mss
        with mss.mss() as sct:
            cur = sct.monitors[1]
        if cur["width"] == rec_w and cur["height"] == rec_h:
            return True
        logger.warning(
            f"[computer_use] ⚠ 螢幕解析度變了："
            f"錄製 {rec_w}×{rec_h} → 目前 {cur['width']}×{cur['height']}；"
            f"將禁用絕對座標 fallback，強制圖像比對（常見於接/拔外接螢幕後）"
        )
        return False
    except Exception as e:
        logger.warning(f"[computer_use] 讀 meta.json 失敗：{e}")
        return True


def execute_computer_use_step(
    actions: list[dict],
    assets_dir: str,
    logger: logging.Logger,
    run_id: Optional[str] = None,
    fail_fast: bool = True,
    cv_threshold: float = 0.5,
    cv_search_only_near: bool = False,
    cv_search_radius: int = 400,
    cv_trigger_hover: bool = True,
    cv_hover_wait_ms: int = 200,
    cv_coord_fallback: bool = False,
    ocr_threshold: float = 0.6,
    ocr_cv_fallback: bool = False,
    # VLM 把關 Phase 1 參數(預設 off、不影響舊行為):
    cu_vlm_check_strategy: str = "off",     # off / after_each / critical_only
    cu_on_mismatch: str = "stop_notify",    # stop_notify / retry_once / skip_and_continue
    cu_vlm_max_retries: int = 1,
    # UIA 模式相關(action.type 是 uia_* 時用)
    uia_window: str = "",                   # 視窗 title pattern(可含 *)、空字串 = foreground
) -> StepResult:
    """執行一整個 computer_use 步驟。

    - actions: ComputerUseAction 物件的 list of dict
    - assets_dir: 錨點圖片資料夾（絕對路徑，通常是 ai_output/<name>/ 下的子資料夾）
    - fail_fast: True 則遇到失敗立刻中止；False 則繼續但記錄失敗數
    - cv_threshold: CV 比對門檻（0.50 寬鬆 / 0.80 標準 / 0.90 嚴格）
    - cv_search_only_near: True = 只搜錄製座標附近、找不到直接 FAIL（不退回全螢幕也不退回座標）
    - cv_search_radius: 附近搜尋半徑（像素）；實際搜尋範圍 (2r × 2r)
    - cv_trigger_hover: True = 比對前先 moveTo(錄製座標) + 200ms 讓 Windows hover 效果出現
    """
    import json  # 供 _screen_layout_match 讀 meta.json
    clear_abort(run_id or "")
    if len(actions) > MAX_ACTIONS_PER_STEP:
        return StepResult(
            success=False,
            total_actions=len(actions),
            succeeded=0,
            failed_at=-1,
            stdout="",
            stderr=f"動作數 {len(actions)} 超過安全上限 {MAX_ACTIONS_PER_STEP}，拒絕執行",
            exit_code=2,
        )
    assets = Path(assets_dir)
    if not assets.is_dir():
        # 沒有 assets 目錄也可能 OK（例如只有 type_text / wait），不直接失敗
        logger.warning(f"[computer_use] assets 目錄不存在：{assets_dir}")
    else:
        # 錨點圖 preflight：避免跑到一半才發現圖不見
        missing_imgs = validate_action_assets(actions, assets)
        if missing_imgs:
            preview = ", ".join(missing_imgs[:5])
            more = f"...（共 {len(missing_imgs)} 張）" if len(missing_imgs) > 5 else ""
            return StepResult(
                success=False,
                total_actions=len(actions),
                succeeded=0,
                failed_at=0,
                stdout="",
                stderr=f"preflight 失敗：assets_dir 缺少錨點圖：{preview}{more}",
                exit_code=2,
            )

    # 螢幕解析度比對：若改變（接/拔外接螢幕）就禁用座標 fallback
    layout_ok = _screen_layout_match(assets / "meta.json", logger) if assets.is_dir() else True

    logger.info(f"[computer_use] ▶ 開始執行 {len(actions)} 個動作 "
                f"（assets: {assets_dir}, fail_fast={fail_fast}）")
    logger.info(f"[computer_use] 🛡 Safety: 滑鼠移到螢幕左上角 (0,0) 可立即中止")

    succeeded = 0
    failed_at = -1
    messages: list[str] = []
    # 跨 action 變數儲存(uia_get_text / uia_get_table_rowcount 用 save_as 存的)
    # 後續 action 內 {{var_name}} 替換靠這個
    step_variables: dict[str, Any] = {}

    # VLM 把關 Phase 1 開關:strategy != off 才啟動驗證 loop;否則整段邏輯跳過、不影響舊行為
    _vlm_active = (cu_vlm_check_strategy or "off").lower() != "off"
    if _vlm_active:
        logger.info(f"[computer_use] 🛡 VLM 把關啟用 strategy={cu_vlm_check_strategy} on_mismatch={cu_on_mismatch}")

    def _should_verify(action: dict) -> bool:
        """依 strategy + action 欄位決定是否要送 VLM 驗。"""
        if not _vlm_active:
            return False
        expected = (action.get("expected") or "").strip()
        if not expected:
            return False
        if cu_vlm_check_strategy == "after_each":
            return True
        if cu_vlm_check_strategy == "critical_only":
            return bool(action.get("verify_critical", False))
        return False

    def _capture_to_png(prefix: str, idx: int) -> str:
        """抓螢幕、寫 PNG、回路徑;失敗回空字串。"""
        try:
            import cv2 as _cv
            img, _w, _h = _capture_screen()
            out_path = assets / f"_vlm_{prefix}_{idx:03d}.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ok, buf = _cv.imencode(".png", img)
            if not ok:
                return ""
            out_path.write_bytes(buf.tobytes())
            return str(out_path)
        except Exception as e:
            logger.warning(f"[computer_use] _capture_to_png 失敗 ({prefix}_{idx:03d}): {e}")
            return ""

    def _run_vlm_verify(before: str, after: str, expected: str) -> dict:
        """同步包裝 async verify_action_outcome、給 sync loop 內呼叫。"""
        from .cu_vlm_verifier import verify_action_outcome
        import asyncio as _aio
        try:
            # execute_computer_use_step 在 run_in_executor 內、無 event loop、可直接 asyncio.run
            return _aio.run(verify_action_outcome(
                before_path=before,
                after_path=after,
                expected=expected,
                logger=logger,
                timeout_sec=30.0,
            ))
        except RuntimeError as e:
            # 萬一被誤從 async context 呼叫(不該發生、但保險)、退到 new loop
            if "event loop" in str(e).lower():
                _loop = _aio.new_event_loop()
                try:
                    return _loop.run_until_complete(verify_action_outcome(
                        before_path=before, after_path=after,
                        expected=expected, logger=logger, timeout_sec=30.0,
                    ))
                finally:
                    _loop.close()
            raise

    def _push_mismatch_to_tg(action_idx: int, action: dict, verdict: dict, after_path: str) -> None:
        """VLM 驗證失敗時 push TG 截圖 + verdict、不繞 chat agent(直接走 telegram Bot)。"""
        try:
            from pipeline.runner import _get_tg_token, _get_tg_chat_id
            from telegram import Bot
            import asyncio as _aio2
        except Exception as e:
            logger.debug(f"[computer_use] TG push import 失敗、跳過: {e}")
            return
        token = _get_tg_token()
        chat_id = _get_tg_chat_id()
        if not token or not chat_id:
            logger.debug("[computer_use] TG token / chat_id 未設、跳過 push")
            return

        action_type = action.get("type", "?")
        expected = action.get("expected", "")
        caption = (
            f"❌ Computer use 步驟 {action_idx+1}/{len(actions)} 偏離預期\n\n"
            f"動作: {action_type}\n"
            f"預期: {expected[:200]}\n\n"
            f"VLM 判定: 不符合 ({verdict.get('mismatch_type', '?')})\n"
            f"原因: {verdict.get('reason', '')[:300]}"
        )
        if verdict.get("unexpected"):
            caption += f"\n意外元素: {verdict['unexpected'][:200]}"

        async def _send():
            async with Bot(token=token) as bot:
                if after_path and Path(after_path).exists():
                    with open(after_path, "rb") as f:
                        await bot.send_photo(chat_id=int(chat_id), photo=f, caption=caption[:1024])
                else:
                    await bot.send_message(chat_id=int(chat_id), text=caption)

        try:
            _aio2.run(_send())
        except Exception as e:
            logger.warning(f"[computer_use] TG push 失敗(忽略): {e}")

    for i, action in enumerate(actions):
        do_verify = _should_verify(action)
        before_path = _capture_to_png("before", i) if do_verify else ""

        # 路由:uia_* type 走 uia_executor、其他 type 走原 pixel-based execute_action
        atype = action.get("type", "")
        if atype.startswith("uia_"):
            try:
                from .uia_executor import execute_uia_action
                uia_res = execute_uia_action(action, uia_window, step_variables, logger)
                # 把結果包成 ActionResult 兼容後續流程
                res = ActionResult(
                    ok=uia_res.ok,
                    action_index=i,
                    action_type=atype,
                    message=uia_res.message,
                    duration_ms=0,
                )
                # 收集 save_as 變數
                if uia_res.saved_var:
                    var_name, var_value = uia_res.saved_var
                    step_variables[var_name] = var_value
                    logger.info(f"[computer_use] 變數 {var_name} = {var_value!r:.80}")
            except Exception as e:
                logger.exception(f"[computer_use] uia action {atype} 例外")
                res = ActionResult(ok=False, action_index=i, action_type=atype,
                                   message=f"{type(e).__name__}: {e}")
            messages.append(f"#{i+1} [{res.action_type}] {'OK' if res.ok else 'FAIL'}: {res.message}")
            if res.ok:
                succeeded += 1
            else:
                if failed_at < 0:
                    failed_at = i
                if fail_fast:
                    return StepResult(
                        success=False, total_actions=len(actions),
                        succeeded=succeeded, failed_at=i,
                        stdout="\n".join(messages),
                        stderr=f"動作 #{i+1} ({atype}) 失敗:{res.message}",
                        exit_code=1,
                        step_variables=dict(step_variables),
                    )
            continue  # uia 動作不走 VLM 把關(它本來就讀結構、漂移免疫)

        try:
            res = execute_action(action, assets, i, logger, run_id,
                                 allow_coord_fallback=layout_ok,
                                 cv_threshold=cv_threshold,
                                 cv_search_only_near=cv_search_only_near,
                                 cv_search_radius=cv_search_radius,
                                 cv_trigger_hover=cv_trigger_hover,
                                 cv_hover_wait_ms=cv_hover_wait_ms,
                                 cv_coord_fallback=cv_coord_fallback,
                                 ocr_threshold=ocr_threshold,
                                 ocr_cv_fallback=ocr_cv_fallback)
        except RuntimeError as abort_err:
            logger.warning(f"[computer_use] {abort_err}")
            return StepResult(
                success=False,
                total_actions=len(actions),
                succeeded=succeeded,
                failed_at=i,
                stdout="\n".join(messages),
                stderr=str(abort_err),
                exit_code=130,  # SIGINT-ish
                step_variables=dict(step_variables),
            )
        messages.append(f"#{i+1} [{res.action_type}] {'OK' if res.ok else 'FAIL'}: {res.message}")

        # VLM 把關:動作沒立即失敗(res.ok)且需驗 → 截後圖 + 送 VLM 比對 expected
        # 動作本身已 fail 走原有 fail 路徑、不浪費 VLM token
        if do_verify and res.ok:
            time.sleep(0.3)  # 給 UI render 時間、太快截圖會抓到動作前狀態
            after_path = _capture_to_png("after", i)
            if not before_path or not after_path:
                logger.warning(f"[computer_use] 動作 #{i+1} VLM 截圖不齊全(before={bool(before_path)}, after={bool(after_path)})、跳過驗證")
            else:
                expected = (action.get("expected") or "").strip()
                _retry_count = 0
                while True:
                    verdict = _run_vlm_verify(before_path, after_path, expected)
                    logger.info(f"[computer_use] 動作 #{i+1} VLM verdict: ok={verdict['ok']} reason={verdict['reason'][:100]}")
                    if verdict["ok"]:
                        break
                    # 不 ok、依 cu_on_mismatch 處理
                    if cu_on_mismatch == "skip_and_continue":
                        logger.warning(f"[computer_use] 動作 #{i+1} VLM 失敗、skip_and_continue 模式繼續: {verdict['reason'][:120]}")
                        messages.append(f"  ⚠ VLM mismatch ignored: {verdict['reason'][:120]}")
                        break
                    if cu_on_mismatch == "retry_once" and _retry_count < cu_vlm_max_retries:
                        _retry_count += 1
                        logger.info(f"[computer_use] 動作 #{i+1} VLM 失敗、retry {_retry_count}/{cu_vlm_max_retries}")
                        # 重執行同動作:不重新截 before、用同 before 比新 after
                        try:
                            res = execute_action(action, assets, i, logger, run_id,
                                                 allow_coord_fallback=layout_ok,
                                                 cv_threshold=cv_threshold,
                                                 cv_search_only_near=cv_search_only_near,
                                                 cv_search_radius=cv_search_radius,
                                                 cv_trigger_hover=cv_trigger_hover,
                                                 cv_hover_wait_ms=cv_hover_wait_ms,
                                                 cv_coord_fallback=cv_coord_fallback,
                                                 ocr_threshold=ocr_threshold,
                                                 ocr_cv_fallback=ocr_cv_fallback)
                        except RuntimeError as abort_err:
                            return StepResult(
                                success=False, total_actions=len(actions),
                                succeeded=succeeded, failed_at=i,
                                stdout="\n".join(messages), stderr=str(abort_err),
                                exit_code=130,
                                step_variables=dict(step_variables),
                            )
                        if not res.ok:
                            messages.append(f"  ⚠ retry {_retry_count} 動作直接失敗: {res.message}")
                            break  # 動作 fail、跳出 retry loop、走下面 res.ok=False 邏輯
                        time.sleep(0.3)
                        after_path = _capture_to_png("after_retry", i)
                        continue
                    # stop_notify(預設)或 retry 用完仍失敗
                    logger.warning(f"[computer_use] 動作 #{i+1} VLM 失敗、stop_notify: {verdict['reason'][:200]}")
                    messages.append(f"  ❌ VLM mismatch (stop_notify): {verdict['reason'][:200]}")
                    _push_mismatch_to_tg(i, action, verdict, after_path)
                    return StepResult(
                        success=False,
                        total_actions=len(actions),
                        succeeded=succeeded,
                        failed_at=i,
                        stdout="\n".join(messages),
                        stderr=f"VLM 把關失敗(動作 #{i+1} {res.action_type}): {verdict['reason'][:200]}",
                        exit_code=2,  # 區別動作 fail (=1) 和 VLM mismatch (=2)
                        step_variables=dict(step_variables),
                    )

        if res.ok:
            succeeded += 1
        else:
            if failed_at < 0:
                failed_at = i
            if fail_fast:
                return StepResult(
                    success=False,
                    total_actions=len(actions),
                    succeeded=succeeded,
                    failed_at=i,
                    stdout="\n".join(messages),
                    stderr=f"動作 #{i + 1} ({res.action_type}) 失敗：{res.message}",
                    exit_code=1,
                    step_variables=dict(step_variables),
                )

    all_ok = (failed_at < 0)
    logger.info(f"[computer_use] ■ 結束：{succeeded}/{len(actions)} 成功")
    return StepResult(
        success=all_ok,
        total_actions=len(actions),
        succeeded=succeeded,
        failed_at=failed_at,
        stdout="\n".join(messages),
        stderr="" if all_ok else f"失敗動作數：{len(actions) - succeeded}",
        exit_code=0 if all_ok else 1,
        step_variables=dict(step_variables),
    )
