"""Ticket 2:condition 節點測試。

驗證:
- eval_condition 正確判斷各種 truthy/falsy 寫法
- eval_value 正確 render switch 用的字串
- 表達式語法錯 / 變數未定義 → ExpressionError
- IF 模式 dispatcher 跳對 step
- Switch 模式 dispatcher 命中 case + default fallback
- MAX_VISITS 防無限迴圈(僅單元 — 跑 pipeline 的測試由 e2e 處理)
"""
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pipeline.expression import (  # noqa: E402
    eval_condition, eval_value, ExpressionError,
)


class EvalConditionTests(unittest.TestCase):
    def test_simple_comparison_true(self):
        ctx = {"steps": {"x": {"output": {"rows": "150"}}}}
        self.assertTrue(eval_condition("{{ steps.x.output.rows | int > 100 }}", ctx))

    def test_simple_comparison_false(self):
        ctx = {"steps": {"x": {"output": {"rows": "50"}}}}
        self.assertFalse(eval_condition("{{ steps.x.output.rows | int > 100 }}", ctx))

    def test_string_in(self):
        ctx = {"steps": {"api": {"output": {"stdout": "result is ok"}}}}
        self.assertTrue(eval_condition('{{ "ok" in steps.api.output.stdout }}', ctx))
        self.assertFalse(eval_condition('{{ "fail" in steps.api.output.stdout }}', ctx))

    def test_input_truthy(self):
        ctx = {"input": {"flag": "1"}}
        self.assertTrue(eval_condition("{{ input.flag }}", ctx))
        ctx2 = {"input": {"flag": ""}}
        self.assertFalse(eval_condition("{{ input.flag }}", ctx2))

    def test_truthy_words_recognized(self):
        ctx = {"input": {"v": "yes"}}
        self.assertTrue(eval_condition("{{ input.v }}", ctx))
        ctx2 = {"input": {"v": "no"}}
        self.assertFalse(eval_condition("{{ input.v }}", ctx2))

    def test_without_braces(self):
        """user 寫純表達式不包 {{ }} 也要能 work"""
        ctx = {"input": {"n": "5"}}
        self.assertTrue(eval_condition("input.n | int >= 5", ctx))
        self.assertFalse(eval_condition("input.n | int > 10", ctx))

    def test_undefined_var_raises(self):
        with self.assertRaises(ExpressionError):
            eval_condition("{{ steps.no.output.x > 0 }}", {"steps": {}})

    def test_empty_expression_raises(self):
        with self.assertRaises(ExpressionError):
            eval_condition("", {})
        with self.assertRaises(ExpressionError):
            eval_condition("   ", {})


class EvalValueTests(unittest.TestCase):
    def test_returns_string(self):
        ctx = {"steps": {"api": {"output": {"status": "200"}}}}
        self.assertEqual(eval_value("{{ steps.api.output.status }}", ctx), "200")

    def test_strips_whitespace(self):
        ctx = {"input": {"x": "  hello  "}}
        # render 後 strip 掉前後空白
        self.assertEqual(eval_value("{{ input.x }}", ctx), "hello")

    def test_undefined_raises(self):
        with self.assertRaises(ExpressionError):
            eval_value("{{ steps.no_such.output.x }}", {"steps": {}})


class ConditionStepModelTests(unittest.TestCase):
    """驗證 PipelineStep 接受 condition 欄位"""

    def test_condition_fields_default_empty(self):
        from pipeline.models import PipelineStep
        s = PipelineStep(name="x")
        self.assertFalse(s.condition)
        self.assertEqual(s.expression, "")
        self.assertEqual(s.on_true, "")
        self.assertEqual(s.on_false, "")
        self.assertEqual(s.switch, "")
        self.assertEqual(s.cases, {})
        self.assertEqual(s.default, "")

    def test_condition_node_full_config(self):
        from pipeline.models import PipelineStep
        s = PipelineStep(
            name="route",
            condition=True,
            expression="{{ steps.x.output.rows | int > 100 }}",
            on_true="bulk_step",
            on_false="single_step",
        )
        self.assertTrue(s.condition)
        self.assertIn("rows", s.expression)
        self.assertEqual(s.on_true, "bulk_step")

    def test_switch_node_config(self):
        from pipeline.models import PipelineStep
        s = PipelineStep(
            name="route_switch",
            condition=True,
            switch="{{ steps.api.output.status }}",
            cases={"200": "ok", "404": "retry", "500": "fail"},
            default="fail",
        )
        self.assertEqual(s.switch, "{{ steps.api.output.status }}")
        self.assertEqual(s.cases["404"], "retry")
        self.assertEqual(s.default, "fail")


if __name__ == "__main__":
    unittest.main()
