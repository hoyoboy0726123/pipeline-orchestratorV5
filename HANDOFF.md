# Handoff — Pipeline Orchestrator V5(雙 Claude 協作指南)

這個專案有兩個 Claude Code 實例同時工作:

- **主審 agent**:跑在原 dev 機器(已熟悉專案、累積完整 memory),負責 **review PR、決定 merge**。
- **協作 agent(你)**:跑在另一台機器,負責 **寫 code、改 bug、開分支、push PR**。

這份文件**雙向同步**(走 git pull / push)—— 你發現新雷或主審 agent 修了流程都在這裡更新。

---

## 角色分工

| | 協作 agent(你) | 主審 agent |
|---|---|---|
| 寫 code、改 bug、加小功能 | ✅ 主力 | ❌ 不做 |
| 開 feature branch + push origin | ✅ | ❌ |
| 開 PR、寫 description | ✅ | ❌ |
| Review PR、決定 merge | ❌ | ✅ |
| Push 到 `main` | ❌ **絕對禁止** | ✅ 只他能 |
| 更新這份 HANDOFF.md | ✅(發現新雷時) | ✅(調整流程時) |

---

## 進場前準備

### 1. clone + 安裝
照 `README.md` 的「快速開始(uv)」走。後端 port 8004、前端 3002。

### 2. 解壓 `handoff_to_co_agent.zip`(同捆物)

| 壓縮包內檔案 | 放到哪 |
|---|---|
| `global_CLAUDE.md` | 改名 `CLAUDE.md`,放 `<你的家目錄>\.claude\`(使用者個人偏好) |
| `project_CLAUDE.md` | 改名 `CLAUDE.md`,放專案根(已 .gitignore,不會 commit) |
| `memory/` 整個資料夾 | 放到 `<家目錄>\.claude\projects\<專案路徑編碼>\memory\` |

**「專案路徑編碼」**:啟動 Claude Code 時的工作目錄絕對路徑,所有 `\` 和 `:` 換成 `-`。
- 例:`D:\Atlas\pipeline-orchestratorV5` → `D--Atlas-pipeline-orchestratorV5`
- 不確定就先在這台跑一下 Claude Code、讓它自動建出資料夾、再把 `memory/` 倒進去

### 3. 讀
- 這份 `HANDOFF.md`
- `README.md`(快速開始 + 架構)
- `memory/` 裡的 `*.md`(Claude Code 啟動時自動載入,內含踩雷紀錄、使用者偏好、設計原則)

---

## 每個任務的工作流

```
1. git checkout main && git pull origin main
2. git checkout -b fix/<短主題>             # 或 feat/<短主題>
3. 改 code + 本機測(後端 8004、前端 3002)
4. git status + git diff 自己 review
5. git commit -m "<conventional commit 訊息>"
   訊息結尾加:Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
6. git push origin fix/<短主題>
7. gh pr create --title "..." --body "..."  # 或 GitHub UI 開
   description 寫:bug / 改了什麼 / 怎麼驗證
8. 告訴使用者 PR URL,主審 agent 會 review
```

### Commit message 慣例
跟著 repo 現有風格 —— **conventional commits + 繁體中文描述**:
```
feat: 條件分支面板加常駐提示

- 改了什麼(why,不是 what,讓 reviewer 快速懂)
- 為什麼這樣做

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## 紅線(絕對不要)

- **不要直接推 main**(連本機 main 上做 commit 都不要)
- **不要 force push** 任何分支
- **不要 `--no-verify`** 跳 hook
- **不要 commit** `.env` / `*.key` / `credentials*` / `secret*`(已 gitignore 但別 `git add -A`)
- **不要動 `CLAUDE.md`**(專案版的,內容刻意停留舊版、已 gitignore)
- **不要碰原 dev 機器跑著的 Telegram bot**(同個 token 兩台同時 poll 會 409 Conflict;這台要嘛新開 bot、要嘛把 `TELEGRAM_BOT_TOKEN` 留空)

---

## 設計原則(避免重複討論)

- **使用者寫得鬆、系統扛得住** —— skill 節點的 batch 提示詞用「使用者口吻」、不明寫欄位名 / 大小寫。runner 會自動注入上游檔內容樣本給 skill(見 `executor.py` `_read_file_sample`),LLM 不用猜。**改範例提示詞請維持模糊版**,別「為了安全」又把欄位格式寫死。
- **零術語** —— UI 文案、節點面板、提示訊息一律白話。`exit_code` → 「是否執行成功」、`stdout` → 「畫面輸出的文字」、`status` → 「驗證結果」。
- **不讓 LLM 重做流程已決定好的判斷** —— 例如 IF 已判過好評/負評,後面 Word 標題就「沿用」report.md 第一行、別讓 LLM 重判一次。
- **共用模組改 bug 要回問使用者是否 backport 到 V3 / V4**(見 memory `backport_rule.md`)。
- **驗證使用者實際看得到的東西** —— 不要光看 log 出現某字樣就宣稱成功,要追到 user 真的會打開的檔案 / 畫面。

---

## 環境細節

- 主要環境:**Windows**(cmd.exe 不支援 `&&` 串接,要換行或 PowerShell)
- PowerShell 首次需:`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- 後端 port:**8004**(寫死在 `frontend/next.config.mjs` 的 rewrite 目標)
- 前端 port:**3002**
- 沙盒選用,容器名 `pipeline-sandbox-v5`,跑在 WSL 內 Docker;設定 `sandbox\setup_sandbox.bat`
- **`uv venv` 必須加 `--seed`**,否則 venv 沒 pip、後端 `skill_pkg_manager` 自動裝套件會炸

---

## 協作 agent 交辦 — install_dep 自癒裝錯環境修正(給主審 review)

- **分支**:`fix/install-dep-target-step-interpreter`(只動 `backend/pipeline/runner.py`)
- **bug**:`install_dep`(缺套件自癒)只看 `skill_sandbox_mode` 決定裝沙盒/後端 venv。但 **script 節點是在 host 跑、且刻意走「自己的直譯器」**(見 `executor._script_env`:沒勾 venv→系統全域、勾了→專案 venv,刻意不污染後端 venv)。所以 **wsl_docker 模式下 script 步驟缺套件時,自癒把套件裝進 Docker 沙盒容器 → script 根本碰不到 → 自癒鬼打牆**(實測連按 9 次 install 都修不好)。
- **修法(對齊 `_script_env` 的設計精神)**:自癒改成裝進「**該失敗 step 實際執行的直譯器**」——
  - **script step** → 解析 batch 的 python(裸 `python`→依 `_script_env` PATH 的全域;帶路徑→該 venv)→ `<該直譯器> -m pip install`。**不碰後端 venv、不裝沙盒**。
  - **skill step** → 維持原邏輯(看 `skill_sandbox_mode` 裝沙盒/後端 venv)。
  - 新增兩個 helper:`_resolve_script_interpreter(step)`、`_pip_install_into(py, pkg)`。
- **實測(都在 wsl_docker 模式)**:① Q1 財務報表(venv 直譯器)移除 openpyxl → 自癒裝回 **venv** → completed 4/4;② 裸 `python` 工作流缺 humanize → 自癒裝進 **全域 python** → completed。兩者都不再誤裝沙盒。
- **請主審判斷**:① 這個分流(script→自身直譯器、skill→沙盒設定)對不對你的設計;② 是否要 backport V3/V4。
## 協作 agent 交辦(2026-06-17~24,給主審 agent review)

### 1. 待 review 的 PR — 爬蟲反爬殼誤殺修正
- **分支**:`fix/web-crawler-shell-false-positive`(commit `2e0557c`,只動 `backend/pipeline/web_crawler.py`,+14/-1)
- **bug**:`_looks_like_render_shell` 只要 markdown 出現 `js_challenge` / `jsc_orig_r` / `跳至主要內容` 就整頁判反爬殼丟掉。但這些字串出現在 **www.reddit 每個正常頁面**的 skip-link 網址與導覽列裡 → 27KB 含完整 PO 文的頁面被整頁誤殺、子頁全 0 成功。近期 commit(`68b5105`/`2b8bbaf`)加這偵測後才開始誤殺。
- **修法**:加長度閘 `_SHELL_MAX_BYTES=6000` —— markdown ≥ 6000 bytes 視為有實質正文、不當殼;真正的 CF/SPA 短挑戰頁(<2KB)仍被擋。
- **實測**:www.reddit r/ASUS `list_with_children` 由 **0/3 → 3/3 取得正文**;短 CF 頁仍正確判殼。
- **請主審判斷**:① 閾值 6000 是否 OK(會不會有「真殼但 >6KB」的情況);② 是否偏好更語意化做法(把 `js_challenge`/`jsc_orig_r` 這種「正常 skip-link 也有」的訊號跟 `just a moment`/`checking your browser` 這種「真 CF 頁才有」的分開處理)。我選最小改動的長度閘。

### 2. 本機 DB 狀態已偏離乾淨 seed(不在 git、提醒避免誤會)
協作期間為了錄製 demo,動了本機 SQLite(這些**不會**進 git):
- **新增工作流**:`PTT MobileComm 口碑摘要 (錄製)`(我自建的 5 步:爬蟲→解析→report_writer→human_confirm→docx)。
- **改過範例 URL**:`Reddit ASUS 版口碑日報`、`Reddit ASUS 口碑分級報告` 的 `wc_url` 已從 `www.reddit.com` →(中途試過 old.reddit)→ 改回 `www.reddit.com`(配合上面爬蟲修正)。
- **灌了 recipe**(強模型手寫 code 灌 DB):PTT 的解析+docx 兩步、兩支 Reddit 的「解析貼文清單」step2。都是確定性步、replay 零 LLM。

### 3. 過程中發現的 recipe 命中陷阱(寫給未來省事)
- **batch 含 `{{ steps.X.output.path }}` → runtime 展開成帶 run 時間戳的絕對路徑 → task_hash 每次變、recipe 永不命中**。給「要灌 recipe 的確定性步」的 batch 改用純檔名(code 自己從 cwd 找檔)。
- **human_confirm 後的步驟算輸入指紋時 cwd 不在 per-run 夾 → 輸入檔被算成 `missing:檔名`**。該步 recipe 的 fingerprint 要對齊 runtime 實際的 `missing:` 值才命中。
- **`seed_recipe.py` 在 Windows cp1252 console 印中文會 crash** → 跑前設 `PYTHONUTF8=1`。

---

## 當下狀態快照(每次更新此份請同步刷新)

> 更新時把這段內容覆寫掉、寫上你看到的當下狀態。日期填 commit 推上去那刻。

- **HANDOFF 最後更新**:2026-06-24 by 協作 agent(交辦上面 3 點)
- **main 在**:`d27aeaf`(或更新,請 pull)
- **待 review 分支**:`fix/web-crawler-shell-false-positive`
- **4 個範例工作流已 seed**:財務純串接、財務健診進化版、Reddit IF、Reddit Switch
- **模糊提示詞 + skill 自動注入樣本** 機制已驗證通過
- **已知開放任務**:由 user 直接交辦,或見 GitHub Issues(若有)

---

不確定的事先讀 `memory/`、`README.md`、這份 —— 還不確定就**直接問 user、別猜**。
