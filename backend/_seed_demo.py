# -*- coding: utf-8 -*-
import sys, os, json, subprocess
sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
WID = open(os.path.join(BACKEND, "_demo_wid.txt")).read().strip()
CSV = "C:/Users/G635LXG/Atlas/pipeline-orchestratorV5/demo_data/sales_2026Q1.csv"

STEP1 = r'''
import pandas as pd, json
CSV = r"C:/Users/G635LXG/Atlas/pipeline-orchestratorV5/demo_data/sales_2026Q1.csv"
df = pd.read_csv(CSV, encoding="utf-8-sig")
df["date"] = pd.to_datetime(df["date"], errors="coerce")
for c in ["qty","unit_price","amount"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["date","amount"])
df = df[df["amount"] > 0]
df["ym"] = df["date"].dt.strftime("%Y-%m")
by_month = df.groupby("ym")["amount"].sum()
out = {"by_month": {k: int(v) for k, v in sorted(by_month.items())},
       "total_amount": int(df["amount"].sum()), "total_orders": int(len(df))}
with open("monthly_revenue.json","w",encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("monthly_revenue.json ok", out["total_amount"], out["total_orders"])
'''

STEP2 = r'''
import pandas as pd, json
CSV = r"C:/Users/G635LXG/Atlas/pipeline-orchestratorV5/demo_data/sales_2026Q1.csv"
df = pd.read_csv(CSV, encoding="utf-8-sig")
df["date"] = pd.to_datetime(df["date"], errors="coerce")
for c in ["qty","unit_price","amount"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["amount"])
df = df[df["amount"] > 0]
total = float(df["amount"].sum())
def rank(col):
    g = df.groupby(col)["amount"].sum().sort_values(ascending=False)
    return [{col: k, "amount": int(v), "pct": round(v/total*100, 1)} for k, v in g.items()]
out = {"by_region": rank("region"), "by_category": rank("category")}
with open("category_analysis.json","w",encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("category_analysis.json ok")
'''

STEP3 = r'''
import json
m = json.load(open("monthly_revenue.json", encoding="utf-8"))
c = json.load(open("category_analysis.json", encoding="utf-8"))
def fmt(v): return f"{int(v):,}"
rm = "".join(f"<tr><td>{k}</td><td>{fmt(v)}</td></tr>" for k, v in m["by_month"].items())
rr = "".join(f"<tr><td>{x['region']}</td><td>{fmt(x['amount'])}</td><td>{x['pct']}%</td></tr>" for x in c["by_region"])
rc = "".join(f"<tr><td>{x['category']}</td><td>{fmt(x['amount'])}</td><td>{x['pct']}%</td></tr>" for x in c["by_category"])
html = f"""<!DOCTYPE html><html lang="zh-TW"><head><meta charset="utf-8"><title>2026 第一季 銷售分析報表</title>
<style>body{{font-family:'Microsoft JhengHei',sans-serif;margin:40px;background:#f4f7f6}}h1{{text-align:center;color:#222}}
.cards{{display:flex;gap:20px;justify-content:center;margin:24px 0}}.card{{background:#fff;padding:20px 30px;border-radius:10px;box-shadow:0 2px 6px rgba(0,0,0,.1);text-align:center}}
.card h3{{margin:0;color:#666;font-size:14px}}.card p{{margin:8px 0 0;font-size:26px;font-weight:bold;color:#007bff}}
table{{width:100%;border-collapse:collapse;margin:0 0 36px;background:#fff}}th,td{{border:1px solid #ddd;padding:11px;text-align:center}}
th{{background:#007bff;color:#fff}}tr:nth-child(even){{background:#f9f9f9}}h2{{color:#333}}</style></head><body>
<h1>2026 第一季 銷售分析報表</h1>
<div class="cards"><div class="card"><h3>總營收</h3><p>{fmt(m['total_amount'])}</p></div>
<div class="card"><h3>總訂單數</h3><p>{fmt(m['total_orders'])}</p></div></div>
<h2>月度營收</h2><table><tr><th>月份</th><th>營收</th></tr>{rm}</table>
<h2>各地區營收排名</h2><table><tr><th>地區</th><th>營收</th><th>佔比%</th></tr>{rr}</table>
<h2>各品類營收排名</h2><table><tr><th>品類</th><th>營收</th><th>佔比%</th></tr>{rc}</table>
</body></html>"""
with open("sales_report.html", "w", encoding="utf-8") as f:
    f.write(html)
print("sales_report.html ok")
'''

M_KEYS = ["by_month", "total_amount", "total_orders"]
C_KEYS = ["by_region", "by_category"]
spec = {
    "backend_dir": BACKEND,
    "workflow_id": WID,
    "step_num_base": 1,
    "steps": [
        {"index": 0, "code": STEP1, "output_path": "monthly_revenue.json", "inputs": []},
        {"index": 1, "code": STEP2, "output_path": "category_analysis.json",
         "inputs": [{"path": "monthly_revenue.json", "schema": {"type": "json_obj", "keys": M_KEYS}}]},
        {"index": 2, "code": STEP3, "output_path": "sales_report.html",
         "inputs": [{"path": "monthly_revenue.json", "schema": {"type": "json_obj", "keys": M_KEYS}},
                    {"path": "category_analysis.json", "schema": {"type": "json_obj", "keys": C_KEYS}}]},
    ],
}
sp = os.path.join(BACKEND, "_demo_spec.json")
json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("spec written:", sp, "| wid", WID)
SEED = r"C:/Users/G635LXG/.claude/skills/atlas-assistant/seed_recipe.py"
r = subprocess.run([sys.executable, SEED, sp], capture_output=True, text=True, encoding="utf-8")
print(r.stdout); print(r.stderr[-500:] if r.returncode else "")
