"""電商庫存分析 CLI(demo 既有專案)— 多子命令 + 選項。純標準庫、沙盒可跑。

用法:
  python main.py report --view stock|turnover|shortage [--top N] --out PATH [--format json|csv]
  python main.py kpi --out PATH
"""
import argparse
import csv
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = Path(__file__).parent / "inventory.csv"


def load():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["on_hand"] = int(r["on_hand"])
            r["safety_stock"] = int(r["safety_stock"])
            r["monthly_sold"] = int(r["monthly_sold"])
            rows.append(r)
    return rows


def write_out(out, title, table, fmt):
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label", "value"])
            w.writerows(table)
    else:
        p.write_text(json.dumps(
            {"title": title, "data": {k: round(v, 2) for k, v in table}},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {p}  ({len(table)} rows, title={title})")


def cmd_report(a):
    rows = load()
    if a.view == "stock":
        table = [(r["product"], r["on_hand"]) for r in rows]
        table.sort(key=lambda x: -x[1])
    elif a.view == "turnover":
        # 周轉率 = 月銷量 / 現有庫存(越高越好、賣得快)
        table = [(r["product"], round(r["monthly_sold"] / r["on_hand"], 2) if r["on_hand"] else 0) for r in rows]
        table.sort(key=lambda x: -x[1])
    else:  # shortage:低於安全庫存的缺口
        table = [(r["product"], r["safety_stock"] - r["on_hand"]) for r in rows if r["on_hand"] < r["safety_stock"]]
        table.sort(key=lambda x: -x[1])
    if a.top and a.top > 0:
        table = table[: a.top]
    write_out(a.out, f"庫存-{a.view}", table, a.format)


def cmd_kpi(a):
    rows = load()
    short = [r for r in rows if r["on_hand"] < r["safety_stock"]]
    table = [
        ("品項數", len(rows)),
        ("總庫存量", sum(r["on_hand"] for r in rows)),
        ("低於安全庫存品項", len(short)),
        ("月總銷量", sum(r["monthly_sold"] for r in rows)),
    ]
    write_out(a.out, "庫存-KPI", table, a.format)


def main():
    p = argparse.ArgumentParser(description="電商庫存分析 CLI")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("report", help="庫存報表")
    r.add_argument("--view", choices=["stock", "turnover", "shortage"], default="stock")
    r.add_argument("--top", type=int, default=0)
    r.add_argument("--out", required=True)
    r.add_argument("--format", choices=["json", "csv"], default="json")
    r.set_defaults(func=cmd_report)

    k = sub.add_parser("kpi", help="關鍵指標")
    k.add_argument("--out", required=True)
    k.add_argument("--format", choices=["json", "csv"], default="json")
    k.set_defaults(func=cmd_kpi)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
