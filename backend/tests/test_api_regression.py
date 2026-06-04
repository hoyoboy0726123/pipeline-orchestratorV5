# -*- coding: utf-8 -*-
"""
Atlas V5 — API 回歸測試(read-only;只透過 HTTP API 驗證後端行為)
=================================================================
目的:在不改動應用程式碼的前提下,以 API 呼叫覆蓋「正常路徑 + 各種設定 +
邊緣條件」,作為改動(如授權替換 html2text、補測試等)後的回歸安全網。

設計原則:
  - 純讀為主;凡有變更設定者(model / self-heal)測完一律「還原」。
  - 不觸發 LLM(不打 chat / skill / subagent 執行)以求快速、零成本、可重現;
    僅以 script 節點(echo)做端到端執行,驗證 runner/executor/store 鏈路。
  - 絕不印出任何密鑰/token 值;含密鑰的端點只驗「鍵存在 + 200」。
  - 每個檢查獨立 try/except,單項失敗不中斷整體;最後輸出彙整與 exit code。

用法:後端需在 http://localhost:8004 執行中。
  & backend\\.venv\\Scripts\\python.exe backend\\tests\\test_api_regression.py
"""
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx

BASE = os.environ.get("ATLAS_API_BASE", "http://localhost:8004")
TIMEOUT = 30.0
_results = []  # (ok: bool|None, name, detail)  None = SKIP/N-A


def _rec(ok, name, detail=""):
    _results.append((ok, name, detail))
    tag = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def check(name, fn):
    """fn() 回傳 (ok, detail) 或 True/False;丟例外視為 FAIL。"""
    try:
        r = fn()
        if isinstance(r, tuple):
            _rec(r[0], name, r[1] if len(r) > 1 else "")
        else:
            _rec(bool(r), name)
    except Exception as e:
        _rec(False, name, f"例外:{type(e).__name__}: {str(e)[:160]}")


def g(path, **kw):
    return httpx.get(f"{BASE}{path}", timeout=TIMEOUT, **kw)


def p(path, **kw):
    return httpx.post(f"{BASE}{path}", timeout=TIMEOUT, **kw)


def put(path, **kw):
    return httpx.put(f"{BASE}{path}", timeout=TIMEOUT, **kw)


def dele(path, **kw):
    return httpx.delete(f"{BASE}{path}", timeout=TIMEOUT, **kw)


# ======================================================================
# Section A — 唯讀 GET 端點:回 200 且結構合理(含密鑰端點只驗鍵存在)
# ======================================================================
def section_A():
    print("\n== A. 唯讀 GET 端點 ==")
    simple_200 = [
        "/health", "/system/host-tools", "/settings/model",
        "/settings/models/available", "/settings/node-status",
        "/settings/skill-packages", "/settings/sandbox", "/settings/memory",
        "/settings/self-heal", "/settings/skills-dir", "/settings/auto-minimize-for-computer-use",
        "/skills/available", "/subagent/roles", "/memory/facts",
        "/workflows", "/recipes", "/pipeline/runs", "/pipeline/scheduled", "/env/paths",
    ]
    for path in simple_200:
        check(f"GET {path} → 200", lambda path=path: (g(path).status_code == 200, ""))

    # 含可能敏感資訊的端點:只驗 200 + 是 JSON dict,絕不印值
    def secretish(path):
        r = g(path)
        if r.status_code != 200:
            return (False, f"status {r.status_code}")
        try:
            j = r.json()
        except Exception:
            return (False, "非 JSON")
        return (isinstance(j, (dict, list)), "200 + JSON(值已遮蔽不顯示)")
    check("GET /settings/notifications → 200(不印值)", lambda: secretish("/settings/notifications"))
    check("GET /settings/web-search → 200(不印值)", lambda: secretish("/settings/web-search"))

    # /settings/model 結構:應含 provider / model
    def model_shape():
        j = g("/settings/model").json()
        keys = set(j.keys()) if isinstance(j, dict) else set()
        return ("provider" in keys and "model" in keys, f"keys={sorted(keys)[:6]}")
    check("GET /settings/model 含 provider/model", model_shape)


# ======================================================================
# Section B — 設定 round-trip(變更後一律還原)
# ======================================================================
def section_B():
    print("\n== B. 設定 round-trip(測完還原) ==")

    # B1: model 同值 PUT(冪等、安全),測完還原成原值
    def model_roundtrip():
        cur = g("/settings/model").json()
        prov, mdl = cur.get("provider"), cur.get("model")
        if not prov or not mdl:
            return (None, "無法取得現值,跳過")
        r = put("/settings/model", json={"provider": prov, "model": mdl})
        ok1 = r.status_code == 200
        # 還原(同值,no-op)
        put("/settings/model", json={"provider": prov, "model": mdl})
        after = g("/settings/model").json()
        ok2 = after.get("provider") == prov and after.get("model") == mdl
        return (ok1 and ok2, f"PUT 同值 200={ok1}, 還原一致={ok2}")
    check("PUT /settings/model 冪等 + 還原", model_roundtrip)

    # B2: self-heal 開關 flip → 驗證變更 → 還原
    def selfheal_roundtrip():
        cur = g("/settings/self-heal").json()
        # 取布林欄位(容忍不同鍵名)
        key = next((k for k, v in cur.items() if isinstance(v, bool)), None) if isinstance(cur, dict) else None
        if key is None:
            return (None, f"無布林欄位可切換,跳過(keys={list(cur)[:5] if isinstance(cur,dict) else cur})")
        orig = cur[key]
        put("/settings/self-heal", json={key: (not orig)})
        mid = g("/settings/self-heal").json().get(key)
        put("/settings/self-heal", json={key: orig})  # 還原
        restored = g("/settings/self-heal").json().get(key)
        return (mid == (not orig) and restored == orig, f"flip={mid==(not orig)}, restore={restored==orig}")
    check("PUT /settings/self-heal flip + 還原", selfheal_roundtrip)


# ======================================================================
# Section C — 邊緣條件 / 錯誤處理
# ======================================================================
def section_C():
    print("\n== C. 邊緣條件 / 錯誤處理 ==")
    check("GET 不存在的 run → 404",
          lambda: (g("/pipeline/runs/__nope_xyz__").status_code == 404, ""))
    check("GET 不存在的 workflow → 404",
          lambda: (g("/workflows/__nope_xyz__").status_code == 404, ""))
    check("PUT /settings/model 空 body → 422",
          lambda: (put("/settings/model", json={}).status_code == 422, "驗證應擋下"))
    check("POST /pipeline/run 空 body → 422",
          lambda: (p("/pipeline/run", json={}).status_code == 422, ""))

    def bad_yaml():
        r = p("/pipeline/run", json={"yaml_content": "name: [unclosed\n  steps: : :", "validate": False})
        return (r.status_code == 400, f"壞 YAML 應 400(got {r.status_code})")
    check("POST /pipeline/run 壞 YAML → 400", bad_yaml)

    def dryrun_bad_yaml():
        r = p("/pipeline/dry-run", json={"yaml_content": "::: not yaml :::"})
        return (r.status_code in (400, 422, 200), f"狀態 {r.status_code}(壞 YAML 應有結構化回應)")
    check("POST /pipeline/dry-run 壞 YAML 不崩潰", dryrun_bad_yaml)

    # 路徑穿越:fs/browse 不應回傳系統根目錄敏感內容(對齊稽核 M-2)
    def fs_traversal():
        r = g("/fs/browse", params={"path": "../../../../../../etc/passwd"})
        # 期望:被擋(4xx)或回傳空/受限,而非真的列出系統檔
        body = (r.text or "").lower()
        leaked = "root:x:0:0" in body or "/bin/bash" in body
        return (not leaked, f"status {r.status_code}, 系統檔外洩={leaked}")
    check("GET /fs/browse 路徑穿越被擋", fs_traversal)


# ======================================================================
# Section D — Workflows CRUD round-trip(建立→讀→改→匯出→刪→驗刪)
# ======================================================================
def section_D():
    print("\n== D. Workflows CRUD round-trip ==")
    wf_id = None
    try:
        r = p("/workflows", json={"name": "__api_regression_tmp__", "canvas": {"nodes": [], "edges": []}, "validate": False})
        wf = r.json() if r.status_code == 200 else {}
        wf_id = wf.get("id")
        _rec(r.status_code == 200 and bool(wf_id), "POST /workflows 建立", f"id={wf_id}")
    except Exception as e:
        _rec(False, "POST /workflows 建立", str(e)[:120])

    if not wf_id:
        _rec(None, "CRUD 後續步驟", "無 wf_id,跳過")
        return

    check("GET /workflows/{id} → 200", lambda: (g(f"/workflows/{wf_id}").status_code == 200, ""))
    check("PUT /workflows/{id} 改名 → 200",
          lambda: (put(f"/workflows/{wf_id}", json={"name": "__api_regression_tmp2__"}).status_code == 200, ""))
    check("GET /workflows/{id}/export → 200",
          lambda: (g(f"/workflows/{wf_id}/export").status_code == 200, "zip 匯出"))
    check("DELETE /workflows/{id} → 200",
          lambda: (dele(f"/workflows/{wf_id}").status_code == 200, ""))
    check("GET /workflows/{id} 刪除後 → 404",
          lambda: (g(f"/workflows/{wf_id}").status_code == 404, ""))


# ======================================================================
# Section E — LLM-free 端到端(script 節點 echo),驗 runner/executor/store
# ======================================================================
def section_E():
    print("\n== E. Script 節點端到端(無 LLM) ==")
    yaml_content = "name: api_regression_smoke\nsteps:\n  - name: s1\n    batch: echo regression-ok\n"
    run_id = None
    try:
        r = p("/pipeline/run", json={"yaml_content": yaml_content, "validate": False, "use_recipe": False})
        run_id = r.json().get("run_id") if r.status_code == 200 else None
        _rec(r.status_code == 200 and bool(run_id), "POST /pipeline/run 啟動(script)", f"run_id={run_id}")
    except Exception as e:
        _rec(False, "POST /pipeline/run 啟動(script)", str(e)[:120])

    if not run_id:
        _rec(None, "E2E 後續", "無 run_id,跳過")
        return

    final = {}
    t0 = time.time()
    for _ in range(40):  # 最多 ~80s
        time.sleep(2)
        try:
            st = g(f"/pipeline/runs/{run_id}").json()
        except Exception:
            continue
        s = st.get("status", "")
        if any(k in s for k in ("complet", "fail", "abort", "awaiting")):
            final = st
            break
    status = final.get("status", "(timeout)")
    _rec("complet" in status, "script run 完成 (completed)", f"status={status}, 耗時 {time.time()-t0:.0f}s")

    # GET run log 應可取得
    check("GET /pipeline/runs/{id}/log → 200",
          lambda: (g(f"/pipeline/runs/{run_id}/log").status_code == 200, ""))
    # 清理:刪掉這次 run
    check("DELETE /pipeline/runs/{id} 清理 → 200",
          lambda: (dele(f"/pipeline/runs/{run_id}").status_code == 200, ""))


# ======================================================================
# Section F — dry-run 正常路徑
# ======================================================================
def section_F():
    print("\n== F. dry-run 正常路徑 ==")
    yaml_content = "name: dryrun_smoke\nsteps:\n  - name: s1\n    batch: echo {{ input.x }}\n"
    def dryrun_ok():
        r = p("/pipeline/dry-run", json={"yaml_content": yaml_content, "input_params": {"x": "hello"}})
        return (r.status_code == 200, f"status {r.status_code}")
    check("POST /pipeline/dry-run 正常 → 200", dryrun_ok)


def main():
    print("=" * 70)
    print(f"Atlas V5 API 回歸測試 @ {BASE}")
    print("=" * 70)
    # 前置:後端可達?
    try:
        up = g("/health").status_code == 200
    except Exception as e:
        print(f"❌ 後端不可達({e}),中止。請先啟動 backend(port 8004)。")
        sys.exit(2)
    if not up:
        print("❌ /health 非 200,中止。")
        sys.exit(2)

    section_A()
    section_B()
    section_C()
    section_D()
    section_E()
    section_F()

    passed = sum(1 for ok, _, _ in _results if ok is True)
    failed = sum(1 for ok, _, _ in _results if ok is False)
    skipped = sum(1 for ok, _, _ in _results if ok is None)
    print("\n" + "=" * 70)
    print(f"彙整:PASS {passed} / FAIL {failed} / SKIP {skipped}(共 {len(_results)})")
    if failed:
        print("失敗項:")
        for ok, name, detail in _results:
            if ok is False:
                print(f"  - {name} :: {detail}")
    print("=" * 70)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
