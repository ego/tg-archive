import json
import math
import os
import sqlite3
from collections import namedtuple
from datetime import datetime
import pytz
from typing import Iterator
import shutil


schema = """
CREATE table chats (
    id INTEGER NOT NULL PRIMARY KEY,
    title TEXT,
    name TEXT,
    date TIMESTAMP,
    archived BOOLEAN,
    is_channel BOOLEAN,
    is_group BOOLEAN,
    is_user BOOLEAN,
    pinned BOOLEAN,
    full_dialog TEXT,
    full_entity TEXT,
    username TEXT
);
##
CREATE table messages (
    id INTEGER NOT NULL,
    type TEXT NOT NULL,
    date TIMESTAMP NOT NULL,
    edit_date TIMESTAMP,
    content TEXT,
    reply_to INTEGER,
    user_id INTEGER,
    media_id INTEGER,
    full TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    from_chat_id INTEGER,
    PRIMARY KEY (id, chat_id),
    FOREIGN KEY(chat_id) REFERENCES chats(id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(media_id) REFERENCES media(id)
);
##
CREATE table users (
    id INTEGER NOT NULL PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    tags TEXT,
    avatar TEXT,
    full TEXT NOT NULL
);
##
CREATE table media (
    id INTEGER NOT NULL PRIMARY KEY,
    type TEXT,
    url TEXT,
    title TEXT,
    description TEXT,
    thumb TEXT,
    full TEXT NOT NULL
);
"""

Chat = namedtuple(
    "Chat",
    [
        "id",
        "date",
        "archived",
        "is_channel",
        "is_group",
        "is_user",
        "name",
        "title",
        "pinned",
        "full_dialog",
        "full_entity",
        "username",
    ],
)

User = namedtuple(
    "User", ["id", "username", "first_name", "last_name", "tags", "avatar", "full"]
)

Message = namedtuple(
    "Message",
    [
        "id",
        "type",
        "date",
        "edit_date",
        "content",
        "reply_to",
        "user",
        "media",
        "full",
        "chat_id",
        "from_chat_id",
        "from_chat",
    ],
)

Media = namedtuple(
    "Media", ["id", "type", "url", "title", "description", "thumb", "full"]
)

Month = namedtuple("Month", ["date", "slug", "label", "count"])

Day = namedtuple("Day", ["date", "slug", "label", "count", "page"])


def _page(n, multiple):
    return math.ceil(n / multiple)


class DB:
    conn = None
    tz = None

    def __init__(self, dbfile, tz=None, config=None, sync=None):
        # Initialize the SQLite DB. If it's new, create the table schema.
        is_new = not os.path.isfile(dbfile)

        if sync and (not is_new) and config.get("database_backup_dir"):
            # make backup for dbfile
            backup_dir = config.get("database_backup_dir")
            backup_name = datetime.now().isoformat().replace(":", "_").replace(".", "_")
            backup_path = os.path.join(backup_dir, backup_name)
            os.makedirs(backup_path, exist_ok=True)
            new_backup = shutil.copyfile(
                dbfile,
                os.path.join(backup_path, dbfile),
            )
            res = shutil.make_archive(backup_path, "zip", backup_path)
            if res:
                shutil.rmtree(backup_path)

        self.conn = sqlite3.Connection(
            dbfile,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )

        # Add the custom PAGE() function to get the page number of a row
        # by its row number and a limit multiple.
        self.conn.create_function("PAGE", 2, _page)

        if tz:
            self.tz = pytz.timezone(tz)

        if is_new:
            for s in schema.split("##"):
                self.conn.cursor().execute(s)
                self.conn.commit()

    def _parse_date(self, d) -> str:
        return datetime.strptime(d, "%Y-%m-%dT%H:%M:%S%z")

    def get_last_message_id(self) -> [int, datetime]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, strftime('%Y-%m-%d 00:00:00', date) as "[timestamp]" 
            FROM messages
            ORDER BY id 
            DESC LIMIT 1
        """
        )
        res = cur.fetchone()
        if not res:
            return 0, None

        id, date = res
        return id, date

    def get_timeline(self) -> Iterator[Month]:
        """
        Get the list of all unique yyyy-mm month groups and
        the corresponding message counts per period in chronological order.
        """
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT strftime('%Y-%m-%d 00:00:00', date) as "[timestamp]",
            COUNT(*) FROM messages AS count
            GROUP BY strftime('%Y-%m', date) ORDER BY date
        """
        )

        for r in cur.fetchall():
            date = pytz.utc.localize(r[0])
            if self.tz:
                date = date.astimezone(self.tz)

            yield Month(
                date=date,
                slug=date.strftime("%Y-%m"),
                label=date.strftime("%b %Y"),
                count=r[1],
            )

    def get_dayline(self, year, month, limit=500) -> Iterator[Day]:
        """
        Get the list of all unique yyyy-mm-dd days corresponding
        message counts and the page number of the first occurrence of
        the date in the pool of messages for the whole month.
        """
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT strftime("%Y-%m-%d 00:00:00", date) AS "[timestamp]",
            COUNT(*), PAGE(rank, ?) FROM (
                SELECT ROW_NUMBER() OVER() as rank, date FROM messages
                WHERE strftime('%Y%m', date) = ? ORDER BY id
            )
            GROUP BY "[timestamp]";
        """,
            (limit, "{}{:02d}".format(year, month)),
        )

        for r in cur.fetchall():
            date = pytz.utc.localize(r[0])
            if self.tz:
                date = date.astimezone(self.tz)

            yield Day(
                date=date,
                slug=date.strftime("%Y-%m-%d"),
                label=date.strftime("%d %b %Y"),
                count=r[1],
                page=r[2],
            )

    def get_messages(self, year, month, last_id=0, limit=500) -> Iterator[Message]:
        date = "{}{:02d}".format(year, month)

        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT messages.id, messages.type, messages.date, messages.edit_date,
            messages.content, messages.reply_to, messages.user_id,
            users.username, users.first_name, users.last_name, users.tags, users.avatar,
            media.id, media.type, media.url, media.title, media.description, media.thumb,
            users.full as user_full, media.full as media_full, messages.full as message_full,
            messages.chat_id, messages.from_chat_id, chats.full_entity as chat_full_entity
            FROM messages
            LEFT JOIN users ON (users.id = messages.user_id)
            LEFT JOIN media ON (media.id = messages.media_id)
            LEFT JOIN chats ON (chats.id = messages.from_chat_id OR chats.id = printf('-100%d', messages.from_chat_id))
            WHERE strftime('%Y%m', messages.date) = ?
            AND messages.id > ? ORDER by messages.id LIMIT ?
            """,
            (date, last_id, limit),
        )

        for r in cur.fetchall():
            yield self._make_message(r)

    def get_message_count(self, year, month) -> int:
        date = "{}{:02d}".format(year, month)

        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM messages WHERE strftime('%Y%m', date) = ?
            """,
            (date,),
        )

        (total,) = cur.fetchone()
        return total

    def insert_user(self, u: User):
        """Insert a user and if they exist, update the fields."""
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO users (id, username, first_name, last_name, tags, avatar, full)
            VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id)
            DO UPDATE SET username=excluded.username, first_name=excluded.first_name,
                last_name=excluded.last_name, tags=excluded.tags, avatar=excluded.avatar, full=excluded.full
            """,
            (
                u.id,
                u.username,
                u.first_name,
                u.last_name,
                " ".join(u.tags),
                u.avatar,
                u.full,
            ),
        )

    def insert_media(self, m: Media):
        cur = self.conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO media
            (id, type, url, title, description, thumb, full)
            VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (m.id, m.type, m.url, m.title, m.description, m.thumb, m.full),
        )

    def insert_chat(self, chat: Chat):
        cur = self.conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO chats
            (id, title, name, date, archived, is_channel, is_group, is_user, pinned, full_dialog, full_entity, username)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat.id,
                chat.title,
                chat.name,
                chat.date,
                chat.archived,
                chat.is_channel,
                chat.is_group,
                chat.is_user,
                chat.pinned,
                chat.full_dialog,
                chat.full_entity,
                chat.username,
            ),
        )

    def insert_message(self, m: Message):
        cur = self.conn.cursor()
        cur.execute(
            """INSERT OR REPLACE INTO messages
            (id, type, date, edit_date, content, reply_to, user_id, media_id, full, chat_id, from_chat_id)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m.id,
                m.type,
                m.date.strftime("%Y-%m-%d %H:%M:%S"),
                m.edit_date.strftime("%Y-%m-%d %H:%M:%S") if m.edit_date else None,
                m.content,
                m.reply_to,
                m.user.id if m.user else None,
                m.media.id if m.media else None,
                m.full,
                m.chat_id,
                m.from_chat_id,
            ),
        )

    def commit(self):
        """Commit pending writes to the DB."""
        self.conn.commit()

    def _make_message(self, m) -> Message:
        """Makes a Message() object from an SQL result tuple."""
        (
            id,
            typ,
            date,
            edit_date,
            content,
            reply_to,
            user_id,
            username,
            first_name,
            last_name,
            tags,
            avatar,
            media_id,
            media_type,
            media_url,
            media_title,
            media_description,
            media_thumb,
            user_full,
            media_full,
            message_full,
            chat_id,
            from_chat_id,
            chat_full_entity,
        ) = m

        media = None
        if media_id:
            desc = media_description
            if media_type == "poll":
                desc = json.loads(media_description)

            media = Media(
                id=media_id,
                type=media_type,
                url=media_url,
                title=media_title,
                description=desc,
                thumb=media_thumb,
                full=json.loads(media_full) if media_full else None,
            )

        date = pytz.utc.localize(date) if date else None
        edit_date = pytz.utc.localize(edit_date) if edit_date else None

        if self.tz:
            date = date.astimezone(self.tz) if date else None
            edit_date = edit_date.astimezone(self.tz) if edit_date else None

        return Message(
            id=id,
            type=typ,
            date=date,
            edit_date=edit_date,
            content=content,
            reply_to=reply_to,
            user=User(
                id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                tags=tags,
                avatar=avatar,
                full=json.loads(user_full) if user_full else None,
            ),
            media=media,
            full=json.loads(message_full) if message_full else None,
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            from_chat=json.loads(chat_full_entity) if chat_full_entity else None,
        )

    def get_groups(self, ids) -> Iterator[Chat]:
        cur = self.conn.cursor()
        placeholders = ", ".join(["?"] * len(ids))
        query = f"""
        SELECT
            id, "" as date, archived, is_channel, is_group, is_user, name, 
            title, pinned, full_dialog, full_entity, username 
        FROM chats WHERE id IN ({placeholders})
        """
        cur.execute(query, tuple(ids))
        for row in cur.fetchall():
            x = dict_factory(cur, row)
            for json_key in ["full_dialog", "full_entity"]:
                if json_key in x and x[json_key]:
                    x[json_key] = json.loads(x[json_key])

            yield Chat(**x)
            # yield Chat(
            #     id=x.id,
            #     date=x.date,
            #     archived=x.archived,
            #     is_channel=x.is_channel,
            #     is_group=x.is_group,
            #     is_user=x.is_user,
            #     name=x.name,
            #     title=x.title,
            #     pinned=x.pinned,
            #     full_dialog=json.loads(x.full_dialog) if x.full_dialog else None,
            #     full_entity=json.loads(x.full_entity) if x.full_entity else None,
            #     username=x.username,
            # )


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d
