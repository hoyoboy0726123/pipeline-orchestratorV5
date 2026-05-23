# `google_action` 節點研究 / 開發計畫

> 撰寫日期:2026-05-24
> 目的:在 V5 加入「Google 全家桶統一節點」、讓任何 workflow 透過單一 OAuth 憑證存取 Gmail / Calendar / Drive / Sheets / Docs / 等 service。
> 設計目標:**讓這個節點成為 V5 對外世界(個人雲端)的統一窗口**,憑證集中、審計集中、AI 助手規劃時有明確路徑。
> 狀態:**開發計畫** — Phase 1 等使用者確認後動工。

---

## 一、戰略定位

### 目前 V5 對外世界的窗口(碎裂)

| 窗口 | 用途 | 限制 |
|---|---|---|
| `web_crawler` | 抓任意網頁 | 沒 auth、cookie 難管、易被擋 |
| `web_search` (Tavily) | 探索式搜尋 | 1000/月 free tier |
| `outlook_automation` | 企業信 | 限 Windows + Outlook COM |
| `send_file_to_tg` | 通知 / 檔案 | 50MB cap、TG only |
| Host `win32com` | Office 桌面 | 限 Windows + 桌面 |
| Sandbox `requests / httpx` | 任意 HTTP | 沒 auth 框架、token 自己管 |

### 加 `google_action` 後

**一個 node、一份憑證、全套 Google 雲端服務**:
- 個人 Gmail / Calendar / Drive / Sheets / Docs 統一管理
- OAuth 憑證在設定頁一次完成、workflow 不再碰 secret
- 對外存取走統一審計層(audit log)
- AI 助手規劃 workflow 時有明確路徑可循

### 取代 vs 互補

| 對既有 | 關係 |
|---|---|
| 寫 `.xlsx` 到 host | **部分取代**(Sheets 跨平台、可分享、Google 也能跑 formula) |
| 寫 `.docx` 到 host | **部分取代**(Docs 同上) |
| host filesystem 跨機共享 | **取代**(Drive 是真正的解) |
| `web_search` (Tavily) | **獨立**(Tavily 仍是探索搜尋首選) |
| `web_crawler` | **獨立**(爬非 Google 站還是要) |
| `outlook_automation` | **互補**(Exchange ≠ Gmail、使用者選一邊) |
| `send_file_to_tg` | **互補**(即時通知 ≠ 長存歸檔) |

---

## 二、Scope 分層

### Tier 1 — MVP 必含(個人自動化 80% 用量)

| Service | 主要 action |
|---|---|
| **Gmail** | search / read / send / draft / label / attachments |
| **Calendar** | list_events / create_event / find_free_slot / update_event |
| **Drive** | list / search / upload / download / share / move |
| **Sheets** | read / append_row / batch_update / formula |
| **Docs** | create / read / append / find_replace / share |

### Tier 2 — 中頻特定場景

Tasks / Slides / People (Contacts) / Translate / YouTube Data v3 / Forms

### Tier 3 — 進階 / 需 GCP 帳單

Maps / Places / GA4 / Search Console / BigQuery / Cloud Storage / Vision / Speech / Vertex AI / Chat

### 不建議優先做

Photos(API 受限)、Keep(無公開 API)、Sites(寫很弱)、Meet(只能透過 Calendar 順帶建)。

---

## 三、架構設計

### Hybrid 模式:一個 node + service/action selector + free-form fallback

跟既有 `outlook_automation` 同 pattern。3 種使用模式:

**模式 1 — 模板填空**(70% 用量):
```yaml
- name: 抓今日行事曆
  google_action:
    account: personal
    service: calendar
    action: list_events
    params:
      since: today
      until: tomorrow
      max_results: 20
  output:
    path: today_events.json
```

**模式 2 — LLM batch**(20%、複雜 / 多步):
```yaml
- name: 整理上週未回信
  google_action:
    account: personal
    service: gmail
  batch: |
    讀上週收件匣 unread + unanswered 信、
    按主題分類、寫成 markdown 摘要存 summary.md。
    超過 5 天沒回的標記 [URGENT] 在前面。
```
走 skill_mode 變體:LLM 拿到 google_action client + scope 提示、用 `run_python` 寫 code 呼 API。

**模式 3 — 跨 service workflow**(10%、進階):
```yaml
- name: 會議 prep
  google_action:
    account: personal
  batch: |
    1. Calendar 找下個會議
    2. 抓會議邀請信 (Gmail)
    3. 找參與者背景 (People + web_search)
    4. Drive 新建 prep doc、貼結論
    5. Calendar 把 doc URL 加進 event description
```

### Node 內部架構

```
backend/pipeline/google_action/
├── client.py             # OAuth + token refresh 統一
├── services/
│   ├── gmail.py          # GmailService
│   ├── calendar.py       # CalendarService
│   ├── drive.py
│   ├── sheets.py
│   └── docs.py
├── actions.py            # template registry (service.action → handler)
├── batch_runner.py       # 模式 2/3 LLM batch loop
└── audit.py              # 審計 log
```

---

## 四、OAuth + 憑證(關鍵設計)

### 兩種模式並存

**Mode A — OAuth 2.0 user consent**(預設、個人用)
- GCP console 建 OAuth 2.0 Client(desktop application type)
- 下載 `client_secrets.json` → V5 設定頁上傳
- 設定頁「連結 Google 帳號」→ 開瀏覽器 → 使用者授權 → 拿回 `refresh_token`
- access token 過期 V5 自動 refresh
- **支援多帳號**:存多份 token、workflow 內 `account: work` / `personal` 切換

**Mode B — Service Account**(進階 / 自動化 daemon)
- 純 Sheets / BigQuery / Cloud Storage 場景
- 沒有個人 Gmail / Drive(除非 Workspace 開 domain-wide delegation)

### Token 儲存

**選擇:加密 JSON 檔**
```
~/ai_output/google_credentials/
├── client_secrets.json   # GCP console 下載、低敏感
├── tokens.enc            # 加密的 refresh_token + access_token (fernet)
└── encryption_key        # 32-byte、chmod 600、.env 備援一份
```

理由:獨立 / 好備份 / 好遷移 / 不污染 pipeline.db schema。

### Scope 最小化

預設只開 **read-only**;寫 scope 要勾才啟用;寫操作 workflow 內走兩步協議。

設定頁 checkbox:
```
Gmail
  ☑ 讀信 (gmail.readonly)
  ☐ 寄信 + 修改 (gmail.send + gmail.modify)
Calendar
  ☑ 讀行事曆 (calendar.readonly)
  ☐ 建 / 改事件 (calendar.events)
Drive
  ☑ 讀 / 列檔案 (drive.readonly)
  ☐ 寫 / 改 / 刪 (drive)
...
```

### 多帳號 UX

```
Google 帳號連結
[+ 連結新帳號]

已連結:
  📧 wilson@personal.com    (個人)
     Gmail ✓  Calendar ✓  Drive ✓  Sheets ✓
     [重新授權] [中斷連結]

  📧 wilson@company.com     (工作)
     Gmail ✓  Calendar ✓  Drive ✓
     [重新授權] [中斷連結]
```

workflow 內 `account: personal`(alias) 或完整 email。

---

## 五、AI 助手 chat 直接操作(關鍵 UX 設計)

### 不是只有透過 workflow 才能用

**TG / 桌面 AI 助手 chat 內、自然語言能直接驅動 Google 操作**,不必每次都畫 workflow 或派 subagent。

**設計**:新增**一個** chat tool `google_action(service, action, params, confirm, account)`(不是每個 action 一個 tool、避免撐爆 tool list)。

```python
@tool
def google_action(
    service: str,           # gmail | calendar | drive | sheets | docs | ...
    action: str,            # service-specific
    params: dict,
    account: str = "default",
    confirm: bool = False,  # 寫操作必走兩步
) -> str:
    """所有 Google 服務的統一入口"""
```

System prompt 動態注入「每個 service 支援的 action 清單 + 參數 schema」,AI 看得到完整能力地圖。

### Read vs Write 行為差異

| 操作類型 | 行為 |
|---|---|
| **Read**(無風險:gmail.search、calendar.list_events、drive.list、sheets.read、docs.read) | AI 直接呼、立即回結果給使用者、不必 confirm |
| **Write**(gmail.send、calendar.create_event、drive.upload、sheets.write、docs.create) | **強制兩步協議**:`confirm=False` 預覽 → AI 用文字告訴使用者「我要做 X、確認?」→ 使用者 yes → `confirm=True` 真寫 |

### 何時畫 workflow vs 直接 chat 操作(寫進 AI 助手 prompt)

| 訊號 | 行為 |
|---|---|
| 「現在 / 立刻 / 馬上 / 一下 / 順便」 | **直接 chat 工具**、不畫 workflow |
| 「每天 / 每週 / 定時 / 自動化 / 排個程」 | **畫 workflow**(可排程 / 可重複跑) |
| 一次性、不確定要重跑 | 預設**不畫**(更輕量) |
| 多步驟邏輯固定、長期會用 | **畫 workflow** |
| 使用者要看 progress / 留紀錄 | 畫 workflow(canvas + run log) |

### 對話範例

```
使用者: 我下午有什麼會
AI:    [google_action(service=calendar, action=list_events, params={since:'14:00', until:'18:00'})]
       下午 14:00 跟客戶 X 開會、15:30 內部 review。

使用者: 寄信給 john 跟他說我會晚 10 分鐘到
AI:    我打算用 personal 帳號寄信給 john@company.com、主題「會議延遲」、
       內文「Hi John, 我大概晚 10 分鐘到、抱歉」、確認?
使用者: 好
AI:    [google_action(send, confirm=True, ...)]  ✅ 已寄出。

使用者: 把這個 markdown 變成 Google Doc 給我分享連結
AI:    我要新建 Google Doc 標題「[檔名]」、內容 N 字、設成可看連結權限、確認?
使用者: 好
AI:    [google_action(docs.create + drive.share, confirm=True)]  ✅ 文件:https://...

使用者: 每天早上 8 點幫我整理當日會議跟未讀重要信
AI:    這需要排程,我幫你建一個 workflow,跑 cron 每天 08:00。
       [畫 workflow yaml → YAML_READY block]
```

---

## 六、YAML schema(完整)

```yaml
- name: <step name>
  google_action:
    account: personal             # 必填、設定頁連結的 alias 或完整 email
    service: gmail | calendar | drive | sheets | docs | tasks | ...
    action: <service-specific>    # 模式 1 用;模式 2/3 不填
    params:                       # 模式 1 參數
      <key>: <value>
  batch: |                        # 模式 2/3 用、跟 action 互斥
    自由描述任務
  confirm: false                  # 寫操作必加;false=預覽 / true=真執行
  output:
    path: <relative file>
    description: <desc>
  timeout: 300
```

---

## 七、新解鎖場景(20 個 V5 目前做不到的)

### 日常通勤 / 工作流(每天)
1. **早晨摘要**:cron 06:30 → Calendar 今日 + Gmail 過夜未讀 → LLM 整理 → TG 卡片
2. **會議 prep**:會議前 30 分 → 參與者背景 + 相關郵件串 → Doc → Calendar attachment
3. **下班送日報**:18:00 → 多個 sheets / docs / 對話 → Gmail 寄主管 + 抄自己
4. **未讀分類**:每小時掃新信 → LLM 分類 → 自動 label + 重要的推 TG
5. **電子報摘要**:newsletter 週日 → LLM 看 30 封 → 5 個重點 → Doc

### 多人協作(每週)
6. **共識會議自動排**:3 個人名 → find_free_slots → 建 event + Meet + 邀請 + prep doc
7. **協作 doc 變更通知**:Doc 被改 → diff → LLM 摘要 → TG
8. **Form 收回後客製回信**:200 份回應 → 分群 → 客製 follow-up

### 資料分析(每月)
9. **跨平台 KPI dashboard**:GA4 + Sheets + Stripe → pivot → 月度 Doc
10. **競品 YouTube 監測**:5 個 channel → 最近上傳 → LLM 摘要 → Slides
11. **訂閱 / 發票管理**:Gmail invoice → 金額 / 日期 / 訂閱名 → Sheets → 月底報表
12. **網站週報**:Search Console + GA4 → top queries / 變化 → Doc

### 個人生產力(每月)
13. **航班 / 訂房整合**:Gmail 訂單 → 解析 → Calendar 行程 → Maps 交通時間
14. **跨檔案 RAG**:Drive PDF → 索引 → AI 答問引用「3 月前那份 X」
15. **聯絡人維護**:People API 重複 / 久未聯絡 → Sheets 列表
16. **個人習慣追蹤**:Sheets 一欄填運動 / 讀書 / 寫作 hour → 月底 Doc 趨勢圖

### 進階威力大
17. **客服初回應**:共用 Gmail 監看 → LLM 草稿 → 兩步 confirm → 真寄(24/7 客服)
18. **多語化 pipeline**:英文 Doc → Translate → 5 語 → 各市場 PM
19. **影片字幕回流**:YouTube 新片 → 字幕 → Translate → 上傳多語版
20. **公司會議室 + 設備預訂**:Calendar 空會議室 + Maps 訂車

---

## 八、風險與緩解

| 風險 | 緩解 |
|---|---|
| OAuth token 外洩 | 加密儲存、`.env` 不入 git、設定頁顯示「上次活動」、可即時 revoke |
| API quota 滿 | 每個 service 各自 rate limiter、429 自動 backoff、設定頁顯示 quota 使用率 |
| Google 改 API / deprecation | 鎖 google-api-python-client 版本、每季 review release notes |
| AI 助手 abuse | 寫操作強制 confirm=True、deny-list 不准寄到沒設定過的新 domain、audit log 全寫 |
| Vendor lock-in | services/ 抽 interface,未來可加 Microsoft Graph 對應實作 |
| LLM 拿到 Gmail 內文外流 | 信用卡 / 身分證 自動 redact 再送 LLM;敏感場景強制走本地 Ollama |
| 多帳號搞混 | YAML 必填 `account:`、漏寫 fail;UI 高亮 account email |
| Tier 3 GCP 燒錢 | 設定頁設定月度上限、超過 alert;Tier 3 預設關閉 |
| 同步重複 send | client-side idempotency key (message hash) |
| 誤點中斷連結 | 提示「N 個 workflow 在用、確定?」 |

---

## 九、實作 Phase

### Phase 1 — MVP(5-7 工作天)

- [ ] GCP OAuth 2.0 client 設定 + 教學 README
- [ ] OAuth flow 後端(refresh token 拿 / 存 / 加密)
- [ ] 設定頁 UI:連結 / 多帳號 / scope 勾選 / quota 顯示
- [ ] `pipeline/google_action/client.py` 統一 client(token refresh / rate limit / error)
- [ ] **Gmail**:search / read / send / 4 action
- [ ] **Calendar**:list_events / create_event / find_free_slot / 3 action
- [ ] **Drive**:list / upload / download / share / 4 action
- [ ] Node panel(學 outlook_automation)
- [ ] **Chat tool `google_action`**(read 直接 / write 兩步)
- [ ] AI 助手 prompt 加章節 + 動態注入「已連結帳號 + 可用 action」
- [ ] 範例 workflow:`meeting_prep_assistant`

### Phase 2 — 結構化資料(3-5 工作天)

- [ ] **Sheets**:5 action
- [ ] **Docs**:5 action
- [ ] AI 助手 skill_mode 內 import google client 寫 code
- [ ] Token refresh background job
- [ ] Audit log

### Phase 3 — 中頻擴展(3-5 工作天)

- [ ] Tasks / Slides / People / Translate / YouTube Data / Forms

### Phase 4 — GCP tier(選做)

- [ ] Maps / Places / GA4 / Search Console
- [ ] BigQuery / Cloud Storage
- [ ] Vision / Speech / Document AI

### 持續強化

- [ ] LLM redact 規則
- [ ] Panel inline account switcher
- [ ] Sheets-as-config(workflow 從 sheet 跑 batch task)

---

## 十、開工前決策(預設選項)

| Q | 預設 |
|---|---|
| OAuth 模式 | **user OAuth 預設、保留 service account 進階** |
| 多帳號 | **多帳號 + alias** |
| Token 儲存 | **加密 JSON 檔 + chmod 600** |
| Phase 1 service 範圍 | **Gmail + Calendar + Drive** 3 個 |
| Gmail 內文 redact | **強制 redact 信用卡 / 身分證** |
| AI chat 直接操作 | **Read 直接呼、Write 兩步確認** |
| `list_google_accounts` chat tool | 加(read,讓 AI 知道有哪些帳號可用) |
| OAuth 設定工具給 AI | **不加**(那是設定頁的事、AI 不該代授權) |

---

## 十一、第一個 demo workflow(Phase 1 完成後)

**`meeting_prep_assistant`** — 一鍵展示 Gmail + Calendar + Drive + Docs + subagent + TG 6 個系統串接。30 行 YAML 取代手寫 200 行 Python。詳見 §11 of source proposal。

---

## 十二、相關文件

- `docs/web-crawler-node-proposal.md` — 通用爬蟲節點(對應)
- `docs/ROADMAP.md` — V5 整體路線圖
- backend `pipeline/outlook_handler.py` — outlook_automation 是本節點的參考實作
