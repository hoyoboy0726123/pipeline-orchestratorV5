"""Subagent smoke test — 直接呼 run_subagent、不經 HTTP / pipeline runner。

兩個測試：
  1. planner（無 tool、只能 done）→ 驗證 LLM call + done parsing 通
  2. data_analyst（含 run_python）→ 驗證 sandbox 工具白名單 + tool dispatch 通
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# 確保能 import 同層的 settings / llm_factory / pipeline.*
sys.path.insert(0, str(Path(__file__).parent.absolute()))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("subagent-smoke")


async def test_planner():
    """planner 只能 done、純推理。預期：1 iter、success=True、回計畫 markdown。"""
    log.info("=" * 60)
    log.info("Test 1: planner 角色")
    log.info("=" * 60)

    from pipeline.subagent_runner import run_subagent
    result = await run_subagent(
        role_name="planner",
        task="我有個 sales.xlsx、想做季度趨勢分析。幫我拆出步驟。",
        max_iter=2,
        step_name="planner-test",
        timeout=120,
    )
    log.info(f"success: {result.success}")
    log.info(f"iterations: {result.iterations}")
    log.info(f"tools used: {[tc.get('name') for tc in result.tool_calls_made]}")
    log.info(f"error: {result.error}")
    log.info(f"final_message:\n{result.final_message}")
    return result


async def test_data_analyst():
    """data_analyst 用 run_python 在 sandbox 算簡單運算。"""
    log.info("=" * 60)
    log.info("Test 2: data_analyst 角色")
    log.info("=" * 60)

    from pipeline.subagent_runner import run_subagent
    result = await run_subagent(
        role_name="data_analyst",
        task=(
            "用 Python 在 sandbox 內產一個簡單運算：算 1 到 100 的平方和、"
            "把結果 print 出來、然後呼叫 done 回報結果（summary 寫『1-100 平方和 = X』）。"
            "不要寫檔、不要查網路、就直接運算 + done。"
        ),
        max_iter=5,
        step_name="analyst-test",
        timeout=180,
    )
    log.info(f"success: {result.success}")
    log.info(f"iterations: {result.iterations}")
    log.info(f"tools used: {[tc.get('name') for tc in result.tool_calls_made]}")
    log.info(f"error: {result.error}")
    log.info(f"final_message:\n{result.final_message}")
    for i, tc in enumerate(result.tool_calls_made, 1):
        log.info(f"  tool[{i}] {tc.get('name')}: input={tc.get('input_preview')[:120]}")
        log.info(f"           result={tc.get('result_preview')[:200]}")
    return result


async def test_critic_whitelist():
    """critic 試圖 call run_python（不被允許）→ 驗證白名單擋下、loop 引導改用 read_file 或 done。"""
    log.info("=" * 60)
    log.info("Test 3: critic 白名單擋下")
    log.info("=" * 60)

    from pipeline.subagent_runner import run_subagent
    result = await run_subagent(
        role_name="critic",
        task=(
            "讀 backend/subagent_roles.yaml 這個檔（路徑是相對 V5 根）、"
            "挑出 3 個你覺得這份 role 設計可疑的點。"
            "（提示：你只有 read_file + done 兩個工具）"
        ),
        max_iter=4,
        step_name="critic-test",
        timeout=180,
    )
    log.info(f"success: {result.success}")
    log.info(f"iterations: {result.iterations}")
    log.info(f"tools used: {[tc.get('name') for tc in result.tool_calls_made]}")
    log.info(f"final_message:\n{result.final_message}")
    return result


async def main():
    results = []

    # Test 1: planner（最低風險）
    try:
        r = await test_planner()
        results.append(("planner", r.success, r.error))
    except Exception as e:
        log.exception("planner test 例外")
        results.append(("planner", False, str(e)))

    # Test 2: data_analyst（碰沙盒）
    if "--no-sandbox" not in sys.argv:
        try:
            r = await test_data_analyst()
            results.append(("data_analyst", r.success, r.error))
        except Exception as e:
            log.exception("data_analyst test 例外")
            results.append(("data_analyst", False, str(e)))

    # Test 3: critic 白名單
    if "--with-critic" in sys.argv:
        try:
            r = await test_critic_whitelist()
            results.append(("critic", r.success, r.error))
        except Exception as e:
            log.exception("critic test 例外")
            results.append(("critic", False, str(e)))

    log.info("=" * 60)
    log.info("最終總結")
    log.info("=" * 60)
    for name, ok, err in results:
        log.info(f"  {name}: {'✅ PASS' if ok else '❌ FAIL'} {('('+err+')') if err else ''}")


if __name__ == "__main__":
    asyncio.run(main())
