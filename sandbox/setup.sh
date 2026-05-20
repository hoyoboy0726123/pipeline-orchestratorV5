#!/usr/bin/env bash
# Pipeline Orchestrator V5 — WSL 內的沙盒安裝腳本
#
# 由 setup_sandbox.bat 從 Windows 呼叫進來，在 WSL Ubuntu 內執行。
# 做三件事：
#   1. 如果沒有 Docker Engine 就裝
#   2. build 沙盒映像檔（如果尚未存在）
#   3. 啟動長駐容器 pipeline-sandbox-v5（bind mount 專案根目錄）
#
# 之後 backend 會透過 `wsl docker exec pipeline-sandbox-v5 ...` 執行 skill 程式碼。
#
# 用法：
#   setup.sh <project_dir_in_wsl>              # 一般安裝（跳過已存在的 image / container）
#   setup.sh <project_dir_in_wsl> --rebuild    # 強制 rebuild image + 重建 container
#                                              # （改了 Dockerfile / requirements.txt 後用）
set -euo pipefail

# ── 參數：專案根目錄 + 可選 --rebuild 旗標
PROJECT_DIR="${1:-}"
REBUILD="no"
for arg in "${@:2}"; do
    case "$arg" in
        --rebuild|-r) REBUILD="yes" ;;
    esac
done
if [[ -z "$PROJECT_DIR" ]]; then
    echo "用法：$0 <project_dir_in_wsl> [--rebuild]"
    echo "範例：$0 /mnt/c/Users/GU605_PR_MZ/pipeline-orchestratorV5"
    echo "改了 Dockerfile / requirements.txt 要重裝：$0 ... --rebuild"
    exit 1
fi
if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "✗ 找不到專案目錄：$PROJECT_DIR"
    exit 1
fi

CONTAINER="pipeline-sandbox-v5"
IMAGE="pipeline-sandbox:latest"   # 跟 V3 image tag 共用，container name 才差異化

echo "══════════════════════════════════════════════════════"
echo "Pipeline Orchestrator V5 — 沙盒安裝"
echo "══════════════════════════════════════════════════════"
echo "專案目錄：$PROJECT_DIR"
echo ""

# ── Docker CLI 前綴偵測：優先跑 plain docker；失敗才用 sudo
# 已加入 docker group 的使用者（usermod -aG docker）重啟 WSL 後就免 sudo
if docker info &>/dev/null; then
    DOCKER="docker"
    echo "✓ docker 免 sudo 可用"
else
    DOCKER="sudo docker"
    echo "ℹ docker 需要 sudo（尚未加入 docker group 或 WSL 還沒重啟）"
fi
echo ""

# ── 1. 確認 / 安裝 Docker Engine
if ! command -v docker &>/dev/null; then
    echo "==> Docker 未安裝，開始自動安裝（~2-3 分鐘）..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    echo "✓ Docker 已安裝"
    echo "  ⚠ 已把目前使用者加進 docker group，WSL 重啟後免 sudo 可用 docker"
else
    echo "✓ Docker 已存在：$(docker --version)"
fi

# ── 2. 啟動 Docker daemon（WSL 內 systemd 未預設啟動時需手動）
# `docker info` 已通過代表 daemon 在跑、跳過後續 sudo service 檢查（避免卡 sudo 密碼）
if [[ "$DOCKER" == "sudo docker" ]] && ! sudo -n service docker status &>/dev/null; then
    echo "==> 啟動 Docker daemon..."
    sudo service docker start
fi

# ── 3. Build 沙盒映像檔
# --rebuild：強制砍掉舊 image + 舊 container，重裝（改 Dockerfile / requirements.txt 後用）
# 沒 --rebuild：沒 image 才 build；有就跳過（fresh clone 會 build；重跑不浪費時間）
if [[ "$REBUILD" == "yes" ]]; then
    echo "==> 強制 rebuild（--rebuild）：先移除舊 container + image..."
    $DOCKER rm -f "$CONTAINER" 2>/dev/null || true
    $DOCKER rmi -f "$IMAGE" 2>/dev/null || true
    echo "==> 重建映像檔 $IMAGE（約 5-10 分鐘，含 Node.js + 所有 pip 套件）..."
    $DOCKER build --no-cache -t "$IMAGE" "$PROJECT_DIR/sandbox"
    echo "✓ 映像檔已 rebuild"
elif [[ "$($DOCKER images -q $IMAGE 2>/dev/null)" == "" ]]; then
    echo "==> Build 沙盒映像檔 $IMAGE（首次約 5-10 分鐘）..."
    $DOCKER build -t "$IMAGE" "$PROJECT_DIR/sandbox"
    echo "✓ 映像檔已建立"
else
    echo "✓ 映像檔已存在：$IMAGE"
    echo "  （改了 Dockerfile / requirements.txt 要生效，加 --rebuild 重跑本腳本）"
fi

# ── 4. 計算 AGENTS_DIR（永遠都做、後面 default_skills 安裝跟 container mount 都要用）
# 找出 Windows 使用者 home 對應的 WSL 路徑（/mnt/c/Users/XXX）
WIN_USER=$(echo "$PROJECT_DIR" | sed -n 's|^/mnt/\([a-z]\)/Users/\([^/]*\)/.*|\2|p')
DRIVE_LETTER=$(echo "$PROJECT_DIR" | sed -n 's|^/mnt/\([a-z]\)/.*|\1|p')
if [[ -n "$WIN_USER" && -n "$DRIVE_LETTER" ]]; then
    USER_HOME_WSL="/mnt/$DRIVE_LETTER/Users/$WIN_USER"
else
    # 專案不在 /mnt/c/Users/... 下（例如放在 D:\ 或其他位置）
    # → 仍讓 ~/.agents 有 fallback，指到 Windows 預設 C:\Users\<current>\.agents
    USER_HOME_WSL="/mnt/c/Users/$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')"
    echo "ℹ 專案不在 /mnt/<drive>/Users/... 下，.agents 將定位到：$USER_HOME_WSL"
fi
AGENTS_DIR="$USER_HOME_WSL/.agents"
mkdir -p "$AGENTS_DIR/skills"

# ── 4b. 安裝預設 skill（idempotent、不覆蓋使用者已有版本）
# repo 內的 default_skills/ 是專案的「出廠技能包」（兩個 V5 專屬 skill）：
#   • scraped-content-parser — 爬蟲節點抓回來的原始內容結構化
#   • python-cli-extractor   — 把現成的 Python GUI/Web app 無破壞性接進 V5 pipeline
# Office 三件套（docx/pptx/xlsx）使用者自己從 Anthropic / Claude Code 安裝、不 bundle。
# 已存在的 skill 一律跳過、保留使用者的版本（可能他自己改過或升過級）
DEFAULT_SKILLS_DIR="$PROJECT_DIR/default_skills"
if [[ -d "$DEFAULT_SKILLS_DIR" ]]; then
    installed=0
    skipped=0
    for src in "$DEFAULT_SKILLS_DIR"/*/; do
        name=$(basename "$src")
        target="$AGENTS_DIR/skills/$name"
        if [[ -d "$target" ]]; then
            ((skipped++))
        else
            cp -r "$src" "$target"
            ((installed++))
        fi
    done
    if (( installed > 0 )); then
        echo "✓ 已安裝 $installed 個預設 skill 到 $AGENTS_DIR/skills/"
    fi
    if (( skipped > 0 )); then
        echo "  ($skipped 個 skill 已存在、保留使用者版本)"
    fi
fi

# ── 5. 啟動 / 重建容器
if $DOCKER ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    # 已存在 → 確認是否 running
    if $DOCKER ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
        echo "✓ 容器 $CONTAINER 已經在跑"
    else
        echo "==> 容器 $CONTAINER 存在但已停止，啟動中..."
        $DOCKER start "$CONTAINER"
    fi
else
    echo "==> 建立並啟動容器 $CONTAINER..."
    # ── Bind mount 策略 ──────────────────────────────────────────
    # 需要讓容器看到三類檔案（都用「同路徑映射」，不翻譯路徑）：
    #   (1) 專案本體：$PROJECT_DIR（讓使用者工作流產出存 ai_output/ 時兩邊同步）
    #   (2) Agent Skills：$AGENTS_DIR（skill 掛載時 LLM 呼叫 scripts/）
    #   (3) 容器內的 $HOME 也指到同一份 .agents，這樣 Path.home() / ".agents"
    #       在容器跟 Windows 都指向同一個地方
    $DOCKER run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        -v "$PROJECT_DIR:$PROJECT_DIR" \
        -v "$AGENTS_DIR:$AGENTS_DIR" \
        -v "$AGENTS_DIR:/root/.agents" \
        -w "$PROJECT_DIR" \
        "$IMAGE"
    echo "✓ 容器已啟動，掛載："
    echo "    $PROJECT_DIR → $PROJECT_DIR（專案本體）"
    echo "    $AGENTS_DIR → $AGENTS_DIR（Agent Skills，絕對路徑相容）"
    echo "    $AGENTS_DIR → /root/.agents（容器內 ~/.agents 相容）"
fi

# ── 4b. FlareSolverr（web_crawler 節點 Tier 2 用：解 Cloudflare challenge）
# 走 docker compose；compose file 在 $PROJECT_DIR/sandbox/docker-compose.yml
# 失敗不擋整體（爬蟲 Tier 1 仍可運作，只是遇到 CF 站會 fallback 失敗）
echo ""
echo "==> 啟動 FlareSolverr（web_crawler 節點 Tier 2 fallback）..."
if $DOCKER compose version &>/dev/null; then
    if (cd "$PROJECT_DIR/sandbox" && $DOCKER compose up -d flaresolverr); then
        echo "✓ FlareSolverr 已啟動：http://localhost:8191"
    else
        echo "⚠ FlareSolverr 啟動失敗（不影響 Tier 1 爬蟲；遇到 Cloudflare 時 Tier 2 會無法 fallback）"
    fi
else
    echo "⚠ docker compose 不可用，跳過 FlareSolverr（你的 docker 版本太舊？升級到 20.10+ 即可）"
fi

# ── 5. 冒煙測試
echo ""
echo "==> 冒煙測試 — 核心套件："
if ! $DOCKER exec "$CONTAINER" python -c "import pandas, openpyxl, numpy, requests; print('  ✓ Tier 1-2 OK')"; then
    echo "✗ 核心套件測試失敗"
    exit 1
fi

echo "==> 冒煙測試 — 進階套件（Tier 4-5）："
$DOCKER exec "$CONTAINER" python -c "
missing = []
for name in ['pptx', 'pdfplumber', 'newspaper', 'cloudscraper', 'feedparser', 'fake_useragent']:
    try:
        __import__(name)
    except Exception as e:
        missing.append(f'{name} ({e.__class__.__name__})')
if missing:
    print('  ⚠ 缺少：', ', '.join(missing))
    print('    解法：setup_sandbox.bat --rebuild 重建')
else:
    print('  ✓ python-pptx / pdfplumber / newspaper3k / cloudscraper / feedparser / fake_useragent 全部 OK')
" || true

echo "==> 冒煙測試 — web_crawler（Crawl4AI + Playwright Chromium）："
$DOCKER exec "$CONTAINER" python -c "
import sys
try:
    import crawl4ai
    # crawl4ai 0.8 把版本放在 .__version__.__version__；舊版可能直接是字串
    v = getattr(crawl4ai, '__version__', None)
    v = getattr(v, '__version__', v)
    print(f'  ✓ crawl4ai {v}')
except Exception as e:
    print(f'  ⚠ crawl4ai 未安裝（{e.__class__.__name__}）— setup_sandbox.bat --rebuild')
try:
    import trafilatura, html2text
    print('  ✓ trafilatura + html2text')
except Exception as e:
    print(f'  ⚠ trafilatura / html2text 缺：{e}')
# 用 Playwright 自己 API 拿 chromium binary 路徑（最可靠；版本不同子目錄名稱會變）
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        cp = p.chromium.executable_path
    import os
    if cp and os.path.exists(cp):
        print(f'  ✓ Chromium binary：{cp}')
    else:
        print(f'  ⚠ Chromium binary 不存在：{cp} — setup_sandbox.bat --rebuild')
except Exception as e:
    print(f'  ⚠ Playwright Chromium 偵測失敗（{e.__class__.__name__}）— setup_sandbox.bat --rebuild')
" || true

echo "==> 冒煙測試 — Node.js + pptxgenjs："
if $DOCKER exec "$CONTAINER" bash -c 'node --version && npm list -g --depth=0 2>/dev/null | grep pptxgenjs' >/dev/null 2>&1; then
    NODE_VER=$($DOCKER exec "$CONTAINER" node --version 2>/dev/null)
    echo "  ✓ Node.js $NODE_VER + pptxgenjs OK"
else
    echo "  ⚠ Node.js 或 pptxgenjs 未安裝（解法：setup_sandbox.bat --rebuild）"
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo "✓ 沙盒就緒！"
echo "  容器名：$CONTAINER"
$DOCKER inspect "$CONTAINER" --format '{{range .Mounts}}    {{.Source}} → {{.Destination}}{{"\n"}}{{end}}' 2>/dev/null || true
echo "══════════════════════════════════════════════════════"

# ── 6. 寫旗標讓 setup_sandbox.bat 知道要不要提醒使用者關閉 WSL
# 條件：當前還在用 sudo 跑 docker (代表 docker group 還沒 reload)
# 旗標檔讀完即刪、不留下殘留
FLAG_FILE="$PROJECT_DIR/sandbox/.needs_wsl_shutdown"
if [[ "$DOCKER" == "sudo docker" ]]; then
    touch "$FLAG_FILE" 2>/dev/null || true
else
    rm -f "$FLAG_FILE" 2>/dev/null || true
fi
