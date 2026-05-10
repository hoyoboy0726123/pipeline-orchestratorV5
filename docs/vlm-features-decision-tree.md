# Computer Use 4 種 VLM 功能 — 決策樹 / 用法對照

> 適用於 V5 起的 `computer_use` 節點。Phase 1 加入 `expected` 把關後、共 4 個 VLM
> 相關設定、使用者容易混淆。這份文件分清楚「**什麼時候該用哪個**」。

---

## TL;DR — 決策樹

```
我要 VLM 來做什麼?

├─ 動作執行「之後」、想自動驗證有沒生效
│   → expected 欄位(節點 cu_vlm_check_strategy 開、每動作填 expected)
│   例:點 Save As 後驗「對話框已開」、漏點 / 點空了立刻停
│
├─ 動作執行「之前」、想找「點哪裡」
│   ├─ 目標**文字會變**(動態金額 / 排名 / 隨機帳號)
│   │   → vlm_mode = description(VLM 看圖回目標文字 → OCR 找該文字 → 點)
│   │   例:「點當前最便宜的方案」、金額每次不同
│   │
│   └─ 目標 UI **有多種視覺變體**(深淺主題 / 中英版 / hover 變色)
│       → vlm_mode = anchor_pick(VLM 從多張候選錨點挑當下最像的 → CV 用該張)
│       例:Slack 發送鍵在淺/深色主題不同、Windows 關閉 X 滑鼠變色
│
└─ 動作序列「中間」想插入 explicit 驗證 / 條件判斷
    → vlm_check action(獨立一個動作步、不點擊純判斷、pass=false 該步直接 fail)
    例:登入後加一步驗「成功訊息有出現」、表單送出後驗「沒有紅字錯誤」
```

---

## 四個功能詳細對照

| 功能 | 觸發時機 | 圖片數 | 任務 | 失敗行為 | 典型例 |
|---|---|---|---|---|---|
| **`expected`** + `cu_vlm_check_strategy` | 任意動作**執行後** | 2(前後) | 動作後狀態符合 expected? + 4 種 mismatch 分類 | 3 選 1:stop_notify / retry_once / skip_and_continue | 點 Save As 後驗對話框開了 |
| **`vlm_mode='description'`** | click_image **執行前** | 1 + 描述 | 目標的實際文字是什麼? | OCR 找文字 → 點該位置 | 點當前最低價的按鈕 |
| **`vlm_mode='anchor_pick'`** | click_image **執行前** | 1 + N 候選圖 | 哪張錨點當下最像? | 取 index → CV 用該張比對 | 點關閉 X(hover 變色變體) |
| **`vlm_check`** action | 序列任意位置 explicit step | 1(當下) | 條件 X 是否成立? | pass/fail、fail 該步 fail | 登入後驗成功訊息 |

---

## 場景對照(實戰用法)

### 場景 1:點完按鈕、想確認 UI 真的有反應
**用 `expected`**(Phase 1 新加)
```yaml
- type: click_image
  image: save_as_btn.png
  expected: "另存新檔對話框已開啟、含 File name 輸入框"
  # 節點層級設 cu_vlm_check_strategy: after_each
```
👉 動作後自動截圖比對、漏點立刻停、不需自己加 verify step。

### 場景 2:目標按鈕的金額 / 排名每次不同
**用 `vlm_mode='description'`**
```yaml
- type: click_image
  vlm_mode: description
  vlm_prompt: "找最便宜方案的『立即購買』按鈕"
  use_ocr: true  # description 模式必須開 OCR
```
👉 VLM 看當下螢幕、回出『立即購買 ($299)』、OCR 找該字串 → 點。

### 場景 3:同個按鈕有 hover / theme 多狀態
**用 `vlm_mode='anchor_pick'`**(剛加的「立即截圖」UX 來補變體最快)
```yaml
- type: click_image
  vlm_mode: anchor_pick
  vlm_anchors: ["close_x_default.png", "close_x_hover.png", "close_x_pressed.png"]
  vlm_prompt: "點視窗右上的關閉按鈕(可能是 hover 變色狀態)"
```
👉 VLM 從 3 張候選挑當下最像的 → CV 用挑出的那張比對 → 點。

### 場景 4:登入後想驗成功訊息有出現
**用 `vlm_check` action**
```yaml
- type: click_image
  image: login_btn.png
- type: vlm_check  # 獨立檢查步、不點擊
  vlm_prompt: "畫面是否出現綠色『登入成功』訊息?"
  search_region: [0, 0, 800, 200]  # 只看頁面上方省 token
- type: click_image
  image: dashboard_link.png
```
👉 中間插一步 vlm_check 當條件閘門、不過就 fail 整個 step。

### 場景 5:多個動作組合、又想保 fail-safe
**`expected` + 偶爾 `vlm_check`** 組合用
```yaml
- type: click_image
  image: file_menu.png
  expected: "檔案選單已開、含 Save As 選項"
  verify_critical: true  # 標 critical、節點設 critical_only 也會驗
- type: click_image
  image: save_as.png
  expected: "另存新檔對話框已開啟"
- type: type_text
  text: "output.docx"
  # type_text 不必驗、信任 OS keyboard event
- type: vlm_check    # 送出前最後把關
  vlm_prompt: "檔名輸入框顯示 output.docx 嗎?"
- type: click_image
  image: save_btn.png
```

---

## 何時不用 VLM(注意成本)

每次 VLM 呼叫 ~$0.005-0.015、跑 20 步 expected 流程約 $0.1-0.3。**以下情境別開**:

- 純錄製重播(沒 UI 變動、沒 hover 漂移、沒視窗位置變)→ 純 CV 已夠
- 簡單 wait / type_text 動作(沒 click 風險)→ 開了浪費 token
- 開發測試階段(快速跑驗整體流程)→ 等流程穩了再開 VLM 把關

開了 VLM 就把它當作「**保險**」、不是「**主執行邏輯**」。錄製座標仍是主路徑。

---

## 變更紀錄

- 2026-05-09:初稿、Phase 1 落地後寫
