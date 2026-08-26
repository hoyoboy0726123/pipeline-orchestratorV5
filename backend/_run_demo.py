import sys, time
sys.stdout.reconfigure(encoding="utf-8")
import httpx
from db import get_workflow

API = "http://127.0.0.1:8004"
mode = sys.argv[1] if len(sys.argv) > 1 else "full"   # full | recipe
use_recipe = (mode == "recipe")

wid = open("_demo_wid.txt").read().strip()
yaml_str = open("_demo_yaml.txt", encoding="utf-8").read()

print(f"=== 執行模式: {'快速模式(recipe)' if use_recipe else '完整模式(LLM)'} | wf={wid} ===", flush=True)
t0 = time.time()
r = httpx.post(f"{API}/pipeline/run", json={
    "yaml_content": yaml_str, "workflow_id": wid,
    "use_recipe": use_recipe, "validate": not use_recipe,
    "no_save_recipe": use_recipe,   # recipe 驗證時不要覆寫
}, timeout=30)
rid = r.json().get("run_id")
print("run_id:", rid, flush=True)
deadline = time.time() + 600; last = ""
while time.time() < deadline:
    time.sleep(4)
    d = httpx.get(f"{API}/pipeline/runs/{rid}", timeout=15).json()
    st = d.get("status")
    if st != last:
        print("  status:", st, flush=True); last = st
    if st in ("completed", "failed", "aborted", "awaiting_human"):
        tot = 0
        for sr in d.get("step_results", []):
            tu = sr.get("token_usage") or {}
            tk = tu.get("total_tokens", 0); tot += tk
            print(f"   {sr.get('name')}: {sr.get('validation_status')} | tokens={tk}", flush=True)
        print(f"總 tokens: {tot} | 終態: {st} | 耗時 {time.time()-t0:.1f}s", flush=True)
        if st == "awaiting_human":
            print("AWAITING:", d.get("awaiting_type"), str(d.get("awaiting_suggestion"))[:200], flush=True)
        break
