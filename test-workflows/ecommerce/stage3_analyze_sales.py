"""
【工具 C】電商銷售分析彙總
製作者：電商營運部 (E-commerce Ops)

用途：基於清洗後訂單算 KPI 與多維度切片：
      - 整體營收 / 訂單數 / 客單價 / 毛利
      - 商品類別營收貢獻 + Top 10 暢銷商品
      - 地區別、付款別、時段別、星期別
      - 每日營收趨勢

輸入：~/ai_output/ecommerce/cleaned_orders.xlsx
輸出：~/ai_output/ecommerce/sales_summary.xlsx（多工作表）
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
            os.path.join(env_run_dir, "cleaned_orders.xlsx"),
            os.path.join(env_run_dir, "sales_summary.xlsx"),
        )
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return (
            os.path.join(base_path, "ecommerce", "cleaned_orders.xlsx"),
            os.path.join(base_path, "ecommerce", "sales_summary.xlsx"),
        )
    project_root = Path(__file__).parent.parent.parent
    return (
        os.path.join(project_root, "ai_output", "ecommerce", "cleaned_orders.xlsx"),
        os.path.join(project_root, "ai_output", "ecommerce", "sales_summary.xlsx"),
    )


INPUT, OUTPUT = get_paths()

if not os.path.exists(INPUT):
    print(f"[ERROR] 找不到輸入檔案：{INPUT}")
    print("        請先執行 stage2_clean_orders.py")
    sys.exit(1)

df = pd.read_excel(INPUT)
df["Order_Date"] = pd.to_datetime(df["Order_Date"])

# ── KPI 總覽 ────────────────────────────────────────────────────────
total_orders = len(df)
total_revenue = int(df["Amount"].sum())
total_profit = int(df["Gross_Profit"].sum())
unique_customers = df["Customer_ID"].nunique()
avg_order_value = round(total_revenue / total_orders, 0) if total_orders else 0
profit_margin = round(100 * total_profit / total_revenue, 2) if total_revenue else 0
days = (df["Order_Date"].max() - df["Order_Date"].min()).days + 1
daily_avg_revenue = round(total_revenue / days, 0) if days else 0

kpi_data = [
    ["資料區間",         f"{df['Order_Date'].min().date()} ~ {df['Order_Date'].max().date()}（{days} 天）"],
    ["總訂單數",         total_orders],
    ["獨立顧客數",       unique_customers],
    ["總營收 (NT$)",     total_revenue],
    ["總毛利 (NT$)",     total_profit],
    ["毛利率 (%)",       profit_margin],
    ["客單價 (NT$)",     int(avg_order_value)],
    ["日均營收 (NT$)",   int(daily_avg_revenue)],
]
kpi_df = pd.DataFrame(kpi_data, columns=["指標", "值"])

# ── 各類別營收 ─────────────────────────────────────────────────────
cat_data = []
for cat, sub in df.groupby("Category"):
    rev = int(sub["Amount"].sum())
    cat_data.append({
        "類別":         cat,
        "訂單數":       len(sub),
        "總銷量":       int(sub["Quantity"].sum()),
        "營收 (NT$)":    rev,
        "毛利 (NT$)":    int(sub["Gross_Profit"].sum()),
        "占比 (%)":      round(100 * rev / total_revenue, 2),
    })
cat_df = pd.DataFrame(cat_data).sort_values("營收 (NT$)", ascending=False).reset_index(drop=True)

# ── Top 10 暢銷商品 ────────────────────────────────────────────────
prod_data = []
for prod, sub in df.groupby("Product_Name"):
    prod_data.append({
        "商品":          prod,
        "類別":          sub["Category"].iloc[0],
        "訂單數":        len(sub),
        "銷量":          int(sub["Quantity"].sum()),
        "營收 (NT$)":     int(sub["Amount"].sum()),
        "平均單筆":       int(sub["Amount"].mean()),
    })
top_prod_df = (
    pd.DataFrame(prod_data)
    .sort_values("營收 (NT$)", ascending=False)
    .head(10)
    .reset_index(drop=True)
)

# ── 地區別 ─────────────────────────────────────────────────────────
region_data = []
for r, sub in df.groupby("Region"):
    region_data.append({
        "地區":         r,
        "訂單數":       len(sub),
        "獨立顧客":     sub["Customer_ID"].nunique(),
        "營收 (NT$)":    int(sub["Amount"].sum()),
        "客單價":       int(sub["Amount"].mean()),
    })
region_df = pd.DataFrame(region_data).sort_values("營收 (NT$)", ascending=False).reset_index(drop=True)

# ── 付款方式 ───────────────────────────────────────────────────────
pay_data = []
for p, sub in df.groupby("Payment"):
    rev = int(sub["Amount"].sum())
    pay_data.append({
        "付款方式":      p,
        "訂單數":        len(sub),
        "營收 (NT$)":     rev,
        "占比 (%)":       round(100 * rev / total_revenue, 2),
    })
pay_df = pd.DataFrame(pay_data).sort_values("營收 (NT$)", ascending=False).reset_index(drop=True)

# ── 時段別 ─────────────────────────────────────────────────────────
seg_order = ["上午", "中午", "下午", "晚上", "深夜"]
seg_data = []
for s in seg_order:
    sub = df[df["Time_Segment"] == s]
    if len(sub) == 0:
        continue
    seg_data.append({
        "時段":          s,
        "訂單數":        len(sub),
        "營收 (NT$)":     int(sub["Amount"].sum()),
        "客單價":        int(sub["Amount"].mean()),
    })
seg_df = pd.DataFrame(seg_data)

# ── 星期別 ─────────────────────────────────────────────────────────
dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
dow_data = []
for d in dow_order:
    sub = df[df["DayOfWeek"] == d]
    if len(sub) == 0:
        continue
    dow_data.append({
        "星期":         d,
        "訂單數":       len(sub),
        "營收 (NT$)":    int(sub["Amount"].sum()),
    })
dow_df = pd.DataFrame(dow_data)

# ── 每日營收趨勢 ───────────────────────────────────────────────────
daily = (
    df.groupby("Order_Date")
    .agg(訂單數=("Order_ID", "count"),
         **{"營收 (NT$)": ("Amount", "sum")},
         **{"毛利 (NT$)": ("Gross_Profit", "sum")})
    .reset_index()
    .sort_values("Order_Date")
)
daily["營收 (NT$)"] = daily["營收 (NT$)"].astype(int)
daily["毛利 (NT$)"] = daily["毛利 (NT$)"].astype(int)
daily["Order_Date"] = daily["Order_Date"].dt.strftime("%Y-%m-%d")

# ── 寫多 sheet Excel ────────────────────────────────────────────────
with pd.ExcelWriter(OUTPUT, engine="openpyxl") as writer:
    kpi_df.to_excel(writer,      sheet_name="KPI 總覽",      index=False)
    cat_df.to_excel(writer,      sheet_name="類別別營收",    index=False)
    top_prod_df.to_excel(writer, sheet_name="Top 10 商品",   index=False)
    region_df.to_excel(writer,   sheet_name="地區別",        index=False)
    pay_df.to_excel(writer,      sheet_name="付款方式",      index=False)
    seg_df.to_excel(writer,      sheet_name="時段別",        index=False)
    dow_df.to_excel(writer,      sheet_name="星期別",        index=False)
    daily.to_excel(writer,       sheet_name="每日趨勢",      index=False)

print(f"[Stage 3] ✅ 銷售分析完成")
print(f"  總營收：NT$ {total_revenue:,}、客單價 NT$ {int(avg_order_value):,}")
print(f"  Top 類別：{cat_df.iloc[0]['類別']}（NT$ {cat_df.iloc[0]['營收 (NT$)']:,}）")
print(f"  輸出（8 個工作表）：{OUTPUT}")
