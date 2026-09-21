"""会话记忆：SQLite 存聊天上下文和"已知信息"，重启不丢。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .sanitize import clean


class Memory:
    def __init__(self, path, recent_turns=12):
        self.path = str(path)
        self.recent_turns = recent_turns
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS msgs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, "
            "sender TEXT, content TEXT, ts REAL)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, fact TEXT, ts REAL)"
        )
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_msgs_chat ON msgs(chat_id, id)")
        self.conn.commit()

    # ---------- 聊天记录 ----------
    def add(self, chat_id, role, content, sender=""):
        chat_id = clean(chat_id)
        content = clean(content or "").strip()
        sender = clean(sender or "")
        if not content:
            return
        cursor = self.conn.execute(
            "SELECT role, content FROM msgs WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
        )
        last = cursor.fetchone()
        if last and last[0] == role and last[1] == content:
            return
        self.conn.execute(
            "INSERT INTO msgs (chat_id, role, sender, content, ts) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, sender, content, time.time()),
        )
        self.conn.commit()

    def recent(self, chat_id, limit=None):
        limit = limit or self.recent_turns * 2
        cursor = self.conn.execute(
            "SELECT role, sender, content FROM msgs WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, int(limit)),
        )
        rows = cursor.fetchall()
        rows.reverse()
        return [{"role": row[0], "sender": row[1], "content": row[2]} for row in rows]

    def messages_since(self, chat_id, last_id, limit=40):
        """取 id 大于 last_id 的消息（做滚动摘要时用）"""
        cursor = self.conn.execute(
            "SELECT id, role, sender, content FROM msgs "
            "WHERE chat_id = ? AND id > ? ORDER BY id LIMIT ?",
            (chat_id, int(last_id), int(limit)),
        )
        return [
            {"id": row[0], "role": row[1], "sender": row[2], "content": row[3]}
            for row in cursor.fetchall()
        ]

    def last_id(self, chat_id):
        cursor = self.conn.execute("SELECT MAX(id) FROM msgs WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return int(row[0] or 0)

    def history_for_prompt(self, chat_id, limit=None):
        """转成大模型的 messages 片段（对方的算 user，本人算 assistant）。"""
        messages = []
        for row in self.recent(chat_id, limit):
            role = "assistant" if row["role"] == "assistant" else "user"
            content = row["content"]
            if role == "user" and row.get("sender"):
                content = "%s：%s" % (row["sender"], content)
            messages.append({"role": role, "content": content})
        return messages

    def count(self, chat_id):
        cursor = self.conn.execute("SELECT COUNT(*) FROM msgs WHERE chat_id = ?", (chat_id,))
        return int(cursor.fetchone()[0])

    # ---------- 长期记忆 ----------
    def add_fact(self, chat_id, fact):
        chat_id = clean(chat_id)
        fact = clean(fact or "").strip()
        if len(fact) < 2:
            return False
        cursor = self.conn.execute(
            "SELECT 1 FROM facts WHERE chat_id = ? AND fact = ? LIMIT 1", (chat_id, fact)
        )
        if cursor.fetchone():
            return False
        self.conn.execute(
            "INSERT INTO facts (chat_id, fact, ts) VALUES (?, ?, ?)", (chat_id, fact, time.time())
        )
        self.conn.commit()
        return True

    def facts(self, chat_id, limit=12):
        cursor = self.conn.execute(
            "SELECT fact FROM facts WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, int(limit)),
        )
        rows = [row[0] for row in cursor.fetchall()]
        rows.reverse()
        return rows

    def clear(self, chat_id=None):
        if chat_id:
            self.conn.execute("DELETE FROM msgs WHERE chat_id = ?", (chat_id,))
            self.conn.execute("DELETE FROM facts WHERE chat_id = ?", (chat_id,))
        else:
            self.conn.execute("DELETE FROM msgs")
            self.conn.execute("DELETE FROM facts")
        self.conn.commit()

    # ---------- 状态 ----------
    def set_state(self, key, value):
        key = clean(key)
        self.conn.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, clean(json.dumps(value, ensure_ascii=False))),
        )
        self.conn.commit()

    def get_state(self, key, default=None):
        cursor = self.conn.execute("SELECT v FROM kv WHERE k = ?", (key,))
        row = cursor.fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
