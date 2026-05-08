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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

ROLES_YAML_PATH = Path(__file__).resolve().parent.parent / "subagent_roles.yaml"

# Subagent 一律允許的工具（即使 role 沒列、也讓 LLM 用 done 終止）
_ALWAYS_ALLOWED = {"done"}

# 已知工具集（供防禦解析驗證 tool_name 是否乾淨）
_KNOWN_TOOLS = {"run_python", "run_shell", "read_file", "web_search", "view_image", "done", "ask_user"}


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


def load_roles() -> dict[str, dict]:
    """載入角色配置。失敗回 fallback。"""
    try:
        with open(ROLES_YAML_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"[subagent] 載入 {ROLES_YAML_PATH} 失敗: {e}、用 fallback role")
        return {
            "data_analyst": {
                "description": "fallback",
                "tools": ["run_python", "read_file", "done"],
                "system_prompt": "你是資料分析師、在 sandbox 內處理資料。回繁體中文。",
            }
        }


def get_role(role_name: str) -> dict:
    """取單一角色設定、找不到回 data_analyst（保證可用）。"""
    roles = load_roles()
    if role_name in roles:
        return roles[role_name]
    logger.warning(f"[subagent] 角色 '{role_name}' 不存在、退回 data_analyst")
    return roles.get("data_analyst") or list(roles.values())[0]


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


def _build_user_prompt(
    task: str,
    output_path: Optional[str],
    prev_outputs: Optional[list[dict]],
    allowed_tools: set[str],
) -> str:
    parts = [f"請完成以下任務：\n\n{task}"]
    if output_path:
        parts.append(f"\n預期輸出路徑：{output_path}")
    if prev_outputs:
        parts.append("\n前面步驟的輸出：")
        for po in prev_outputs:
            p = po.get("path") or "(無路徑)"
            schema = po.get("schema") or ""
            parts.append(f"  - {p}{(' — ' + schema) if schema else ''}")
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
        v5_root_wsl = "/mnt/c/Users/GU605_PR_MZ/pipeline-orchestratorV5"

    return system_prompt + f"""

【🛡️ Sandbox 環境（重要）】
本 step 的 run_python / run_shell **在 Linux Docker 容器內執行**：
- OS = Linux：沒有 win32com / pywin32 / PowerShell；用純 Python / Linux 工具
- Windows 路徑要轉：`C:\\X\\...` → `/mnt/c/X/...`
- 專案根目錄：`{v5_root_wsl}`（任務裡的相對路徑以這個為基準）
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
) -> SubagentResult:
    """執行 subagent loop。

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
    # 延後 import 避免循環依賴
    from langchain_core.messages import SystemMessage, HumanMessage
    from llm_factory import build_llm, invoke_with_streaming
    from pipeline.executor import (
        _parse_skill_tool_calls,
        _execute_skill_tool,
        _compute_tool_timeout,
    )

    log = step_logger or logger
    role = get_role(role_name)
    allowed_tools = set(role.get("tools", [])) | _ALWAYS_ALLOWED

    log.info(f"[{step_name}] 🤖 Subagent 啟動（role={role_name}, max_iter={max_iter}, tools={sorted(allowed_tools)}）")

    system_prompt = _maybe_inject_sandbox_hint(role.get("system_prompt", ""))
    user_prompt = _build_user_prompt(task, output_path, prev_outputs, allowed_tools)

    tool_timeout = _compute_tool_timeout(timeout)

    try:
        llm = build_llm()
    except Exception as e:
        return SubagentResult(success=False, final_message="", iterations=0, error=f"LLM 建立失敗: {e}")

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    tool_calls_made: list[dict] = []
    final_message = ""
    success = False

    for i in range(max_iter):
        iteration = i + 1
        log.info(f"[{step_name}] Subagent 迭代 {iteration}/{max_iter}")

        try:
            reply = (await invoke_with_streaming(
                llm, messages,
                label=f"subagent[{role_name}]/{step_name}",
                timeout=600.0,
                logger=log,
            )).strip()
        except asyncio.TimeoutError:
            return SubagentResult(
                success=False, final_message="", iterations=iteration,
                tool_calls_made=tool_calls_made, error="LLM 串流逾時",
            )
        except Exception as e:
            return SubagentResult(
                success=False, final_message="", iterations=iteration,
                tool_calls_made=tool_calls_made, error=f"LLM 呼叫失敗: {type(e).__name__}: {e}",
            )

        tool_calls = _parse_skill_tool_calls(reply)

        if not tool_calls:
            # 沒 tool 呼叫：要求補一次、若再沒有就把這段當最終回覆
            if i == max_iter - 1:
                final_message = reply
                break
            messages.append(HumanMessage(content=reply))
            messages.append(HumanMessage(content="請使用 <tool>...</tool> 格式呼叫工具、或 <tool>done</tool> 回 JSON 結束。"))
            continue

        # 一次只處理第一個 tool（避免 LLM 同時 run_python + done 製造假成功）
        first = _renormalize_tool_call(tool_calls[0], reply)
        tool_name = (first.get("tool") or "").strip()
        tool_input = first.get("input", "")

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

        # 把 LLM 回覆 + tool 結果接回對話（沿用 skill loop 慣例）
        messages.append(HumanMessage(content=reply))
        messages.append(HumanMessage(content=f"[工具結果 — {tool_name}]\n{result_str}"))

    iterations_done = min(max_iter, i + 1)

    if not final_message:
        final_message = "(subagent 達到 max_iter 未呼叫 done、視為失敗)"
        log.warning(f"[{step_name}] ⚠ Subagent 達到 max_iter 仍未 done")
        return SubagentResult(
            success=False, final_message=final_message, iterations=iterations_done,
            tool_calls_made=tool_calls_made, error="reached_max_iter_without_done",
        )

    return SubagentResult(
        success=success,
        final_message=final_message,
        iterations=iterations_done,
        tool_calls_made=tool_calls_made,
    )
