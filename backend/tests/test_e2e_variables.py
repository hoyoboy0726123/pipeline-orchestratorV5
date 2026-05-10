"""端對端變數系統測試 — Ticket 1g。

不隔離 DB(env vars 在 config.py import 時已 frozen),改用真實 DB + cleanup。
"""
import sys
import time
import unittest
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)


class DryRunEndpointTests(unittest.TestCase):
    """測 POST /pipeline/dry-run — 不執行、只 render、不需 DB。"""

    YAML_LINEAR = """
pipeline:
  name: dry_run_linear
  steps:
    - name: fetch
      batch: "python a.py --date {{ input.date }}"
      output:
        path: "ai_output/{{ input.date }}/raw.csv"
    - name: clean
      batch: "python b.py --in {{ steps.fetch.output.path }} --out final.xlsx"
    - name: notify
      batch: "echo 完成 {{ input.date }} 跟 {{ steps.clean.output.path }}"
      output:
        path: "final.xlsx"
"""

    def test_dry_run_with_full_inputs(self):
        r = client.post("/pipeline/dry-run", json={
            "yaml_content": self.YAML_LINEAR,
            "input_params": {"date": "2026-05-10"},
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"], f"預期全 OK,但有錯:{[s['errors'] for s in data['steps']]}")
        steps = data["steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["rendered"]["batch"], "python a.py --date 2026-05-10")
        self.assertEqual(steps[0]["rendered"]["output_path"], "ai_output/2026-05-10/raw.csv")
        # step2 引用 step1 的 output.path(從 step1 rendered 鏈式餵給 step2)
        self.assertIn("ai_output/2026-05-10/raw.csv", steps[1]["rendered"]["batch"])
        # step3 雙重引用
        self.assertIn("2026-05-10", steps[2]["rendered"]["batch"])

    def test_dry_run_undefined_input(self):
        r = client.post("/pipeline/dry-run", json={
            "yaml_content": self.YAML_LINEAR,
            "input_params": {},  # 故意不帶 date
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["ok"])
        all_errors = [e for s in data["steps"] for e in s["errors"]]
        self.assertTrue(any("date" in e for e in all_errors),
                        f"預期看到 input.date 的錯誤,實際:{all_errors}")

    def test_dry_run_no_variables_passthrough(self):
        """workflow 沒寫任何 {{ }} 應該照舊跑、不報錯"""
        plain_yaml = """
pipeline:
  name: plain_no_var
  steps:
    - name: a
      batch: "echo hello"
    - name: b
      batch: "echo world"
"""
        r = client.post("/pipeline/dry-run", json={"yaml_content": plain_yaml, "input_params": {}})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        for s in data["steps"]:
            # 沒寫 {{ }} 的 step,rendered 應該是空的、errors 空、referenced_vars 空
            self.assertEqual(s["rendered"], {})
            self.assertEqual(s["errors"], [])
            self.assertEqual(s["referenced_vars"], [])


class WorkflowVariablesEndpointTests(unittest.TestCase):
    """測 GET /workflows/{id}/variables — 用真實 DB 但 unique name + cleanup。"""

    @classmethod
    def setUpClass(cls):
        cls.wf_name = f"_t1g_test_{uuid.uuid4().hex[:8]}"
        # 1. 建 workflow
        r = client.post("/workflows", json={"name": cls.wf_name, "canvas": {"nodes": [], "edges": []}})
        assert r.status_code == 200, f"建 workflow 失敗:{r.text}"
        cls.wf_id = r.json()["id"]
        # 2. 寫 YAML
        yaml_str = """
pipeline:
  name: t1g_var_test
  steps:
    - name: fetch
      batch: "python a.py {{ input.customer }} {{ input.date }}"
    - name: process
      batch: "python b.py --in {{ steps.fetch.output.path }}"
"""
        r = client.put(f"/workflows/{cls.wf_id}", json={"yaml": yaml_str})
        assert r.status_code == 200

    @classmethod
    def tearDownClass(cls):
        try:
            client.delete(f"/workflows/{cls.wf_id}?cascade=true")
        except Exception:
            pass

    def test_variables_endpoint_returns_correct_data(self):
        r = client.get(f"/workflows/{self.wf_id}/variables")
        self.assertEqual(r.status_code, 200)
        data = r.json()

        # referenced 包含這 3 個
        ref_set = set(data["referenced"])
        self.assertIn("input.customer", ref_set)
        self.assertIn("input.date", ref_set)
        self.assertIn("steps.fetch.output.path", ref_set)

        # input 列表 = customer + date
        input_keys = {i["key"] for i in data["available"]["input"]}
        self.assertEqual(input_keys, {"customer", "date"})

        # steps 列表 = fetch + process
        step_names = {s["name"] for s in data["available"]["steps"]}
        self.assertEqual(step_names, {"fetch", "process"})


class RunRecordCapturesInputParamsTests(unittest.TestCase):
    """POST /pipeline/run 接受 input_params + 寫進 run record。

    注:不驗證實際執行結果(TestClient 不跑 background asyncio task);
    render 行為由 test_expression.py 23 unit + DryRunEndpointTests 3 個
    cover 完整。這裡只驗 input_params 真的進到 PipelineRun.input_params。
    """

    def test_input_params_persisted_in_run(self):
        yaml_str = """
pipeline:
  name: e2e_t1g_run
  validate: false
  steps:
    - name: echo1
      batch: "echo {{ input.date }}"
"""
        r = client.post("/pipeline/run", json={
            "yaml_content": yaml_str,
            "validate": False,
            "use_recipe": False,
            "input_params": {"date": "2026-05-10", "customer": "ASUS"},
        })
        self.assertEqual(r.status_code, 200)
        run_id = r.json()["run_id"]
        # 拉 run record 驗 input_params
        r = client.get(f"/pipeline/runs/{run_id}")
        self.assertEqual(r.status_code, 200)
        run = r.json()
        self.assertEqual(run.get("input_params", {}).get("date"), "2026-05-10")
        self.assertEqual(run.get("input_params", {}).get("customer"), "ASUS")
        # cleanup(中止 + 刪)
        try:
            client.post(f"/pipeline/runs/{run_id}/abort")
        except Exception:
            pass
        try:
            client.delete(f"/pipeline/runs/{run_id}")
        except Exception:
            pass

    def test_run_without_input_params_still_works(self):
        """沒帶 input_params 的舊 workflow 行為不變。"""
        yaml_str = """
pipeline:
  name: e2e_t1g_no_inputs
  validate: false
  steps:
    - name: echo1
      batch: "echo plain"
"""
        r = client.post("/pipeline/run", json={
            "yaml_content": yaml_str,
            "validate": False,
            "use_recipe": False,
        })
        self.assertEqual(r.status_code, 200)
        run_id = r.json()["run_id"]
        r = client.get(f"/pipeline/runs/{run_id}")
        self.assertEqual(r.status_code, 200)
        # input_params 預設空 dict
        self.assertEqual(r.json().get("input_params", None), {})
        try:
            client.post(f"/pipeline/runs/{run_id}/abort")
        except Exception:
            pass
        try:
            client.delete(f"/pipeline/runs/{run_id}")
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
