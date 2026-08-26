# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from yaml_to_canvas import yaml_to_canvas

wid = open("_demo_wid.txt").read().strip()
wf = db.get_workflow(wid)
y = wf["yaml"]
# 把 step3 batch 的 {{ steps.X.output.path }} 換成固定檔名(避免 runner 展開導致 task_hash 漂移)
y2 = (y.replace("{{ steps.資料清洗與月度營收計算.output.path }}", "monthly_revenue.json")
       .replace("{{ steps.地區與品類佔比分析.output.path }}", "category_analysis.json"))
if y2 == y:
    print("[warn] 沒有替換到 {{ }} —— 檢查 batch 內容")
canvas = yaml_to_canvas(y2)
db.update_workflow(wid, {"canvas": canvas, "yaml": y2})
chk = db.get_workflow(wid)
print("更新後 step3 batch 還有 {{ 嗎:", "{{" in chk["yaml"])
print(chk["yaml"])
