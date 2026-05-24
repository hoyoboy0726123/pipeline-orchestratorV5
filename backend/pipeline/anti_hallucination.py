"""反幻覺工具集(SUBAGENT 跟 SKILL loop 共用)。

針對 2026-05-24 ai_coding_market_research 等多次燒 token 的根因:
1. LLM 一次寫 N 個 <tool> 標籤、parser 只跑第一個、LLM 以為都跑了 → done(success=true) 幻覺
2. LLM 在 reply 內偽造 [工具結果 — X] 假 stdout(strings like "OK size=87KB")
3. done 守門失敗只能整個 step retry(燒整 prompt)、沒做 surgical retry

本模組提供:
- wrap_tool_result(): 強邊界格式、難偽造
- scan_llm_reply_for_fake_output(): 偵測 LLM 自己生強邊界(冒充 orchestrator)
- check_done_preflight(): done 多層檢查(檔存在 + 大小門檻 + 上一個 tool 是 run_python + 結果含 exists/size)
- surgical_retry_prompt(): 失敗時的短指令(節省 ~100× token)
- SYSTEM_PROMPT_ANTI_HALLUCINATION: 反幻覺 prompt 規則(加進 system prompt)

設計原則:
- 跟現有 _FAKE_DONE_LIMIT / multi_tool_warn 守門共存、不取代
- 對 Phase A.1 native function calling 友善(done preflight / surgical retry 都可保留)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ============================================================
# 強邊界格式
# ============================================================
# LLM 比較難偽造這麼結構化的標記、且 system prompt 明確說「邊界外的字串都是腦補」。
# 也可被 scan_llm_reply_for_fake_output 偵測 LLM 自己生這標記、reject。
_REAL_OUTPUT_BEGIN = "====[REAL OUTPUT FROM TOOL — {name} — DO NOT FABRICATE BELOW]===="
_REAL_OUTPUT_END = "====[END OF TOOL RESULT — {name}]===="


def wrap_tool_result(tool_name: str, result: str) -> str:
    """把工具結果包進強邊界格式。LLM 收到後比較難混淆「真結果」vs「自己的敘述」。"""
    name = (tool_name or "?").strip()
    body = result if result is not None else ""
    return (
        f"{_REAL_OUTPUT_BEGIN.format(name=name)}\n"
        f"{body}\n"
        f"{_REAL_OUTPUT_END.format(name=name)}"
    )


# 偵測 LLM 自己生強邊界(企圖冒充 orchestrator 的真結果)— 應該 0 出現率
_FAKE_OUTPUT_PATTERN = re.compile(
    r"====\[REAL OUTPUT FROM TOOL.*?DO NOT FABRICATE.*?====",
    re.IGNORECASE,
)


def scan_llm_reply_for_fake_output(reply: str) -> list[str]:
    """偵測 LLM reply 內有沒有偽造強邊界。
    回傳偽造片段(空 list = 乾淨)。有 → 拒收 reply + 要求 LLM 重寫。

    注意:caller 應該套用「>= 2 個才算違規」的寬鬆閾值。LLM 引用之前 tool result
    可能會 quote 1 個邊界當 cite source、不算惡意偽造。連續 N 次 >= 2 違規才升級成
    abort step(避免無限 reject 燒 token)。
    """
    if not reply:
        return []
    return _FAKE_OUTPUT_PATTERN.findall(reply)


def fake_output_reminder(fake_count: int) -> str:
    """LLM 偽造強邊界時的 reminder — 明確告訴它「把資料寫進檔、不要 inline 在 reply」。

    對應根因:LLM 心智模型錯誤、把報告 / 資料表直接寫 reply 內、自己畫邊界當分隔器。
    純說「不要偽造」沒用、必須給出正確策略(寫到檔)。
    """
    return (
        f"[系統] 你 reply 內生了 {fake_count} 個 ====[REAL OUTPUT FROM TOOL ...]==== 強邊界、"
        f"那是 orchestrator 才能生的、你不可以冒充。\n"
        f"\n"
        f"⚠ **根本問題**:你想把資料表 / 報告 / 來源列表**直接寫在 reply 內**、用邊界區分區塊 — 這是錯的策略。\n"
        f"\n"
        f"✗ 不要再做的:把 markdown 報告 / data table / 來源 cite 寫在 reply 文字內。reply 越長越燒 token、且會被 reject。\n"
        f"\n"
        f"✓ **正確策略**:用 run_python 把資料寫進檔:\n"
        f"```\n"
        f"<tool>run_python</tool>\n"
        f"<input>\n"
        f"content = '''# 報告標題\n"
        f"## 資料來源\n"
        f"- ...你的完整內容...\n"
        f"'''\n"
        f"with open('/絕對路徑/output.md', 'w', encoding='utf-8') as f:\n"
        f"    f.write(content)\n"
        f"print('OK size=', len(content))\n"
        f"</input>\n"
        f"```\n"
        f"\n"
        f"下一輪 reply **不要含任何資料 / 報告 / 來源內容** — 只寫一個 <tool>run_python</tool> + 寫檔 code。"
    )


# 偽 fake 違規連續次數上限 — 超過直接 abort step、別無限 reject 燒 token
FAKE_OUTPUT_VIOLATION_LIMIT = 3
# 多少個偽邊界才算違規(LLM 引用 1 個當 cite 可寬鬆放行)
FAKE_OUTPUT_MIN_COUNT = 2


# ============================================================
# done preflight + surgical retry
# ============================================================
@dataclass
class DoneCheckResult:
    accept: bool
    reason: str = ""
    surgical_retry_prompt: str = ""


# Office / 圖檔 / 一般檔的大小門檻(避免 LLM 寫空殼)
_OFFICE_EXTS = {".pptx", ".docx", ".xlsx"}
_OFFICE_MIN_BYTES = 5000
_GENERIC_MIN_BYTES = 100

# 上一個 tool 結果裡至少要看到「真實驗證過」的痕跡(寬鬆關鍵字)
_VERIFICATION_HINTS = (
    "exists()", "exists:", "exists =", "exists=true",
    "size=", "size:", "size =", "ok size", "✅",
)


def check_done_preflight(
    output_path: Optional[str],
    last_tool_name: Optional[str],
    last_tool_result: Optional[str],
) -> DoneCheckResult:
    """LLM 想 <tool>done</tool> 之前的多層 preflight 檢查。

    通過 = LLM 真的寫了檔、上一輪 run_python 也驗證過、結果有 exists/size 痕跡。
    失敗 = surgical_retry_prompt 是極短指令(< 1KB)、直接塞進 LLM 下一輪 input、
           不重發整個 system prompt。
    """
    if not output_path:
        # 沒 output_path 要求 → 沒辦法驗、放行(原行為)
        return DoneCheckResult(accept=True)

    out = Path(output_path).expanduser()
    ext = out.suffix.lower()
    is_office = ext in _OFFICE_EXTS
    size_floor = _OFFICE_MIN_BYTES if is_office else _GENERIC_MIN_BYTES

    # 1. 檔案存在?
    if not out.exists():
        return DoneCheckResult(
            accept=False,
            reason=f"output file does not exist: {out}",
            surgical_retry_prompt=_retry_prompt_by_ext(out, "not_exist"),
        )

    # 2. 大小門檻(避免空殼 / 損毀 / 0 byte)
    try:
        size = out.stat().st_size
    except OSError as e:
        return DoneCheckResult(
            accept=False,
            reason=f"stat failed: {e}",
            surgical_retry_prompt=_retry_prompt_by_ext(out, "stat_fail"),
        )
    if size < size_floor:
        return DoneCheckResult(
            accept=False,
            reason=f"output too small ({size:,} bytes, floor={size_floor:,})",
            surgical_retry_prompt=_too_small_retry(out, size, size_floor),
        )

    # 3. 上一個 tool 應該是 run_python / run_shell(寫檔的證據)、不是 read_file / web_search
    if last_tool_name and last_tool_name not in ("run_python", "run_shell"):
        return DoneCheckResult(
            accept=False,
            reason=f"last tool was {last_tool_name}, need run_python with exists() verification",
            surgical_retry_prompt=_no_verify_retry(out),
        )

    # 4. 上一個 tool 結果含「真實驗證過」痕跡
    if last_tool_result:
        low = last_tool_result.lower()
        if not any(h in low for h in _VERIFICATION_HINTS):
            return DoneCheckResult(
                accept=False,
                reason="last tool result has no exists()/size verification trace",
                surgical_retry_prompt=_no_verify_retry(out),
            )

    return DoneCheckResult(accept=True)


def _retry_prompt_by_ext(out: Path, reason: str) -> str:
    ext = out.suffix.lower()
    if ext == ".pptx":
        return _pptx_retry(out)
    if ext == ".docx":
        return _docx_retry(out)
    if ext == ".xlsx":
        return _xlsx_retry(out)
    return _generic_retry(out)


def _pptx_retry(out: Path) -> str:
    return (
        f"⚠ 上次 done 被拒 — 輸出檔不存在:{out}\n\n"
        f"請**只寫一個** <tool>run_python</tool> 跑下面這段(不要寫多 tool、不要解釋、不要重寫整個 JS):\n\n"
        f"```python\n"
        f"import subprocess\n"
        f"from pathlib import Path\n"
        f"out = Path(r\"{out}\")\n"
        f"js = None\n"
        f"for cand in ('create_ppt.js', 'create_pptx.js', 'build_pptx.js', 'build_ppt.js'):\n"
        f"    c = out.parent / cand\n"
        f"    if c.exists():\n"
        f"        js = c; break\n"
        f"if js is None:\n"
        f"    raise SystemExit(f'找不到 .js 腳本於 {{out.parent}}')\n"
        f"r = subprocess.run(['node', str(js)], capture_output=True, text=True, timeout=180, cwd=str(out.parent))\n"
        f"print('rc=', r.returncode)\n"
        f"print('stderr:', r.stderr[:500])\n"
        f"assert r.returncode == 0\n"
        f"assert out.exists(), f'node 跑完但 {{out}} 還是不存在'\n"
        f"print('OK size=', out.stat().st_size)\n"
        f"```\n\n"
        f"看到 `OK size=...` 才能 <tool>done</tool>。"
    )


def _docx_retry(out: Path) -> str:
    return (
        f"⚠ 上次 done 被拒 — 輸出檔不存在:{out}\n\n"
        f"請**只寫一個** <tool>run_python</tool>:\n\n"
        f"```python\n"
        f"import subprocess\n"
        f"from pathlib import Path\n"
        f"out = Path(r\"{out}\")\n"
        f"py = None\n"
        f"for cand in ('create_docx.py', 'build_docx.py', 'make_docx.py'):\n"
        f"    c = out.parent / cand\n"
        f"    if c.exists():\n"
        f"        py = c; break\n"
        f"if py is None:\n"
        f"    raise SystemExit('找不到產生器腳本')\n"
        f"r = subprocess.run(['python', str(py)], capture_output=True, text=True, timeout=180, cwd=str(out.parent))\n"
        f"print('rc=', r.returncode, 'stderr:', r.stderr[:500])\n"
        f"assert r.returncode == 0 and out.exists()\n"
        f"print('OK size=', out.stat().st_size)\n"
        f"```\n"
    )


def _xlsx_retry(out: Path) -> str:
    return (
        f"⚠ 上次 done 被拒 — 輸出檔不存在:{out}\n\n"
        f"請**只寫一個** <tool>run_python</tool> 真實生成 .xlsx 後驗證:\n\n"
        f"```python\n"
        f"from pathlib import Path\n"
        f"out = Path(r\"{out}\")\n"
        f"assert out.exists(), f'{{out}} 不存在'\n"
        f"print('OK size=', out.stat().st_size)\n"
        f"```\n"
    )


def _generic_retry(out: Path) -> str:
    return (
        f"⚠ 上次 done 被拒 — 輸出檔不存在:{out}\n\n"
        f"請**只寫一個** <tool>run_python</tool> 真實產出此檔、然後驗證:\n\n"
        f"```python\n"
        f"from pathlib import Path\n"
        f"out = Path(r\"{out}\")\n"
        f"assert out.exists(), f'{{out}} 不存在'\n"
        f"print('OK size=', out.stat().st_size)\n"
        f"```\n\n"
        f"看到 `OK size=` 才能 done。"
    )


def _too_small_retry(out: Path, size: int, floor: int) -> str:
    return (
        f"⚠ {out.name} 雖然存在、但只有 {size:,} bytes(門檻 {floor:,})、看起來是空殼或損毀。\n"
        f"請真實重新生成、確認 size > {floor:,} 才能 done。\n"
        f"先跑短 run_python 看 `print(out.stat().st_size)`、再決定要不要補資料。"
    )


def _no_verify_retry(out: Path) -> str:
    return (
        f"⚠ 你的 done 之前沒有真實的 Path.exists() / size 驗證 — 不能信任。\n\n"
        f"請**只寫一個** <tool>run_python</tool> 跑這 3 行:\n\n"
        f"```python\n"
        f"from pathlib import Path\n"
        f"out = Path(r\"{out}\")\n"
        f"assert out.exists(), f'{{out}} 不存在'\n"
        f"print('OK size=', out.stat().st_size)\n"
        f"```\n\n"
        f"看到 `OK size=` 才能 done。"
    )


# ============================================================
# Multi-tool 警告強化(從 P0 SingleAcceptStripper 借核心)
# ============================================================
def multi_tool_reminder(tag_count: int, executed_tool_name: str) -> str:
    """LLM 一個 reply 寫 N 個 tool 標籤(只跑第 1 個)時、塞給 LLM 下一輪的 reminder。
    比單純「⚠ 偵測到 N 個」更明確告訴 LLM「你 reply 內描述後面那些 tool 已跑、那是錯覺」。"""
    dropped = tag_count - 1
    return (
        f"[系統警告] 你上次 reply 寫了 {tag_count} 個 <tool> 標籤、"
        f"但**系統只執行了第 1 個({executed_tool_name})**。"
        f"後面 {dropped} 個被丟掉、從沒跑過。\n"
        f"\n"
        f"⚠ **如果你 reply 內描述「已經跑了 XX」「已生成 YY KB」「OK size=...」**,"
        f"那是錯覺 — 那些後續 tool 沒有任何結果回給你。\n"
        f"\n"
        f"請只基於上面**強邊界內的真結果**繼續推論。"
        f"\n"
        f"**下一輪請只寫一個 <tool> 標籤、等真結果再寫下一個。**"
    )


# ============================================================
# 加進 system prompt 的反幻覺規則
# ============================================================
SYSTEM_PROMPT_ANTI_HALLUCINATION = """

## ⛔ 反幻覺絕對規則(違反 = 系統拒收、燒你 token)

### Rule 1 — 一次只寫一個 <tool> 標籤
- ❌ 不要在同一個 reply 寫多個 `<tool>...</tool><input>...</input>` 配對。
- 正確:寫**一個**配對 → **停下** → 等真結果回 → 再寫下一個。
- 寫多個 = orchestrator 只跑第 1 個、後面那些**從沒執行過**;你下次說「已經跑了」就是幻覺。

### Rule 2 — <tool>done</tool> 之前必須真實驗證
- 任何聲稱「已生成檔案 X」的 done、**前一個 tool 必須是 run_python**、且該 tool 結果含:
  - `Path(X).exists()` 真實 print 為 True
  - 檔案 size 大於合理門檻(.pptx > 5KB,其他檔 > 100B)
- 不符 = orchestrator 拒收 done + 發短指令叫你補驗、燒你 retry token。

### Rule 3 — 「我以為已經跑了」 = 沒跑
- 上下文壓縮 / context 超長時、寧可先跑 3 行 run_python 看 `Path(x).exists()`、別憑記憶 done。

### Rule 4 — 不要把資料 / 報告 inline 在 reply 內
- 任務需要產出 markdown / json / 資料表 → 用 run_python + `with open(path, 'w'): f.write(content)` 寫進檔。
- ❌ 不要在 reply 文字內列完整資料表 / 來源列表 / 報告內容(會燒 token + 不會被 validator 看到)。
"""
