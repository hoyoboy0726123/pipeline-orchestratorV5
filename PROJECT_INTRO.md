# Pipeline Orchestrator V5 — 專案介紹

> **本文件用途**:對外介紹專案時的話術 + 功能清單。依對話對象選用不同版本。

---

## 🎯 一句話介紹(依對象不同)

| 對象 | 怎麼說 |
|---|---|
| **工程師 / AI engineer** | LLM-native pipeline orchestrator。自寫 agent framework、LLM 客戶端用 LangChain 跨 5 家(Anthropic / OpenAI / Gemini / Groq / Ollama),agent 邏輯、subagent 角色、recipe cache、sandbox 路由全自製。 |
| **老闆 / 客戶** | 視覺化 AI 工作流自動化平台。拖拉 canvas 組合多步驟流程、LLM 自動寫 code 跑、可接 Telegram 真人介入確認、支援企業常見任務(報表、爬蟲、桌面自動化、Office 整合)。 |
| **非技術人** | 自家做的 AI 工作流平台、你拖拖拉拉、AI 幫你跑。 |

---

## 📝 簡短介紹(< 200 字、可貼簡報 / 募資 PPT)

**Pipeline Orchestrator V5** 是一個視覺化的 AI 工作流自動化平台。

使用者在瀏覽器拖拉 canvas 設計流程、每個節點可以是「跑 shell 指令」、「叫 AI 寫 code 跑」、「派一個有角色的子代理執行複雜任務」、「條件分支」、「停下來等真人在 Telegram 確認」等。AI 不只寫腳本、還會自己驗證輸出對不對、錯了會 retry。

底層支援 5 家主流 LLM(Anthropic Claude、OpenAI GPT、Google Gemini、Groq、本地 Ollama)、可動態切換。沙盒環境用 Docker 隔離、避免 AI 亂跑指令影響主機。整套 agent 框架自製、只用 LangChain 接 LLM。

---

## 🔍 詳細介紹(< 1000 字、技術人友善)

### 核心概念

把企業常見的「多步驟、需要 AI 思考、偶爾要人介入」流程、用視覺化方式拼起來,讓使用者**不寫程式碼也能做 AI 自動化**,讓開發者**用 AI 替代撰寫一次性腳本**。

### 主要組成

**前端**:Next.js 14 + React Flow 視覺化 canvas + Zustand 狀態管理。
**後端**:FastAPI + SQLite(WAL 模式)、Python 3.11+。
**LLM 層**:LangChain 統一 5 家 provider 介面、native function calling。
**Agent 層**:全自寫、不依賴 LangGraph / AgentExecutor / CrewAI。
**Sandbox**:WSL2 + Docker Engine、隔離 LLM 生成的程式碼執行。
**通知 / 互動**:Telegram Bot(human-in-loop 確認 + 自然語言對話助手)。
**排程**:APScheduler(cron / interval)。

### 為什麼自寫 agent framework

- 套用 LangGraph / Autogen / CrewAI 後、深度客製常要繞框架限制
- 自寫 ~3000 行 agent loop、換來 100% 控制(retry 策略、token 預算、人類介入時機、recipe 快取邏輯)
- LangChain 留作 LLM 抽象層、provider 換很方便、但 agent 邏輯不被綁

---

## 🧩 節點類型(8 種主要 + 2 種輔助配置)

| 節點 | 做什麼 | 典型用途 |
|---|---|---|
| **🔧 Script** | 直接跑 shell command / 既有腳本 | 「跑這個 .py 檔」「執行這個 npm script」 |
| **🤖 Skill** | LLM 看任務描述、自己寫 Python code 並執行(含 recipe cache) | 「讀這個 csv 算月平均、產 Excel 報表」 |
| **🎭 Subagent** | 多輪 LLM agent loop、可指派角色(data_analyst / coder / researcher / 自訂),工具白名單可選 | 「以資料分析師身份分析這份報告、給結論」 |
| **🚦 Condition** | 條件分支(if / switch),根據上游結果走不同路線 | 「分數 > 80 → 寄稱讚信、否則 → 寄改進建議」 |
| **🙋 Human Confirm** | 暫停 pipeline、推訊息到 Telegram、等真人按按鈕確認才繼續 | 「寄信前讓老闆 review 草稿」 |
| **🖥️ Computer Use** | 桌面 GUI 自動化(UIA + 電腦視覺 + 座標、三層 fallback) | 「打開這個應用、點某按鈕、填表單」 |
| **📧 Outlook Automation** | Outlook 信件 / 行事曆操作 | 「掃這週信、提取重要事項摘要」 |
| **🌐 Web Crawler** | 爬蟲(支援 JS 渲染、Cloudflare、登入)、結構化解析 | 「爬這頁的價格 / 評論 / 表格」 |
| _輔助_:**AI Validation** | 加在任何節點後、用 AI 驗證輸出是否符合預期 | 「報表有沒有缺欄」 |
| _輔助_:**Visual Validation** | 截圖 + 視覺對比驗證(Computer Use 配套) | 「畫面有沒有跳對視窗」 |

---

## ⚡ 主要功能特色

### 1. **Recipe Cache(智能快取)**
Skill 節點第一次跑、LLM 寫 code、跑成功 → **存起來**。下次相同任務 + 相同輸入指紋 → **直接重跑、不再呼 LLM**。長期省 70-90% LLM cost。

### 2. **AI 助手(對話式 workflow 建構)**
不會寫 YAML?跟 AI 助手講中文「我要每天早上 9 點抓這頁價格、超過 X 元 Telegram 通知我」、AI 自動建好 workflow + 排程。透過 web 介面或 Telegram 都能用。

### 3. **多 LLM 切換**
設定頁切 provider/model、即時生效。每個任務可以用不同 LLM(主任務用 Claude、驗證用 Gemini 省錢)。

### 4. **Sandbox 隔離**
LLM 寫的 Python / Shell code 全在 Docker 容器內跑、不會弄壞主機。掛點自動 fallback、會告知 LLM 路徑切換。

### 5. **Telegram Bot 雙模式**
- **被動模式**:Pipeline 走到 human confirm 節點 → 主動推訊息 + 按鈕等回應
- **主動模式**:使用者直接跟 Bot 自然語言對話「幫我看一下 sales 工作流跑得怎樣」、「重新跑昨天那個任務」

### 6. **自訂 Subagent 角色**
內建 5 個角色(data_analyst / coder / researcher / critic / planner),也可在設定頁新增「主管」、「員工」、「業務助理」等自訂角色,各自有 system prompt + 工具白名單。

### 7. **排程**
Cron / interval 排程、跨 backend 重啟保留、UI 可看下次執行時間。

### 8. **Workflow 範例 seed**
新環境第一次啟動、自動 seed 2-3 個範例工作流,使用者開箱即用。

### 9. **Multi-step 編排能力**
單一 workflow 可串 10+ 步驟、上游輸出餵下游、變數透過 `{{ steps.X.output.Y }}` template 帶。

### 10. **AI Validation(防 LLM 幻覺)**
LLM 寫 code 跑完不算數、另一個 AI 看輸出檔對不對、不對自動 retry。

---

## 🔧 技術棧(供工程師交流)

```
Frontend:
  Next.js 14 (App Router)
  React Flow 11 (canvas)
  Zustand (state)
  Tailwind + shadcn/ui

Backend:
  FastAPI
  SQLite (WAL mode)
  APScheduler (cron job store)
  Pydantic v2

LLM 抽象層:
  langchain-core
  langchain-anthropic / langchain-openai / langchain-google-genai
  langchain-groq / langchain-ollama

Sandbox:
  WSL2 + Docker Engine (不用 Docker Desktop, 避商業授權)
  Custom python:3.13-slim image + node + pptxgenjs + python-pptx 等預裝

Telegram:
  python-telegram-bot v21+ (polling 模式)
```

**沒有用的**(常被誤會):
- ❌ LangGraph(自寫 state machine)
- ❌ LangChain AgentExecutor(自寫 agent loop)
- ❌ CrewAI / Autogen / OpenAI Assistants API
- ❌ n8n / Zapier(我們是 LLM-native、不是 connector-based)

---

## 🆚 跟相似產品的差別

| 產品 | 跟我們的差別 |
|---|---|
| **n8n / Zapier / Make** | 它們是預定義 connector(已有的 API 串起來)。我們是 **LLM 寫 code 跑**、不需要事先定義每個 service 怎麼接。 |
| **Dify / Coze / Flowise** | 它們偏 prompt engineering 編排。我們偏**可執行 pipeline**(skill 節點 AI 寫真的 Python 跑出真產物、有 sandbox 隔離)。 |
| **LangFlow / LangSmith** | 它們是 LangChain 視覺化包裝、被 LangChain 抽象綁死。我們 agent 邏輯自製、LangChain 只用來接 LLM。 |
| **AutoGPT / BabyAGI** | 它們是純自主 agent、難控制走向。我們是**人類定義流程 + AI 填節點**、可預期可重跑。 |

---

## 🚀 適合誰用

- **企業 IT / Ops**:把重複的「資料整理 + 報表生成 + 信件通知」自動化、不必每次寫腳本
- **數據分析師**:不會寫 Python 也能讓 AI 幫你跑分析(skill 節點)
- **產品 / 行銷**:設定爬蟲監測競品價格、設定排程、結果 push 到 Telegram
- **小型團隊 ops**:Telegram 變成「跟整個自動化系統對話的入口」、不用切換工具

---

## 📦 部署 / 上手

- 一鍵啟動腳本:`launch_full_project.bat`(Windows) / `start.sh`(Mac/Linux)
- 前端:`http://localhost:3002`
- 後端 API:`http://localhost:8000`
- Swagger UI:`http://localhost:8000/docs`

需要的東西:
- Python 3.11+
- Node.js 18+
- WSL2 + Docker(若要用 sandbox 模式、非必須)
- 至少一個 LLM provider API key(Groq 免費就夠玩)

---

_最後更新:2026-05-24_
