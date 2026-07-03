# Atlas 產品戰略 — 要加什麼、怎麼超越同類開源

> 定位一句話:**對手在做「流程自動化工具」或「LLM app builder」;Atlas 做的是「AI Agent 的作業系統」**
> —— 唯一同時具備:LLM 生碼沙盒執行、Recipe 成本歸零、桌面+網頁全域自動化、地端合規、白箱可控。

---

## 一、競品盤點:你在跟誰打

| 對手 | 強項 | 弱點(= 你的機會) |
|---|---|---|
| **n8n**(~50k★) | 500+ 整合、成熟、社群大 | 節點是「預寫好的 API 接口」,**不會自己寫程式**;AI 只是外掛;無桌面 RPA |
| **Dify / Flowise / LangFlow** | LLM app builder、RAG 強 | 編排的是「LLM 呼叫鏈」不是「真實工作」;**沒有沙盒生碼執行、沒有 RPA、沒有成本歸零機制** |
| **Zapier / Make** | 無腦易用 | 雲端、貴、封閉、資料出境 |
| **OpenClaw / Hermes 類自主 Agent** | 自主性、話題性 | **黑箱不可控**(你 PPT 已打這點,對) |
| **UiPath / Power Automate** | 企業 RPA 王者 | 貴、重、AI 生成能力弱、授權綁死 |

### Atlas 真正獨有的三張牌(對手都沒有「同時」具備)
1. **LLM 現場寫碼 + 沙盒執行 + Recipe 固化** → 「第一次用 AI、之後零成本確定性重放」= n8n/Dify 都沒有的**成本結構優勢**。
2. **Web 工作流 + 桌面 RPA 同一條 pipeline**(爬蟲→分析→點桌面 GUI→寄信)。
3. **全地端可跑通**(Gemma 驗證過)= 合規市場入場券。

---

## 二、要加的功能(按「贏面」排序)

### 🥇 P1:Recipe / 工作流市集(Community Hub)
n8n 贏在 2000+ 社群 templates。Atlas 的 Recipe 比 template 更強 —— **它是「跑通、驗證過的可執行資產」**。
- `atlas export/import`:一鍵打包工作流 + recipe(去敏)成 `.atlas` 檔。
- GitHub repo 當市集起步(仿 n8n-templates),README 掛 badge。
- **這是唯一能滾社群飛輪的功能;沒有它,其他都是單機自嗨。**

### 🥈 P1:MCP(Model Context Protocol)雙向支援
2026 行業標準。你 PPT 的 M4–M6 有排,建議**提前**:
- **當 MCP client**:一個「MCP 節點」= 立刻繼承上千個現成 MCP server(GitHub/Slack/Notion/DB…),**用一個功能追平 n8n 的 500 整合**。
- **當 MCP server**:把 Atlas 工作流曝露成 MCP tool → Claude Desktop / 任何 agent 都能呼叫。**Atlas 變成「其他 AI 的手腳」**,生態位升級。

### 🥉 P1:觸發器層(Trigger)補全
現在只有 cron + 手動。真自動化要事件驅動:
- **Webhook 觸發**(收到 HTTP → 跑工作流)—— 半天工作量、價值巨大。
- **檔案夾監看**(新檔案落地 → 跑)—— 對行政/營運受眾超實用。
- Email / TG 訊息觸發。
- 沒有 trigger 層,「每日競品情報」永遠要人按或等排程;有了它才是真 OS。

### 4. Run Diff(執行差異視圖)
已有完整 runs 快照 → 加「這次 vs 上次輸出差在哪」(檔案 diff / 數值變化)。
競品監控類工作流價值翻倍(「今天比昨天多了哪台新機」直接可視化)。**對手全都沒有。**

### 5. 工作流單元測試 / 金絲雀模式(`atlas test`)
用固定 fixture 輸入跑一遍、驗 expect。Recipe 已是確定性 → 補這個 = **「工作流 CI」**,工程師受眾會愛。

### 6. 秘密管理(Secrets Vault)
API key 現在在 .env / 環境變數。加加密 secrets store(DPAPI / keyring),節點用 `{{ secrets.X }}` 引用。
= 企業導入的硬門檻,早做早贏。

### 7. 弱模型「結構化輸出合約」(output JSON schema)
親歷的痛:弱模型解析步產 frontmatter 垃圾、統計 0/0/0。系統性解法:skill 節點加 **output JSON Schema**,
驗證不過自動退回重做(expect 機制的結構化升級)。**直接強化「弱模型也能穩」的核心賣點。**

---

## 三、怎麼「超越」—— 打法

- **不要**跟 n8n 拚整合數量(MCP 一招借力);**不要**跟 Dify 拚 RAG(記憶四層夠用)。
- **要**把「Recipe 成本歸零」做成公開 benchmark:同一任務 n8n(AI 節點)每次 $X vs Atlas 第 2 次起 $0 —— 這種對比圖最會傳播。
- **要**吃「合規 / 地端」缺口:UiPath 太貴、n8n AI 要雲 key、Dify 地端難 —— Atlas 是唯一「全地端 + RPA + LLM 編排」三合一。

---

## 四、90 天落地順序建議
1. **Webhook 觸發 + 檔案監看**(快、立刻有感)。
2. **MCP client 節點**(一個節點吃掉整個生態)。
3. **Export/Import + GitHub 市集**(社群飛輪點火)。
4. **output schema 合約**(把「弱模型穩定」焊死)。
5. **MCP server 模式 + Run Diff**(差異化拉滿)。
6. **Secrets Vault**(企業入場)。

---

## 五、給行銷/簡報的一句話金句庫
- 「不是更聰明的模型,而是更好的執行框架。」
- 「第一次用 AI,之後零成本、確定性重放。」
- 「看得見、可編輯、可加把關 —— Agent 第一次被你掌控。」
- 「便宜步驟走弱模型、關鍵步驟才路由強模型,成本花在刀口。」
- 「唯一同時有:生碼沙盒、成本歸零、桌面+網頁全域、地端合規、白箱可控。」
- 「Atlas = 給 AI Agent 的作業系統。」

---
*（由 AI 於 V5→Atlas 正式化任務期間整理存檔;新功能將在獨立 Atlas 資料夾依此路線圖開發。）*
