# -*- coding: utf-8 -*-
import sys, os, subprocess, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db, httpx
from yaml_to_canvas import yaml_to_canvas

NAME = "銷售資料分析_2026Q1"
CSV = "C:/Users/G635LXG/Atlas/pipeline-orchestratorV5/demo_data/sales_2026Q1.csv"

# 先清掉同名/sales 舊工作流
for w in httpx.get("http://127.0.0.1:8004/workflows", timeout=10).json():
    if "銷售" in w["name"] or "sales" in w["name"].lower():
        try: db.delete_workflow_recipes(w["id"])
        except Exception: pass
        httpx.delete(f"http://127.0.0.1:8004/workflows/{w['id']}?cascade=true", timeout=10)

YAML = f"""name: {NAME}
validate: true

steps:
  - name: 資料清洗與月度營收計算
    skill_mode: true
    batch: |
      讀取銷售資料 {CSV}(UTF-8-BOM,欄位 order_id,date,region,category,qty,unit_price,amount)。
      用 pandas 清洗(date 轉日期、數值轉型、移除缺值與 amount<=0),
      計算每月營收與總計,輸出 monthly_revenue.json。
    output:
      path: monthly_revenue.json
  - name: 地區與品類佔比分析
    skill_mode: true
    batch: |
      讀取銷售資料 {CSV}。用 pandas 計算各地區(region)與各品類(category)的營收與佔比,
      依營收由大到小排序,輸出 category_analysis.json。
    output:
      path: category_analysis.json
  - name: 產出HTML分析報表
    skill_mode: true
    batch: |
      讀取 monthly_revenue.json 與 category_analysis.json,
      產出一份自包含 HTML 銷售分析報表 sales_report.html(摘要卡 + 月度/地區/品類表格)。
    output:
      path: sales_report.html
"""

canvas = yaml_to_canvas(YAML)
wf = db.create_workflow(name=NAME, canvas=canvas, validate=True)
wid = wf["id"] if isinstance(wf, dict) else wf
db.update_workflow(wid, {"yaml": YAML, "canvas": canvas})
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_demo_wid.txt"), "w").write(str(wid))
print("created workflow:", wid, NAME)

# seed 正確 recipe
r = subprocess.run([sys.executable, "_seed_demo.py"], capture_output=True, text=True, encoding="utf-8")
print(r.stdout[-400:]);
if r.returncode: print("SEED ERR", r.stderr[-400:])

# 驗證 API 快速模式 = 0
y = db.get_workflow(wid)["yaml"]
rr = httpx.post("http://127.0.0.1:8004/pipeline/run", json={"yaml_content": y, "workflow_id": wid, "use_recipe": True, "validate": False, "no_save_recipe": True}, timeout=30)
rid = rr.json().get("run_id")
for _ in range(60):
    time.sleep(3); d = httpx.get(f"http://127.0.0.1:8004/pipeline/runs/{rid}", timeout=15).json()
    if d["status"] in ("completed", "failed", "aborted"):
        tot = sum((sr.get("token_usage") or {}).get("total_tokens", 0) for sr in d.get("step_results", []))
        print("快速模式驗證:", d["status"], "| 總tokens", tot)
        break
