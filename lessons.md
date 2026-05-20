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
