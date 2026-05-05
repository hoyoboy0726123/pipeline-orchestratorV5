"""
【工具 A】產線品質測試原始資料產生器
製作者：產線品保部門 (QA Team)

用途：模擬電子產品產線 4 個批次的品質測試紀錄，
      包含尺寸、重量、阻抗、溫度耐受、絕緣強度 5 種測項。
      刻意摻入若干髒資料（缺值、超規、設備異常）供清洗階段使用。

輸入：無（資料由程式隨機產生）
輸出：~/ai_output/qa_test/raw_test_records.xlsx
"""
import sys
import io as _io_e2e
try:
    sys.stdout = _io_e2e.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import random
from datetime import datetime, timedelta

import pandas as pd
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")


def get_output_path():
    env_run_dir = os.getenv("PIPELINE_OUTPUT_DIR")
    if env_run_dir:
        return os.path.join(env_run_dir, "raw_test_records.xlsx")
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return os.path.join(base_path, "qa_test", "raw_test_records.xlsx")
    project_root = Path(__file__).parent.parent.parent
    return os.path.join(project_root, "ai_output", "qa_test", "raw_test_records.xlsx")


OUTPUT = get_output_path()
os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

random.seed(42)

# ── 測試項目定義 ────────────────────────────────────────────────────
# (項目名, 規格下限, 規格上限, 量測雜訊 sigma, 單位)
TEST_ITEMS = [
    ("Dimension_Length",   99.5,  100.5, 0.30, "mm"),
    ("Weight",             45.0,   55.0, 1.50, "g"),
    ("Resistance",        100.0,  120.0, 4.00, "Ω"),
    ("Thermal_Tolerance", 65.0,   85.0,  3.00, "°C"),
    ("Insulation_Strength", 1500, 2500,  150,  "V"),
]

BATCHES = ["B-2024Q4-001", "B-2024Q4-002", "B-2024Q4-003", "B-2024Q4-004"]
LINES = ["Line-A", "Line-B", "Line-C"]
OPERATORS = ["OP-001", "OP-002", "OP-003", "OP-004", "OP-005"]
EQUIPMENT_STATUS_CHOICES = ["OK", "OK", "OK", "OK", "OK", "Calibration_Drift"]  # 6:1 比例

UNITS_PER_BATCH = 100  # 每批 100 件 × 4 批 × 5 項 = 2000 筆


def gen_value(spec_low, spec_high, sigma):
    """80% 落在規格內、15% 邊緣、5% 超規"""
    r = random.random()
    center = (spec_low + spec_high) / 2
    if r < 0.80:
        return round(random.gauss(center, sigma), 3)
    elif r < 0.95:
        # 偏離中心更遠（仍多半在規格內）
        return round(random.gauss(center, sigma * 2), 3)
    else:
        # 5% 超規
        if random.random() < 0.5:
            return round(spec_low - abs(random.gauss(0, sigma * 2)) - 0.5, 3)
        else:
            return round(spec_high + abs(random.gauss(0, sigma * 2)) + 0.5, 3)


def in_spec(value, low, high):
    if value is None or pd.isna(value):
        return None
    return low <= value <= high


# ── 產生資料 ────────────────────────────────────────────────────────
records = []
start_date = datetime(2024, 11, 18)

unit_seq = 1
for batch_idx, batch in enumerate(BATCHES):
    line = LINES[batch_idx % len(LINES)]
    test_date = start_date + timedelta(days=batch_idx * 2)

    for unit_in_batch in range(UNITS_PER_BATCH):
        serial = f"SN-{unit_seq:05d}"
        unit_seq += 1
        operator = random.choice(OPERATORS)

        for item_name, low, high, sigma, unit in TEST_ITEMS:
            value = gen_value(low, high, sigma)
            equip_status = random.choice(EQUIPMENT_STATUS_CHOICES)
            result = "PASS" if in_spec(value, low, high) else "FAIL"

            records.append({
                "Record_ID":      f"R-{len(records)+1:05d}",
                "Test_Date":      test_date.strftime("%Y-%m-%d"),
                "Batch":          batch,
                "Production_Line": line,
                "Serial_Number":  serial,
                "Test_Item":      item_name,
                "Spec_Low":       low,
                "Spec_High":      high,
                "Measured_Value": value,
                "Unit":           unit,
                "Result":         result,
                "Operator":       operator,
                "Equipment_Status": equip_status,
            })

# ── 摻髒資料 ────────────────────────────────────────────────────────
# 1. 隨機把 12 筆 Measured_Value 改成 None（缺值）
miss_indices = random.sample(range(len(records)), 12)
for i in miss_indices:
    records[i]["Measured_Value"] = None
    records[i]["Result"] = None  # 量測缺失 → 結果也未知

# 2. 隨機把 8 筆改成單位錯誤（明顯打錯，例：mm 寫成 m）
unit_typo_indices = random.sample(
    [i for i in range(len(records)) if i not in miss_indices], 8
)
for i in unit_typo_indices:
    records[i]["Unit"] = records[i]["Unit"].lower() + "_typo"

# 3. 加 5 筆完全重複（資料異常）
dup_indices = random.sample(range(len(records)), 5)
for i in dup_indices:
    records.append({**records[i], "Record_ID": f"R-DUP-{len(records)}"})

# 4. 加 3 筆設備標記為 Calibration_Drift（之後清洗階段要剔除）
cal_drift_count = 0
for r in records:
    if r["Equipment_Status"] == "Calibration_Drift":
        cal_drift_count += 1

random.shuffle(records)

# 重編 Record_ID 保持遞增
for idx, r in enumerate(records, 1):
    r["Record_ID"] = f"R-{idx:05d}"

# ── 輸出 ────────────────────────────────────────────────────────────
df = pd.DataFrame(records)
df.to_excel(OUTPUT, index=False)

print(f"[Stage 1] ✅ 產生原始測試紀錄完成")
print(f"  總筆數：{len(df)}")
print(f"  批次：{', '.join(BATCHES)}")
print(f"  測項：{len(TEST_ITEMS)} 種")
print(f"  髒資料：缺值 12、單位錯誤 8、重複 5、設備異常 {cal_drift_count} 筆")
print(f"  輸出：{OUTPUT}")
