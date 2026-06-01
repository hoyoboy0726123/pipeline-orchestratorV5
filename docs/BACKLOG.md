# Pipeline Orchestrator V5 — 未來開發待辦清單 (Backlog)

> 「已規劃、決定先擱著、進未來版本」的功能集中記這。每次判定「之後再做」就加一筆。
> 與 `ROADMAP.md`(2026-04 的詳細 ticket 規劃)分開:這份是滾動式 backlog。
> 最後更新:2026-05-31

---

## 🔧 進行中 (Current)

- [ ] **沙盒依賴衝突偵測 + 告警** — 容器裝套件時比對版本,撞到衝突(例 A 要 `numpy==1.x`、B 要 `numpy==2.x`)就在 TG / 前端**告警**。
  - 不隔離、只告知 —— 擋掉「後裝覆蓋前裝、舊專案靜默壞掉」這種最難 debug 的情況。
  - 對齊「可控白箱」定位:讓使用者對全域沙盒環境累積了什麼有可視性。
  - 實作點:`skill_pkg_manager.py` 的 `add_package` / `add_package_sandbox` 裝完後跑 `pip check`,有衝突附進回傳訊息 → `runner` install_dep 顯示給使用者。

---

## 🧊 沙盒依賴管理 — 自動隔離 (規模化後)

- [ ] **衝突專案自動隔離、系統代管 venv** — 只有**真的偵測到衝突**的專案,才為它開專屬 venv / 容器;其他繼續共用全域。
  - venv 由 runner / cli-extractor **自動建立 / 選用、使用者無感**(複雜度藏後端、不違背零學習成本)。
  - 前置:上方「衝突偵測 + 告警」已完成。
  - 觸發時機:多使用者 / 長期累積大量既有專案、衝突變高頻時才做。現階段(demo / 個人用)全域共用即可、不過度工程。

- [ ] **runner venv 與編排器 venv 脫鉤(代管獨立 runner venv)** — 未來進化:編排器代管一顆**獨立的 runner venv**(跟跑 uvicorn 的 venv 分開),預載常用套件 → 兼顧「零設定高成功率」+「使用者裝套件弄不壞編排器本體」+「可預測」。
  - 背景:2026-06-01 已先做「script 節點未勾 venv → 走**系統全域** Python(不再導向 V5 venv、不污染編排器)、缺依賴 loud fail」(見 `executor._script_env`)。這是脫鉤的第一步(把預設從 V5 venv 改成全域)。
  - 本條是再進一步:預設不要落到「裸全域(成功率不穩)」,而是落到「編排器代管、預載常用套件的獨立 runner venv」。屆時 script 節點未勾 venv → 用 runner venv 而非系統全域。
  - 與上一條「衝突專案自動隔離」可整併成同一套 venv 代管機制。

---

## 🤖 AI 助手

- [ ] **system prompt 改漸進揭露 (progressive disclosure)** — 不一次塞全部規則,按情境分層載入,省 token + 降誤觸。(對應 task #99)

---

## 🔗 google_action 節點 (Google 服務整合)

- [ ] **Phase 1** — OAuth + Gmail / Calendar / Drive + chat tool(⚠️ 開工前先問使用者 Q1-Q8 決策)
- [ ] **Phase 2** — Sheets + Docs + audit log
- [ ] **Phase 3** — Tasks / Slides / People / Translate / YouTube / Forms
- [ ] **Phase 4** — GCP tier (選做)
- [ ] **持續強化** — redact / account switcher / sheets-as-config

---

## 🍎 macOS 支援

- [ ] sandbox.py 加 platform branch(Mac 直接 docker、跳過 wsl)
- [ ] setup_sandbox_mac.sh — Mac 啟 sandbox container 一鍵腳本
- [ ] 路徑轉換 `_wsl_to_windows_path` 在 Mac 上 no-op
- [ ] Computer Use 節點 Mac graceful disable
- [ ] Outlook 節點 Mac graceful disable
- [ ] DPI awareness + pywin32 import 加 platform guard

---

## 🎨 Hero UI

- [ ] **Phase 4** — Mini button + Drawer + 過渡動畫
- [ ] **Phase 5** — 觸發狀態切換 + 範例卡點擊行為 + 跑現有 workflow modal

---

## 🔴 native loop 保護補齊(系統性 regression — 最優先)

> **背景**:skill/subagent 改 native FC(Phase A.1/A.2、#155/#156)後成為**預設**,但所有保護/守門當初只加在 text fallback loop、**沒同步搬進 native loop**。預設模式下這些保護全失效。2026-06-01 兩份審計證實。**逐項補、每項 compile + 測**(一次改 12 處易引入新 regression)。

### skill native loop(`executor.py` `_execute_skill_native_loop`,for tc 迴圈 ~3864)
- [x] **pip install 硬攔 → missing_dependency**(已補、已 E2E 驗證 2026-05-31)
- [x] **ModuleNotFoundError 早期攔截**(run_python/run_shell result 含 ModuleNotFoundError → 抽套件名 → missing_packages,已補 2026-06-01、待 E2E)
- [~] **命令授權 classify_command**(🟡 重新評估後降級:install 類已被上面 pip 硬攔 cover;rm/wget 等其他敏感命令在 **sandbox 容器內隔離**;ask_mode **預設 off** 時本就不觸發。→ 只在「使用者開 ask_mode」情境有邊際價值、且 async approval 複雜。建議併進共用 helper 時一起做、不單獨硬塞)
- [~] **sandbox pre-flight `_preflight_sandbox`**(🟡 native 的 tool_fn 是 langchain tool、**不接 force_host**,host fallback 在 native 不可行;#161 已修「silent fallback 告知 LLM」。→ 真要做得改 build_subagent_tools 讓 tool 接 force_host,大改、列為共用 helper 重構一部分)
- [x] **web_search 上限**(✅ 本來就有 — `sandbox_tools.py:137` web_search wrapper 內建 `WEB_SEARCH_MAX_PER_STEP`,native/text 都吃得到。agent 誤報)
- [~] **ask_user 上限 ASK_USER_MAX**(🟡 native 經 build_subagent_tools 的 ask_user、目前無硬上限。但 cli-extractor 正常要問 ≥4 輪(策略/功能/參數)、加死上限會擋正常互動。→ 若要做須設寬鬆值或可配置、列共用 helper)
- [x] **連續失敗早停**(✅ native 有 repeat_fail 不收斂守門擋「相同 input 連 3 次」;「換方法狂試」的全域計數較少見、價值低)

### subagent native loop(`subagent_runner.py` `_run_subagent_native` ~839-1354)
- [x] **pip install 硬攔 + ModuleNotFoundError steering**(已補 2026-06-01:subagent 非 pipeline step、無 missing_dependency、以 steering 要求 done(missing_packages),擋住「自行裝套件繞過確認」核心)
- [ ] **last_run_python_ok / last_run_shell_ok 假 done 守門**(🔴 P0:run 失敗後硬送 done(success=true) 沒擋;skill 有、subagent 連 text loop 都缺。需:迴圈追蹤 last_run_*_ok + done 處理 1300 前檢查)
- [ ] **命令授權 classify_command**(🔴 subagent 兩個 loop 都沒;ask_mode off 預設下價值較低、install 已被 pip 攔)
- [ ] **sandbox pre-flight**(🟠 tool_fn 不接 force_host、要改 build_subagent_tools、複雜)
- [ ] **ask_user / web_search 上限**(🟡)
- 已有(native 比 text 好):不收斂守門 repeat_fail、output-ready 自動 done、prose cap、連續無 tool 中止、假 done(output 存在)守門

### 共用化建議
- 這些保護 skill/subagent native loop 重複度高 → 可抽**共用 helper**(tool 執行前 `_preflight_tool_call(tc_name, tc_args, ask_mode)`、執行後 `_inspect_tool_result(result)` 回 missing_packages),兩邊呼叫、集中維護、避免再次 drift。

---

## 🕳️ run_python code 內 pip install 繞過(2026-06-01 subagent E2E 抓到 → 已修)

- [x] **[已修 2026-06-01] run_python code 內 pip install 繞過** — 共用 helper `detect_pip_install` 現在同時掃 run_shell command + run_python code 內的 `subprocess`/`os.system`/`pip.main`/`['pip','install']` 繞法,套件名抽取遇 code 邊界(`'`/`)`/`;`)即停、不混入 result/print 等 identifier(單元測 7/7、subagent E2E 實測攔 2 次、容器 pyfiglet 維持 False)。skill native + subagent native 都呼叫同一組 helper(`detect_pip_install` + `detect_missing_module`)。
- [ ] **(殘留)run_python 內 exec/importlib/動態組字串裝套件** — 圖靈完備、regex 擋不完。目前擋常見明碼模式;exec('pip ins'+'tall') 之類仍可繞。sandbox 容器兜底(不傷 host)、嚴重度低,列觀察。

### 原始問題(已解決、保留紀錄)
- [x] **run_python code 內 pip install 繞過 pip 硬攔**(🟠 skill + subagent native 都有):
  - 現況:pip 攔截只看 **run_shell 的 command**。但 LLM 可在 `run_python` 的 code 內用 `subprocess.run(['pip','install',X])` / `os.system('pip install X')` / `pip.main([...])` 裝套件 → 在 sandbox 容器內裝成功、**沒撞 ModuleNotFoundError、沒走 run_shell** → 三道防線全繞過。實測:subagent coder「用 pyfiglet 產 banner」直接在 run_python 裡裝了 pyfiglet。
  - **安全影響**:在 **sandbox 容器內**裝(裝到容器、不傷 host)→ 「不傷 host」安全核心仍守住;但「裝前經使用者確認」的**可控白箱**被繞過。嚴重度中(容器兜底)。
  - 修法:在 run_python tool 執行前,掃 code 偵測 pip install 模式(regex:`pip\s+install`、`['p\"]pip['\"]\s*,\s*['\"]install`、`pip\.main`、`os\.system.*pip`、`subprocess.*pip.*install`)→ 攔 + steering / 轉 missing_dependency。注意完整偵測是圖靈完備問題(exec/importlib 繞法),先擋常見模式即可。建議放進共用 helper `_inspect_tool_call`(對 run_shell command 與 run_python code 都掃)。

---

## 🐛 已知 bug

- [x] **[已修 2026-05-31] `get_pending_question` 漏 return** — `GET /pipeline/runs/{id}/ask-user` 永遠回 `pending=False`(函式在 pending 存在時 fall through、隱式回 None)。害任何靠這 endpoint 查 pending 的自動化 / 前端輪詢都判斷「沒在等」→ 不 resume → skill 卡到 3600s timeout。TG 手動回答因走 `_send_ask_user_notification` + 直接 deliver、繞過這 endpoint,所以之前沒被發現。修:補 `return {question, options, context}`。(原本誤判為 task GC、其實是這個。)
- [ ] **edge case:`awaiting_type` 殘留但 in-memory `pending` 已遺失**(重啟後端會發生)：此時 resume 會把 run 設 `running` 卻無 skill agent 接手 → 卡死。resume 前應檢查 `get_pending_question` 存在否,不存在則回報「此 run 的等待已失效、請重跑」而非設 running。

---

## 🔬 validator skill 驗證 agent 遷移 native FC(2026-06-01 半修、待完整修)

- [~] **skill 驗證 agent 文字協議不相容 gemma** — `validator.py` 的 agentic skill 驗證仍走文字協議(`invoke_with_streaming` 無 bind_tools + `_parse_tool_calls` 解析 `<tool>` 文字)。gemma 在 native-FC config 下不吐 `<tool>` 文字 → content 0 字 → 空轉 15 輪 + 180s 逾時才退一般驗證(實測卡 3 分鐘)。
  - **已半修(2026-06-01)**:連 2 輪空回應 → raise → 立刻退一般驗證(消除卡頓)。
  - **待完整修**:把驗證 agent 改成 native FC(`llm.bind_tools` + 讀 `response.tool_calls`),跟 skill / subagent loop 一致。屆時 gemma 能正常驗證、不必每次都退一般驗證。風險:動驗證路徑、需 E2E 驗。

---

## 🧩 其他

- [ ] OpenAI Prompt Caching 驗證(0 code、自動生效)
- [ ] Phase A.3 — 砍冗餘 prompt 規範(native FC + caching 都穩了之後做)
- [ ] cli-extractor:gemma 的 ask_user `options` 常空 `[]`(Step 3 A/B 與 Step 5A 選功能都會)。已在 SKILL.md Step 5A 加強制規則,Step 3 與「模型穩定填 options」待觀察 / 補守門。
