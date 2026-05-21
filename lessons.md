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
