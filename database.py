import sqlite3
from contextlib import closing

DB_PATH = "bot.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                content_type TEXT NOT NULL,   -- text | photo | video | document | audio | voice | animation
                content_text TEXT,            -- caption or plain text
                file_id TEXT,                 -- telegram file_id for media (NULL for text-only)
                sort_order INTEGER DEFAULT 0,
                delete_after INTEGER,         -- عدد الثواني قبل حذف الرسالة (NULL = بدون حذف)
                after_delete_text TEXT        -- الرسالة التي تُرسل بعد الحذف (NULL = بدون رسالة)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS start_counts (
                user_id INTEGER PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        conn.commit()


# ---------- Subscribers ----------

def add_subscriber(user_id: int, username: str | None):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        conn.commit()


def get_all_subscribers() -> list[int]:
    with closing(get_conn()) as conn:
        rows = conn.execute("SELECT user_id FROM subscribers").fetchall()
        return [r["user_id"] for r in rows]


def subscribers_count() -> int:
    with closing(get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM subscribers").fetchone()["c"]


def remove_subscriber(user_id: int):
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM subscribers WHERE user_id = ?", (user_id,))
        conn.commit()


# ---------- Buttons ----------

def add_button(
    name: str,
    content_type: str,
    content_text: str | None,
    file_id: str | None,
    delete_after: int | None = None,
    after_delete_text: str | None = None,
):
    with closing(get_conn()) as conn:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM buttons").fetchone()["m"]
        conn.execute(
            "INSERT INTO buttons (name, content_type, content_text, file_id, sort_order, delete_after, after_delete_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, content_type, content_text, file_id, max_order + 1, delete_after, after_delete_text),
        )
        conn.commit()


def get_all_buttons():
    with closing(get_conn()) as conn:
        return conn.execute("SELECT * FROM buttons ORDER BY sort_order ASC").fetchall()


def get_button(button_id: int):
    with closing(get_conn()) as conn:
        return conn.execute("SELECT * FROM buttons WHERE id = ?", (button_id,)).fetchone()


def get_button_by_name(name: str):
    with closing(get_conn()) as conn:
        return conn.execute("SELECT * FROM buttons WHERE name = ?", (name,)).fetchone()


def delete_button(button_id: int):
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM buttons WHERE id = ?", (button_id,))
        conn.commit()


# ---------- Settings (force subscribe channel) ----------

def set_setting(key: str, value: str):
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_setting(key: str) -> str | None:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def delete_setting(key: str):
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()


def get_force_sub_channels() -> list[str]:
    """Returns list of channel usernames/ids, comma separated in settings."""
    raw = get_setting("force_sub_channels")
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def set_force_sub_channels(channels: list[str]):
    set_setting("force_sub_channels", ",".join(channels))


# ---------- الاشتراك الإجباري الوهمي (Fake Force-Sub) ----------

def is_fake_sub_enabled() -> bool:
    return get_setting("fake_sub_enabled") == "1"


def set_fake_sub_enabled(enabled: bool):
    set_setting("fake_sub_enabled", "1" if enabled else "0")


def get_fake_sub_required() -> int:
    raw = get_setting("fake_sub_required")
    return int(raw) if raw and raw.isdigit() else 3


def set_fake_sub_required(count: int):
    set_setting("fake_sub_required", str(count))


def get_fake_sub_channels() -> list[str]:
    raw = get_setting("fake_sub_channels")
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def set_fake_sub_channels(channels: list[str]):
    set_setting("fake_sub_channels", ",".join(channels))


def get_start_count(user_id: int) -> int:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT count FROM start_counts WHERE user_id = ?", (user_id,)).fetchone()
        return row["count"] if row else 0


def increment_start_count(user_id: int) -> int:
    with closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO start_counts (user_id, count) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET count = count + 1",
            (user_id,),
        )
        conn.commit()
        row = conn.execute("SELECT count FROM start_counts WHERE user_id = ?", (user_id,)).fetchone()
        return row["count"]


def reset_start_count(user_id: int):
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM start_counts WHERE user_id = ?", (user_id,))
        conn.commit()
