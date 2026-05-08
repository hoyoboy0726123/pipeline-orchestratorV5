# Computer Use VLM 把關 — 開發計劃

> **Status**: Phase 1 進行中(2026-05-09 啟動)
> **Owner**: Michael + Atlas(技術總監)
> **Decision**: Phase 1 立刻做、Phase 2 延後 6-12 月觀察 OmniParser / Anthropic computer_use 模型成熟度

---

## 0. 動機 / Why

V5 桌面自動化(`computer_use`)節點目前**純人工錄製座標 + pyautogui 重播**。

- 優點:確定性高、座標 100% 對(因為是錄的)
- 致命缺點:**任何環境變化都會直接崩**
  - UI 改版(Slack 升級、Excel 介面換) → 點空 / 點錯 → 後續全錯
  - 視窗位置 / 大小變了 → 全偏
  - 新對話框跳出(權限 / 廣告 / 通知) → 蓋住目標元素
  - DPI / 解析度變了 → 座標參考系變

**現況沒有任何「動作是否生效」的偵測**、出錯就是悶著頭錯到底、最壞情況跑完整段才發現產出全錯。

### 為什麼不直接讓 LLM 自主找座標?

業界 2026 Q2 主流模型(Anthropic Sonnet 4.6 / OpenAI Operator)在常見 UI 約 75-90% 精度、但:
- 自繪 UI(遊戲、Electron 客製化)精度掉很快
- 多 monitor / DPI 縮放仍是難題
- Cascade failure — 一步錯後面全錯
- 輸出的座標不是 ground truth、是「猜」

**Phase 1 的核心理念**:錄製座標保持確定性主路徑、AI 只當「驗證者」、不當「決策者」。99% 失敗模式從「整套悶著錯」變「立刻發現+人介入」。

---

## 1. Phase 1 範圍

### 1.1 必做

- [ ] `cu_actions[i].expected` — 每動作的預期 outcome 描述(自然語言、用戶錄製時填)
- [ ] `cu_actions[i].verify_with_vlm` — 每動作個別開關
- [ ] 動作執行前後各截一張圖、送 VLM 驗證 ok / fail / unclear
- [ ] `cu_on_mismatch` — 偏離時行為:`stop_notify` / `retry_once` / `skip_and_continue`
- [ ] `cu_vlm_provider` — `anthropic` / `openai`(共用 settings.model 那邊已有的 key)
- [ ] `cu_vlm_check_strategy` — `after_each` / `critical_only` / `off`
- [ ] 失敗時 push TG(含截圖)+ pipeline 進 awaiting_human

### 1.2 偵測 4 種 mismatch

| 類型 | 例 | 處理 |
|---|---|---|
| 預期元素不在 | 點完按鈕但「另存新檔」對話框沒開 | stop_notify |
| 元素在但動作沒生效 | hover 變藍但 click 沒下去 | retry_once |
| 出現 unexpected popup | 權限請求 / Windows 更新通知 | stop_notify(含 popup 截圖) |
| UI 完全變了 | 視窗被關 / 切到其他 app | stop_notify |

### 1.3 不做(留 Phase 2)

- LLM 自主生新座標
- OmniParser / Set-of-Mark 整合
- 自動修復(自動點關掉 popup 後續跑)

---

## 2. 技術設計

### 2.1 資料模型(`pipeline/models.py`)

新增 `PipelineStep` 欄位:
```python
# 每個 cu_action 加 expected + verify
cu_actions: list[dict] = []
# 之前只有 [{type:"click", x:120, y:340}, ...]
# 現在每筆額外可含:
#   expected: str          — 動作後的預期狀態(自然語言)
#   verify_with_vlm: bool  — 預設 false、True 才送驗
#   verify_critical: bool  — 預設 false、True 表示這步驗、不論 strategy

# 節點層級欄位
cu_vlm_check_strategy: str = "off"   # off / after_each / critical_only
cu_on_mismatch: str = "stop_notify"  # stop_notify / retry_once / skip_and_continue
cu_vlm_provider: str = "anthropic"   # anthropic / openai
cu_vlm_max_retries: int = 1          # retry_once 模式下重試幾次
```

### 2.2 執行流程(`pipeline/computer_use.py`)

虛擬碼:
```python
async def execute_cu_step(step):
    for i, action in enumerate(step.cu_actions):
        before_img = screenshot()
        execute_action(action)      # pyautogui.click / type / scroll
        await asyncio.sleep(0.3)    # 給 UI render
        after_img = screenshot()

        if not should_verify(action, step):
            continue

        verdict = await vlm_verify(
            before=before_img,
            after=after_img,
            expected=action["expected"],
            provider=step.cu_vlm_provider,
        )
        # verdict = {"ok": bool, "reason": str, "diff_summary": str}

        if verdict["ok"]:
            log(f"step {i+1}/{len(actions)} ✅ {action['expected']}")
            continue

        # 不 ok
        if step.cu_on_mismatch == "stop_notify":
            push_tg(after_img, verdict, action)
            return ExecResult(exit_code=1, awaiting_human=True)
        elif step.cu_on_mismatch == "retry_once":
            if retry_count[i] < step.cu_vlm_max_retries:
                retry this action, +1 retry_count
            else:
                push_tg + stop
        elif step.cu_on_mismatch == "skip_and_continue":
            log warning, continue to next action
```

### 2.3 VLM 驗證(`pipeline/cu_vlm_verifier.py` — 新檔)

```python
async def vlm_verify(
    before: bytes,           # PNG bytes
    after: bytes,
    expected: str,
    provider: str = "anthropic",
) -> dict:
    """送兩張截圖 + expected 描述給 VLM、回 verdict。"""
    prompt = f"""你看兩張截圖、第一張是動作前、第二張是動作後。
預期動作後的狀態:{expected}

請判斷:動作後是否符合預期?
回 JSON(嚴格、不要多餘文字):
{{
  "ok": true | false,
  "reason": "(1-2 句說明)",
  "unexpected": "(若有 popup / 錯誤訊息 / 預期外元素、列出;沒有就空字串)"
}}"""
    if provider == "anthropic":
        # claude-sonnet-4-6 用 base64 image
        ...
    elif provider == "openai":
        # gpt-4o 用 image_url base64
        ...
    return parsed_json
```

### 2.4 Frontend 設定 UI(`frontend/app/pipeline/_humanConfirmPanel.tsx`...)

⚠ 等 backend 跑通再做。預估:
- 錄製器加「expected outcome 描述」欄(每動作一格)
- 節點 inspector 加 `cu_vlm_check_strategy` / `cu_on_mismatch` / `cu_vlm_provider` 下拉
- 失敗 awaiting_human 狀態前端可看到截圖 + verdict reason

### 2.5 TG 推送格式

```
❌ Computer use 步驟 3/12 偏離預期

節點: open_save_dialog
動作: click(x=200, y=380)
預期: 點完選單裡『另存新檔』、輸入框出現

VLM 判定: 不符合
原因: 動作後對話框沒開、選單仍維持原狀
意外: 無 popup

要重試 / 跳過 / 中止? 在 desktop 點按鈕。
[附:after.png 截圖]
```

---

## 3. 實作順序

| 步驟 | 內容 | 工作量 |
|---|---|---|
| 1 | `pipeline/models.py` 加新欄位 | 30 min |
| 2 | `cu_vlm_verifier.py` 新檔(Anthropic + OpenAI 兩個 provider) | 半天 |
| 3 | `pipeline/computer_use.py` 改執行 loop 接驗證 | 半天 |
| 4 | TG push 接 stop_notify 路徑(含截圖 send_photo) | 半天 |
| 5 | 寫 e2e 測試:錄一段已知會失敗的動作、驗 VLM 抓得到 | 半天 |
| 6 | Frontend 錄製器加 expected 欄 | 半天 |
| 7 | Frontend 節點 inspector 加 strategy / mismatch 設定 | 半天 |
| 8 | 文件:更新 CLAUDE.md / README 寫 VLM 驗證怎麼開 | 1 hr |

**估時:2-3 個工作天**

---

## 4. 成本估算

VLM 驗證單次:~500-1000 tok input(2 張圖)+ ~100 tok output

| Provider | 單次 cost | 20 步流程 |
|---|---|---|
| Anthropic Sonnet 4.6 | ~$0.005-0.012 | $0.10-0.24 |
| OpenAI GPT-4o | ~$0.005-0.015 | $0.10-0.30 |

對比省下「跑錯整套後 debug 1 小時」、CP 值極高。

---

## 5. 風險與緩解

| 風險 | 機率 | 緩解 |
|---|---|---|
| VLM 誤判(明明 ok 卻說 fail) | 中 | 提供 retry_once 模式 + skip_and_continue 鬆模式 |
| VLM 漏判(明明 fail 卻說 ok) | 中 | expected 描述要精準、用戶錄製時 prompt 引導怎麼寫 |
| 截圖太大送爆 token | 低 | 截圖前壓 / resize 到 1280x720 max |
| TG send_photo 失敗 | 低 | fallback 純文字 push、附 screenshot 路徑供桌面看 |
| 自動 retry 重複觸發失敗動作 | 中 | retry 上限預設 1、用戶可調 |

---

## 6. Phase 2(延後做)

觸發條件:Phase 1 跑穩 ≥3 個月 + 用戶要求「VLM 驗證失敗時自動修復而非通知」

技術選型(2026 Q2 推薦):
- **OmniParser** (Microsoft、開源、本地跑、需 GPU)
- **Set-of-Mark prompting** — 把 element 標號讓 LLM 選編號、不算座標
- **Hybrid**:OmniParser 解元素 → LLM 看 element list 選 → 取中心座標 click

安全 fence:
- 自主生座標連續最多 N 次(N=2-3)、再失敗強制 stop_notify
- 寫入操作(覆寫檔 / 送出表單 / 刪除)強制人類二次確認、不自動跑
- 每次自主操作 dry-run(hover 5 秒)讓用戶看到才真點

---

## 7. 開放問題

- [ ] expected 描述用戶不寫怎麼處理? **暫定**:沒寫就 verify_with_vlm=false 強制略過(降級為現況行為)
- [ ] 動作之間 sleep 時間怎麼定? **暫定**:固定 0.3s + 用戶可在 action 加 `wait_after: 1.0`
- [ ] 多 monitor 場景截圖? **暫定**:預設只截 primary、之後加 monitor 選項
- [ ] VLM 跑很慢拖慢整套執行? **暫定**:async 並行下個 action 截圖、用戶可調 `cu_vlm_check_strategy=critical_only` 只驗關鍵步

---

## 變更紀錄

- 2026-05-09:初稿、Phase 1 啟動
