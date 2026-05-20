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

## 當下狀態快照(每次更新此份請同步刷新)

> 更新時把這段內容覆寫掉、寫上你看到的當下狀態。日期填 commit 推上去那刻。

- **HANDOFF 最後更新**:2026-05-20 by 主審 agent
- **main 在**:`d27aeaf`(或更新,請 pull)
- **4 個範例工作流已 seed**:財務純串接、財務健診進化版、Reddit IF、Reddit Switch
- **模糊提示詞 + skill 自動注入樣本** 機制已驗證通過
- **已知開放任務**:由 user 直接交辦,或見 GitHub Issues(若有)

---

不確定的事先讀 `memory/`、`README.md`、這份 —— 還不確定就**直接問 user、別猜**。
