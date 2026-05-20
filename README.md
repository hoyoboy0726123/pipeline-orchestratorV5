# Pipeline Orchestrator

**視覺化 Pipeline 編排器** — 透過拖拉式介面設計自動化工作流程，結合 AI 驅動的腳本生成、條件分支、智慧驗證與排程執行。

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/License-MIT-green)

> 設計目標：**零術語也能用**。即使不懂程式，也能用白話描述需求、拉拉節點就完成自動化流程。

## 功能特色

### 視覺化流程編輯器
透過 React Flow 畫布自由排列、連接節點。支援多種節點類型：

| 節點 | 說明 |
|------|------|
| **腳本** | 執行你電腦上現成的 Python / Shell 程式 |
| **AI 技能** | 用白話描述任務，AI 自動產生並執行程式碼 |
| **條件分支** | 依條件決定流程走向（IF 成立/不成立、Switch 多情況分流）|
| **網頁爬蟲** | 抓取網頁 / 論壇列表與子頁、影片字幕 |
| **人工確認** | 在任意步驟暫停，透過 Telegram 等待人工審核 |
| **AI 驗證** | 每步執行後由 AI 檢查輸出是否符合預期 |
| **視覺驗證** | 用視覺模型（VLM）判斷畫面 / 輸出是否正確 |
| **桌面自動化** | 錄製並重播滑鼠鍵盤操作（pyautogui）|
| **Outlook 自動化** | 透過桌面版 Outlook 收發信、套用範本 |
| **多輪代理 (Subagent)** | 交給可多輪推理的 AI 代理處理複雜子任務 |

### AI 驅動
- **AI 助手**：用自然語言描述需求，AI 引導你釐清細節後產出可執行的工作流
- **智慧驗證**：每步執行完，AI 自動檢查輸出是否符合預期
- **Recipe 快取**：成功的 AI 技能執行結果會被快取，相同任務 + 輸入直接重播、跳過 LLM 呼叫

### 多 LLM 支援
- **Groq**（雲端，預設）、**Google Gemini**（雲端）、**Ollama**（本地，含 thinking 模式）
- 可為個別節點指定主 / 副模型

### 排程、通知與隔離執行
- **排程執行**：Cron 式排程，支援一次性 / 週期性
- **Telegram 通知**：完成 / 失敗推播；人工確認節點可直接在 Telegram 操作
- **Skill 沙盒**（選用）：AI 生成的程式碼隔離在 WSL + Docker 容器內執行，不直接碰 host

### 其他
- **YAML 匯入匯出**：工作流可序列化為 YAML，便於版本控制
- **內建範例工作流**：首次啟動自動載入數個可直接執行 / 參考的範例
- **AI 技能套件管理**：透過 Web UI 管理 Python 套件
- **Log 即時串流**：執行過程即時查看完整日誌

---

## 系統需求

| 項目 | 版本 | 必要性 |
|------|------|--------|
| Python | 3.10+（**建議 3.12**）| 必要 |
| Node.js | 18+ | 必要 |
| npm | 9+ | 必要 |
| WSL2 + Docker Engine | — | 選用（只有要用 Skill 沙盒才需要）|

選用：
- [uv](https://docs.astral.sh/uv/) — 更快的 Python 套件管理工具
- Telegram Bot — 遠端通知與人工確認操作

---

## 快速開始（uv，推薦）

從**完全空機**到跑起來 —— 只裝 3 個工具（Git、Node.js、uv），其餘 uv 處理（含 Python 本身）。

### 1. 裝工具

**Windows**（一般使用者 PowerShell，不需 admin）：

```powershell
winget install Git.Git -e
winget install OpenJS.NodeJS.LTS -e
winget install Python.Python.3.12 -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS**：
```bash
brew install git node python@3.12
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Linux (Debian / Ubuntu)**：
```bash
sudo apt install -y git nodejs npm python3.12 python3.12-venv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **為什麼也裝全域 Python?**uv 會在 `uv venv` 時自動下載自己的 Python(放在 uv 私有目錄,不污染 PATH);但建議**另外裝一份 Python 3.12 到全域**,給你之後寫其他 Python 程式、開 IDE、跑 ad-hoc 腳本用 —— 兩份並存、互不干擾。
>
> **為什麼是 3.12?**這個專案需要 3.10+,而 3.12 是當前綜合最佳:套件 wheel 齊全(裝得快、極少從原始碼編譯)、效能比 3.10 / 3.11 好,又比 3.13 成熟、不會踩到剛出爐的相容性坑。

> 沒有 winget(舊版 Windows 10)?從這 3 個官網下載安裝即可：
> [Git](https://git-scm.com/download/win) · [Node.js LTS](https://nodejs.org/) · [uv](https://docs.astral.sh/uv/getting-started/installation/)
>
> **裝完後重開終端機**(PATH 才會吃到新工具)。

### 2. 抓專案 + 裝依賴

```bash
git clone https://github.com/hoyoboy0726123/pipeline-orchestratorV5.git
cd pipeline-orchestratorV5

# 後端 —— uv 會自動下載 Python 3.12,你不用先裝 Python
cd backend
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
cd ..

# 前端
cd frontend
npm install
cd ..
```

### 3. 設 API Key

編輯 `backend/.env`,至少填一個:

```env
GROQ_API_KEY=...       # https://console.groq.com/keys (免費額度)
GEMINI_API_KEY=...     # https://aistudio.google.com/apikey (免費)
```

### 4. 啟動

```bash
# Windows
launch_full_project.bat

# macOS / Linux
chmod +x start.sh && ./start.sh
```

開瀏覽器到 **http://localhost:3002**,完成。

> Skill 沙盒(選用,讓 AI 生成的 code 跑在容器內)的安裝見下方「安裝步驟」第 5 步。

---

## 安裝步驟

### 1. 取得原始碼

```bash
git clone https://github.com/hoyoboy0726123/pipeline-orchestratorV5.git
cd pipeline-orchestratorV5
```

### 2. 設定環境變數

```bash
# Windows (CMD)
copy backend\.env.example backend\.env
# PowerShell / macOS / Linux
cp backend/.env.example backend/.env
```

編輯 `backend/.env`，至少填一個 LLM API Key：

```env
GROQ_API_KEY=your_groq_api_key_here      # https://console.groq.com/keys
GEMINI_API_KEY=your_gemini_api_key_here  # https://aistudio.google.com/apikey

# Telegram 通知（選填，也可在 Web UI 設定）
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

TIMEZONE=Asia/Taipei
OUTPUT_BASE_PATH=~/ai_output
PIPELINE_DIR=~/pipelines
```

### 3. 啟動

#### 一鍵啟動（推薦）

啟動腳本會在**首次執行時自動**建立後端虛擬環境、安裝前後端依賴、複製 `.env` 範本：

```bash
# Windows — 雙擊或在終端機執行
launch_full_project.bat

# macOS / Linux
chmod +x start.sh
./start.sh
```

#### 手動啟動

<details>
<summary>展開手動步驟</summary>

**後端依賴**（擇一）：
```bash
cd backend
# 方法 A：uv（快）
uv venv .venv && uv pip install -r requirements.txt
# 方法 B：pip + venv
python -m venv .venv
.venv\Scripts\activate          # Windows CMD
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

**前端依賴**：
```bash
cd frontend && npm install
```

**啟動**（兩個終端機）：
```bash
# 終端機 1 — 後端
cd backend
.venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8004   # Windows
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8004           # macOS / Linux

# 終端機 2 — 前端
cd frontend && npm run dev -- --port 3002
```

> Windows PowerShell 若出現「無法執行指令碼」：先跑
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

</details>

### 4. 開始使用

| 服務 | 網址 |
|------|------|
| 前端（編輯器） | http://localhost:3002 |
| 後端 API | http://localhost:8004 |
| API 文件（Swagger） | http://localhost:8004/docs |

### 5.（選用）安裝 Skill 沙盒

若要讓 AI 生成的程式碼**隔離在容器內**執行，需安裝沙盒（需要 WSL2）：

```bash
# Windows — 一次性安裝
sandbox\setup_sandbox.bat
```

未安裝沙盒時，Skill 節點會 fallback 在本機直接執行。詳見 `sandbox/README.md`。

---

## 專案結構

```
pipeline-orchestratorV5/
├── backend/                  # FastAPI 後端
│   ├── main.py               # 所有 REST API 端點
│   ├── config.py             # 環境變數、路徑、SQLite 設定
│   ├── llm_factory.py        # LLM 多 provider 工廠
│   ├── settings.py           # 使用者設定持久化
│   ├── db.py                 # SQLite 資料層
│   ├── skill_scanner.py      # 掃描 Agent Skills 目錄
│   ├── skill_pkg_manager.py  # AI 技能套件安裝
│   ├── yaml_to_canvas.py     # YAML → 畫布轉換
│   ├── seed_examples.py      # 首次啟動載入內建範例
│   ├── requirements.txt      # Python 依賴
│   ├── skill_packages.txt    # 預設 AI 技能套件清單
│   ├── .env.example          # 環境變數範本
│   ├── examples/             # 內建範例工作流（YAML）
│   ├── pipeline/             # Pipeline 核心
│   │   ├── runner.py         # 狀態機式執行引擎
│   │   ├── executor.py       # 單步驟執行（含 AI 技能）
│   │   ├── validator.py      # AI 驗證
│   │   ├── recipe.py         # Recipe 快取
│   │   ├── subagent_runner.py# 多輪代理
│   │   ├── sandbox.py        # 沙盒容器橋接
│   │   └── ...
│   └── scheduler/            # APScheduler 排程
├── frontend/                 # Next.js 14 前端
│   └── app/pipeline/         # 畫布編輯器 / settings / recipes
├── sandbox/                  # Skill 沙盒（WSL + Docker）
│   ├── Dockerfile
│   ├── setup_sandbox.bat     # Windows 一鍵安裝
│   └── README.md
├── test-workflows/finance/   # 財務腳本範例（供範例工作流引用）
├── launch_full_project.bat   # Windows 一鍵啟動
├── start.sh                  # macOS / Linux 一鍵啟動
└── README.md
```

---

## 內建範例工作流

首次啟動時會自動載入數個範例工作流（在工作流列表中名稱含「(範例)」），可直接打開參考或執行：

- **Q1 財務報表（純腳本串接）** — 4 支現成 Python 腳本線性串接，不需 LLM
- **Q1 財務健診 + 分流報告** — 在腳本流程中插入 AI 技能 + 條件節點 + 人工確認
- **Reddit ASUS 版口碑日報** — 爬蟲 → AI 分析 → IF 條件分流 → 產出 Word 日報
- **Reddit ASUS 口碑分級報告** — 爬蟲 → AI 分析 → Switch 多情況分流

---

## 預設 AI 技能套件

後端啟動時會自動安裝以下套件到虛擬環境（可在 **設定 > AI 技能套件** 管理）：
`pandas`、`openpyxl`、`matplotlib`、`requests`、`beautifulsoup4`、`Pillow`、`python-docx`

---

## 設定說明

Web UI **設定**頁面可調整：

- **LLM Provider** — 切換 Groq / Gemini / Ollama，設定主 / 副模型
- **Telegram 通知** — 填入 Bot Token + Chat ID（向 [@BotFather](https://t.me/BotFather) 建立 Bot、[@userinfobot](https://t.me/userinfobot) 取得 Chat ID）
- **AI 技能套件** — 安裝 / 移除 Python 套件
- **Skill 檔案目錄** — 自訂 Agent Skills 的存放位置（預設 `~/.agents/skills/`，也可用環境變數 `SKILLS_DIR` 覆蓋）
- **Skill 沙盒** — 切換 Skill 節點在本機或容器內執行

---

## License

MIT
