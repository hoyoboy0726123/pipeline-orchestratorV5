"""
【工具 B】電商銷售訂單清洗
製作者：電商營運部 (E-commerce Ops)

用途：清洗 Stage 1 的原始訂單：
      - 移除取消（Cancelled）/ 退款（Refunded）訂單
      - 移除完全重複（同 Order_ID 簽章 / 客戶+商品+時間+金額 完全一致）
      - 剔除金額異常（< 0 或 > 1,000,000）
      - 缺值補上「Unknown」(Region) / 標記匿名 (Customer_ID)
      - 補時間維度欄位

輸入：~/ai_output/ecommerce/raw_orders.xlsx
輸出：~/ai_output/ecommerce/cleaned_orders.xlsx
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
            os.path.join(env_run_dir, "raw_orders.xlsx"),
            os.path.join(env_run_dir, "cleaned_orders.xlsx"),
        )
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return (
            os.path.join(base_path, "ecommerce", "raw_orders.xlsx"),
            os.path.join(base_path, "ecommerce", "cleaned_orders.xlsx"),
        )
    project_root = Path(__file__).parent.parent.parent
    return (
        os.path.join(project_root, "ai_output", "ecommerce", "raw_orders.xlsx"),
        os.path.join(project_root, "ai_output", "ecommerce", "cleaned_orders.xlsx"),
    )


INPUT, OUTPUT = get_paths()

if not os.path.exists(INPUT):
    print(f"[ERROR] 找不到輸入檔案：{INPUT}")
    print("        請先執行 stage1_generate_orders.py")
    sys.exit(1)

df = pd.read_excel(INPUT)
n_raw = len(df)

# ── 1. 過濾 Cancelled / Refunded（只留 Completed）────────────────
n_cancelled = int((df["Status"] == "Cancelled").sum())
n_refunded = int((df["Status"] == "Refunded").sum())
df = df[df["Status"] == "Completed"].copy()

# ── 2. 異常金額剔除 ────────────────────────────────────────────────
mask_abnormal = (df["Amount"] < 0) | (df["Amount"] > 1_000_000)
n_abnormal = int(mask_abnormal.sum())
df = df[~mask_abnormal].copy()

# ── 3. 移除重複（同 Customer_ID + Product_Name + Order_Time + Amount）─
n_before_dedup = len(df)
df = df.drop_duplicates(
    subset=["Customer_ID", "Product_Name", "Order_Time", "Amount"], keep="first"
).copy()
n_dup = n_before_dedup - len(df)

# ── 4. 補缺值 ──────────────────────────────────────────────────────
n_missing_cust = int(df["Customer_ID"].isna().sum())
df["Customer_ID"] = df["Customer_ID"].fillna("ANONYMOUS")
n_missing_region = int(df["Region"].isna().sum())
df["Region"] = df["Region"].fillna("Unknown")

# ── 5. 時間欄位 ────────────────────────────────────────────────────
df["Order_Time"] = pd.to_datetime(df["Order_Time"])
df["Order_Date"] = df["Order_Time"].dt.date
df["Year"] = df["Order_Time"].dt.year
df["Month"] = df["Order_Time"].dt.month
df["Day"] = df["Order_Time"].dt.day
df["DayOfWeek"] = df["Order_Time"].dt.day_name()
df["Hour"] = df["Order_Time"].dt.hour
# 時段
def to_segment(h):
    if 6 <= h < 12: return "上午"
    if 12 <= h < 14: return "中午"
    if 14 <= h < 18: return "下午"
    if 18 <= h < 22: return "晚上"
    return "深夜"


df["Time_Segment"] = df["Hour"].apply(to_segment)

# ── 6. 換算每筆毛利（簡化：以 35% 毛利率假設）──────────────────────
GROSS_MARGIN = 0.35
df["Gross_Profit"] = (df["Amount"] * GROSS_MARGIN).round(0).astype(int)

# ── 整理欄位順序 ───────────────────────────────────────────────────
ordered_cols = [
    "Order_ID", "Order_Time", "Order_Date", "Year", "Month", "Day",
    "DayOfWeek", "Hour", "Time_Segment",
    "Customer_ID", "Region",
    "Product_Name", "Category", "Quantity", "Unit_Price", "Discount_Rate",
    "Amount", "Gross_Profit",
    "Payment", "Status",
]
df = df[[c for c in ordered_cols if c in df.columns]]

df.to_excel(OUTPUT, index=False)

n_clean = len(df)
total_revenue = int(df["Amount"].sum())
total_profit = int(df["Gross_Profit"].sum())

print(f"[Stage 2] ✅ 訂單清洗完成")
print(f"  原始：{n_raw} 筆")
print(f"  剔除 Cancelled/{n_cancelled} + Refunded/{n_refunded} + 異常金額/{n_abnormal} + 重複/{n_dup}")
print(f"  缺值補正：客戶 ID/{n_missing_cust} 筆改 ANONYMOUS、地區/{n_missing_region} 筆改 Unknown")
print(f"  乾淨資料：{n_clean} 筆")
print(f"  期間總營收：NT$ {total_revenue:,}（毛利 NT$ {total_profit:,}）")
print(f"  輸出：{OUTPUT}")
