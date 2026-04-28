"""Outlook 自動化節點純函式單元測試（不碰 COM、不需 Outlook）。

跑法：
    cd backend
    .venv\\Scripts\\python -m unittest tests.test_outlook_helpers -v

涵蓋：
- _translate_com_error 例外訊息翻譯
- _resolve_user_path 相對 / 絕對 / ~ 解析
- _resolve_prev_output 從 prev_outputs 取最後一個 path
- _substitute_prev_in_attachments {prev_output} 展開
- win32_helpers _FLAG_ALIASES 中英文別名
- download_attachments 副檔名正規化（透過模擬呼叫驗證 set 結果）

不涵蓋（需要 COM）：
- search_mail / send_mail / move_mail / mark_read / set_flag 實際呼叫
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 把 backend 加進 sys.path，讓 import 可以走 pipeline.xxx
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestTranslateComError(unittest.TestCase):
    def setUp(self):
        from pipeline.outlook_templates import _translate_com_error
        self.translate = _translate_com_error

    def test_unknown_name_hresult(self):
        e = Exception("(-2147352570, 'Unknown name.', None, None)")
        out = self.translate(e)
        self.assertIn("Classic Outlook", out)

    def test_rpc_unavailable(self):
        e = Exception("(-2147023174, 'RPC_S_SERVER_UNAVAILABLE', None, None)")
        out = self.translate(e)
        self.assertIn("Outlook 程序", out)

    def test_access_denied(self):
        e = Exception("Access is denied.")
        out = self.translate(e)
        self.assertIn("拒絕存取", out)

    def test_no_such_item(self):
        e = Exception("(-2147217406, 'no such item', None, None)")
        out = self.translate(e)
        self.assertIn("EntryID", out)

    def test_unknown_error_passes_through(self):
        e = ValueError("something obscure")
        out = self.translate(e)
        self.assertIn("ValueError", out)
        self.assertIn("something obscure", out)


class TestResolveUserPath(unittest.TestCase):
    def setUp(self):
        from pipeline.outlook_templates import _resolve_user_path, _PROJ_ROOT
        self.resolve = _resolve_user_path
        self.proj = _PROJ_ROOT

    def test_absolute_path_unchanged(self):
        abs_path = "C:/some/abs/path.xlsx"
        out = self.resolve(abs_path)
        self.assertTrue(out.is_absolute())
        # 不應加上 PROJ_ROOT
        self.assertNotIn(str(self.proj), str(out).replace(abs_path.replace('/', '\\'), ''))

    def test_relative_path_joined_with_project_root(self):
        out = self.resolve("ai_output/x/y.xlsx")
        self.assertTrue(out.is_absolute())
        self.assertTrue(str(out).startswith(str(self.proj)))
        self.assertTrue(str(out).endswith("y.xlsx"))

    def test_home_expansion(self):
        out = self.resolve("~/foo.txt")
        self.assertTrue(out.is_absolute())
        # 不應在 proj root 下
        self.assertNotIn(str(self.proj), str(out))


class TestResolvePrevOutput(unittest.TestCase):
    def setUp(self):
        from pipeline.outlook_templates import _resolve_prev_output, _PROJ_ROOT
        self.resolve = _resolve_prev_output
        self.proj = _PROJ_ROOT

    def test_none_returns_none(self):
        self.assertIsNone(self.resolve(None))
        self.assertIsNone(self.resolve([]))

    def test_returns_last_with_path(self):
        prev = [
            {"path": "ai_output/a.xlsx"},
            {"path": "ai_output/b.xlsx"},
            {"schema": "no path here"},
        ]
        out = self.resolve(prev)
        self.assertIsNotNone(out)
        # 跳過沒 path 的、回最後一個有 path 的
        self.assertTrue(out.endswith("b.xlsx"))
        # 已正規化成絕對路徑
        self.assertTrue(Path(out).is_absolute())

    def test_returns_none_when_no_path(self):
        prev = [{"schema": "x"}, {"foo": "bar"}]
        self.assertIsNone(self.resolve(prev))


class TestSubstitutePrevInAttachments(unittest.TestCase):
    def setUp(self):
        from pipeline.outlook_templates import _substitute_prev_in_attachments
        self.sub = _substitute_prev_in_attachments

    def test_empty_returns_empty(self):
        self.assertEqual(self.sub("", None), [])
        self.assertEqual(self.sub(None, None), [])

    def test_string_split_by_newline(self):
        out = self.sub("/a/b.txt\n/c/d.txt", None)
        self.assertEqual(len(out), 2)
        self.assertTrue(any("b.txt" in p for p in out))
        self.assertTrue(any("d.txt" in p for p in out))

    def test_list_input(self):
        out = self.sub(["/a/b.txt", "/c/d.txt"], None)
        self.assertEqual(len(out), 2)

    def test_prev_output_substitution(self):
        prev = [{"path": "ai_output/result.xlsx"}]
        out = self.sub("{prev_output}", prev)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].endswith("result.xlsx"))
        self.assertTrue(Path(out[0]).is_absolute())

    def test_prev_output_missing_raises(self):
        from pipeline.outlook_templates import OutlookTemplateError
        with self.assertRaises(OutlookTemplateError):
            self.sub("{prev_output}", None)


class TestFlagAliases(unittest.TestCase):
    """測試 win32_helpers.outlook._FLAG_ALIASES — 中英文 + 大小寫支援。"""

    def setUp(self):
        from pipeline.win32_helpers.outlook import _FLAG_ALIASES
        self.aliases = _FLAG_ALIASES

    def test_follow_up_variants(self):
        # FlagStatus = 2 (olFlagMarked)
        self.assertEqual(self.aliases["follow_up"], 2)
        self.assertEqual(self.aliases["marked"], 2)
        self.assertEqual(self.aliases["追蹤"], 2)
        self.assertEqual(self.aliases["旗標"], 2)

    def test_complete_variants(self):
        self.assertEqual(self.aliases["complete"], 1)
        self.assertEqual(self.aliases["done"], 1)
        self.assertEqual(self.aliases["完成"], 1)

    def test_clear_variants(self):
        self.assertEqual(self.aliases["clear"], 0)
        self.assertEqual(self.aliases["none"], 0)
        self.assertEqual(self.aliases["off"], 0)
        self.assertEqual(self.aliases["清除"], 0)
        self.assertEqual(self.aliases["取消"], 0)


class TestDirectAndPrefetchHandlers(unittest.TestCase):
    """確認 P0 / P1 加進來的 handler 都註冊成功。"""

    def test_direct_handlers_registered(self):
        from pipeline.outlook_templates import DIRECT_HANDLERS, is_direct_template
        # P0 / P1 加的
        for tid in ("bulk_move", "bulk_mark_read", "bulk_set_flag"):
            self.assertIn(tid, DIRECT_HANDLERS)
            self.assertTrue(is_direct_template(tid))
        # 既有的（保留）
        for tid in ("daily_todo", "send_mail", "send_with_attachment",
                    "bulk_send", "download_attachments"):
            self.assertIn(tid, DIRECT_HANDLERS)
        # 移除的（行事曆）
        for tid in ("calendar_list", "create_meeting"):
            self.assertNotIn(tid, DIRECT_HANDLERS)

    def test_prefetch_handlers_registered(self):
        from pipeline.outlook_templates import LLM_PREFETCH_HANDLERS, has_prefetch
        # P0 加的
        self.assertIn("unanswered", LLM_PREFETCH_HANDLERS)
        self.assertTrue(has_prefetch("unanswered"))
        # 既有
        self.assertIn("search_summary", LLM_PREFETCH_HANDLERS)


class TestBuildCleanSuccessStdout(unittest.TestCase):
    """validator stale-output fix 的助手函式（之前 commit 的）。"""

    def setUp(self):
        from pipeline.executor import _build_clean_success_stdout
        self.fn = _build_clean_success_stdout

    def test_keeps_only_last_tool_result_plus_done(self):
        all_stdout = [
            "[run_python] [stderr] Traceback iter 1",
            "[run_python] [stderr] Traceback iter 2",
            "[run_python] success: produced result.xlsx",
            "[Skill 完成] 已成功",
        ]
        out = self.fn(all_stdout, "[Skill 完成]")
        self.assertNotIn("iter 1", out)
        self.assertNotIn("iter 2", out)
        self.assertIn("success: produced", out)
        self.assertIn("[Skill 完成]", out)

    def test_no_done_marker_falls_back_to_full(self):
        all_stdout = ["[run_python] [stderr] err1", "[run_python] no done"]
        out = self.fn(all_stdout, "[Skill 完成]")
        # 沒 done marker → 回完整
        self.assertIn("err1", out)

    def test_empty_returns_empty(self):
        self.assertEqual(self.fn([], "[Skill 完成]"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
