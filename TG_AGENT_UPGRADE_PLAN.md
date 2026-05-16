# TG Agent 改造計劃

> 建立日期:2026-05-16
> 目標:把 Telegram 上的 AI 助手從「工作流規劃器」升級成「有長期記憶、能多步驟執行的私人 agent」。
> 範圍說明:**只強化 TG 通道**。桌面面板維持現狀(規劃器),不升級。

---

## 1. 現況體檢

兩通道共用同一套核心(`_chat_agent_loop`、同一份 prompt、同一個 `CHAT_TOOLS`),
由「有沒有傳 `on_tool_event`」切換 desktop / telegram。TG 通道目前的硬傷:

| 問題 | 位置 | 影響 |
|---|---|---|
| 對話歷史全 in-memory | `telegram_handler.py:144` `_tg_chat_history` dict | backend 重啟即失憶 |
| `_tg_loaded_context` / `_tg_last_ai_yaml` 同樣 in-memory | `telegram_handler.py:151,155` | 同上 |
| 歷史只保留最近 30 則、無摘要 | `_TG_CHAT_HISTORY_CAP=30`(`telegram_handler.py:145,1250`) | 舊脈絡直接丟 |
| LLM 每輪只收最近 30 則 | `main.py:3459` `_CHAT_HISTORY_CAP=30` | 同上 |
| agent loop 上限 5 輪 | `main.py:3345` `_CHAT_MAX_TOOL_ITERATIONS=5` | 多步任務中途被砍 |
| 無任何跨對話 / 跨重啟記憶 | — | 不是「私人 agent」、只是「無狀態問答機」 |

工具現況:19 個(13 共用 + 6 TG 專屬),全圍繞「工作流管理 / 子代理派遣」,
沒有「記住事情」「查自己歷史決策」「讀寫任意檔案」這類 agent 基本能力。

---

## 2. 三階段藍圖

| 階段 | 主題 | 本計劃詳述? |
|---|---|---|
| 階段一 | 記憶系統 | ✅ 完整設計(第 3 節) |
| 階段二 | 工具強化 | 概要(第 4 節) |
| 階段三 | agent loop + 主動性 | 概要(第 4 節) |

建議:**先做完階段一**再評估二、三。階段一內部也照子步驟順序做,不要跳。

---

## ⚓ 貫穿原則:防止本業稀釋(每階段都適用)

> 背景:升級的最大風險不是技術、是**身分稀釋** — 工具一多,agent 就從
> 「工作流規劃師」漂成「萬能助理」,開始選錯工具、忘記本業(規劃工作流)。

### 兩種「加能力」要分清楚

| | 橫向擴張(危險) | 縱深 / 支援工具(安全) |
|---|---|---|
| 做的事 | 讓 agent 會做**新種類的任務**(寄信、爬蟲、修圖…) | 讓 agent 把**現有本業做得更好**(記憶、查過去規劃) |
| 後果 | 身分稀釋、選錯工具、忘本業 | 不換跑道,還是規劃工作流、只是更強 |

記憶系統(階段一)屬**右欄** — 純加分、無稀釋風險。風險區在階段二、三。

### 把關規則(階段二每個新工具 / 能力都要過)

1. **本業測試**:這工具是「幫 agent 規劃工作流」還是「讓它變萬能助理」?
   只有前者才加。後者一律拒絕、或改用「委派」實現(見規則 4)。
2. **身分宣告優先**:「你是工作流規劃助手」這句留在 system prompt **最前面、最顯眼**,
   地位不能被後面一堆工具守則蓋過。
3. **記憶框定成手段**:記憶守則明寫「記憶是為了把工作流規劃做得更好」—
   是服務本業的手段,不是新職責。
4. **委派優先於自己握工具**:能用 workflow 節點或 subagent 完成的事
   (寄信、爬蟲、桌面自動化…),就讓 agent **規劃成 workflow / 派 subagent**,
   **不要**塞成 agent 自己的工具。agent 的工具列要小而精。
5. **工具不投機新增**:每個工具都要「掙得」位置,想不到明確、常見的使用情境就不加。

---

## 3. 階段一:記憶系統(詳細設計)

### 3.1 子步驟順序(有依賴、照順序做)

1. 對話歷史落地 SQLite
2. 長期記憶表 + digest 注入
3. `save_memory` / `recall_memory` 工具
4. 滾動摘要

### 3.2 資料表設計(加進 `db.py` 的 `init_db()`)

沿用現有慣例:`conn.executescript` 建表、`CREATE TABLE IF NOT EXISTS`、WAL 模式。

```sql
-- 對話歷史落地(取代 in-memory _tg_chat_history)
CREATE TABLE IF NOT EXISTS tg_chat_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    role      TEXT    NOT NULL,          -- 'user' | 'assistant'
    content   TEXT    NOT NULL,
    ts        REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tg_hist_chat ON tg_chat_history(chat_id, ts);

-- 長期記憶(蒸餾過的事實)
CREATE TABLE IF NOT EXISTS agent_memory (
    id          TEXT    PRIMARY KEY,     -- uuid
    chat_id     INTEGER NOT NULL,        -- 單人用:統一寫 0 當 global(見決策點 1)
    type        TEXT    NOT NULL,        -- profile | preference | fact | decision | summary
    title       TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    importance  INTEGER NOT NULL DEFAULT 3,  -- 1-5、digest 截斷時依此排序
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_chat_type ON agent_memory(chat_id, type);
```

### 3.3 子步驟一:對話歷史落地 SQLite

- `db.py`:加 `tg_chat_history` 表 + CRUD(`append_tg_message`、`load_tg_history(chat_id, limit)`、`trim_tg_history`)
- `telegram_handler.py` `_handle_tg_freeform_chat`:
  - 開頭從 DB 載歷史(取代 `_tg_chat_history.setdefault`)
  - 每則 user / assistant 訊息寫回 DB
  - in-memory dict 可保留當 cache、但 **DB 是 source of truth**
- **驗收**:跟 TG bot 對話幾輪 → 重啟 backend → 再對話、AI 接得上前文

### 3.4 子步驟二:長期記憶表 + digest 注入

- `db.py`:加 `agent_memory` 表 + CRUD(`save_memory`、`list_memory`、`search_memory`、`delete_memory`)
- 新增 `_build_memory_digest(chat_id)`:把 `agent_memory` 依 importance 排序、組一份**很小**的 digest
  - **常駐 digest 上限 ≤1K token**(只放核心 profile + 最高 importance 的幾條)
  - 長尾記憶**不塞 digest**、交給 `recall_memory` 工具按需撈(見 3.7 prompt 體積策略)
  - 格式參考 Claude Code 的 `MEMORY.md`:分類條列、每條一行
- `telegram_handler.py`:把 digest 併進現有的 `extra_system`
  (目前已有 `_build_tg_state_digest()` 的注入機制、跟著它走即可)
- **驗收**:手動 INSERT 一筆記憶 → 下次對話、AI 引用得到

### 3.5 子步驟三:`save_memory` / `recall_memory` 工具

- `chat_tools.py`:加兩個 `@tool`
  - `save_memory(type, title, content, importance)` — 寫一筆長期記憶
  - `recall_memory(query)` — 查記憶(**先做 SQL `LIKE` 關鍵字查詢**、不上 embedding)
- 加進 `CHAT_TOOLS`,並列入 `main.py` 的 `_TG_ONLY_TOOLS`(只給 TG、桌面不放)
- `main.py` `_PIPELINE_SYSTEM_BASE`:在 `<!--TG_ONLY_BEGIN-->` 區塊內加「記憶工具守則」
  - 何時存(使用者明說「記住」、或學到偏好 / 決策)
  - 存什麼、不要存什麼 → **套 CLAUDE.md 敏感 deny list**(見 3.7)
  - 何時 recall(使用者問起過去、或任務需要脈絡)
- **驗收**:跟 AI 說「記住我偏好 X」→ 它 call `save_memory` → 下一輪對話引用得到

### 3.6 子步驟四:滾動摘要

- 對話歷史超過門檻(見決策點 3)時,背景把**最舊一段**丟給 LLM 摘要成一筆
  `type=summary` 的記憶,舊訊息才從 working set 移除
- 觸發點:`_handle_tg_freeform_chat` 結尾、歷史 trim 之前
- **驗收**:長對話(>門檻)後、舊脈絡仍能在 digest 裡被引用

### 3.7 prompt 體積策略(重要 — 避免提示詞膨脹)

現況實測:TG 系統提示詞已 **~11.8K token / 979 行**,加 19 個工具 schema(~6-8K),
單輪輸入現在就 ~20K token。記憶系統若死塞 2-3K digest,單輪會到 ~24-25K。
**技術上塞得下(Gemini 1M / Groq 128K),但 free tier quota 會燒更快、長 prompt 也稀釋注意力。**

對策:
- **常駐 digest 做很小(≤1K)** — 只放核心 profile + 最高優先幾條。長尾交給 `recall_memory`
  工具按需撈、用到才花 token。記憶量再大、常駐成本也不膨脹。
- **先用 SQL `LIKE` 做 `recall_memory`**,不上 embeddings / 向量檢索
  — 那要嵌入模型 + 向量庫、複雜度跳一級。等量大到 LIKE 不夠用再說。
- **連動 Task #99(漸進揭露)**:把 base prompt 裡不常用的段落(Outlook 模板細節、
  邊角規則)挪進 `read_help_doc` lazy doc,把 11.8K 的 base 砍下來、騰空間給記憶系統。

### 3.8 安全

- 記憶寫入前過濾 CLAUDE.md 敏感 deny list:`token` / `key` / `secret` / `credential`
  / `.env` / 密碼類內容一律不寫進 `agent_memory`
- `save_memory` 工具描述明寫「不要存密鑰 / 憑證類內容」
- 落地的 `tg_chat_history` 同理 — 若訊息含上述字樣、考慮遮蔽再存(或至少不進 digest)

---

## 4. 階段二:工具強化

### 4.1 原則(先讀「⚓ 貫穿原則」)

階段二是橫向擴張風險最高的一段。**刻意做小** — 每個工具都要過本業測試,
能委派的不要塞成工具(護欄 1、4)。agent 的力量是「腦」(記憶、規劃)
+「委派」,不是無限堆「手」(工具)。

### 4.2 `read_file` — 規劃支援工具(唯一明確要加的)

- **用途**:規劃 workflow 前,讀使用者引用的既有 script / 資料檔,
  看清楚它的 I/O、欄位、結構,才能規劃得準(例:使用者說「幫我把
  legacy `financial.py` 接後處理」→ agent 先讀 `financial.py` 看它輸出什麼)
- **為何過本業測試**:這是「把規劃做得更準」的支援,不是讓 agent 變萬能助理
- **範圍限制**:唯讀;限專案目錄 / 使用者明確引用的路徑;套敏感 deny list
  (`.env` / `*.key` / `*secret*` 等不給讀)
- 加進 `CHAT_TOOLS`、列入 `_TG_ONLY_TOOLS`

### 4.3 明確「不做」/ 改用委派的

- **不加 `write_file` / `edit_file`** — 要改檔交給 subagent(`coder` role),不自己握
- **不加寄信 / 爬蟲 / 修圖等工具** — 那些一律規劃成 workflow 節點
- 「自排提醒」歸到階段三(屬主動性、一起做)

> 階段二實際上只加 1 個工具。這是刻意的 — 符合「腦不是手」的原則。

---

## 5. 階段三:主動性 — 完整閉環

### 5.1 目標

補完閉環:**規劃 → 執行 → 監看 → 診斷 → 修正 → 重跑**。
現在斷在「監看」與「診斷修正」兩段。

### 5.2 缺口

agent 目前「被叫才動」— 只在使用者發訊息時醒來,**沒有事件觸發器、
沒有自主迴圈**。所以監看只能被動查、失敗無法自我修復。

### 5.3 三個組件

**組件 A — 事件觸發機制**
- runner 已有 `_notify_final`(完成 / 中止推 TG)、步驟失敗 inline keyboard
- 新增:run 失敗(retry 耗盡、進 `awaiting_human`)時,觸發「喚醒 agent」hook,
  帶 run context(run_id、失敗步驟、log 摘要)
- 整合點:runner 失敗暫停機制本來就會進 `awaiting_human` + 推 TG 決策鍵盤 —
  在那個點接上 agent 即可,不必另造機制

**組件 B — 自主診斷迴圈**
- agent 收到失敗事件 → 走特化路徑:`get_run_log` 讀錯 → `get_workflow_yaml`
  拿 YAML → 診斷 root cause
- 技術上可重用 `_chat_agent_loop`、餵一個合成 message 描述失敗情境

**組件 C — 核准式自我修復**
- agent 產出修正方案 → 在 TG 推「診斷 + 修正後 YAML + 一個『核准重跑』鈕」
- 使用者點核准 → 套用(`save_workflow_yaml`)+ 重跑(`start_workflow`)
- runner 已有 inline keyboard infra(`resume_pipeline` + `retry_with_hint`)—
  沿用,把「hint」從使用者打字改成 agent 產出
- **界線**:自動修最多 **1 次**;還失敗 → 停、升級給使用者。
  **不無限自我重試**(會繞圈、燒爆 free tier quota)

### 5.4 agent loop 上限

- `_CHAT_MAX_TOOL_ITERATIONS` 現在 5(`main.py:3345`);診斷流程(讀 log +
  讀 YAML + 改 + 重跑)就 4 步、餘裕不足
- 調高到 **8-10**,或對「自我修復路徑」單獨給較高上限

### 5.5 主動進度回報(選做)

- 目前 runner 的 `_notify_final` 是寫死的原始摘要
- 進階:讓 agent 用「規劃者口吻」摘要 run 結果回報(它規劃的、它最懂)

### 5.6 安全界線

- 自動修上限 1 次、之後升級給人
- 套 YAML + 重跑**保留核准步驟**(對齊 CLAUDE.md「危險動作先確認」)
- 本業測試:這整段是編排核心 → 縱深、不是橫向擴張、安全

### 5.7 影響檔案

`runner.py`(失敗 hook)、`telegram_handler.py`(喚醒事件處理 + 核准鍵盤)、
`main.py`(loop 上限)。

---

## 6. 待裁示的決策點

| # | 階段 | 問題 | 我的傾向 |
|---|---|---|---|
| 1 | 一 | 記憶範圍:單一 chat_id 私有 vs 跨 chat_id 共用 | 你單人用 → **全域共用一份**(`chat_id=0` 當 global) |
| 2 | 一 | 常駐 digest 大小上限 | **≤1K token**(長尾交給 `recall_memory` 按需撈、見 3.7) |
| 3 | 一 | 滾動摘要觸發門檻 | 歷史 **> 40 則**時摘要最舊 20 則 |
| 4 | 二 | `read_file` 之外還要不要加別的工具 | **先只加 `read_file`**、其餘照本業測試逐個評估 |
| 5 | 三 | 自我修復重試上限 | **1 次**、之後升級給人 |
| 6 | 三 | 哪些 run 狀態觸發 agent | retry 耗盡的失敗(`awaiting_human`);完成可選 |
| 7 | 三 | 修復走核准式 vs 有界自動 | **核准式**(TG 推方案 + 核准鈕) |
| 8 | 三 | `_CHAT_MAX_TOOL_ITERATIONS` 調到多少 | **8-10** |
| 9 | 全 | 階段執行順序 | **一 → 三 → 二**(三對你價值最高、二最該保守) |

---

## 7. 影響檔案清單

| 檔案 | 階段 | 改動 |
|---|---|---|
| `backend/db.py` | 一 | 加 `tg_chat_history` + `agent_memory` 兩表 + CRUD |
| `backend/telegram_handler.py` | 一、三 | 一:歷史落地、digest 注入、滾動摘要;三:失敗喚醒事件處理 + 核准鍵盤 |
| `backend/chat_tools.py` | 一、二 | 一:`save_memory`/`recall_memory`;二:`read_file` |
| `backend/main.py` | 一、三 | 一:`_TG_ONLY_TOOLS` + 記憶守則;三:`_CHAT_MAX_TOOL_ITERATIONS` 調高 |
| `backend/pipeline/runner.py` | 三 | run 失敗時觸發「喚醒 agent」hook |

---

## 8. 相關事項

- 既有待辦 **Task #99(prompt 改漸進揭露)** 會因本計劃更相關 —
  記憶 digest 注入會讓 system prompt 變大,階段一做完後 prompt 體積要重新評估。
- 階段一、二是純 backend;階段三會碰 runner 但仍不碰前端。
- **建議執行序:階段一 → 階段三 → 階段二**(見決策點 9)。
  階段三補的「執行→監看→修正」閉環對你價值最高;階段二最該保守、放最後。
