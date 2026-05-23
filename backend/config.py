import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL_MAIN   = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL_MAIN = os.getenv("GEMINI_MODEL", "gemma-4-31b-it")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

TIMEZONE           = os.getenv("TIMEZONE", "Asia/Taipei")
# OUTPUT_BASE_PATH 解析規則:
#   1. .env 顯式設絕對路徑 → 用該路徑
#   2. .env 設相對路徑 → 視為相對 repo_root(不是 backend cwd!)、解成 repo_root/<rel>
#   3. .env 沒設 → 預設 repo_root/ai_output(對齊 pipeline/runner.py 內 _workflow_output_dir
#      的 hardcoded 計算結果、避免 workflow runner 跟 chat_tools 兩條路徑解到不同地方)
# 強制 absolute 確保之後傳到 sandbox docker exec -w 不會炸「Cwd must be an absolute path」。
_REPO_ROOT = Path(__file__).parent.parent.resolve()   # backend/config.py → backend/ → repo_root/
_OUTPUT_ENV = os.getenv("OUTPUT_BASE_PATH", "").strip()
if _OUTPUT_ENV:
    _p = Path(_OUTPUT_ENV).expanduser()
    OUTPUT_BASE_PATH = _p if _p.is_absolute() else (_REPO_ROOT / _p).resolve()
else:
    OUTPUT_BASE_PATH = (_REPO_ROOT / "ai_output").resolve()
SCHEDULER_DB_PATH  = OUTPUT_BASE_PATH / "pipeline_scheduler.db"
PIPELINE_DIR       = Path(os.getenv("PIPELINE_DIR", "~/pipelines")).expanduser()

OUTPUT_BASE_PATH.mkdir(parents=True, exist_ok=True)
PIPELINE_DIR.mkdir(parents=True, exist_ok=True)


# ── Auto-migration:舊 backend/ai_output → 新 OUTPUT_BASE_PATH(僅一次) ──
# 背景:V5 內部統一 OUTPUT_BASE_PATH 之前(commit 53ed100 / 2026-05-24 前)、
# 設定 .env OUTPUT_BASE_PATH=./ai_output 的 user、實際 DB / 產物存到 backend/ai_output/。
# 統一後新預設是 repo_root/ai_output/、舊資料變孤兒、user 看到 workflow 全消失。
# 這層在啟動時主動偵測 + 搬遷,搬一次就放 .migrated 旗標、不會重跑。
def _auto_migrate_legacy_ai_output():
    legacy = (_REPO_ROOT / "backend" / "ai_output").resolve()
    new = OUTPUT_BASE_PATH
    if legacy == new:
        return  # 路徑相同、不必搬
    flag = new / ".migrated_from_backend"
    if flag.exists():
        return  # 已搬過
    if not legacy.exists() or not legacy.is_dir():
        return  # 沒舊資料、不必搬
    # 舊位置有 pipeline.db 才視為「真有 user 資料」(避免 backend/ai_output 是空殼仍誤搬)
    if not (legacy / "pipeline.db").exists():
        return
    # 新位置如果已有 pipeline.db 視為 user 已手動處理過、跳過避免覆寫
    if (new / "pipeline.db").exists():
        return
    import shutil as _sh
    print(f"\n{'='*60}")
    print(f"[V5 auto-migrate] 偵測到舊 ai_output 在 {legacy}")
    print(f"  搬遷到新位置 {new} ...")
    print(f"{'='*60}\n")
    try:
        for item in legacy.iterdir():
            target = new / item.name
            if target.exists():
                continue   # 同名跳過(優先保新 / seed)
            try:
                _sh.move(str(item), str(target))
            except Exception as _e:
                print(f"  ⚠ 搬 {item.name} 失敗: {_e}")
        flag.write_text("migrated by config.py auto-migration")
        print(f"\n[V5 auto-migrate] ✅ 搬遷完成、舊資料夾保留(只搬內容)、可手動刪 {legacy}\n")
    except Exception as e:
        print(f"\n[V5 auto-migrate] ⚠ 搬遷部分失敗:{e}、建議手動 robocopy。\n")

try:
    _auto_migrate_legacy_ai_output()
except Exception as _e:
    print(f"[V5 auto-migrate] ⚠ 略過(發生例外):{_e}")

def check_config() -> list[str]:
    missing = []
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY（AI 驗證與 YAML 助手需要）")
    return missing


def check_host_tools() -> list[str]:
    """檢查 host 端必要系統工具(非 pip 套件、非 sandbox 可達)是否裝。

    回傳警告字串 list、空 list = 全裝齊。non-fatal、只 log 給使用者參考。
    """
    warnings: list[str] = []
    try:
        from pipeline.host_tools import get_host_tools
        for t in get_host_tools():
            if t.required and not t.installed:
                # 平台對應 install 指令
                import platform
                plat_key = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
                    platform.system(), "linux"
                )
                cmd = t.install_cmd.get(plat_key, "")
                warnings.append(
                    f"⚠ 缺少 {t.name}({t.why})。安裝指令:{cmd}"
                )
    except Exception as e:
        warnings.append(f"⚠ host 工具檢查失敗、略過:{e}")
    return warnings
