"""手動端對端跑 demo workflow — Ticket 2 驗證腳本。

這個腳本不是 unittest、是給人類看「真的跑起來什麼樣子」用:
- 直接呼叫已啟動的 backend (http://localhost:8004)
- 用各種變數 + condition 組合跑 3 個案例
- 印每個 step 跑出來的 stdout 確認 render + 跳轉真的對

跑法:
    cd backend
    .venv/Scripts/python.exe tests/manual_run_demo.py
"""
import time
import sys
import json
from pathlib import Path

import requests

BASE = "http://localhost:8004"


def run_workflow(name: str, yaml_str: str, input_params: dict, expected_steps: list[str]):
    """啟動 workflow + poll 到完成 + 印每步 stdout、回傳 run record。"""
    print(f"\n{'━' * 70}")
    print(f"🧪 案例:{name}")
    print(f"   input: {input_params}")
    print(f"{'━' * 70}")

    r = requests.post(f"{BASE}/pipeline/run", json={
        "yaml_content": yaml_str,
        "validate": False,
        "use_recipe": False,
        "input_params": input_params,
    })
    if r.status_code != 200:
        print(f"❌ 啟動失敗:{r.status_code} {r.text}")
        return None
    run_id = r.json()["run_id"]
    print(f"   run_id: {run_id}")

    # poll 最多 60 秒
    deadline = time.time() + 60
    last = None
    while time.time() < deadline:
        r = requests.get(f"{BASE}/pipeline/runs/{run_id}")
        last = r.json()
        if last.get("status") in ("completed", "failed", "aborted"):
            break
        time.sleep(0.5)

    print(f"   狀態: {last.get('status')}")
    print(f"   跑了 {len(last.get('step_results', []))} 個 step\n")

    for sr in last.get("step_results", []):
        icon = {"ok": "✅", "failed": "❌"}.get(sr.get("validation_status"), "⚠️")
        print(f"   {icon} {sr['step_name']:20s} exit={sr['exit_code']}")
        stdout = (sr.get("stdout_tail") or "").strip()
        if stdout:
            for line in stdout.split("\n")[:3]:  # 印前 3 行
                print(f"      {line}")
        if sr.get("validation_reason") and sr["validation_status"] != "ok":
            print(f"      原因:{sr['validation_reason']}")

    # 驗證實際跑的 step 跟預期一致
    actual_steps = [sr["step_name"] for sr in last.get("step_results", [])]
    if expected_steps and actual_steps != expected_steps:
        print(f"\n   ⚠️ 跑的 step 跟預期不符!")
        print(f"      預期: {expected_steps}")
        print(f"      實際: {actual_steps}")
    else:
        print(f"\n   ✓ 跑的 step 跟預期一致:{actual_steps}")

    # cleanup
    try:
        requests.delete(f"{BASE}/pipeline/runs/{run_id}")
    except Exception:
        pass
    return last


def main():
    # ── 案例 1:純變數(沒 condition)── 驗證 input + steps.X.output 都展開 ──
    yaml_a = """
pipeline:
  name: t2_case1_vars
  validate: false
  steps:
    - name: fetch
      batch: "echo 抓 {{ input.customer }} 在 {{ input.date }} 的資料"
    - name: process
      batch: "echo 處理 {{ steps.fetch.output.stdout }}"
"""
    run_workflow("案例 1:純變數展開",
                 yaml_a,
                 {"customer": "ASUS", "date": "2026-05-10"},
                 expected_steps=["fetch", "process"])

    # ── 案例 2:IF 分支 — 大資料走 bulk、小走 light ──
    yaml_b = """
pipeline:
  name: t2_case2_if
  validate: false
  steps:
    - name: setup
      batch: "echo setup size={{ input.size }}"
    - name: route
      condition: true
      expression: "{{ input.size | int > 100 }}"
      on_true: bulk
      on_false: light
    - name: bulk
      batch: "echo BULK_PROCESS size={{ input.size }}"
      next: end
    - name: light
      batch: "echo LIGHT_PROCESS size={{ input.size }}"
      next: end
"""
    # case 2a:大資料 → 走 bulk、light 不跑
    run_workflow("案例 2a:IF true(size=200 → bulk)",
                 yaml_b,
                 {"size": "200"},
                 expected_steps=["setup", "route", "bulk"])

    # case 2b:小資料 → 走 light、bulk 不跑
    run_workflow("案例 2b:IF false(size=10 → light)",
                 yaml_b,
                 {"size": "10"},
                 expected_steps=["setup", "route", "light"])

    # ── 案例 3:Switch 多分支 ──
    yaml_c = """
pipeline:
  name: t2_case3_switch
  validate: false
  steps:
    - name: route_action
      condition: true
      switch: "{{ input.action }}"
      cases:
        "fetch": do_fetch
        "process": do_process
        "archive": do_archive
      default: do_unknown
    - name: do_fetch
      batch: "echo FETCH"
      next: end
    - name: do_process
      batch: "echo PROCESS"
      next: end
    - name: do_archive
      batch: "echo ARCHIVE"
      next: end
    - name: do_unknown
      batch: "echo UNKNOWN_DEFAULT"
      next: end
"""
    run_workflow("案例 3a:Switch hit (action=process)",
                 yaml_c,
                 {"action": "process"},
                 expected_steps=["route_action", "do_process"])

    run_workflow("案例 3b:Switch miss → default (action=xyz)",
                 yaml_c,
                 {"action": "xyz"},
                 expected_steps=["route_action", "do_unknown"])

    # ── 案例 4:組合 — 變數 + IF + chained step output ──
    yaml_d = """
pipeline:
  name: t2_case4_combo
  validate: false
  steps:
    - name: count_rows
      batch: "echo 142"
      output:
        path: ai_output/{{ input.date }}_count.txt
    - name: route_size
      condition: true
      expression: "{{ steps.count_rows.output.stdout | int > 100 }}"
      on_true: heavy_path
      on_false: light_path
    - name: heavy_path
      batch: "echo HEAVY date={{ input.date }} count={{ steps.count_rows.output.stdout }}"
      next: notify
    - name: light_path
      batch: "echo LIGHT date={{ input.date }}"
      next: notify
    - name: notify
      batch: "echo notification sent"
"""
    # heavy path:count=142 > 100 → heavy → notify
    run_workflow("案例 4:組合 — 變數 + 上游 stdout 餵下游 condition",
                 yaml_d,
                 {"date": "2026-05-10"},
                 expected_steps=["count_rows", "route_size", "heavy_path", "notify"])

    print("\n" + "═" * 70)
    print("✅ 全部案例跑完")
    print("═" * 70)


if __name__ == "__main__":
    main()
