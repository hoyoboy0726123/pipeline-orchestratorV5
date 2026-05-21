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

# ── DPI awareness（Windows）─────────────────────────────────────
# 必須在 import mss / pyautogui / ctypes 任何螢幕相關模組之前呼叫,
# 不然子模組 cache 住 DPI-unaware 的螢幕 metric、改不過來。
#
# 為什麼要做：DPI-unaware process 在高 DPI 螢幕上 Windows 會「撒謊」、
# 回邏輯(虛擬)像素 — 不同 scaling 機台之間邏輯像素不一致、跨機器搬
# workflow 會整個錯位到左上(C: 150% 邏輯 1280×720、D: 125% 邏輯
# 1536×864、同一組 (x,y) 在兩台對應的物理位置完全不同)。
#
# 設成 PROCESS_PER_MONITOR_DPI_AWARE_V2 = -4 (新 API,Win10 1703+)
# fallback：PROCESS_PER_MONITOR_DPI_AWARE = 2 (Win 8.1+)
# fallback：SetProcessDPIAware (Win Vista+)
#
# 設定後：mss.grab、pyautogui.click、GetCursorPos 全部用物理座標、
# 跨機器只要物理螢幕解析度一致就能相容。
#
# ⚠ 副作用：舊的(本修復前錄製的)workflow 座標是邏輯像素、修完
# 在同台機器上也會錯位、必須重錄一次。
if _sys.platform == "win32":
    try:
        import ctypes as _ctypes

        def _try_set_dpi_awareness() -> bool:
            """嘗試三層 fallback、回傳是否真的設成功(用 GetProcessDpiAwareness 反查)。"""
            user32 = _ctypes.windll.user32
            # 1. SetProcessDpiAwarenessContext (Win10 1703+) — 接 HANDLE(指標) 不是 int
            #    所以一定要走 c_void_p、不然 ctypes 預設 c_int 傳整數會 fail silently
            if hasattr(user32, "SetProcessDpiAwarenessContext"):
                user32.SetProcessDpiAwarenessContext.argtypes = [_ctypes.c_void_p]
                user32.SetProcessDpiAwarenessContext.restype = _ctypes.c_int
                # -4 = PER_MONITOR_AWARE_V2
                if user32.SetProcessDpiAwarenessContext(_ctypes.c_void_p(-4)):
                    return True
                # -3 = PER_MONITOR_AWARE(舊版 v1)— v2 不支援時退一步
                if user32.SetProcessDpiAwarenessContext(_ctypes.c_void_p(-3)):
                    return True
            # 2. SetProcessDpiAwareness (Win8.1+)
            try:
                shcore = _ctypes.windll.shcore
                if hasattr(shcore, "SetProcessDpiAwareness"):
                    # 2 = PROCESS_PER_MONITOR_DPI_AWARE。回傳 HRESULT,0 = S_OK
                    if shcore.SetProcessDpiAwareness(2) == 0:
                        return True
            except Exception:
                pass
            # 3. SetProcessDPIAware (Win Vista+,system-wide aware,粗糙但聊勝於無)
            try:
                if user32.SetProcessDPIAware():
                    return True
            except Exception:
                pass
            return False

        _try_set_dpi_awareness()
    except Exception:
        # 設不到不致命、只是回退到原本 DPI-unaware 行為
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
    # 全新安裝時 seed 預設範例工作流(已 seed 過會自動略過)
    from seed_examples import seed_example_workflows
    seed_example_workflows()
    # 自動安裝 skill_packages.txt 中缺少的套件
    from skill_pkg_manager import auto_install_packages
    auto_install_packages()
    # 主機系統工具健康檢查(non-fatal、只警告)
    from config import check_host_tools
    for w in check_host_tools():
        print(w)
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


@app.get("/system/host-tools")
async def host_tools_status():
    """主機系統工具(非 pip)健康檢查 — 給前端設定頁顯示。

    回每個工具的 name / installed / found_at / install_cmd / why / required。
    """
    from dataclasses import asdict
    import platform
    from pipeline.host_tools import get_host_tools
    return {
        "platform": platform.system(),  # "Windows" / "Darwin" / "Linux"
        "tools": [asdict(t) for t in get_host_tools()],
    }


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
    anthropic_thinking: Optional[str] = None  # "off" | "on" — Claude Opus 4 extended thinking
    # ── 副模型(選填、空字串 = 不啟用、所有節點都走 primary)──
    secondary_provider: Optional[str] = None
    secondary_model: Optional[str] = None
    secondary_ollama_thinking: Optional[str] = None
    secondary_ollama_num_ctx: Optional[int] = None
    secondary_gemini_thinking: Optional[str] = None
    secondary_anthropic_thinking: Optional[str] = None


@app.put("/settings/model")
async def put_model_settings(req: ModelSettingsRequest):
    from settings import update_settings
    try:
        return update_settings(
            req.provider, req.model, req.ollama_base_url, req.ollama_thinking, req.ollama_num_ctx,
            req.gemini_thinking, req.anthropic_thinking,
            secondary_provider=req.secondary_provider,
            secondary_model=req.secondary_model,
            secondary_ollama_thinking=req.secondary_ollama_thinking,
            secondary_ollama_num_ctx=req.secondary_ollama_num_ctx,
            secondary_gemini_thinking=req.secondary_gemini_thinking,
            secondary_anthropic_thinking=req.secondary_anthropic_thinking,
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
    from config import GROQ_API_KEY as _groq_key, GEMINI_API_KEY as _gemini_key, OPENAI_API_KEY as _oai_key, ANTHROPIC_API_KEY as _ant_key

    # 命中快取直接回，~5ms
    if not refresh and _MODELS_CACHE["data"] and (_time.time() - _MODELS_CACHE["ts"]) < _MODELS_CACHE_TTL:
        return _MODELS_CACHE["data"]

    ollama_models: list[dict] = []
    ollama_error: Optional[str] = None
    groq_models: list[dict] = []
    groq_error: Optional[str] = None
    gemini_models: list[dict] = []
    gemini_error: Optional[str] = None
    openai_models: list[dict] = []
    openai_error: Optional[str] = None
    anthropic_models: list[dict] = []
    anthropic_error: Optional[str] = None

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

        async def fetch_openai() -> tuple[list[dict], Optional[str]]:
            if not _oai_key:
                return [], "未設定 OPENAI_API_KEY"
            try:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {_oai_key}"},
                )
                r.raise_for_status()
                # OpenAI list 含很多 embedding / audio / image / fine-tune 模型、過濾出 chat 用的
                _CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
                _EXCLUDE = ("audio", "transcribe", "tts", "embed", "moderation", "realtime",
                            "instruct", "vision-preview", "search-preview")
                models = []
                for m in r.json().get("data", []):
                    mid = m.get("id", "")
                    if not any(mid.startswith(p) for p in _CHAT_PREFIXES):
                        continue
                    if any(kw in mid for kw in _EXCLUDE):
                        continue
                    models.append({"id": mid, "label": mid})
                models.sort(key=lambda x: x["id"])
                return models, None
            except Exception as e:
                return [], f"OpenAI API 錯誤：{e}"

        async def fetch_anthropic() -> tuple[list[dict], Optional[str]]:
            if not _ant_key:
                return [], "未設定 ANTHROPIC_API_KEY"
            try:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": _ant_key, "anthropic-version": "2023-06-01"},
                )
                r.raise_for_status()
                models = []
                for m in r.json().get("data", []):
                    mid = m.get("id", "")
                    display = m.get("display_name") or mid
                    label = f"{display}（{mid}）" if display != mid else mid
                    models.append({"id": mid, "label": label})
                models.sort(key=lambda x: x["id"])
                return models, None
            except Exception as e:
                return [], f"Anthropic API 錯誤：{e}"

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

        # 五條 coroutine 一口氣併發執行，總時間 ≈ max 而不是 sum
        (groq_models, groq_error), (gemini_models, gemini_error), \
        (openai_models, openai_error), (anthropic_models, anthropic_error), \
        (ollama_models, ollama_error) = \
            await _asyncio.gather(
                fetch_groq(), fetch_gemini(),
                fetch_openai(), fetch_anthropic(),
                fetch_ollama(),
            )

    payload = {
        "groq": groq_models,
        "groq_error": groq_error,
        "gemini": gemini_models,
        "gemini_error": gemini_error,
        "openai": openai_models,
        "openai_error": openai_error,
        "anthropic": anthropic_models,
        "anthropic_error": anthropic_error,
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


@app.post("/computer-use/recording/arm-hotkey")
async def arm_recording_hotkey(req: RecordingStartRequest):
    """註冊全域熱鍵(預設 F7)按下後自動 start_recording。
    用途:讓使用者最小化瀏覽器、把焦點留在要錄製的 app、用熱鍵啟動錄製。
    """
    from pipeline.recorder import arm_start_hotkey
    from pathlib import Path as _P
    out_path = _P(req.output_dir).expanduser()
    if not out_path.is_absolute():
        _PROJ = _P(__file__).parent.parent.absolute()
        out_path = _PROJ / out_path
    try:
        return arm_start_hotkey(session_id=req.session_id, output_dir=str(out_path), key="f7")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/computer-use/recording/disarm-hotkey")
async def disarm_recording_hotkey():
    """取消已註冊的全域熱鍵(panel 關閉或開始錄製時呼叫)。"""
    from pipeline.recorder import disarm_start_hotkey
    return disarm_start_hotkey()


class DuplicateAssetsRequest(BaseModel):
    src: str   # 原始 assetsDir(相對 or 絕對)
    dest: str  # 新 assetsDir


@app.post("/canvas/duplicate-assets")
async def duplicate_canvas_assets(req: DuplicateAssetsRequest):
    """節點複製貼上時、把 computer_use 的 assets 資料夾整份複製到新路徑。
    防止兩節點共用同一個資料夾(會互覆寫)。"""
    import shutil
    from pathlib import Path as _P
    _PROJ = _P(__file__).parent.parent.absolute()

    def _resolve(p: str) -> _P:
        pp = _P(p).expanduser()
        return pp if pp.is_absolute() else (_PROJ / pp)

    src = _resolve(req.src)
    dest = _resolve(req.dest)
    if not src.exists() or not src.is_dir():
        return {"ok": False, "error": f"原始資料夾不存在:{src}", "copied_files": 0}
    if dest.exists():
        return {"ok": False, "error": f"目標已存在(避免覆寫):{dest}", "copied_files": 0}
    try:
        shutil.copytree(src, dest)
        # 算 copied file 數
        n = sum(1 for _ in dest.rglob("*") if _.is_file())
        return {"ok": True, "src": str(src), "dest": str(dest), "copied_files": n}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "copied_files": 0}


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


class SavePngRequest(BaseModel):
    """前端裁切完直接送 base64 PNG 存到 assets_dir、給 VLM 錨點立即截圖用。
    跟 crop_anchor_from_full 不同:那個要先有 full_image 在磁碟、這個直接收 base64。"""
    dir: str                 # assets 資料夾
    name: str                # 檔名(可不含 .png、會自動補)
    png_b64: str             # 純 PNG base64(不含 data: prefix)


@app.post("/computer-use/assets/save-png")
async def save_png_to_assets(req: SavePngRequest):
    """把前端 canvas.toBlob() 出來的 PNG bytes 存進 assets_dir。
    用途:VLM 錨點立即截圖功能 — 使用者按下截圖、瀏覽器內裁切、再回傳裁好的圖。
    跟 crop_anchor_from_full 互補(那個吃磁碟上的 full_image、這個吃 base64)。
    """
    import base64
    target_dir = _validate_assets_path(req.dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    save_name = req.name.strip()
    if not save_name:
        raise HTTPException(status_code=400, detail="name 為空")
    # 過濾路徑符號(防 .. / 等跳出資料夾)
    if "/" in save_name or "\\" in save_name or save_name.startswith("."):
        raise HTTPException(status_code=400, detail=f"name 含非法字元:{save_name!r}")
    if not save_name.lower().endswith(".png"):
        save_name += ".png"

    try:
        # 容忍前端有沒帶 data:image/png;base64, prefix
        b64 = req.png_b64.split(",", 1)[-1] if "," in req.png_b64 else req.png_b64
        data = base64.b64decode(b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"base64 解碼失敗:{e}")

    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(status_code=400, detail="不是有效 PNG bytes")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"PNG 太大({len(data)} bytes、上限 5MB)")

    out_path = target_dir / save_name
    try:
        out_path.write_bytes(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"寫檔失敗:{e}")

    # 跟 crop 同步回 metadata、讓前端 UI 顯示一致
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            variance = float(np.var(gray))
        else:
            h, w, variance = 0, 0, 0.0
    except Exception:
        h, w, variance = 0, 0, 0.0

    return {
        "image": save_name,
        "width": w,
        "height": h,
        "variance": round(variance, 1),
        "size_bytes": len(data),
    }


class UiaInspectRequest(BaseModel):
    """檢視指定視窗(或 foreground)的 UIA element tree。"""
    window: str = ""             # 視窗 title pattern(支援 wildcard *)、空字串 = 當前 foreground
    max_depth: int = 6           # tree 深度上限(避免某些 app 上千層)
    max_children_per_node: int = 50  # 每節點子元素上限(避免大表格 1 萬列展開)


@app.post("/computer-use/uia/inspect")
async def uia_inspect(req: UiaInspectRequest):
    """檢視 UIA element tree、給 frontend tree picker 用。
    詳見 docs/uia-feature-evaluation.md。
    """
    from pipeline.uia_executor import inspect_window
    import logging as _log
    result = inspect_window(
        window_pattern=req.window,
        max_depth=req.max_depth,
        max_children_per_node=req.max_children_per_node,
        logger=_log.getLogger("uia_inspect"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "inspect 失敗"))
    return result


class UiaHighlightRequest(BaseModel):
    """在桌面對應位置畫紅框 outline、給 inspector hover 看清楚對應實體 control 用。
    清除用 ttl_ms=0 或呼 /computer-use/uia/highlight/clear。"""
    x: int                       # 螢幕絕對 X(虛擬桌面座標、可負)
    y: int                       # 螢幕絕對 Y
    width: int                   # 邊框寬
    height: int                  # 邊框高
    ttl_ms: int = 1500           # 自動消失時間;0 = 立即清掉


@app.post("/computer-use/uia/highlight")
async def uia_highlight(req: UiaHighlightRequest):
    """在桌面 (x, y, w, h) 位置畫紅色 outline。
    透明 topmost、click 穿透不擋滑鼠。"""
    from pipeline.cu_highlight_overlay import highlight, clear_highlight
    if req.ttl_ms <= 0:
        clear_highlight()
    else:
        highlight(req.x, req.y, req.width, req.height, req.ttl_ms)
    return {"ok": True}


@app.post("/computer-use/uia/highlight/clear")
async def uia_highlight_clear():
    from pipeline.cu_highlight_overlay import clear_highlight
    clear_highlight()
    return {"ok": True}


@app.post("/computer-use/uia/picker/start")
async def uia_picker_start():
    """啟動 Live Picker:滑鼠 hover 桌面 → UIA 元素跟隨紅框 + F8 確認 / F9 取消。"""
    from pipeline.uia_picker import get_picker
    p = get_picker()
    started = p.start()
    return {"ok": True, "started": started, "running": p.is_running}


@app.get("/computer-use/uia/picker/poll")
async def uia_picker_poll():
    """frontend 輪詢:當下 hover element + 是否 confirmed。"""
    from pipeline.uia_picker import get_picker
    return get_picker().poll()


@app.post("/computer-use/uia/picker/consume")
async def uia_picker_consume():
    """拿完 confirmed 後 reset、避免重複處理。"""
    from pipeline.uia_picker import get_picker
    el = get_picker().consume_confirmed()
    return {"ok": True, "element": el}


@app.post("/computer-use/uia/picker/stop")
async def uia_picker_stop():
    from pipeline.uia_picker import get_picker
    was_running = get_picker().stop()
    return {"ok": True, "was_running": was_running}


@app.post("/computer-use/uia/picker/confirm")
async def uia_picker_confirm():
    """frontend 按鈕「確認當前 hover」走這個、不靠 F8 hotkey。"""
    from pipeline.uia_picker import get_picker
    p = get_picker()
    el = p.confirm_current()
    if not el:
        return {"ok": False, "error": "目前沒 hover 任何元素、移動滑鼠到目標再確認"}
    return {"ok": True, "element": el}


@app.get("/computer-use/uia/windows")
async def uia_list_windows():
    """列當下所有可見的 top-level 視窗、給 frontend 「📋 列出視窗」選單用。

    用 uiautomation 為主(File Explorer / TeamsWebView 等 shell-hosted window
    EnumWindows 抓不到)、win32 EnumWindows 為輔(catch cloaked / 邊角 cases)。
    去重 by name+class、合併兩路結果。
    """
    try:
        import uiautomation as auto
    except ImportError:
        raise HTTPException(status_code=500, detail="uiautomation 未安裝")

    seen: set[tuple[str, str]] = set()
    windows: list[dict] = []

    # Pass 1: uiautomation(看 shell-hosted / 看標準 GUI app)
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                name = str(w.Name or "").strip()
                cls = str(getattr(w, "ClassName", "") or "")
                rect = w.BoundingRectangle
                rw = int(rect.right - rect.left)
                rh = int(rect.bottom - rect.top)
                if not name and (rw == 0 or rh == 0):
                    continue
                if not name:
                    name = f"(無標題 {cls})"
                key = (name, cls)
                if key in seen:
                    continue
                seen.add(key)
                windows.append({
                    "name": name,
                    "class": cls,
                    "rect": [int(rect.left), int(rect.top), rw, rh],
                    "is_offscreen": bool(getattr(w, "IsOffscreen", False)),
                })
            except Exception:
                continue
    except Exception:
        pass

    # Pass 2: win32 EnumWindows(補 cloaked / hidden ApplicationFrameWindow)
    try:
        import win32gui  # type: ignore
        import ctypes

        def _enum_cb(hwnd, _ignored):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                cls = win32gui.GetClassName(hwnd) or ""
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w < 50 or h < 50:
                    return True
                # 系統殼有名字才補(避免一堆 noise)
                if not title:
                    return True
                key = (title, cls)
                if key in seen:
                    return True
                seen.add(key)
                # 偵測 cloaked
                is_cloaked = False
                try:
                    DWMWA_CLOAKED = 14
                    cloaked = ctypes.c_int(0)
                    res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                    )
                    is_cloaked = (res == 0 and cloaked.value != 0)
                except Exception:
                    pass
                windows.append({
                    "name": title, "class": cls,
                    "rect": [rect[0], rect[1], w, h],
                    "is_offscreen": is_cloaked,
                })
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_cb, None)
    except Exception:
        pass

    # 排序:非 cloaked + 有真正 title 優先
    windows.sort(key=lambda x: (
        x["is_offscreen"],
        x["name"].startswith("(無標題"),
        -len(x["name"]),
    ))
    return {"ok": True, "windows": windows[:80]}


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
    from skill_scanner import list_available_skills, get_skills_root
    _root = get_skills_root()
    return {
        "skills_root": str(_root),
        "exists": _root.exists(),
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


# ── Skill 檔案目錄設定 ───────────────────────────────────────
class SkillsDirRequest(BaseModel):
    skills_dir: str  # 自訂 Skill 目錄絕對路徑;空字串 = 用預設 ~/.agents/skills/


@app.get("/settings/skills-dir")
async def get_skills_dir():
    """回傳目前 Skill 目錄設定 + 實際解析到的路徑 + 是否存在 + skill 數。"""
    import os
    from settings import get_settings
    from skill_scanner import get_skills_root, list_available_skills, _DEFAULT_SKILLS_ROOT
    configured = (get_settings().get("skills_dir") or "").strip()
    resolved = get_skills_root()
    return {
        "skills_dir": configured,
        "resolved": str(resolved),
        "default": str(_DEFAULT_SKILLS_ROOT),
        "exists": resolved.exists(),
        "skill_count": len(list_available_skills()),
        "env_override": bool((os.getenv("SKILLS_DIR") or "").strip()),
    }


@app.put("/settings/skills-dir")
async def put_skills_dir(req: SkillsDirRequest):
    """設定自訂 Skill 目錄。空字串 = 還原預設。指定路徑必須是已存在的資料夾。"""
    from pathlib import Path as _Path
    from settings import set_skills_dir
    from skill_scanner import get_skills_root, list_available_skills
    path = (req.skills_dir or "").strip()
    if path:
        p = _Path(path).expanduser()
        if not p.is_dir():
            raise HTTPException(status_code=400, detail=f"找不到資料夾:{p}")
    set_skills_dir(path)
    resolved = get_skills_root()
    return {
        "skills_dir": path,
        "resolved": str(resolved),
        "exists": resolved.exists(),
        "skill_count": len(list_available_skills()),
    }


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
    no_save_recipe: bool = False  # True = 延遲 recipe 儲存，等用戶確認（桌面手動勾「skill workflow」用）
    silent_recipe: bool = False  # True = 「無人值守」模式：直接覆寫 recipe、永不延遲、不彈確認
                                   # （TG 遠端遙控 / 排程觸發用、避免桌面卡在 pending dialog）
    # 啟動時傳入的參數，runner render 階段以 `{{ input.<key> }}` 引用。
    # workflow YAML 寫死的欄位仍照舊用,只有寫了 {{ input.X }} 的欄位才需要這裡帶值。
    input_params: dict = {}


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
    config_d["_silent_recipe"] = req.silent_recipe    # 無人值守模式（TG/排程）→ 直接覆寫
    run = PRun(
        run_id=run_id,
        pipeline_name=config.name,
        config_dict=config_d,
        telegram_chat_id=0,
        log_path=log_path,
        workflow_id=req.workflow_id,
        input_params=req.input_params or {},
    )
    get_store().save(run)

    # 背景執行（runner 看到已存在的 run_id 會恢復執行）
    from pipeline.runner import register_task
    task = asyncio.create_task(run_pipeline(config_d, chat_id=0, run_id=run_id))
    register_task(run_id, task)

    return {"run_id": run_id, "message": f"Pipeline '{config.name}' 已啟動"}


# ── 變數系統(Ticket 1):dry-run + list_workflow_variables ────────────────

class DryRunRequest(BaseModel):
    yaml_content: str
    input_params: dict = {}
    # 可選:給定後從這個 workflow 最近一次 run 撈 step output 真實值,
    # 讓「step2 引用 step1 output」能 render 出實際值預覽
    workflow_id: Optional[str] = None


@app.post("/pipeline/dry-run")
async def api_pipeline_dryrun(req: DryRunRequest):
    """不執行 workflow、純 render 每 step 的 {{ }} → 回傳渲染後的命令。

    用於前端「預覽渲染」按鈕;讓使用者看清楚變數展開後實際會跑什麼。
    """
    import yaml as _yaml
    from pipeline.models import PipelineConfig
    from pipeline.expression import build_context, render, ExpressionError, find_referenced_vars
    from pipeline.store import get_store

    # 1) 解析 YAML
    try:
        data = _yaml.safe_load(req.yaml_content)
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = config_dict.get("validate", True)
        config = PipelineConfig(**config_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失敗:{e}")

    # 2) 從最近一次 run 撈 step output(供「step2 引用 step1 output」做預覽)
    last_step_results = []
    if req.workflow_id:
        try:
            recent = get_store().list_recent(20)
            for r in recent:
                if r.workflow_id == req.workflow_id and r.step_results:
                    last_step_results = list(r.step_results)
                    break
        except Exception:
            pass

    # 3) 針對每個 step、render 主要欄位
    # 用 running_results 一邊 render 一邊累積:這樣 step2 可以引用 step1 在 YAML 寫的 output.path
    # (即使沒跑過、user 在 YAML 寫 path: foo_{{ input.date }}.csv 也能 render 給 step2 看)
    from pipeline.store import StepResult as _SR
    from pathlib import Path as _P
    running_results: list = list(last_step_results)
    # 把已知 step_name → StepResult 整理出來,新 render 的 step 要更新或追加
    _by_name = {sr.step_name: i for i, sr in enumerate(running_results)}

    # 為了預覽 working_dir,模擬 runner 的計算邏輯(包括跨 step 沿用)
    _proj_root = _P(__file__).parent.parent.absolute()
    _wf_default_wd = str(_proj_root / "ai_output" / config.name)
    _prev_wd: str = ""

    # 預先計算「哪些 step 有 output.path 設」、給警告用
    _steps_with_output: dict[str, bool] = {}
    for s in config.steps:
        _steps_with_output[s.name] = bool(s.output and s.output.path)

    out_steps: list[dict] = []
    overall_ok = True
    for idx, step in enumerate(config.steps):
        # 每個 step 開始前重 build context、納入前面 step 的 rendered output
        base_ctx = build_context(
            step_results=running_results,
            input_params=req.input_params or {},
        )
        step_info: dict = {
            "index": idx,
            "name": step.name,
            "node_type": _classify_node_type(step),
            "rendered": {},
            "referenced_vars": [],
            "errors": [],
        }
        # 收集這個 step 引用了哪些變數(掃 batch / message / output.path 等)
        for fname in ("batch", "message", "uia_window", "vv_prompt"):
            v = getattr(step, fname, "")
            if isinstance(v, str) and v:
                step_info["referenced_vars"].extend(find_referenced_vars(v))
        if step.output and step.output.path:
            step_info["referenced_vars"].extend(find_referenced_vars(step.output.path))
        step_info["referenced_vars"] = sorted(set(step_info["referenced_vars"]))

        # 試 render 主要顯示欄位
        for fname in ("batch", "message", "uia_window", "vv_prompt"):
            v = getattr(step, fname, "")
            if not (isinstance(v, str) and v and "{{" in v):
                continue
            try:
                step_info["rendered"][fname] = render(v, base_ctx)
            except ExpressionError as e:
                step_info["rendered"][fname] = v  # 保留原樣
                step_info["errors"].append(f"{fname}: {e}")
                overall_ok = False
        rendered_output_path = (step.output.path if step.output else "") or ""
        if step.output and step.output.path and "{{" in step.output.path:
            try:
                rendered_output_path = render(step.output.path, base_ctx)
                step_info["rendered"]["output_path"] = rendered_output_path
            except ExpressionError as e:
                step_info["errors"].append(f"output.path: {e}")
                overall_ok = False

        # ── 計算 working_dir(同 runner.py 的邏輯)──────────────────
        # 優先序:step.working_dir > parent(output.path) > 前一步沿用 > workflow dir 預設
        _wd: str = getattr(step, "working_dir", "") or ""
        if not _wd and rendered_output_path:
            # 用 rendered 後的 output.path(可能含 {{ }} 解開)算 parent
            _p = _P(rendered_output_path)
            if not _p.is_absolute():
                _p = _proj_root / "ai_output" / config.name / _p
            _wd = str(_p.parent.absolute())
        if not _wd and _prev_wd:
            _wd = _prev_wd
        if not _wd:
            _wd = _wf_default_wd
        step_info["working_dir"] = _wd
        _prev_wd = _wd

        # ── 檢查引用的上游 step 有沒有 output.path(沒有就 warn)─────
        # 例:user 在 batch 寫 {{ steps.前一步.output.path }} 但前一步沒設 output.path
        # → 跑起來會拿到空字串、靜默踩坑
        for ref in step_info["referenced_vars"]:
            if not ref.startswith("steps."):
                continue
            parts = ref.split(".")
            if len(parts) < 4 or parts[2] != "output" or parts[3] != "path":
                continue  # 只警告 .output.path 引用
            ref_step_name = parts[1]
            if ref_step_name not in _steps_with_output:
                step_info["errors"].append(
                    f"引用了 {ref}、但找不到名為「{ref_step_name}」的上游 step(check 拼字)"
                )
                overall_ok = False
            elif not _steps_with_output[ref_step_name]:
                # 該 step 沒設 output.path → runtime 拿到的值會是 snapshot diff 推測結果
                # 對 skill / outlook / web_crawler 這類「會自動偵測輸出」的節點還 OK
                # 對 script 節點若沒設 output.path、又沒在 batch 寫檔到 CWD → 真的會空
                step_info.setdefault("warnings", []).append(
                    f"引用了 {ref}、但「{ref_step_name}」沒設 output.path。"
                    f"runtime 會靠系統 snapshot 偵測該 step 寫了什麼新檔;若該 step 沒寫檔到工作流資料夾、此引用會是空字串"
                )

        # 把 rendered output_path 塞進 running_results、給下一個 step 的 render 用
        # (這樣即使沒跑過,user 在 YAML 寫 output.path 的下游引用也能 render 出實際值)
        if step.name and not step.human_confirm:
            stub = _SR(
                step_index=idx, step_name=step.name, exit_code=0,
                stdout_tail="", stderr_tail="",
                validation_status="ok", validation_reason="", validation_suggestion="",
                actual_output_path=rendered_output_path,
            )
            if step.name in _by_name:
                running_results[_by_name[step.name]] = stub
            else:
                _by_name[step.name] = len(running_results)
                running_results.append(stub)

        out_steps.append(step_info)

    return {
        "ok": overall_ok,
        "workflow_name": config.name,
        "input_params": req.input_params or {},
        "steps": out_steps,
    }


def _classify_node_type(step) -> str:
    """從 PipelineStep 的旗標判斷實際 node 類型(給前端 / dry-run 顯示用)。"""
    if step.condition: return "condition"
    if step.human_confirm: return "human_confirm"
    if step.computer_use: return "computer_use"
    if step.skill_mode: return "skill"
    if step.subagent: return "subagent"
    if step.web_crawler: return "web_crawler"
    if step.outlook_automation: return "outlook"
    if step.visual_validation: return "visual_validation"
    return "script"


@app.get("/workflows/{wf_id}/variables")
async def api_workflow_variables(wf_id: str):
    """列出此 workflow 的可用變數 + 上次跑出來的實際值(給前端「插入變數」modal 用)。

    回傳:
        available.steps[].fields[]:每個上游 step 提供的 output 欄位 + 上次值
        available.input[]:此 workflow 引用到的 input.X + 上次傳入值
        available.env[]:可用環境變數(過濾 secrets)
        referenced:整份 YAML 引用到的所有 dotted-path
    """
    import yaml as _yaml
    import os as _os
    from db import get_workflow as _get_wf
    from pipeline.models import PipelineConfig
    from pipeline.expression import find_referenced_vars
    from pipeline.store import get_store

    wf = _get_wf(wf_id)
    if not wf:
        raise HTTPException(status_code=404, detail="找不到工作流")

    yaml_str = wf.get("yaml", "") or ""
    if not yaml_str.strip():
        return {
            "available": {"steps": [], "input": [], "env": []},
            "referenced": [],
            "last_run_id": None,
        }

    try:
        data = _yaml.safe_load(yaml_str)
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = config_dict.get("validate", True)
        config = PipelineConfig(**config_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"workflow YAML 解析失敗:{e}")

    # 撈該 workflow 最近幾次 run、用來填 last_value。
    # 跨多次 run 合併:某步的變數以「最近一次有跑出該步變數的 run」為準 ——
    # 這樣就算最新一次 run 是半截 / 失敗,之前跑出來的變數值也不會消失。
    last_run = None
    last_step_by_name: dict = {}
    last_input_params: dict = {}
    try:
        runs = get_store().list_by_workflow(wf_id, 10)  # 新→舊
        if runs:
            last_run = runs[0]
            last_input_params = getattr(runs[0], "input_params", None) or {}
        for r in runs:
            for sr in r.step_results:
                existing = last_step_by_name.get(sr.step_name)
                if existing is None:
                    last_step_by_name[sr.step_name] = sr
                elif not getattr(existing, "step_vars", None) and getattr(sr, "step_vars", None):
                    # 先前記到的那筆沒有變數、這筆(較舊)有 → 用這筆把變數補回來
                    last_step_by_name[sr.step_name] = sr
    except Exception:
        pass

    # 1) 掃整份 YAML 找所有 {{ }} 引用
    referenced: set[str] = set()
    for step in config.steps:
        for fname in ("batch", "message", "uia_window", "vv_prompt", "working_dir",
                      "wc_url", "wc_video_url", "wc_wait_for_selector"):
            v = getattr(step, fname, "")
            if isinstance(v, str) and v:
                for ref in find_referenced_vars(v):
                    referenced.add(ref)
        if step.output and step.output.path:
            for ref in find_referenced_vars(step.output.path):
                referenced.add(ref)
        # action 內的 text / control / image
        if step.actions:
            for a in step.actions:
                for fname in ("text", "title", "title_contains", "vlm_prompt", "expected", "ocr_text"):
                    v = getattr(a, fname, "")
                    if isinstance(v, str) and v:
                        for ref in find_referenced_vars(v):
                            referenced.add(ref)
                if a.control:
                    for v in a.control.values():
                        if isinstance(v, str):
                            for ref in find_referenced_vars(v):
                                referenced.add(ref)

    # 2) 上游 step 提供的 output 欄位
    avail_steps: list[dict] = []
    for step in config.steps:
        if step.human_confirm:
            continue
        sr = last_step_by_name.get(step.name)
        fields: list[dict] = []

        # 通用欄位:path / stdout / exit_code / status
        if step.output and step.output.path:
            fields.append({
                "key": "path",
                "type": "string",
                "last_value": (sr.actual_output_path if sr else "") or step.output.path,
                "source": "output.path"
            })
        elif sr and getattr(sr, "actual_output_path", ""):
            fields.append({
                "key": "path", "type": "string",
                "last_value": sr.actual_output_path, "source": "auto-detected"
            })

        if sr:
            fields.append({"key": "stdout", "type": "string",
                           "last_value": (sr.stdout_tail or "")[:200], "source": "stdout"})
            fields.append({"key": "exit_code", "type": "number",
                           "last_value": sr.exit_code, "source": "exit_code"})
            fields.append({"key": "status", "type": "string",
                           "last_value": sr.validation_status, "source": "validation"})

        # save_as / step_vars
        if step.actions:
            seen: set[str] = set()
            for a in step.actions:
                if a.save_as and a.save_as not in seen:
                    seen.add(a.save_as)
                    last_v = ""
                    if sr and getattr(sr, "step_vars", None):
                        last_v = sr.step_vars.get(a.save_as, "")
                    fields.append({
                        "key": a.save_as,
                        "type": "string",
                        "last_value": str(last_v) if last_v else "",
                        "source": f"save_as ({a.type})",
                    })

        # skill / script 自動開放的變數:上次 run 的 step_vars
        # (skill 的 JSON 輸出欄位 / export_var 都會落在 step_vars)
        if sr and getattr(sr, "step_vars", None):
            _existing_keys = {f["key"] for f in fields}
            for _vk, _vv in sr.step_vars.items():
                if str(_vk) not in _existing_keys:
                    fields.append({
                        "key": str(_vk),
                        "type": "number" if isinstance(_vv, (int, float)) and not isinstance(_vv, bool) else "string",
                        "last_value": str(_vv),
                        "source": "節點輸出",
                    })

        avail_steps.append({
            "name": step.name,
            "node_type": _classify_node_type(step),
            "fields": fields,
        })

    # 3) input 欄位:從 referenced 中抓 input.X、配上 last_run 的值
    input_keys: list[str] = sorted({
        ref.split(".", 1)[1] for ref in referenced
        if ref.startswith("input.") and "." in ref
    })
    avail_input = [
        {"key": k, "last_value": last_input_params.get(k, ""),
         "required": True}
        for k in input_keys
    ]

    # 4) env:常用清單 + workflow 引用到的(過濾 secrets)
    common_env = ["OUTPUT_BASE_PATH", "PIPELINE_DIR", "HOME", "USERPROFILE", "TIMEZONE"]
    env_keys: set[str] = set(common_env)
    for ref in referenced:
        if ref.startswith("env.") and "." in ref:
            k = ref.split(".", 1)[1]
            env_keys.add(k)

    def _is_secret(k: str) -> bool:
        u = k.upper()
        return any(t in u for t in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PWD"))

    avail_env = [
        {"key": k, "last_value": _os.environ.get(k, ""), "is_secret": False}
        for k in sorted(env_keys) if not _is_secret(k) and _os.environ.get(k)
    ]

    return {
        "available": {
            "steps": avail_steps,
            "input": avail_input,
            "env": avail_env,
        },
        "referenced": sorted(referenced),
        "last_run_id": last_run.run_id if last_run else None,
    }


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
    if req.decision not in ("retry", "skip", "abort", "continue", "retry_with_hint", "answer", "install_dep", "approve_command", "deny_command", "hint_command", "redo_prev"):
        raise HTTPException(status_code=400, detail="decision 必須是 retry / skip / abort / continue / retry_with_hint / answer / install_dep / approve_command / deny_command / hint_command / redo_prev")
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

# 你能呼叫的工具

當使用者問起特定工作流 / run / log 細節、且資訊不在你目前看到的脈絡裡 → **主動呼叫工具拉資料**、不要編造、也不要叫使用者再說一次。

## 讀工具（read-only、隨意呼叫）

| Tool | 用途 |
|---|---|
| `list_workflows()` | 列所有工作流（id, name, 最近一次 run 狀態）。模糊問題優先用這個摸清狀態 |
| `get_workflow_yaml(query)` | 拿某工作流的 YAML（query 用 name 或 id 前綴）|
| `get_recent_runs(query, limit=5)` | 拿某工作流最近 N 筆 run（含 run_id, status, 時間）|
| `get_run_log(run_id, max_chars=12000)` | 拿某 run 的 log 內容（從末段截、log 末尾通常是錯誤訊息）|
| `list_workflow_variables(query)` | 列出某工作流的可用 `{{ }}` 變數 + 上次跑出來的實際值。修改 / 規劃時想引用上游 step output 前用這個確認 |

## 寫工具（destructive、必走兩步協議）

| Tool | 用途 |
|---|---|
| `save_workflow_yaml(query, yaml_content, confirm)` | **更新既有** workflow 的 YAML（query 找名/id、會覆蓋原 YAML）|
| `create_workflow_yaml(name, yaml_content, confirm)` | **建立全新** workflow（同名已存在會拒絕）|
| `start_workflow(query, confirm)` | 啟動指定 workflow |
| `send_file_to_tg(workflow_query, filename, confirm)` | 從 workflow 輸出資料夾抓檔送到使用者 TG |
| `schedule_workflow(query, schedule_expr, confirm)` | 為某 workflow 建立 cron 排程（每天/每週固定時間自動跑）|
| `cancel_schedule(task_id_or_name, confirm)` | 取消（刪除）某個 cron 排程 |
| `list_schedules()` | 列出所有 cron 排程任務（read-only、隨意呼叫）|

### 排程使用情境
- 使用者說「每天早上 9 點跑」「每週一定時跑」「自動化」→ `schedule_workflow(query, schedule_expr)`
- 常見 cron 範例：`0 9 * * *`（每天 9:00）、`0 9 * * 1-5`（週一至五 9:00）、`0 */2 * * *`（每 2 小時）、`30 18 * * 1`（週一 18:30）
- 使用者說「取消那個定時任務」→ 先 `list_schedules()` 看清單、再 `cancel_schedule(name)`

### 何時 save vs create
- 使用者說「修這個 / 改那個」「把 X 改成」→ `save_workflow_yaml`（更新既有）
- 使用者說「建一個新工作流」「再開一個叫 X 的」「設定成新 YAML」→ `create_workflow_yaml`（建新的）
- 對話脈絡剛規劃好新流程、要落地 → 預設 `create_workflow_yaml`（除非使用者指定要套到既有的某個）

## 網路搜尋（限定工作流相關研究）

| Tool | 用途 |
|---|---|
| `web_search(query, max_results, full_content)` | 用 Tavily 搜網。**只在規劃 / debug 工作流需要外部資訊時用** |

### 何時 call `web_search`（要 call）
- 使用者要抓某站點、但你不知道它的 URL 結構 / RSS / API endpoint（例：「ithome 的 RSS 在哪？」「PTT 看板 URL 規則」）
- 你不確定某 Python 套件的最新版本 / 新 API / 替代品（例：「crawl4ai 0.8 怎麼用」「opencv 4.x 跟 4.5 差別」）
- 使用者要的功能你想不出來怎麼裝（例：「pdf 加密怎麼搞」→ search 一下找出推薦套件再規劃）
- 工作流邏輯需要的具體外部知識（網站反爬機制、登入流程等）

### 何時**不要** call（避免漂題 + 浪費 quota）
- 使用者閒聊：天氣、股價、新聞、八卦、笑話 → **不 search**、簡短禮貌回 + 引導回主題
- 已是你 LLM 常識的事（Python 基本語法、HTTP 概念、git 基本命令）→ **不 search**、直接答
- 使用者問你身分 / 你是誰 / 你能做什麼 → **不 search**、直接介紹

### 離題處理範例

> 👤 今天天氣怎樣？
>
> 🤖 我是 Pipeline 工作流設定助手、不太查天氣這類即時資訊。我能幫你的是：規劃自動化工作流、debug 跑失敗的 run、改 YAML、傳檔到 TG 等。你有想自動化的事嗎？

> 👤 AAPL 股價多少？
>
> 🤖 一般股價我不適合查（不是我本職、也容易給過時資訊）。但如果你要建一個「**每天自動抓股價、寄日報**」的工作流、我可以幫你規劃 — 要試試嗎？

### 🛑 兩步協議（必須遵守、違反等於擅自寫資料）

寫工具呼叫**永遠分兩步**：

**第一次：confirm=False（預覽、不寫）**
- 工具回 `[PREVIEW]` 結果（只是看、沒動 DB）
- 你用純文字向使用者明確確認，例：
  > 「我準備把新版 YAML 套到「PPT PC版輿情分析」、原 4 節點變 5 節點。要套用嗎？回 yes 確認、no 取消」

**第二次：confirm=True（實寫）**
- 等使用者**明確**同意（例：「yes」「OK」「套用」「好」「跑吧」「是」）才呼叫
- 使用者語意模糊（「再看看」「好像」「也許」）= 沒同意 → 繼續確認、不要寫
- 使用者沒明確說過同意就 confirm=True = 你違反協議、擅自寫資料

### 寫工具情境流程

| 使用者意圖 | 你的動作 |
|---|---|
| 「幫我加一步 X」 | get_workflow_yaml → 改好 → save_workflow_yaml(confirm=False) → 文字確認 |
| 「OK 套用吧」（在你已詢問後）| save_workflow_yaml(confirm=True) |
| 「跑這個 workflow」 | start_workflow(confirm=False) → 文字確認 → 等同意 → start_workflow(confirm=True) |
| 「套用後直接跑」 | save(confirm=False) → 確認 → save(confirm=True) → start(confirm=False) → 確認 → start(confirm=True) |

## 工具使用原則

- 使用者問「最近哪個失敗了」→ 先 `list_workflows()` 看狀態 → 如有 failed/aborted → `get_run_log()` 看細節再回答
- 使用者貼 run_id（含完整或前 8 字前綴）→ 直接 `get_run_log(run_id)`、不要先問「是哪個工作流」
- 使用者要求看某工作流 YAML → 直接 `get_workflow_yaml(query)`
- 使用者要修 YAML → 先 `get_workflow_yaml` 拿原 YAML、再針對性給 patch
- 連續呼叫上限 5 次／chat turn、超過 = 用現有資料回答、不再呼叫
- **不要為了「保險」一口氣呼叫 4 個 tool**，按需呼叫、節省 latency
- **寫工具必走兩步協議、不要省略確認**

## ⚠️ 修改既有 YAML 時的「個人化欄位」鐵律（很重要、違反算嚴重 bug）

當你透過 `get_workflow_yaml` 拿到某工作流的 YAML、要產出修改版時，**不要默默沿用原 YAML 裡的「個人化／使用者特定欄位」**。原 YAML 那些欄位是上一位設定者填的、新使用者可能完全不一樣。

| 個人化欄位（不要沿用） | 你應該做的 |
|---|---|
| `to` / `cc` / `bcc`（收件人 email） | **反問使用者**「要寄給誰？」 |
| 主旨 / 內文（含具體公司、人名、產品名） | 用通用敘述、或反問 |
| Windows 絕對路徑（如 `C:/Users/xxx/...` 或 `D:/...`） | 反問或改用相對 `ai_output/<workflow>/` |
| `cookies` / API token / login 憑證 | 不寫 YAML、提醒走 `.env` 或 settings |
| 個人 chat_id / phone / Slack channel | 反問 |

**判斷原則**：這個欄位是「**會跟著個人變、不能 reuse 別人的**」嗎？是 → 反問；不是（如 `timeout`、`validate`、`scroll_count`、節點類型等通用設定）→ 沿用原值即可。

**例外**：使用者明確說「沿用原本的收件人」「跟之前一樣寄給 X」時、才照原 YAML 填。否則預設反問。

**處理方式 2 選 1**：
1. **反問**：「我看到原 YAML 是寄給 `wilson_bai@asus.com`，要繼續寄給他、還是換別人？」（推薦）
2. **佔位符**：在 YAML 裡寫 `to: "<請填收件人 email>"` 並提醒使用者：「YAML 裡的 `to` 我留空、請套用後到 Outlook 節點 panel 填上你的收件人」

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
| `subagent` | `subagent: true` + `subagent_role` + `batch` | role 沒填走 data_analyst 預設;batch 太籠統 LLM 多輪推理也走不出來 |
| `condition` | `condition: true` +（`expression`+`on_true`/`on_false`）或（`switch`+`cases`）| on_true/on_false/cases 指到不存在的 step name |

3. **特別針對 `outlook_automation`：使用者給的 email 必填到 `outlook_params.to`**。沒有 email 不要產 YAML、回去問。
4. **`outlook_params` 一律用 inline JSON 一行寫**（不要多行 YAML 格式 — 前端解析器只認 inline JSON）：
   - ✅ 正確：`outlook_params: {"to":"wilson@x.com","subject":"日報","body":"請查收"}`
   - ❌ 錯誤：`outlook_params:` 換行後 `  to: wilson@x.com` `  subject: ...`（多行格式會被前端解析器丟掉）
   - **⚠ 已知 round-trip bug**：用戶把含 `outlook_params:` 的 YAML 貼到 YAML 面板「套用」後，這欄位有時會被前端 round-trip 吃掉。**所以你最後在 Plan / Confirm / 回應結尾必須附帶提醒**：
     > 「貼上 YAML 套用後，請到畫布的 Outlook 節點 panel 點開、確認 to / subject / body 都填好（YAML round-trip 有時會吃掉這欄位）。」
5. **路徑判斷**：使用者沒指定 → 用相對（純檔名最簡，系統自動落到 workflow dir）。使用者明說特定值（含絕對路徑、家目錄、磁碟代號）→ 照用

---

# 🪄 變數系統 — 何時該寫 `{{ }}`(很重要!)

Pipeline 支援 Jinja2 變數語法、讓 workflow 從「寫死腳本」變成「可重用函式」。**判斷得當會大幅提升使用者價值。**

## 三種變數來源(YAML 任何字串欄位都能用)

| 語法 | 來源 | 範例 |
|---|---|---|
| `{{ steps.<name>.output.<key> }}` | 上游節點輸出 | `{{ steps.crawl.output.path }}` / `{{ steps.extract_order.output.order_id }}` |
| `{{ input.<key> }}` | 啟動 workflow 時 user 傳入的參數(/run 帶或前端對話框填) | `{{ input.date }}` / `{{ input.customer }}` |
| `{{ env.<key> }}` | 環境變數(`OUTPUT_BASE_PATH` / `HOME` / 等) | `{{ env.OUTPUT_BASE_PATH }}/result.csv` |

**所有 step 字串欄位都吃變數**:`batch` / `output.path` / `message` / `uia_window` / `vv_prompt` / `outlook_params.subject` / 等。

## 何時**該**用變數(看到這些情境主動加)

| 使用者說 | 該用 | 為什麼 |
|---|---|---|
| 「每天 / 每週自動跑」 | `{{ input.date }}`,設定 cron 排程帶 today | 否則檔名會固定、每天蓋掉昨天 |
| 「同一條流程跑不同客戶 / 部門」 | `{{ input.customer }}` 等 | 一條 YAML 處理所有 case、改流程改一處 |
| 「上一步抓到的 X 餵給下一步」 | `{{ steps.X.output.<save_as> }}` | 跨節點傳值,免剪貼簿繞道 |
| 「用 UIA 抓欄位、後面要查 / 寄 / 存」 | UIA 用 `save_as: order_id`、下游 `{{ steps.uia_step.output.order_id }}` | UIA save_as 自動成為 inter-step 變數 |

## 何時**不該**用變數(避免過度抽象)

- 使用者只跑「**一次性 / 寫死腳本**」、值不會變 → 不要硬塞 `{{ }}`,直接寫死
- 使用者已給絕對路徑 / 具體 email / 固定 URL → 寫死即可
- 步驟內 UIA 短變數(如 `text: "{{order_id}}"` 引用同步驟 save_as)→ **保留 UIA 既有語法**,不要轉成 `steps.X.output.X`(那是錯的、會打架)

## 範例對照

❌ **寫死(每天要改 YAML)**:
```yaml
- name: crawl
  batch: python ptt.py --date 2026-05-10 --out data/2026-05-10.csv
- name: email
  batch: python send.py --to boss@x.com --file data/2026-05-10.csv
```

✅ **變數化(一條 YAML 配 cron 跑一輩子)**:
```yaml
- name: crawl
  batch: python ptt.py --date {{ input.date }} --out data/{{ input.date }}.csv
- name: email
  batch: python send.py --to {{ input.email }} --file {{ steps.crawl.output.path }}
```

啟動方式:`/run daily_report date=today email=boss@x.com`(`today` 自動轉今天日期)

## UIA save_as 跨節點傳值(很重要)

```yaml
- name: extract_order
  computer_use: true
  cu_mode: uia
  actions:
    - type: uia_get_text
      control: { type: Edit, name: "訂單編號" }
      save_as: order_id          # ← 自動晉升為 steps.extract_order.output.order_id

- name: query_logistics
  batch: curl https://api.x.com/track/{{ steps.extract_order.output.order_id }}
```

> 設計變數化的 workflow 時,主動跟使用者確認:「這個值之後會變嗎?要不要做成啟動參數?」如果使用者要排程跑、跨客戶用、或上一步抓的值要餵給後面 → **務必用 `{{ }}`**。

## ⚠️ skill 產 JSON → 下游讀取:先對齊欄位名（過夜測試踩過）

skill 節點讓 LLM 自由寫 code、輸出 JSON 時，**欄位名是 LLM 即興決定的**
（需求講中文「姓名 / 薪資」→ LLM 多半就產中文 key `姓名` / `薪資`）。
若下游接一個 **script 節點**、用寫死的 code 去讀（`e['salary']`），key 對不上就 `KeyError`。

兩種正確做法、擇一（不要產「上游 skill 自由輸出 + 下游 script 寫死 key」這種組合）：
- **上游 skill 的 batch 明確釘死輸出 key 名**：
  「…輸出 JSON、每筆含欄位 `name` / `dept` / `salary`（就用這三個英文 key）」
- **下游也用 skill 節點**（而非 script）：skill 的 LLM 會自己 read_file 看實際結構、適應 schema

---

# 節點類型（節點全集）

## 1. 腳本節點（script）
**使用者說**：「我的 xxx.py 腳本」「執行 xxx 指令」「跑這個批次檔」
```yaml
- name: 抓資料
  batch: |
    python ~/scripts/fetch.py --date=today
  timeout: 300
  retry: 2
```

### ⚠️ script 的 `batch` 寫法鐵則（過夜測試踩過兩個坑、務必遵守）

1. **`batch` 一律用區塊純量 `batch: |`**（換行後縮排寫命令）。
   裸 `batch: <命令>` 若命令含冒號（Python 的 `with:` / `for:` / `if:` / dict、中文「為:」）
   會被 YAML 當成 mapping 解析、**整份 YAML 炸掉**（`mapping values are not allowed here`）。
   用 `|` 一律安全 — 所以**不管命令長什麼樣、永遠用 `batch: |`**。

2. **絕對不要產多行的 `python -c "..."`**。多行 `python -c` 在 Windows 會被 shell
   在換行處切斷 → **exit 0 卻什麼都沒執行**（靜默失敗、最難抓的那種 bug）。
   要跑 inline Python：
   - 簡單邏輯 → 寫成**單行**、用 `;` 串：
     `python -c "import json; d=json.load(open('x.json')); print(len(d))"`
   - 稍複雜 / 需要多行 / 含迴圈或 `with` → **改用 skill 節點**（描述需求讓 LLM 寫 .py），
     不要硬塞多行 `python -c`。

### 1b. 背景模式(`background: true`)— GUI / daemon / server
**使用者說**:「開 GUI app」「啟動視窗」「跑一個 server」「daemon 跑著」「一直開著」「永遠不結束」、
「打開 X 程式讓後面節點點按鈕」、「launch X 然後自動化」
這時 script 必須加 `background: true`、否則 workflow 會卡在這 step 等 process 永遠不會 exit。

```yaml
- name: 開 GUI app
  batch: python my_app.py
  background: true             # ← 不等 exit、立即下一步、subprocess 留著給後面節點用
  ready_after_seconds: 3       # 選填:等 N 秒讓 GUI boot、再下一步(預設 0 = 不等)
  # workflow 結束時 subprocess 自動 kill、不會殘留
```

判斷小竅門:使用者描述含「**啟動**」「**開**」「**launch**」「**GUI**」「**server**」「**daemon**」「**永遠**」「**一直**」「**讓 X 開著**」這類字眼 → 主動加 `background: true`。
反例:「跑」「執行」「處理」「分析」這類「會結束的批次任務」→ **不要**加 `background`(會誤判)。

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
- `wc_cookies` 可填 `${VAR_NAME}` 參照 backend/.env（cookie 是登入憑證、不該明文存 workflow）
- `wc_child_link_pattern` 自動清單已涵蓋 Reddit / PTT / Dcard / HN / 新聞 / Mobile01 + 購物站
  （蝦皮 / momo / PChome / 露天 / Amazon / eBay / 淘寶 / 京東）；不在清單的站可自填 regex
- **反爬現實**：蝦皮 / 淘寶 / 京東 / Walmart 等用「自家反爬」（非 Cloudflare），FlareSolverr
  解不了、需登入 cookie 才爬得到；一般論壇 / 新聞站 / 維基幾乎無反爬、好爬

## 4.5 解析爬蟲內容節點（skill: scraped-content-parser）

**核心**：爬蟲節點輸出的是**原始 HTML / markdown**。若使用者要的是「**結構化資料**」
（每則留言 / 每個商品 / 每筆搜尋結果 → JSON），**不要叫一個通用 skill 節點「讀內容自己抽」**
（LLM 逐筆抽取非確定性、慢、會漏、會編造），而是掛專用 skill `scraped-content-parser`：
它讓 LLM 看樣本寫一支確定性 parser、再用程式碼跑完整檔、V5 Recipe 快取下次秒過。

```yaml
- name: 解析貼文
  skill_mode: true
  skill: scraped-content-parser
  batch: |
    解析 {{ steps.爬蟲節點名.output.path }}、辨識列表頁與子頁、
    抽出每篇貼文的標題 / 連結 / 內文、輸出結構化 JSON 至 parsed.json
  output:
    path: parsed.json
```

**何時掛 / 不掛**：
- 掛 → 輸出含**大量重複記錄**、要逐筆資料（論壇留言、購物商品、搜尋結果、新聞**列表頁**）
- 不掛 → **單一頁面**（一篇新聞內文、一篇部落格、維基條目）→ 爬蟲 markdown 本身就是內容、
  直接餵下游、再掛 parser 是多餘的 LLM 步驟

**多站比較場景**（如「比較 3 個購物站的 X 價格」）：
- 3 個**不同站**結構不同 → 要 **3 組「爬蟲 + scraped-content-parser」**、各站各一支 parser
- **不要**把 3 個不同站塞進一個爬蟲節點的多 URL（會合併成一檔、一支 parser 解不了 3 種結構）
- 各 parser 輸出**不同檔名**（pchome.json / amazon.json / ...）
- 最後一個 skill 節點當「比較 / 分析節點」、用多個 `{{ steps.X.output.path }}` 讀進 3 個 JSON 彙整
- runner 是線性執行 → 節點排成一直線即可（3 站依序爬、非真平行、但結果一樣）

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

## 6.5 AI 驗證(`output.expect` — 上一個 step 的驗證描述)
**使用者說**:「自動審核輸出」「跑完幫我檢查對不對」「驗證內容符不符合預期」「AI 驗證」
**重要**:畫布上**看得到獨立的「AI 驗證節點」**(紫色盾牌 icon),但**它編譯成 YAML 時會併進前一個** step 的 `output.expect`、**不是獨立 YAML 步驟**。
所以你若看到畫布上有 aiValidation 節點、轉回 YAML 時:
- 直接把它的 expectText 塞到前一個 step 的 `output.expect`
- **不要**在 YAML 寫成獨立 step(會炸、PipelineConfig 沒這欄位)
- 反之若使用者直接要「幫我加 AI 驗證」,在 skill / script step 的 output 加 expect 就好、不需要建獨立節點

```yaml
- name: 摘要報告
  skill_mode: true
  batch: |
    讀 raw.md、摘成 10 條重點、輸出 daily.md
  output:
    path: daily.md
    expect: "至少 10 條中文重點、每條 30 字內、不能有英文段落"
    # 進階:深度驗證(LLM 用 read_file 等工具去細查、token 成本較高)
    # skill_mode: true
```

**`expect` 填什麼**:用自然語言寫具體驗收條件、不要寫成模糊「好不好看」。
- ✅ 「CSV 至少 100 筆、欄位 email 全部 RFC5322 合法」
- ✅ 「每張投影片有標題、no overflow、字體至少 18pt」
- ❌ 「品質要好」「結果要對」

**何時加深度驗證(`output.skill_mode: true`)**:文字 / 數字精準度要查內容(不只是格式)、容忍 LLM 多花 5-15s 跑工具確認。
**何時不加**:單純看 stdout / 檔案存在,淺驗證夠用、便宜。

## 7. 視覺驗證節點（visual_validation）
**使用者說**：「檢查產出畫面對不對」「驗證截圖」「看圖判斷」
```yaml
- name: 檢查 Excel 排版
  visual_validation: true
  vv_source: prev_output           # 上一步的輸出檔；另一個值 current_screen 是即時抓螢幕
  vv_prompt: 應該看到一張表頭加粗、欄寬對齊內容的 Excel
  timeout: 120
```

## 8. 多輪代理節點（subagent）— 探索式 / 試錯式任務
**使用者說**：「研究 / 探索」「邊想邊做」「不確定怎麼做、試試看」「debug 到通」「結構不固定」
**跟 AI 技能的差別**：每次都 LLM 多輪推理（無 Recipe、無外部驗證）、token 成本是 skill 的 2-5 倍、但能根據中間結果調整。

**5 個內建角色**（`subagent_role` 欄位、各自的工具白名單不同）：

| role | 職責 | 工具白名單 |
|---|---|---|
| `data_analyst` | 處理 csv/xlsx、產 markdown/xlsx/png | run_python, read_file, web_search |
| `coder` | 寫 / debug Python script 到通 | run_python, run_shell, read_file, web_search |
| `researcher` | 收料、產 markdown 摘要、列來源、不下決策 | web_search, read_file, run_python |
| `critic` | 純唯讀、挑 3 個最重要問題、不建議補強 | read_file（**沒 run_python，不能改檔**）|
| `planner` | 純推理、拆模糊大任務成步驟、不執行 | （無工具，只能 done）|

```yaml
- name: 探索式財務分析
  subagent: true
  subagent_role: data_analyst
  subagent_max_iter: 5            # 預設 5、複雜任務 8-10
  batch: |
    讀 sales.xlsx、找出 Q1 環比下滑最嚴重的 3 個品類、
    畫趨勢折線圖、產出分析報告 analysis.md
  timeout: 600
```

### ⚠️ 何時用 subagent vs AI 技能（**重要決策、不要選錯**）

**預設用 AI 技能 + Recipe**。出現以下訊號才升級用 subagent：

| 訊號 | 用什麼 |
|---|---|
| 每天 / 每週重複跑、邏輯固定 | **AI 技能 + Recipe**（第 1 次 LLM 寫 code、之後零 token 直接 replay）|
| 結構不固定、邊想邊改、可能要試錯 | **subagent**（每次重新推理、能根據中間結果調整）|
| 純拿意見 / 審稿 / 挑問題 | **subagent + critic**（只讀檔挑錯、不會改）|
| 純拆任務、規劃步驟 | **subagent + planner**（純推理、不執行任何工具）|
| 收料 + 整理摘要、要列來源 | **subagent + researcher**（研究式收料、不下決策）|
| 寫 / debug Python 到通 | **subagent + coder**（多輪試錯改 code）|

**不要用 subagent 的徵兆**（勸使用者改用 AI 技能）：
- 每天 / 每週重複跑（→ Recipe 更省錢、第二次起零 token）
- 流程明確固定（→ 寫死成 skill 邏輯更穩、不會每次結果不一樣）
- 對成本敏感（→ subagent token 用量是 skill 的 2-5 倍）

**判斷小竅門**：使用者描述含「研究」「探索」「試試看」「邊看邊改」「debug」「不確定」「看情況」這類字眼 → 多代理；含「每天」「自動化」「定時」「日報」「跑一次」這類 → AI 技能。

## 9. 條件節點（condition）— 分支控制流
**使用者說**：「如果 X 就…否則…」「資料超過 N 筆才寄信」「依狀態走不同步驟」「分支」「失敗就走另一條」
**純 metadata 節點、不跑任何命令**：runner 求值表達式、再依結果跳到指定的下游步驟。
**有這個節點、別跟使用者說系統沒有分支功能。**

### ⚠️ 判斷值要怎麼來（最重要、不照做 condition 一定壞）

`expression` / `switch` 只能引用 `output` namespace **真的有的 key**。固定 key：
- `{{ steps.X.output.stdout }}` — 該步的 stdout
- `{{ steps.X.output.path }}` — 該步輸出檔的路徑
- `{{ steps.X.output.status }}` — ⚠️ 這是該步的**驗證狀態**（`ok` / `failed`）、**不是**你資料裡的 status 欄位、不要拿來分流
- `{{ input.X }}` — 啟動參數

**判斷值有兩種正確做法、擇一：**

**做法 A — `script` 步驟 print 成 stdout(最簡單、script 節點用這個):**
script 把要判斷的值 `print` 出來,condition 引用 `{{ steps.X.output.stdout }}`。

**做法 B — skill 把判斷值放進它的 JSON 輸出檔(skill 節點要餵 condition 就用這個):**
skill 節點的 stdout 太雜(`[run_python]...`)沒法直接給 condition。但 skill 的輸出檔
**若是個 JSON 物件,runner 會自動把裡面的數字 / 文字欄位開放給下游** —— condition 就能用
`{{ steps.X.output.<欄位名> }}` 引用。所以 skill 的 batch **完全不用提任何系統機制 / 工具名**,
只要正常講「算出某值、跟其他數字一起存成 stats.json 之類的 JSON 檔」就好。欄位名可中可英。
```yaml
- name: 統計
  skill_mode: true
  batch: |
    …算出負評百分比…把數字存成 stats.json、欄位包含「負評百分比」
  output:
    path: stats.json
- name: 判斷
  condition: true
  expression: "{{ steps.統計.output.負評百分比 | int > 40 }}"
```

### IF 模式（`expression` + `on_true` / `on_false`）
```yaml
- name: count_orders
  batch: |
    python -c "import json; print(len(json.load(open('orders.json'))))"
- name: check_count
  condition: true
  expression: "{{ steps.count_orders.output.stdout | int > 25 }}"   # Jinja2 布林表達式
  on_true: 批次處理       # 成立 → 跳到這個 step name
  on_false: 簡易處理      # 不成立 → 跳到這個 step name（留空 = 結束流程）
```

### Switch 模式（`switch` + `cases`，忽略 `expression`）
```yaml
- name: read_status
  batch: |
    python -c "print(open('status.txt').read().strip())"   # 把要分流的值 print 成 stdout
- name: 依狀態分流
  condition: true
  switch: "{{ steps.read_status.output.stdout }}"
  cases: {"paid": 出貨, "pending": 催款, "cancelled": 退款}   # inline JSON 一行寫
  default: 通報異常       # 沒命中任何 case 的 fallback（留空 = 結束）
```

### 分支步驟要收尾
被 `on_true` 指到的步驟跑完後，會線性掉進 YAML 的下一個步驟。
若不想讓 true 分支跑完又掉進 false 分支，在 true 分支的步驟加 `next: end`：
```yaml
- name: 批次處理
  skill_mode: true
  batch: |
    讀上一步的資料做批次處理
  next: end             # 跑完直接結束、不會再掉進「簡易處理」
```

**何時用 condition**：使用者明說「如果 / 否則 / 依情況 / 超過就 / 分支」這類條件邏輯時才用。
單純線性流程不要硬加。

<!--TG_ONLY_BEGIN-->
## ⛓️ 派子代理 ad-hoc 執行 vs 建 workflow（重要決策）

當使用者要實際在沙盒內執行：寫程式 / 跑測試 / 處理檔案 / 做分析、有兩條路徑：

### A. 直接派子代理（`dispatch_subagent_async`）
非同步派出、立即釋放對話、子代理在沙盒(WSL Docker)寫 + 跑、結果之後查。
適合：單次 ad-hoc 任務、不重複跑、用戶之後不會從畫布手動觸發。

### B. 建 workflow YAML（`create_workflow_yaml` + `start_workflow`）
正式工作流、畫布上一個或多個節點、可重複跑、能 schedule、從 sidebar 看歷史。
適合：每天 / 每週重複跑、想 cron 排程、想能停下重跑、想 sidebar 看 run history。

### 何時直接派、何時建 workflow、何時反問

| 使用者描述 | 行為 |
|---|---|
| 「我要每天 / 每週自動 X」「能不能排程」「自動化」 | **直接走 workflow 路線**、不反問 |
| 「幫我建工作流做 X」「在畫布上加 X」「設定一個 pipeline」 | **直接 workflow 路線**(explicit 指定) |
| 「跑這段試試看」「測試一下這個」「驗證 ...」 | **直接派子代理**(明顯一次性 ad-hoc、不必反問) |
| 純討論 / 問建議 / 問怎麼寫 | **不派、不建**、直接答 |
| **「幫我寫個 X 工具/腳本/應用」「分析這份資料找 Y」「處理這檔案」**（沒提自動化 / 排程 / 工作流）| **反問用戶選 A 還 B**(預設不擅自決策) |

### 反問範本（用使用者語氣、不要太正式）

> 這個任務我有兩種做法、看你比較想要哪個:
>
> **A. 直接派子代理在沙盒寫 + 跑一次**
> - 30 秒派出、跑完約 1-3 分鐘、結果回我這裡給你看
> - 適合「這次寫好就好、之後不一定會再跑」
>
> **B. 建一個工作流**
> - 之後可以從畫布手動跑、能排程定時跑
> - 適合「會重複用 / 想自動化」
>
> 你選哪個？

### 派子代理時細節（A 路徑）

- **role 怎麼選**：寫程式 / debug → `coder`；處理 csv/xlsx 產報表 → `data_analyst`；
  收料 / 研究 / 摘要 → `researcher`；純唯讀挑問題 → `critic`；
  純拆步驟、不執行 → `planner`
- **working_dir**：使用者沒明說 → 留空(系統自動推 `ai_output/chat-adhoc/<timestamp>/`)。
  使用者說「放在 X」→ 把 X 帶進去
- **max_iter**：簡單任務 6-8、複雜任務 10-15。**不要設 5 以下**:寫 .py + 跑 + done
  最少 3 輪、LLM 多繞 read_file / 試錯就 5-7 輪、設 5 高機率 max_iter exceeded 失敗
- **失敗時不要自動重派同樣 prompt**:看 check_subagent_status 的 error / summary、
  跟使用者說「跑了 N 輪沒完成、原因 X」、讓使用者決定:加 max_iter 重派 / 改任務描述 / 放棄。
  連續派 3 次都 max_iter exceeded 等於白燒 token、要主動 stop

### Chain 模式(多階段自動接力)
複雜任務含「寫 + 審 + 改」「規劃 + 執行 + 驗證」、用 dispatch_subagent_async 的
follow_up 參數讓 backend 自動接力、不必每階段再 trigger。**格式 / max_iter 建議 /
典型配置請 call `read_help_doc('chain')`**。

### 不要做的事

- 不要每次小事都派子代理(問怎麼寫個 for 迴圈不需要派)
- 派之前**至少要清楚 working_dir + role 任務描述**、不要 prompt 太籠統("寫個程式")
- 同時間最多派 3 個子代理(避免沙盒併發過多)— check 一下 in-flight 區段

## 📡 In-flight 子代理狀態主動匯報

System prompt 結尾若有「in-flight 子代理」digest、回應使用者時要主動帶出進度:

- **running 中**：在你訊息**結尾**順帶一句進度。例:
  > （順便：子代理 abc123 還在跑、剛跑完第 2 輪、用了 run_python）
- **剛完成（60 秒內）且使用者沒問起**：主動報結果。例:
  > （✅ 子代理 abc123 跑完了：寫了 sincos.py 在 ai_output/sincos_tool/、test 通過）
- **已完成且超過 60 秒、使用者沒問**：不再主動提(避免每次都重複報)
- 想看細節 → 呼叫 `check_subagent_status(task_id)` 拿完整 summary、tool 用量、token 數

## 📂 子代理產物 / ⏹ 中止子代理(細節 lazy load)
- 使用者問「貼程式給我」「下載」「傳檔」 → call `read_help_doc('files')` 看
  read_subagent_file / send_subagent_file_to_tg 用法(子代理產物**不能**用
  send_file_to_tg、那個是 workflow 用的)
- 使用者說「停止」「中斷」「不要跑了」 → call `read_help_doc('cancel')` 看
  cancel_subagent_task 規則(完成 / 失敗的不用 cancel)
<!--TG_ONLY_END-->

## ⚠️ 桌面自動化節點（computer_use）— 你不要寫 YAML
**使用者說**：「自動點按鈕」「UI 自動化」「錄製操作」「滑鼠點擊」
**你的回應**：
> 桌面自動化節點需要先在畫布拉一個 computer_use 節點，按錄製鈕錄下你要操作的動作（滑鼠/鍵盤/截圖比對），AI 助手沒辦法幫你寫 actions 序列。錄完後再來討論前後步驟。

actions 序列是錄製產生的，不是 LLM 該寫的。

---

# 共用欄位規則

- `name`：步驟名稱（中文 OK）。**不要用空格** — 用底線或連字號（`抓取_PTT_列表`、不要 `抓取 PTT 列表`）。
  步驟名會被 `{{ steps.<name>.output.path }}` 引用，name 含空格會讓 Jinja 模板解析失敗
  （`{{ steps.抓取 PTT 列表.output.path }}` → 炸）。condition 的 `on_true`/`on_false`/`cases` 同理
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

當使用者明說「報告存到 D:\\Reports\\daily.xlsx」「寫到 ~/Documents/output.md」這類**特定位置**時，
**直接用使用者給的路徑、包含絕對路徑都 OK**。系統 backend 完全接受絕對路徑：
- ✅ `output.path: D:\\Reports\\daily.xlsx`（Windows）
- ✅ `output.path: ~/Documents/output.md`（家目錄展開）
- ✅ `output.path: /shared/reports/daily.csv`（POSIX 絕對）

**注意**：絕對路徑會綁定該機器、跨機器移植 YAML 時要手動改。所以使用者沒指定就**不要主動用絕對路徑**。

## 重要：legacy script 整合場景 → 一定用絕對路徑

使用者說「我有支舊的 `financial.py`，它寫死輸出到 `D:\\Old\\out.xlsx`，後面幫我做資料清洗」這種場景：

**第一步 script 節點的 `output.path` 必須跟 script 內部寫死的位置一致**（用絕對路徑）。
不這樣寫的話會踩兩個坑：
1. **Validator 找不到檔案 → 假失敗 → 整個 step 卡 awaiting_human**
2. **下一個 step 的 `prev_outputs` 會給錯路徑**，後處理 skill 讀不到上一步的東西

正確寫法：

```yaml
- name: 跑既有財務系統
  batch: python D:\\LegacyProject\\financial.py    # 內部寫死輸出到 D:\\Old\\out.xlsx
  output:
    path: D:\\Old\\out.xlsx                        # ← 跟 script 寫的一致

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
| **RSS / Atom feed 抓取**（重要、不要用 web_crawler）| `skill(用 `feedparser` 解 RSS) → skill(摘要)` |
| **啟動既有 Python 專案 + 驗證 + 確認** | `skill(啟動 main.py、必要時改 CLI 版) → ai_validation 或 visual_validation → human_confirm` |
| **探索式分析 / debug / 研究式收料**（不固定流程）| `subagent(role + batch)` ← 單節點多輪推理、不要拆成多個 skill |
| **拆任務 + 跑** | `subagent(planner) → skill 或 subagent` ← planner 先拆步驟、後續按步跑 |
| **寫 + 審稿循環** | `subagent(coder) → subagent(critic)` ← coder 寫到通、critic 唯讀挑 3 個問題 |

# ⚠️ RSS / Atom Feed 不要用 web_crawler 節點（重要、之前踩過）

當使用者要抓 RSS / Atom feed（URL 含 `/rss/`、`/feed`、`.xml`、`.atom`）：

**❌ 不要用 `web_crawler` 節點**：
- web_crawler 走 Playwright/Chromium、Chrome 不渲染 XML、會回 `net::ERR_HTTP_RESPONSE_CODE_FAILURE`
- 真實踩過：theverge.com/rss/ai-artificial-intelligence 用 web_crawler → Tier 1 失敗
- Tier 2 FlareSolverr 也是 Puppeteer、同樣不適合 XML

**✅ 改用 `skill_mode` 節點**：
```yaml
- name: 抓 RSS
  skill_mode: true
  batch: |
    抓取 RSS feed: https://www.theverge.com/rss/index.xml
    用 feedparser 解析、產出最新 10 篇的標題、連結、發布時間、摘要、
    寫到 ai_output/<workflow>/rss_items.md
  timeout: 60
```

**判斷原則**：
- URL 是 RSS / Atom XML → skill 節點 + feedparser
- URL 是 HTML 頁面（首頁、列表頁、文章頁）→ web_crawler 節點
- 不確定？反問使用者「這是 RSS 還是一般網頁？」

**feedparser 已預裝在沙盒**(skill_packages.txt 含)、skill 節點直接用。

# 啟動既有 Python 專案的特別規則（重要）

當使用者描述含「我有 Python 專案 / GUI / main.py / 既有專案 / 啟動我的程式」這類用語時：

1. **沒給專案路徑 → 必須先反問**：「你的 Python 專案放在哪個資料夾？」
   - **同時主動告知標準位置**：「建議放在本專案根目錄底下的 `external_projects/<你的專案名>/`，AI 技能才能讀寫該專案內容並修改。」
   - 確認路徑後再進 Plan、不要先猜路徑跳到 Emit
2. **GUI / 含 `input()` 互動 → 用 skill 節點而非 script 節點**：
   - script 節點直接 subprocess 跑 GUI 會被 input() 阻塞（直到 timeout），體驗很差
   - skill 節點會自動 read_file 源碼、找出互動點、改寫成 CLI 參數版本再跑
   - Plan 中要明說「AI 技能會先讀 main.py、把 GUI / input() 改成 CLI 版本」
3. **若使用者明確說「不要改原檔」**：skill 走 `readonly: true` 並且生成 `main_cli.py` 副本；否則預設會直接 in-place 改寫 main.py（並 git diff 可追溯）。

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


def _build_pipeline_system_prompt(channel: str = "desktop") -> str:
    """組裝 AI 助手 system prompt：底稿 + 動態注入已安裝的 Agent Skills + Outlook 模板清單。

    channel:
      - "telegram"：TG bot 通道(手機 / 遠端)、會包含 ad-hoc 派子代理工具與教學
      - "desktop" (預設):桌面 :3005 chat、聚焦在畫板 / workflow 規劃，
        把 TG_ONLY 標記之間的 subagent 章節剝掉、節省 token + 避免 LLM 誤呼
        不存在的工具。
    """
    base = _PIPELINE_SYSTEM_BASE
    if channel != "telegram":
        # 把 <!--TG_ONLY_BEGIN--> ... <!--TG_ONLY_END--> 之間整塊拿掉(含 marker)
        import re as _re
        base = _re.sub(
            r"<!--TG_ONLY_BEGIN-->.*?<!--TG_ONLY_END-->\s*",
            "",
            base,
            flags=_re.DOTALL,
        )
    parts = [base]
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
    # ── 今日日期(讓 web_search query 能用最新年份)──────────────────
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            now = datetime.now(ZoneInfo("Asia/Taipei"))
        except Exception:
            now = datetime.now()
        ymd = now.strftime("%Y-%m-%d")
        weekday_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][now.weekday()]
        parts.append(
            f"\n\n## 📅 今日日期\n\n"
            f"**現在是 {ymd}（{weekday_zh}）、{now.strftime('%H:%M')}**(Asia/Taipei)\n\n"
            f"**重要**：使用 `web_search` 時、query 含年份請用「{now.year}」"
            f"或「{now.year - 1}-{now.year}」、不要用陳舊年份(例：'2023 best XX')— "
            f"那會搜到過期資訊。"
        )
    except Exception:
        pass
    # ── In-flight 子代理 digest(Phase 2:每次 chat turn 自動匯報) ─────
    # 只有 TG 通道才注入 — 桌面 AI 助手不派子代理、看到 digest 也沒用、徒增 token
    # 規範詳見 system prompt 內「In-flight 子代理狀態主動匯報」章節(同樣只有 TG 看得到)
    try:
        from chat_tools import _chat_subagents
        import time as _t
        snapshot = list(_chat_subagents.items())  # avoid concurrent mutation
        if snapshot:
            now_ts = _t.time()
            running: list[tuple[str, dict, int]] = []
            recent_done: list[tuple[str, dict, int]] = []
            for tid, info in snapshot:
                state = info.get("state")
                if state == "running":
                    started = info.get("started_at", now_ts)
                    running.append((tid, info, int(now_ts - started)))
                elif state in ("completed", "failed"):
                    ended = info.get("ended_at") or 0
                    ago = int(now_ts - ended)
                    if 0 <= ago <= 60:  # 60s 內才算 "剛完成"、要主動提
                        recent_done.append((tid, info, ago))
            if running or recent_done:
                lines = [
                    "",
                    "## 📡 In-flight 子代理（主動匯報；超過 60s 完成的不要重複提）",
                    "",
                ]
                for tid, info, elapsed in running:
                    role = info.get("role", "?")
                    preview = (info.get("task") or "")[:80].replace("\n", " ")
                    lines.append(f"- `{tid}` ({role}) **running** {elapsed}s — {preview}")
                for tid, info, ago in recent_done:
                    state = info.get("state")
                    role = info.get("role", "?")
                    r = info.get("result") or {}
                    success_mark = "✅" if r.get("success") else "❌"
                    summary = (r.get("summary") or "").replace("\n", " ")[:140]
                    wd = info.get("working_dir", "")
                    lines.append(
                        f"- `{tid}` ({role}) **{state}** {ago}s ago {success_mark} — "
                        f"{summary} (in `{wd}`)"
                    )
                parts.append("\n".join(lines))
    except Exception:
        pass
    return "".join(parts)


class PipelineChatRequest(BaseModel):
    messages: list[dict]
    workflow_id: Optional[str] = None  # 若帶，會把該工作流當前 canvas/YAML 注入 system prompt，
                                       # 讓 AI 能理解「在現有工作流加步驟」的增量需求
    extra_system: Optional[str] = None  # 額外 system 段落、會 append 到 system prompt 末尾。
                                        # TG 通道用來注入「目前狀態 digest」(最近工作流/run/log 摘要)


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


def _extract_text_content(raw) -> str:
    """從 LLM response.content 抽出純文字
    （Gemini/Gemma 可能回傳 list of content blocks 含 thinking + text）。"""
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(raw) if raw is not None else ""


# ── LaTeX → Unicode 清洗（與 frontend cleanLatexInChat 邏輯一致）─────────────
# LLM 偶爾違反「不要用 LaTeX」規則、輸出 $\rightarrow$ 等。
# TG 沒像 frontend 有 ReactMarkdown 後處理、會直接顯示字面亂碼。
# 故在 backend 統一清洗、桌面 / TG 都拿到乾淨輸出。
_LATEX_CMD_TO_UNICODE: dict[str, str] = {
    "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "to": "→", "gets": "←", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "uparrow": "↑", "downarrow": "↓", "updownarrow": "↕",
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "cdot": "·", "cdots": "⋯", "ldots": "…", "dots": "…",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "tau": "τ",
    "phi": "φ", "omega": "ω",
    "infty": "∞", "forall": "∀", "exists": "∃", "in": "∈", "notin": "∉",
    "subset": "⊂", "supset": "⊃", "cup": "∪", "cap": "∩",
    "text": "", "mathrm": "", "mathbf": "", "mathit": "",
}


def _clean_latex(text: str) -> str:
    """把常見 LaTeX 命令換成 Unicode、剝掉錢字號。
    沒覆蓋的命令至少把 \\ 跟 $ 拿掉、不影響可讀性。
    跟 frontend cleanLatexInChat 行為一致。"""
    if not text or ("$" not in text and "\\" not in text):
        return text
    import re as _re

    def _replace_cmd(match):
        cmd = match.group(1)
        return _LATEX_CMD_TO_UNICODE.get(cmd, cmd)

    # 1. inline math $...$ → 內容(剝錢字號 + 解析命令)
    def _replace_inline_math(m):
        body = m.group(1)
        return _re.sub(r"\\([a-zA-Z]+)", _replace_cmd, body).strip()
    out = _re.sub(r"\$([^\$\n]+?)\$", _replace_inline_math, text)

    # 2. $ 外裸命令(例 \rightarrow 沒包 $)
    def _replace_bare_cmd(m):
        cmd = m.group(1)
        if cmd in _LATEX_CMD_TO_UNICODE:
            return _LATEX_CMD_TO_UNICODE[cmd]
        return m.group(0)  # 不認識就保留原樣
    out = _re.sub(r"\\([a-zA-Z]+)", _replace_bare_cmd, out)

    return out


# 單次 chat turn 內最多幾輪 tool calling iteration（防無限迴圈、防 token 爆）
_CHAT_MAX_TOOL_ITERATIONS = 5


def _friendly_llm_error(e: Exception) -> tuple[int, str]:
    """把 LLM build / invoke 階段的常見 exception 翻譯成繁中友善訊息。
    回傳 (HTTP status code, 訊息)。

    這是給 chat / agent 用的 error wrapper、讓桌面 + TG 都看到一致的原因說明
    （而非 raw stack trace）。
    """
    name = type(e).__name__
    msg = str(e) or ""
    msg_lc = msg.lower()

    # 1. 未設定 provider / model
    if isinstance(e, KeyError) and "provider" in msg.lower():
        return 400, "未設定 LLM。請到設定頁選 provider（Groq / Gemini / Ollama / OpenRouter）+ model"
    if isinstance(e, ValueError) and msg_lc.startswith("unknown provider"):
        return 400, f"LLM provider 設定有誤：{msg}。請到設定頁重選 provider"

    # 2. API key 缺
    if "api_key" in msg_lc and ("missing" in msg_lc or "not provided" in msg_lc or "required" in msg_lc):
        return 400, f"LLM API Key 缺：{msg[:200]}。到 backend/.env 填、或設定頁設定"
    if "googleapierror" in msg_lc and "api key" in msg_lc:
        return 400, "Gemini API Key 無效或未設定、請到 backend/.env 填 GEMINI_API_KEY"
    if name == "AuthenticationError" or "401" in msg or "unauthorized" in msg_lc or "invalid api key" in msg_lc:
        return 401, f"LLM API Key 無效：{msg[:200]}。請更新 .env 或設定頁的 key"

    # 3. 配額 / rate limit
    if "rate" in msg_lc and "limit" in msg_lc:
        return 429, f"LLM API 觸發 rate limit、請稍後再試或換 model：{msg[:200]}"
    if "quota" in msg_lc or "exceeded" in msg_lc or "429" in msg:
        return 429, f"LLM API 配額用完、請換 model 或等配額重置：{msg[:200]}"

    # 4. 網路 / 連線
    if name in ("ConnectionError", "ConnectError", "ConnectTimeout"):
        return 503, f"連不上 LLM API（{name}）：{msg[:200]}。檢查網路或 API endpoint 設定"
    if ("timeout" in msg_lc or "timed out" in msg_lc
        or name in ("ReadTimeout", "Timeout", "TimeoutError")):
        return 504, f"LLM API 逾時：{msg[:200]}。可能網路慢或 model 太大、可換更快的 model 試試"
    if name == "APIConnectionError":
        return 503, f"無法連到 LLM API：{msg[:200]}"

    # 5. 模型不認得
    if "model" in msg_lc and ("not found" in msg_lc or "does not exist" in msg_lc or "404" in msg):
        return 400, f"LLM model 名稱無效：{msg[:200]}。請到設定頁挑現有 model"

    # 6. 未指定提供商支援
    if isinstance(e, ImportError):
        if "groq" in msg_lc or "gemini" in msg_lc or "ollama" in msg_lc or "openai" in msg_lc:
            return 500, f"缺套件無法用此 provider：{msg[:200]}。pip install 對應套件再試"

    # 7. 其他 — 給原始 type + 前 200 字
    return 500, f"LLM 呼叫失敗（{name}）：{msg[:300]}"


async def _chat_agent_loop(
    req: PipelineChatRequest,
    on_tool_event=None,
):
    """AI 助手 agent loop 核心。

    on_tool_event:Optional[Awaitable callable]
        簽名:async def cb(phase: "before" | "after", tool_call: dict, result: str | None)
        - phase="before":在 tool 呼叫前 fire(result=None)
        - phase="after":tool 回傳後 fire(result=tool 回值字串)
        TG handler 用這個推進度訊息。

    跟 pipeline_chat 一致回 dict {reply, has_yaml, yaml_content, yaml_error}。
    """
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
    from llm_factory import build_llm
    from chat_tools import CHAT_TOOLS, CHAT_TOOLS_BY_NAME
    import re
    import logging as _log

    # build_llm 失敗:provider/key 沒設、套件缺 → 翻譯成友善訊息
    try:
        llm = build_llm(temperature=0.3)
    except Exception as e:
        _log.warning(f"[/pipeline/chat] build_llm 失敗:{type(e).__name__}: {e}")
        sc, friendly = _friendly_llm_error(e)
        raise HTTPException(status_code=sc, detail=friendly)
    # 通道偵測：只 _chat_agent_loop 有 on_tool_event 參數(TG handler 才會傳)、
    # 桌面 chat 走 pipeline_chat_stream / pipeline_chat 都不傳 → 預設 desktop。
    # TG 通道才放 dispatch_subagent_async / check_subagent_status 兩個 ad-hoc 子代理工具
    # + 帶 in-flight digest 教學區塊。
    _channel = "telegram" if on_tool_event is not None else "desktop"
    system_prompt = _build_pipeline_system_prompt(channel=_channel)
    if req.workflow_id:
        system_prompt += _workflow_state_block(req.workflow_id)
    if req.extra_system:
        system_prompt += "\n\n" + req.extra_system

    # ── 嘗試 bind_tools；失敗就退到舊單輪 ────────────────────────
    tools_enabled = True
    # desktop 不做 ad-hoc 子代理(那是 TG 專用)、把整套 subagent admin tools 拿掉
    # 省 6 個 tool schema(每個 ~400-600 tok)、desktop 每輪節省 ~2-3K token
    _TG_ONLY_TOOLS = {
        "dispatch_subagent_async", "check_subagent_status",
        "read_subagent_file", "send_subagent_file_to_tg",
        "cancel_subagent_task", "read_help_doc",
    }
    _active_tools = CHAT_TOOLS if _channel == "telegram" else [
        t for t in CHAT_TOOLS if t.name not in _TG_ONLY_TOOLS
    ]
    try:
        llm_with_tools = llm.bind_tools(_active_tools)
    except Exception as e:
        _log.warning(f"[/pipeline/chat] bind_tools 失敗、退到單輪：{e}")
        llm_with_tools = llm
        tools_enabled = False

    lc_messages: list = [SystemMessage(content=system_prompt)]
    # 只取最近 _CHAT_HISTORY_CAP 則訊息送進 LLM，避免對話太長 token 爆炸
    recent = req.messages[-_CHAT_HISTORY_CAP:] if len(req.messages) > _CHAT_HISTORY_CAP else req.messages
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))

    # ── Agent loop：最多 _CHAT_MAX_TOOL_ITERATIONS 輪 ─────────────
    # 用 ainvoke (async) 讓 event loop 不被 LLM call 阻塞、
    # 上層 (TG handler) 才能在等待時跑 typing keepalive
    final_response = None
    for iteration in range(_CHAT_MAX_TOOL_ITERATIONS):
        try:
            if hasattr(llm_with_tools, "ainvoke"):
                response = await llm_with_tools.ainvoke(lc_messages)
            else:
                response = llm_with_tools.invoke(lc_messages)
        except Exception as e:
            _log.warning(f"[/pipeline/chat] LLM invoke 失敗(iter {iteration}):{type(e).__name__}: {e}")
            sc, friendly = _friendly_llm_error(e)
            raise HTTPException(status_code=sc, detail=friendly)
        lc_messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls or not tools_enabled:
            final_response = response
            break
        # 跑每個 tool call、把結果加進對話
        for tc in tool_calls:
            tname = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            targs = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "tc")
            # 統一成 dict 形式給 callback、callback 失敗不影響主流程
            tc_dict = {"name": tname, "args": targs or {}, "id": tid}
            if on_tool_event:
                try:
                    await on_tool_event("before", tc_dict, None)
                except Exception as cb_e:
                    _log.warning(f"[/pipeline/chat] on_tool_event before 失敗(忽略):{cb_e}")
            tool_obj = CHAT_TOOLS_BY_NAME.get(tname)
            if not tool_obj:
                tool_result = f"[Unknown tool: {tname}]"
            else:
                try:
                    # 用 ainvoke、async tool(如 start_workflow)才能正確 await
                    # sync tool 也有 ainvoke、會自動跑 sync 邏輯、不會壞
                    if hasattr(tool_obj, "ainvoke"):
                        tool_result = await tool_obj.ainvoke(targs or {})
                    else:
                        tool_result = tool_obj.invoke(targs or {})
                except Exception as e:
                    tool_result = f"[Tool error: {type(e).__name__}: {str(e)[:300]}]"
            # Cap 單個 tool result（避免 token 爆）
            if isinstance(tool_result, str) and len(tool_result) > 16000:
                tool_result = tool_result[:16000] + f"\n... (回傳超過 16000 字、後面截掉)"
            lc_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tid))
            _log.info(f"[/pipeline/chat] tool={tname} args={targs} result_len={len(str(tool_result))}")
            if on_tool_event:
                try:
                    await on_tool_event("after", tc_dict, str(tool_result))
                except Exception as cb_e:
                    _log.warning(f"[/pipeline/chat] on_tool_event after 失敗(忽略):{cb_e}")
    else:
        # for-else：迴圈正常結束（沒 break）→ 達上限沒收到純文字回覆
        _log.warning(f"[/pipeline/chat] 達 tool iteration 上限 {_CHAT_MAX_TOOL_ITERATIONS}、強制結束")
        # 最後再 invoke 一次叫 LLM 給純文字總結
        try:
            lc_messages.append(HumanMessage(content="(系統:已達工具呼叫上限、請現在用純文字回答使用者、不要再呼叫工具)"))
            if hasattr(llm_with_tools, "ainvoke"):
                final_response = await llm_with_tools.ainvoke(lc_messages)
            else:
                final_response = llm_with_tools.invoke(lc_messages)
        except Exception:
            final_response = lc_messages[-1] if lc_messages else None

    raw = final_response.content if final_response else ""
    content = _extract_text_content(raw)
    # LaTeX 清洗：LLM 偶發違規寫 $\rightarrow$,後端統一清理(桌面 + TG 都受惠)
    content = _clean_latex(content)

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


@app.post("/pipeline/chat")
async def pipeline_chat(req: PipelineChatRequest):
    """AI 助手聊天 endpoint。包薄殼、實際邏輯在 _chat_agent_loop。

    LLM 看到 chat_tools 裡定義的 7 個 tool(read + write + send_file + web_search)、
    自己決定何時 call。bind_tools 失敗(model 不支援)時退到單輪。

    on_tool_event callback 留給內部呼叫者用(例 TG handler 推進度訊息)、
    HTTP 端點不傳 callback。
    """
    return await _chat_agent_loop(req)


async def _chat_agent_stream(req: "PipelineChatRequest"):
    """串流版 chat agent loop。yield NDJSON 事件供 SSE 端點。

    事件種類:
    - {"type": "token", "text": "..."}            # LLM 串流的文字片段
    - {"type": "tool_start", "name": "X", "args": {...}}
    - {"type": "tool_end", "name": "X", "result_preview": "...(前 200 字)"}
    - {"type": "done", "reply": "...", "has_yaml": bool, "yaml_content": str|None, "yaml_error": str|None}
    - {"type": "error", "detail": "..."}          # 任何階段失敗
    """
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
    from langchain_core.messages import AIMessageChunk
    from llm_factory import build_llm
    from chat_tools import CHAT_TOOLS, CHAT_TOOLS_BY_NAME
    import re
    import logging as _log

    # build_llm 失敗 → emit error event
    try:
        llm = build_llm(temperature=0.3)
    except Exception as e:
        sc, friendly = _friendly_llm_error(e)
        yield {"type": "error", "status_code": sc, "detail": friendly}
        return

    # SSE stream 端點(/pipeline/chat/stream)是給桌面 chat 用、永遠 desktop 通道。
    # TG handler 不走 stream、直接 await _chat_agent_loop。
    system_prompt = _build_pipeline_system_prompt(channel="desktop")
    if req.workflow_id:
        system_prompt += _workflow_state_block(req.workflow_id)
    if req.extra_system:
        system_prompt += "\n\n" + req.extra_system

    tools_enabled = True
    # stream 端點永遠 desktop、把整套 subagent admin tools 拿掉(同 _chat_agent_loop)
    _TG_ONLY_TOOLS = {
        "dispatch_subagent_async", "check_subagent_status",
        "read_subagent_file", "send_subagent_file_to_tg",
        "cancel_subagent_task", "read_help_doc",
    }
    _active_tools = [t for t in CHAT_TOOLS if t.name not in _TG_ONLY_TOOLS]
    try:
        llm_with_tools = llm.bind_tools(_active_tools)
    except Exception as e:
        _log.warning(f"[/pipeline/chat/stream] bind_tools 失敗、退到單輪:{e}")
        llm_with_tools = llm
        tools_enabled = False

    lc_messages: list = [SystemMessage(content=system_prompt)]
    recent = req.messages[-_CHAT_HISTORY_CAP:] if len(req.messages) > _CHAT_HISTORY_CAP else req.messages
    for m in recent:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))

    full_content_parts: list[str] = []  # 累積整輪 chat 看到的純文字 content

    for iteration in range(_CHAT_MAX_TOOL_ITERATIONS):
        # 用 astream 拿 chunks(token + tool calls 都會以 chunk 形式來)
        accumulated: AIMessageChunk | None = None
        try:
            async for chunk in llm_with_tools.astream(lc_messages):
                # chunk.content:這次的 token(text)
                ctext = chunk.content if isinstance(chunk.content, str) else ""
                if not ctext and isinstance(chunk.content, list):
                    # Gemini list-of-blocks 形式、抽 text block
                    parts = []
                    for b in chunk.content:
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                            parts.append(b["text"])
                    ctext = "".join(parts)
                if ctext:
                    full_content_parts.append(ctext)
                    yield {"type": "token", "text": ctext}
                # 累積整體 chunk(用 + 操作符;AIMessageChunk 支援 merge)
                accumulated = chunk if accumulated is None else accumulated + chunk
        except Exception as e:
            _log.warning(f"[/pipeline/chat/stream] astream 失敗(iter {iteration}):{type(e).__name__}: {e}")
            sc, friendly = _friendly_llm_error(e)
            yield {"type": "error", "status_code": sc, "detail": friendly}
            return

        if accumulated is None:
            yield {"type": "error", "detail": "LLM 沒回任何 chunk"}
            return

        # 把 accumulated 轉成 AIMessage 加進 messages、看有沒有 tool_calls
        # AIMessageChunk 有 .tool_calls 屬性、merge 後的 chunk 也帶 tool_calls 完整資訊
        lc_messages.append(accumulated)
        tool_calls = getattr(accumulated, "tool_calls", None) or []

        if not tool_calls or not tools_enabled:
            # 這輪沒 tool、結束 agent loop
            break

        # 執行 tools
        for tc in tool_calls:
            tname = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            targs = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "tc")
            yield {"type": "tool_start", "name": tname, "args": targs or {}}
            tool_obj = CHAT_TOOLS_BY_NAME.get(tname)
            if not tool_obj:
                tool_result = f"[Unknown tool: {tname}]"
            else:
                try:
                    if hasattr(tool_obj, "ainvoke"):
                        tool_result = await tool_obj.ainvoke(targs or {})
                    else:
                        tool_result = tool_obj.invoke(targs or {})
                except Exception as e:
                    tool_result = f"[Tool error: {type(e).__name__}: {str(e)[:300]}]"
            tool_result_str = str(tool_result)
            if len(tool_result_str) > 16000:
                tool_result_str = tool_result_str[:16000] + "\n... (回傳超過 16000 字、後面截掉)"
            lc_messages.append(ToolMessage(content=tool_result_str, tool_call_id=tid))
            preview = tool_result_str[:200]
            yield {"type": "tool_end", "name": tname, "result_preview": preview}
    else:
        # 達上限、再 invoke 一次叫 LLM 給純文字
        yield {"type": "tool_end", "name": "(達工具呼叫上限)", "result_preview": "強制結束"}
        try:
            lc_messages.append(HumanMessage(content="(系統:已達工具呼叫上限、請現在用純文字回答使用者、不要再呼叫工具)"))
            async for chunk in llm_with_tools.astream(lc_messages):
                ctext = chunk.content if isinstance(chunk.content, str) else ""
                if ctext:
                    full_content_parts.append(ctext)
                    yield {"type": "token", "text": ctext}
        except Exception as e:
            _log.warning(f"[/pipeline/chat/stream] 強制結束 LLM call 失敗:{e}")

    # 整合最終回覆 + LaTeX 清洗 + YAML 解析
    content = _clean_latex("".join(full_content_parts))
    has_yaml = "YAML_READY" in content
    yaml_content = None
    yaml_error = None
    if has_yaml:
        match = re.search(r"```yaml\n([\s\S]+?)```", content)
        if match:
            yaml_content = match.group(1).strip()
            try:
                import yaml as _yaml
                from pipeline.models import PipelineConfig
                parsed = _yaml.safe_load(yaml_content) or {}
                raw_cfg = parsed.get("pipeline", parsed)
                PipelineConfig.from_dict({k: v for k, v in raw_cfg.items() if not str(k).startswith("_")})
            except Exception as e:
                yaml_error = f"YAML 語法/結構錯誤:{type(e).__name__}:{str(e)[:300]}"

    yield {
        "type": "done",
        "reply": content,
        "has_yaml": has_yaml,
        "yaml_content": yaml_content,
        "yaml_error": yaml_error,
    }


@app.post("/pipeline/chat/stream")
async def pipeline_chat_stream(req: PipelineChatRequest):
    """AI 助手聊天 SSE 串流端點。回 NDJSON(每行一個 JSON event)。

    前端可用 fetch streaming + ReadableStream reader 一行一行讀。
    比 /pipeline/chat 多:即時 token / tool_start / tool_end 事件、體驗類似 ChatGPT。
    """
    from fastapi.responses import StreamingResponse
    import json as _json

    async def _ndjson_gen():
        try:
            async for ev in _chat_agent_stream(req):
                yield _json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:
            yield _json.dumps({"type": "error", "detail": f"stream 內部錯誤:{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _ndjson_gen(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 防 nginx / proxy buffer
        },
    )


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
             "stdout_tail": s.stdout_tail, "stderr_tail": s.stderr_tail,
             "actual_output_path": getattr(s, 'actual_output_path', '') or '',
             "token_usage": getattr(s, 'token_usage', {}) or {},
             "tool_calls": getattr(s, 'tool_calls', []) or [],
             "started_at": getattr(s, 'started_at', '') or '',
             "ended_at": getattr(s, 'ended_at', '') or ''}
            for s in r.step_results
        ],
        "config_dict": r.config_dict,
        "log_path": r.log_path,
        "pending_recipes": getattr(r, 'pending_recipes', []) or [],
        "awaiting_type": getattr(r, 'awaiting_type', '') or '',
        "awaiting_message": getattr(r, 'awaiting_message', '') or '',
        "awaiting_suggestion": getattr(r, 'awaiting_suggestion', '') or '',
        "input_params": getattr(r, 'input_params', None) or {},
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
