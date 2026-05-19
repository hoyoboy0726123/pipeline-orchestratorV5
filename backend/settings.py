"""使用者可調設定（模型選擇等）— 持久化到 JSON 檔案。"""
import json
import threading
from pathlib import Path
from typing import Optional

from config import OUTPUT_BASE_PATH, GROQ_MODEL_MAIN

_SETTINGS_PATH = OUTPUT_BASE_PATH / "pipeline_settings.json"
_lock = threading.Lock()

# 預設：沿用環境變數 / config.py 預設的 Groq 模型
_DEFAULT = {
    # ── 主模型(預設、所有節點不另設 llm_role 時用這個)──
    "provider": "groq",           # "groq" | "ollama" | "gemini" | "openai" | "anthropic"
    "model": GROQ_MODEL_MAIN,      # e.g. "meta-llama/llama-4-scout-17b-16e-instruct" or "qwen3:8b"
    "ollama_base_url": "http://localhost:11434",
    "ollama_thinking": "off",      # "auto" | "on" | "off" — 預設關閉，避免 thinking 模式 rambling 卡住
    "ollama_num_ctx": 16384,       # Ollama context window tokens（僅 Ollama）
    "gemini_thinking": "off",      # "off" | "auto" | "low" | "medium" | "high"
    "anthropic_thinking": "off",   # "off" | "on" — Claude Opus 4 系列 extended thinking
    # ── 副模型(節點可在 panel 選 llm_role: secondary 切到這個)──
    # 預設全空、=不啟用副模型;任一 node 設 llm_role=secondary 而副模型未設 → fallback 主
    "secondary_provider": "",
    "secondary_model": "",
    "secondary_ollama_thinking": "off",
    "secondary_ollama_num_ctx": 16384,
    "secondary_gemini_thinking": "off",
    "secondary_anthropic_thinking": "off",
    # 通知設定
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # Telegram 遠端遙控：開啟後 bot 會處理你發的指令訊息（/menu 列工作流、按按鈕啟動）
    # 預設 OFF — 安全起見、要使用時手動開
    # 開啟前提：telegram_bot_token + telegram_chat_id 都要填好
    "telegram_remote_control": False,
    "line_notify_token": "",       # LINE Notify（預留）
    # Skill 沙盒（V4）：run_python / run_shell 執行位置
    #   "host"       — Windows 原生 subprocess（快、跟 V2 一樣）
    #   "wsl_docker" — 透過 WSL 內的 pipeline-sandbox-v4 容器執行（隔離、需先跑 sandbox/setup_sandbox.bat）
    "skill_sandbox_mode": "host",
    # Skill 檔案目錄:留空 = 預設 ~/.agents/skills/;可填自訂絕對路徑,
    # 讓專案使用者把 skill 檔放在自己選的位置。亦可用環境變數 SKILLS_DIR 覆蓋。
    "skills_dir": "",
    # 網路搜尋（Tavily）：skill agent 需要即時 / 外部資訊時使用
    # key 可先填但預設關閉，避免誤觸扣費
    "tavily_api_key": "",
    "web_search_enabled": False,
    # 完整內容模式：ON 時 Tavily 直接回文章原文（Agent 不用寫爬蟲）
    # 代價：一次回傳 ~15000 字，需要雲端大 context 模型；本地 Ollama 8B 小 context 會爆
    "web_search_full_content_default": False,
}

_cache: Optional[dict] = None


def _load_from_disk() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(_DEFAULT)
            merged.update({k: v for k, v in data.items() if k in _DEFAULT})
            return merged
        except Exception:
            pass
    return dict(_DEFAULT)


def get_settings() -> dict:
    """取得當前設定（含快取）。"""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_disk()
        return dict(_cache)


def update_settings(
    provider: str,
    model: str,
    ollama_base_url: Optional[str] = None,
    ollama_thinking: Optional[str] = None,
    ollama_num_ctx: Optional[int] = None,
    gemini_thinking: Optional[str] = None,
    anthropic_thinking: Optional[str] = None,
    # ── 副模型(選填、空字串 = 不啟用)──
    secondary_provider: Optional[str] = None,
    secondary_model: Optional[str] = None,
    secondary_ollama_thinking: Optional[str] = None,
    secondary_ollama_num_ctx: Optional[int] = None,
    secondary_gemini_thinking: Optional[str] = None,
    secondary_anthropic_thinking: Optional[str] = None,
) -> dict:
    """更新並寫入磁碟。"""
    global _cache
    if provider not in ("groq", "ollama", "gemini", "openai", "anthropic"):
        raise ValueError(f"unsupported provider: {provider}")
    if not model or not isinstance(model, str):
        raise ValueError("model is required")
    thinking = (ollama_thinking or "off").strip()
    if thinking not in ("auto", "on", "off"):
        raise ValueError(f"invalid ollama_thinking: {thinking}")
    gem_thinking = (gemini_thinking or "off").strip()
    if gem_thinking not in ("off", "auto", "low", "medium", "high"):
        raise ValueError(f"invalid gemini_thinking: {gem_thinking}")
    ant_thinking = (anthropic_thinking or "off").strip()
    if ant_thinking not in ("off", "on"):
        raise ValueError(f"invalid anthropic_thinking: {ant_thinking}")
    num_ctx = ollama_num_ctx if ollama_num_ctx is not None else _DEFAULT["ollama_num_ctx"]
    if not isinstance(num_ctx, int) or num_ctx < 2048 or num_ctx > 262144:
        raise ValueError(f"invalid ollama_num_ctx: {num_ctx}(需介於 2048~262144)")

    # ── Secondary 驗證(可全空表示停用)──
    sec_provider = (secondary_provider or "").strip()
    sec_model = (secondary_model or "").strip()
    if sec_provider and sec_provider not in ("groq", "ollama", "gemini", "openai", "anthropic"):
        raise ValueError(f"unsupported secondary_provider: {sec_provider}")
    if sec_provider and not sec_model:
        raise ValueError("secondary_model is required when secondary_provider is set")
    sec_oll_thinking = (secondary_ollama_thinking or "off").strip()
    if sec_oll_thinking not in ("auto", "on", "off"):
        raise ValueError(f"invalid secondary_ollama_thinking: {sec_oll_thinking}")
    sec_gem_thinking = (secondary_gemini_thinking or "off").strip()
    if sec_gem_thinking not in ("off", "auto", "low", "medium", "high"):
        raise ValueError(f"invalid secondary_gemini_thinking: {sec_gem_thinking}")
    sec_ant_thinking = (secondary_anthropic_thinking or "off").strip()
    if sec_ant_thinking not in ("off", "on"):
        raise ValueError(f"invalid secondary_anthropic_thinking: {sec_ant_thinking}")
    sec_num_ctx = secondary_ollama_num_ctx if secondary_ollama_num_ctx is not None else _DEFAULT["secondary_ollama_num_ctx"]
    if not isinstance(sec_num_ctx, int) or sec_num_ctx < 2048 or sec_num_ctx > 262144:
        raise ValueError(f"invalid secondary_ollama_num_ctx: {sec_num_ctx}")

    with _lock:
        # 先讀取現有設定(保留通知等其他欄位)
        existing = _cache if _cache else _load_from_disk()
        existing.update({
            "provider": provider,
            "model": model.strip(),
            "ollama_base_url": (ollama_base_url or _DEFAULT["ollama_base_url"]).strip(),
            "ollama_thinking": thinking,
            "ollama_num_ctx": num_ctx,
            "gemini_thinking": gem_thinking,
            "anthropic_thinking": ant_thinking,
            "secondary_provider": sec_provider,
            "secondary_model": sec_model,
            "secondary_ollama_thinking": sec_oll_thinking,
            "secondary_ollama_num_ctx": sec_num_ctx,
            "secondary_gemini_thinking": sec_gem_thinking,
            "secondary_anthropic_thinking": sec_ant_thinking,
        })
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        _cache = existing
    return dict(existing)


def settings_signature() -> str:
    """回傳一個代表當前設定的簡易字串，用於 LLM 快取失效判斷。"""
    s = get_settings()
    return f"{s['provider']}::{s['model']}::{s['ollama_base_url']}::{s.get('ollama_thinking', 'off')}::{s.get('ollama_num_ctx', 16384)}"


# ── Sandbox mode（獨立 setter，不混進 model 更新流程） ─────────────
def set_skill_sandbox_mode(mode: str) -> dict:
    """切換 skill 執行模式。mode ∈ {"host", "wsl_docker"}。"""
    global _cache
    mode = (mode or "host").strip()
    if mode not in ("host", "wsl_docker"):
        raise ValueError(f"invalid skill_sandbox_mode: {mode}")
    with _lock:
        existing = _cache if _cache else _load_from_disk()
        existing["skill_sandbox_mode"] = mode
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        _cache = existing
    return dict(existing)


# ── Skill 檔案目錄（獨立 setter） ──────────────────────────────────
def set_skills_dir(path: str) -> dict:
    """設定 Skill 檔案目錄。空字串 = 用預設 ~/.agents/skills/。"""
    global _cache
    path = (path or "").strip()
    with _lock:
        existing = _cache if _cache else _load_from_disk()
        existing["skills_dir"] = path
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        _cache = existing
    return dict(existing)
