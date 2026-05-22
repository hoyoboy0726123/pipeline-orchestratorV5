# V5 Lessons

專案專屬的踩坑記錄。跨專案通用的請寫到 Obsidian `30-Lessons/`。

---

## 2026-05-20:bash `((var++))` 在 `set -e` 下是地雷

**症狀**:`sandbox/setup.sh` 跑到 default_skills 安裝迴圈第一次 `((installed++))`
就 abort 整個腳本,前端只看到「!! Setup FAILED」沒有任何 root cause 提示。

**根因**:bash 算術 post-increment `((var++))` 的 exit status 用「**舊值**」當布林:
- `var=0` → `((var++))` 把 var 設成 1、但 expression 回傳 0 → exit 1(false)
- `var=1` → `((var++))` 把 var 設成 2、expression 回傳 1 → exit 0(true)

腳本開頭 `set -euo pipefail`,exit 1 立刻 abort。**第一次計數從 0 增加必死**。

**修法**:全部改 `var=$((var + 1))` 算術賦值。賦值表達式 exit status 永遠是 0(指派成功)、
跟變數值無關。

```bash
# ❌ 在 set -e 下是地雷
installed=0
for src in ...; do
    cp -r "$src" "$target"
    ((installed++))    # 第一次回傳 exit 1 → set -e abort
done

# ✅ 安全
installed=0
for src in ...; do
    cp -r "$src" "$target"
    installed=$((installed + 1))    # exit 永遠 0
done
```

**衍生**:`((++var))` (pre-increment) 也安全,因為它回傳「新值」、第一次是 1(true)。
但 `var=$((var + 1))` 寫法更明確、不會誤用成 post。

**Commit**:`fee40a8` (`fix(install): default_skills loop abort under set -e`)

---

## 2026-05-21:Windows DPI awareness 是 OCR/click 跨機台的隱形地雷

**症狀**:在 C:(150% scaling)錄製的 OCR 框 / click 座標,搬到 D:(125% scaling)
整個錯位到左上、OCR「看到的字都不在框選範圍附近」。同台機自相容、跨機就壞。

**根因**:整個 backend 沒呼叫過 `SetProcessDpiAwareness*`,Python process 預設
DPI-unaware。Windows 對 DPI-unaware app 會撒謊、回**邏輯像素**(虛擬桌面尺寸):

| 機台 | 物理 | scaling | Windows 騙它的「邏輯螢幕」 |
|---|---|---|---|
| C: dev | 2560×1600 | 150% | 1707×1067 |
| D: Atlas | 1920×1080 | 125% | 1536×864 |

`mss.grab()` 跟 `pyautogui.click()` 在這個邏輯空間自相容、所以同台機錄什麼都對。
但同一組 (x, y) 邏輯座標在兩台對應的物理位置完全不同 → 跨機台搬必錯位。

**修法**:`main.py` 最頂(任何 import 之前)設 `PROCESS_PER_MONITOR_DPI_AWARE_V2`。
之後 mss / pyautogui / GetCursorPos 全部用物理座標、跨機只要物理解析度一致就相容。

**副作用**:**舊的(本修復前錄製的)workflow 座標是邏輯像素、修完即使在同一台
也會錯位、必須重錄一次**。新錄製的座標跨機台 portable(物理解析度一致為前提)。

**血淚附註(這個 lesson 第二層)**:`SetProcessDpiAwarenessContext(-4)` 第一次寫
是 `_user32.SetProcessDpiAwarenessContext(-4)`,看似合理但 **silent fail**(awareness
還是 0)。原因:這函式型別是 `BOOL fn(DPI_AWARENESS_CONTEXT)`、`DPI_AWARENESS_CONTEXT`
是 HANDLE(指標),不是 int。ctypes 預設把 Python int 當 `c_int` 傳、API 收到的指標
被當成不合法 handle、回 FALSE。沒檢查回傳值就以為設成功、commit 推完 GetProcessDpiAwareness
反查才發現 awareness 還是 UNAWARE。

正確寫法:
```python
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int  # BOOL
ok = user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
if not ok:
    # fallback to v1 / SetProcessDpiAwareness / SetProcessDPIAware
    ...
```

跨專案的 ctypes 教訓參 Obsidian `30-Lessons/2026-05-21-ctypes-win32-handle-silent-fail.md`。

**Commit**:`3200f24`(真正生效的版本)。先前 `c843080` 推上去 awareness 還是 0、
就是踩到上面 ctypes 那一槍。

**驗證辦法**:跑外部腳本 `GetProcessDpiAwareness(pid)`、預期 = 2 (PER_MONITOR):
```powershell
$bpid = (Get-NetTCPConnection -LocalPort 8004 -State Listen).OwningProcess
& ".\.venv\Scripts\python.exe" -c "import ctypes; h=ctypes.windll.kernel32.OpenProcess(0x1000,False,$bpid); a=ctypes.c_int(); ctypes.windll.shcore.GetProcessDpiAwareness(h,ctypes.byref(a)); print(a.value)"
```

---

## 2026-05-23:Pydantic BaseModel 預設默默丟未宣告欄位,新加 action 欄位記得進 schema

**症狀**:錄製端把 UIA element info 寫進 YAML(`ui: {name: ..., control_type: ...}`),
肉眼確認 YAML 檔有,但回放時 `action.get("ui")` 永遠 None、UIA-first phase 在
if 條件就 silent skip,連 log 都不出。

**根因**:V5 的 action 走 `ComputerUseAction(BaseModel)` Pydantic 模型。YAML 載入後
經 `PipelineConfig.from_dict() → PipelineStep.actions: list[ComputerUseAction]` 解析,
Pydantic 預設行為(`model_config` 沒設 `extra=...` 時)會**默默丟掉未宣告的欄位**。

`ui` 在 YAML 有,但 schema 沒宣告 → Pydantic instantiate 時 silently drop →
`model_dump()` 出來的 dict 沒這個 key → execute_action 永遠看不到。

**修法**:在 `ComputerUseAction` schema 顯式宣告 `ui: dict = {}` 欄位、Pydantic 才會
收。即使只是透傳用的雜資料、也要進 schema。

**衍生規則**(這個專案以後遇到):
- 任何錄製端 / AI 助手 / YAML 想塞給 action 的新欄位、都要**先在 `ComputerUseAction`
  schema 加上**(`dict = {}` / `list = []` / 字面型別都行)
- 同規則適用 `PipelineStep` 跟 `PipelineConfig`
- 想要 strict 一點、避免下次又踩同坑,可以給 model_config 設 `extra="forbid"`,
  寫一個未宣告欄位直接 raise — 但這會打到很多現有 workflow,保守做法是 case-by-case 補
- debug 類似問題,優先加「無條件 log 一行 action keys 進來時長什麼樣」,不要把 log
  鎖在 `if condition:` 內,silent skip 最難 debug

**Commit**:`345d555`(fix 加 ui 欄位)、`e81e1c6`(加 verbose log 才暴露 silent skip)
