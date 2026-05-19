"""
【工具 E】成本警示報表產生器
製作者：財務長辦公室 / 管理報表組 (CFO Office)

用途：當財務健診判定「費用率偏高」時使用。讀取財務分析結果，
      挑出最大的幾個費用類別，產生一份精簡的成本警示報表 Excel，
      提醒管理層複查支出。

輸入：~/ai_output/finance/financial_summary.xlsx
輸出：~/ai_output/finance/cost_alert_report.xlsx
"""
import os
import sys
import pandas as pd
from openpyxl.styles import Font
from dotenv import load_dotenv
from pathlib import Path

# 載入 .env 設定
load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")


# ── 路徑設定（與 stage3 / stage4 同一套規則）─────────────────────────────────
def get_paths():
    env_run_dir = os.getenv("PIPELINE_OUTPUT_DIR")
    if env_run_dir:
        return (
            os.path.join(env_run_dir, "financial_summary.xlsx"),
            os.path.join(env_run_dir, "cost_alert_report.xlsx"),
        )
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return (
            os.path.join(base_path, "finance", "financial_summary.xlsx"),
            os.path.join(base_path, "finance", "cost_alert_report.xlsx"),
        )
    project_root = Path(__file__).parent.parent.parent
    return (
        os.path.join(project_root, "ai_output", "finance", "financial_summary.xlsx"),
        os.path.join(project_root, "ai_output", "finance", "cost_alert_report.xlsx"),
    )


INPUT, OUTPUT = get_paths()

if not os.path.exists(INPUT):
    print(f"[ERROR] 找不到輸入檔案：{INPUT}")
    print("        請先執行 stage3_analyze_finance.py")
    sys.exit(1)

# ── 讀取分析結果 ─────────────────────────────────────────────────────────────
kpi = pd.read_excel(INPUT, sheet_name="KPI 總覽")
kpi_map = dict(zip(kpi["指標"], kpi["金額 (USD)"]))
exp_ratio = float(kpi_map.get("費用率", 0))
total_exp = float(kpi_map.get("Q1 總支出", 0))
net_income = float(kpi_map.get("Q1 淨利", 0))

cat = pd.read_excel(INPUT, sheet_name="費用類別排名")
top3 = cat.head(3)

# ── 組成本警示報表 ───────────────────────────────────────────────────────────
overview = pd.DataFrame([
    {"項目": "費用率",    "數值": f"{exp_ratio:.1f}%",        "說明": "總支出 / 總收入"},
    {"項目": "Q1 總支出", "數值": f"USD {total_exp:,.0f}",    "說明": "本季所有支出合計"},
    {"項目": "Q1 淨利",   "數值": f"USD {net_income:,.0f}",   "說明": "負值代表本季虧損"},
])

with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
    overview.to_excel(writer, sheet_name="成本警示", index=False, startrow=2)
    top3.to_excel(writer, sheet_name="最大費用類別 TOP3", index=False)
    ws = writer.sheets["成本警示"]
    ws["A1"] = "⚠ 成本警示報表 — 費用率偏高，建議財務長辦公室複查支出"
    ws["A1"].font = Font(bold=True, size=13, color="C00000")

# ── 終端摘要 ─────────────────────────────────────────────────────────────────
print("=" * 55)
print("Stage 5：成本警示報表產生完成")
print("=" * 55)
print(f"  費用率           : {exp_ratio:.1f}%")
print(f"  Q1 總支出        : USD {total_exp:,.2f}")
print("  最大費用類別 TOP3 :")
for _, row in top3.iterrows():
    name = row.get("Category", "?")
    amt = row.get("支出合計 (USD)", 0)
    print(f"    - {name}: USD {amt:,.2f}")
print(f"  輸出路徑         : {OUTPUT}")
