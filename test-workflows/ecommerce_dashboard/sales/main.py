"""電商銷售分析 CLI(demo 既有專案)— 多子命令 + 選項。純標準庫、沙盒可跑。

用法:
  python main.py report --by product|category|month --metric revenue|units [--top N] --out PATH [--format json|csv]
  python main.py kpi --out PATH
"""
import argparse
import csv
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = Path(__file__).parent / "orders.csv"


def load():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["units"] = int(r["units"])
            r["revenue"] = float(r["revenue"])
            rows.append(r)
    return rows


def write_out(out, title, table, fmt):
    """table: list[(label, value)]。json → {title, data:{label:value}};csv → label,value 兩欄。"""
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
    keyf = {
        "product": lambda r: r["product"],
        "category": lambda r: r["category"],
        "month": lambda r: r["date"][:7],
    }[a.by]
    agg = defaultdict(float)
    for r in rows:
        agg[keyf(r)] += r["revenue"] if a.metric == "revenue" else r["units"]
    table = sorted(agg.items(), key=lambda x: -x[1])
    if a.by == "month":
        table = sorted(agg.items())  # 月份用時間序、不排名次
    if a.top and a.top > 0:
        table = table[: a.top]
    title = f"銷售-{a.by}-{a.metric}"
    write_out(a.out, title, table, a.format)


def cmd_kpi(a):
    rows = load()
    rev = sum(r["revenue"] for r in rows)
    table = [
        ("總營收", round(rev, 2)),
        ("總訂單", len(rows)),
        ("總銷量", sum(r["units"] for r in rows)),
        ("客單價", round(rev / len(rows), 2) if rows else 0),
    ]
    write_out(a.out, "銷售-KPI", table, a.format)


def main():
    p = argparse.ArgumentParser(description="電商銷售分析 CLI")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("report", help="彙總報表")
    r.add_argument("--by", choices=["product", "category", "month"], default="product")
    r.add_argument("--metric", choices=["revenue", "units"], default="revenue")
    r.add_argument("--top", type=int, default=0, help="只取前 N 名(0=全部)")
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
