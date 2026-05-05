"""
【工具 B】產線品質測試資料清洗
製作者：產線品保部門 (QA Team)

用途：清洗 Stage 1 的原始測試資料：
      - 剔除設備校正異常 (Calibration_Drift) 的紀錄
      - 修正單位欄錯誤（_typo 後綴）
      - 移除完全重複（同 Serial + Test_Item）
      - 缺值標 Status="Pending" 不剔除（後續可手動補測）
      - 重新計算 Result（避免原始檔結果欄錯誤）
      - 補上 Quarter / Week 等分析欄位

輸入：~/ai_output/qa_test/raw_test_records.xlsx
輸出：~/ai_output/qa_test/cleaned_test_records.xlsx
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
            os.path.join(env_run_dir, "raw_test_records.xlsx"),
            os.path.join(env_run_dir, "cleaned_test_records.xlsx"),
        )
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return (
            os.path.join(base_path, "qa_test", "raw_test_records.xlsx"),
            os.path.join(base_path, "qa_test", "cleaned_test_records.xlsx"),
        )
    project_root = Path(__file__).parent.parent.parent
    return (
        os.path.join(project_root, "ai_output", "qa_test", "raw_test_records.xlsx"),
        os.path.join(project_root, "ai_output", "qa_test", "cleaned_test_records.xlsx"),
    )


INPUT, OUTPUT = get_paths()

if not os.path.exists(INPUT):
    print(f"[ERROR] 找不到輸入檔案：{INPUT}")
    print("        請先執行 stage1_generate_test_records.py")
    sys.exit(1)

# ── 讀檔 ────────────────────────────────────────────────────────────
df = pd.read_excel(INPUT)
n_raw = len(df)

# ── 1. 剔除設備校正異常 ─────────────────────────────────────────────
mask_drift = df["Equipment_Status"] == "Calibration_Drift"
n_drift = int(mask_drift.sum())
df = df[~mask_drift].copy()

# ── 2. 修正單位 typo ────────────────────────────────────────────────
typo_mask = df["Unit"].astype(str).str.endswith("_typo")
n_typo = int(typo_mask.sum())
df.loc[typo_mask, "Unit"] = df.loc[typo_mask, "Unit"].str.replace("_typo", "", regex=False)
# 並做大小寫修正（mm/g/Ω/°C/V）
unit_canon = {
    "mm": "mm", "g": "g", "Ω": "Ω", "°c": "°C", "v": "V",
}
df["Unit"] = df["Unit"].astype(str).map(lambda u: unit_canon.get(u.lower(), u))

# ── 3. 標記缺值（Pending），不剔除 ──────────────────────────────────
n_missing = int(df["Measured_Value"].isna().sum())
df["Status"] = df["Measured_Value"].apply(lambda v: "Pending" if pd.isna(v) else "Tested")

# ── 4. 移除重複（同 Serial + Test_Item，保留第一筆）─────────────────
n_before_dedup = len(df)
df = df.drop_duplicates(subset=["Serial_Number", "Test_Item"], keep="first").copy()
n_dup = n_before_dedup - len(df)

# ── 5. 重算 Result（量測值有 → 算規格內外；缺值維持 None）──────────
def calc_result(row):
    v = row["Measured_Value"]
    if pd.isna(v):
        return None
    return "PASS" if row["Spec_Low"] <= v <= row["Spec_High"] else "FAIL"


df["Result"] = df.apply(calc_result, axis=1)

# ── 6. 補上時間維度欄 ──────────────────────────────────────────────
df["Test_Date"] = pd.to_datetime(df["Test_Date"])
df["Year"] = df["Test_Date"].dt.year
df["Month"] = df["Test_Date"].dt.month
df["Week"] = df["Test_Date"].dt.isocalendar().week.astype(int)
df["Quarter"] = "Q" + ((df["Month"] - 1) // 3 + 1).astype(str)

# ── 7. 計算量測值偏離規格中心的程度（後續分析用） ──────────────────
def deviation(row):
    v = row["Measured_Value"]
    if pd.isna(v):
        return None
    center = (row["Spec_Low"] + row["Spec_High"]) / 2
    half_range = (row["Spec_High"] - row["Spec_Low"]) / 2
    if half_range == 0:
        return None
    return round((v - center) / half_range, 3)  # -1~+1 落在規格內


df["Spec_Deviation"] = df.apply(deviation, axis=1)

# ── 整理欄位順序輸出 ────────────────────────────────────────────────
ordered_cols = [
    "Record_ID", "Test_Date", "Year", "Quarter", "Month", "Week",
    "Batch", "Production_Line", "Serial_Number",
    "Test_Item", "Spec_Low", "Spec_High", "Unit",
    "Measured_Value", "Spec_Deviation", "Status", "Result",
    "Operator", "Equipment_Status",
]
df = df[[c for c in ordered_cols if c in df.columns]]

df.to_excel(OUTPUT, index=False)

# ── 摘要 ────────────────────────────────────────────────────────────
n_clean = len(df)
n_pass = int((df["Result"] == "PASS").sum())
n_fail = int((df["Result"] == "FAIL").sum())
n_pending = int((df["Status"] == "Pending").sum())

print(f"[Stage 2] ✅ 資料清洗完成")
print(f"  原始：{n_raw} 筆")
print(f"  剔除設備異常：{n_drift} 筆")
print(f"  修正單位 typo：{n_typo} 筆")
print(f"  移除重複：{n_dup} 筆")
print(f"  缺值（Pending）：{n_pending} 筆")
print(f"  最終乾淨資料：{n_clean} 筆（PASS {n_pass} / FAIL {n_fail}）")
print(f"  輸出：{OUTPUT}")
