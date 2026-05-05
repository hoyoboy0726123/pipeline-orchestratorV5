# Agent Runtime 實作說明

本文件整理 `agent-studio` 目前如何實作「LLM 先檢查環境、檢查現有依賴、必要時自行安裝依賴、再依使用者權限設定決定是否需要確認」這一套通用 agent runtime。目標不是只描述 UI，而是提供其他專案可直接參考的實作分層。

本文描述的是 `agent-studio` 的自行實作版本，不是外部研究材料的直接原碼包裝；執行依賴也應由 `agent-studio/package.json` 自己管理。

## 1. 核心設計原則

這套 runtime 的關鍵不是讓模型「隨便做事」，而是把行為拆成四層：

1. `Prompt contract`
   - 明確告訴 LLM：先讀環境、缺依賴可自行安裝、缺資訊要呼叫 `request_user_input`，不要只輸出建議。
2. `Tool contract`
   - 把檔案讀寫、搜尋、shell、MCP、skill script 都做成結構化工具，而不是讓模型自由輸出 pseudo command。
3. `Approval contract`
   - 高風險工具不是直接交給模型決定，而是由 runtime 依節點權限與 pipeline policy 判斷是否放行。
4. `Replay contract`
   - 成功執行後保留最後成功腳本、必要依賴檢查與驗證命令，讓下次能重播成功路徑，而不是重做整段探索。

## 2. 專案內對應檔案

- `agent-studio/lib/llm-runner.js`
  - 組 system prompt、驅動 LLM tool loop。
- `agent-studio/lib/runtime.js`
  - 管理 agent 執行、approval、memory、compiled plan、replay。
- `agent-studio/lib/builtin-tools.js`
  - 定義 `read_file`、`write_file`、`edit_file`、`powershell_command`、`request_user_input` 等工具。
- `agent-studio/lib/approvals.js`
  - 管理待確認請求、記住規則、回覆使用者輸入。
- `agent-studio/lib/control-plane.js`
  - 對外暴露 `initialize / set_permission_mode / mcp_status / resolve_control_request` 等控制介面。
- `agent-studio/lib/tool-policy.js`
  - 工具分類與命名規則。

## 3. LLM 如何學會先檢查環境

這件事主要不是靠模型自己猜，而是靠 system prompt 明確約束。`llm-runner.js` 的 prompt 目前包含這幾條關鍵規則：

```text
Use workspace read/search tools before editing or running large commands when context is missing.
If dependencies are missing and they are necessary to complete the task, you may install them yourself with the terminal tool.
If required user information is missing, call request_user_input and continue after the answer.
Separate confirmed facts from your own inference, and do the work instead of only proposing it.
```

這樣做的效果是：

1. 先用 `list_directory`、`read_file`、`grep_search` 看現有專案狀態。
2. 再用 `powershell_command` 做環境探測，例如：
   - `python --version`
   - `node -v`
   - `pip show requests beautifulsoup4`
   - `npm list playwright`
3. 缺依賴時才執行安裝。
4. 缺任務資訊時呼叫 `request_user_input`，而不是直接停在自然語言回答。

## 4. 依賴檢查與安裝的實作方式

### 4.1 由工具層提供 shell 能力

`builtin-tools.js` 內建 `powershell_command`：

- 工具名稱：`powershell_command`
- `approvalMode: "ask"`
- `risk: "high"`

也就是說，模型確實可以安裝依賴，但安裝命令不會繞過 runtime 的風險判斷。

### 4.2 由命令分析器辨識「這是安裝命令」

`builtin-tools.js` 內會分析命令內容，若命中 `pip install`、`npm install`、`Install-Module` 等模式，就標記：

- `category: dependency-install`
- `severity: warn`
- `Policy notes: This command installs dependencies or modules.`

這一層的價值是：

- UI log 能明確顯示「這一步是在安裝依賴」
- approval 流程能更容易區分一般讀取與副作用操作
- replay 可以把成功依賴檢查與修復命令分開保存

### 4.3 建議的執行順序

其他專案若要複用，可直接採用這個順序：

```text
1. 檢查 runtime 是否存在
2. 檢查目標套件是否已安裝
3. 若未安裝，執行安裝
4. 再次做 import / version 驗證
5. 執行正式腳本
```

例如 Python：

```powershell
python --version
pip show requests beautifulsoup4
pip install requests beautifulsoup4
python -c "import requests, bs4"
python scrape.py
```

## 5. 如何區分哪些操作必須先問使用者

目前有兩層設定共同決定：

### 5.1 Pipeline 層：approval policy

`pipeline.approvalPolicy.mode` 支援：

- `ask`
- `preapproved`
- `deny`

含義：

- `ask`：遇到需確認操作就進待確認區。
- `preapproved`：直接放行。
- `deny`：直接拒絕。

此外也可對個別工具建立 rule，讓某些工具被永久放行或永久拒絕。

### 5.2 Agent 層：permission

目前 agent 節點使用：

- `safe`
- `confirm`
- `elevated`

runtime 內的實際邏輯是：

- `elevated`
  - 高風險 builtin tool 與需確認的 MCP tool 直接放行。
- `safe`
  - 高風險操作直接拒絕。
- 其他值
  - 走 approval 流程。現在實務上就是 `confirm`。

可用這段偽碼理解：

```js
if (!needsApproval) {
  approve();
} else if (agent.permission === 'elevated') {
  approve();
} else if (agent.permission === 'safe') {
  deny();
} else {
  askUser();
}
```

## 6. 使用者確認是怎麼被 runtime 接住的

`runtime.js` 在執行工具前會呼叫 `requestToolApproval()`。若需要確認，會委派給 `approvals.js`：

1. 建立 pending request
2. 發佈 `approval-requested` 事件
3. 前端顯示到「待確認」區
4. 使用者按允許 / 拒絕，或補充文字回答
5. runtime 收到 `approval-resolved` 後繼續執行

若模型缺的是任務資訊，而不是工具權限，則使用 `request_user_input`：

```json
{
  "question": "請提供輸出檔名",
  "context": "目前已確認會產生 Excel，但尚未知道命名規則"
}
```

這樣 agent 不會直接失敗，而是等待回答後繼續同一次流程。

## 7. 如何避免每次都讓 LLM 重做探索

成功執行後，`runtime.js` 會建立：

- `compiledPlan`
- `replayPackage`

其中 `replayPackage` 會盡量只保留「最後成功路徑」：

- `scripts`
- `preflight`
- `repair`
- `run`
- `validate`

例如：

```json
{
  "preflight": [
    { "command": "python -c \"import docx\"" }
  ],
  "repair": [
    { "command": "pip install python-docx" }
  ],
  "run": [
    { "command": "python ...\\create_news_word.py" }
  ],
  "validate": [
    { "command": "python -X utf8 -c \"import os; assert os.path.exists('output.docx')\"" }
  ]
}
```

這種設計比只存整段 transcript 更實用，因為它能直接重播成功腳本，而不是重播所有失敗嘗試。

## 8. 其他專案可直接照抄的提示詞骨架

如果你要在別的 agent 系統實作同樣能力，system prompt 至少應包含以下意思：

```text
你是一個可執行本地任務的自治 agent。
當上下文不足時，先用讀取/搜尋工具檢查工作區，再決定是否編輯檔案或執行命令。
若完成任務需要依賴且依賴缺失，你可以自行安裝，但必須透過 shell 工具執行，不可只輸出建議。
若任務缺少必要資訊，請呼叫 request_user_input 並在收到答案後繼續執行，不要直接結束。
工具必須以原生 tool call 形式呼叫，不可輸出假 JSON 或命令範例。
將已確認事實與推論分開，並以完成任務為目標，而不是只提供說明。
```

## 9. 建議給其他專案的最小資料模型

### Agent 設定

```json
{
  "mode": "prompt",
  "task": "抓取新聞並輸出 markdown",
  "permission": "confirm",
  "memory": "project",
  "allowSubagents": true
}
```

### Pipeline 設定

```json
{
  "approvalPolicy": {
    "mode": "ask",
    "rules": []
  },
  "control": {
    "permissionMode": "ask"
  }
}
```

### MCP 設定

```json
{
  "name": "github",
  "type": "sdk",
  "enabled": true,
  "permissionMode": "ask",
  "scope": "project"
}
```

## 10. 目前版本的限制

這份文件描述的是 `agent-studio` 現況，不是理想未來式。要注意兩點：

1. `write_file / edit_file` 目前尚未完整納入與 `powershell_command` 同級的 approval gate。
   - 也就是說，若你要更強安全模型，其他專案應把「寫可執行檔」也納入確認流程。
2. memory 目前只有目錄骨架與 prompt 注入，`consolidation / recall policy` 還沒完全做完。

## 11. 實作建議

如果你在其他專案重做這套能力，建議優先順序如下：

1. 先做工具層與 approval gate，不要先做 UI。
2. prompt 只負責行為約束，不要把安全邏輯放在 prompt。
3. 對 shell 命令做分類，至少識別：
   - dependency install
   - process control
   - destructive file op
   - outside repository path
4. 成功腳本一定要抽成 replay artifact，不要只保存對話。
5. 缺資訊時一定要有 `request_user_input` 類工具，否則 agent 只會停在自然語言回答。

---

如需對照實作，請優先閱讀：

- `agent-studio/lib/llm-runner.js`
- `agent-studio/lib/runtime.js`
- `agent-studio/lib/builtin-tools.js`
- `agent-studio/lib/approvals.js`
- `agent-studio/lib/control-plane.js`
