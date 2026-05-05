"""
Telegram Bot callback handler — 處理 inline keyboard 按鈕回調。

在後端啟動時以背景 task 運行，持續 polling Telegram 更新。
當收到 pipe_retry / pipe_hint / pipe_log / pipe_abort / pipe_continue 回調時，
呼叫 resume_pipeline() 繼續或中止 pipeline。

pipe_hint 流程：
1. 用戶點擊「💬 補充指示」按鈕
2. Bot 回覆「請輸入補充指示：」
3. 用戶發送文字訊息
4. Bot 呼叫 resume_pipeline(run_id, "retry_with_hint", hint=text)

── 多實例協調 ─────────────────────────────────────────────────────────────
Telegram Bot API 同一 token 同時間只允許一個 getUpdates long-poll session；
多個 backend 同時 poll 會收到 409 Conflict、callback 被亂搶、按鈕按了沒人回。
為避免這種情況，啟動前先用 PID lock 檢查：
  - Lock 路徑：%LOCALAPPDATA%/pipeline_orchestrator/telegram.lock（Windows）
              ~/.cache/pipeline_orchestrator/telegram.lock（Unix）
  - 內容：JSON {pid, project, started_at}
  - 若 lock 被另一個還活著的 process 持有 → 本實例跳過 polling，log 清楚說明
  - 持有 process 死掉（stale lock）→ 覆蓋接管
"""
import asyncio
import html
import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("telegram_handler")


def _lock_path() -> Path:
    """全機共用 lock 位置。Windows 用 %LOCALAPPDATA%，Unix 用 ~/.cache。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    d = base / "pipeline_orchestrator"
    d.mkdir(parents=True, exist_ok=True)
    return d / "telegram.lock"


def _pid_alive(pid: int) -> bool:
    """跨平台檢查 pid 是否真的還在跑（不靠 psutil）。
    Windows 坑：OpenProcess 對「已結束但 handle 還沒清完」的 process 也會成功，
    所以光靠 OpenProcess 會把 stale PID 誤判成 alive → lock 永遠釋不掉。
    改用 GetExitCodeProcess：exit_code == STILL_ACTIVE(259) 才算真活著。
    """
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED = 0x1000
            STILL_ACTIVE = 259
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if not h:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                if not ok:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _try_acquire_lock() -> bool:
    """嘗試拿下 telegram polling 的機器級 lock。
    回傳 True = 拿到、可以 poll；False = 別人還活著在 poll，本實例不 poll。
    """
    path = _lock_path()
    try:
        if path.exists():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            holder_pid = int(meta.get("pid", 0) or 0)
            holder_proj = meta.get("project", "unknown")
            if holder_pid and holder_pid != os.getpid() and _pid_alive(holder_pid):
                logger.warning(
                    f"Telegram polling 被另一實例持有 (pid={holder_pid}, project={holder_proj})。"
                    f" 本實例跳過 polling — Telegram 按鈕/截圖/補充指示將由該實例處理。"
                    f" 若要本實例處理，請先關閉 pid {holder_pid} 或刪掉 lock：{path}"
                )
                return False
        # 寫入自己的 meta 接管 lock
        meta = {
            "pid": os.getpid(),
            "project": _detect_project_tag(),
            "started_at": time.time(),
        }
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Telegram polling lock 取得：{path} (pid={os.getpid()})")
        return True
    except Exception as e:
        # lock 檔出問題別擋啟動、照常 poll；最差就退回舊行為
        logger.warning(f"Telegram lock 操作失敗（忽略、繼續 poll）：{e}")
        return True


def _release_lock() -> None:
    """停止 polling 時釋放 lock（只有自己持有才刪）。"""
    path = _lock_path()
    try:
        if not path.exists():
            return
        meta = json.loads(path.read_text(encoding="utf-8"))
        if int(meta.get("pid", 0) or 0) == os.getpid():
            path.unlink()
    except Exception:
        pass


def _detect_project_tag() -> str:
    """從 cwd 推個專案標籤寫進 lock，方便 debug 知道是誰持有。"""
    cwd = str(Path.cwd()).lower()
    for tag in ("pipeline-orchestratorv3", "pipeline-orchestratorv2", "pipeline-orchestratorv1"):
        if tag in cwd:
            return tag
    return "unknown"

# 等待用戶輸入補充指示的狀態：chat_id → run_id
_pending_hints: dict[int, str] = {}

# 等待用戶輸入 ask_user 自由回答的狀態：chat_id → run_id
_pending_answers: dict[int, str] = {}

# Polling loop 持有的 Bot 實例 — 升到 module scope 讓 _cmd_* 遠端遙控指令也能用
# （沒升 module scope 之前，_cmd_menu 引用會 NameError）
_bot_instance = None
_current_token = ""


def _i_still_hold_lock() -> bool:
    """檢查 lock file 裡的 pid 是不是自己。

    防護一個之前實際踩到的 race：
      - V4 backend 持續 polling 24h，但中途 V5 重啟、_pid_alive(V4) 偶發
        誤回 False（Windows OpenProcess 對長 uptime 程序有時會這樣）
      - V5 重新接管 lock，自己也開始 poll
      - V4 不知道自己已被接管，兩邊一起 poll → Telegram 回 409 Conflict

    解法：polling loop 每個 iteration 檢一次，若 lock 已不是自己 → 退出
    polling，把 token 讓給接管者。
    """
    path = _lock_path()
    try:
        if not path.exists():
            return False
        meta = json.loads(path.read_text(encoding="utf-8"))
        return int(meta.get("pid", 0) or 0) == os.getpid()
    except Exception:
        # 讀不到當作還持有，避免 lock 暫時不可讀就退出
        return True


# ── 遠端遙控指令處理（settings.telegram_remote_control=True 才生效）──────
def _is_remote_control_authorized(chat_id: int) -> bool:
    """檢查 chat_id 是否被授權使用遠端遙控指令。
    要求：(1) settings.telegram_remote_control=True (2) chat_id 等於授權 chat
    授權 chat 解析順序：settings.telegram_chat_id → .env TELEGRAM_CHAT_ID（與 runner._get_tg_chat_id 一致）
    任一不滿足都回 False（外人 DM 直接被忽略）"""
    try:
        from settings import get_settings
        s = get_settings()
        if not s.get("telegram_remote_control", False):
            return False
        auth_chat = (s.get("telegram_chat_id") or "").strip()
        if not auth_chat:
            auth_chat = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
        if not auth_chat:
            return False
        return str(chat_id) == auth_chat
    except Exception:
        return False


async def _handle_remote_command(chat_id: int, text: str) -> None:
    """處理 / 開頭的指令。"""
    logger = logging.getLogger("telegram")
    cmd = text.split()[0].lower() if text else ""
    args = text[len(cmd):].strip()
    logger.info(f"[遠端遙控] 收到指令 from chat {chat_id}: {cmd}")

    try:
        if cmd in ("/menu", "/list", "/start", "/選單"):
            await _cmd_menu(chat_id)
        elif cmd in ("/status", "/狀態"):
            await _cmd_status(chat_id)
        elif cmd in ("/help", "/幫助", "/?"):
            await _cmd_help(chat_id)
        elif cmd in ("/abort", "/中止"):
            await _cmd_abort(chat_id, args)
        elif cmd in ("/screenshot", "/截圖", "/screen"):
            await _cmd_screenshot(chat_id)
        else:
            await _bot_instance.send_message(
                chat_id=chat_id,
                text=f"❓ 不認識的指令：{cmd}\n打 /help 看可用指令。",
            )
    except Exception as e:
        logger.error(f"[遠端遙控] 指令 {cmd} 處理失敗：{e}", exc_info=True)
        try:
            await _bot_instance.send_message(
                chat_id=chat_id,
                text=f"❌ 指令處理失敗：{str(e)[:200]}",
            )
        except Exception:
            pass


def _format_next_run(iso_str) -> str:
    """ISO datetime → 'MM/DD HH:MM'。失敗回空字串。"""
    if not iso_str:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return ""


def _build_menu_metadata(workflows: list[dict]) -> dict[str, dict]:
    """為 /menu 蒐集每個工作流的狀態徽章資料。
    回傳 {workflow_id: {"running": PipelineRun|None, "schedule": task_dict|None}}
    """
    meta: dict[str, dict] = {wf["id"]: {"running": None, "schedule": None} for wf in workflows}

    # 執行中的 run（用 workflow_id 對應；store 已有此欄）
    try:
        from pipeline.store import get_store
        for r in get_store().list_recent(limit=30):
            wid = getattr(r, "workflow_id", None)
            if (
                r.status in ("running", "awaiting_human")
                and wid
                and wid in meta
                and meta[wid]["running"] is None
            ):
                meta[wid]["running"] = r
    except Exception:
        pass

    # 排程：scheduler 的 task name 對應 workflow name（既有的弱關聯，名字改了會脫鉤）
    try:
        from scheduler.manager import list_tasks
        name_to_wid: dict[str, str] = {}
        for wf in workflows:
            n = wf.get("name") or ""
            if n and n not in name_to_wid:
                name_to_wid[n] = wf["id"]
        for t in list_tasks():
            if t.get("output_format") != "pipeline":
                continue
            wid = name_to_wid.get(t.get("name", ""))
            if wid and wid in meta and meta[wid]["schedule"] is None:
                meta[wid]["schedule"] = t
    except Exception:
        pass

    return meta


async def _cmd_menu(chat_id: int) -> None:
    """列出工作流，附「啟動」按鈕 + 執行中／排程徽章。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import db
    workflows = db.list_workflows()
    if not workflows:
        await _bot_instance.send_message(
            chat_id=chat_id,
            text="📭 沒有任何工作流。請先在前端建立。",
        )
        return

    wf_meta = _build_menu_metadata(workflows)
    rows = []
    # callback_data 上限 64 bytes（wf_id 短不會爆）
    # button label 上限約 256 字元，徽章塞在文字裡夠用
    for wf in workflows[:25]:
        wf_id = wf.get("id", "")
        name = (wf.get("name") or wf_id)[:30]
        info = wf_meta.get(wf_id, {})
        badges: list[str] = []
        running = info.get("running")
        if running is not None:
            badges.append("🔄 執行中" if running.status == "running" else "⏸ 等待人工")
        sched = info.get("schedule")
        if sched is not None:
            nxt = _format_next_run(sched.get("next_run"))
            badges.append(f"📅 {nxt}" if nxt else "📅 排程中")
        label = f"▶ {name}"
        if badges:
            label += "  " + " ".join(badges)
        rows.append([InlineKeyboardButton(label[:80], callback_data=f"pipe_start_wf:{wf_id}")])

    extra_note = ""
    if len(workflows) > 25:
        extra_note = f"\n\n（還有 {len(workflows) - 25} 個未列出，請進前端 UI 啟動）"

    await _bot_instance.send_message(
        chat_id=chat_id,
        text=(
            f"📋 <b>選擇要啟動的工作流</b>（共 {len(workflows)} 個）{extra_note}\n"
            f"<i>🔄 = 執行中　📅 = 已排程（下次執行時間）</i>"
        ),
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def _cmd_status(chat_id: int) -> None:
    """列出執行中的 run。"""
    from pipeline.store import get_store
    runs = get_store().list_recent(limit=20)
    active = [r for r in runs if r.status in ("running", "awaiting_human")]
    if not active:
        await _bot_instance.send_message(
            chat_id=chat_id, text="🟢 目前沒有執行中的 run。",
        )
        return
    lines = ["📊 <b>執行中的 Run</b>", ""]
    for r in active:
        emoji = "🔄" if r.status == "running" else "⏸"
        try:
            steps = r.config_dict.get("steps") or []
            total = len(steps)
        except Exception:
            total = "?"
        lines.append(f"{emoji} <code>{r.run_id}</code> — {r.pipeline_name}")
        lines.append(f"  步驟 {r.current_step + 1} / {total}")
        if r.status == "awaiting_human" and r.awaiting_message:
            lines.append(f"  等待中：{r.awaiting_message[:80]}")
        lines.append("")
    await _bot_instance.send_message(
        chat_id=chat_id, text="\n".join(lines), parse_mode="HTML",
    )


async def _cmd_help(chat_id: int) -> None:
    text = (
        "📖 <b>Telegram 遠端遙控指令</b>\n\n"
        "<code>/menu</code> — 列出工作流（點按鈕啟動）\n"
        "<code>/status</code> — 查看執行中的 run\n"
        "<code>/screenshot</code> — 抓 host 桌面即時截圖（看畫面決定要不要開工作流）\n"
        "<code>/abort &lt;run_id&gt;</code> — 中止某個 run\n"
        "<code>/help</code> — 顯示這份說明\n\n"
        "啟動工作流後進度會自動推送到此對話。"
    )
    await _bot_instance.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


async def _cmd_screenshot(chat_id: int) -> None:
    """/screenshot — 隨時抓 host 桌面截圖、傳到 TG。
    1 螢幕 1 張、N 螢幕 N 張。委託 runner.take_screenshots + _tg_send_photos。"""
    logger = logging.getLogger("telegram")
    try:
        await _bot_instance.send_message(chat_id=chat_id, text="📸 正在截圖…")
        from pipeline.runner import take_screenshots, _tg_send_photos
        # pipeline_name 給個固定夾名 _remote_screenshots(避免污染工作流目錄)
        # step_name 用時間戳區分 — 多次按 /screenshot 不會互相覆蓋
        import time as _t
        ss_paths = take_screenshots("_remote_screenshots", f"tg_{_t.strftime('%H%M%S')}")
        if ss_paths:
            await _tg_send_photos(
                chat_id,
                ss_paths,
                caption_prefix="📸 桌面即時截圖",
            )
        else:
            await _bot_instance.send_message(
                chat_id=chat_id,
                text="❌ 截圖失敗（host 主機可能無圖形介面、或 mss 套件出錯）",
            )
    except Exception as e:
        logger.error(f"[/screenshot] 失敗：{e}", exc_info=True)
        try:
            await _bot_instance.send_message(
                chat_id=chat_id, text=f"❌ 截圖失敗：{str(e)[:200]}",
            )
        except Exception:
            pass


async def _cmd_abort(chat_id: int, args: str) -> None:
    run_id = (args or "").strip()
    if not run_id:
        await _bot_instance.send_message(
            chat_id=chat_id, text="⚠ 請帶 run_id：/abort &lt;run_id&gt;",
            parse_mode="HTML",
        )
        return
    try:
        from pipeline.runner import request_abort
        request_abort(run_id)
        await _bot_instance.send_message(
            chat_id=chat_id, text=f"🛑 已要求中止 run <code>{run_id}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await _bot_instance.send_message(
            chat_id=chat_id, text=f"❌ 中止失敗：{str(e)[:200]}",
        )


async def _register_bot_commands(bot) -> None:
    """寫入 TG 客戶端 autocomplete 清單（取代 BotFather 既有設定）。
    這是 bot-global 設定，會覆蓋此 bot token 在其他專案註冊過的 /commands、
    /skill、/approve 等舊項目。Token 變更才呼叫一次，不需常駐刷新。
    """
    from telegram import BotCommand
    try:
        await bot.set_my_commands([
            BotCommand("menu",       "列工作流並啟動（附執行中／排程徽章）"),
            BotCommand("status",     "查看目前執行中的 run"),
            BotCommand("screenshot", "抓 host 桌面即時截圖"),
            BotCommand("abort",      "中止某個 run（用法：/abort <run_id>）"),
            BotCommand("help",   "顯示指令說明"),
        ])
        logger.info("Telegram bot 指令清單（autocomplete）已更新為 V5 版本")
    except Exception as e:
        # set_my_commands 失敗不影響功能（autocomplete 只是視覺提示），記 log 就好
        logger.warning(f"set_my_commands 失敗（忽略，不影響功能）：{e}")


async def _start_workflow_from_tg(chat_id: int, wf_id: str, force: bool = False) -> None:
    """從 TG 啟動工作流。把 chat_id 帶進 run、後續通知會推回此對話。
    force=False 時若同 workflow 已有 running/awaiting 的 run，會先回警告 + 強制啟動按鈕。
    """
    logger = logging.getLogger("telegram")
    import db
    wf = db.get_workflow(wf_id)
    if not wf:
        await _bot_instance.send_message(
            chat_id=chat_id, text=f"❌ 找不到工作流：{wf_id}",
        )
        return
    yaml_content = (wf.get("yaml") or "").strip()
    if not yaml_content:
        await _bot_instance.send_message(
            chat_id=chat_id,
            text=("⚠ 此工作流的 YAML 為空，無法直接啟動。"
                  "請先在前端開啟並儲存一次（觸發 yaml 自動產生）後再試。"),
        )
        return

    # ── 重複啟動守門：同 workflow 已在執行 → 先警告，需用戶確認才強跑 ──
    if not force:
        try:
            from pipeline.store import get_store
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            for r in get_store().list_recent(limit=30):
                if (
                    getattr(r, "workflow_id", None) == wf_id
                    and r.status in ("running", "awaiting_human")
                ):
                    status_label = "🔄 執行中" if r.status == "running" else "⏸ 等待人工確認"
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "⚠ 仍要強制啟動（會跑兩個 run）",
                            callback_data=f"pipe_force_start_wf:{wf_id}",
                        )],
                        [InlineKeyboardButton("取消", callback_data="pipe_cancel_select")],
                    ])
                    await _bot_instance.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⚠ 此工作流已有執行中的 run，先確認再操作：\n\n"
                            f"  📛 名稱：<b>{wf.get('name') or wf_id}</b>\n"
                            f"  🆔 run_id：<code>{r.run_id}</code>\n"
                            f"  📍 狀態：{status_label}\n\n"
                            f"建議：\n"
                            f"  • <code>/status</code> 看詳細進度\n"
                            f"  • <code>/abort {r.run_id}</code> 中止舊 run\n\n"
                            f"若確定要再開一個（會兩個 run 平行跑），點下方按鈕。"
                        ),
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                    return
        except Exception as e:
            logger.warning(f"重複 run 檢查失敗（忽略繼續啟動）：{e}")

    try:
        import uuid
        import asyncio
        import yaml as yaml_lib
        from pipeline.models import PipelineConfig
        from pipeline.runner import run_pipeline, register_task
        from pipeline.store import PipelineRun as PRun, get_store
        from pipeline.logger import create_run_logger

        data = yaml_lib.safe_load(yaml_content) or {}
        config_dict = data.get("pipeline", data)
        config_dict["validate"] = True
        config = PipelineConfig(**config_dict)

        run_id = str(uuid.uuid4())[:12]
        _, log_path = create_run_logger(run_id, config.name)
        config_d = config.model_dump()
        config_d["_workflow_id"] = wf_id

        run = PRun(
            run_id=run_id,
            pipeline_name=config.name,
            config_dict=config_d,
            telegram_chat_id=chat_id,  # 後續通知推回此對話
            log_path=log_path,
            workflow_id=wf_id,
        )
        get_store().save(run)
        task = asyncio.create_task(run_pipeline(config_d, chat_id=chat_id, run_id=run_id))
        register_task(run_id, task)

        await _bot_instance.send_message(
            chat_id=chat_id,
            text=(f"🚀 已啟動工作流：<b>{config.name}</b>\n"
                  f"run_id: <code>{run_id}</code>\n\n"
                  f"後續進度會自動推送到此對話。"),
            parse_mode="HTML",
        )
        logger.info(f"[遠端遙控] 從 TG 啟動 run {run_id}（{config.name}）")
    except Exception as e:
        logger.error(f"[遠端遙控] 啟動失敗：{e}", exc_info=True)
        await _bot_instance.send_message(
            chat_id=chat_id, text=f"❌ 啟動失敗：{str(e)[:300]}",
        )


async def _poll_loop():
    """長輪詢 Telegram updates，處理 callback_query 和文字訊息"""
    from telegram import Bot
    from telegram.error import RetryAfter, TimedOut, NetworkError, Conflict
    global _bot_instance, _current_token

    last_offset = 0
    _bot_instance = None
    _current_token = ""

    while True:
        # 每個 iteration 檢 lock 還是不是自己；不是的話退出
        # （另一實例已接管 polling 的話，本實例該停手）
        if not _i_still_hold_lock():
            logger.warning(
                "Telegram polling lock 已被另一實例接管，本實例退出 polling"
                " 避免兩邊一起 poll 同 token 造成 409 Conflict。"
            )
            return

        try:
            from settings import get_settings
            s = get_settings()
            token = s.get("telegram_bot_token", "")
            # Fallback 順序：pipeline_settings.json → .env TELEGRAM_BOT_TOKEN
            # 後端 outbound 通知是讀 env var，有些人只設 env 沒存到 settings UI，
            # polling loop 若只讀 settings 會永遠 sleep 導致 callback 收不到
            if not token:
                token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            if not token:
                await asyncio.sleep(15)
                continue

            # token 變更時重建 bot
            if token != _current_token:
                if _bot_instance:
                    try:
                        await _bot_instance.close()
                    except Exception:
                        pass
                _bot_instance = Bot(token=token)
                _current_token = token
                last_offset = 0  # 重置 offset
                # 清除舊 session，避免 Conflict
                try:
                    await _bot_instance.delete_webhook(drop_pending_updates=False)
                    # 短 timeout getUpdates 搶佔 session
                    stale = await _bot_instance.get_updates(timeout=1)
                    if stale:
                        last_offset = stale[-1].update_id + 1
                except Exception:
                    pass
                logger.info("Telegram bot 已連線（session 已重置）")
                # 把 autocomplete 指令清單覆寫成 V5 版（覆蓋舊專案殘留的 /skill、/approve 等）
                await _register_bot_commands(_bot_instance)

            updates = await _bot_instance.get_updates(
                offset=last_offset,
                timeout=30,
                allowed_updates=["callback_query", "message"],
            )

            for update in updates:
                last_offset = update.update_id + 1

                # ── 文字訊息：檢查是否有等待中的補充指示或 ask_user 答案 ──
                if update.message and update.message.text:
                    chat_id = update.message.chat_id
                    if chat_id in _pending_answers:
                        run_id = _pending_answers.pop(chat_id)
                        answer = update.message.text.strip()
                        logger.info(f"收到 ask_user 答案 for run {run_id}: {answer[:100]}")
                        try:
                            from pipeline.runner import resume_pipeline
                            msg = await resume_pipeline(run_id, "answer", hint=answer)
                            await _bot_instance.send_message(
                                chat_id=chat_id,
                                text=f"✅ {msg}",
                            )
                        except Exception as e:
                            logger.error(f"ask_user answer failed: {e}")
                            await _bot_instance.send_message(
                                chat_id=chat_id,
                                text=f"❌ 送出失敗：{str(e)[:200]}",
                            )
                        continue
                    if chat_id in _pending_hints:
                        run_id = _pending_hints.pop(chat_id)
                        hint_text = update.message.text.strip()
                        logger.info(f"收到補充指示 for run {run_id}: {hint_text[:100]}")
                        try:
                            from pipeline.runner import resume_pipeline
                            msg = await resume_pipeline(run_id, "retry_with_hint", hint=hint_text)
                            await _bot_instance.send_message(
                                chat_id=chat_id,
                                text=f"💬 已收到指示，正在重試…\n\n{msg}",
                            )
                        except Exception as e:
                            logger.error(f"Hint resume failed: {e}")
                            await _bot_instance.send_message(
                                chat_id=chat_id,
                                text=f"❌ 重試失敗：{str(e)[:200]}",
                            )
                        continue
                    # ── 遠端遙控指令（必須 settings 開啟 + chat_id 授權）──
                    text = update.message.text.strip()
                    if text.startswith("/"):
                        if _is_remote_control_authorized(chat_id):
                            await _handle_remote_command(chat_id, text)
                        else:
                            # 不授權的 /command 也記一筆 — 方便 debug 為何 /menu 沒反應
                            try:
                                from settings import get_settings as _gs
                                _s = _gs()
                                _enabled = bool(_s.get("telegram_remote_control", False))
                                _auth = (_s.get("telegram_chat_id") or "").strip() or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
                                logger.warning(
                                    f"[遠端遙控] 拒絕 {text} from chat_id={chat_id}："
                                    f"toggle={_enabled}, auth_chat={_auth!r}, match={str(chat_id) == _auth}"
                                )
                            except Exception:
                                pass
                    continue

                if not update.callback_query:
                    continue

                cb = update.callback_query
                data = cb.data or ""

                # 解析 callback_data: pipe_{action}:{run_id} 或 pipe_answer:{run_id}:{idx}
                if not data.startswith("pipe_"):
                    continue

                parts = data.split(":", 2)
                if len(parts) < 2:
                    continue

                action = parts[0].replace("pipe_", "")
                run_id = parts[1]
                extra = parts[2] if len(parts) >= 3 else ""

                # ── 查看 Log ──
                if action == "log":
                    logger.info(f"Telegram: 查看 log for run {run_id}")
                    try:
                        from pipeline.runner import get_run_log_tail
                        log_text = get_run_log_tail(run_id, lines=25)
                        # Telegram 訊息上限 4096 字元
                        if len(log_text) > 3800:
                            log_text = "…（前面省略）\n" + log_text[-3800:]
                        safe_log = html.escape(log_text)
                        await cb.answer("📋 Log 已發送")
                        await _bot_instance.send_message(
                            chat_id=cb.message.chat_id,
                            text=f"📋 <b>Pipeline Log（最近 25 行）</b>\n\n<pre>{safe_log}</pre>",
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        await cb.answer(f"❌ {str(e)[:150]}")
                    continue

                # ── 截圖 ── 逐螢幕截（1 螢幕 1 張、N 螢幕 N 張）
                # 委託給 runner._tg_send_photos，行為跟自動截圖一致，並含 photo→document fallback
                # （4K 螢幕 PNG 常 >5MB、send_photo 會被拒；send_document 不受尺寸/壓縮限制）
                if action == "screenshot":
                    logger.info(f"Telegram: 截圖 for run {run_id}")
                    try:
                        from pipeline.store import get_store
                        from pipeline.runner import take_screenshots, _tg_send_photos
                        store = get_store()
                        run = store.load(run_id)
                        if not run:
                            await cb.answer("❌ 找不到此 run")
                            continue
                        steps = run.config_dict.get("steps", [])
                        step_idx = run.current_step
                        step_name = steps[step_idx]["name"] if step_idx < len(steps) else "unknown"
                        await cb.answer("📸 正在截圖…")
                        ss_paths = take_screenshots(run.pipeline_name, step_name)
                        if ss_paths:
                            await _tg_send_photos(
                                cb.message.chat_id,
                                ss_paths,
                                caption_prefix=f"📸 {run.pipeline_name} / {step_name}",
                            )
                        else:
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text="❌ 截圖失敗，請確認後端主機是否有螢幕",
                            )
                    except Exception as e:
                        logger.error(f"Screenshot failed: {e}")
                        try:
                            await cb.answer(f"❌ {str(e)[:150]}")
                        except Exception:
                            pass
                    continue

                # ── HQ 預覽：使用者按「🎨 原版式預覽」→ LibreOffice 轉 PDF → render ──
                # B1 的 docx/pptx 只抽文字，版式看不到；此按鈕用 LibreOffice 轉出真版式
                # 時間開銷 5-10s / 檔案，所以做成按鈕觸發、不自動跑
                if action == "preview_hq":
                    logger.info(f"Telegram: 原版式預覽 for run {run_id}")
                    try:
                        from pipeline.store import get_store
                        from pipeline.models import PipelineConfig
                        from pipeline.runner import _find_prev_output_file
                        from pipeline.file_preview import _render_via_libreoffice, _libreoffice_binary
                        store = get_store()
                        run = store.load(run_id)
                        if not run:
                            await cb.answer("❌ 找不到此 run")
                            continue
                        if not _libreoffice_binary():
                            await cb.answer("⚠️ 未安裝 LibreOffice")
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text=(
                                    "❌ 原版式預覽需要 LibreOffice，但本機未安裝。\n"
                                    "下載：https://libreoffice.org（免費，~500MB）\n"
                                    "裝完不用改任何設定，系統會自動偵測。"
                                ),
                            )
                            continue
                        config = PipelineConfig.from_dict(run.config_dict)
                        prev_file = _find_prev_output_file(run, config)
                        if not prev_file:
                            await cb.answer("⚠️ 找不到上一步輸出檔")
                            continue
                        await cb.answer("🎨 LibreOffice 轉檔中，約 5-10 秒…")
                        # 在 executor 跑（轉檔 CPU 重，避免 block poll loop）
                        import asyncio as _a
                        from pathlib import Path as _P
                        preview_paths = await _a.get_event_loop().run_in_executor(
                            None,
                            lambda fp=prev_file: _render_via_libreoffice(_P(fp), _P(fp).parent),
                        )
                        if preview_paths:
                            from pipeline.runner import _tg_send_photos
                            await _tg_send_photos(
                                cb.message.chat_id,
                                preview_paths,
                                caption_prefix=f"🎨 原版式預覽：{_P(prev_file).name}",
                            )
                        else:
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text="❌ LibreOffice 轉檔後沒有產生可預覽的頁面",
                            )
                    except Exception as e:
                        logger.error(f"preview_hq failed: {e}")
                        try:
                            await cb.answer(f"❌ {str(e)[:150]}")
                        except Exception:
                            pass
                        try:
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text=f"❌ 原版式預覽失敗：{str(e)[:300]}",
                            )
                        except Exception:
                            pass
                    continue

                # ── 取上一步輸出檔（pipe_prev_output）──
                # 人工確認 keyboard 的「📎 上一步輸出」按鈕；不論 send_prev_output 是否開都可用
                if action == "prev_output":
                    logger.info(f"Telegram: 取上一步輸出 for run {run_id}")
                    try:
                        from pipeline.store import get_store
                        from pipeline.models import PipelineConfig
                        from pipeline.runner import _send_step_output_to_tg
                        store = get_store()
                        run = store.load(run_id)
                        if not run:
                            await cb.answer("❌ 找不到此 run")
                            continue
                        config = PipelineConfig.from_dict(run.config_dict)
                        # 跳過連續 human_confirm 找上一個可執行步驟（跟 auto-send 邏輯一致）
                        idx = run.current_step - 1
                        while idx >= 0 and config.steps[idx].human_confirm:
                            idx -= 1
                        if idx < 0:
                            await cb.answer("⚠ 沒有上一步")
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text="⚠ 此節點是第一步、沒有上一步輸出可取",
                            )
                            continue
                        prev_step = config.steps[idx]
                        # 從 run.step_results 找對應的 StepResult、給 actual_output_path
                        prev_result = next((sr for sr in run.step_results if sr.step_index == idx), None)
                        await cb.answer("📎 取得中…")
                        ok, msg = await _send_step_output_to_tg(
                            cb.message.chat_id, prev_step,
                            step_label=f"步驟 {idx+1}：{prev_step.name}",
                            workflow_name=config.name,
                            logger=logger,
                            step_result=prev_result,
                        )
                        if not ok:
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id, text=f"⚠ {msg}",
                            )
                    except Exception as e:
                        logger.error(f"prev_output failed: {e}")
                        try: await cb.answer(f"❌ {str(e)[:150]}")
                        except Exception: pass
                    continue

                # ── 列出所有步驟讓使用者挑要取哪一步輸出（pipe_select_step）──
                # 點下去 bot 回一個新訊息、含每步的按鈕；按按鈕觸發 pipe_step_output:{run_id}:{idx}
                if action == "select_step":
                    logger.info(f"Telegram: 列出步驟選單 for run {run_id}")
                    try:
                        from pipeline.store import get_store
                        from pipeline.models import PipelineConfig
                        from pipeline.runner import _resolve_step_output_for_tg
                        store = get_store()
                        run = store.load(run_id)
                        if not run:
                            await cb.answer("❌ 找不到此 run")
                            continue
                        config = PipelineConfig.from_dict(run.config_dict)
                        # 列「可能有輸出」的步驟：
                        #   - 明確設 output.path（任何節點類型）
                        #   - 節點類型有 default rule（outlook / web_crawler）
                        #   - skill_mode / 一般 script（會寫檔到 working_dir）
                        # 排除：human_confirm / visual_validation / computer_use（不寫檔）
                        from pipeline.runner import _step_default_output_path
                        # 預先把 step_results 做成 idx → StepResult map，給每步解析時用
                        sr_by_idx = {sr.step_index: sr for sr in run.step_results}
                        listed: list[tuple[int, str, str]] = []  # (idx, label, status_emoji)
                        for i, st in enumerate(config.steps):
                            has_explicit = bool(st.output and st.output.path)
                            has_default = bool(_step_default_output_path(st, config.name))
                            could_produce = bool(
                                getattr(st, "skill_mode", False)
                                or (not getattr(st, "human_confirm", False)
                                    and not getattr(st, "visual_validation", False)
                                    and not getattr(st, "computer_use", False)
                                    and getattr(st, "batch", ""))
                            )
                            sr_i = sr_by_idx.get(i)
                            has_actual = bool(getattr(sr_i, "actual_output_path", "") if sr_i else "")
                            if not has_explicit and not has_default and not could_produce and not has_actual:
                                continue  # 該節點本就沒輸出概念
                            fp, _disp, err = _resolve_step_output_for_tg(
                                st, workflow_name=config.name, logger=logger,
                                step_result=sr_i,
                            )
                            emoji = "✅" if fp else "⚠"
                            label = f"{emoji} {i+1}. {st.name}"
                            # callback_data 上限 64 bytes、保險裁短 label
                            if len(label) > 50:
                                label = label[:47] + "…"
                            listed.append((i, label, emoji))

                        if not listed:
                            await cb.answer("⚠ 沒有任何步驟有可傳的輸出")
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id,
                                text="⚠ 此工作流沒有任何步驟有可傳的輸出檔（檢查 step.output.path）",
                            )
                            continue

                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                        rows = [[InlineKeyboardButton(label, callback_data=f"pipe_step_output:{run_id}:{i}")]
                                for i, label, _ in listed]
                        rows.append([InlineKeyboardButton("✕ 取消", callback_data=f"pipe_cancel_select:{run_id}")])
                        await cb.answer()
                        await _bot_instance.send_message(
                            chat_id=cb.message.chat_id,
                            text=("📂 <b>選擇要取得哪一步的輸出</b>\n\n"
                                  "✅ = 檔案準備好可傳；⚠ = 設了 output.path 但檔案不存在 / 太大"),
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(rows),
                        )
                    except Exception as e:
                        logger.error(f"select_step failed: {e}")
                        try: await cb.answer(f"❌ {str(e)[:150]}")
                        except Exception: pass
                    continue

                # ── 使用者從步驟選單挑了某步、傳該步輸出（pipe_step_output）──
                if action == "step_output":
                    try:
                        target_idx = int(extra) if extra else -1
                    except Exception:
                        await cb.answer("❌ 步驟索引錯誤")
                        continue
                    logger.info(f"Telegram: 取步驟 #{target_idx} 輸出 for run {run_id}")
                    try:
                        from pipeline.store import get_store
                        from pipeline.models import PipelineConfig
                        from pipeline.runner import _send_step_output_to_tg
                        store = get_store()
                        run = store.load(run_id)
                        if not run:
                            await cb.answer("❌ 找不到此 run")
                            continue
                        config = PipelineConfig.from_dict(run.config_dict)
                        if target_idx < 0 or target_idx >= len(config.steps):
                            await cb.answer("❌ 步驟索引超出範圍")
                            continue
                        st = config.steps[target_idx]
                        sr_target = next((sr for sr in run.step_results if sr.step_index == target_idx), None)
                        await cb.answer("📎 取得中…")
                        ok, msg = await _send_step_output_to_tg(
                            cb.message.chat_id, st,
                            step_label=f"步驟 {target_idx+1}：{st.name}",
                            workflow_name=config.name,
                            logger=logger,
                            step_result=sr_target,
                        )
                        if not ok:
                            await _bot_instance.send_message(
                                chat_id=cb.message.chat_id, text=f"⚠ {msg}",
                            )
                    except Exception as e:
                        logger.error(f"step_output failed: {e}")
                        try: await cb.answer(f"❌ {str(e)[:150]}")
                        except Exception: pass
                    continue

                # ── 取消選擇步驟（pipe_cancel_select）──
                if action == "cancel_select":
                    try:
                        await cb.answer()
                        # 把選單訊息刪掉、避免殘留
                        await cb.message.delete()
                    except Exception:
                        pass
                    continue

                # ── 遠端遙控：從 TG 啟動工作流 ──
                if action in ("start_wf", "force_start_wf"):
                    # 必須通過授權檢查
                    if not _is_remote_control_authorized(cb.message.chat_id):
                        await cb.answer("❌ 未授權")
                        continue
                    wf_id = run_id  # callback_data parsed slot 用來放 wf_id
                    if not wf_id:
                        await cb.answer("❌ 無效的工作流 ID")
                        continue
                    force = (action == "force_start_wf")
                    try:
                        await cb.answer("⏳ 啟動中…" if force else "⏳ 檢查中…")
                        # 強制啟動時把警告訊息刪掉，避免畫面留警告
                        if force:
                            try:
                                await cb.message.delete()
                            except Exception:
                                pass
                        await _start_workflow_from_tg(cb.message.chat_id, wf_id, force=force)
                    except Exception as e:
                        logger.error(f"{action} failed: {e}", exc_info=True)
                        try:
                            await cb.answer(f"❌ {str(e)[:50]}")
                        except Exception:
                            pass
                    continue

                # ── ask_user 按選項回答 ──
                if action == "answer":
                    # extra 是 option index
                    try:
                        opt_idx = int(extra)
                    except Exception:
                        await cb.answer("❌ 選項索引錯誤")
                        continue
                    # 從 run 狀態取出原 options
                    from pipeline.store import get_store
                    import json as _json
                    store = get_store()
                    run = store.load(run_id)
                    if not run or run.awaiting_type != "ask_user":
                        await cb.answer("⚠️ 已非等待狀態")
                        continue
                    try:
                        meta = _json.loads(run.awaiting_suggestion or "{}")
                        options = meta.get("options") or []
                    except Exception:
                        options = []
                    if opt_idx < 0 or opt_idx >= len(options):
                        await cb.answer("❌ 選項索引越界")
                        continue
                    chosen = str(options[opt_idx])
                    logger.info(f"Telegram: ask_user 選項 {chosen} for run {run_id}")
                    try:
                        from pipeline.runner import resume_pipeline
                        msg = await resume_pipeline(run_id, "answer", hint=chosen)
                        await cb.answer(f"已選：{chosen[:50]}")
                        try:
                            await cb.edit_message_text(
                                text=(cb.message.text or "") + f"\n\n✅ 已選擇：{chosen}",
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        await cb.answer(f"❌ {str(e)[:150]}")
                    continue

                # ── ask_user 自由輸入：設定等待狀態，改走文字訊息 ──
                if action == "answer_free":
                    logger.info(f"Telegram: 等待 ask_user 自由輸入 for run {run_id}")
                    _pending_answers[cb.message.chat_id] = run_id
                    await cb.answer("請輸入答案")
                    await _bot_instance.send_message(
                        chat_id=cb.message.chat_id,
                        text=(
                            "✍ <b>請輸入你的答案</b>\n\n"
                            "直接回覆文字訊息即可。AI 會根據你的回答繼續任務。"
                        ),
                        parse_mode="HTML",
                    )
                    continue

                # ── 補充指示：設定等待狀態 ──
                if action == "hint":
                    logger.info(f"Telegram: 等待補充指示 for run {run_id}")
                    _pending_hints[cb.message.chat_id] = run_id
                    await cb.answer("請輸入補充指示")
                    await _bot_instance.send_message(
                        chat_id=cb.message.chat_id,
                        text=(
                            "💬 <b>請輸入補充指示</b>\n\n"
                            "AI 會根據你的指示重新嘗試此步驟。\n"
                            "例如：「改用 selenium」「檢查 CSS selector 是否正確」「用另一個 API」"
                        ),
                        parse_mode="HTML",
                    )
                    continue

                if action not in ("retry", "skip", "abort", "continue"):
                    await cb.answer("❓ 未知操作")
                    continue

                logger.info(f"Telegram callback: {action} for run {run_id}")

                try:
                    from pipeline.runner import resume_pipeline
                    msg = await resume_pipeline(run_id, action)
                    await cb.answer(msg[:200])
                    # 更新原訊息，標記已處理
                    action_labels = {
                        "retry": "🔄 已選擇重試",
                        "skip": "⏩ 已選擇跳過",
                        "abort": "🛑 已選擇中止",
                        "continue": "✅ 已確認繼續",
                    }
                    try:
                        original_text = cb.message.text or ""
                        await cb.edit_message_text(
                            text=original_text + f"\n\n{action_labels.get(action, action)}",
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"Resume failed: {e}")
                    try:
                        await cb.answer(f"❌ {str(e)[:150]}")
                    except Exception:
                        pass

        except asyncio.CancelledError:
            logger.info("Telegram polling stopped")
            if _bot_instance:
                try:
                    await _bot_instance.close()
                except Exception:
                    pass
            break
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"Telegram flood control, waiting {wait}s")
            await asyncio.sleep(wait)
        except Conflict:
            # 409 Conflict = 有別人（同機別 backend / 別台機器的 bot）在 poll 同一 token。
            # 不再悶頭重試：大聲 log、等久一點（30s），避免跟對方亂搶亂吃 callback。
            logger.warning(
                "Telegram 409 Conflict — 另一個 bot 實例正在 poll 同一 token。"
                " 這代表有別的 backend（本機或其他機器）在用同一個 token，"
                " 會造成按鈕 callback 被亂搶。請確認只開一個 backend，或為每個版本用不同 bot token。"
            )
            await asyncio.sleep(30)
        except (TimedOut, NetworkError):
            # 正常的 long-poll 超時或網路問題
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"Telegram poll error: {e}")
            await asyncio.sleep(10)


_poll_task = None


async def start_polling():
    """啟動 Telegram callback polling（背景 task）
    啟動前先試著拿機器級 lock；拿不到代表已有實例在 poll，本實例就不啟 task
    （避免同機多 backend 互搶 Telegram getUpdates session）。
    """
    global _poll_task
    if _poll_task and not _poll_task.done():
        return
    if not _try_acquire_lock():
        return  # 另一實例持有 — 本實例只做 outbound 通知
    _poll_task = asyncio.create_task(_poll_loop())
    logger.info("Telegram callback polling 已啟動")


async def stop_polling():
    """停止 polling"""
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = None
    _release_lock()
