# UIA(Microsoft UI Automation)節點評估

> **Status**:評估中、待使用者用 Accessibility Insights 測公司系統後決定是否啟動
> **Trigger 場景**:公司系統 DB 表格找最後資料下方空白格輸入公式、座標每次不同、無 API 可繞 GUI
> **2026-05-09 起草**

---

## 1. 是什麼

**UIA = Microsoft UI Automation API**

Windows 原生的「**讓程式直接讀寫 GUI 結構**」API、不靠像素、不靠模板比對、直接拿到「視窗有哪些控制項、控制項目前狀態、表格幾列、按鈕能不能按」。

原本給輔助科技(螢幕閱讀器 / 語音控制)用、後來變桌面自動化的金標準。

每個 Windows 應用會暴露一棵「UI 元素樹」、UIA 讓你 query / interact:

```
Window: "公司系統 - 訂單管理"
├── ToolBar
│   ├── Button: "新增" (enabled, x=50, y=80)
│   └── Button: "刪除" (disabled)
├── DataGrid: "訂單表"
│   ├── Header
│   │   ├── ColumnHeader: "訂單編號"
│   │   └── ColumnHeader: "金額"
│   ├── DataItem (row 1)
│   │   ├── DataItem.Cell[0]: "ORD-001"
│   │   └── DataItem.Cell[1]: "1500"
│   ├── DataItem (row 2)
│   ...
└── StatusBar: "已連線 / 共 234 筆"
```

直接拿「DataGrid 共幾列」「按鈕 'Save' 現在 enabled?」、不必截圖、不必 OCR、不必 VLM。

---

## 2. 為什麼值得加(對比 computer_use)

| 能力 | computer_use 怎麼做 | UIA 怎麼做 |
|---|---|---|
| 找按鈕點擊 | 錄製座標 + CV / OCR / VLM | `WindowControl.ButtonControl(Name="儲存").Click()` |
| 讀整張表單欄位值 | 一格格 OCR | 直接 `Form.GetTexts()` 拿全部 |
| 等按鈕變 enabled 才點 | 截圖比顏色 / 不停試 | event-driven `WaitForEnabled()` |
| 拿 Dropdown 所有選項 | 點開 → OCR → 點關 | `ComboBox.Items` 直接列 |
| 讀 status bar / log 文字 | OCR | `StatusBar.Texts[0]` |
| 偵測 modal popup 跳出 | 額外 vlm_check | `Window.WaitForWindow(Name="警告")` |
| 走檔案樹不必捲動 | 截圖捲動找 | `TreeView.GetItems()` 全部一次拿 |
| 偵測程式 hang | 看不出來 | `Window.IsResponding == False` |
| 點背景視窗 | 必須 activate_window 先 | 直接點、不必前景 |

**核心差異**:
- computer_use = 「看像素、模擬人」、UIA = 「讀程式結構、繞過視覺」
- 互補不互斥:有 UIA 樹的視窗用 UIA(穩、快、便宜)、沒樹的(canvas / 自繪 / 遊戲)用 computer_use

---

## 3. 能 / 不能用 UIA 的 app 對照

| 應用類型 | UIA 表現 | 備註 |
|---|---|---|
| WPF / WinForms / .NET app | ✅ 完美 | 幾乎所有企業內部系統 |
| WinUI 3 / UWP | ✅ 完美 | 新版 Windows 11 app |
| Office (Word / Excel / Outlook) | ✅ 完美 | UIA tree 非常豐富 |
| Chrome / Edge | ✅ 中等 | 透過 IAccessible2、結構有但不深 |
| Java Swing app | ⚠️ 需安裝 Java Access Bridge | |
| Electron(Slack / Discord / VS Code) | ⚠️ 部分支援 | 看 app 有沒啟用 accessibility |
| 遊戲 / DirectX / OpenGL canvas | ❌ 看不到 | |
| Photoshop / Blender / 自繪 UI | ❌ 看不到 | canvas 內無 tree |
| Citrix / RDP 遠端桌面 | ❌ 看不到 | pixel-based 串流、本機 UIA 看不到對方 tree |
| iOS / macOS / Linux | ❌ Windows-only | |

**典型結果**:企業內部系統(主要是 .NET / Web)幾乎都吃 UIA、但要實測。

---

## 4. 5 分鐘判定能否使用

### 工具:Accessibility Insights for Windows
- 下載:https://accessibilityinsights.io/downloads
- 免費、Microsoft 出
- 獨立桌面 app(MSI / exe)、不是 Python 套件
- 純測試用、判定完可解除安裝

### 替代品(擇一)
- **Inspect.exe**(Windows SDK 內建、更原始)
- **UIA Verify**(較舊但某些情況更準)
- **FlaUInspect**(open source)

### 判定流程
1. 開啟 Accessibility Insights
2. 切到 "Live Inspect" 模式
3. 滑鼠移到目標 app(例公司系統表格)
4. 看右側 panel 顯示什麼結構

| Insights 顯示 | 結論 | 該選 |
|---|---|---|
| 看到 `DataGrid` / `DataItem` / 個別 cell 結構 | ✅ UIA 可用 | UIA 節點 |
| 只看到 `Pane` / `Custom` / 一個 box 沒細節 | ❌ UIA 沒用 | vlm_find_coord(Phase 2) |
| 完全看不到(空白 / 抓不到) | ❌ canvas-based | vlm_find_coord(Phase 2) |

---

## 5. V5 整合設計(走「方案 C」)

**新節點 `uia_action`**(獨立)、+ sidebar 視覺分組:

```
🖥 桌面自動化
  ├ 桌面操作 (computer_use)    — 錄製座標、CV/OCR/VLM 找位置
  └ UIA 控制 (uia_action)      — 讀 GUI 樹、不靠座標

📄 資料處理
  ├ Skill (skill_mode)
  ├ Script (shell)
  └ ...
```

**為什麼不用「web_crawler 那種 mode 分頁」**:UIA 跟 pixel-based 共用設定 < 20%(只有 timeout / retry)、panel UI 完全不同(tree viewer vs 動作時間軸)、塞同節點會像兩個無關功能擠盒子。

**為什麼不直接擴 computer_use**:已經 8+ 區段、再加會太擠。獨立節點 panel 可以為 UIA 量身打造。

---

## 6. UIA action types(草案)

最小可用集合:

| type | 用途 |
|---|---|
| `uia_click` | 找控制項點擊(by Name / AutomationId / ControlType) |
| `uia_send_keys` | 對控制項打字(等同 focus + type) |
| `uia_get_text` | 讀控制項文字、存進變數給後續 step 用 |
| `uia_get_table_rowcount` | 讀 DataGrid / ListView 列數、存變數 |
| `uia_click_cell` | 點 DataGrid 第 N 列第 M 欄 cell |
| `uia_get_dropdown_items` | 拿 ComboBox / ListBox 所有選項 |
| `uia_wait_enabled` | 等控制項變 enabled / 出現才繼續 |
| `uia_wait_window` | 等指定 title 視窗出現(modal popup 偵測) |
| `uia_assert_state` | 驗某控制項狀態(enabled / checked / focused) |

YAML 範例(解使用者那個寫公式場景):

```yaml
- name: write_formula
  uia_action: true
  uia_window: "公司系統*訂單*"        # 視窗 title 模糊比對
  steps:
    - action: uia_get_table_rowcount
      control: { type: DataGrid, name: "訂單表" }
      save_as: row_count
    - action: uia_click_cell
      control: { type: DataGrid, name: "訂單表" }
      row: "{{row_count + 1}}"          # 最後資料下一列
      column: 4
    - action: uia_send_keys
      text: "=SUM(D2:D{{row_count + 1}})"
    - action: uia_send_keys
      keys: ["enter"]
```

---

## 7. 實作技術選型

### Python 套件(擇一)

| 套件 | 特色 | 推薦度 |
|---|---|---|
| `uiautomation` | API 簡潔、效能好、Windows-native binding | ⭐⭐⭐ |
| `pywinauto` | 老牌、文件多、可同時用 UIA + 舊 Win32 API | ⭐⭐ |
| `comtypes` + 直接 IUIAutomation COM | 最底層、最有彈性、但 API 複雜 | ⭐(底層備案) |

**初版選 `uiautomation`**:API 直觀、社群大、跟 V5 既有 win32com / pywin32 相容。

### Frontend Element Tree Picker

panel 內顯示當下視窗的 UIA tree、使用者點選想要的 control、自動填 ControlType + Name + AutomationId。

實作:
- backend `/uia/inspect-window` endpoint:給 window title pattern、回 element tree JSON
- frontend tree component:遞迴渲染、點 node 回填到 step 設定

不必自己寫 tree viewer、可用 `react-arborist` 或 `rc-tree`(現成 component)。

---

## 8. 工程估算

| 項目 | 工作量 |
|---|---|
| Backend:加 `uiautomation` 套件 + 基礎 inspect endpoint | 0.5 天 |
| Backend:5-9 個 uia_action types(見上方表) | 2-3 天 |
| Backend:`pipeline/uia_executor.py`(action dispatcher) | 1 天 |
| Backend:variable 系統(`{{row_count}}` 替換、跨 step 傳值) | 1 天(如果還沒做) |
| Frontend:新節點 panel(tree viewer + control picker) | 2-3 天 |
| Frontend:sidebar 分組整理(順便做) | 0.3 天 |
| 測試(WPF / WinForms / Office / Chrome 樣本) | 1-2 天 |
| 文件:加 docs/uia-actions-reference.md | 0.5 天 |
| **合計** | **8-11 天** |

(原估 5-7 天太樂觀、加上 variable 系統 + frontend tree picker、實際 8-11 天較合理)

---

## 9. 跟其他功能的關係

| 功能 | UIA 之後是否還需要? |
|---|---|
| `computer_use` 錄製座標 | ✅ 仍要(canvas / 自繪 UI / 遠端桌面) |
| Phase 1 expected 把關 | ✅ 仍要(UIA 也會偶爾失準、加 verify 多保險) |
| Phase 2 vlm_find_coord | ✅ 仍要(UIA 不可用的場景的 fallback) |
| `outlook_automation` | ⚠️ 可考慮整合(outlook 也是用 win32com / UIA、概念相通) |

UIA 不取代任何現有功能、是補一個 dimension(「程式式 GUI 操作」)。

---

## 10. 還沒決定的問題

- [ ] **UIA 找不到時要不要 fallback 到 vlm_find_coord?**
  目前傾向:給節點層級 toggle `uia_fallback_to_vlm`、預設 false(不靜默退、保留可預期性)
- [ ] **要不要支援同節點內混 UIA + computer_use action?**
  目前傾向:不混、各跑各、需要混就用 2 個節點接力(UIA 拿值存變數、computer_use 用變數)
- [ ] **跨 step variable 系統**(例 `{{row_count}}`)
  目前 V5 沒有完整 variable 機制、只有 `prev_outputs`(檔案路徑)。UIA 需要傳數值、要設計新東西。簡單方案:`save_as:` 寫進 step output、後續 step 用 `{{step_name.var}}` 取。
- [ ] **Element 識別策略**(用 Name vs AutomationId vs ControlType)
  Name 易讀但會隨語言 / 版本變、AutomationId 穩但開發者沒設就沒。建議:Name 為主、AutomationId 為輔、ControlType 為篩選。

---

## 11. 啟動條件

開做前需:

1. ✅ 使用者用 Accessibility Insights 測公司系統、確認看得到結構
2. ⏸ Phase 1 跑穩 ≥1 週、無 regression
3. ⏸ 列出 ≥3 個具體待解 UIA 場景(避免做完只解一個 case)

達成後啟動、約 8-11 工作天。

---

## 變更紀錄

- 2026-05-09:初稿、使用者公司 DB 表格場景觸發評估
