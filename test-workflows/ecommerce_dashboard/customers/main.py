"""電商客戶分析 CLI(demo 既有專案)— 多子命令 + 選項。純標準庫、沙盒可跑。

用法:
  python main.py report --by tier|region --metric count|spend --out PATH [--format json|csv]
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

DATA = Path(__file__).parent / "customers.csv"


def load():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["orders"] = int(r["orders"])
            r["total_spend"] = float(r["total_spend"])
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
    keyf = {"tier": lambda r: r["tier"], "region": lambda r: r["region"]}[a.by]
    agg = defaultdict(float)
    for r in rows:
        agg[keyf(r)] += 1 if a.metric == "count" else r["total_spend"]
    table = sorted(agg.items(), key=lambda x: -x[1])
    write_out(a.out, f"客戶-{a.by}-{a.metric}", table, a.format)


def cmd_kpi(a):
    rows = load()
    spend = sum(r["total_spend"] for r in rows)
    table = [
        ("客戶數", len(rows)),
        ("總消費額", round(spend, 2)),
        ("平均消費額", round(spend / len(rows), 2) if rows else 0),
        ("總訂單", sum(r["orders"] for r in rows)),
    ]
    write_out(a.out, "客戶-KPI", table, a.format)


def main():
    p = argparse.ArgumentParser(description="電商客戶分析 CLI")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("report", help="客戶報表")
    r.add_argument("--by", choices=["tier", "region"], default="tier")
    r.add_argument("--metric", choices=["count", "spend"], default="spend")
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
