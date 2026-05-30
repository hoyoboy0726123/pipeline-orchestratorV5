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
import json
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from config import OUTPUT_BASE_PATH

try:
    import numpy as _np
except Exception:
    _np = None

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

-- 情節記憶:對話摘要(+ 可選 embedding 做語意檢索)
CREATE TABLE IF NOT EXISTS episodes (
    user_id     TEXT NOT NULL DEFAULT 'local',
    conv_key    TEXT NOT NULL,                  -- 同一對話的識別、滾動更新摘要(不重複塞)
    summary     TEXT NOT NULL,                  -- LLM 1-3 句摘要
    embedding   BLOB,                           -- numpy float32 bytes;NULL = 無向量、走關鍵字
    tags        TEXT DEFAULT '',
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (user_id, conv_key)
);
CREATE INDEX IF NOT EXISTS idx_episodes_user ON episodes(user_id, updated_at DESC);
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


def snapshot(limit: int = 25, user_id: str = "local") -> list[dict]:
    """給 system prompt 注入用:最近更新的 top-N facts。
    25 筆 ≈ 1-2K token,個人偏好通常 <25 筆能全帶進 context、recall 不漏;
    超過 25 筆才需靠 list_facts 主動查(system prompt 有引導)。"""
    return list_facts(limit=limit, user_id=user_id)


# ─── Episodic(對話摘要 + 向量/關鍵字檢索)──────────────────────
def embed(text: str) -> "Optional[_np.ndarray]":
    """用 Gemini gemini-embedding-001 把文字轉向量。無 key / 套件 / 失敗 → None(降級關鍵字)。"""
    if _np is None:
        return None
    from config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        return None
    try:
        url = ("https://generativelanguage.googleapis.com/v1beta/"
               f"models/gemini-embedding-001:embedContent?key={GEMINI_API_KEY}")
        body = json.dumps({"model": "models/gemini-embedding-001",
                           "content": {"parts": [{"text": text[:8000]}]}}).encode()
        rq = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        resp = json.loads(urllib.request.urlopen(rq, timeout=20).read().decode())
        vals = resp.get("embedding", {}).get("values")
        return _np.array(vals, dtype=_np.float32) if vals else None
    except Exception:
        return None


def add_episode(conv_key: str, summary: str, tags: str = "", user_id: str = "local") -> dict:
    """存/更新一條對話摘要(同 conv_key 滾動更新、不重複塞)。自動嘗試 embed。"""
    summary = (summary or "").strip()
    if not summary:
        return {"ok": False, "error": "summary 空"}
    emb = embed(summary)
    blob = emb.astype(_np.float32).tobytes() if emb is not None else None
    now = int(time.time())
    with _lock:
        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO episodes(user_id, conv_key, summary, embedding, tags, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, conv_key, summary, blob, tags or "", now),
        )
        db.commit()
    return {"ok": True, "conv_key": conv_key, "has_vector": emb is not None, "updated_at": now}


def backfill_embeddings(user_id: str = "local", limit: int = 30) -> int:
    """把沒有向量的舊 episode 補上 embedding(無縫銜接:使用者後來才加 Gemini key)。
    embed 失敗(無 key/套件)立即停、不空轉。回補了幾筆。"""
    if _np is None:
        return 0
    with _lock:
        miss = _db().execute(
            "SELECT conv_key, summary FROM episodes WHERE user_id=? AND embedding IS NULL LIMIT ?",
            (user_id, limit)).fetchall()
    done = 0
    for ck, summ in miss:
        e = embed(summ)
        if e is None:
            break  # 沒 embedding 能力 → 停(維持關鍵字降級)
        with _lock:
            _db().execute("UPDATE episodes SET embedding=? WHERE user_id=? AND conv_key=?",
                          (e.astype(_np.float32).tobytes(), user_id, ck))
            _db().commit()
        done += 1
    return done


def recall_episode(query: str, max_results: int = 5, user_id: str = "local") -> list[dict]:
    """語意檢索過去對話摘要。有 embedding → cosine top-k;否則 → 關鍵字 LIKE。
    查詢時順手補舊 episode 的向量(加 key 後自我修復、無縫銜接)。"""
    query = (query or "").strip()
    backfill_embeddings(user_id)   # 加 key 後第一次查 → 把舊 episode 補上向量
    with _lock:
        rows = _db().execute(
            "SELECT conv_key, summary, embedding, tags, updated_at FROM episodes WHERE user_id=? "
            "ORDER BY updated_at DESC LIMIT 500", (user_id,)).fetchall()
    if not rows:
        return []
    qemb = embed(query) if query else None
    vec_rows = [(r, _np.frombuffer(r[2], dtype=_np.float32)) for r in rows if r[2]] if (qemb is not None and _np is not None) else []
    if qemb is not None and vec_rows:
        mat = _np.stack([v for _, v in vec_rows])
        sims = mat @ qemb / (_np.linalg.norm(mat, axis=1) * _np.linalg.norm(qemb) + 1e-9)
        order = _np.argsort(sims)[::-1][:max_results]
        return [{"summary": vec_rows[i][0][1], "tags": vec_rows[i][0][3],
                 "similarity": float(sims[i]), "updated_at": vec_rows[i][0][4]} for i in order]
    # 降級:關鍵字 LIKE(query 拆詞、任一命中)
    terms = [t for t in re.split(r"\s+", query) if len(t) >= 2] or [query]
    scored = []
    for r in rows:
        hits = sum(1 for t in terms if t and t.lower() in (r[1] or "").lower())
        if hits:
            scored.append((hits, r))
    scored.sort(key=lambda x: (-x[0], -x[1][4]))
    return [{"summary": r[1], "tags": r[3], "match_terms": h, "updated_at": r[4]}
            for h, r in scored[:max_results]]


def count_episodes(user_id: str = "local") -> int:
    with _lock:
        row = _db().execute("SELECT COUNT(*) FROM episodes WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else 0
