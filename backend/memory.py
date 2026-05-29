"""AI 助手長期記憶 — 階段1:Semantic facts(語意記憶 / 偏好)。

設計參考 project-to-agent phase14-memory,針對本專案調整:
- 全域單使用者(user_id 預設 'local';保留欄位,未來多人可擴)
- category 針對「工作流助手」場景:workflow_pref / domain / past_decision / vocabulary / fact
- sqlite 單檔,放 OUTPUT_BASE_PATH/agent_memory/memory.db
- 敏感資料 hard deny-list(API key / 密碼等拒記)
- 階段2 再加 episodes(對話摘要 + 向量/關鍵字檢索)+ 保守自動萃取

記憶寫入走 chat_tools 的兩步確認(confirm),本模組只負責 storage。
"""
from __future__ import annotations
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from config import OUTPUT_BASE_PATH

_DB_PATH = OUTPUT_BASE_PATH / "agent_memory" / "memory.db"
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

# 合法 category(工作流助手場景)
CATEGORIES = ("workflow_pref", "domain", "past_decision", "vocabulary", "fact", "preference")

# 敏感資料 hard deny-list:value 命中就拒記(避免 agent 偷記 key / 密碼)
_SENSITIVE = [
    re.compile(r"sk-[a-zA-Z0-9]{16,}"),          # OpenAI-style key
    re.compile(r"AIza[a-zA-Z0-9_\-]{30,}"),       # Google API key
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),          # Groq key
    re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),    # Anthropic key
    re.compile(r"bearer\s+[a-zA-Z0-9._\-]{12,}", re.I),
    re.compile(r"\b(password|passwd|pwd|密碼)\b\s*[:=]", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bid_rsa\b|\bid_ed25519\b"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    user_id     TEXT NOT NULL DEFAULT 'local',
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'fact',
    source      TEXT NOT NULL DEFAULT 'user_told',  -- 'user_told' / 'inferred'
    confidence  REAL NOT NULL DEFAULT 1.0,          -- inferred 較低
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, updated_at DESC);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def is_sensitive(value: str) -> Optional[str]:
    """value 含敏感樣式 → 回觸發的描述;否則 None。"""
    for pat in _SENSITIVE:
        if pat.search(value or ""):
            return pat.pattern
    return None


def remember_fact(key: str, value: str, category: str = "fact",
                  source: str = "user_told", confidence: float = 1.0,
                  user_id: str = "local") -> dict:
    """記一個事實/偏好。敏感資料拒記。回 dict 結果。"""
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return {"ok": False, "error": "key 與 value 都不可空"}
    hit = is_sensitive(value)
    if hit:
        return {"ok": False, "error": f"拒記:value 像敏感資料(樣式 {hit})、不收進記憶"}
    if category not in CATEGORIES:
        category = "fact"
    now = int(time.time())
    with _lock:
        db = _db()
        prev = db.execute("SELECT value FROM facts WHERE user_id=? AND key=?",
                          (user_id, key)).fetchone()
        db.execute(
            "INSERT OR REPLACE INTO facts(user_id, key, value, category, source, confidence, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, key, value, category, source, float(confidence), now),
        )
        db.commit()
    return {"ok": True, "key": key, "value": value, "category": category,
            "previous": prev[0] if prev else None, "updated_at": now}


def recall_fact(key: str, user_id: str = "local") -> dict:
    with _lock:
        row = _db().execute(
            "SELECT value, category, source, confidence, updated_at FROM facts WHERE user_id=? AND key=?",
            (user_id, key)).fetchone()
    if not row:
        return {"found": False, "key": key}
    return {"found": True, "key": key, "value": row[0], "category": row[1],
            "source": row[2], "confidence": row[3], "updated_at": row[4]}


def list_facts(category: Optional[str] = None, limit: int = 50,
               user_id: str = "local") -> list[dict]:
    q = "SELECT key, value, category, source, confidence, updated_at FROM facts WHERE user_id=?"
    args: list = [user_id]
    if category:
        q += " AND category=?"
        args.append(category)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(int(limit))
    with _lock:
        rows = _db().execute(q, args).fetchall()
    return [{"key": r[0], "value": r[1], "category": r[2], "source": r[3],
             "confidence": r[4], "updated_at": r[5]} for r in rows]


def forget_fact(key: str, user_id: str = "local") -> dict:
    with _lock:
        db = _db()
        cur = db.execute("DELETE FROM facts WHERE user_id=? AND key=?", (user_id, key))
        db.commit()
    return {"ok": True, "deleted": cur.rowcount, "key": key}


def count_facts(user_id: str = "local") -> int:
    with _lock:
        row = _db().execute("SELECT COUNT(*) FROM facts WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else 0


def snapshot(limit: int = 8, user_id: str = "local") -> list[dict]:
    """給 system prompt 注入用:最近更新的 top-N facts。"""
    return list_facts(limit=limit, user_id=user_id)
