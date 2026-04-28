"""
Outlook 自動化節點：確定性模板 handlers（不走 LLM）。

每個 handler 對應一個前端模板 ID。直接呼叫 win32_helpers.outlook 的 wrapper、
把結果寫到 output_path。完全跳過 LLM agent loop —— 快、可預測、沒 token 成本。

哪些模板適合在這？
  - 「mechanical」操作：查詢→格式化→存檔；寄信→存檔；下載附件→存檔
  - 不需要文字摘要 / 智能分類 / 結構推論的

哪些模板需要 LLM（不放這、留在 execute_step_with_outlook 的 agent loop）？
  - search_summary（要 LLM 寫摘要）
  - unanswered（要綜合判斷「我有沒有回過」、暫時太複雜）
  - 任何「自由輸入需求」

使用：runner.py 看到 step.outlook_automation 且 template 在 DIRECT_HANDLERS 裡時，
直接 call handler，不進 LLM。
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# 注意：這些 import 只在 host venv 跑得起來；runner 已經保證 outlook node 走 host
import pandas as pd  # noqa: E402

from .models import StepOutput  # noqa: E402

# win32_helpers 內部 import 才會帶 pywin32（只在 Windows 上跑）
# 這個 module 是後端的一部分、只會被 Windows host 載入，不會被 sandbox 載入

logger = logging.getLogger("pipeline.outlook_templates")


class OutlookTemplateError(RuntimeError):
    """Direct handler 執行失敗，含使用者可看的中文訊息。"""


# ── 共用 helper ──────────────────────────────────────────────────────


def _split_emails(s: Any) -> Optional[list[str]]:
    """字串 'a@x.com, b@x.com' → list；空值回 None。"""
    if not s:
        return None
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    parts = [p.strip() for p in str(s).replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    return parts or None


def _split_keywords(s: Any) -> Optional[list[str]]:
    """主旨 / 寄件人 / 關鍵字的多值字串解析（支援 ',' 跟 '，'）。"""
    if not s:
        return None
    if isinstance(s, list):
        return [str(x).strip() for x in s if str(x).strip()]
    parts = [p.strip() for p in str(s).replace("，", ",").split(",")]
    parts = [p for p in parts if p]
    return parts or None


# 專案根目錄 (backend/pipeline/outlook_templates.py 的上三層)。
# 跟 runner.py 同邏輯：YAML / canvas 寫的相對路徑都以這個為基準。
_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_user_path(p: str) -> Path:
    """使用者填的路徑解析成絕對路徑：
    - `~/xxx` 展開到家目錄
    - 絕對路徑直接用
    - 相對路徑 → 以**專案根目錄**為基準
    跟 runner.py:_resolve_path 對齊，避免 outlook 模板看到 prev_outputs 裡的
    相對路徑（如 ai_output/xxx/file.xlsx）以為要從 backend cwd 找而拿不到。"""
    pp = Path(p).expanduser()
    if not pp.is_absolute():
        pp = _PROJ_ROOT / pp
    return pp


def _resolve_prev_output(prev_outputs: Optional[list]) -> Optional[str]:
    """從 prev_outputs 拿最近一個有 path 的；解析成絕對路徑。"""
    if not prev_outputs:
        return None
    for o in reversed(prev_outputs):
        p = o.get("path") if isinstance(o, dict) else None
        if p:
            return str(_resolve_user_path(str(p)))
    return None


def _substitute_prev_in_attachments(att: Any, prev_outputs: Optional[list]) -> list[str]:
    """把使用者填的附件清單裡的 {prev_output} placeholder 換成實際路徑。

    輸入支援：list / 多行字串 / 單一字串 / None
    """
    if not att:
        return []
    if isinstance(att, str):
        items = [line.strip() for line in att.replace("\r\n", "\n").split("\n") if line.strip()]
    elif isinstance(att, list):
        items = [str(x).strip() for x in att if str(x).strip()]
    else:
        items = [str(att).strip()]
    prev = _resolve_prev_output(prev_outputs) or ""
    out = []
    for it in items:
        if "{prev_output}" in it:
            if not prev:
                raise OutlookTemplateError("附件含 {prev_output} 但前一步驟沒有可用的輸出檔")
            it = it.replace("{prev_output}", prev)
        # 相對路徑解析到專案根，避免從 backend cwd 找不到
        out.append(str(_resolve_user_path(it)))
    return out


def _df_to_format(df: pd.DataFrame, fmt: str, output_path: Path, *,
                   columns: Optional[list[str]] = None,
                   rename: Optional[dict[str, str]] = None,
                   header: str = "") -> Path:
    """把 DataFrame 依使用者要的格式寫到 output_path。
    fmt: md / xlsx / txt（其他值預設走 md）
    columns: 只保留這些欄位（None = 全部）
    rename: 欄名改中文 {"received": "收件時間", ...}
    header: 寫到檔案最上方的標題段（md / txt 適用，xlsx 忽略）

    回傳實際寫入的 Path（可能跟入參不同 — 若 fmt 跟原副檔名不匹配會自動調整）。"""
    if columns:
        df = df[[c for c in columns if c in df.columns]].copy()
    if rename:
        df = df.rename(columns=rename)

    fmt = (fmt or "md").lower()
    # 副檔名跟 fmt 不一致時自動調整 — runner 預先 default 路徑（通常是 .md）但
    # 使用者選了 xlsx → 若不調整會 ValueError "Invalid extension for engine"
    expected_suffix = {"xlsx": ".xlsx", "txt": ".txt", "md": ".md"}.get(fmt, ".md")
    if output_path.suffix.lower() != expected_suffix:
        output_path = output_path.with_suffix(expected_suffix)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "xlsx":
        df.to_excel(str(output_path), index=False, engine="openpyxl")
        return output_path

    if fmt == "txt":
        lines = []
        if header:
            lines.append(header)
            lines.append("")
        for _, row in df.astype(str).iterrows():
            for col in df.columns:
                lines.append(f"{col}: {row[col]}")
            lines.append("-" * 60)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    # 預設 md
    lines = []
    if header:
        lines.append(header)
        lines.append("")
    if df.empty:
        lines.append("_（沒有符合條件的項目）_")
    else:
        # 手動構 markdown table 避免依賴 tabulate；同時 astype(str) 避開 pandas 的 tz crash
        df_str = df.astype(str).replace({"NaT": "", "nan": "", "None": ""})
        cols = list(df_str.columns)
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join(["---"] * len(cols)) + "|")
        for _, row in df_str.iterrows():
            vals = [str(row[c]).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
                    for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _to_dt(s: Any) -> Optional[datetime]:
    """str / datetime / None → datetime；空回 None。"""
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return pd.to_datetime(s).to_pydatetime()


# ── Handler 定義（每個對應一個前端 template ID）──────────────────────


def _h_daily_todo(*, params: dict, output_path: Path,
                  prev_outputs: Optional[list], step_name: str,
                  logger_obj: Optional[logging.Logger] = None) -> str:
    """整理符合條件信件 → 待辦清單。

    回傳 stdout（給 runner 顯示），同時把整理結果寫到 output_path。
    """
    from .win32_helpers.outlook import search_mail

    folder = (params.get("folder") or "inbox").strip() or "inbox"
    subject = _split_keywords(params.get("subject"))
    sender = _split_keywords(params.get("sender"))
    exact = bool(params.get("exact_match"))
    since = _to_dt(params.get("since"))
    until = _to_dt(params.get("until"))
    unread_only = bool(params.get("unread_only"))
    fmt = (params.get("output_format") or "md").lower()

    df = search_mail(
        folder=folder, subject=subject, sender=sender,
        since=since, until=until,
        unread_only=unread_only, exact_match=exact,
        limit=500,
        logger=logger_obj,
    )
    n = len(df)
    (logger_obj or logger).info(f"[{step_name}] daily_todo 命中 {n} 封信，寫到 {output_path}")

    # 友善的中文欄名 + 只取常用欄位
    columns = ["received", "sender_name", "subject", "is_unread", "has_attachments"]
    rename = {
        "received": "收件時間", "sender_name": "寄件人", "subject": "主旨",
        "is_unread": "未讀", "has_attachments": "有附件",
    }
    header_parts = [
        f"# 待辦清單 — {step_name}",
        f"資料夾：`{folder}`，命中 {n} 封信",
    ]
    if subject:
        header_parts.append(f"主旨關鍵字：{', '.join(subject)}（{'精確' if exact else '模糊'}比對）")
    if sender:
        header_parts.append(f"寄件人：{', '.join(sender)}")
    if since or until:
        header_parts.append(f"日期：{since or '∞'} ~ {until or '現在'}")
    if unread_only:
        header_parts.append("僅未讀")

    actual_path = _df_to_format(df, fmt, output_path,
                                 columns=columns, rename=rename, header="\n".join(header_parts))
    return f"daily_todo 完成：命中 {n} 封信、輸出格式 {fmt}、檔案：{actual_path}"


def _h_download_attachments(*, params: dict, output_path: Path,
                             prev_outputs: Optional[list], step_name: str,
                  logger_obj: Optional[logging.Logger] = None) -> str:
    """批次下載符合條件信件的附件。"""
    from .win32_helpers.outlook import search_mail, download_attachments

    out_dir = (params.get("out_dir") or "").strip()
    if not out_dir:
        raise OutlookTemplateError("download_attachments 需要填「目標資料夾 (out_dir)」")
    out_dir_path = Path(out_dir).expanduser()

    df = search_mail(
        subject=_split_keywords(params.get("subject")),
        sender=_split_keywords(params.get("sender")),
        since=_to_dt(params.get("since")),
        until=_to_dt(params.get("until")),
        has_attachment=True,  # 強制有附件才有意義
        limit=500,
        logger=logger_obj,
    )
    if df.empty:
        output_path.write_text(f"# 附件下載報告\n\n找不到符合條件的有附件信件。", encoding="utf-8")
        return "download_attachments 完成：0 封信件、未下載任何附件"

    name_tpl = (params.get("name_template")
                or "{date}_{sender}_{filename}").strip()
    # 副檔名過濾：CSV 格式 'pdf, .xlsx, zip'；空字串或 None = 全抓
    raw_ext = (params.get("extensions") or "").strip()
    ext_list: Optional[list[str]] = None
    if raw_ext:
        ext_list = [
            e.strip() for e in raw_ext.replace("，", ",").split(",")
            if e and e.strip()
        ] or None

    saved = download_attachments(
        entry_ids=df["entry_id"].tolist(),
        out_dir=str(out_dir_path),
        name_template=name_tpl,
        extensions=ext_list,
    )

    filter_desc = f"（過濾：{', '.join(ext_list)}）" if ext_list else ""
    lines = [
        f"# 附件下載報告{filter_desc}",
        f"來源信件：{len(df)} 封",
        f"下載附件：{len(saved)} 個",
        f"目標資料夾：`{out_dir_path}`",
        "",
        "## 已下載清單",
    ]
    for p in saved:
        lines.append(f"- `{Path(p).name}`")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"download_attachments 完成：{len(saved)} 個附件已存到 {out_dir_path}{filter_desc}"


def _h_send_mail(*, params: dict, output_path: Path,
                 prev_outputs: Optional[list], step_name: str,
                 logger_obj: Optional[logging.Logger] = None) -> str:
    """寄信給指定收件人。"""
    from .win32_helpers.outlook import send_mail

    to = _split_emails(params.get("to"))
    if not to:
        raise OutlookTemplateError("send_mail 缺收件人 (to)")
    cc = _split_emails(params.get("cc"))
    bcc = _split_emails(params.get("bcc"))
    subject = (params.get("subject") or "").strip() or "(無主旨)"
    body = params.get("body") or ""
    body_format = (params.get("body_format") or "html").lower()
    attachments = _substitute_prev_in_attachments(params.get("attachments"), prev_outputs)
    save_to_drafts = bool(params.get("save_to_drafts"))

    eid = send_mail(
        to=to, cc=cc, bcc=bcc,
        subject=subject, body=body, body_format=body_format,
        attachments=attachments, save_to_drafts=save_to_drafts,
    )

    action = "存草稿" if save_to_drafts else "已送出"
    lines = [
        f"# 寄信報告 — {action}",
        f"收件人：{', '.join(to)}",
        f"主旨：{subject}",
        f"附件：{len(attachments)} 個" if attachments else "附件：無",
        f"EntryID：`{eid}`",
    ]
    if cc:
        lines.insert(2, f"副本：{', '.join(cc)}")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"send_mail {action}：to={to}, subject={subject[:40]}"


def _h_send_with_attachment(*, params: dict, output_path: Path,
                             prev_outputs: Optional[list], step_name: str,
                  logger_obj: Optional[logging.Logger] = None) -> str:
    """寄信附檔。預設用上一步輸出；attachment_path 有填則優先用該路徑。

    attachment_path 也支援 `{prev_output}` placeholder。
    """
    from .win32_helpers.outlook import send_mail

    to = _split_emails(params.get("to"))
    if not to:
        raise OutlookTemplateError("send_with_attachment 缺收件人 (to)")
    subject = (params.get("subject") or "").strip() or "(無主旨)"
    body = params.get("body") or ""

    # 1. 優先：使用者自填的 attachment_path
    custom_path = (params.get("attachment_path") or "").strip()
    if custom_path:
        if "{prev_output}" in custom_path:
            prev = _resolve_prev_output(prev_outputs) or ""
            if not prev:
                raise OutlookTemplateError(
                    "attachment_path 含 {prev_output} 但前一步驟沒有可用的輸出檔"
                )
            custom_path = custom_path.replace("{prev_output}", prev)
        attachment = _resolve_user_path(custom_path)
        source_desc = "自訂路徑"
    else:
        # 2. fallback：上一步輸出檔
        prev = _resolve_prev_output(prev_outputs)
        if not prev:
            raise OutlookTemplateError(
                "send_with_attachment 需要附件 — 請填 attachment_path、或讓前一步有 output"
            )
        attachment = Path(prev)
        source_desc = "上一步輸出"

    if not attachment.exists():
        raise OutlookTemplateError(f"附件不存在（{source_desc}）：{attachment}")

    eid = send_mail(
        to=to, subject=subject, body=body, body_format="html",
        attachments=[str(attachment)],
    )
    lines = [
        f"# 寄信報告（附 {source_desc}）",
        f"收件人：{', '.join(to)}",
        f"主旨：{subject}",
        f"附件：`{attachment.name}`（{attachment}）",
        f"EntryID：`{eid}`",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"send_with_attachment 已送出：to={to}, attachment={attachment.name}"


def _h_bulk_send(*, params: dict, output_path: Path,
                 prev_outputs: Optional[list], step_name: str,
                 logger_obj: Optional[logging.Logger] = None) -> str:
    """從 csv/xlsx 收件清單群發，主旨/本文支援 {欄位名} 變數。"""
    from .win32_helpers.outlook import send_mail

    rec_file = (params.get("recipient_file") or "").strip()
    if not rec_file:
        raise OutlookTemplateError("bulk_send 缺收件清單檔案 (recipient_file)")
    rec_path = Path(rec_file).expanduser()
    if not rec_path.exists():
        raise OutlookTemplateError(f"收件清單檔不存在：{rec_path}")

    if rec_path.suffix.lower() in (".csv",):
        df = pd.read_csv(rec_path)
    elif rec_path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(rec_path)
    else:
        raise OutlookTemplateError(f"不支援的收件清單格式：{rec_path.suffix}（請用 csv 或 xlsx）")

    if "email" not in df.columns and "Email" not in df.columns:
        raise OutlookTemplateError("收件清單必須有 'email' 欄位")
    email_col = "email" if "email" in df.columns else "Email"

    subject_tpl = params.get("subject_template") or "(無主旨)"
    body_tpl = params.get("body_template") or ""

    sent_log = []
    for _, row in df.iterrows():
        try:
            ctx = {k: ("" if pd.isna(v) else str(v)) for k, v in row.items()}
            subj = subject_tpl.format(**ctx)
            body = body_tpl.format(**ctx)
            send_mail(to=ctx[email_col], subject=subj, body=body, body_format="html")
            sent_log.append({"email": ctx[email_col], "status": "ok", "error": ""})
        except Exception as e:
            sent_log.append({"email": str(row.get(email_col, "?")), "status": "fail", "error": str(e)})

    df_log = pd.DataFrame(sent_log)
    success = (df_log["status"] == "ok").sum()
    fail = len(df_log) - success
    header = (f"# Bulk Send 報告\n\n總計 {len(df_log)} 筆，成功 {success}、失敗 {fail}")
    _df_to_format(df_log, "md", output_path, header=header)
    return f"bulk_send 完成：成功 {success}、失敗 {fail}（共 {len(df_log)} 筆）"


# ── Phase 2：批次管理操作 ───────────────────────────────────────────


def _search_for_bulk(params: dict, *, default_folder: str = "inbox",
                     logger_obj: Optional[logging.Logger] = None) -> "pd.DataFrame":
    """三個 bulk_* 模板共用的搜尋邏輯：依 subject/sender/folder/since/until 找信。"""
    from .win32_helpers.outlook import search_mail
    df = search_mail(
        subject=_split_keywords(params.get("subject")),
        sender=_split_keywords(params.get("sender")),
        folder=(params.get("folder") or default_folder).strip() or default_folder,
        since=_to_dt(params.get("since")),
        until=_to_dt(params.get("until")),
        limit=int(params.get("limit") or 500),
        logger=logger_obj,
    )
    return df


def _h_bulk_move(*, params: dict, output_path: Path,
                 prev_outputs: Optional[list], step_name: str,
                 logger_obj: Optional[logging.Logger] = None) -> str:
    """搜尋符合條件的信件、批次搬到目標資料夾。"""
    from .win32_helpers.outlook import move_mail

    target = (params.get("target_folder") or "").strip()
    if not target:
        raise OutlookTemplateError("bulk_move 缺『目標資料夾 (target_folder)』")

    df = _search_for_bulk(params, logger_obj=logger_obj)
    if df.empty:
        output_path.write_text("# 批次搬信報告\n\n找不到符合條件的信件。", encoding="utf-8")
        return "bulk_move 完成：0 封信件可搬"

    moved, failed = 0, []
    for eid in df["entry_id"].tolist():
        try:
            move_mail(entry_id=eid, target_folder=target)
            moved += 1
        except Exception as e:
            failed.append((eid, str(e)[:100]))

    lines = [
        f"# 批次搬信報告",
        f"條件命中：{len(df)} 封",
        f"成功搬移：{moved} 封 → `{target}`",
        f"失敗：{len(failed)} 封",
    ]
    if failed:
        lines.append("\n## 失敗清單")
        for eid, err in failed[:20]:
            lines.append(f"- `{eid[:30]}...`：{err}")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"bulk_move 完成：搬 {moved} 封到 `{target}`，失敗 {len(failed)} 封"


def _h_bulk_mark_read(*, params: dict, output_path: Path,
                      prev_outputs: Optional[list], step_name: str,
                      logger_obj: Optional[logging.Logger] = None) -> str:
    """搜尋符合條件的信件、批次標已讀／未讀。"""
    from .win32_helpers.outlook import mark_read

    state = (params.get("state") or "read").strip().lower()
    unread_flag = state in ("unread", "未讀")
    state_desc = "未讀" if unread_flag else "已讀"

    df = _search_for_bulk(params, logger_obj=logger_obj)
    if df.empty:
        output_path.write_text("# 批次標記報告\n\n找不到符合條件的信件。", encoding="utf-8")
        return "bulk_mark_read 完成：0 封信件可標"

    ok, failed = 0, []
    for eid in df["entry_id"].tolist():
        try:
            mark_read(entry_id=eid, unread=unread_flag)
            ok += 1
        except Exception as e:
            failed.append((eid, str(e)[:100]))

    lines = [
        f"# 批次標記{state_desc}報告",
        f"條件命中：{len(df)} 封",
        f"成功標記：{ok} 封",
        f"失敗：{len(failed)} 封",
    ]
    if failed:
        lines.append("\n## 失敗清單")
        for eid, err in failed[:20]:
            lines.append(f"- `{eid[:30]}...`：{err}")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"bulk_mark_read 完成：標 {state_desc} {ok} 封，失敗 {len(failed)} 封"


def _h_bulk_set_flag(*, params: dict, output_path: Path,
                     prev_outputs: Optional[list], step_name: str,
                     logger_obj: Optional[logging.Logger] = None) -> str:
    """搜尋符合條件的信件、批次設定旗標。"""
    from .win32_helpers.outlook import set_flag

    flag = (params.get("flag") or "follow_up").strip()

    df = _search_for_bulk(params, logger_obj=logger_obj)
    if df.empty:
        output_path.write_text("# 批次旗標報告\n\n找不到符合條件的信件。", encoding="utf-8")
        return "bulk_set_flag 完成：0 封信件可設旗標"

    ok, failed = 0, []
    for eid in df["entry_id"].tolist():
        try:
            set_flag(entry_id=eid, flag=flag)
            ok += 1
        except Exception as e:
            failed.append((eid, str(e)[:100]))

    lines = [
        f"# 批次旗標報告（{flag}）",
        f"條件命中：{len(df)} 封",
        f"成功設定：{ok} 封",
        f"失敗：{len(failed)} 封",
    ]
    if failed:
        lines.append("\n## 失敗清單")
        for eid, err in failed[:20]:
            lines.append(f"- `{eid[:30]}...`：{err}")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return f"bulk_set_flag 完成：設旗標 {ok} 封（{flag}），失敗 {len(failed)} 封"


# ── 註冊表 ───────────────────────────────────────────────────────────


# template_id → handler；只有在這裡的 template 才走 direct path、不進 LLM
DIRECT_HANDLERS = {
    "daily_todo": _h_daily_todo,
    "download_attachments": _h_download_attachments,
    "send_mail": _h_send_mail,
    "send_with_attachment": _h_send_with_attachment,
    "bulk_send": _h_bulk_send,
    "bulk_move": _h_bulk_move,
    "bulk_mark_read": _h_bulk_mark_read,
    "bulk_set_flag": _h_bulk_set_flag,
}


def is_direct_template(template: str) -> bool:
    return template in DIRECT_HANDLERS


# ── LLM prefetch handlers ────────────────────────────────────────────
# 給「需要 LLM 摘要 / 分析」的模板用。後端先把資料抓好（呼叫 win32_helpers），
# 把結果以 markdown 字串塞進 LLM prompt，LLM 只負責讀字串、整理成報告。
# 比讓 LLM 從零寫 search_mail() 程式碼穩定多了 — 不會因為 timezone / 套件 / pandas 怪
# 異 crash 卡住。LLM 只需要做語意理解（摘要、分類、結論）就好。


def _prefetch_search_summary(params: dict, prev_outputs: Optional[list],
                              logger_obj: Optional[logging.Logger] = None) -> tuple[bool, str, str]:
    """search_summary 模板的預抓資料。

    回傳 (ok, prefetched_markdown, error_msg)。
      ok=True  → prefetched_markdown 含信件內容，LLM 只摘要
      ok=False → LLM 自己想辦法抓（fallback path）
    """
    from .win32_helpers.outlook import search_mail

    raw_keywords = params.get("keywords") or ""
    if not raw_keywords.strip():
        return (False, "", "缺『關鍵字』參數，無法預抓")

    keywords = _split_keywords(raw_keywords)
    if not keywords:
        return (False, "", "關鍵字解析後為空")

    search_in = (params.get("search_in") or "subject").strip().lower()
    folder = (params.get("folder") or "inbox").strip() or "inbox"
    since = _to_dt(params.get("since"))
    until = _to_dt(params.get("until"))

    try:
        if search_in == "subject":
            df = search_mail(subject=keywords, folder=folder,
                             since=since, until=until, limit=200,
                             logger=logger_obj)
        elif search_in == "body":
            df = search_mail(body_keyword=keywords, folder=folder,
                             since=since, until=until, limit=200,
                             logger=logger_obj)
        else:  # both
            df_subj = search_mail(subject=keywords, folder=folder,
                                  since=since, until=until, limit=200,
                                  logger=logger_obj)
            df_body = search_mail(body_keyword=keywords, folder=folder,
                                  since=since, until=until, limit=200,
                                  logger=logger_obj)
            df = pd.concat([df_subj, df_body], ignore_index=True)
            if not df.empty:
                df = df.drop_duplicates(subset=["entry_id"]).reset_index(drop=True)
    except Exception as e:
        return (False, "", f"search_mail 失敗：{e.__class__.__name__}: {e}")

    if df.empty:
        return (True, f"（在資料夾 `{folder}` 內、條件「{', '.join(keywords)}」搜尋範圍 `{search_in}`，無符合的信件）", "")

    # 把信件內容組成 LLM 友善的 markdown
    lines = [f"## 共找到 {len(df)} 封符合條件的信件\n"]
    df_str = df.astype(str).replace({"NaT": "", "nan": "", "None": ""})
    for i, row in df_str.iterrows():
        lines.append(f"### 信件 #{i + 1}：{row['subject']}")
        lines.append(f"- **寄件人**：{row['sender_name']} <{row['sender_email']}>")
        lines.append(f"- **收件時間**：{row['received']}")
        if row.get("has_attachments") == "True":
            lines.append(f"- **附件**：{row.get('attachment_names', '')}")
        body = row.get("body_text", "")
        # body 截短 — 太長 LLM 也讀不完，1500 字夠摘要
        if len(body) > 1500:
            body = body[:1500] + f"\n\n…（本文截斷，原長 {len(body)} 字）"
        lines.append(f"- **本文**：")
        lines.append("```")
        lines.append(body.replace("`", "'"))  # 避免 ` 跟外層 code fence 衝突
        lines.append("```")
        lines.append("")
    return (True, "\n".join(lines), "")


def _prefetch_unanswered(params: dict, prev_outputs: Optional[list],
                          logger_obj: Optional[logging.Logger] = None) -> tuple[bool, str, str]:
    """unanswered 模板的預抓資料：找出收件匣中我還沒回過的信。

    邏輯：
      1. 抓收件匣內 N 天前到 X 天前之間的信（received 在 [until-N, until]）
      2. 抓寄件備份內過去 (N + grace) 天的信（涵蓋我可能晚一點才回）
      3. 兩邊都用 ConversationID 比對；inbox 中 conv_id 沒在 sent.conv_ids 裡 = 未回

    回傳 (ok, prefetched_markdown, error_msg)。
    """
    from .win32_helpers.outlook import search_mail

    days = params.get("days")
    try:
        days = int(days) if days not in (None, "") else 3
    except Exception:
        return (False, "", f"days 必須是整數，收到 {days!r}")
    if days < 0:
        return (False, "", "days 不能是負數")

    sender_filter = _split_keywords(params.get("sender_filter")) or None
    log = logger_obj or logger

    # 時間範圍：找 N 天前以上、但不超過 90 天前的信（避免一次抓太多）
    until = datetime.now() - timedelta(days=days)
    since = datetime.now() - timedelta(days=90)

    log.info(f"[unanswered] 搜尋收件匣 {since.date()} ~ {until.date()}（>= {days} 天前未回）")
    try:
        df_inbox = search_mail(
            folder="inbox",
            sender=sender_filter,
            since=since, until=until,
            limit=500,
            logger=log,
        )
    except Exception as e:
        return (False, "", f"search_mail(inbox) 失敗：{e.__class__.__name__}: {e}")

    if df_inbox.empty:
        return (True, f"（過去 90 天內、超過 {days} 天前的收件匣信件為空）", "")

    # 寄件備份：拉寬一點抓，涵蓋「晚回」的情況
    log.info(f"[unanswered] 搜尋寄件備份（抓到 ConversationID 比對）")
    try:
        df_sent = search_mail(
            folder="sent",
            since=since, until=datetime.now(),
            limit=2000,
            logger=log,
        )
    except Exception as e:
        log.warning(f"[unanswered] 寄件備份抓取失敗、改全部視為未回：{e}")
        df_sent = pd.DataFrame()

    sent_conv_ids: set[str] = set()
    if not df_sent.empty and "conversation_id" in df_sent.columns:
        sent_conv_ids = {str(c) for c in df_sent["conversation_id"].tolist() if c}

    if "conversation_id" not in df_inbox.columns:
        return (False, "", "search_mail 沒回 conversation_id 欄位（可能是舊版 helper，請更新）")

    df_unanswered = df_inbox[
        ~df_inbox["conversation_id"].astype(str).isin(sent_conv_ids)
    ].reset_index(drop=True)

    if df_unanswered.empty:
        return (True, f"（過去 90 天內、超過 {days} 天前的收件匣信件全都已回覆，無未回信件）", "")

    # 組 markdown 給 LLM
    lines = [f"## 共找到 {len(df_unanswered)} 封超過 {days} 天前還沒回的信"]
    if sender_filter:
        lines.append(f"\n（已套用寄件人過濾：{', '.join(sender_filter)}）")
    lines.append("")
    df_str = df_unanswered.astype(str).replace({"NaT": "", "nan": "", "None": ""})
    for i, row in df_str.iterrows():
        lines.append(f"### 信件 #{i + 1}：{row['subject']}")
        lines.append(f"- **寄件人**：{row['sender_name']} <{row['sender_email']}>")
        lines.append(f"- **收件時間**：{row['received']}")
        if row.get("has_attachments") == "True":
            lines.append(f"- **附件**：{row.get('attachment_names', '')}")
        body = row.get("body_text", "")
        if len(body) > 1500:
            body = body[:1500] + f"\n\n…（本文截斷，原長 {len(body)} 字）"
        lines.append(f"- **本文**：")
        lines.append("```")
        lines.append(body.replace("`", "'"))
        lines.append("```")
        lines.append("")
    return (True, "\n".join(lines), "")


# template_id → prefetch handler；LLM 模板可以選擇性註冊一個 prefetch
LLM_PREFETCH_HANDLERS = {
    "search_summary": _prefetch_search_summary,
    "unanswered": _prefetch_unanswered,
}


def has_prefetch(template: str) -> bool:
    return template in LLM_PREFETCH_HANDLERS


def run_prefetch(*, template: str, params: dict,
                 prev_outputs: Optional[list],
                 logger_obj: Optional[logging.Logger] = None) -> tuple[bool, str, str]:
    """執行 LLM 模板的預抓資料。

    回傳 (ok, prefetched_markdown, error_msg)：
      - ok=True：prefetched_markdown 是給 LLM prompt 用的資料字串
      - ok=False：error_msg 是失敗原因，caller 應 fallback 到 LLM 自己寫 code
    """
    handler = LLM_PREFETCH_HANDLERS.get(template)
    if handler is None:
        return (False, "", f"未註冊的 prefetch 模板：{template}")
    log = logger_obj or logger
    try:
        ok, md, err = handler(params or {}, prev_outputs, logger_obj=log)
        if ok:
            log.info(f"[prefetch/{template}] OK：{len(md)} 字資料已預抓")
        else:
            log.warning(f"[prefetch/{template}] failed：{err}")
        return (ok, md, err)
    except Exception as e:
        msg = f"prefetch 例外：{e.__class__.__name__}: {e}"
        log.error(f"[prefetch/{template}] {msg}", exc_info=True)
        return (False, "", msg)


def run_direct_template(*, template: str, params: dict, output_path: str,
                        prev_outputs: Optional[list], step_name: str,
                        logger_obj: Optional[logging.Logger] = None) -> tuple[bool, str]:
    """執行 direct 模板，回傳 (success, summary)。

    success=False 時 summary 是錯誤訊息（給使用者看的中文）。
    summary 也會包含輸出檔路徑等資訊。
    """
    handler = DIRECT_HANDLERS.get(template)
    if handler is None:
        return (False, f"未註冊的 direct 模板：{template}")

    log = logger_obj or logger
    out_path = Path(output_path).expanduser() if output_path else None
    if not out_path:
        return (False, "direct 模板需要 output_path（runner 應該已自動 default）")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        summary = handler(
            params=params or {},
            output_path=out_path,
            prev_outputs=prev_outputs,
            step_name=step_name,
            logger_obj=log,
        )
        log.info(f"[{step_name}] direct/{template} OK：{summary}")
        return (True, summary)
    except OutlookTemplateError as e:
        msg = f"模板「{template}」執行失敗：{e}"
        log.warning(f"[{step_name}] {msg}")
        return (False, msg)
    except Exception as e:
        msg = f"模板「{template}」遇到非預期錯誤：{e.__class__.__name__}: {e}"
        log.error(f"[{step_name}] {msg}", exc_info=True)
        return (False, msg)
