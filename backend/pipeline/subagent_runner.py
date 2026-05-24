"""Subagent 節點執行核心。

Subagent 節點與 skill 節點的差異：
  - skill: agent loop、寫死的 system prompt、recipe cache、validator
  - subagent: 同樣 agent loop、但 system prompt 由 role 決定、tool 白名單過濾、
              跳過 recipe cache、跳過 validator

實作策略：
  - 直接共用 executor 既有的 sandbox 工具集（_execute_skill_tool）
  - 直接共用 executor 既有的 tool call 解析（_parse_skill_tool_calls）
  - 訊息協定一致（<tool>name</tool> + code block / JSON）
  - 多了一層「角色 tool 白名單」過濾、不在白名單的工具直接擋下、回提示給 LLM
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

ROLES_YAML_PATH = Path(__file__).resolve().parent.parent / "subagent_roles.yaml"

# 自訂 role 寫到 user 家目錄 ai_output 下、跟其他 v5 user data 一起、不污染 repo
# load_roles() 會 merge 兩個 yaml(自訂 **不能** override 內建、避免 AI / user 把內建搞壞)
def _custom_roles_path() -> Path:
    try:
        from config import OUTPUT_BASE_PATH  # ~/ai_output
        base = Path(OUTPUT_BASE_PATH)
    except Exception:
        base = Path.home() / "ai_output"
    base.mkdir(parents=True, exist_ok=True)
    return base / "custom_subagent_roles.yaml"


# 內建 role 名單 — 自訂不可使用這 5 個 ID,避免 override
BUILTIN_ROLE_IDS = {"data_analyst", "coder", "researcher", "critic", "planner"}

# 自訂 role 可選的工具白名單(跟 _KNOWN_TOOLS 對齊、扣掉 done 因為它永遠 ON)
SELECTABLE_TOOLS = ["run_python", "run_shell", "read_file", "web_search", "view_image", "ask_user"]

# Subagent 一律允許的工具（即使 role 沒列、也讓 LLM 用 done 終止）
_ALWAYS_ALLOWED = {"done"}

# 已知工具集（供防禦解析驗證 tool_name 是否乾淨）
_KNOWN_TOOLS = {"run_python", "run_shell", "read_file", "web_search", "view_image", "done", "ask_user"}


# ─── Prose-before-tool 智能 cap ──────────────────────────────────────────
# 場景:有 tool call、但第一個 <tool> tag 之前 LLM 寫了一大段 prose(「想很多
# 才動手」)、Claude 系列特別常見、單輪 30K-90K 字 prose、燒 output token。
# 這個 cap 只在「有 tool」時觸發、最終回覆使用者(無 tool call)的長 reply 不擋。
#
# threshold 3000 字 ≈ 一個複雜決策思考夠用、寫不下 91K 字 plan。
# soft mode 預設(觀察期 3-7 天):只 log warning、不擋。
# enforce mode 開啟方式:.env SUBAGENT_PROSE_CAP_MODE=enforce
_PROSE_BEFORE_TOOL_THRESHOLD = 3000   # 第一個 tool 前的 prose 字數上限
_PROSE_BEFORE_TOOL_LIMIT = 2          # 連 N 次違規才中止(enforce 模式)
_PROSE_BEFORE_TOOL_MODE = (os.environ.get("SUBAGENT_PROSE_CAP_MODE", "soft").strip().lower())
if _PROSE_BEFORE_TOOL_MODE not in ("soft", "enforce", "off"):
    _PROSE_BEFORE_TOOL_MODE = "soft"

# 假 done 守門上限(text + native loop 共用):連 N 次假 done 就停止注入 reminder、
# 讓 runner 走 step retry 機制(走 #144 防線、總成本可控)
_FAKE_DONE_LIMIT = 2


def _extract_input_for_tool(tool_name: str, after_text: str) -> str:
    """從 tool 標籤之後的文字抽出對應 input。
    搜尋範圍限縮在「下一個 <tool> 之前」、避免抓到後續 tool 的 input。
    done → 找第一段 JSON 物件（用 brace counter 容忍巢狀）
    其他 → 優先 <input>...</input>、其次 ``` code block ```、最後整段 strip"""
    next_tool = re.search(r"<tool>", after_text)
    section = after_text[:next_tool.start()] if next_tool else after_text

    if tool_name == "done":
        try:
            start = section.index("{")
            depth = 0
            for i in range(start, len(section)):
                ch = section[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return section[start:i + 1]
        except ValueError:
            pass
        return section.strip()

    m_input = re.search(r"<input>(.*?)</input>", section, re.DOTALL)
    if m_input:
        return m_input.group(1).strip()
    m_code = re.search(r"```(?:python|json|bash|sh)?\s*\n?(.*?)```", section, re.DOTALL)
    if m_code:
        return m_code.group(1).strip()
    return section.strip()


def _renormalize_tool_call(parsed: dict, original_reply: str) -> dict:
    """防禦性 re-parse：executor 的主 parser 在 LLM 一個 reply 同時嵌多個 <tool> 時
    可能把多段內容黏進 tool_name（DOTALL non-greedy 的 backtrack bug、且 input 也會被
    錯配到第二個 <tool> 的 <input>）。

    若 tool_name 不是已知識別字、就完全捨棄 parser 的結果、用嚴格 regex 從 original reply
    重抓第一個合法 <tool>NAME</tool> + 其後對應的 input。
    """
    raw_name = (parsed.get("tool") or "").strip()
    if raw_name in _KNOWN_TOOLS:
        return parsed  # 乾淨、原封不動

    # 從 original reply 重新抓第一個 <tool>NAME</tool>（嚴格 identifier）
    m_strict = re.search(r"<tool>\s*([a-z_]+)\s*</tool>", original_reply)
    if not m_strict or m_strict.group(1) not in _KNOWN_TOOLS:
        # 嘗試從 raw_name 第一行抽 token 當最後手段
        m_simple = re.match(r"^([a-z_]+)\b", raw_name)
        if m_simple and m_simple.group(1) in _KNOWN_TOOLS:
            return {"tool": m_simple.group(1), "input": parsed.get("input", "")}
        return parsed  # 無計可施、留原樣讓白名單擋下

    new_name = m_strict.group(1)
    after = original_reply[m_strict.end():]
    new_input = _extract_input_for_tool(new_name, after)

    logger.info(
        f"[subagent] 防禦 re-parse：tool_name {raw_name[:40]!r}... → {new_name!r}"
        f"（input 重新抽取、{len(new_input)} 字）"
    )
    return {"tool": new_name, "input": new_input}


@dataclass
class SubagentResult:
    """Subagent 執行結果、給 runner 用。"""
    success: bool
    final_message: str
    iterations: int
    tool_calls_made: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    # 多輪 LLM call 的累計 token 用量、給 budget 統計與 trace 視圖。
    # 結構: {"input_tokens": int, "output_tokens": int, "total_tokens": int, "model": str}
    token_usage: dict = field(default_factory=dict)


def load_roles() -> dict[str, dict]:
    """載入內建 + 自訂 role,merge 成單一 dict。

    自訂 role(來自 ~/ai_output/custom_subagent_roles.yaml)**不能** override 內建
    (避免 AI / user 把 data_analyst 等改壞)。同 ID 的自訂會被忽略 + log warning。
    """
    # 內建
    try:
        with open(ROLES_YAML_PATH, encoding="utf-8") as f:
            builtin = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"[subagent] 載入內建 {ROLES_YAML_PATH} 失敗: {e}、用 fallback role")
        builtin = {
            "data_analyst": {
                "description": "fallback",
                "tools": ["run_python", "read_file", "done"],
                "system_prompt": "你是資料分析師、在 sandbox 內處理資料。回繁體中文。",
            }
        }
    # 自訂(如果存在)
    custom_path = _custom_roles_path()
    if custom_path.exists():
        try:
            with open(custom_path, encoding="utf-8") as f:
                custom = yaml.safe_load(f) or {}
            for rid, cfg in custom.items():
                if rid in builtin:
                    logger.warning(
                        f"[subagent] 自訂 role '{rid}' 跟內建同名、忽略自訂(內建不可被 override)"
                    )
                    continue
                builtin[rid] = cfg
        except Exception as e:
            logger.warning(f"[subagent] 載入自訂 {custom_path} 失敗: {e}、只用內建")
    return builtin


def load_custom_roles() -> dict[str, dict]:
    """只取自訂(不含內建),供 CRUD endpoint 用。"""
    custom_path = _custom_roles_path()
    if not custom_path.exists():
        return {}
    try:
        with open(custom_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def save_custom_roles(roles: dict[str, dict]) -> None:
    """寫回自訂 role yaml(覆蓋整檔)。"""
    custom_path = _custom_roles_path()
    with open(custom_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(roles, f, allow_unicode=True, sort_keys=False)


class UnknownRoleError(ValueError):
    """Subagent role name 不在內建 / 自訂清單裡。"""
    pass


def get_role(role_name: str) -> dict:
    """取單一角色設定、找不到 raise UnknownRoleError 列可選 role 並提示新增方式。

    舊版邏輯是 silent fallback 到 data_analyst,結果使用者以為「AI 助手自行新增了角色」
    跑得起來但實際是當 data_analyst 跑;改成明確 raise、避免幻覺。
    """
    roles = load_roles()
    if role_name in roles:
        return roles[role_name]
    available = sorted(roles.keys())
    raise UnknownRoleError(
        f"未知 subagent role '{role_name}'。"
        f"可用 role:{available}。"
        f"想新增請(a)在設定頁的『Subagent 角色管理』新增,"
        f"或(b)用 AI 助手呼叫 create_subagent_role 工具(兩步確認)。"
    )


def list_role_names() -> list[str]:
    """列出所有可用角色名（給前端下拉用）。"""
    return sorted(load_roles().keys())


def _build_tool_protocol_hint(allowed_tools: set[str]) -> str:
    """把 role 允許的工具用法寫進 user prompt、讓 LLM 知道格式。"""
    descs = {
        "run_python": "在 sandbox 內跑 Python（檔案讀寫、計算、產出都靠這個）",
        "run_shell": "在 sandbox 內跑 shell 命令",
        "read_file": "讀單一檔案（最多 100 行）",
        "web_search": "Tavily 網路搜尋（query 字串、可選 max_results）",
        "done": '回報任務完成、結束 loop。input 必須是 JSON：{"success": true, "summary": "..."}',
    }
    lines = ["可用工具（限定）:"]
    for t in sorted(allowed_tools):
        lines.append(f"  - {t}: {descs.get(t, '(自訂)')}")
    lines.append("")
    lines.append("呼叫格式：<tool>工具名</tool> 後接 ```python ...``` 或 ```json ...``` 或 <input>...</input>")
    lines.append("最終務必呼叫 <tool>done</tool> 回 JSON 結束。")
    return "\n".join(lines)


def _to_wsl_path(p: str) -> str:
    """sandbox 模式下、把 Windows path 轉成容器內 /mnt/<drive>/... 路徑、給 LLM hint 用。

    防 LLM 在沙盒看到 D:\\... 自己亂猜成 /root/.agents/... 那種 mount 混淆 bug。
    跟 executor.py 內同邏輯 (一個 helper 兩邊用)。
    """
    if not p:
        return p
    try:
        from settings import get_settings
        if (get_settings().get("skill_sandbox_mode") or "host").strip() != "wsl_docker":
            return p
        import re as _re
        m = _re.match(r"^([A-Za-z]):[\\/](.+)$", p)
        if m:
            drv = m.group(1).lower()
            rest = m.group(2).replace(chr(92), "/")
            return f"/mnt/{drv}/{rest}"
    except Exception:
        pass
    return p


def _build_user_prompt(
    task: str,
    output_path: Optional[str],
    prev_outputs: Optional[list[dict]],
    allowed_tools: set[str],
) -> str:
    parts = [f"請完成以下任務：\n\n{task}"]
    if output_path:
        _hint = _to_wsl_path(output_path)
        parts.append(f"\n預期輸出路徑(容器內絕對路徑、寫到這 host 端會看到、不要改別處):`{_hint}`")
    if prev_outputs:
        parts.append("\n前面步驟的輸出:")
        for po in prev_outputs:
            p = po.get("path") or "(無路徑)"
            schema = po.get("schema") or ""
            _p_hint = _to_wsl_path(p) if p != "(無路徑)" else p
            parts.append(f"  - {_p_hint}{(' — ' + schema) if schema else ''}")
    parts.append("\n" + _build_tool_protocol_hint(allowed_tools))
    return "\n".join(parts)


def _maybe_inject_sandbox_hint(system_prompt: str) -> str:
    """若 settings.skill_sandbox_mode='wsl_docker'、追加沙盒環境提示（共用 skill 的格式）。"""
    try:
        from settings import get_settings
        if (get_settings().get("skill_sandbox_mode") or "host").strip() != "wsl_docker":
            return system_prompt
    except Exception:
        return system_prompt

    try:
        v5_root_win = Path(__file__).parent.parent.parent.absolute()
        drive = str(v5_root_win)[0].lower()
        rest = str(v5_root_win)[3:].replace("\\", "/")
        v5_root_wsl = f"/mnt/{drive}/{rest}"
    except Exception:
        v5_root_wsl = "/mnt/c/" + Path(__file__).resolve().parents[2].name

    return system_prompt + f"""

【🛡️ Sandbox 環境(重要)】
本 step 的 run_python / run_shell **在 Linux Docker 容器內執行**:
- OS = Linux:沒有 win32com / pywin32 / PowerShell;用純 Python / Linux 工具
- Windows 路徑要轉:`C:\\X\\...` → `/mnt/c/X/...`、`D:\\X\\...` → `/mnt/d/X/...`
- 專案根目錄:`{v5_root_wsl}`(任務裡的相對路徑以這個為基準)

【⛔ 兩個 mount 不要混淆 — 寫產物搞錯位置 = step 找不到產物 fail】
A. **專案目錄**(寫 workflow 產物的地方、絕大多數情境):
   容器內:`{v5_root_wsl}/`
   workflow 產物寫到 `{v5_root_wsl}/ai_output/<workflow_name>/<檔名>`
   ✅ 範例:`{v5_root_wsl}/ai_output/sales_q1/report.md`

B. **Skill / Agent 目錄**(讀 only、skill 自身住的地方):
   容器內:`/root/.agents/`
   裡面是 SKILL.md / scripts / references — **不是 workflow 產物存放區**

⛔ 絕對不要把 workflow 產物寫到 `/root/.agents/ai_output/<xxx>/`!
   那路徑可能存在(.agents/ai_output 巧合也有)、但**不對應到 host 的 ai_output**、
   Pipeline runner 看不到、step 標 fail。這是踩過的真實坑、必須記牢。

✅ output_path 提示給的是哪個容器內絕對路徑、就寫到那、不要自作主張改路徑。
"""


async def run_subagent(
    *,
    role_name: str,
    task: str,
    max_iter: int = 5,
    workflow_dir: Optional[str] = None,
    run_id: str = "",
    step_name: str = "",
    output_path: Optional[str] = None,
    prev_outputs: Optional[list[dict]] = None,
    timeout: int = 600,
    step_logger: Optional[logging.Logger] = None,
    llm_role: str = "primary",
) -> SubagentResult:
    """執行 subagent loop。

    SUBAGENT_LOOP_MODE 環境變數控制協議:
    - "text"(預設):文字 <tool>...</tool> 協議、自寫 parser(向後相容)
    - "native":LangChain bind_tools() native function calling(Phase A.1)
                output token -30%(無 <tool>tag),input prompt 砍冗餘(A.3 後)、
                LLM 不能偽造 stdout / 不能寫多個 tool 混亂 parser
    切換方式:.env 加 SUBAGENT_LOOP_MODE=native、重啟 backend。

    Args:
        role_name: 角色名（data_analyst / coder / researcher / critic / planner）
        task: 任務描述（節點 batch 內容）
        max_iter: 最多 LLM 輪數
        workflow_dir: 工作目錄（傳給 sandbox tool 當 cwd）
        run_id: pipeline run id
        step_name: 節點名稱（log 標識）
        output_path: 預期輸出路徑（給 LLM 提示）
        prev_outputs: 前一步輸出列表 [{path, schema}, ...]
        timeout: 整體上限秒數（推導 tool_timeout）
        step_logger: per-step logger（傳給 _execute_skill_tool）
    """
    # Phase A.1 feature flag — native function calling 開關
    _mode = (os.environ.get("SUBAGENT_LOOP_MODE", "text") or "text").strip().lower()
    if _mode == "native":
        return await _run_subagent_native(
            role_name=role_name, task=task, max_iter=max_iter,
            workflow_dir=workflow_dir, run_id=run_id, step_name=step_name,
            output_path=output_path, prev_outputs=prev_outputs,
            timeout=timeout, step_logger=step_logger, llm_role=llm_role,
        )

    # 延後 import 避免循環依賴
    from langchain_core.messages import SystemMessage, HumanMessage
    from llm_factory import build_llm, invoke_with_streaming
    from pipeline.executor import (
        _parse_skill_tool_calls,
        _execute_skill_tool,
        _compute_tool_timeout,
    )

    log = step_logger or logger
    try:
        role = get_role(role_name)
    except UnknownRoleError as e:
        # 不存在的 role 直接 step fail、不 silent fallback 到 data_analyst
        log.error(f"[{step_name}] ✗ {e}")
        return SubagentResult(
            success=False, final_message="", iterations=0,
            tool_calls_made=[], error=str(e),
        )
    allowed_tools = set(role.get("tools", [])) | _ALWAYS_ALLOWED

    log.info(f"[{step_name}] 🤖 Subagent 啟動（role={role_name}, max_iter={max_iter}, tools={sorted(allowed_tools)}）")

    system_prompt = _maybe_inject_sandbox_hint(role.get("system_prompt", ""))
    user_prompt = _build_user_prompt(task, output_path, prev_outputs, allowed_tools)

    tool_timeout = _compute_tool_timeout(timeout)

    try:
        llm = build_llm(role=llm_role)
    except Exception as e:
        return SubagentResult(success=False, final_message="", iterations=0, error=f"LLM 建立失敗: {e}")

    # Prompt caching (#153):對 SystemMessage 加 ephemeral 1h cache_control。
    # 多輪 subagent loop 第 2 輪起 system_prompt 命中 cache、input cost 0.1x 計價。
    # Anthropic 收費結構:cache write +25%、cache read -90%、整體多輪場景大省。
    # 對其他 provider(Groq/OpenAI/Gemini/Ollama)cache_control 是 unknown kwarg、會被略過、不影響。
    _sys_msg_kwargs = {"cache_control": {"type": "ephemeral", "ttl": "1h"}}
    messages: list = [
        SystemMessage(content=system_prompt, additional_kwargs=_sys_msg_kwargs),
        HumanMessage(content=user_prompt),
    ]

    tool_calls_made: list[dict] = []
    final_message = ""
    success = False
    # 累計每輪 LLM 的 token usage（subagent 整段執行的總成本）
    accumulated_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "model": "",
    }

    # 連續無 tool 計數器:LLM 連 N 輪沒呼工具就強制終止、避免 prose 死循環(常見:
    # LLM 把分析報告直接寫在 reply 而不寫進檔案、validator 看不到產物就把 step 打 fail)
    consecutive_no_tool = 0
    _NO_TOOL_LIMIT = 2  # 連 2 輪無 tool 就中止
    _PROSE_REPLY_THRESHOLD = 1500  # 單輪 reply > 1500 字但沒 tool 視為「prose 違規」直接算 +1

    # 假 done 計數器:LLM done(success=true) 但 output_path 檔不存在 → reject done、注入
    # reminder 強迫補 run_python(SKILL 模式從 V3 就有的守門、subagent 補上)。比 runner-level
    # 整步 retry 省 50-70% token,因為不必整個 step 從頭跑。
    fake_done_count = 0
    # _FAKE_DONE_LIMIT 已提升到 module level、text + native loop 共用

    # ─── 從 SKILL loop (executor.py) 移植過來的 3 個守門 ──────────────────────
    # 之前 subagent 漏抄、導致 LLM 卡 self-check 循環、user 反映 step 5 max_iter 不 done
    short_code_streak = 0   # 連續 < 200 字 run_python:LLM 卡分步驟讀檔、提示寫整段
    last_error_sig = ""     # 連續 stderr 同錯偵測:避免 LLM 一直寫同 code 撞同 error
    same_error_count = 0
    # tool result smart truncation:read_file 長結果不全塞 history、降 input 累積

    # Prose-before-tool 違規計數器(soft/enforce 共用):有 tool call 但第一個
    # <tool> 之前 prose > _PROSE_BEFORE_TOOL_THRESHOLD 時累加。Claude 寫 91K 字 plan 案例會擋。
    prose_before_tool_violations = 0

    for i in range(max_iter):
        iteration = i + 1
        log.info(f"[{step_name}] Subagent 迭代 {iteration}/{max_iter}")

        # LLM call with retry — Gemma / Gemini 免費 tier 高負載常 503、其他 provider
        # 也有 429 / overloaded、長 task 容易撞。retriable error 用 exponential
        # backoff 重試 2 次(1s / 2s wait)、總共最多 3 次嘗試。非 retriable
        # exception(語法錯、key 無效等)直接 fail、不浪費時間。
        _RETRIABLE_KEYWORDS = (
            "503", "429", "unavailable", "rate limit", "rate_limit",
            "service_unavailable", "overloaded", "internal error", "500",
            "deadline exceeded", "resource_exhausted",
        )
        llm_result = None
        last_llm_err: Optional[Exception] = None
        last_was_timeout = False
        for _attempt in range(3):  # 1 + 2 retries
            try:
                llm_result = await invoke_with_streaming(
                    llm, messages,
                    label=f"subagent[{role_name}]/{step_name}",
                    timeout=600.0,
                    logger=log,
                    return_usage=True,
                )
                last_llm_err = None
                break
            except asyncio.TimeoutError as e:
                last_llm_err = e
                last_was_timeout = True
                if _attempt < 2:
                    _wait = 2 ** _attempt
                    log.warning(f"[{step_name}] LLM 串流逾時、{_wait}s 後 retry({_attempt + 1}/2)")
                    await asyncio.sleep(_wait)
                    continue
                break
            except Exception as e:
                last_llm_err = e
                last_was_timeout = False
                _msg = str(e).lower()
                _retriable = any(k in _msg for k in _RETRIABLE_KEYWORDS)
                if _retriable and _attempt < 2:
                    _wait = 2 ** _attempt
                    log.warning(
                        f"[{step_name}] LLM 暫時錯誤、{_wait}s 後 retry({_attempt + 1}/2):"
                        f" {type(e).__name__}: {str(e)[:200]}"
                    )
                    await asyncio.sleep(_wait)
                    continue
                break  # 非 retriable 或 retry 用完
        if last_llm_err is not None:
            err_msg = "LLM 串流逾時(retry 3 次仍失敗)" if last_was_timeout else (
                f"LLM 呼叫失敗: {type(last_llm_err).__name__}: {last_llm_err}"
            )
            return SubagentResult(
                success=False, final_message="", iterations=iteration,
                tool_calls_made=tool_calls_made, error=err_msg,
                token_usage=accumulated_usage,
            )
        # llm_result 拿到、解析 reply + 累計 token
        reply = (llm_result.get("content") or "").strip()
        um = llm_result.get("usage_metadata") or {}
        if um:
            accumulated_usage["input_tokens"] += um.get("input_tokens", 0) or 0
            accumulated_usage["output_tokens"] += um.get("output_tokens", 0) or 0
            accumulated_usage["total_tokens"] += um.get("total_tokens", 0) or 0
            accumulated_usage["cache_read_tokens"] += um.get("cache_read_tokens", 0) or 0
            accumulated_usage["cache_creation_tokens"] += um.get("cache_creation_tokens", 0) or 0
        if not accumulated_usage["model"]:
            accumulated_usage["model"] = llm_result.get("model") or ""

        tool_calls = _parse_skill_tool_calls(reply)

        if not tool_calls:
            # 沒 tool 呼叫:累計違規、連 _NO_TOOL_LIMIT 次強制中止避免 prose 死循環
            consecutive_no_tool += 1
            is_prose = len(reply) > _PROSE_REPLY_THRESHOLD
            log.warning(
                f"[{step_name}] ⚠ Subagent 第 {iteration} 輪沒呼工具"
                f"(reply {len(reply)} 字{'、屬 prose 違規' if is_prose else ''})、"
                f"累計 {consecutive_no_tool}/{_NO_TOOL_LIMIT}"
            )
            if consecutive_no_tool >= _NO_TOOL_LIMIT:
                err_msg = (
                    f"連續 {_NO_TOOL_LIMIT} 輪沒呼叫任何 tool 或 done、強制終止避免死循環。"
                    f"LLM 可能把分析/結論直接寫在 reply 而不是用 run_python 寫到檔案。"
                )
                log.error(f"[{step_name}] ✗ {err_msg}")
                final_message = f"(被系統終止)最後一輪 reply 前 200 字:\n{reply[:200]}"
                return SubagentResult(
                    success=False, final_message=final_message, iterations=iteration,
                    tool_calls_made=tool_calls_made, error="consecutive_no_tool_calls",
                    token_usage=accumulated_usage,
                )
            if i == max_iter - 1:
                final_message = reply
                break
            messages.append(HumanMessage(content=reply))
            # 違規累積時提示越來越強硬
            if consecutive_no_tool == 1:
                reminder = "請使用 <tool>...</tool> 格式呼叫工具、或 <tool>done</tool> 回 JSON 結束。"
            else:
                reminder = (
                    f"[最後一次警告] 你已連 {consecutive_no_tool} 輪沒呼叫任何工具。"
                    f"下一輪 reply 必須含 <tool>run_python</tool> / <tool>read_file</tool> 之一"
                    f"或 <tool>done</tool>、否則系統會強制終止 step。"
                    f"分析結論不要寫在 reply 裡、寫進 run_python 的 Path.write_text() 存進指定檔。"
                )
            messages.append(HumanMessage(content=reminder))
            continue

        # 有 tool 呼叫 → 重置 no-tool 計數
        consecutive_no_tool = 0

        # Prose-before-tool 智能 cap:有 tool call、但第一個 <tool> tag 之前的
        # prose > _PROSE_BEFORE_TOOL_THRESHOLD 字 → 「想很多才動手」、燒 output token。
        # Claude 寫 91K 字 plan 案例會擋。最終回覆使用者(no tool)的長 reply 不擋。
        if _PROSE_BEFORE_TOOL_MODE != "off":
            _first_tool_match = re.search(r"<tool>", reply)
            if _first_tool_match is not None:
                _prose_chars = len(reply[: _first_tool_match.start()].strip())
                if _prose_chars > _PROSE_BEFORE_TOOL_THRESHOLD:
                    prose_before_tool_violations += 1
                    log.warning(
                        f"[{step_name}] ⚠ Subagent 第 {iteration} 輪 tool 前 prose {_prose_chars:,} 字"
                        f"(上限 {_PROSE_BEFORE_TOOL_THRESHOLD})、累計違規 "
                        f"{prose_before_tool_violations}/{_PROSE_BEFORE_TOOL_LIMIT} "
                        f"[mode={_PROSE_BEFORE_TOOL_MODE}]"
                    )
                    if (_PROSE_BEFORE_TOOL_MODE == "enforce"
                            and prose_before_tool_violations >= _PROSE_BEFORE_TOOL_LIMIT):
                        err_msg = (
                            f"連續 {_PROSE_BEFORE_TOOL_LIMIT} 輪 tool 前 prose 超過 "
                            f"{_PROSE_BEFORE_TOOL_THRESHOLD} 字、強制中止避免燒 token。"
                            f"思考分析請寫進 run_python 的 comment 或 print()、不要寫 reply 內。"
                        )
                        log.error(f"[{step_name}] ✗ {err_msg}")
                        final_message = (
                            f"(被系統終止 — prose-before-tool 違規)最後一輪 prose 前 200 字:\n"
                            f"{reply[:_first_tool_match.start()].strip()[:200]}"
                        )
                        return SubagentResult(
                            success=False, final_message=final_message,
                            iterations=iteration, tool_calls_made=tool_calls_made,
                            error="prose_before_tool_violation",
                            token_usage=accumulated_usage,
                        )

        # 一次只處理第一個 tool（避免 LLM 同時 run_python + done 製造假成功）
        first = _renormalize_tool_call(tool_calls[0], reply)
        tool_name = (first.get("tool") or "").strip()
        tool_input = first.get("input", "")

        # 多 tool 偵測:LLM 一次塞多個 <tool>...</tool> tag 是惡習(後面的會被當 prose 忽略)
        # 對齊 SKILL 的同類守門(executor.py:2652)、之前 subagent 漏抄
        _tag_count = len(re.findall(r"<tool>\s*\w+\s*</tool>", reply))
        multi_tool_warn = _tag_count > 1
        if multi_tool_warn:
            log.info(f"[{step_name}] 解析:tool={tool_name}, input_len={len(tool_input)} (⚠ 偵測到 {_tag_count} 個 <tool> 標籤、只跑第一個)")

        # done：解析 JSON、結束 loop
        if tool_name == "done":
            try:
                done_data = json.loads(tool_input) if tool_input.strip().startswith("{") else {"success": True, "summary": tool_input}
                success = bool(done_data.get("success", True))
                final_message = (
                    done_data.get("summary")
                    or done_data.get("message")
                    or done_data.get("error")
                    or "(空 done)"
                )
                # 假 done 守門:success=true 但 output_path 檔不存在 → reject、注入 reminder
                # 強迫 LLM 補 run_python 真寫檔(SKILL 模式對應的 executor.py:2701)。
                # 連 _FAKE_DONE_LIMIT 次都假 done → 讓 runner 走 step retry(走 #144 防線)
                if (success and output_path and Path(output_path).expanduser().exists() is False
                        and fake_done_count < _FAKE_DONE_LIMIT):
                    fake_done_count += 1
                    log.warning(
                        f"[{step_name}] ⛔ Subagent 想 done(success=true) 但 output 檔 {output_path} 不存在"
                        f"、reject + reminder({fake_done_count}/{_FAKE_DONE_LIMIT})"
                    )
                    messages.append(HumanMessage(content=reply))
                    messages.append(HumanMessage(content=(
                        f"[系統] 你宣稱成功但輸出檔 {output_path} 不存在!"
                        f"\n必須先用 <tool>run_python</tool> 實際跑 code 把產物寫到那個路徑(用 Path(...).write_text() / df.to_excel() / fig.savefig() 等)、"
                        f"\n然後再 self-check Path('{output_path}').exists() == True 才能 done(success=true)。"
                        f"\n不准只展示 code、必須真跑、跑完 print 確認檔存在。"
                    )))
                    continue
                tool_calls_made.append({"name": "done", "input_preview": tool_input[:200], "result_preview": ""})
                log.info(f"[{step_name}] ✅ Subagent 主動 done（success={success}）")
                break
            except Exception as e:
                messages.append(HumanMessage(content=reply))
                messages.append(HumanMessage(content=f"[系統] done 的 input JSON 解析失敗（{e}）、請改用 {{\"success\": true, \"summary\": \"...\"}} 重試。"))
                continue

        # 白名單過濾
        if tool_name not in allowed_tools:
            messages.append(HumanMessage(content=reply))
            messages.append(HumanMessage(content=(
                f"[系統] 此角色（{role_name}）無權呼叫工具 '{tool_name}'。"
                f"\n可用工具：{sorted(allowed_tools)}。請改用允許的工具或直接 done。"
            )))
            log.warning(f"[{step_name}] ⛔ 白名單擋下 tool={tool_name}（role={role_name}）")
            continue

        # 執行工具（共用 skill 的 dispatcher、自動走 sandbox 路由）
        try:
            result = _execute_skill_tool(
                tool_name, tool_input,
                cwd=workflow_dir,
                run_id=run_id,
                logger=log,
                tool_timeout=tool_timeout,
            )
        except Exception as e:
            result = f"[執行失敗] {type(e).__name__}: {e}"

        result_str = str(result) if result is not None else ""
        tool_calls_made.append({
            "name": tool_name,
            "input_preview": (tool_input or "")[:200],
            "result_preview": result_str[:300],
        })
        log.info(f"[{step_name}] 🛠 tool={tool_name} 完成（result {len(result_str)} 字）")

        # 截斷大型 tool 輸出再接回 messages、防 context 雪崩
        # 真實案例:某 run_shell(find / pip list 之類)回 92K 字、被 append 後
        # 之後每輪 input 全帶這 92K、瞬間衝爆 context、max_iter 燒完
        # 留前 4K + 後 1K(中間 elide)、保訊息開頭 + 失敗尾巴(stderr 通常在尾)
        #
        # **D task**:read_file 結果改 head 2K + tail 1K(更激進、因為使用者用得多、累積快)、
        # 提示 LLM 需要更多再 read_file(offset=N) 拉 page、不要每輪都讓全文塞 history。
        if tool_name == "read_file":
            _MAX_TOOL_OUT = 3000
            _HEAD = 2000
            _TAIL = 1000
        else:
            _MAX_TOOL_OUT = 5000
            _HEAD = 4000
            _TAIL = 1000
        if len(result_str) > _MAX_TOOL_OUT:
            _head = result_str[:_HEAD]
            _tail = result_str[-_TAIL:]
            _elide_hint = ""
            if tool_name == "read_file":
                _elide_hint = (
                    f"\n[系統提示] read_file 結果太長已截、想看中間請用 "
                    f"<tool>read_file</tool><input>{{\"path\": \"...\", \"offset\": N}}</input> 從第 N 行讀。"
                    f"不必每輪都讀全文。"
                )
            result_for_msg = (
                f"{_head}\n"
                f"\n…[中間省略 {len(result_str) - _HEAD - _TAIL} 字、完整長度 {len(result_str)}、"
                f"head {_HEAD}+ tail {_TAIL}]…\n"
                f"{_tail}{_elide_hint}"
            )
            log.info(f"[{step_name}] 🪚 tool={tool_name} 結果過長({len(result_str)} 字)、截到 ~{_HEAD + _TAIL} 接回 messages")
        else:
            result_for_msg = result_str

        # 把 LLM 回覆 + tool 結果接回對話（沿用 skill loop 慣例）
        messages.append(HumanMessage(content=reply))
        messages.append(HumanMessage(content=f"[工具結果 — {tool_name}]\n{result_for_msg}"))

        # 多 tool 警告 reminder(對齊 SKILL executor.py:3057-3066)
        if multi_tool_warn:
            messages.append(HumanMessage(
                content=f"[系統警告] 你這個 reply 裡有 {_tag_count} 個 <tool> 標籤。系統只跑第一個({tool_name})、"
                        f"其他 tag + done 都被忽略。規則:每個 reply 只能寫一個 <tool>...</tool><input>...</input>、"
                        f"然後等系統回真實結果再決定下一步。不要 plan 多 tool。"
            ))

        # 連續短 run_python 偵測(對齊 SKILL executor.py:3069-3080):
        # LLM 卡在「分步驟讀檔」、提示寫整段
        if tool_name == "run_python" and len(tool_input) < 200:
            short_code_streak += 1
            if short_code_streak >= 3:
                log.warning(f"[{step_name}] 連續 {short_code_streak} 次短 run_python、注入打破循環提示")
                messages.append(HumanMessage(
                    content="[系統警告] 你已連續多次只跑很短的 run_python 讀資料、任務尚未完成。"
                            "請在一個 <tool>run_python</tool> 內寫完整 code:讀取 + 處理 + 寫入 output_path、"
                            "self-check 完畢後 done(success=true)。不要再 1 行 1 行讀。"
                ))
                short_code_streak = 0
        else:
            short_code_streak = 0

        # 連續同錯偵測(對齊 SKILL executor.py:3082-3103):
        # LLM 寫 self-check py / 主 code、反覆撞同 stderr → 提示換做法、不要硬刷同樣 code
        if tool_name == "run_python" and "[stderr]" in result_str:
            _err_lines = [l for l in result_str.split("\n") if l.strip() and not l.startswith("[")]
            error_sig = _err_lines[-1].strip() if _err_lines else ""
            if error_sig and error_sig == last_error_sig:
                same_error_count += 1
                if same_error_count >= 2:
                    log.warning(f"[{step_name}] 相同錯誤連 {same_error_count + 1} 次、注入修正提示")
                    messages.append(HumanMessage(
                        content=f"[系統警告] 你已連續 {same_error_count + 1} 次撞到相同錯誤:{error_sig}\n"
                                f"不能再寫一樣 / 類似的 code。請完全換做法:\n"
                                f"  1. 用 <tool>read_file</tool> 先看當前 output_path 內容、確認檔到底在不在 / 內容對不對\n"
                                f"  2. 如果檔已存在且內容 OK → 直接 <tool>done</tool> 回 success=true、不要再 self-check\n"
                                f"  3. 如果檔有問題 → 寫一段全新 code(改變數名 / 改邏輯)、不要重複前面失敗的 pattern"
                    ))
                    same_error_count = 0
            else:
                last_error_sig = error_sig
                same_error_count = 1
        else:
            last_error_sig = ""
            same_error_count = 0

    iterations_done = min(max_iter, i + 1)

    if not final_message:
        final_message = "(subagent 達到 max_iter 未呼叫 done、視為失敗)"
        log.warning(f"[{step_name}] ⚠ Subagent 達到 max_iter 仍未 done")
        return SubagentResult(
            success=False, final_message=final_message, iterations=iterations_done,
            tool_calls_made=tool_calls_made, error="reached_max_iter_without_done",
            token_usage=accumulated_usage,
        )

    return SubagentResult(
        success=success,
        final_message=final_message,
        iterations=iterations_done,
        tool_calls_made=tool_calls_made,
        token_usage=accumulated_usage,
    )


# ============================================================
# Phase A.1 — Native function calling loop
# ============================================================
async def _run_subagent_native(
    *,
    role_name: str,
    task: str,
    max_iter: int = 5,
    workflow_dir: Optional[str] = None,
    run_id: str = "",
    step_name: str = "",
    output_path: Optional[str] = None,
    prev_outputs: Optional[list[dict]] = None,
    timeout: int = 600,
    step_logger: Optional[logging.Logger] = None,
    llm_role: str = "primary",
) -> SubagentResult:
    """Phase A.1 — SUBAGENT loop 用 LangChain bind_tools() native function calling 版本。

    跟 text 版相同 signature 跟回傳、可以 1:1 替換。差別:
    - tool_calls 從 AIMessage.tool_calls 拿(LLM API 結構保證、不必文字 parser)
    - tool 結果用 ToolMessage 接(取代 HumanMessage + 「[工具結果 — X]」)
    - 沒有「多 tool 標籤誤判」「LLM 偽 [工具結果]」「<tool> 文字格式錯誤」這類 parsing-level bug

    保留的守門(從 text 版繼承):
    - 角色白名單(透過 build_subagent_tools 的 allowed_tool_names)
    - max_iter 上限
    - consecutive_no_tool 計數(連 N 輪無 tool_call → 中止)
    - prose-before-tool soft cap(AIMessage.content 太長 + 有 tool_calls 警告)
    - fake_done 檔案存在驗證(LLM done 但 output_path 不存在 → reject + reminder)
    - token usage 累計(含 cache_read / cache_creation)
    """
    from langchain_core.messages import (
        SystemMessage, HumanMessage, AIMessage, ToolMessage,
    )
    from llm_factory import build_llm
    from pipeline.executor import _compute_tool_timeout
    from pipeline.sandbox_tools import build_subagent_tools

    log = step_logger or logger
    try:
        role = get_role(role_name)
    except UnknownRoleError as e:
        log.error(f"[{step_name}] ✗ {e}")
        return SubagentResult(
            success=False, final_message="", iterations=0,
            tool_calls_made=[], error=str(e),
        )
    allowed_tools = set(role.get("tools", [])) | _ALWAYS_ALLOWED

    log.info(
        f"[{step_name}] 🤖 Subagent 啟動 [NATIVE FC](role={role_name}, "
        f"max_iter={max_iter}, tools={sorted(allowed_tools)})"
    )

    system_prompt = _maybe_inject_sandbox_hint(role.get("system_prompt", ""))
    user_prompt = _build_user_prompt(task, output_path, prev_outputs, allowed_tools)
    tool_timeout = _compute_tool_timeout(timeout)

    # Build LLM + bind tools
    try:
        llm = build_llm(role=llm_role)
    except Exception as e:
        return SubagentResult(
            success=False, final_message="", iterations=0,
            error=f"LLM 建立失敗: {e}",
        )

    web_search_counter = {"count": 0}
    tools = build_subagent_tools(
        cwd=workflow_dir, run_id=run_id, logger=log,
        tool_timeout=tool_timeout, allowed_tool_names=allowed_tools,
        step_name=step_name, web_search_counter=web_search_counter,
    )
    name_to_tool = {t.name: t for t in tools}
    try:
        llm_with_tools = llm.bind_tools(tools)
    except Exception as e:
        log.error(f"[{step_name}] bind_tools 失敗:{e}、fallback 不可用")
        return SubagentResult(
            success=False, final_message="", iterations=0,
            error=f"bind_tools 失敗: {e}",
        )

    # Prompt caching — SystemMessage 加 cache_control(對 Anthropic 有效、其他 provider 略過)
    _sys_msg_kwargs = {"cache_control": {"type": "ephemeral", "ttl": "1h"}}
    messages: list = [
        SystemMessage(content=system_prompt, additional_kwargs=_sys_msg_kwargs),
        HumanMessage(content=user_prompt),
    ]

    tool_calls_made: list[dict] = []
    final_message = ""
    success = False
    accumulated_usage = {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0, "model": "",
    }
    consecutive_no_tool = 0
    fake_done_count = 0
    iterations_done = 0

    _RETRIABLE_KEYWORDS = (
        "503", "429", "unavailable", "rate limit", "rate_limit",
        "service_unavailable", "overloaded", "internal error", "500",
        "deadline exceeded", "resource_exhausted",
    )

    for i in range(max_iter):
        iteration = i + 1
        iterations_done = iteration
        log.info(f"[{step_name}] Subagent 迭代 {iteration}/{max_iter} [NATIVE]")

        # LLM call with retry
        response: Optional[AIMessage] = None
        last_llm_err: Optional[Exception] = None
        for _attempt in range(3):
            try:
                log.info(f"[subagent[{role_name}]/{step_name}] 🤖 LLM 開始處理(input {sum(len(str(getattr(m, 'content', '') or '')) for m in messages):,} 字)…")
                _t0 = asyncio.get_event_loop().time()
                response = await asyncio.wait_for(
                    llm_with_tools.ainvoke(messages), timeout=600.0,
                )
                _elapsed = asyncio.get_event_loop().time() - _t0
                _content_str = (getattr(response, "content", "") or "") if isinstance(getattr(response, "content", ""), str) else ""
                log.info(
                    f"[subagent[{role_name}]/{step_name}] ✅ LLM 完成"
                    f"({_elapsed:.0f}s, content {len(_content_str)} 字, "
                    f"tool_calls={len(getattr(response, 'tool_calls', []) or [])})"
                )
                last_llm_err = None
                break
            except asyncio.TimeoutError as e:
                last_llm_err = e
                if _attempt < 2:
                    _wait = 2 ** _attempt
                    log.warning(f"[{step_name}] LLM 逾時、{_wait}s retry({_attempt + 1}/2)")
                    await asyncio.sleep(_wait)
                    continue
                break
            except Exception as e:
                last_llm_err = e
                _msg = str(e).lower()
                _retriable = any(k in _msg for k in _RETRIABLE_KEYWORDS)
                if _retriable and _attempt < 2:
                    _wait = 2 ** _attempt
                    log.warning(
                        f"[{step_name}] LLM 暫時錯誤、{_wait}s retry({_attempt + 1}/2):"
                        f"{type(e).__name__}: {str(e)[:200]}"
                    )
                    await asyncio.sleep(_wait)
                    continue
                break

        if last_llm_err is not None or response is None:
            err_msg = (
                f"LLM 呼叫失敗: {type(last_llm_err).__name__}: {last_llm_err}"
                if last_llm_err else "LLM 回傳 None"
            )
            return SubagentResult(
                success=False, final_message="", iterations=iteration,
                tool_calls_made=tool_calls_made, error=err_msg,
                token_usage=accumulated_usage,
            )

        # 累計 token usage
        um = getattr(response, "usage_metadata", None) or {}
        if isinstance(um, dict) and um:
            accumulated_usage["input_tokens"] += um.get("input_tokens", 0) or 0
            accumulated_usage["output_tokens"] += um.get("output_tokens", 0) or 0
            accumulated_usage["total_tokens"] += um.get("total_tokens", 0) or 0
            itd = um.get("input_token_details") or {}
            if isinstance(itd, dict):
                accumulated_usage["cache_read_tokens"] += itd.get("cache_read", 0) or 0
                accumulated_usage["cache_creation_tokens"] += itd.get("cache_creation", 0) or 0
        if not accumulated_usage["model"]:
            _rm = getattr(response, "response_metadata", None) or {}
            accumulated_usage["model"] = _rm.get("model_name") or _rm.get("model") or ""

        # 把 AIMessage 加進 messages(下一輪 LLM 看得到自己上輪 reply)
        messages.append(response)

        tool_calls = list(getattr(response, "tool_calls", []) or [])
        content_str = response.content if isinstance(response.content, str) else ""

        # ── 沒 tool_calls → 純 prose、累計 consecutive_no_tool ─────
        if not tool_calls:
            consecutive_no_tool += 1
            log.warning(
                f"[{step_name}] ⚠ 第 {iteration} 輪沒 tool_calls(reply {len(content_str)} 字)、"
                f"累計 {consecutive_no_tool}/2"
            )
            if consecutive_no_tool >= 2:
                err_msg = (
                    f"連 2 輪沒呼叫任何 tool、強制中止。"
                    f"LLM 可能把分析結論寫 content 而非 run_python 寫檔。"
                )
                log.error(f"[{step_name}] ✗ {err_msg}")
                return SubagentResult(
                    success=False,
                    final_message=f"(被系統終止)最後 reply 前 200 字:\n{content_str[:200]}",
                    iterations=iteration, tool_calls_made=tool_calls_made,
                    error="consecutive_no_tool_calls", token_usage=accumulated_usage,
                )
            if i == max_iter - 1:
                final_message = content_str
                break
            # 提示繼續
            messages.append(HumanMessage(content=(
                "請呼叫一個 tool(run_python / read_file / 等)繼續推進、"
                "或呼叫 done 結束。分析請寫進 run_python 的程式碼、不要寫 content。"
            )))
            continue

        consecutive_no_tool = 0

        # ── 處理 tool_calls ─────────────────────────────────────
        # 先掃過一次找 done(LLM 在同輪可能 [run_python, done]、要先跑非 done、再驗 done)
        done_call: Optional[dict] = None
        regular_calls = []
        for tc in tool_calls:
            if tc.get("name") == "done":
                done_call = tc  # 留最後處理(若有多個 done 取最後)
            else:
                regular_calls.append(tc)

        # 跑非 done 的 tool、每個 append ToolMessage
        last_tool_name_this_iter: Optional[str] = None
        last_tool_result_this_iter: Optional[str] = None
        for tc in regular_calls:
            tc_name = tc.get("name", "")
            tc_id = tc.get("id") or ""
            tc_args = tc.get("args", {}) or {}
            tool_fn = name_to_tool.get(tc_name)
            log.info(f"[{step_name}] 🛠 tool={tc_name} args_keys={list(tc_args.keys())}")
            try:
                if tool_fn is None:
                    result = (
                        f"[錯誤] tool '{tc_name}' 不在白名單。可用:{sorted(name_to_tool.keys())}"
                    )
                else:
                    # async tool (ask_user) 用 ainvoke、sync 也接 ainvoke
                    raw = await tool_fn.ainvoke(tc_args)
                    result = str(raw) if raw is not None else ""
            except Exception as e:
                result = f"[執行失敗] {type(e).__name__}: {e}"

            tool_calls_made.append({
                "name": tc_name,
                "input_preview": json.dumps(tc_args, ensure_ascii=False)[:200],
                "result_preview": result[:300],
            })
            last_tool_name_this_iter = tc_name
            last_tool_result_this_iter = result

            # 截斷大 result(防 context 雪崩)
            _MAX = 3000 if tc_name == "read_file" else 5000
            _HEAD = 2000 if tc_name == "read_file" else 4000
            _TAIL = 1000
            if len(result) > _MAX:
                _head = result[:_HEAD]
                _tail = result[-_TAIL:]
                result = (
                    f"{_head}\n…[中間省略 {len(result) - _HEAD - _TAIL} 字、"
                    f"完整長度 {len(result)}]…\n{_tail}"
                )

            messages.append(ToolMessage(content=result, tool_call_id=tc_id))

        # ── 處理 done(如有)── 驗證 output 存在、否則 reject + reminder
        if done_call is not None:
            tc_id = done_call.get("id") or ""
            tc_args = done_call.get("args", {}) or {}
            _success = bool(tc_args.get("success", True))
            _summary = (
                tc_args.get("summary")
                or tc_args.get("error")
                or "(空 summary)"
            )

            # 假 done 守門:success=true 但 output_path 檔不存在 → reject、注入 reminder
            if (
                _success and output_path
                and Path(output_path).expanduser().exists() is False
                and fake_done_count < _FAKE_DONE_LIMIT
            ):
                fake_done_count += 1
                log.warning(
                    f"[{step_name}] ⛔ done(success=true) 但 output 檔 {output_path} 不存在、"
                    f"reject + reminder({fake_done_count}/{_FAKE_DONE_LIMIT})"
                )
                messages.append(ToolMessage(
                    content=(
                        f"[拒收] 你宣稱成功但輸出檔 {output_path} 不存在!"
                        f"請先用 run_python 實際寫檔到那個路徑、跑完 print 確認 "
                        f"Path('{output_path}').exists() == True 才能 done(success=true)。"
                        f"不准只展示 code、必須真跑。"
                    ),
                    tool_call_id=tc_id,
                ))
                continue

            # 通過 → 標 done 結束 loop
            messages.append(ToolMessage(
                content="__DONE_ACCEPTED__",
                tool_call_id=tc_id,
            ))
            tool_calls_made.append({
                "name": "done",
                "input_preview": json.dumps(tc_args, ensure_ascii=False)[:200],
                "result_preview": "",
            })
            success = _success
            final_message = _summary
            log.info(
                f"[{step_name}] ✅ Subagent 主動 done"
                f"(success={_success}, summary 前 80 字={_summary[:80]})"
            )
            break

    else:
        # max_iter 用完沒 done
        return SubagentResult(
            success=False,
            final_message=f"(超過 {max_iter} 輪未 done)最後 reply:{(content_str or '')[:200]}",
            iterations=iterations_done, tool_calls_made=tool_calls_made,
            error="reached_max_iter_without_done", token_usage=accumulated_usage,
        )

    return SubagentResult(
        success=success,
        final_message=final_message,
        iterations=iterations_done,
        tool_calls_made=tool_calls_made,
        token_usage=accumulated_usage,
    )
