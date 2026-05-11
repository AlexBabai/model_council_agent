import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def connect(self) -> aiosqlite.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA journal_mode = WAL")
        return db

    async def migrate(self) -> None:
        async with self._connection() as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS mcp_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    token TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    llm_config_ids TEXT NOT NULL,
                    is_running INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chat_mcp_links (
                    chat_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    mcp_config_id INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, mcp_config_id),
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
                    FOREIGN KEY (mcp_config_id) REFERENCES mcp_configs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_llm_configs_user ON llm_configs(user_id);
                CREATE INDEX IF NOT EXISTS idx_mcp_configs_user ON mcp_configs(user_id);
                CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id);
                CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
                """
            )
            await db.commit()

    async def create_llm_config(
        self, user_id: str, name: str, base_url: str, api_key: str, model: str
    ) -> dict[str, Any]:
        async with self._connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO llm_configs (user_id, name, base_url, api_key, model)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, name, base_url, api_key, model),
            )
            await db.commit()
            config_id = self._lastrowid(cursor.lastrowid)
            return await self.get_llm_config(user_id, config_id, include_secret=False)

    async def list_llm_configs(self, user_id: str) -> list[dict[str, Any]]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, name, base_url, model
                FROM llm_configs
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]

    async def get_llm_config(
        self, user_id: str, config_id: int, *, include_secret: bool = True
    ) -> dict[str, Any]:
        columns = (
            "id, name, base_url, model, api_key"
            if include_secret
            else "id, name, base_url, model"
        )
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                f"SELECT {columns} FROM llm_configs WHERE user_id = ? AND id = ?",
                (user_id, config_id),
            )
            if row is None:
                raise KeyError("LLM config not found")
            return dict(row)

    async def get_llm_configs(self, user_id: str, config_ids: list[int]) -> list[dict[str, Any]]:
        return [await self.get_llm_config(user_id, config_id) for config_id in config_ids]

    async def create_mcp_config(
        self, user_id: str, name: str, url: str, token: str
    ) -> dict[str, Any]:
        async with self._connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO mcp_configs (user_id, name, url, token)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name, url, token),
            )
            await db.commit()
            config_id = self._lastrowid(cursor.lastrowid)
            return await self.get_mcp_config(user_id, config_id, include_secret=False)

    async def list_mcp_configs(self, user_id: str) -> list[dict[str, Any]]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, name, url
                FROM mcp_configs
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]

    async def get_mcp_config(
        self, user_id: str, config_id: int, *, include_secret: bool = True
    ) -> dict[str, Any]:
        columns = "id, name, url, token" if include_secret else "id, name, url"
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                f"SELECT {columns} FROM mcp_configs WHERE user_id = ? AND id = ?",
                (user_id, config_id),
            )
            if row is None:
                raise KeyError("MCP config not found")
            return dict(row)

    async def create_chat(
        self, user_id: str, title: str, llm_config_ids: list[int]
    ) -> dict[str, Any]:
        await self.get_llm_configs(user_id, llm_config_ids)
        async with self._connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO chats (user_id, title, llm_config_ids)
                VALUES (?, ?, ?)
                """,
                (user_id, title, json.dumps(llm_config_ids)),
            )
            await db.commit()
            chat_id = self._lastrowid(cursor.lastrowid)
            return await self.get_chat(user_id, chat_id)

    async def get_chat(self, user_id: str, chat_id: int) -> dict[str, Any]:
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                """
                SELECT id, title, llm_config_ids, created_at
                FROM chats
                WHERE user_id = ? AND id = ?
                """,
                (user_id, chat_id),
            )
            if row is None:
                raise KeyError("Chat not found")
            chat = dict(row)
            chat["llm_config_ids"] = json.loads(chat["llm_config_ids"])
            chat["attached_mcp_ids"] = await self._list_chat_mcp_ids(db, user_id, chat_id)
            return chat

    async def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, title, llm_config_ids, created_at
                FROM chats
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            )
            chats: list[dict[str, Any]] = []
            for row in rows:
                chat = dict(row)
                chat["llm_config_ids"] = json.loads(chat["llm_config_ids"])
                chat["attached_mcp_ids"] = await self._list_chat_mcp_ids(
                    db,
                    user_id,
                    int(chat["id"]),
                )
                chats.append(chat)
            return chats

    async def set_chat_mcp_links(
        self, user_id: str, chat_id: int, mcp_config_ids: list[int]
    ) -> dict[str, Any]:
        async with self._connection() as db:
            chat = await self._fetchone(
                db,
                "SELECT is_running FROM chats WHERE user_id = ? AND id = ?",
                (user_id, chat_id),
            )
            if chat is None:
                raise KeyError("Chat not found")
            if int(chat["is_running"]):
                raise RuntimeError("Chat is currently running an agent iteration")
            for config_id in mcp_config_ids:
                config = await self._fetchone(
                    db,
                    "SELECT id FROM mcp_configs WHERE user_id = ? AND id = ?",
                    (user_id, config_id),
                )
                if config is None:
                    raise KeyError(f"MCP config {config_id} not found")
            await db.execute(
                "DELETE FROM chat_mcp_links WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            await db.executemany(
                """
                INSERT INTO chat_mcp_links (chat_id, user_id, mcp_config_id)
                VALUES (?, ?, ?)
                """,
                [(chat_id, user_id, config_id) for config_id in mcp_config_ids],
            )
            await db.commit()
        return await self.get_chat(user_id, chat_id)

    async def list_chat_mcp_configs(self, user_id: str, chat_id: int) -> list[dict[str, Any]]:
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """
                SELECT m.id, m.name, m.url, m.token
                FROM mcp_configs m
                JOIN chat_mcp_links l ON l.mcp_config_id = m.id
                WHERE l.user_id = ? AND l.chat_id = ?
                ORDER BY m.id
                """,
                (user_id, chat_id),
            )
            return [dict(row) for row in rows]

    async def add_message(
        self,
        user_id: str,
        chat_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO messages (chat_id, user_id, role, content, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            await db.commit()
            row = await self._fetchone(
                db,
                """
                SELECT id, role, content, metadata, created_at
                FROM messages
                WHERE id = ? AND user_id = ?
                """,
                (self._lastrowid(cursor.lastrowid), user_id),
            )
            if row is None:
                raise KeyError("Message not found")
            return self._message_from_row(row)

    async def list_messages(self, user_id: str, chat_id: int) -> list[dict[str, Any]]:
        async with self._connection() as db:
            await self._ensure_chat(db, user_id, chat_id)
            rows = await db.execute_fetchall(
                """
                SELECT id, role, content, metadata, created_at
                FROM messages
                WHERE user_id = ? AND chat_id = ?
                ORDER BY id
                """,
                (user_id, chat_id),
            )
            return [self._message_from_row(row) for row in rows]

    async def set_chat_running(self, user_id: str, chat_id: int, running: bool) -> bool:
        async with self._connection() as db:
            if running:
                cursor = await db.execute(
                    """
                    UPDATE chats
                    SET is_running = 1
                    WHERE user_id = ? AND id = ? AND is_running = 0
                    """,
                    (user_id, chat_id),
                )
            else:
                cursor = await db.execute(
                    "UPDATE chats SET is_running = 0 WHERE user_id = ? AND id = ?",
                    (user_id, chat_id),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def _list_chat_mcp_ids(
        self, db: aiosqlite.Connection, user_id: str, chat_id: int
    ) -> list[int]:
        rows = await db.execute_fetchall(
            """
            SELECT mcp_config_id
            FROM chat_mcp_links
            WHERE user_id = ? AND chat_id = ?
            ORDER BY mcp_config_id
            """,
            (user_id, chat_id),
        )
        return [int(row["mcp_config_id"]) for row in rows]

    async def _ensure_chat(self, db: aiosqlite.Connection, user_id: str, chat_id: int) -> None:
        row = await self._fetchone(
            db,
            "SELECT id FROM chats WHERE user_id = ? AND id = ?",
            (user_id, chat_id),
        )
        if row is None:
            raise KeyError("Chat not found")

    def _message_from_row(self, row: aiosqlite.Row) -> dict[str, Any]:
        message = dict(row)
        message["metadata"] = json.loads(message["metadata"])
        return message

    async def _fetchone(
        self, db: aiosqlite.Connection, query: str, parameters: tuple[Any, ...]
    ) -> aiosqlite.Row | None:
        async with db.execute(query, parameters) as cursor:
            row = await cursor.fetchone()
            return row

    def _lastrowid(self, value: int | None) -> int:
        if value is None:
            raise RuntimeError("SQLite did not return lastrowid")
        return value

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await self.connect()
        try:
            yield db
        finally:
            await db.close()
