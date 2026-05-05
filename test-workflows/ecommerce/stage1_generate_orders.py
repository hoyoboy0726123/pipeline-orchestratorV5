"""
【工具 A】電商銷售原始訂單產生器
製作者：電商營運部 (E-commerce Ops)

用途：模擬電商平台 30 天的訂單資料，
      含商品類別、客戶區域、付款方式、訂單狀態。
      刻意摻入髒資料：取消訂單、重複訂單、欄位空值。

輸入：無（資料由程式隨機產生）
輸出：~/ai_output/ecommerce/raw_orders.xlsx
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
        return os.path.join(env_run_dir, "raw_orders.xlsx")
    base_path = os.getenv("OUTPUT_BASE_PATH")
    if base_path:
        if not os.path.isabs(base_path):
            base_path = os.path.join(Path(__file__).parent.parent.parent, base_path)
        return os.path.join(base_path, "ecommerce", "raw_orders.xlsx")
    project_root = Path(__file__).parent.parent.parent
    return os.path.join(project_root, "ai_output", "ecommerce", "raw_orders.xlsx")


OUTPUT = get_output_path()
os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

random.seed(42)

# ── 商品目錄 ────────────────────────────────────────────────────────
PRODUCTS = [
    # (商品名,         類別,           單價 NTD)
    ("Wireless Earbuds Pro",   "3C 配件",    2890),
    ("Bluetooth Speaker X1",   "3C 配件",    1690),
    ("USB-C Hub 7-in-1",       "3C 配件",     990),
    ("Mechanical Keyboard",    "電腦周邊",   3490),
    ("Gaming Mouse RGB",       "電腦周邊",   1290),
    ("4K Webcam",              "電腦周邊",   2190),
    ("Smart Watch S",          "穿戴",       4990),
    ("Fitness Tracker",        "穿戴",       1490),
    ("Aroma Diffuser",         "居家",        890),
    ("LED Desk Lamp",          "居家",       1290),
    ("Portable Air Purifier",  "居家",       3290),
    ("Insulated Tumbler 500ml","居家",        650),
    ("Yoga Mat Pro",           "運動健身",   1190),
    ("Dumbbells 5kg Pair",     "運動健身",   1890),
    ("Resistance Bands Set",   "運動健身",    790),
    ("Coffee Beans 250g",      "美食",        450),
    ("Matcha Powder 100g",     "美食",        380),
    ("Granola 500g",           "美食",        320),
]

REGIONS = ["北部", "中部", "南部", "東部", "離島"]
REGION_WEIGHTS = [0.45, 0.25, 0.20, 0.07, 0.03]
PAYMENTS = ["信用卡", "貨到付款", "ATM 轉帳", "電子錢包"]
PAYMENT_WEIGHTS = [0.55, 0.20, 0.10, 0.15]
STATUSES = ["Completed", "Completed", "Completed", "Completed", "Cancelled", "Refunded"]

DAYS = 30
ORDERS_PER_DAY_RANGE = (18, 32)
start_date = datetime(2025, 3, 1)

orders = []
order_seq = 1

for d in range(DAYS):
    today = start_date + timedelta(days=d)
    # 週末訂單較多
    is_weekend = today.weekday() >= 5
    n_orders = random.randint(*ORDERS_PER_DAY_RANGE) + (8 if is_weekend else 0)

    for _ in range(n_orders):
        product_name, category, unit_price = random.choice(PRODUCTS)
        qty = random.choices([1, 2, 3, 4, 5], weights=[0.5, 0.25, 0.15, 0.07, 0.03])[0]
        region = random.choices(REGIONS, weights=REGION_WEIGHTS)[0]
        payment = random.choices(PAYMENTS, weights=PAYMENT_WEIGHTS)[0]
        status = random.choices(STATUSES, weights=[1, 1, 1, 1, 0.5, 0.3])[0]
        # 5% 機率有折扣（5%~25%）
        discount_rate = round(random.uniform(0.05, 0.25), 2) if random.random() < 0.05 else 0.0
        amount_before_discount = unit_price * qty
        amount = round(amount_before_discount * (1 - discount_rate))

        # 隨機產出時間（當天 9:00~22:00）
        order_time = today.replace(hour=random.randint(9, 22),
                                    minute=random.randint(0, 59),
                                    second=random.randint(0, 59))

        orders.append({
            "Order_ID":       f"O-{order_seq:06d}",
            "Order_Time":     order_time.strftime("%Y-%m-%d %H:%M:%S"),
            "Customer_ID":    f"C-{random.randint(10000, 99999)}",
            "Region":         region,
            "Product_Name":   product_name,
            "Category":       category,
            "Quantity":       qty,
            "Unit_Price":     unit_price,
            "Discount_Rate":  discount_rate,
            "Amount":         amount,
            "Payment":        payment,
            "Status":         status,
        })
        order_seq += 1

# ── 摻髒資料 ────────────────────────────────────────────────────────
# 1. 隨機 15 筆 Customer_ID 欄空值
miss_indices = random.sample(range(len(orders)), 15)
for i in miss_indices:
    orders[i]["Customer_ID"] = None

# 2. 隨機 10 筆 Region 欄空值
miss_region = random.sample(
    [i for i in range(len(orders)) if i not in miss_indices], 10
)
for i in miss_region:
    orders[i]["Region"] = None

# 3. 加入 8 筆完全重複（系統雙重提交 bug）
dup_indices = random.sample(range(len(orders)), 8)
for i in dup_indices:
    orders.append({**orders[i], "Order_ID": f"O-DUP-{len(orders)}"})

# 4. 加入 5 筆異常金額（負值 / 超大值）
anomalies_added = 0
for i in random.sample(range(len(orders)), 5):
    orders[i]["Amount"] = -100 if random.random() < 0.5 else 9999999
    anomalies_added += 1

random.shuffle(orders)

# 重編 Order_ID
for idx, o in enumerate(orders, 1):
    o["Order_ID"] = f"O-{idx:06d}"

df = pd.DataFrame(orders)
df.to_excel(OUTPUT, index=False)

print(f"[Stage 1] ✅ 產生原始訂單完成")
print(f"  天數：{DAYS}，總訂單：{len(df)}")
print(f"  商品：{len(PRODUCTS)} 種、跨 {len(set(p[1] for p in PRODUCTS))} 個類別")
print(f"  髒資料：客戶 ID 缺值 15、地區缺值 10、重複 8、金額異常 {anomalies_added}")
print(f"  輸出：{OUTPUT}")
