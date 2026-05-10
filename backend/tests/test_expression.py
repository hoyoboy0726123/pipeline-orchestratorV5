"""Ticket 1a:Jinja2 變數系統測試。

驗證:
- 不含 {{ }} 的字串完全不動(舊 workflow 行為不變)
- {{ steps.X.output.Y }} / {{ input.X }} / {{ env.X }} 正確 render
- save_as 透過 step_vars promote 到 steps.<name>.output.<key>
- 未定義變數 raise ExpressionError
- UIA 既有的「步驟內 {{var}}」短變數受保護、Jinja2 不會吃掉
- find_referenced_vars 正確掃出引用清單
"""
import os
import sys
import unittest
from pathlib import Path

# 確保 import 走 backend 套件
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.expression import (  # noqa: E402
    render,
    render_step,
    build_context,
    find_referenced_vars,
    ExpressionError,
)
from pipeline.models import PipelineStep, StepOutput, ComputerUseAction  # noqa: E402
from pipeline.store import StepResult  # noqa: E402


class RenderBasicsTests(unittest.TestCase):
    def test_no_braces_passthrough(self):
        """沒寫 {{ }} 的字串完全不動、不會踩到 Jinja2"""
        s = "python a.py --in data.csv"
        out = render(s, {"steps": {}})
        self.assertIs(out, s)

    def test_non_string_passthrough(self):
        for v in (None, 0, 1.5, True, [1, 2], {"a": 1}):
            self.assertEqual(render(v, {}), v)

    def test_steps_output_path(self):
        ctx = {"steps": {"crawl": {"output": {"path": "data/ptt.csv"}}}}
        out = render("python a.py --in {{ steps.crawl.output.path }}", ctx)
        self.assertEqual(out, "python a.py --in data/ptt.csv")

    def test_input_params(self):
        ctx = {"input": {"date": "2026-05-10", "customer": "ASUS"}}
        out = render("--date {{ input.date }} --customer {{ input.customer }}", ctx)
        self.assertEqual(out, "--date 2026-05-10 --customer ASUS")

    def test_env_passthrough(self):
        ctx = {"env": {"OUTPUT_BASE_PATH": "C:/data"}}
        out = render("{{ env.OUTPUT_BASE_PATH }}/result.csv", ctx)
        self.assertEqual(out, "C:/data/result.csv")

    def test_undefined_raises(self):
        ctx = {"steps": {}, "input": {}}
        with self.assertRaises(ExpressionError):
            render("{{ steps.no_such.output.path }}", ctx)
        with self.assertRaises(ExpressionError):
            render("{{ input.missing_key }}", ctx)

    def test_filter_works(self):
        """Jinja2 內建 filter 可用、自訂 json filter 可用"""
        ctx = {"input": {"count": "5"}}
        out = render("{{ input.count | int + 1 }}", ctx)
        self.assertEqual(out, "6")

    def test_intra_step_uia_var_preserved(self):
        """UIA 步驟內短變數 {{var}} / {{var+1}} 不能被 Jinja2 render 吃掉、
        應該原樣保留給 uia_executor._substitute_vars 之後處理"""
        # 純粹 intra-step,沒寫 inter-step
        ctx = {"steps": {}}
        out = render("row {{row_count + 1}}", ctx)
        self.assertEqual(out, "row {{row_count + 1}}")

        out2 = render("{{order_id}}", ctx)
        self.assertEqual(out2, "{{order_id}}")

    def test_mixed_intra_and_inter_step(self):
        """同字串內 intra-step + inter-step 共存,intra 受保護、inter 被 render"""
        ctx = {"steps": {"x": {"output": {"order_id": "SO-123"}}}}
        out = render("inter={{ steps.x.output.order_id }} intra={{var_name}}", ctx)
        self.assertEqual(out, "inter=SO-123 intra={{var_name}}")


class BuildContextTests(unittest.TestCase):
    def _mk_sr(self, name, **kwargs):
        defaults = dict(
            step_index=0, step_name=name, exit_code=0,
            stdout_tail="", stderr_tail="",
            validation_status="ok", validation_reason="", validation_suggestion="",
        )
        defaults.update(kwargs)
        return StepResult(**defaults)

    def test_step_vars_promote_to_output(self):
        """save_as 存進 step_vars 的 key 應該在 steps.<name>.output 看得到"""
        sr = self._mk_sr("extract_order",
                         step_vars={"order_id": "SO-2026-1234", "amount": 35000})
        ctx = build_context(step_results=[sr])
        out = ctx["steps"]["extract_order"]["output"]
        self.assertEqual(out["order_id"], "SO-2026-1234")
        self.assertEqual(out["amount"], 35000)

    def test_actual_output_path_promoted_to_path(self):
        sr = self._mk_sr("crawl", actual_output_path="/tmp/data.csv")
        ctx = build_context(step_results=[sr])
        self.assertEqual(ctx["steps"]["crawl"]["output"]["path"], "/tmp/data.csv")

    def test_step_vars_dont_overwrite_reserved_keys(self):
        """save_as 用了 'path' / 'stdout' 等保留 key 時、不應蓋掉系統欄位"""
        sr = self._mk_sr("x",
                         actual_output_path="/real/path.csv",
                         step_vars={"path": "FAKE", "stdout": "FAKE"})
        ctx = build_context(step_results=[sr])
        self.assertEqual(ctx["steps"]["x"]["output"]["path"], "/real/path.csv")

    def test_input_passthrough(self):
        ctx = build_context(input_params={"date": "2026-05-10"})
        self.assertEqual(ctx["input"]["date"], "2026-05-10")

    def test_env_includes_os_environ(self):
        os.environ["__TICKET_1A_TEST_VAR"] = "hello"
        try:
            ctx = build_context()
            self.assertEqual(ctx["env"].get("__TICKET_1A_TEST_VAR"), "hello")
        finally:
            os.environ.pop("__TICKET_1A_TEST_VAR", None)


class RenderStepTests(unittest.TestCase):
    def test_renders_batch_field(self):
        step = PipelineStep(name="x", batch="python a.py --in {{ input.path }}")
        render_step(step, {"input": {"path": "data.csv"}})
        self.assertEqual(step.batch, "python a.py --in data.csv")

    def test_renders_step_output_path(self):
        step = PipelineStep(
            name="x",
            batch="python b.py",
            output=StepOutput(path="ai_output/{{ input.date }}/result.csv"),
        )
        render_step(step, {"input": {"date": "2026-05-10"}})
        self.assertEqual(step.output.path, "ai_output/2026-05-10/result.csv")

    def test_renders_action_text_and_control(self):
        step = PipelineStep(
            name="x",
            computer_use=True,
            actions=[
                ComputerUseAction(
                    type="uia_send_keys",
                    text="{{ steps.prev.output.order_id }}",
                    control={"name": "輸入欄 {{ input.tag }}"},
                )
            ],
        )
        ctx = {
            "steps": {"prev": {"output": {"order_id": "SO-1"}}},
            "input": {"tag": "A"},
        }
        render_step(step, ctx)
        self.assertEqual(step.actions[0].text, "SO-1")
        self.assertEqual(step.actions[0].control["name"], "輸入欄 A")

    def test_intra_step_var_in_action_text_preserved(self):
        """UIA 步驟內 {{var}} 短變數在 action.text 裡也要受保護"""
        step = PipelineStep(
            name="x",
            computer_use=True,
            actions=[ComputerUseAction(type="uia_send_keys", text="{{order_id}}")],
        )
        render_step(step, {"steps": {}})
        self.assertEqual(step.actions[0].text, "{{order_id}}")

    def test_no_braces_means_no_change(self):
        """沒寫 {{ }} 的字串欄位不應被任何方式改動"""
        step = PipelineStep(name="x", batch="python plain.py", working_dir="/work")
        original_batch = step.batch
        render_step(step, {})  # 空 context — 若 render 試圖跑會 raise
        self.assertEqual(step.batch, original_batch)
        self.assertEqual(step.working_dir, "/work")


class FindReferencedVarsTests(unittest.TestCase):
    def test_simple_dotted(self):
        refs = find_referenced_vars("python a.py {{ steps.crawl.output.path }}")
        self.assertEqual(refs, ["steps.crawl.output.path"])

    def test_multiple_refs(self):
        s = "{{ input.date }} - {{ steps.x.output.order_id }} - {{ env.HOME }}"
        refs = find_referenced_vars(s)
        self.assertEqual(set(refs), {"input.date", "steps.x.output.order_id", "env.HOME"})

    def test_intra_step_excluded(self):
        """UIA 步驟內 {{var}} 不算 inter-step 變數、不應被列舉"""
        refs = find_referenced_vars("{{order_id}} {{ input.date }}")
        self.assertEqual(refs, ["input.date"])

    def test_filter_ignored(self):
        """變數後接 filter 時、回傳的 dotted-path 不含 filter"""
        refs = find_referenced_vars("{{ input.count | int + 1 }}")
        self.assertEqual(refs, ["input.count"])


if __name__ == "__main__":
    unittest.main()
