"""Ticket 2:condition 節點 dispatcher 端對端測試。

直接 import runner、用 mock-able 方式跑流程。
不走 TestClient(它不執行 background asyncio task)、改自己跑 run_pipeline。
"""
import asyncio
import sys
import unittest
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class ConditionDispatcherE2ETests(unittest.TestCase):
    """驗證 condition dispatcher 真的會跳到正確 step。

    用最小 workflow + echo 命令、確認:
    - IF true → on_true 那條 branch 真的跑
    - IF false → on_false 那條 branch 真的跑
    - Switch 命中 case → 對應 step 真的跑
    - Switch miss → default 真的跑
    """

    def _run_workflow(self, yaml_str: str, input_params: dict = None) -> "PipelineRun":  # noqa: F821
        """直接用 asyncio 跑 run_pipeline、回傳完成的 PipelineRun。"""
        from pipeline.runner import run_pipeline
        from pipeline.models import PipelineConfig
        from pipeline.store import get_store, PipelineRun
        from pipeline.logger import create_run_logger
        import yaml as _yaml

        data = _yaml.safe_load(yaml_str)
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = False
        config = PipelineConfig(**config_dict)
        run_id = uuid.uuid4().hex[:12]
        config_d = config.model_dump()
        # 先建立 log file 給 logger 用
        _, log_path = create_run_logger(run_id, config.name)
        run = PipelineRun(
            run_id=run_id,
            pipeline_name=config.name,
            config_dict=config_d,
            telegram_chat_id=0,
            log_path=log_path,
            input_params=input_params or {},
        )
        get_store().save(run)

        async def _go():
            await run_pipeline(config_d, chat_id=0, run_id=run_id)

        asyncio.run(_go())
        result = get_store().load(run_id)
        try:
            get_store().delete(run_id)
        except Exception:
            pass
        return result

    def test_if_true_branch(self):
        """IF expression 為 true → 跳 on_true、跳過 on_false。
        每個 branch 用 next:end 標明跑完就結束、避免線性掉到下一個 branch。"""
        yaml_str = """
pipeline:
  name: t2_if_true
  steps:
    - name: setup
      batch: "echo setup"
    - name: route
      condition: true
      expression: "{{ input.flag }}"
      on_true: branch_true
      on_false: branch_false
    - name: branch_true
      batch: "echo TRUE_BRANCH_RAN"
      next: end
    - name: branch_false
      batch: "echo FALSE_BRANCH_RAN"
      next: end
"""
        run = self._run_workflow(yaml_str, input_params={"flag": "1"})
        self.assertIn(run.status, ("completed", "failed"))
        ran_names = [sr.step_name for sr in run.step_results]
        self.assertIn("setup", ran_names)
        self.assertIn("route", ran_names)
        self.assertIn("branch_true", ran_names)
        self.assertNotIn("branch_false", ran_names,
                         f"branch_false 不應該跑、實際 ran: {ran_names}")

    def test_if_false_branch(self):
        """IF expression 為 false → 跳 on_false、跳過 on_true"""
        yaml_str = """
pipeline:
  name: t2_if_false
  steps:
    - name: route
      condition: true
      expression: "{{ input.flag }}"
      on_true: branch_true
      on_false: branch_false
    - name: branch_true
      batch: "echo TRUE_BRANCH"
      next: end
    - name: branch_false
      batch: "echo FALSE_BRANCH"
      next: end
"""
        run = self._run_workflow(yaml_str, input_params={"flag": ""})
        ran_names = [sr.step_name for sr in run.step_results]
        self.assertIn("route", ran_names)
        self.assertIn("branch_false", ran_names)
        self.assertNotIn("branch_true", ran_names,
                         f"branch_true 不應該跑、實際 ran: {ran_names}")

    def test_switch_case_match(self):
        """Switch 求值命中 case → 跳對應 step"""
        yaml_str = """
pipeline:
  name: t2_switch_match
  steps:
    - name: route
      condition: true
      switch: "{{ input.code }}"
      cases:
        "200": ok
        "404": not_found
      default: server_error
    - name: ok
      batch: "echo OK_BRANCH"
      next: end
    - name: not_found
      batch: "echo NF_BRANCH"
      next: end
    - name: server_error
      batch: "echo ERR_BRANCH"
      next: end
"""
        run = self._run_workflow(yaml_str, input_params={"code": "404"})
        ran_names = [sr.step_name for sr in run.step_results]
        self.assertIn("not_found", ran_names)
        self.assertNotIn("ok", ran_names)
        self.assertNotIn("server_error", ran_names)

    def test_switch_default_fallback(self):
        """Switch 沒命中 → 跑 default"""
        yaml_str = """
pipeline:
  name: t2_switch_default
  steps:
    - name: route
      condition: true
      switch: "{{ input.code }}"
      cases:
        "200": ok
      default: server_error
    - name: ok
      batch: "echo OK"
      next: end
    - name: server_error
      batch: "echo ERR"
      next: end
"""
        run = self._run_workflow(yaml_str, input_params={"code": "999"})
        ran_names = [sr.step_name for sr in run.step_results]
        self.assertIn("server_error", ran_names)
        self.assertNotIn("ok", ran_names)

    def test_jump_to_nonexistent_fails(self):
        """跳到不存在的 step name → run 失敗"""
        yaml_str = """
pipeline:
  name: t2_bad_jump
  steps:
    - name: route
      condition: true
      expression: "{{ input.flag }}"
      on_true: nonexistent_step
      on_false: real_step
    - name: real_step
      batch: "echo real"
"""
        run = self._run_workflow(yaml_str, input_params={"flag": "1"})
        self.assertEqual(run.status, "failed")
        # route 的 step_result 應該標 failed
        route_sr = next((sr for sr in run.step_results if sr.step_name == "route"), None)
        self.assertIsNotNone(route_sr)
        self.assertEqual(route_sr.validation_status, "failed")
        self.assertIn("不存在", route_sr.validation_reason)


if __name__ == "__main__":
    unittest.main()
