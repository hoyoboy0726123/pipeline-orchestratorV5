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
# 強制 absolute:.env 設相對路徑時(例如 OUTPUT_BASE_PATH=ai_output)、Path 會保留相對形式、
# 之後傳到 sandbox docker exec -w 會炸「Cwd must be an absolute path (OCI runtime exec)」。
# .resolve() 把相對 path 解到當下 cwd 後變 absolute、行為跟之前一致只是物件形式變了
OUTPUT_BASE_PATH   = Path(os.getenv("OUTPUT_BASE_PATH", "~/ai_output")).expanduser().resolve()
SCHEDULER_DB_PATH  = OUTPUT_BASE_PATH / "pipeline_scheduler.db"
PIPELINE_DIR       = Path(os.getenv("PIPELINE_DIR", "~/pipelines")).expanduser()

OUTPUT_BASE_PATH.mkdir(parents=True, exist_ok=True)
PIPELINE_DIR.mkdir(parents=True, exist_ok=True)

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
