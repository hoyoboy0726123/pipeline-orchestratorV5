"""
【工具 C】產線品質測試分析彙總
製作者：產線品保部門 (QA Team)

用途：基於清洗後的測試資料計算各種品質 KPI：
      - 整體良率（First Pass Yield）
      - 各批次良率
      - 各測項不良率排行
      - 不良 trend（按週）
      - 各產線/操作員良率

輸入：~/ai_output/qa_test/cleaned_test_records.xlsx
輸出：~/ai_output/qa_test/quality_summary.xlsx（多工作表）
"""
import sys
import io as _io_e2e
try:
    sys.stdout = _io_e2e.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import sys
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")


def get_paths():
    env_run_dir = os.getenv("PIPELINE_OUTPUT_DIR")
    if env_run_dir:
        return (
            os.path.join(env_run_dir, "cleaned_test_records.xlsx"),
            os.path.join(env_run_dir, "quality_summary.xlsx"),
        )
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return (
            os.path.join(base_path, "qa_test", "cleaned_test_records.xlsx"),
            os.path.join(base_path, "qa_test", "quality_summary.xlsx"),
        )
    project_root = Path(__file__).parent.parent.parent
    return (
        os.path.join(project_root, "ai_output", "qa_test", "cleaned_test_records.xlsx"),
        os.path.join(project_root, "ai_output", "qa_test", "quality_summary.xlsx"),
    )


INPUT, OUTPUT = get_paths()

if not os.path.exists(INPUT):
    print(f"[ERROR] 找不到輸入檔案：{INPUT}")
    print("        請先執行 stage2_clean_records.py")
    sys.exit(1)

df = pd.read_excel(INPUT)
# 只用「已測試」資料分析（Pending 排除）
df_tested = df[df["Status"] == "Tested"].copy()


def yield_rate(sub):
    if len(sub) == 0:
        return 0.0
    return round(100 * (sub["Result"] == "PASS").sum() / len(sub), 2)


# ── KPI 總覽 ────────────────────────────────────────────────────────
total_tested = len(df_tested)
total_pass = int((df_tested["Result"] == "PASS").sum())
total_fail = int((df_tested["Result"] == "FAIL").sum())
fpy = yield_rate(df_tested)
total_pending = int((df["Status"] == "Pending").sum())
total_records = len(df)

# 按 Serial 計算「整機通過」（5 項全 PASS 才算通過）
unit_results = (
    df_tested.groupby("Serial_Number")["Result"]
    .apply(lambda s: "PASS" if (s == "PASS").all() else "FAIL")
)
unit_yield = round(100 * (unit_results == "PASS").sum() / len(unit_results), 2) if len(unit_results) else 0

kpi_data = [
    ["總紀錄數",           total_records],
    ["已測試紀錄",         total_tested],
    ["待補測（Pending）",   total_pending],
    ["PASS 數",            total_pass],
    ["FAIL 數",            total_fail],
    ["項目良率 (FPY %)",   fpy],
    ["整機良率 (Unit %)",  unit_yield],
]
kpi_df = pd.DataFrame(kpi_data, columns=["指標", "值"])

# ── 各批次良率 ──────────────────────────────────────────────────────
batch_data = []
for batch, sub in df_tested.groupby("Batch"):
    batch_data.append({
        "Batch":   batch,
        "測試數":   len(sub),
        "PASS":    int((sub["Result"] == "PASS").sum()),
        "FAIL":    int((sub["Result"] == "FAIL").sum()),
        "良率 (%)": yield_rate(sub),
    })
batch_df = pd.DataFrame(batch_data).sort_values("良率 (%)", ascending=False)

# ── 各測項不良率排行 ────────────────────────────────────────────────
item_data = []
for item, sub in df_tested.groupby("Test_Item"):
    fail_count = int((sub["Result"] == "FAIL").sum())
    fail_rate = round(100 * fail_count / len(sub), 2) if len(sub) else 0
    item_data.append({
        "Test_Item":   item,
        "測試數":       len(sub),
        "FAIL":        fail_count,
        "不良率 (%)":   fail_rate,
        "良率 (%)":     yield_rate(sub),
    })
item_df = pd.DataFrame(item_data).sort_values("不良率 (%)", ascending=False)

# ── 各產線良率 ──────────────────────────────────────────────────────
line_data = []
for line, sub in df_tested.groupby("Production_Line"):
    line_data.append({
        "Production_Line": line,
        "測試數":           len(sub),
        "PASS":            int((sub["Result"] == "PASS").sum()),
        "FAIL":            int((sub["Result"] == "FAIL").sum()),
        "良率 (%)":         yield_rate(sub),
    })
line_df = pd.DataFrame(line_data).sort_values("良率 (%)", ascending=False)

# ── 各操作員良率 ────────────────────────────────────────────────────
op_data = []
for op, sub in df_tested.groupby("Operator"):
    op_data.append({
        "Operator": op,
        "測試數":    len(sub),
        "PASS":     int((sub["Result"] == "PASS").sum()),
        "FAIL":     int((sub["Result"] == "FAIL").sum()),
        "良率 (%)":  yield_rate(sub),
    })
op_df = pd.DataFrame(op_data).sort_values("良率 (%)", ascending=False)

# ── 週 trend ────────────────────────────────────────────────────────
df_tested["Test_Date"] = pd.to_datetime(df_tested["Test_Date"])
weekly = (
    df_tested.groupby("Week")
    .apply(lambda s: pd.Series({
        "測試數": len(s),
        "PASS":   int((s["Result"] == "PASS").sum()),
        "FAIL":   int((s["Result"] == "FAIL").sum()),
        "良率 (%)": yield_rate(s),
    }))
    .reset_index()
    .sort_values("Week")
)

# ── 寫多 sheet Excel ────────────────────────────────────────────────
with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
    kpi_df.to_excel(writer,    sheet_name="KPI 總覽",     index=False)
    batch_df.to_excel(writer,  sheet_name="批次別良率",   index=False)
    item_df.to_excel(writer,   sheet_name="測項不良率排行", index=False)
    line_df.to_excel(writer,   sheet_name="產線別良率",   index=False)
    op_df.to_excel(writer,     sheet_name="操作員別良率", index=False)
    weekly.to_excel(writer,    sheet_name="週 trend",     index=False)

print(f"[Stage 3] ✅ 品質分析完成")
print(f"  整體 FPY：{fpy}%")
print(f"  整機良率：{unit_yield}%")
print(f"  輸出（6 個工作表）：{OUTPUT}")
