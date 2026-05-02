# Pipeline Orchestrator — 五版本演進對照表

> 撰寫日期：2026-04-28
> 來源：以實際 `git log`、`frontend/app/pipeline/`、`backend/pipeline/` 檔案差異整理
>
> ⚠️ V2/V3/V4/V5 的 `CLAUDE.md` 內容**完全相同**（都還停在「V2 新增 computer_use」），不能拿那份當權威。

## 一、五版同源

使用者在 `C:\Users\GU605_PR_MZ\pipeline-orchestrator{V1..V5}\` 維護**五個平行 git repo**。每個都是完整工作版本，版號累加 — Vn ≈ Vn-1 + 一個新節點類型 / 大功能。

**V5 是當前主力開發版本。**

## 二、節點類型演進

| 版本 | 節點數 | 該版獨有新增 | 後端關鍵新檔 |
|---|---|---|---|
| **V1** | 4 | base：`script` / `skill` / `ai_validation` / `human_confirm` + Telegram + recipe + AI assistant + OpenRouter + ask_user | runner / executor / validator / recipe / store |
| **V2** | 5 | + 🖱 **`computer_use` 桌面自動化**（pyautogui + OpenCV template matching + pynput 錄製、80×80 px 錨點圖） | + `computer_use.py` / `recorder.py` / `ocr.py` / `file_preview.py` |
| **V3** | 5 | + 📦 **Skill sandbox**（LLM 生成的程式碼丟 WSL Docker 容器 `pipeline-sandbox` 跑、隔離 Windows host） | + `sandbox.py` |
| **V4** | 6 | + 👁 **`visual_validation` 視覺驗證節點**（VLM 判斷螢幕內容、screen region picker、VLM anchor picker） | + `visual_validator.py` |
| **V5** | 7 | + 📧 **`outlook_automation` 節點**（pywin32 + Outlook COM；模板系統 + AST allowlist + win32_helpers wrapper） | + `outlook_templates.py` / `win32_agent_config.py` / `win32_helpers/` |

### V5 Outlook 模板（共 12 個，定義在 `frontend/app/pipeline/_outlookPanel.tsx`）

兩種執行模式：
- **direct**：後端直接 call wrapper，不進 LLM、零 token、結果可預測
- **llm**：進 LLM agent loop，需要摘要 / 分析時用

四個 category：📥 inbox（收信整理）/ 📤 send（寄信）/ 📎 attach（附件）/ 🗂 manage（信件管理）

## 三、Backport 觸發判斷

> 修共用模組 bug 時，**commit V5 後一定要先問使用者**：「要不要也推到 V4 / V3？」

| 改的檔案 | 影響範圍 | 修完要問哪些版本 |
|---|---|---|
| `runner.py` / `executor.py` / `validator.py` / `telegram_handler.py` / `recipe.py` / `store.py` / `models.py` 等 | 全 5 版都有 | **V1~V4 全問** |
| `frontend/app/pipeline/page.tsx` / `_store.ts` / `_helpers.ts` / `_sidebar.tsx` 等基礎前端 | 全 5 版都有 | V1~V4 全問 |
| `_scriptNode/Panel.tsx` / `_skillNode/Panel.tsx` / `_humanConfirmNode/Panel.tsx` / `_aiValidationNode/Panel.tsx` | 全 5 版都有 | V1~V4 全問 |
| `computer_use.py` / `recorder.py` / `ocr.py` / `file_preview.py` / `_computerUse*.tsx` / `_anchorEditorModal.tsx` | V2~V5 才有 | V2 / V3 / V4 |
| `sandbox.py` | V3~V5 才有 | V3 / V4 |
| `visual_validator.py` / `_visualValidation*.tsx` / `_screenRegionPicker.tsx` / `_vlmAnchorPicker.tsx` | V4~V5 才有 | V4 |
| `outlook_*.py` / `win32_*.py` / `win32_helpers/` / `_outlook*.tsx` | **V5 only** | **不問**、純 V5 |

### Backport 既有實證

V2 ~ V5 都有相同的 commit 訊息：
- `fix(telegram): polling loop 加 lock re-check 避免兩個 backend 撞 409 Conflict`
- `fix(validator): 不再因 Skill agent 試錯歷史誤判 failed → 工作流變慢主因`
- `fix(runner): human_confirm step stale-write race → 同步驟啟動兩次`

V2 commit log 還明確寫 `backport from V4`、`backport from V3` — 證明這是常規流程。

## 四、共用模組詳細清單

### 全 5 版都有（V1 / V2 / V3 / V4 / V5）

**Backend**：
```
main.py, config.py, db.py, settings.py, llm_factory.py
telegram_handler.py
skill_pkg_manager.py, skill_scanner.py
pipeline/{runner.py, executor.py, validator.py, recipe.py, store.py, models.py, logger.py}
scheduler/manager.py
```

**Frontend**：
```
app/pipeline/{page.tsx, _store.ts, _helpers.ts, _sidebar.tsx, _runStatus.ts}
app/pipeline/{_scriptNode.tsx, _scriptPanel.tsx, _skillNode.tsx, _skillPanel.tsx,
              _humanConfirmNode.tsx, _humanConfirmPanel.tsx,
              _aiValidationNode.tsx, _aiValidationPanel.tsx}
app/{settings/page.tsx, recipes/page.tsx}
lib/{api.ts, types.ts}
```

### V2~V5 才有

**Backend**：`computer_use.py`、`recorder.py`、`ocr.py`、`file_preview.py`
**Frontend**：`_computerUseNode.tsx`、`_computerUsePanel.tsx`、`_anchorEditorModal.tsx`、`_insertableEdge.tsx`

### V3~V5 才有

**Backend**：`sandbox.py`

### V4~V5 才有

**Backend**：`visual_validator.py`
**Frontend**：`_visualValidationNode.tsx`、`_visualValidationPanel.tsx`、`_screenRegionPicker.tsx`、`_vlmAnchorPicker.tsx`

### V5 only

**Backend**：`outlook_templates.py`、`win32_agent_config.py`、`win32_helpers/{__init__.py, _common.py, outlook.py}`
**Frontend**：`_outlookNode.tsx`、`_outlookPanel.tsx`

## 五、Backport 注意事項

1. 不要無腦 copy — 舊版本可能少了某個依賴函式 / 型別欄位
2. 對 patch 局部適配（例如新版加了 `cv_strict_region` 欄位、舊版只認舊欄位）
3. commit 訊息建議帶 `— backport from V<X> <hash>` 慣例（V2 的 commit log 有現成範例）
4. backport 順序：通常 V5 → V4 → V3 → V2 → V1，逐版回溯避免漏依賴

## 六、文件維護

- 各版本 `CLAUDE.md` 已過時（V2/V3/V4/V5 內容相同、都停在 V2）
- 此檔（`docs/version-evolution.md`）以 V5 為基準維護，是當前最新的版本對照
- 之後如果加新節點，記得回來更新本檔的「節點類型演進」「共用模組詳細清單」兩節
