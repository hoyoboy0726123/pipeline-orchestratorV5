# Skill Sandbox (V5)

WSL + Docker Engine 沙盒：讓 LLM 生成的 Python / Shell 程式碼隔離執行，不直接碰 Windows host。

## 為什麼要有這個？

AI 技能節點會執行 LLM 即時生成、未經人工審核的程式碼。沒有沙盒時，這些 code 直接以 `subprocess` 在 Windows host 上跑，只隔著一個 venv。啟用沙盒後，code 改送進容器執行 —— LLM 亂刪檔、存取敏感資料都被擋在沙盒內。

沒有 Docker Desktop、沒有付費授權 — 只靠 Windows 內建的 WSL2 + 在 WSL 內裝 Docker Engine（開源免費）。

## 架構

```
Windows host
  └─ FastAPI backend (8004)
        └─ wsl docker exec pipeline-sandbox-v5 python /tmp/xxx.py
              │
              └→ WSL2 Ubuntu
                   └─ Docker Engine
                        └─ pipeline-sandbox-v5 容器（長駐）
                             - Python 3.13
                             - 預裝：pandas / openpyxl / numpy / matplotlib / opencv-headless
                               / python-pptx / pdfplumber / newspaper3k / cloudscraper / feedparser
                             - Node.js + pptxgenjs（對應 `.agents/skills/pptx`）
                             - Bind mounts（三條，路徑全部 1:1 映射，容器內外同路徑）：
                               • 專案本體：C:\...\pipeline-orchestratorV5
                                        → /mnt/c/.../pipeline-orchestratorV5
                               • Agent Skills：C:\Users\<you>\.agents
                                        → /mnt/c/Users/<you>/.agents
                                        (且容器內 ~/.agents 也指向同一地點)
```

## 一次性安裝

1. 雙擊 `setup_sandbox.bat`
2. 如果提示沒 WSL，按提示在管理員 PowerShell 執行 `wsl --install` 並重啟，然後再跑一次 `setup_sandbox.bat`
3. 完成後可看到 `✓ 沙盒就緒！`

## 預設 skill

`setup_sandbox.bat` 安裝時會把 `default_skills/` 底下的兩個 V5 專屬 skill 複製到
`C:\Users\<你>\.agents\skills\`（已存在的不覆蓋）：

| Skill | 功能 |
|---|---|
| `scraped-content-parser` | 爬蟲節點抓回來的留言/評論/商品列表等重複性內容結構化成 JSON |
| `python-cli-extractor`   | 把使用者既有的 Python GUI/Streamlit/Flask 專案無破壞性接進 V5 pipeline |

**Office 三件套（docx / pptx / xlsx）不附 — 是 Anthropic 官方專有授權、禁止 redistribute**。
要用 docx / pptx 自己從 Claude Code 安裝：
```
# 透過 Claude Code 的 skill marketplace 或官方 plugin 安裝
# https://docs.claude.com/en/docs/agents-and-tools/agent-skills
```
裝到 `C:\Users\<你>\.agents\skills\docx\` 跟 `.\pptx\` 即可被本系統使用。

## 升級已安裝的沙盒（改了 Dockerfile / requirements.txt 之後）

預設重跑 `setup_sandbox.bat` **不會** rebuild（偵測到 image 存在就跳過，避免每次啟動都等 10 分鐘）。
改了 Dockerfile 或 requirements.txt 後要生效：

```cmd
setup_sandbox.bat --rebuild
```

這會：
1. 強制移除舊 container `pipeline-sandbox-v5` 與 image `pipeline-sandbox:latest`
2. 從 Dockerfile 重新 build（`--no-cache`，確保新套件真的裝進去）
3. 重新啟動容器（沿用原本的 bind mount 策略）

約 5-10 分鐘。

檔案清單：
- `Dockerfile` — 沙盒映像檔定義（Python 3.13 slim + 常用資料套件）
- `requirements.txt` — 預裝套件清單（對齊 backend/skill_packages.txt 可跨平台的部分）
- `setup.sh` — 在 WSL 內實際安裝 Docker、build image、啟動容器
- `setup_sandbox.bat` — Windows 入口，把專案路徑轉成 WSL 格式再呼叫 setup.sh
- `README.md` — 這份文件

## 每日啟動

**不用手動做任何事**。backend 啟動時會：
1. 檢查 WSL 能跑 `wsl docker ps`
2. 檢查 `pipeline-sandbox-v5` 容器是否在跑，若沒在跑會試著 `docker start`
3. 前端 Settings 頁會顯示沙盒狀態（綠燈=就緒、紅燈=有問題）

如果沙盒壞掉或關閉，skill 會 fallback 到舊的 host subprocess 模式（有 warning log）。

## 手動管理

偶爾會想手動操作容器：

```bash
# 進 WSL
wsl

# 看容器狀態
sudo docker ps -a | grep pipeline-sandbox-v5

# 手動啟動/停止/重啟
sudo docker start pipeline-sandbox-v5
sudo docker stop pipeline-sandbox-v5
sudo docker restart pipeline-sandbox-v5

# 進容器看看
sudo docker exec -it pipeline-sandbox-v5 bash

# 加新 Python 套件（臨時）
sudo docker exec pipeline-sandbox-v5 pip install <package>

# 修改 requirements.txt 後永久加：從 Windows 跑
#   setup_sandbox.bat --rebuild
# （自動砍 container + image，重 build 並重建容器）
```

## 限制

- **computer_use 節點完全不走沙盒** — pyautogui / pynput / mss 需要 host 桌面權限，永遠在 Windows 原生跑
- **Script 節點預設也不走沙盒** — 使用者自己寫的腳本通常需要訪問他們 Windows 上的特定環境
- 只有 **Skill 節點** 走沙盒（LLM 產生的不信任程式碼）
- 路徑翻譯：LLM 程式碼若用 Windows 絕對路徑 `C:\Users\...`，backend 會自動轉成 Linux `/mnt/c/Users/...`（映射同一份檔案）
- Agent Skills：`~/.agents/skills/<skill_name>/` 在容器內也能用 `Path.home() / ".agents" / "skills" / ...` 讀到（`/root/.agents` 指向 host 的 `.agents`）

## 如果 build 撞到 SIGBUS / OOM

Docker build 壓縮 numpy / matplotlib / opencv 等大套件時，如果 WSL 的記憶體或磁碟不夠會直接 daemon crash：

**先檢查磁碟**：C: 殘餘要至少 **8 GB**（Docker image + swap.vhdx 會佔空間）。低於這量會各種莫名其妙失敗。
```powershell
Get-PSDrive C
```

**記憶體不足**：手動建 `C:\Users\<你>\.wslconfig`：
```ini
[wsl2]
memory=8GB
swap=16GB
```
然後 `wsl --shutdown`，下次啟動 WSL 吃新設定。

Dockerfile 已經把 pip install 拆成多層（Tier 1 核心 / Tier 2 HTTP / Tier 3a matplotlib / Tier 3b opencv），單層 commit 壓力較小。預設 WSL 記憶體通常夠用，不用先動 `.wslconfig`。

## 疑難排解

**Build 卡在 `pip install` 然後 Docker daemon crash（SIGBUS / bus error）**
→ WSL 記憶體不夠。先套上面的 `.wslconfig`、`wsl --shutdown`、清空舊快取：
```
wsl sudo docker builder prune -af
wsl sudo docker rm -f pipeline-sandbox-v5
```
然後再跑一次 `setup_sandbox.bat`。

Dockerfile 本來就把 pip install 拆成多個 RUN 分層安裝，每層獨立 OOM-safe。若仍然爆記憶體，把 Tier 3（matplotlib / opencv）那段 RUN 改分成兩行。


**`setup_sandbox.bat` 卡在 sudo 密碼**
→ WSL Ubuntu 的密碼。忘了的話在 WSL 內 `sudo passwd $USER` 重設（需輸入目前密碼）。完全忘了可用 admin PowerShell `wsl --user root passwd <your_username>`。

**Docker 安裝後 `docker ps` 說權限不足**
→ WSL 重啟：`wsl --shutdown` 然後重開 WSL。若仍然不行，手動 `sudo docker ps` 可繞過。

**容器啟動失敗說 port 已使用**
→ 容器不需要對外開 port（純內部 exec），若真撞到可能是舊容器殘留：`sudo docker rm -f pipeline-sandbox-v5`。

**Build 很久**
→ 第一次 build 會從 Docker Hub 拉 python:3.13-slim 基底映像（~150MB）再 pip install，首次約 3-5 分鐘，之後 cache 會快很多。
