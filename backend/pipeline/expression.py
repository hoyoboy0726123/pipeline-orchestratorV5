"""Jinja2-based template render for pipeline step string fields.

設計原則:
- 只 render 含 {{ }} / {% %} 的字串、其餘原樣回傳(零成本)
- 用 StrictUndefined:未定義變數明確報錯、不靜默 ""
- 保護 UIA 既有的「步驟內 {{var}} / {{var + 1}}」短變數語法、不被 Jinja2 吃掉
  (那段在 uia_executor._substitute_vars 處理、執行 action 時才解析)

Context 結構:
    {
        "steps": {
            "<step_name>": {
                "output": {
                    "path": "...",        # actual_output_path
                    "stdout": "...",      # stdout_tail
                    "stderr": "...",
                    "exit_code": 0,
                    "status": "ok",
                    "<save_as_var>": ...,  # step_vars promote 到 output
                }
            },
            ...
        },
        "input": {<input_params>},
        "env": {<os.environ>},
    }
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from jinja2 import Environment, StrictUndefined, UndefinedError, TemplateSyntaxError


class ExpressionError(Exception):
    """render 失敗(未定義變數 / 語法錯誤)。錯誤訊息已包含原因。"""


# Jinja2 環境、lazy init
_env: Optional[Environment] = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            undefined=StrictUndefined,
            autoescape=False,        # 不是 render HTML
            keep_trailing_newline=True,
        )
        # 常用 filter
        import json as _json
        _env.filters["json"] = lambda v: _json.dumps(v, ensure_ascii=False)
    return _env


# UIA 既有的「步驟內」短變數語法:{{var}} 或 {{var + 1}} / {{var - 2}}
# 規則:整個 {{ }} 內只有單一識別字、可選 [+-] 整數;不含 dot / pipe / filter
# 這類 pattern 由 uia_executor._substitute_vars 在執行 action 時解析、Jinja2 不能碰
_INTRA_STEP_VAR_RE = re.compile(
    r"\{\{\s*\w+\s*(?:[+-]\s*\d+)?\s*\}\}"
)


def _protect_intra_step_vars(s: str) -> str:
    """把 UIA 步驟內短變數包進 {% raw %},Jinja2 render 時原樣輸出、不嘗試解析。

    {{var}}     → {% raw %}{{var}}{% endraw %}
    {{steps.X}} → 原樣(會被 Jinja2 處理)
    """
    def _wrap(m: re.Match) -> str:
        return "{% raw %}" + m.group(0) + "{% endraw %}"
    return _INTRA_STEP_VAR_RE.sub(_wrap, s)


def render(s: Any, context: dict) -> Any:
    """Render 單一字串。非字串 / 不含 {{ }} 直接回傳。

    Raises:
        ExpressionError: 變數未定義或 Jinja2 語法錯
    """
    if not isinstance(s, str):
        return s
    if "{{" not in s and "{%" not in s:
        return s
    protected = _protect_intra_step_vars(s)
    try:
        tmpl = _get_env().from_string(protected)
        return tmpl.render(**context)
    except UndefinedError as e:
        raise ExpressionError(f"未定義變數: {e}") from e
    except TemplateSyntaxError as e:
        raise ExpressionError(f"模板語法錯誤: {e}") from e


# PipelineStep top-level 字串欄位,實際會用到變數的清單
# (timeout / retry / 等數值欄位不 render)
_STEP_STR_FIELDS = (
    "batch",
    "working_dir",
    "message",
    "uia_window",
    "vv_prompt",
    "wc_url",
    "wc_video_url",
    "wc_wait_for_selector",
    "wc_cookies",
    "wc_child_link_pattern",
    "wc_video_subs_langs",
    "skill",
)


# ComputerUseAction 字串欄位中、會吃變數的清單
_ACTION_STR_FIELDS = (
    "text", "title", "title_contains", "vlm_prompt", "expected",
    "ocr_text", "window", "image", "image2",
)


def render_step(step, context: dict):
    """In-place render 一個 PipelineStep 的所有字串欄位。

    沒寫 {{ }} 的欄位完全跳過(零 render 成本、舊行為不變)。
    回傳同一個 step 物件(方便鏈式使用)。

    覆蓋範圍:
      - PipelineStep top-level str 欄位(batch / working_dir / message / etc.)
      - StepOutput.path / .expect
      - ComputerUseAction list 內每個 action 的 str 欄位 + control dict + keys list + row/column
      - outlook_params dict 的 str values
      - wc_urls list 的 str items
    """
    # 1) top-level 字串欄位
    for fname in _STEP_STR_FIELDS:
        v = getattr(step, fname, None)
        if isinstance(v, str) and v:
            new = render(v, context)
            if new != v:
                setattr(step, fname, new)

    # 2) StepOutput
    if step.output is not None:
        if isinstance(step.output.path, str) and step.output.path:
            step.output.path = render(step.output.path, context)
        if isinstance(step.output.expect, str) and step.output.expect:
            step.output.expect = render(step.output.expect, context)
        if isinstance(step.output.description, str) and step.output.description:
            step.output.description = render(step.output.description, context)

    # 3) ComputerUseAction list
    if step.actions:
        for action in step.actions:
            for fname in _ACTION_STR_FIELDS:
                v = getattr(action, fname, None)
                if isinstance(v, str) and v:
                    new = render(v, context)
                    if new != v:
                        setattr(action, fname, new)
            # control dict 的 str values
            if action.control:
                action.control = {
                    k: (render(v, context) if isinstance(v, str) else v)
                    for k, v in action.control.items()
                }
            # row / column 可填字串(intra-step var 或 inter-step)
            for fname in ("row", "column"):
                v = getattr(action, fname, None)
                if isinstance(v, str) and v:
                    new = render(v, context)
                    if new != v:
                        setattr(action, fname, new)
            # keys list
            if action.keys:
                action.keys = [
                    render(k, context) if isinstance(k, str) else k
                    for k in action.keys
                ]

    # 4) outlook_params:shallow render 每個 str value
    if step.outlook_params:
        step.outlook_params = {
            k: (render(v, context) if isinstance(v, str) else v)
            for k, v in step.outlook_params.items()
        }

    # 5) wc_urls list
    if step.wc_urls:
        step.wc_urls = [
            render(u, context) if isinstance(u, str) else u
            for u in step.wc_urls
        ]

    return step


def build_context(*, step_results=None, input_params=None,
                  env_passthrough: bool = True) -> dict:
    """組裝 render context: {steps, input, env}。

    Args:
        step_results: list[StepResult] — PipelineRun.step_results(已完成的)
        input_params: dict — run 啟動時傳的參數
        env_passthrough: 是否把 os.environ 暴露到 env namespace
    """
    steps_ns: dict[str, dict] = {}
    if step_results:
        for sr in step_results:
            out: dict[str, Any] = {
                "stdout": getattr(sr, "stdout_tail", "") or "",
                "stderr": getattr(sr, "stderr_tail", "") or "",
                "exit_code": getattr(sr, "exit_code", 0),
                "path": getattr(sr, "actual_output_path", "") or "",
                "status": getattr(sr, "validation_status", "") or "",
            }
            # save_as / step_vars promote 到 output namespace
            sv = getattr(sr, "step_vars", None) or {}
            for k, v in sv.items():
                # 不覆寫上面的固定 key(stdout / path 等)
                if k not in out:
                    out[k] = v
            steps_ns[sr.step_name] = {"output": out}

    ctx: dict[str, Any] = {
        "steps": steps_ns,
        "input": dict(input_params or {}),
    }
    if env_passthrough:
        # 把整份 os.environ 暴露;secrets 之後另開 namespace,不直接讀環境變數
        ctx["env"] = dict(os.environ)
    else:
        ctx["env"] = {}
    return ctx


def find_referenced_vars(s: str) -> list[str]:
    """掃字串裡所有 Jinja2 變數引用、回傳 dotted-path list。

    例:"python a.py {{ steps.x.output.path }} --date {{ input.date }}"
       → ["steps.x.output.path", "input.date"]

    用於 GET /workflows/{id}/variables — 列舉 workflow 引用了哪些變數。
    intra-step UIA 短變數會被忽略(不算 inter-step 變數)。
    """
    if not isinstance(s, str) or "{{" not in s:
        return []
    # 移除 intra-step pattern,避免被誤掃進來
    cleaned = _INTRA_STEP_VAR_RE.sub("", s)
    # 簡單規則:抓 {{ ... }} 裡第一個 dotted identifier
    refs = []
    for m in re.finditer(r"\{\{\s*([^}|]+?)\s*(?:\|.*?)?\s*\}\}", cleaned):
        expr = m.group(1).strip()
        # 取 expression 開頭的 dotted-path(忽略後面算術)
        dotted = re.match(r"([a-zA-Z_][\w.]*)", expr)
        if dotted:
            refs.append(dotted.group(1))
    return refs
