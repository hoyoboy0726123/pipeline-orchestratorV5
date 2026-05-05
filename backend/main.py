"""
Pipeline Orchestrator — 獨立後端
啟動：uvicorn main:app --host 0.0.0.0 --port 8002
"""
# Windows console 預設 cp1252/cp950 無法印 emoji / 中文 → 啟動時強制 UTF-8
# 不靠 PYTHONIOENCODING env var，避免使用者沒設或 .bat 傳遞失效
import sys as _sys
try:
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import check_config
from scheduler.manager import start as sched_start, shutdown as sched_shutdown

app = FastAPI(title="Pipeline Orchestrator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3005", "http://127.0.0.1:3005",  # V5 dev port
                   "http://localhost:3004", "http://127.0.0.1:3004",  # V4 dev port
                   "http://localhost:3003", "http://127.0.0.1:3003",
                   "http://localhost:3002", "http://127.0.0.1:3002",
                   "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    from db import init_db
    init_db()
    print("✅ SQLite 資料庫已初始化")
    # 自動安裝 skill_packages.txt 中缺少的套件
    from skill_pkg_manager import auto_install_packages
    auto_install_packages()
    await sched_start()
    print("✅ Pipeline Scheduler 已啟動")
    from telegram_handler import start_polling as tg_start
    await tg_start()
    print("✅ Telegram callback polling 已啟動")


@app.on_event("shutdown")
async def shutdown():
    await sched_shutdown()
    from telegram_handler import stop_polling as tg_stop
    await tg_stop()


# ── Health ───────────────────────────────────────────────────
@app.get("/health")
async def health():
    missing = check_config()
    return {"status": "ok", "warnings": [f"{k} 未設定" for k in missing]}


# ── Settings（模型選擇）─────────────────────────────────────
# 排除的 Groq 模型（非文字生成用途）
_GROQ_EXCLUDE_PREFIXES = ("whisper-", "llama-prompt-guard", "canopylabs/orpheus")

# Gemini 可用於文字生成的模型前綴（排除 embedding, tts, robotics, audio 等）
_GEMINI_TEXT_PREFIXES = ("gemini-2.5-", "gemini-2.0-", "gemini-3-", "gemini-3.", "gemma-")
_GEMINI_EXCLUDE_KEYWORDS = ("tts", "audio", "embedding", "robotics", "image", "live", "customtools", "computer-use")

# 支援思考模式的 Gemini 模型前綴
_GEMINI_THINKING_PREFIXES = ("gemini-2.5-", "gemini-3-", "gemini-3.")


@app.get("/settings/model")
async def get_model_settings():
    from settings import get_settings
    return get_settings()


class ModelSettingsRequest(BaseModel):
    provider: str
    model: str
    ollama_base_url: Optional[str] = None
    ollama_thinking: Optional[str] = None   # "auto" | "on" | "off"
    ollama_num_ctx: Optional[int] = None
    gemini_thinking: Optional[str] = None   # "off" | "auto" | "low" | "medium" | "high"
    openrouter_thinking: Optional[str] = None  # "off" | "on"


@app.put("/settings/model")
async def put_model_settings(req: ModelSettingsRequest):
    from settings import update_settings
    try:
        return update_settings(
            req.provider, req.model, req.ollama_base_url, req.ollama_thinking, req.ollama_num_ctx,
            req.gemini_thinking, req.openrouter_thinking,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── models/available 快取：4 個外部 API 每次都打太慢，5 分鐘記憶體快取 ──
_MODELS_CACHE: dict = {"ts": 0.0, "data": None}
_MODELS_CACHE_TTL = 300.0  # 秒


@app.get("/settings/models/available")
async def get_available_models(refresh: bool = False):
    """動態列出各 provider 可用模型。有 5 分鐘快取，加 ?refresh=true 強制更新。"""
    import time as _time
    import asyncio as _asyncio
    import httpx
    from config import GROQ_API_KEY as _groq_key, GEMINI_API_KEY as _gemini_key, OPENROUTER_API_KEY as _or_key

    # 命中快取直接回，~5ms
    if not refresh and _MODELS_CACHE["data"] and (_time.time() - _MODELS_CACHE["ts"]) < _MODELS_CACHE_TTL:
        return _MODELS_CACHE["data"]

    ollama_models: list[dict] = []
    ollama_error: Optional[str] = None
    groq_models: list[dict] = []
    groq_error: Optional[str] = None
    gemini_models: list[dict] = []
    gemini_error: Optional[str] = None
    openrouter_models: list[dict] = []
    openrouter_error: Optional[str] = None

    base_url = "http://localhost:11434"
    try:
        from settings import get_settings as _gs
        base_url = _gs().get("ollama_base_url") or base_url
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=8.0) as client:
        # ── 每個 provider 包成獨立 coroutine，用 asyncio.gather 併發 ──
        async def fetch_groq() -> tuple[list[dict], Optional[str]]:
            if not _groq_key:
                return [], "未設定 GROQ_API_KEY"
            try:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {_groq_key}"},
                )
                r.raise_for_status()
                models = []
                for m in r.json().get("data", []):
                    mid = m.get("id", "")
                    if not m.get("active", True):
                        continue
                    if any(mid.startswith(p) for p in _GROQ_EXCLUDE_PREFIXES):
                        continue
                    ctx = m.get("context_window", 0)
                    owner = m.get("owned_by", "")
                    label = mid
                    if owner:
                        label += f"（{owner}"
                        if ctx:
                            label += f", ctx={ctx // 1024}K"
                        label += "）"
                    models.append({"id": mid, "label": label})
                models.sort(key=lambda x: x["id"])
                return models, None
            except Exception as e:
                return [], f"Groq API 錯誤：{e}"

        async def fetch_gemini() -> tuple[list[dict], Optional[str]]:
            if not _gemini_key:
                return [], "未設定 GEMINI_API_KEY"
            try:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={_gemini_key}",
                )
                r.raise_for_status()
                models = []
                for m in r.json().get("models", []):
                    mid = m.get("name", "").replace("models/", "")
                    if not any(mid.startswith(p) for p in _GEMINI_TEXT_PREFIXES):
                        continue
                    if any(kw in mid for kw in _GEMINI_EXCLUDE_KEYWORDS):
                        continue
                    display = m.get("displayName", mid)
                    supports_thinking = any(mid.startswith(p) for p in _GEMINI_THINKING_PREFIXES)
                    label = display
                    if supports_thinking:
                        label += "（支援思考）"
                    models.append({"id": mid, "label": label, "supports_thinking": supports_thinking})
                models.sort(key=lambda x: x["id"])
                return models, None
            except Exception as e:
                return [], f"Gemini API 錯誤：{e}"

        async def fetch_openrouter() -> tuple[list[dict], Optional[str]]:
            try:
                r = await client.get("https://openrouter.ai/api/v1/models")
                r.raise_for_status()
                models = []
                for m in r.json().get("data", []):
                    pricing = m.get("pricing", {})
                    if str(pricing.get("prompt", "1")) != "0" or str(pricing.get("completion", "1")) != "0":
                        continue
                    mid = m.get("id", "")
                    ctx = m.get("context_length", 0)
                    name = m.get("name", mid)
                    label = name
                    if ctx:
                        label += f"（ctx={ctx // 1024}K）"
                    models.append({"id": mid, "label": label, "context_length": ctx})
                models.sort(key=lambda x: x["id"])
                return models, None
            except Exception as e:
                return [], f"OpenRouter API 錯誤：{e}"

        async def fetch_ollama() -> tuple[list[dict], Optional[str]]:
            try:
                r = await client.get(f"{base_url.rstrip('/')}/api/tags", timeout=2.0)
                r.raise_for_status()
                models = []
                for m in r.json().get("models", []):
                    name = m.get("name") or m.get("model")
                    if not name:
                        continue
                    size = m.get("size", 0)
                    size_gb = f"{size / 1024 / 1024 / 1024:.1f} GB" if size else ""
                    models.append({"id": name, "label": f"{name}" + (f"（{size_gb}）" if size_gb else "")})
                return models, None
            except Exception as e:
                return [], f"無法連線 Ollama（{base_url}）：{e}"

        # 四條 coroutine 一口氣併發執行，總時間 ≈ max 而不是 sum
        (groq_models, groq_error), (gemini_models, gemini_error), \
        (openrouter_models, openrouter_error), (ollama_models, ollama_error) = \
            await _asyncio.gather(fetch_groq(), fetch_gemini(), fetch_openrouter(), fetch_ollama())

    payload = {
        "groq": groq_models,
        "groq_error": groq_error,
        "gemini": gemini_models,
        "gemini_error": gemini_error,
        "openrouter": openrouter_models,
        "openrouter_error": openrouter_error,
        "ollama": ollama_models,
        "ollama_base_url": base_url,
        "ollama_error": ollama_error,
    }
    _MODELS_CACHE["ts"] = _time.time()
    _MODELS_CACHE["data"] = payload
    return payload


# ── 專案環境路徑（給前端 AI 助手生成真實可用的範例）────────────
@app.get("/env/paths")
async def get_env_paths():
    """回傳使用者目前專案的關鍵絕對路徑，讓前端範例能顯示真實可貼上執行的指令。"""
    import os as _os
    from pathlib import Path as _P
    project_root = _P(__file__).parent.parent.absolute()
    test_workflows = project_root / "test-workflows"
    finance_dir = test_workflows / "finance"
    return {
        "project_root": str(project_root),
        "test_workflows_dir": str(test_workflows) if test_workflows.is_dir() else None,
        "has_finance_example": finance_dir.is_dir() and (finance_dir / "stage1_generate_transactions.py").is_file(),
        "finance_example_dir": str(finance_dir) if finance_dir.is_dir() else None,
        "path_sep": _os.sep,
    }


# ── Node.js 環境檢測 ────────────────────────────────────────
_NODE_CACHE: dict = {"ts": 0.0, "data": None}
_NODE_CACHE_TTL = 60.0


@app.get("/settings/node-status")
async def get_node_status():
    """檢查系統是否安裝 Node.js / npm，含版本號。有 60s 快取。"""
    import time as _time
    import subprocess
    import shutil as _shutil
    if _NODE_CACHE["data"] and (_time.time() - _NODE_CACHE["ts"]) < _NODE_CACHE_TTL:
        return _NODE_CACHE["data"]

    def _probe(cmd: str) -> tuple[bool, str]:
        exe = _shutil.which(cmd)
        if not exe:
            return False, ""
        try:
            r = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=5)
            return (r.returncode == 0), (r.stdout or "").strip()
        except Exception:
            return False, ""

    node_ok, node_ver = _probe("node")
    npm_ok, npm_ver = _probe("npm")
    payload = {
        "node_installed": node_ok,
        "node_version": node_ver,
        "npm_installed": npm_ok,
        "npm_version": npm_ver,
        "install_hint": "https://nodejs.org/ 下載 LTS 版本；或執行 `winget install OpenJS.NodeJS.LTS`（Windows）",
    }
    _NODE_CACHE["ts"] = _time.time()
    _NODE_CACHE["data"] = payload
    return payload


# ── Skill Packages ──────────────────────────────────────────
@app.get("/settings/skill-packages")
async def get_skill_packages(target: str = "auto"):
    """列出 skill 套件。
    target: "auto"（跟著 skill_sandbox_mode 走）/ "host" / "sandbox"
    回傳含 `target` 欄位讓前端知道實際對象。"""
    from skill_pkg_manager import list_packages_by_target
    return list_packages_by_target(target)


class SkillPackageRequest(BaseModel):
    name: str
    target: str = "auto"


@app.post("/settings/skill-packages")
async def add_skill_package(req: SkillPackageRequest):
    from skill_pkg_manager import add_package_by_target
    ok, msg, resolved = add_package_by_target(req.name, req.target)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "target": resolved}


@app.delete("/settings/skill-packages/{pkg_name}")
async def remove_skill_package(pkg_name: str, target: str = "auto"):
    from skill_pkg_manager import remove_package_by_target
    ok, msg, resolved = remove_package_by_target(pkg_name, target)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "target": resolved}


@app.get("/settings/skill-packages/unlisted")
async def scan_unlisted_skill_packages():
    """掃 venv 中已安裝但不在 skill_packages.txt 也不在 requirements.txt 的套件。"""
    from skill_pkg_manager import scan_unlisted_packages
    return {"packages": scan_unlisted_packages()}


class AdoptPackageRequest(BaseModel):
    name: str


@app.post("/settings/skill-packages/adopt")
async def adopt_existing_package(req: AdoptPackageRequest):
    """把已安裝的套件加入 skill_packages.txt（不再重新 install）。"""
    from skill_pkg_manager import add_to_list_only
    ok, msg = add_to_list_only(req.name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


# ── Computer Use 錄製器 ──────────────────────────────────────
class RecordingStartRequest(BaseModel):
    session_id: str
    # 相對路徑 → 解析到專案根；絕對路徑直接用
    output_dir: str


@app.post("/computer-use/recording/start")
async def start_computer_use_recording(req: RecordingStartRequest):
    """開始錄製一個 computer_use session（鎖定單一進程）。"""
    from pipeline.recorder import start_recording
    from pathlib import Path as _P
    out_path = _P(req.output_dir).expanduser()
    if not out_path.is_absolute():
        _PROJ = _P(__file__).parent.parent.absolute()
        out_path = _PROJ / out_path
    try:
        return start_recording(session_id=req.session_id, output_dir=str(out_path))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/computer-use/recording/stop")
async def stop_computer_use_recording():
    """停止目前錄製中的 session，flush actions.json + meta.json。"""
    from pipeline.recorder import stop_recording
    return stop_recording()


@app.get("/computer-use/recording/status")
async def get_computer_use_recording_status():
    """查詢目前錄製中 session 的即時狀態（前端 polling 用）。"""
    from pipeline.recorder import get_recording_status
    return get_recording_status()


@app.get("/computer-use/recording/load")
async def load_computer_use_recording(output_dir: str):
    """讀回已錄好的 session（actions + meta），供前端編輯器載入。"""
    from pipeline.recorder import load_recording
    from pathlib import Path as _P
    out_path = _P(output_dir).expanduser()
    if not out_path.is_absolute():
        _PROJ = _P(__file__).parent.parent.absolute()
        out_path = _PROJ / out_path
    result = load_recording(str(out_path))
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


def _validate_assets_path(path_str: str) -> "Path":
    """把 assets 相關路徑解析成絕對 Path，並強制限制在 ai_output/ 內（安全防呆）。"""
    from pathlib import Path as _P
    _PROJ = _P(__file__).parent.parent.absolute()
    _ALLOWED_PREFIXES = [
        (_PROJ / "ai_output").resolve(),
        (_PROJ / "backend" / "ai_output").resolve(),
    ]
    target = _P(path_str).expanduser()
    if not target.is_absolute():
        target = _PROJ / target
    try:
        target_resolved = target.resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"路徑解析失敗：{e}")
    is_allowed = any(
        str(target_resolved).startswith(str(pfx) + os.sep) or str(target_resolved) == str(pfx)
        for pfx in _ALLOWED_PREFIXES
    )
    if not is_allowed:
        raise HTTPException(status_code=403,
            detail=f"拒絕存取：路徑不在允許範圍內（只能動 ai_output/ 下的檔案）。")
    return target_resolved


@app.get("/computer-use/assets/list")
async def list_assets(dir: str):
    """列出 assets_dir 內的 PNG 錨點檔。給「VLM 挑錨點」的檔案選擇器用 —
    使用者錄完動作後，這個目錄會有 img_NNN.png（自動截）跟 img_NNN_manual.png
    （手動圈），這兩種都是合法錨點；full_NNN.png 是全螢幕截圖（給編輯器顯示
    用），不是錨點，過濾掉。"""
    target_dir = _validate_assets_path(dir)
    if not target_dir.is_dir():
        return {"dir": str(target_dir), "files": []}
    files = []
    for p in sorted(target_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        if p.name.startswith("full_"):
            continue   # 全螢幕截圖不是錨點
        try:
            stat = p.stat()
            files.append({
                "name": p.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        except OSError:
            continue
    return {"dir": str(target_dir), "files": files}


@app.get("/computer-use/assets/image")
async def get_assets_image(dir: str, name: str):
    """提供單一錨點/全螢幕 PNG 檔供前端顯示（Modal 編輯錨點時用）。
    Query：dir=assets 資料夾（相對或絕對）、name=檔名"""
    from fastapi.responses import FileResponse
    target_dir = _validate_assets_path(dir)
    target_file = target_dir / name
    # 二次防呆：確保 file 也在 target_dir 內（防 name 含 ..）
    try:
        rf = target_file.resolve()
        if not str(rf).startswith(str(target_dir) + os.sep):
            raise HTTPException(status_code=403, detail="檔名不合法")
    except Exception:
        raise HTTPException(status_code=403, detail="檔名不合法")
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"檔案不存在：{name}")
    return FileResponse(str(target_file), media_type="image/png")


class CropRequest(BaseModel):
    dir: str                # assets 資料夾
    full_image: str         # 來源全螢幕截圖檔名（full_NNN.png）
    click_x: int            # 點擊的虛擬桌面絕對座標 X
    click_y: int            # 點擊的虛擬桌面絕對座標 Y
    full_left: int = 0      # 全螢幕截圖對應的虛擬桌面原點 X（可能是負值）
    full_top: int = 0       # 全螢幕截圖對應的虛擬桌面原點 Y
    # 使用者選的裁切區域（虛擬桌面絕對座標系）
    crop_left: int
    crop_top: int
    crop_width: int
    crop_height: int
    save_as: str            # 輸出檔名（例如 img_003_manual.png）


@app.get("/screen/snapshot")
async def get_screen_snapshot():
    """即時抓「整個虛擬桌面」一張 PNG，回 base64。視覺驗證節點的「螢幕區域拉選器」用。

    回傳：
      origin_x / origin_y：虛擬桌面左上角的絕對座標（多螢幕配置可能是負值）
      width / height：截圖像素尺寸
      image_b64：PNG base64（前端直接塞進 <img src="data:image/png;base64,..."/>）

    座標系跟 computer_use 一致：使用者拉出的矩形 [l, t, w, h] 都用「虛擬桌面絕對座標」。"""
    try:
        import base64
        import mss as _mss
        from mss.tools import to_png as _to_png
        with _mss.mss() as sct:
            mon = sct.monitors[0]   # 虛擬桌面全景（含所有實體螢幕聯集）
            shot = sct.grab(mon)
            # to_png(data, size, output=None) → 直接回 PNG bytes（output=path 才寫檔）
            png_bytes = _to_png(shot.rgb, shot.size)
        return {
            "origin_x": int(mon["left"]),
            "origin_y": int(mon["top"]),
            "width": int(mon["width"]),
            "height": int(mon["height"]),
            "image_b64": base64.b64encode(png_bytes).decode(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"螢幕擷取失敗：{e}")


@app.get("/computer-use/monitors")
async def get_computer_use_monitors():
    """列出實體螢幕的幾何（虛擬桌面絕對座標）。
    前端錨點編輯器用這個做「只看單螢幕」的切換 — 多螢幕時整張 full_*.png 被 fit 到
    viewport 會變很小，切單螢幕後畫面可以放大到看清楚。
    回傳 monitors[0] 為虛擬桌面全景、monitors[1..N] 為每台實體螢幕。"""
    try:
        import mss as _mss
        with _mss.mss() as sct:
            monitors = [
                {"left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
                for m in sct.monitors
            ]
        return {"monitors": monitors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"讀取 monitor 清單失敗：{e}")


@app.post("/computer-use/assets/crop")
async def crop_anchor_from_full(req: CropRequest):
    """從全螢幕截圖裁出新錨點。
    - 回傳新錨點檔名 + anchor_off_x/y（點擊相對新錨點中心的偏移）+ variance
    - 支援多螢幕負座標（full_left/top 可以是負的）"""
    import cv2
    import numpy as np
    target_dir = _validate_assets_path(req.dir)
    full_path = target_dir / req.full_image
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"全螢幕截圖不存在：{req.full_image}")

    # 讀 full 圖（支援中文路徑 → 走 read_bytes + imdecode）
    try:
        buf = np.frombuffer(full_path.read_bytes(), dtype=np.uint8)
        full_img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"讀取全螢幕截圖失敗：{e}")
    if full_img is None:
        raise HTTPException(status_code=500, detail=f"全螢幕截圖解碼失敗：{req.full_image}")

    H, W = full_img.shape[:2]
    # 絕對座標 → full 圖的相對座標
    rel_left = req.crop_left - req.full_left
    rel_top = req.crop_top - req.full_top
    rel_right = rel_left + req.crop_width
    rel_bottom = rel_top + req.crop_height
    # 邊界 clamp
    rel_left = max(0, min(rel_left, W))
    rel_top = max(0, min(rel_top, H))
    rel_right = max(0, min(rel_right, W))
    rel_bottom = max(0, min(rel_bottom, H))
    if rel_right - rel_left < 20 or rel_bottom - rel_top < 20:
        raise HTTPException(status_code=400,
            detail=f"裁切範圍太小（{rel_right-rel_left}×{rel_bottom-rel_top}，最小 20×20）")

    cropped = full_img[rel_top:rel_bottom, rel_left:rel_right]
    # 點擊位置相對裁切圖的偏移（依絕對座標計算）
    actual_crop_abs_left = rel_left + req.full_left
    actual_crop_abs_top = rel_top + req.full_top
    actual_w = rel_right - rel_left
    actual_h = rel_bottom - rel_top
    click_dx = req.click_x - actual_crop_abs_left
    click_dy = req.click_y - actual_crop_abs_top
    anchor_off_x = click_dx - actual_w // 2
    anchor_off_y = click_dy - actual_h // 2

    # 特徵豐富度（variance）
    try:
        gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        variance = float(np.var(gray))
    except Exception:
        variance = 0.0

    # 存檔
    save_name = req.save_as
    if not save_name.endswith(".png"):
        save_name += ".png"
    out_path = target_dir / save_name
    try:
        ok, enc = cv2.imencode(".png", cropped)
        if not ok:
            raise HTTPException(status_code=500, detail="imencode 失敗")
        out_path.write_bytes(enc.tobytes())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"寫檔失敗：{e}")

    return {
        "image": save_name,
        "anchor_off_x": anchor_off_x,
        "anchor_off_y": anchor_off_y,
        "width": actual_w,
        "height": actual_h,
        "variance": round(variance, 1),
    }


@app.delete("/computer-use/assets")
async def delete_computer_use_assets(dir: str):
    """刪除指定的錨點資料夾（含 PNG、actions.json、meta.json）。
    用於：Panel 清除全部、刪除節點時的清理。
    安全限制：只允許刪除專案根目錄下 ai_output/ 或 backend/ai_output/ 內的路徑，
    避免誤刪系統檔案。"""
    import shutil
    from pathlib import Path as _P
    _PROJ = _P(__file__).parent.parent.absolute()
    _ALLOWED_PREFIXES = [
        (_PROJ / "ai_output").resolve(),
        (_PROJ / "backend" / "ai_output").resolve(),
    ]
    target = _P(dir).expanduser()
    if not target.is_absolute():
        target = _PROJ / target
    try:
        target_resolved = target.resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"路徑解析失敗：{e}")
    # 必須在允許的資料夾內
    is_allowed = any(
        str(target_resolved).startswith(str(pfx) + os.sep) or str(target_resolved) == str(pfx)
        for pfx in _ALLOWED_PREFIXES
    )
    if not is_allowed:
        raise HTTPException(status_code=403,
            detail=f"拒絕刪除：路徑不在允許範圍內（只能刪 ai_output/ 下的子資料夾）。"
                   f"target={target_resolved}")
    if not target_resolved.exists():
        return {"deleted": False, "reason": "資料夾不存在", "path": str(target_resolved)}
    if not target_resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"路徑不是資料夾：{target_resolved}")
    try:
        shutil.rmtree(target_resolved)
        return {"deleted": True, "path": str(target_resolved)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刪除失敗：{e}")


# ── Claude Code Skills（從 ~/.agents/skills/ 掃描）──────────
@app.get("/skills/available")
async def get_available_skills():
    """列出使用者安裝的 Claude Code skills（掃 ~/.agents/skills/）。"""
    from skill_scanner import list_available_skills, SKILLS_ROOT
    return {
        "skills_root": str(SKILLS_ROOT),
        "exists": SKILLS_ROOT.exists(),
        "skills": list_available_skills(),
    }


@app.get("/skills/{skill_name}/dependencies")
async def scan_skill_deps(skill_name: str):
    """掃描指定 skill 的 Python / Node.js 依賴。
    pip 已安裝列表跟著當前 sandbox 模式走（host venv 或 sandbox container）；
    之前寫死 `list_packages()` 永遠看 host、切到容器模式時所有容器套件都顯示「未安裝」。
    """
    from skill_scanner import scan_skill_dependencies
    result = scan_skill_dependencies(skill_name)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"找不到 skill：{skill_name}")
    # 加上目前已安裝的 pip 套件，前端可對照
    # 走 skill_pkg_manager 的 normalize_pkg_name — 整個 backend 唯一的正規化函式，
    # 之前這裡有自己一份只做 .lower() 的弱化版、會把 `lxml_html_clean` 跟
    # `lxml-html-clean` 視為不同套件、UI 顯示「未安裝」是假警報
    from skill_pkg_manager import list_packages_by_target, normalize_pkg_name

    # 用 target=auto 自動跟著 settings.skill_sandbox_mode（host 或 sandbox）走
    pkg_resp = list_packages_by_target("auto")
    pkg_list = pkg_resp.get("packages") or []
    installed_bases = {normalize_pkg_name(p["name"]) for p in pkg_list if p.get("installed")}
    suggested = result["python"]["suggested_pip"]

    result["python"]["installed"] = sorted(s for s in suggested if normalize_pkg_name(s) in installed_bases)
    result["python"]["missing"] = [s for s in suggested if normalize_pkg_name(s) not in installed_bases]

    # npm 套件也做已安裝對比（跑 `npm list -g`）
    from skill_scanner import list_global_npm_packages
    suggested_npm = result.get("node", {}).get("suggested_npm") or []
    if suggested_npm:
        global_npm = list_global_npm_packages()
        if global_npm:
            result["node"]["installed_npm"] = sorted(p for p in suggested_npm if p.lower() in global_npm)
            result["node"]["missing_npm"] = [p for p in suggested_npm if p.lower() not in global_npm]
            result["node"]["npm_available"] = True
        else:
            # 沒抓到任何全域套件 → npm 不存在或掃描失敗，無法判斷
            result["node"]["installed_npm"] = []
            result["node"]["missing_npm"] = []
            result["node"]["npm_available"] = False
    return result


# ── Notification Settings ──────────────────────────────────
class NotificationSettingsRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_remote_control: Optional[bool] = None
    line_notify_token: Optional[str] = None


def _notification_settings_payload(s: dict) -> dict:
    # 額外回傳「.env 是否有 fallback 值」flag — UI 用來判斷是否可啟用遠端遙控
    # 不直接把 env 值帶到前端（避免明文外洩 / 用戶誤以為已存到 settings）
    import os as _os
    env_token = (_os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    env_chat = (_os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
    return {
        "telegram_bot_token": s.get("telegram_bot_token", ""),
        "telegram_chat_id": s.get("telegram_chat_id", ""),
        "telegram_remote_control": bool(s.get("telegram_remote_control", False)),
        "line_notify_token": s.get("line_notify_token", ""),
        "telegram_bot_token_env_present": bool(env_token),
        "telegram_chat_id_env_present": bool(env_chat),
    }


@app.get("/settings/notifications")
async def get_notification_settings():
    from settings import get_settings
    return _notification_settings_payload(get_settings())


@app.put("/settings/notifications")
async def put_notification_settings(req: NotificationSettingsRequest):
    from settings import get_settings, _SETTINGS_PATH, _lock
    import json as _json
    import settings as _settings_mod
    s = get_settings()
    if req.telegram_bot_token is not None:
        s["telegram_bot_token"] = req.telegram_bot_token.strip()
    if req.telegram_chat_id is not None:
        s["telegram_chat_id"] = req.telegram_chat_id.strip()
    if req.telegram_remote_control is not None:
        s["telegram_remote_control"] = bool(req.telegram_remote_control)
    if req.line_notify_token is not None:
        s["line_notify_token"] = req.line_notify_token.strip()
    with _lock:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            _json.dump(s, f, ensure_ascii=False, indent=2)
        _settings_mod._cache = s
    return _notification_settings_payload(s)


# ── Web Search (Tavily) ────────────────────────────────────
class WebSearchSettingsRequest(BaseModel):
    tavily_api_key: Optional[str] = None
    web_search_enabled: Optional[bool] = None
    web_search_full_content_default: Optional[bool] = None


def _web_search_response_dict(s: dict) -> dict:
    # 回傳給前端的格式：不直接回 key 明文（只回「是否已設定」的 has_key flag）
    # 這樣前端重新載入頁面時，不會把使用者 key 帶回 input 欄位造成誤覆蓋（使用者得重打才能改）
    return {
        "has_key": bool((s.get("tavily_api_key") or "").strip()),
        "web_search_enabled": bool(s.get("web_search_enabled")),
        "web_search_full_content_default": bool(s.get("web_search_full_content_default")),
    }


@app.get("/settings/web-search")
async def get_web_search_settings():
    from settings import get_settings
    return _web_search_response_dict(get_settings())


@app.put("/settings/web-search")
async def put_web_search_settings(req: WebSearchSettingsRequest):
    from settings import get_settings, _SETTINGS_PATH, _lock
    import json as _json
    import settings as _settings_mod
    s = get_settings()
    # key：空字串當「清除」，非空字串覆寫。未提供（None）= 不動
    if req.tavily_api_key is not None:
        s["tavily_api_key"] = req.tavily_api_key.strip()
    if req.web_search_enabled is not None:
        s["web_search_enabled"] = bool(req.web_search_enabled)
    if req.web_search_full_content_default is not None:
        s["web_search_full_content_default"] = bool(req.web_search_full_content_default)
    with _lock:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            _json.dump(s, f, ensure_ascii=False, indent=2)
        _settings_mod._cache = s
    return _web_search_response_dict(s)


# ── Skill Sandbox (V3) ─────────────────────────────────────
@app.get("/settings/sandbox")
async def get_sandbox_status(refresh: bool = False):
    """回傳沙盒目前狀態 + 設定模式，供前端顯示燈號與 toggle。"""
    from settings import get_settings
    from pipeline import sandbox as _sandbox
    mode = (get_settings().get("skill_sandbox_mode") or "host").strip()
    status = _sandbox.check_status(force_refresh=bool(refresh))
    return {
        "mode": mode,
        **status,
    }


class SandboxModeRequest(BaseModel):
    mode: str  # "host" | "wsl_docker"


@app.put("/settings/sandbox")
async def put_sandbox_mode(req: SandboxModeRequest):
    """切換沙盒模式。切到 wsl_docker 時順便回傳目前健康狀態。
    切換時 invalidate 三個快取（host pip / sandbox pip / npm globals），
    讓前端下次 refetch 拿到正確 mode 的資料、不要顯示前一個 mode 的殘留。
    """
    from settings import set_skill_sandbox_mode
    from pipeline import sandbox as _sandbox
    try:
        updated = set_skill_sandbox_mode(req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 清快取：mode 變了之後、所有「跟 mode 連動」的查詢都該重抓
    try:
        from skill_pkg_manager import _invalidate_pip_cache, _invalidate_sandbox_pip_cache
        _invalidate_pip_cache()
        _invalidate_sandbox_pip_cache()
    except Exception:
        pass
    try:
        from skill_scanner import _NPM_CACHE
        _NPM_CACHE["ts"] = 0.0
        _NPM_CACHE["data"] = set()
    except Exception:
        pass
    status = _sandbox.check_status(force_refresh=True)
    return {"mode": updated.get("skill_sandbox_mode", "host"), **status}


# ── Workflows CRUD ──────────────────────────────────────────
class WorkflowRequest(BaseModel):
    name: str = "新工作流"
    canvas: Optional[dict] = None
    validate: bool = False


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    canvas: Optional[dict] = None
    validate: Optional[bool] = None
    yaml: Optional[str] = None


@app.get("/workflows")
async def api_list_workflows():
    from db import list_workflows
    return list_workflows()


@app.post("/workflows")
async def api_create_workflow(req: WorkflowRequest):
    from db import create_workflow
    return create_workflow(name=req.name, canvas=req.canvas, validate=req.validate)


@app.get("/workflows/{wf_id}")
async def api_get_workflow(wf_id: str):
    from db import get_workflow
    wf = get_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="找不到工作流")
    return wf


@app.put("/workflows/{wf_id}")
async def api_update_workflow(wf_id: str, req: WorkflowUpdateRequest):
    from db import update_workflow
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    wf = update_workflow(wf_id, patch)
    if not wf:
        raise HTTPException(status_code=404, detail="找不到工作流")
    return wf


@app.delete("/workflows/{wf_id}")
async def api_delete_workflow(wf_id: str, cascade: bool = True):
    from db import delete_workflow
    delete_workflow(wf_id, cascade=cascade)
    return {"deleted": True, "cascade": cascade}


# ── Workflow Export / Import ─────────────────────────────────

@app.get("/workflows/{wf_id}/export")
async def api_export_workflow(wf_id: str):
    import io
    import zipfile
    from db import get_workflow, list_recipes
    from fastapi.responses import StreamingResponse

    wf = get_workflow(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="找不到工作流")

    recipes = list_recipes(wf_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # workflow.json
        wf_export = {
            "name": wf["name"],
            "canvas": wf["canvas"],
            "validate": wf["validate"],
            "yaml": wf.get("yaml", ""),
        }
        zf.writestr("workflow.json", json.dumps(wf_export, ensure_ascii=False, indent=2))

        # recipes/
        for r in recipes:
            recipe_data = {
                "step_name": r["step_name"],
                "task_hash": r["task_hash"],
                "input_fingerprints": r["input_fingerprints"],
                "output_path": r.get("output_path"),
                "code": r["code"],
                "python_version": r["python_version"],
                "success_count": r["success_count"],
                "avg_runtime_sec": r["avg_runtime_sec"],
            }
            safe_name = r["step_name"].replace("/", "_").replace("\\", "_")
            zf.writestr(f"recipes/{safe_name}.json", json.dumps(recipe_data, ensure_ascii=False, indent=2))

    buf.seek(0)
    from urllib.parse import quote
    safe_wf_name = wf["name"].replace(" ", "_").replace("/", "_")
    encoded_name = quote(safe_wf_name)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"workflow.zip\"; filename*=UTF-8''{encoded_name}.zip"},
    )


@app.post("/workflows/import")
async def api_import_workflow(file: UploadFile = File(...)):
    import io
    import zipfile
    from db import create_workflow, save_recipe

    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="無效的 ZIP 檔案")

    # 讀取 workflow.json
    if "workflow.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="ZIP 中找不到 workflow.json")

    wf_data = json.loads(zf.read("workflow.json"))

    # 自動避免重名：若已存在相同名稱則加 (1), (2)...
    from db import list_workflows
    existing_names = {w["name"] for w in list_workflows()}
    base_name = wf_data.get("name", "匯入的工作流")
    final_name = base_name
    counter = 1
    while final_name in existing_names:
        final_name = f"{base_name}({counter})"
        counter += 1

    wf = create_workflow(
        name=final_name,
        canvas=wf_data.get("canvas"),
        validate=wf_data.get("validate", False),
    )

    # 匯入 recipes
    recipe_count = 0
    for name in zf.namelist():
        if name.startswith("recipes/") and name.endswith(".json"):
            r = json.loads(zf.read(name))
            try:
                save_recipe(
                    workflow_id=wf["id"],
                    step_name=r["step_name"],
                    task_hash=r["task_hash"],
                    input_fingerprints=r.get("input_fingerprints", {}),
                    output_path=r.get("output_path"),
                    code=r.get("code", ""),
                    python_version=r.get("python_version", ""),
                    runtime_sec=r.get("avg_runtime_sec", 0),
                )
                recipe_count += 1
            except Exception:
                pass

    # 檢查是否有非 Skill 步驟（需要本地腳本）
    has_local_scripts = False
    nodes = wf_data.get("canvas", {}).get("nodes", [])
    for node in nodes:
        data = node.get("data", {})
        if not data.get("skillMode", False) and data.get("batch", "").strip():
            has_local_scripts = True
            break

    return {
        "workflow": wf,
        "recipe_count": recipe_count,
        "has_local_scripts": has_local_scripts,
    }


# ── Recipe Book ──────────────────────────────────────────────
@app.get("/recipes")
async def api_list_recipes(workflow_id: Optional[str] = None):
    from db import list_recipes
    return list_recipes(workflow_id)


@app.get("/recipes/status/{workflow_id}")
async def api_recipe_status(workflow_id: str, steps: str = ""):
    from db import get_recipe_status
    step_names = [s.strip() for s in steps.split(",") if s.strip()] if steps else []
    return get_recipe_status(workflow_id, step_names)


@app.delete("/recipes/{workflow_id}/{step_name}")
async def api_delete_recipe(workflow_id: str, step_name: str):
    from db import delete_recipe
    ok = delete_recipe(workflow_id, step_name)
    return {"deleted": ok}


@app.delete("/recipes/{workflow_id}")
async def api_delete_workflow_recipes(workflow_id: str):
    from db import delete_workflow_recipes
    count = delete_workflow_recipes(workflow_id)
    return {"deleted_count": count}


# ── File System Browser ──────────────────────────────────────
@app.get("/fs/browse")
async def fs_browse(path: str = ""):
    home = Path.home()
    target = Path(path).expanduser() if path else home
    try:
        target.resolve().relative_to(home.resolve())
    except ValueError:
        target = home
    if not target.exists() or not target.is_dir():
        target = home

    items = []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith('.'):
                continue
            items.append({"name": item.name, "path": str(item), "is_dir": item.is_dir(), "ext": item.suffix.lower() if item.is_file() else ""})
    except PermissionError:
        pass

    parent = str(target.parent) if target != home else None
    return {"path": str(target), "parent": parent, "items": items}


@app.get("/fs/check-venv")
async def fs_check_venv(dir: str):
    """檢測腳本目錄下是否有可用的 Python 虛擬環境。
    支援兩種常見命名：`venv/`（Windows 慣例）與 `.venv/`（Unix/macOS 慣例），
    回傳第一個找到的 python 可執行檔路徑，讓使用者不用管到底叫哪個名字。"""
    target = Path(dir).expanduser().resolve()
    try:
        target.relative_to(Path.home().resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="只允許在 home 目錄下操作")
    import os as _os
    is_win = _os.name == "nt"
    venv_subdir = "Scripts" if is_win else "bin"
    py_name = "python.exe" if is_win else "python"
    # 兩種慣例都檢查一次，誰先找到用誰（venv 先，因為 Windows 使用者比較常這樣命名）
    for venv_dir_name in ("venv", ".venv"):
        venv_python = target / venv_dir_name / venv_subdir / py_name
        if venv_python.exists():
            return {
                "has_venv": True,
                "python_path": str(venv_python),
                "venv_dir_name": venv_dir_name,
            }
    return {"has_venv": False, "python_path": None, "venv_dir_name": None}


# ── Log Analysis ──────────────────────────────────────────────
# 常見 module → pip 套件名稱對映（module 名與 pip 名不同的情況）
_MODULE_TO_PIP = {
    "cv2": "opencv-python", "PIL": "Pillow", "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn", "yaml": "pyyaml", "docx": "python-docx",
    "pptx": "python-pptx", "dotenv": "python-dotenv", "jwt": "pyjwt",
    "gi": "pygobject", "Crypto": "pycryptodome", "serial": "pyserial",
    "usb": "pyusb", "magic": "python-magic", "dateutil": "python-dateutil",
    "attr": "attrs", "lxml": "lxml", "wx": "wxPython",
}


@app.get("/pipeline/logs/analyze")
async def analyze_logs(count: int = 5):
    """掃描最近 N 筆 pipeline log，找出 ModuleNotFoundError / ImportError 並建議套件"""
    from pipeline.logger import LOG_DIR
    import re

    log_files = sorted(Path(LOG_DIR).glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:count]

    missing: dict[str, dict] = {}  # module_name → { pip, files }
    # 用 [^'\"\n] 阻止跨行貪婪匹配（避免 log 截斷造成的誤判：
    # 例如 `No module named 'p...\n...next-line-has-quote` 不該匹配出 `p`）
    pattern = re.compile(
        r"(?:ModuleNotFoundError:\s*No module named\s*['\"]([^'\"\n]+)['\"]"
        r"|ImportError:\s*cannot import name\s*['\"]?\w+['\"]?\s*from\s*['\"]([^'\"\n]+)['\"]"
        r"|ImportError:\s*No module named\s*['\"]([^'\"\n]+)['\"])"
    )

    analyzed_files = []
    for lf in log_files:
        text = lf.read_text(encoding="utf-8", errors="ignore")
        found_in_file = False
        for m in pattern.finditer(text):
            raw = m.group(1) or m.group(2) or m.group(3)
            top_module = raw.split(".")[0]
            # 過濾無效結果：太短、非 identifier、以 "..." 結尾（log 截斷殘跡）
            if (
                len(top_module) < 3
                or not top_module.isidentifier()
                or raw.endswith("...")
            ):
                continue
            pip_name = _MODULE_TO_PIP.get(top_module, top_module)
            if top_module not in missing:
                missing[top_module] = {"pip": pip_name, "files": []}
            if lf.name not in missing[top_module]["files"]:
                missing[top_module]["files"].append(lf.name)
            found_in_file = True
        analyzed_files.append({
            "name": lf.name,
            "size": lf.stat().st_size,
            "has_errors": found_in_file,
        })

    suggestions = [
        {"module": mod, "pip_name": info["pip"], "found_in": info["files"]}
        for mod, info in sorted(missing.items())
    ]

    return {"analyzed": len(log_files), "files": analyzed_files, "suggestions": suggestions}


# ── Pipeline Run ─────────────────────────────────────────────
class PipelineRunRequest(BaseModel):
    yaml_content: str
    validate: bool = True
    use_recipe: bool = False  # True = 快速模式：recipe 命中時跳過 LLM 驗證
    workflow_id: Optional[str] = None  # 關聯工作流 ID
    no_save_recipe: bool = False  # True = 延遲 recipe 儲存，等用戶確認


class PipelineDecisionRequest(BaseModel):
    decision: str  # retry | skip | abort | continue | retry_with_hint
    hint: Optional[str] = None  # 補充指示（retry_with_hint 時使用）


@app.post("/pipeline/run")
async def start_pipeline(req: PipelineRunRequest):
    import uuid, yaml
    from pipeline.models import PipelineConfig
    from pipeline.runner import run_pipeline
    from pipeline.store import PipelineRun as PRun, get_store
    from pipeline.logger import create_run_logger
    try:
        import logging as _logging
        _log = _logging.getLogger("pipeline")
        _log.debug(f"收到 YAML（{len(req.yaml_content)} 字元）:\n{req.yaml_content}")
        data = yaml.safe_load(req.yaml_content)
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = req.validate
        config = PipelineConfig(**config_dict)
        for i, s in enumerate(config.steps):
            _log.debug(f"步驟[{i}] batch（{len(s.batch)} 字元）：{s.batch[:300]}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失敗：{e}")

    # 先建立 run 並存入 store，確保前端立刻能查詢
    run_id = str(uuid.uuid4())[:12]
    _, log_path = create_run_logger(run_id, config.name)
    config_d = config.model_dump()
    config_d["_use_recipe"] = req.use_recipe  # 傳遞快速模式旗標
    config_d["_workflow_id"] = req.workflow_id  # 關聯工作流
    config_d["_no_save_recipe"] = req.no_save_recipe  # 延遲 recipe 儲存
    run = PRun(
        run_id=run_id,
        pipeline_name=config.name,
        config_dict=config_d,
        telegram_chat_id=0,
        log_path=log_path,
        workflow_id=req.workflow_id,
    )
    get_store().save(run)

    # 背景執行（runner 看到已存在的 run_id 會恢復執行）
    from pipeline.runner import register_task
    task = asyncio.create_task(run_pipeline(config_d, chat_id=0, run_id=run_id))
    register_task(run_id, task)

    return {"run_id": run_id, "message": f"Pipeline '{config.name}' 已啟動"}


@app.get("/pipeline/runs")
async def list_pipeline_runs():
    from pipeline.store import get_store
    runs = get_store().list_recent(20)
    return {"runs": [_run_to_dict(r) for r in runs]}


@app.get("/pipeline/runs/{run_id}")
async def get_pipeline_run(run_id: str):
    from pipeline.store import get_store
    run = get_store().load(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="找不到 pipeline run")
    return _run_to_dict(run)


@app.delete("/pipeline/runs/{run_id}")
async def delete_pipeline_run(run_id: str):
    from pipeline.store import get_store
    if get_store().delete(run_id):
        return {"message": f"Run {run_id} 已刪除"}
    raise HTTPException(status_code=404, detail="找不到該 run")


@app.post("/pipeline/runs/{run_id}/resume")
async def resume_pipeline_run(run_id: str, req: PipelineDecisionRequest):
    if req.decision not in ("retry", "skip", "abort", "continue", "retry_with_hint", "answer"):
        raise HTTPException(status_code=400, detail="decision 必須是 retry / skip / abort / continue / retry_with_hint / answer")
    from pipeline.runner import resume_pipeline
    msg = await resume_pipeline(run_id, req.decision, hint=req.hint or "")
    return {"message": msg}


@app.get("/pipeline/runs/{run_id}/ask-user")
async def get_pending_ask_user(run_id: str):
    """回傳 run 目前的 ask_user 問題（若無則 question 為空）。"""
    from pipeline.executor import get_pending_question
    q = get_pending_question(run_id)
    return {"pending": q is not None, "question": q}


@app.post("/pipeline/runs/{run_id}/abort")
async def abort_pipeline_run(run_id: str):
    """立即中止正在執行的 pipeline（kill 子進程 + cancel task）"""
    from pipeline.store import get_store
    from pipeline.runner import force_abort
    run = get_store().load(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="找不到 pipeline run")
    if run.status not in ("running", "awaiting_human"):
        raise HTTPException(status_code=400, detail=f"Pipeline 狀態為 {run.status}，無法中止")
    await force_abort(run_id)
    return {"message": "⛔ Pipeline 已立即中止"}


@app.post("/pipeline/runs/{run_id}/save-recipes")
async def save_pending_recipes(run_id: str):
    """用戶確認後，將延遲儲存的 recipes 寫入 DB"""
    from pipeline.store import get_store
    from db import save_recipe as _db_save_recipe
    store = get_store()
    run = store.load(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="找不到 pipeline run")
    if not run.pending_recipes:
        return {"saved": 0}
    saved = 0
    for r in run.pending_recipes:
        try:
            _db_save_recipe(
                r["pipeline_id"], r["step_name"], r["task_hash"],
                r["input_fingerprints"], r["output_path"], r["code"],
                r["python_version"], r["runtime_sec"],
            )
            saved += 1
        except Exception:
            pass
    run.pending_recipes = []
    store.save(run)
    return {"saved": saved}


@app.get("/pipeline/runs/{run_id}/log")
async def get_pipeline_log(run_id: str):
    from pipeline.store import get_store
    run = get_store().load(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="找不到 pipeline run")
    log_path = Path(run.log_path)
    if not log_path.exists():
        return {"log": "（尚無 log 檔案）"}
    content = log_path.read_text(encoding="utf-8")
    return {"log": content}


# ── Pipeline Schedule ────────────────────────────────────────
@app.get("/pipeline/scheduled")
async def list_pipeline_scheduled():
    from scheduler.manager import list_tasks
    tasks = list_tasks()
    return {"tasks": [t for t in tasks if t.get("output_format") == "pipeline"]}


@app.delete("/pipeline/scheduled/cancel-by-name/{name}")
async def cancel_pipeline_schedule(name: str):
    from scheduler.manager import remove_task_by_name
    success = remove_task_by_name(name)
    if not success:
        raise HTTPException(status_code=404, detail="找不到該名稱的排程任務")
    return {"status": "ok"}


class PipelineScheduleRequest(BaseModel):
    name: str
    yaml_content: str
    schedule_type: str = "cron"
    schedule_expr: str = "0 8 * * *"
    validate: bool = True
    use_recipe: bool = False
    workflow_id: Optional[str] = None


@app.post("/pipeline/scheduled")
async def create_pipeline_schedule(req: PipelineScheduleRequest):
    import yaml
    from pipeline.models import PipelineConfig
    from scheduler.manager import add_pipeline_task
    from dataclasses import asdict
    try:
        data = yaml.safe_load(req.yaml_content)
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = req.validate
        PipelineConfig(**{k: v for k, v in config_dict.items() if not k.startswith("_")})
        config_dict["_use_recipe"] = req.use_recipe
        if req.workflow_id:
            config_dict["_workflow_id"] = req.workflow_id
        yaml_to_save = yaml.dump({"pipeline": config_dict}, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YAML 格式錯誤：{e}")
    try:
        info = add_pipeline_task(name=req.name, schedule_type=req.schedule_type, schedule_expr=req.schedule_expr, yaml_content=yaml_to_save)
        return {"task": asdict(info)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/pipeline/scheduled/{task_id}")
async def delete_pipeline_schedule(task_id: str):
    from scheduler.manager import remove_task
    if remove_task(task_id):
        return {"message": f"排程 {task_id} 已刪除"}
    raise HTTPException(status_code=404, detail="找不到該排程")


# ── Pipeline YAML Chat Assistant ─────────────────────────────
_PIPELINE_SYSTEM_BASE = """你是 Pipeline 工作流設定助手。使用者用自然語言描述需求，你引導他釐清細節後產出可執行的 YAML。

# 對話流程（很重要 — 不要跳階段直接吐 YAML）

## 1. Discovery — 先判定要不要進（很重要！）

**收到需求第一件事：做兩維度檢查、不要直接問問題**：
- **維度 A 資料源**：使用者有給「URL / 檔案路徑 / 腳本 / Outlook 信件」嗎？
- **維度 B 動作**：使用者有說要做什麼（抓 / 摘要 / 統計 / 寄信 / 轉檔 / 驗證）嗎？

### 規則（嚴格遵守）

| 兩維度狀態 | 行為 |
|---|---|
| 兩個都有 | **直接跳 Plan**（不進 Discovery） |
| 缺一個 | 只反問**缺失的那個**（不要兩個都問） |
| 兩個都缺 | 反問「想做什麼？」一題就好 |

### ❌ 不要做的事（先前測試過度反問了）

**不要追問可推論預設的事**：
- ❌ 「結果輸出到哪？」 → 預設 `ai_output/<workflow>/...`，省略由系統推
- ❌ 「要不要人工確認？」 → 使用者沒提就不要主動加
- ❌ 「要存檔還是寄信？」 → 使用者沒提就只存檔（預設）
- ❌ 「結果格式要 markdown 還是 PDF？」 → 用合理推測（爬網站 → markdown）

**不要連環問「為了讓設定更精準」式的細節**。Plan 末尾可以加「還要調整 X 嗎？」一句、不要連續兩三題追問。

### ✅ 正例

| 使用者輸入 | 正確行為 |
|---|---|
| 「幫我做一個自動化」 | 兩維度都缺 → 問「要做什麼？」 |
| 「抓 https://hn.com 摘要前 10 條」 | 兩維度有 → 直接 Plan |
| 「下載 https://yt.com/x 影片做摘要」 | 兩維度有 → 直接 Plan，明說『影片爬蟲（wc_mode: video）』 |
| 「跑我的 stage1.py」 | 缺 B → 問「跑完要做什麼？」 |
| 「檢查 raw.xlsx 數字異常但不要動原檔」 | 兩維度有 → 直接 Plan，明說『skill 加 readonly: true』 |
| 「腳本產儀表板，跑完看畫面對不對」 | 兩維度有 → 直接 Plan，明說『視覺驗證節點』 |

## 2. Plan — 純文字提案（不貼 YAML）
資訊充足後，**先用條列式描述步驟**讓使用者點頭，例如：

> 我這樣安排：
> 1. **網頁爬蟲**：抓 https://www.pttweb.cc/bbs/Stock 列表頁
> 2. **AI 技能**：抽前 10 篇連結各自展開，摘要寫到 daily.md
> 3. **人工確認**：Telegram 上看摘要、OK 才續跑
> 4. **Outlook 寄信**：把 daily.md 當附件寄給 boss@x.com
>
> 這樣 OK 嗎？或哪一步要調整？

## 3. Confirm
使用者點頭 → 進 Emit。使用者推翻 → 回 Discovery 再問。

## 4. Emit — 才產 YAML
**必須**含 `YAML_READY` 標記。

### Emit 前完整性檢查清單（強制做、不要跳過）

產 YAML 前先逐項檢查、缺東西不要 emit、退回 Discovery 再問：

1. **使用者明確給的資訊（email、人名、檔案路徑、URL、數字、日期）必須字面寫進 YAML**，不可用 placeholder（不要 `boss@x.com`、要用使用者真的給的 `wilson_bai@asus.com`）
2. **每個節點的必要欄位都要齊全**（看下方表）：

| 節點 | 必要欄位 | 易漏項 |
|---|---|---|
| `outlook_automation` | `outlook_template` + `outlook_params`（含 to/subject/body 等該模板的必要 keys） | ❗ 最常漏：`outlook_params` 整段沒寫 |
| `web_crawler` (web) | `wc_url` 或 `wc_urls` | wc_url 留空 |
| `web_crawler` (video) | `wc_video_url` | video URL 漏填 |
| `human_confirm` | （無嚴格必要欄位、`message` 建議填） | 都可省略 |
| `skill` | `skill_mode: true` + `batch`（任務描述） | batch 寫太短 LLM 看不懂 |
| `script` | `batch`（指令） | — |
| `visual_validation` | `visual_validation: true` + `vv_prompt` | vv_prompt 必填 |

3. **特別針對 `outlook_automation`：使用者給的 email 必填到 `outlook_params.to`**。沒有 email 不要產 YAML、回去問。
4. **`outlook_params` 一律用 inline JSON 一行寫**（不要多行 YAML 格式 — 前端解析器只認 inline JSON）：
   - ✅ 正確：`outlook_params: {"to":"wilson@x.com","subject":"日報","body":"請查收"}`
   - ❌ 錯誤：`outlook_params:` 換行後 `  to: wilson@x.com` `  subject: ...`（多行格式會被前端解析器丟掉）
   - **⚠ 已知 round-trip bug**：用戶把含 `outlook_params:` 的 YAML 貼到 YAML 面板「套用」後，這欄位有時會被前端 round-trip 吃掉。**所以你最後在 Plan / Confirm / 回應結尾必須附帶提醒**：
     > 「貼上 YAML 套用後，請到畫布的 Outlook 節點 panel 點開、確認 to / subject / body 都填好（YAML round-trip 有時會吃掉這欄位）。」
5. **路徑判斷**：使用者沒指定 → 用相對（純檔名最簡，系統自動落到 workflow dir）。使用者明說特定位置（含絕對路徑、家目錄、磁碟代號）→ 照用

---

# 七種節點類型（節點全集）

## 1. 腳本節點（script）
**使用者說**：「我的 xxx.py 腳本」「執行 xxx 指令」「跑這個批次檔」
```yaml
- name: 抓資料
  batch: python ~/scripts/fetch.py --date=today
  timeout: 300
  retry: 2
```

## 2. AI 技能節點（skill）
**使用者說**：「幫我抓 / 摘要 / 處理 / 算」+ **沒現成腳本** → LLM 自動寫 Python
```yaml
- name: 摘要報告
  skill_mode: true
  batch: |
    讀 raw.md，摘成 10 條重點，輸出 daily.md
  timeout: 600
  output:
    path: daily.md
    description: "10 條中文重點，每條一行"
```

**進階設定**（依需要才加）：
- `skill: <name>` — 掛載已安裝的 Agent Skill（如 `skill: pptx` / `skill: docx`），把 SKILL.md 注入 prompt 提升正確率
- `readonly: true` — 只讀不寫，適合做深度資料驗證
- `ask_mode: true` — LLM 遇不確定時主動問使用者

## 3. 人工確認節點（human_confirm）
**使用者說**：「審核」「確認」「給我看一下再繼續」「需要我點頭」
```yaml
- name: 審核摘要
  human_confirm: true
  message: 請確認上一步產出的摘要是否正確
  notify_telegram: true        # 預設 true
  send_prev_output: true       # 把上一步的輸出檔自動傳到 TG（手機可下載）
  preview_prev_output: false   # true 時把檔案 render 成 PNG 一併傳
  screenshot: false            # true 時 TG 多一個「📸 截圖」按鈕
  timeout: 3600
```

## 4. 網頁爬蟲節點（web_crawler，wc_mode: web）
**使用者說**：貼 URL「抓這頁」「爬」「擷取」
```yaml
- name: 抓 Reddit 列表
  web_crawler: true
  wc_url: "https://www.reddit.com/r/ASUS/"
  timeout: 90
```

**多 URL** 用 `wc_urls: ["url1", "url2"]`，會輸出到資料夾、每 URL 一個檔。

**論壇 / 列表模式 `wc_with_children`**（重要、優先推薦給「列表 → 詳細頁 → 摘要」場景）：
使用者說「抓 PTT 股版前 10 篇做摘要」/「Reddit r/ASUS 討論摘要」/「Dcard 熱門帖內容分析」這類「**列表頁 → 子頁 → 處理**」結構時，**強烈建議開 `wc_with_children: true`**。
單一節點完成「抓列表 + 抓 N 個子頁 + 合併單一 markdown」、後面只要一個 skill 節點做摘要：
```yaml
- name: 抓 PTT 股版列表 + 前 10 篇內文
  web_crawler: true
  wc_url: "https://www.pttweb.cc/bbs/Stock"
  wc_with_children: true
  wc_max_children: 10              # 預設 10、可調
  # wc_child_link_pattern: ""      # 留空 = 自動辨識（涵蓋 Reddit/PTT/Dcard/HN/新聞 12 種）
  timeout: 600
```

**為什麼比「skill 節點讓 LLM 寫爬蟲程式」好**：
- 不會發生 LLM 偷懶 hardcode 答案在程式碼裡
- 並行抓子頁（5 並發）、速度快
- 自動跳過釘選 / 公告貼文
- 自動過濾跨站連結（列表頁雜訊如圖檔 CDN、Help 站、廣告外鏈不會混進子頁清單）
- 下游 skill 節點直接讀合併的 markdown 做摘要、不用碰 crawl4ai

**SPA / 反爬注意事項**（要主動提醒使用者）：
- Reddit / Twitter / X 等 SPA 站，內部已預設用 `domcontentloaded`（用 `networkidle` 會 timeout）
- 登入站要填 `wc_cookies`；Cloudflare 站系統會自動 fallback FlareSolverr

## 5. 影片爬蟲節點（web_crawler，wc_mode: video）
**使用者說**：貼 YouTube / Vimeo / Bilibili 連結「下載」「抓影片」
```yaml
- name: 下載影片
  web_crawler: true
  wc_mode: video
  wc_video_url: "https://www.youtube.com/watch?v=..."
  wc_video_quality: "720p"
  wc_video_max_duration_min: 30
  wc_video_subs: true
```

## 6. Outlook 自動化節點（outlook_automation）
**使用者說**：「寄信」「Outlook」「讀收件匣」「批次下載附件」「行事曆」
**強制 host 模式執行**（pywin32 + Outlook profile 在 sandbox 沒有）。

```yaml
- name: 寄報告
  outlook_automation: true
  outlook_template: send_with_attachment
  outlook_params: {"to":"boss@x.com","subject":"日報","body":"請查收"}
```

可用模板由系統動態列出（見下方注入區），優先選最貼近使用者意圖的模板。**沒有合適模板**就改成「`outlook_template:` 留空 + `batch:` 填自由需求」走 LLM 路徑。

## 7. 視覺驗證節點（visual_validation）
**使用者說**：「檢查產出畫面對不對」「驗證截圖」「看圖判斷」
```yaml
- name: 檢查 Excel 排版
  visual_validation: true
  vv_source: prev_output           # 上一步的輸出檔；另一個值 current_screen 是即時抓螢幕
  vv_prompt: 應該看到一張表頭加粗、欄寬對齊內容的 Excel
  timeout: 120
```

## ⚠️ 桌面自動化節點（computer_use）— 你不要寫 YAML
**使用者說**：「自動點按鈕」「UI 自動化」「錄製操作」「滑鼠點擊」
**你的回應**：
> 桌面自動化節點需要先在畫布拉一個 computer_use 節點，按錄製鈕錄下你要操作的動作（滑鼠/鍵盤/截圖比對），AI 助手沒辦法幫你寫 actions 序列。錄完後再來討論前後步驟。

actions 序列是錄製產生的，不是 LLM 該寫的。

---

# 共用欄位規則

- `name`：步驟名稱（中文 OK）
- `timeout`：秒數。script 300 / skill 600 / human_confirm 3600 / visual_validation 120 / web_crawler 600
- `retry`：失敗重試次數。各節點預設值（不寫就走預設）：
  - `script`: **1** — 程式碼出錯重跑也是同樣錯，但給一次寫入失敗 / 路徑問題的恢復機會
  - `skill`: **1** — LLM 看到失敗 reason 有機會改寫程式
  - `web_crawler`: **2** — 網路抖動類失敗多半暫時性、重抓很便宜（純 deterministic、零 LLM 費用）
  - `outlook_automation`: **1**
  - 想關掉重試 → `retry: 0`；網路類想多試 → `retry: 2`。**跟預設值一樣可以省略整個欄位**
- `working_dir`：可選，省略走預設
- `output.path`：**可省略**（省略時系統自動推為 `<step name>_result.md`）。寫的話用相對路徑（見下方路徑慣例）
- `output.description`：產出後 AI 驗證是否符合描述。**有填走深度驗證**（agent 自己跑工具查檔案內容）、**空白走淺驗證**（LLM 一次 call 看 stdout 表層）。寧缺勿濫 — 沒明確驗證需求就留空、不要硬寫

# 路徑慣例

判斷依據：**使用者有沒有明確指定路徑**？

## 沒指定路徑（最常見）→ 用相對

預設讓系統自動落到本工作流的輸出資料夾 `ai_output/<pipeline name>/`：
- ✅ `output.path: posts_list.md` → 自動變 `ai_output/<workflow>/posts_list.md`（**推薦、最簡潔**）
- ✅ `output.path: ai_output/daily_news/headlines.csv` → 視為「相對於專案根」（向後相容、auto-default 走這條）

**為什麼預設用相對**：portable（跨機器/改名工作流不會壞）、TG 傳檔 / snapshot diff 等系統機制都跟 workflow dir 對齊。

## 使用者指定了路徑 → 照用、含絕對路徑

當使用者明說「報告存到 D:\Reports\daily.xlsx」「寫到 ~/Documents/output.md」這類**特定位置**時，
**直接用使用者給的路徑、包含絕對路徑都 OK**。系統 backend 完全接受絕對路徑：
- ✅ `output.path: D:\Reports\daily.xlsx`（Windows）
- ✅ `output.path: ~/Documents/output.md`（家目錄展開）
- ✅ `output.path: /shared/reports/daily.csv`（POSIX 絕對）

**注意**：絕對路徑會綁定該機器、跨機器移植 YAML 時要手動改。所以使用者沒指定就**不要主動用絕對路徑**。

## 重要：legacy script 整合場景 → 一定用絕對路徑

使用者說「我有支舊的 `financial.py`，它寫死輸出到 `D:\Old\out.xlsx`，後面幫我做資料清洗」這種場景：

**第一步 script 節點的 `output.path` 必須跟 script 內部寫死的位置一致**（用絕對路徑）。
不這樣寫的話會踩兩個坑：
1. **Validator 找不到檔案 → 假失敗 → 整個 step 卡 awaiting_human**
2. **下一個 step 的 `prev_outputs` 會給錯路徑**，後處理 skill 讀不到上一步的東西

正確寫法：

```yaml
- name: 跑既有財務系統
  batch: python D:\LegacyProject\financial.py    # 內部寫死輸出到 D:\Old\out.xlsx
  output:
    path: D:\Old\out.xlsx                        # ← 跟 script 寫的一致

- name: 資料清洗
  skill_mode: true
  batch: |
    讀上一步產生的 Excel,清理後輸出 cleaned.xlsx
  output:
    path: cleaned.xlsx                           # ← 後續 skill 走 workflow dir 就好
```

**判斷規則**：使用者描述裡有「我的腳本 / 已有的程式 / legacy / 寫死 / 既有專案」+ 提到具體輸出位置 → 第一步用絕對路徑、後續 step 走相對。

後續步驟讀檔用同一個檔名 / 相對路徑就好。

# 常見組合模式

| 情境 | 節點組合 |
|---|---|
| 單純抓單頁摘要 | `web_crawler → skill(摘要)` |
| **論壇 / 討論區「列表→子頁→摘要」**（重要） | `web_crawler(wc_with_children=true) → skill(摘要)` ← 單節點抓列表+ N 篇子頁 |
| 加人工把關 | `web_crawler(with_children) → human_confirm → skill → human_confirm` |
| 抓 + 摘 + 寄 | `web_crawler(with_children) → skill → human_confirm → outlook_automation` |
| 已有腳本 + AI 後處理 | `script → skill` |
| 視覺驗證 | `skill → visual_validation` |
| YouTube 影片摘要 | `web_crawler(video) → skill(轉文字+摘要)` |

# 互動原則（記在心裡）

- **永遠用繁體中文**
- **不要用 LaTeX / MathJax 語法**（前端聊天 UI 沒裝 KaTeX，`$\\rightarrow$` 會字面顯示一坨醜字）
  - 箭頭 → 直接打 Unicode `→`、不要寫 `$\\rightarrow$` 或 `\\to`
  - 變數 N 直接打 `N`、不要寫 `$N$`
  - 數學運算用 `×` `÷` `≤` `≥`、不要 `\\times` `\\leq` 等
- **不要急著吐 YAML** — 先 Discovery → Plan → Confirm → Emit
- 一次只問 1-2 個最關鍵的問題
- 反問超過 3 輪還沒釐清 → 給草稿讓使用者改，比一直問好
- 增量需求（「再加一步人工確認」）→ 在現有 YAML 上修改，不打掉重練
- 提到 computer_use → 直接告訴他要錄製、不寫 YAML

# Discovery → Plan 的判定（很重要 — 先前過度反問）

**判定「資訊夠了」的標準（兩個維度滿足就直接 Plan）**：
1. **資料源**：URL / 檔案路徑 / 已寫好的腳本 / Outlook 信件
2. **動作**：抓取 / 摘要 / 統計 / 寄信 / 轉檔 / 驗證

兩個有了就**直接 Plan**，剩下細節走「Plan 末尾追問」、不要連環問。

**不要追問可推論預設的事**：
- 「輸出到哪」→ 預設 `ai_output/<workflow>/...`，不要問
- 「要不要存檔」→ 不問，直接寫；使用者要寄信會自己提
- 「要不要寄信 / 通知」→ **使用者沒提就不主動加**
- 「結果格式」→ 用站點推合理預設（爬網站 → markdown / Excel→ Excel）

例：「抓 X 摘要」→ 抓什麼有了 + 動作有了 → 直接 Plan：web_crawler + skill 摘要、輸出走預設 → 末尾問「還要調整嗎」就好。

# Plan 階段「明說節點與設定」

Plan 條列要主動點名你會用的節點類型 + 關鍵設定（讓使用者知道你選對方向）：

- **Outlook**：明說用哪個模板，例：「Outlook 用 `download_attachments` 模板」
- **skill 進階**：用到 `readonly: true` / `ask_mode: true` / 掛 `skill: <name>` 時都明說
- **視覺驗證**：直接說「**視覺驗證節點**」（不要只說「檢查畫面」）
- **影片爬蟲**：說「**影片爬蟲（wc_mode: video）**」（不要只說「下載影片」）
- **網頁爬蟲達貼文數**：說「網頁爬蟲，目標撈 N 篇」

# 排程處理（每天/每週/cron 表達）

YAML 本身不負責排程。使用者提到「每天早上 9 點 / 每週一 / cron 表達」時，**Plan 階段先正常規劃步驟**（不需要排程相關欄位），**Emit 階段在回覆末尾加一句**：
> 跑完 YAML 後，到前端的「排程器」頁面設 cron 即可（系統內建 APScheduler）。

# 完整對話範例（從 Discovery 到 Emit）

> **使用者**：幫我抓 PTT 股版做摘要
>
> **你**：好的，幾個問題：
> 1. 想抓多少篇？
> 2. 摘要結果要存檔還是寄信？
> 3. 中間需要人工確認嗎？
>
> **使用者**：抓 10 篇就好，存成 markdown，寄到 boss@x.com
>
> **你**：了解，我這樣安排：
> 1. **網頁爬蟲**：抓 https://www.pttweb.cc/bbs/Stock 列表頁
> 2. **AI 技能**：抽前 10 篇連結各自展開，摘要寫到 daily.md
> 3. **人工確認**：TG 看摘要、點頭才繼續
> 4. **Outlook 寄信**：把 daily.md 當附件寄給 boss@x.com
>
> 這樣 OK 嗎？
>
> **使用者**：OK
>
> **你**：好的，我已經整理好工作流：
>
> YAML_READY
> ```yaml
> name: ptt_stock_daily
> validate: true
> steps:
>   - name: 抓 PTT Stock 列表
>     web_crawler: true
>     wc_url: "https://www.pttweb.cc/bbs/Stock"
>     timeout: 90
>   - name: 摘要 10 篇
>     skill_mode: true
>     batch: |
>       讀上一步抓回的內容,幫每一篇寫 80 字內中文摘要,輸出 daily.md。
>     output:
>       path: daily.md
>       description: "10 篇中文摘要，每篇 80 字內"
>     timeout: 1200
>   - name: 人工確認
>     human_confirm: true
>     send_prev_output: true
>   - name: 寄報告
>     outlook_automation: true
>     outlook_template: send_with_attachment
>     outlook_params: {"to":"boss@x.com","subject":"PTT 股版日報","body":"請查收"}
> ```

# 寫 skill 節點 batch（任務描述）的最佳實踐 — 重要

skill 節點的 `batch` 欄位是給內層 LLM agent 看的任務描述。寫得好不好直接決定能不能跑出結果、跑多久、跑幾次。

## 鐵則：簡短直接 > 複雜詳盡

LLM agent 對「邊角案例清單」「禁止 X 禁止 Y 禁止 Z」這類**防禦性**提示**會反向 prime**：
- 你警告「別把『載入失敗』訊息當沒內容」→ LLM 看到任何小錯誤就以為任務失敗、放棄、把所有結果填「(無實質內容)」
- 你列十條雜訊範例 → LLM 注意力分散到擔心邊角、忘記主任務
- 你堆連環否定句 → 焦點削弱、LLM 寫程式拼命做防禦檢查、跑很慢還容易失敗

**簡單一句「找用戶寫的句子當摘要」反而觸發 LLM 自然的問題解決能力**，多半一次就過。

## 寫法對照

| ❌ 過度防禦（請避免） | ✅ 簡短直接（推薦） |
|---|---|
| 「讀檔。注意：開頭可能有圖片載入失敗訊息（如 `![媒體錯誤]`）、reddit 推 app 訊息、頁首選單，這些不是內文。真正內文在後面、形如『hello! ...』」 | 「讀檔，幫每篇寫 80 字摘要 + 情緒判斷」 |
| 「整理清單。禁止從上半段抽連結、禁止抽留言、禁止抽圖片網址、禁止寫超過 10 篇」 | 「上一步抓了 10 篇，幫我整理成標題+作者+網址清單」 |

## 例外：要寫具體結構提示的時機

**只有當「合併檔有特殊結構、LLM 不知道從哪起手」時才加結構提示，且只加最少一句**：
- ✅「全檔搜尋 `# 子頁` 關鍵字會找到 N 個位置，每個位置就是一篇貼文」（針對 with_children 合併檔）
- ❌ 不要加：「上半段是雜亂列表頁忽略、下半段第 N/M 區塊用 # 子頁 N/M: 標記、要從那一行抽標題不要抽其他地方」

## 你（AI 助手）替使用者寫 skill batch 時的原則

當你產 YAML 裡的 skill batch 內容時：
1. 用「我要 X」的**正向描述**、不要「不要 Y」連環
2. **不列雜訊邊角清單**、信任 LLM 處理一般情況的能力
3. 結構提示只在必要時加最少必要的一句
4. **字數越少越好** — 8 行內能講完就 8 行
5. 對 with_children 合併檔做摘要的場景，**頂多加「全檔搜尋 `# 子頁` 找到的位置就是要處理的貼文」這一句**就好

跑壞了再加最少必要的提示，比一開始就堆滿邊角條件好用很多。
"""


# Outlook 模板註冊表 — id → (label, description, 主要參數鍵)
# 這份是給 AI 助手認識「目前 outlook_automation 節點有哪些開箱即用的模板」用的，
# 跟前端 _outlookPanel.tsx 的清單同步維護（前端是 UI 顯示用，這裡是 LLM prompt 注入用）。
# 新增模板時兩邊都要加；只在前端加 → AI 助手不會推薦；只在這裡加 → UI 看不到。
_OUTLOOK_TEMPLATES_FOR_PROMPT = [
    ("daily_todo", "整理符合條件信件 → 待辦清單",
     "掃指定資料夾的信，按條件過濾，整理成 markdown / xlsx 待辦清單",
     "folder, subject, sender, since, until, unread_only, output_format"),
    ("search_summary", "指定關鍵字撈相關信件 → 摘要報告",
     "用 LLM 摘要符合條件的信件群、產出報告",
     "keywords, search_in, folder, since, until, detail_level, output_format"),
    ("unanswered", "未回覆超過 N 天的信",
     "找出收件匣中我還沒回過、且收件超過指定天數的信",
     "days, sender_filter"),
    ("send_mail", "寄信給指定收件人",
     "直接寄一封信",
     "to, cc, bcc, subject, body, body_format"),
    ("send_with_attachment", "把上一步輸出（或指定檔案）當附件寄出",
     "前一步整理產出 xlsx → 直接寄給主管；或指定檔案路徑寄任何檔案",
     "to, cc, subject, body, attachment_path（可省略，預設取上一步輸出）"),
    ("bulk_send", "從 csv/xlsx 收件清單群發",
     "收件清單一筆一封，主旨/本文可帶 {欄位名} 變數",
     "list_path, subject_template, body_template"),
    ("download_attachments", "批次下載符合條件信件的附件",
     "把搜到的信件附件全部存到資料夾，可自訂檔名規則",
     "folder, subject, sender, since, until, save_dir, name_pattern"),
    ("bulk_move", "批次搬信到指定資料夾",
     "搜出符合條件的信件、批次搬到目標資料夾",
     "source_folder, target_folder, subject, sender, since, until"),
    ("bulk_mark_read", "批次標已讀／未讀",
     "搜出符合條件的信件、批次設為已讀或未讀",
     "folder, subject, sender, since, until, mark_as"),
    ("bulk_set_flag", "批次設旗標 / 標完成 / 清除",
     "搜出符合條件的信件、批次加追蹤旗標、標完成或清除",
     "folder, subject, sender, since, until, flag_action"),
]


def _build_pipeline_system_prompt() -> str:
    """組裝 AI 助手 system prompt：底稿 + 動態注入已安裝的 Agent Skills + Outlook 模板清單。"""
    parts = [_PIPELINE_SYSTEM_BASE]
    # ── Agent Skills 清單 ──────────────────────────────────────────────
    try:
        from skill_scanner import list_available_skills
        skills = list_available_skills()
        if skills:
            lines = ["", "## 使用者已安裝的 Agent Skills（掛載時請用 display_name）：", ""]
            for s in skills:
                desc = (s.get("description") or "").strip()
                if len(desc) > 120:
                    desc = desc[:120] + "…"
                lines.append(f"- **{s['display_name']}**：{desc}")
            lines.append("")
            lines.append("使用者任務若與上述 skill 相關，**優先建議掛載對應 skill**（YAML 加 `skill: <display_name>`）。")
            parts.append("\n".join(lines))
    except Exception:
        pass
    # ── Outlook 模板清單 ──────────────────────────────────────────────
    # 對 outlook_automation 節點來說，挑對模板比讓 LLM 自由發揮穩很多。
    try:
        lines = ["", "## Outlook 自動化節點可用模板（outlook_template 欄位）：", ""]
        for tid, label, desc, params in _OUTLOOK_TEMPLATES_FOR_PROMPT:
            lines.append(f"- **`{tid}`** — {label}")
            lines.append(f"  - 用途：{desc}")
            lines.append(f"  - 主要參數：{params}")
        lines.append("")
        lines.append("使用者談到 Outlook 任務時，**先比對上面模板**。挑最貼近意圖的模板，"
                     "把使用者提供的資訊填到 `outlook_params` 裡。**沒有合適模板**才退而填空 `outlook_template:` "
                     "改用 `batch:` 自由描述需求走 LLM 路徑。")
        parts.append("\n".join(lines))
    except Exception:
        pass
    return "".join(parts)


class PipelineChatRequest(BaseModel):
    messages: list[dict]
    workflow_id: Optional[str] = None  # 若帶，會把該工作流當前 canvas/YAML 注入 system prompt，
                                       # 讓 AI 能理解「在現有工作流加步驟」的增量需求


# 送 LLM 前保留最近多少則訊息（避免對話太長 token 爆炸 / 花錢）
# 設 30 大致能容納「規劃 → 修改 → 再修改」幾輪；早期概念性討論遺忘可接受
_CHAT_HISTORY_CAP = 30


def _workflow_state_block(workflow_id: str) -> str:
    """把當前工作流的 canvas 步驟摘要 + YAML 全文拼成一段注入 system prompt。
    這段告訴 LLM「使用者現在看到的工作流長這樣」，支援增量修改需求
    （例：「再加一個人工確認節點」需要知道現有幾步、叫什麼）。
    找不到 workflow 就回空字串，fallback 到原本的「從零規劃」行為。
    """
    try:
        import db
        wf = db.get_workflow(workflow_id)
        if not wf:
            return ""
        canvas = wf.get("canvas") or {}
        nodes = canvas.get("nodes") or []
        lines = [
            "",
            "## 使用者目前正在編輯的工作流",
            f"名稱：{wf.get('name', '未命名')}（id={workflow_id}）",
            f"節點數：{len(nodes)}",
        ]
        if nodes:
            lines.append("目前節點摘要（依畫布順序）：")
            for i, n in enumerate(nodes[:20], start=1):
                ntype = n.get("type") or "?"
                data = n.get("data") or {}
                name = data.get("name") or data.get("label") or "(未命名)"
                lines.append(f"  {i}. [{ntype}] {name}")
            if len(nodes) > 20:
                lines.append(f"  ... 另有 {len(nodes) - 20} 個節點未列")
        yaml_text = (wf.get("yaml") or "").strip()
        if yaml_text:
            # 避免 YAML 過長塞爆 prompt；超過 3000 字就截斷（頭尾各留一半）
            if len(yaml_text) > 3000:
                yaml_text = yaml_text[:1500] + "\n# ...（中段省略）...\n" + yaml_text[-1500:]
            lines.append("")
            lines.append("完整 YAML：")
            lines.append("```yaml")
            lines.append(yaml_text)
            lines.append("```")
        lines.append("")
        lines.append("**若使用者要求是修改 / 增量調整**（如「再加一步」、「把第 2 步改成…」），"
                     "在既有基礎上改動後回覆完整新 YAML；不是打掉重練。")
        lines.append("**若使用者要求跟現有工作流無關**（另開新題目），照常從零規劃即可。")
        return "\n".join(lines)
    except Exception:
        return ""


@app.post("/pipeline/chat")
async def pipeline_chat(req: PipelineChatRequest):
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from llm_factory import build_llm
    import re

    llm = build_llm(temperature=0.3)
    system_prompt = _build_pipeline_system_prompt()
    if req.workflow_id:
        system_prompt += _workflow_state_block(req.workflow_id)
    lc_messages = [SystemMessage(content=system_prompt)]
    # 只取最近 _CHAT_HISTORY_CAP 則訊息送進 LLM，避免對話太長 token 爆炸
    # （訊息仍全部保存在 DB，只是不全部餵給模型）
    recent = req.messages[-_CHAT_HISTORY_CAP:] if len(req.messages) > _CHAT_HISTORY_CAP else req.messages
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))

    response = llm.invoke(lc_messages)
    raw = response.content
    # Gemini/Gemma 可能回傳 list of content blocks（含 thinking + text）→ 抽出 text
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        content = "".join(parts)
    else:
        content = str(raw) if raw is not None else ""
    has_yaml = "YAML_READY" in content
    yaml_content = None
    yaml_error = None
    if has_yaml:
        match = re.search(r"```yaml\n([\s\S]+?)```", content)
        if match:
            yaml_content = match.group(1).strip()
            # ── 語法驗證：試跑 PipelineConfig.from_dict 檢查 schema ──
            try:
                import yaml as _yaml
                from pipeline.models import PipelineConfig
                parsed = _yaml.safe_load(yaml_content) or {}
                raw_cfg = parsed.get("pipeline", parsed)
                PipelineConfig.from_dict({k: v for k, v in raw_cfg.items() if not str(k).startswith("_")})
            except Exception as e:
                yaml_error = f"YAML 語法/結構錯誤：{type(e).__name__}：{str(e)[:300]}"

    return {"reply": content, "has_yaml": has_yaml, "yaml_content": yaml_content, "yaml_error": yaml_error}


# ── Workflow Chat History（per-workflow AI 助手對話紀錄）─────────────────────
# 用途：每個工作流保留自己的對話歷史，使用者回來還能接續跟 AI 討論加功能
# 儲存在 workflows.chat_messages TEXT 欄位（JSON 陣列），更新不動 updated_at
# （聊天不代表工作流本體有變動，不想擾亂工作流列表的排序）

class ChatMessageIn(BaseModel):
    role: str   # 'user' 或 'assistant'
    content: str


class ChatBulkSetRequest(BaseModel):
    messages: list[ChatMessageIn]


@app.get("/workflows/{workflow_id}/chat")
async def get_workflow_chat_api(workflow_id: str):
    """載入指定工作流的對話歷史。"""
    import db
    msgs = db.get_workflow_chat(workflow_id)
    if msgs is None:
        raise HTTPException(status_code=404, detail=f"找不到工作流：{workflow_id}")
    return {"messages": msgs}


@app.post("/workflows/{workflow_id}/chat")
async def append_workflow_chat_api(workflow_id: str, msg: ChatMessageIn):
    """追加一則訊息（user 或 assistant）。回傳更新後的完整訊息陣列。"""
    import db
    if msg.role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="role 必須是 'user' 或 'assistant'")
    result = db.append_workflow_chat(workflow_id, msg.role, msg.content)
    if result is None:
        raise HTTPException(status_code=404, detail=f"找不到工作流：{workflow_id}")
    return {"messages": result}


@app.put("/workflows/{workflow_id}/chat")
async def set_workflow_chat_api(workflow_id: str, req: ChatBulkSetRequest):
    """一次性整批覆寫訊息（用於 scratch chat 遷移到新建立的工作流）。"""
    import db
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    ok = db.set_workflow_chat(workflow_id, msgs)
    if not ok:
        raise HTTPException(status_code=404, detail=f"找不到工作流：{workflow_id}")
    return {"messages": db.get_workflow_chat(workflow_id)}


@app.delete("/workflows/{workflow_id}/chat")
async def clear_workflow_chat_api(workflow_id: str):
    """清空對話歷史（使用者按「🗑️ 新話題」）。"""
    import db
    ok = db.clear_workflow_chat(workflow_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"找不到工作流：{workflow_id}")
    return {"messages": []}


# ── Helpers ──────────────────────────────────────────────────
def _run_to_dict(r):
    return {
        "run_id": r.run_id,
        "pipeline_name": r.pipeline_name,
        "status": r.status,
        "current_step": r.current_step,
        "total_steps": len(r.config_dict.get("steps", [])),
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "step_results": [
            {"step_index": s.step_index, "step_name": s.step_name, "exit_code": s.exit_code,
             "validation_status": s.validation_status, "validation_reason": s.validation_reason,
             "validation_suggestion": s.validation_suggestion, "retries_used": s.retries_used,
             "stdout_tail": s.stdout_tail, "stderr_tail": s.stderr_tail}
            for s in r.step_results
        ],
        "config_dict": r.config_dict,
        "log_path": r.log_path,
        "pending_recipes": getattr(r, 'pending_recipes', []) or [],
        "awaiting_type": getattr(r, 'awaiting_type', '') or '',
        "awaiting_message": getattr(r, 'awaiting_message', '') or '',
        "awaiting_suggestion": getattr(r, 'awaiting_suggestion', '') or '',
    }


# ── Outlook 連線測試 ──────────────────────────────────────────────────────────


@app.get("/outlook/test-connection")
async def test_outlook_connection():
    """測試 Classic Outlook COM 是否可用 + 預設資料夾的信件數。

    回傳：
        ok               是否能連 COM
        version          Outlook.Application.Version（成功才有）
        inbox_count      收件匣 Items.Count（0 通常代表 profile 沒設好或用了新版 Outlook）
        sent_count       寄件備份信件數
        drafts_count     草稿信件數
        diagnosis        中文診斷結論（給 UI 直接顯示）
        error            COM 失敗時的原始錯誤訊息（成功時為空）
    """
    import platform
    if platform.system() != "Windows":
        return {
            "ok": False,
            "diagnosis": "後端不在 Windows 上 — Outlook COM 只能在 Windows host 跑",
            "error": "non-Windows platform",
        }

    try:
        import win32com.client  # type: ignore[import-not-found]
        import pythoncom  # type: ignore[import-not-found]
    except Exception as e:
        return {
            "ok": False,
            "diagnosis": "pywin32 未安裝 — 後端需要 pywin32 才能呼叫 Outlook COM",
            "error": f"{e.__class__.__name__}: {e}",
        }

    try:
        pythoncom.CoInitialize()
        app = win32com.client.Dispatch("Outlook.Application")
        version = str(app.Version)
        ns = app.GetNamespace("MAPI")
        inbox_count = int(ns.GetDefaultFolder(6).Items.Count)
        sent_count = int(ns.GetDefaultFolder(5).Items.Count)
        drafts_count = int(ns.GetDefaultFolder(16).Items.Count)
    except Exception as e:
        return {
            "ok": False,
            "diagnosis": ("無法連線到 Classic Outlook COM。最常見原因：你日常用的是「新版 Outlook for Windows」"
                          "（不支援 COM）。切換方式：在新版 Outlook 點上方「說明」分頁，"
                          "右邊最後一個按鈕「前往傳統 Outlook」即可切回。"),
            "error": f"{e.__class__.__name__}: {e}",
        }

    # 連得上但 inbox 是 0 → 大機率是 profile 設定錯（用新版 Outlook 但 Classic 帳號沒設）
    if inbox_count == 0:
        diagnosis = (f"COM 連線成功（Outlook {version}），但收件匣是 0 封。"
                     f"通常代表：你用的是新版 Outlook 而 Classic Outlook 的 profile 是空的。"
                     f"最快解法：在新版 Outlook 點「說明」分頁 → 右邊最後一個按鈕「前往傳統 Outlook」"
                     f"切回傳統版（保留同帳號），再回來重測。")
    else:
        diagnosis = (f"✓ Classic Outlook 連線正常（版本 {version}）。"
                     f"收件匣有 {inbox_count} 封信、寄件備份 {sent_count} 封、草稿 {drafts_count} 封。"
                     f"Outlook 自動化節點可以正常使用。")

    return {
        "ok": True,
        "version": version,
        "inbox_count": inbox_count,
        "sent_count": sent_count,
        "drafts_count": drafts_count,
        "diagnosis": diagnosis,
        "error": "",
    }
