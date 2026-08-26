import sys, time, json
sys.stdout.reconfigure(encoding="utf-8")
import httpx
from yaml_to_canvas import yaml_to_canvas
from db import create_workflow, get_workflow

API = "http://127.0.0.1:8004"
CSV = "C:/Users/G635LXG/Atlas/pipeline-orchestratorV5/demo_data/sales_2026Q1.csv"
NAME = "銷售分析報表 (AI生成) (demo)"

YAML = f"""name: {NAME}
validate: true

steps:
  - name: 讀取與清洗銷售資料
    skill_mode: true
    batch: |
      讀取銷售資料 CSV:{CSV}
      (UTF-8-BOM 編碼,欄位:order_id, date, region, category, qty, unit_price, amount)。
      用 pandas 清洗:date 轉成日期、qty/unit_price/amount 轉成數值、
      移除任一欄位有缺值或 amount<=0 的列。
      把清洗後的完整資料(保留所有欄位)寫出到 cleaned.csv。
    output:
      path: cleaned.csv
    timeout: 300
  - name: 計算營收分析
    skill_mode: true
    batch: |
      讀取上一步的 cleaned.csv。用 pandas 計算以下分析,輸出成單一 JSON 物件到 analysis.json:
        - total_amount:總營收(整數)
        - total_orders:總訂單數(整數)
        - by_month:每月營收物件,key 為 "YYYY-MM"、value 為該月營收整數,依月份由小到大排序
        - by_region:各地區營收與佔比的陣列,每筆 {{"region":..,"amount":整數,"pct":佔總營收百分比保留一位小數}},依 amount 由大到小
        - by_category:各品類營收與佔比的陣列,每筆 {{"category":..,"amount":整數,"pct":..}},依 amount 由大到小
      analysis.json 必須是純 JSON、不要任何說明文字。
    output:
      path: analysis.json
    timeout: 300
  - name: 產出HTML分析報表
    skill_mode: true
    batch: |
      讀取上一步的 analysis.json。產出一份繁體中文、自包含(內嵌 CSS、可直接用瀏覽器開)的 HTML 分析報表到 report.html:
        - 大標題:2026 第一季 銷售分析報表
        - 頂部兩張摘要卡:總營收、總訂單數
        - 「月度營收」表格(月份、營收)
        - 「各地區營收排名」表格(地區、營收、佔比%)
        - 「各品類營收排名」表格(品類、營收、佔比%)
      所有金額用千分位逗號;版面乾淨專業、表格有框線與標題底色。
    output:
      path: report.html
    timeout: 300
"""

canvas = yaml_to_canvas(YAML)
wf = create_workflow(name=NAME, canvas=canvas, validate=True)
wid = wf["id"] if isinstance(wf, dict) else wf
print("workflow_id:", wid, flush=True)
open("_demo_wid.txt", "w").write(str(wid))

# 首次完整模式跑(use_recipe:false → LLM 寫 code、會有成本)
print("=== 首次完整模式執行(LLM 生成程式碼)===", flush=True)
r = httpx.post(f"{API}/pipeline/run", json={
    "yaml_content": YAML, "workflow_id": wid, "use_recipe": False, "validate": True,
}, timeout=30)
run = r.json(); rid = run.get("run_id")
print("run_id:", rid, flush=True)
deadline = time.time() + 600
last = ""
while time.time() < deadline:
    time.sleep(4)
    d = httpx.get(f"{API}/pipeline/runs/{rid}", timeout=15).json()
    st = d.get("status")
    if st != last:
        print("  status:", st, flush=True); last = st
    if st in ("completed", "failed", "aborted", "awaiting_human"):
        srs = d.get("step_results", [])
        tot = 0
        for sr in srs:
            tu = sr.get("token_usage") or {}
            t = tu.get("total_tokens", 0)
            tot += t
            print(f"   step {sr.get('name')}: {sr.get('validation_status')} | tokens={t}", flush=True)
        print("首次總 tokens:", tot, "| 終態:", st, flush=True)
        if st == "awaiting_human":
            print("AWAITING:", d.get("awaiting_type"), d.get("awaiting_suggestion"), flush=True)
        break
