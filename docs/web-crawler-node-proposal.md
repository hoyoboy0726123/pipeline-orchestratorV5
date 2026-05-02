# Web Crawler 節點研究 / 提案

> 撰寫日期：2026-04-28
> 目的：在 V5 加入「通用網站爬取節點」，能解析任何網站、繞過常見防爬機制、輸出格式對 AI 友善（後續接 skill 節點分析）
> 狀態：**研究 / 提案** — 尚未實作，等使用者確認方向

---

## 一、使用情境

```
[爬蟲節點]   →   [skill 節點]   →   [後續處理]
URL 進        Markdown 出        AI 摘要 / 翻譯 / 分類
```

例如：
- 抓 PTT 八卦版 → AI 整理熱門話題
- 抓競品官網商品頁 → AI 比較規格
- 抓新聞網站 → AI 摘要 + 翻譯
- 抓論壇討論串 → AI 分析情緒

**核心需求**：
1. 任何網站都能解析（靜態 HTML / SPA / 重 JS）
2. 繞過防爬（Cloudflare、UA 檢查、JS challenge、fingerprinting）
3. 輸出格式給 LLM 吃最順
4. 後續可接 skill 節點，skill 從 `outputPath` 讀取就能分析

---

## 二、輸出格式建議

### 業界共識（2024–2026）

LLM 處理網頁內容的最佳格式 = **Markdown + YAML frontmatter**

理由：
- LLM 訓練資料含大量 Markdown，理解原生
- 保留結構（標題層級、列表、表格、連結、code block）
- 比 HTML 省 token（一篇 5000 字文章 HTML ~50KB → MD ~10KB）
- 易於切片（按 `##` 分段做 RAG）
- 人工 debug 也好讀

**主流工具產出格式對照**：

| 工具 | 主要輸出 | 適用 |
|---|---|---|
| **Crawl4AI** | Markdown + 結構化 JSON | LLM 餵入（業界主推） |
| **Firecrawl** | Markdown + metadata JSON | LLM 餵入（SaaS / 自架） |
| **Jina Reader** | Markdown | LLM 餵入（API） |
| **Trafilatura** | 純文字 / XML / Markdown | 學術文本研究 |
| **MarkItDown**（MS） | Markdown | 檔案轉換、不爬蟲 |
| **html2text** | Markdown | 簡單轉換 |
| **Mozilla Readability** | 清理後 HTML | 閱讀模式 |

### 推薦的最終格式

**單頁** → 一個 `.md` 檔，帶 YAML frontmatter：

```markdown
---
url: https://example.com/article/123
title: 文章標題
fetched_at: 2026-04-28T12:34:56+08:00
status_code: 200
content_type: text/html
language: zh-tw
word_count: 1234
canonical_url: https://example.com/article/123
description: SEO description from <meta>
keywords: [關鍵字1, 關鍵字2]
author: 作者
published_at: 2026-04-15T10:00:00Z
links_internal: 12         # 站內連結數量
links_external: 3
images: 5
crawler:
  engine: crawl4ai
  anti_bot_level: 2
  js_rendered: true
  duration_ms: 1842
---

# 文章標題

正文內容（已轉成 Markdown）...

## 子標題

- 列表
- 項目

| 表 | 格 |
|---|---|
| ... | ... |

[連結文字](https://example.com/foo)

![圖片描述](./assets/img_001.jpg)
```

**多頁 / 整站** → 一個資料夾結構：

```
ai_output/<pipeline>/<step>/
├── index.json                  # 整批 metadata（爬了哪些 URL、成功/失敗、時間）
├── pages/
│   ├── 001_<slug>.md          # 一頁一檔
│   ├── 002_<slug>.md
│   └── ...
└── assets/                     # 圖片 / PDF / 其他下載資源
    └── ...
```

`index.json` 範例：
```json
{
  "crawled_at": "2026-04-28T12:34:56+08:00",
  "root_url": "https://example.com",
  "mode": "sitemap",
  "total_pages": 47,
  "successful": 45,
  "failed": 2,
  "pages": [
    {
      "file": "pages/001_homepage.md",
      "url": "https://example.com/",
      "title": "首頁",
      "status": 200,
      "fetched_at": "..."
    },
    ...
  ],
  "errors": [
    { "url": "...", "error": "403 Forbidden" }
  ]
}
```

---

## 三、防爬蟲層次 / 工具選型

### Tier 1：基本（默認、~95% 網站可用）

**核心：Crawl4AI**（https://github.com/unclecode/crawl4ai）

- LLM-optimized 開源 Python 爬蟲，2024 起最熱門
- 內建：
  - Playwright + Chromium（真實瀏覽器渲染 JS）
  - playwright-stealth 隱藏自動化痕跡
  - 自動 User-Agent / viewport / headers 偽裝
  - Markdown 輸出（直接送 LLM）
  - 結構化內容過濾（去掉導覽列 / 廣告 / footer）
  - 並發、sitemap 解析、深度爬取
- MIT 授權、純本機跑、無需 API key
- 安裝：`pip install crawl4ai && playwright install chromium`（一次性 ~500MB）

**這層搞定**：一般 blog / 新聞 / 電商 / 論壇 / 文件站

### Tier 2：Cloudflare / DataDome（~99% 網站）

加掛 **FlareSolverr**（https://github.com/FlareSolverr/FlareSolverr）

- 獨立 Docker 服務（HTTP API）
- 內部用 Puppeteer 解 Cloudflare JavaScript challenge
- 拿到 `cf_clearance` cookie 後丟回給 Crawl4AI 用
- 偵測流程：Tier 1 失敗回 403 / 5xx 顯著為 CF → fallback 走 FlareSolverr → 拿 cookie 後 retry

**部署選項**：
- A. 利用 V3+ 既有 WSL Docker，加一個 `flaresolverr` container（推薦）
- B. 不部署，遇到 CF 直接回報失敗（節點 retry 策略由使用者決定）

### Tier 3：硬核（CAPTCHA / 高階 anti-bot / 國家級牆）

**選項**（都需要付費 API key）：
- **2Captcha / Anti-Captcha**：解圖型 / hCaptcha / reCAPTCHA
- **Bright Data / Oxylabs Web Unlocker**：商用 SaaS，「丟 URL 進去吐 HTML 出來」、會自動處理所有 anti-bot
- **ZenRows / ScrapingBee / Firecrawl**：類似但便宜

> **建議**：第一階段不做 Tier 3。等真的遇到打不下來的網站再加，且做成 settings 裡的 API key 欄位，按 URL pattern 動態 fallback。

### Tier 摘要

| Tier | 工具 | 適用 | 額外成本 |
|---|---|---|---|
| 1（默認） | Crawl4AI | 95% 網站 | 一次安裝 ~500MB |
| 2（Cloudflare） | + FlareSolverr (Docker) | 99% 網站 | 一個 container |
| 3（極端） | + 2Captcha / Bright Data 等 | 99.9% 網站 | API 付費 |

---

## 四、節點架構草圖

### Frontend（仿 V5 既有節點 pattern）

```
frontend/app/pipeline/
├── _webCrawlerNode.tsx        # canvas 上的節點外觀
└── _webCrawlerPanel.tsx       # 點擊後的設定 panel
```

`_helpers.ts` 增加 `WebCrawlerData` 型別 + `newWebCrawlerData()`、`stepsToFlow` 多分支、`flowToSteps` 多分支、`stepsToYaml` / `parseYaml` 多分支。

`_sidebar.tsx` 增加「+ 網站爬蟲」按鈕。

`page.tsx` 增加 `<WebCrawlerPanel />` 渲染分支。

### Backend

```
backend/pipeline/
├── web_crawler.py             # 爬蟲引擎（Crawl4AI wrapper + Tier 邏輯）
└── (可選) flaresolverr_client.py  # CF fallback 客戶端
```

`executor.py` 增加 `web_crawler` step 處理分支（仿 outlook_automation / computer_use）。

`models.py` 增加 `WebCrawlerStep` dataclass。

### YAML schema 範例

```yaml
- name: 抓官網商品頁
  web_crawler: true
  urls:
    - https://example.com/products/abc
  mode: single                    # single / list / sitemap / deep
  js_render: true                 # 默認 true（用 Crawl4AI 的 Playwright）
  anti_bot_level: 2               # 1=basic / 2=Crawl4AI 預設 / 3=Tier 2 (FlareSolverr)
  wait_for_selector: ".product"   # 可選，等指定元素出現再抓
  max_depth: 1                    # mode=deep 時用
  max_pages: 50                   # 整站抓的上限
  include_url_pattern: ".*/products/.*"  # 正則白名單
  exclude_url_pattern: ".*/login.*"      # 正則黑名單
  download_assets: false          # 是否下載圖片 / PDF
  output:
    path: ai_output/{name}/       # 輸出資料夾
  timeout: 180                    # 秒
  retry: 1
```

### Frontend Panel UI 草圖

```
┌────────────────────────────────────────┐
│ 🌐 網站爬蟲                       [✕] │
├────────────────────────────────────────┤
│ 節點名稱：[網站爬蟲 1            ]    │
│                                        │
│ ┌─ 目標 ─────────────────────────┐    │
│ │ URL（一行一個）                │    │
│ │ ┌────────────────────────────┐ │    │
│ │ │ https://example.com/...    │ │    │
│ │ └────────────────────────────┘ │    │
│ │                                │    │
│ │ 模式：( ) 單頁                  │    │
│ │       ( ) 多 URL（上面列表）    │    │
│ │       ( ) 整站（自動跟連結）    │    │
│ │       ( ) Sitemap (.xml)        │    │
│ └────────────────────────────────┘    │
│                                        │
│ ┌─ 抓取設定 ─────────────────────┐    │
│ │ ☑ 啟用 JS 渲染（適合 SPA）     │    │
│ │ 防爬等級：[Tier 2 ▼]            │    │
│ │   1 = 基本（快、~95% 網站）     │    │
│ │   2 = +Cloudflare 處理（默認）  │    │
│ │   3 = +captcha/proxy（需 key）  │    │
│ │ 等待選擇器：[..............]    │    │
│ │ 最大頁數：[50]  最大深度：[2]   │    │
│ │ 下載附件：☐                     │    │
│ └────────────────────────────────┘    │
│                                        │
│ ┌─ URL 過濾 ─────────────────────┐    │
│ │ 包含（regex）：[..........]     │    │
│ │ 排除（regex）：[..........]     │    │
│ └────────────────────────────────┘    │
│                                        │
│ ┌─ 輸出 ─────────────────────────┐    │
│ │ 資料夾：[ai_output/{name}/   ] │    │
│ │ 格式：( ) 單一 .md（單頁時）    │    │
│ │       ( ) 多檔 + index.json    │    │
│ └────────────────────────────────┘    │
│                                        │
│ Timeout：[180]s   Retry：[1]          │
└────────────────────────────────────────┘
```

---

## 五、實作分期建議

### Phase 1：MVP（2–3 天）
- Backend：`web_crawler.py` 用 Crawl4AI、支援單頁 + 多 URL、Markdown + frontmatter 輸出
- 整合 executor.py
- Frontend：基本節點 + panel（URL 列表、JS render toggle、輸出資料夾、timeout）
- 不做 sitemap / deep crawl / Cloudflare fallback
- 測試：抓 5 個不同類型網站（blog / 新聞 / SPA / GitHub / Cloudflare 保護）

### Phase 2：覆蓋率 + 抗封（1 週）
- 加入 sitemap 模式 / deep crawl（含 max_depth / max_pages / regex 過濾）
- 加入 FlareSolverr Docker fallback（Tier 2）
- 自動 fallback 邏輯：Tier 1 失敗 → 嘗試 Tier 2
- index.json 多頁 metadata
- 加入 wait_for_selector 等 SPA 細節控制

### Phase 3：品質 / 後處理（看需求）
- 內容濾波（去 nav / footer / sidebar，現成 Crawl4AI 有 BM25 / pruning filter）
- 重複偵測（避免同站不同 URL 抓到同內容）
- 增量爬取（記住上次 ETag / Last-Modified）
- 如有需要再加 Tier 3 商用 API 整合

---

## 六、跨版本影響

這是 **V5 only 的新節點**，**不需 backport** 到 V1~V4。

但會修改的「共用模組」：
- `executor.py`：加分支處理新 step type
- `models.py`：加 dataclass
- `_helpers.ts`：加 type / serializer / parser
- `_sidebar.tsx`：加按鈕
- `page.tsx`：加 panel 渲染

按專案慣例這幾個檔的修改是「給新節點開洞」、屬於純 V5 專屬功能，**不需問是否 backport**（沒人會把 V5 only 的 web_crawler 推到 V1）。

---

## 七、開放問題（請使用者確認）

**架構面**：

1. **節點命名**：「網站爬蟲」/「Web 爬取」/「網頁抓取」/ 直接英文「Web Crawler」？我推「網站爬蟲」(node label) + `web_crawler` (yaml key + node type)
2. **單 URL vs 多 URL**：Phase 1 MVP 是否需要支援多 URL 一次抓？還是先做單 URL，Phase 2 再加多 URL？
3. **整站爬（sitemap / deep）**：Phase 1 是否包含？還是先單頁就好？

**部署面**：

4. **Crawl4AI 安裝負擔**：第一次裝會跑 `playwright install chromium` 拉 ~500MB，可以接受嗎？或要找輕量替代（trafilatura + httpx，只能抓靜態網站）？
5. **WSL Docker sandbox**：爬蟲是否要在 V3+ 的 sandbox 容器裡跑（Linux 環境跑 Chromium 比較順）？還是直接 Windows host pip install？
6. **Cloudflare 處理**：Phase 1 不做、後續加？還是一開始就要？

**功能面**：

7. **輸出格式**：純 Markdown + frontmatter 是否符合需求？或還想加結構化欄位（例如自動抽取 schema.org JSON-LD）？
8. **下載附件**：要不要連同網頁的圖片 / PDF / 影片連結都下載成本機檔案？還是只在 markdown 留 URL 即可？
9. **Recipe cache**：爬蟲輸出是否要進 V1+ 的 recipe cache（同 URL + 同設定 → 直接用上次結果）？我建議**不進**，因為網頁內容會變，cache 沒意義；除非有「N 小時內視為快取有效」這種需求。

**進階**：

10. **登入後爬取**：要不要支援帶 cookies / session（例如先登入會員區再抓）？這會大幅複雜化 UX。
11. **JS 互動**：要不要支援先點擊某按鈕、滾動、輸入文字後再抓（例如點開「載入更多」）？
12. **排程整合**：跟現有 scheduler/manager.py 配合（例如每天爬一次新聞、累積成資料庫）？

---

## 八、決策後的下一步

使用者在七、八、九中挑出方向後：
1. 我寫一份「Phase 1 實作計畫」，列具體 file edits
2. 跟使用者確認再開始改 code
3. Phase 1 完成 → 在 V5 commit + 在 docs 補一份 `web-crawler-node.md` 記錄最終決策
