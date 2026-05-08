"""YAML → canvas 轉換器。

使用情境：
- TG `/save` 命令把 AI 對話產生的 YAML 套到工作流時、同步重建 canvas（讓桌面開該工作流能看到節點）
- 一次性遷移 / 修復「YAML 有但 canvas 空」的工作流

把 PipelineConfig 風格的 YAML（pyyaml 解析後的 dict）轉成 frontend stepsToFlow 期望的
{nodes: [...], edges: [...]} 格式。各種節點類型旗標的對應：
- web_crawler / visual_validation / outlook_automation / computer_use /
  ai_validation / human_confirm / skill_mode → 對應 React Flow node type
- 預設 → scriptStep
"""
from __future__ import annotations

import yaml as _yaml
from typing import Optional


def _step_to_node(step: dict, idx: int) -> dict:
    """把 YAML step dict 轉成 canvas node dict（對應 stepsToFlow 的輸出格式）。"""
    name = step.get("name", f"步驟 {idx + 1}")
    common = {
        "id": f"step-{idx}",
        "position": {"x": idx * 320, "y": 160},
    }
    base_data = {
        "name": name,
        "index": idx,
        "status": "idle",
        "errorMsg": "",
        "timeout": step.get("timeout", 300),
        "retry": step.get("retry", 1),
    }
    output = step.get("output") or {}
    output_path = output.get("path", "") if isinstance(output, dict) else ""

    # human_confirm
    if step.get("human_confirm"):
        return {**common, "type": "humanConfirmation", "data": {
            **base_data,
            "message": step.get("batch", "請確認"),
            "outputPath": output_path,
            "sendOutput": step.get("send_output", True),
        }}

    # visual_validation
    if step.get("visual_validation"):
        return {**common, "type": "visualValidation", "data": {
            **base_data,
            "source": step.get("vv_source", "prev_output"),
            "prompt": step.get("vv_prompt", ""),
            "searchRegion": step.get("vv_search_region", []),
        }}

    # outlook_automation
    if step.get("outlook_automation"):
        return {**common, "type": "outlookAutomation", "data": {
            **base_data,
            "template": step.get("outlook_template", ""),
            "freeText": step.get("outlook_free_text", ""),
            "params": step.get("outlook_params", {}),
            "outputPath": output_path,
        }}

    # web_crawler
    if step.get("web_crawler"):
        return {**common, "type": "webCrawler", "data": {
            **base_data,
            "mode": step.get("wc_mode", "web"),
            "url": step.get("wc_url", ""),
            "urls": step.get("wc_urls", []),
            "jsRender": step.get("wc_js_render", True),
            "waitForSelector": step.get("wc_wait_for_selector", ""),
            "cloudflareFallback": step.get("wc_cloudflare_fallback", True),
            "cookies": step.get("wc_cookies", ""),
            "interactions": step.get("wc_interactions", []),
            "downloadAssets": step.get("wc_download_assets", False),
            "scrollCount": step.get("wc_scroll_count", 0),
            "targetPostCount": step.get("wc_target_post_count", 0),
            "withChildren": step.get("wc_with_children", False),
            "outputPath": output_path,
        }}

    # computer_use
    if step.get("computer_use"):
        return {**common, "type": "computerUse", "data": {
            **base_data,
            "actions": step.get("computer_use_actions", []),
            "assetsDir": step.get("computer_use_assets_dir", ""),
            "failFast": step.get("computer_use_fail_fast", True),
            "cvThreshold": step.get("cv_threshold", 0.5),
            "cvSearchOnlyNear": step.get("cv_search_only_near", False),
            "cvSearchRadius": step.get("cv_search_radius", 400),
            "cvTriggerHover": step.get("cv_trigger_hover", True),
            "cvHoverWaitMs": step.get("cv_hover_wait_ms", 200),
            "cvCoordFallback": step.get("cv_coord_fallback", False),
            "ocrThreshold": step.get("ocr_threshold", 0.6),
            "ocrCvFallback": step.get("ocr_cv_fallback", False),
        }}

    # ai_validation
    if step.get("ai_validation"):
        return {**common, "type": "aiValidation", "data": {
            **base_data,
            "criteria": step.get("ai_validation_criteria", ""),
            "source": step.get("ai_validation_source", "prev_output"),
        }}

    # skill_mode
    if step.get("skill_mode"):
        return {**common, "type": "skillStep", "data": {
            **base_data,
            "taskDescription": step.get("batch", ""),
            "workingDir": step.get("working_dir", ""),
            "outputPath": output_path,
            "expectedOutput": step.get("expect", ""),
            "readonly": step.get("readonly", False),
            "skill": step.get("skill", ""),
            "askMode": step.get("ask_mode", False),
        }}

    # 預設 script step
    return {**common, "type": "scriptStep", "data": {
        **base_data,
        "batch": step.get("batch", ""),
        "workingDir": step.get("working_dir", ""),
        "outputPath": output_path,
        "expectedOutput": step.get("expect", ""),
    }}


def yaml_to_canvas(yaml_str: str) -> Optional[dict]:
    """解析 YAML 字串、產出 canvas dict（{nodes, edges}）。

    回傳 None：YAML 為空、無 steps、解析失敗。
    回傳 dict：{nodes: [...], edges: [...]}（edges 串成線性 step-0 → step-1 → ...）
    """
    if not yaml_str or not yaml_str.strip():
        return None
    try:
        parsed = _yaml.safe_load(yaml_str)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    steps = parsed.get("steps") or []
    if not isinstance(steps, list) or len(steps) == 0:
        return None
    nodes = [_step_to_node(s, i) for i, s in enumerate(steps) if isinstance(s, dict)]
    if not nodes:
        return None
    edges = [
        {
            "id": f"edge-{i}-{i + 1}",
            "source": f"step-{i}",
            "target": f"step-{i + 1}",
        }
        for i in range(len(nodes) - 1)
    ]
    return {"nodes": nodes, "edges": edges}
