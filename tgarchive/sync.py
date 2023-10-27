from io import BytesIO
from sys import exit
import json
import logging
import os
import tempfile
import shutil
import time
import random

from PIL import Image, ImageDraw, ImageFont

import telethon.tl.types
from telethon import TelegramClient, errors, sync
from telethon.tl.functions.messages import ExportChatInviteRequest

from .db import User, Message, Media, Chat


class Sync:
    """
    Sync iterates and receives messages from the Telegram group to the
    local SQLite DB.
    """

    config = {}
    db = None

    def __init__(self, config, session_file, db):
        self.config = config
        self.db = db

        self.client = self.new_client(session_file, config)

        if not os.path.exists(self.config["media_dir"]):
            os.makedirs(self.config["media_dir"], exist_ok=True)

    def sync(self, ids=None, from_id=None, group=None):
        """
        Sync syncs messages from Telegram from the last synced message
        into the local SQLite DB.
        """

        if ids:
            last_id, last_date = (ids, None)
            logging.info("fetching message id={}".format(ids))
        elif from_id:
            last_id, last_date = (from_id, None)
            logging.info("fetching from last message id={}".format(last_id))
        else:
            last_id, last_date = self.db.get_last_message_id()
            logging.info(
                "fetching from last message id={} ({})".format(last_id, last_date)
            )

        group_id = self._get_group_id(group)

        n = 0
        while True:
            has = False
            for m in self._get_messages(
                group_id, offset_id=last_id if last_id else 0, ids=ids
            ):
                if not m:
                    continue

                has = True
                # Insert the records into DB.
                if m.user:
                    self.db.insert_user(m.user)

                if m.media:
                    self.db.insert_media(m.media)

                self.db.insert_message(m)

                last_date = m.date

                n += 1
                if n % 100 == 0:
                    logging.info("fetched {} messages".format(n))
                    self.db.commit()
                    time.sleep(random.choice(range(1, 100)) / 50)
                else:
                    slp = random.choice(range(1, 100)) / 50
                    if slp < 0.2:
                        time.sleep(slp)

                if 0 < self.config["fetch_limit"] <= n or ids:
                    has = False
                    break

            self.db.commit()

            if has:
                last_id = m.id
                logging.info(
                    "fetched {} messages. sleeping for {} seconds".format(
                        n, self.config["fetch_wait"]
                    )
                )
                time.sleep(self.config["fetch_wait"])
            else:
                break

        self.db.commit()

        if self.config.get("use_takeout", False):
            self.finish_takeout()

        logging.info(
            "finished. fetched {} messages. last message = {}".format(n, last_date)
        )

    def new_client(self, session, config):
        if "proxy" in config and config["proxy"].get("enable"):
            proxy = config["proxy"]
            client = TelegramClient(
                session,
                config["api_id"],
                config["api_hash"],
                proxy=(proxy["protocol"], proxy["addr"], proxy["port"]),
            )
        else:
            client = TelegramClient(session, config["api_id"], config["api_hash"])
        # hide log messages
        # upstream issue https://github.com/LonamiWebs/Telethon/issues/3840
        client_logger = client._log["telethon.client.downloads"]
        client_logger._info = client_logger.info

        def patched_info(*args, **kwargs):
            if (
                args[0] == "File lives in another DC"
                or args[0]
                == "Starting direct file download in chunks of %d at %d, stride %d"
            ):
                return client_logger.debug(*args, **kwargs)
            client_logger._info(*args, **kwargs)

        client_logger.info = patched_info

        # Start client protocol
        client.start()

        if config.get("use_takeout", False):
            for retry in range(3):
                try:
                    takeout_client = client.takeout(finalize=True)
                    # check if the takeout session gets invalidated
                    takeout_client.get_messages("me")
                    return takeout_client
                except errors.TakeoutInitDelayError as e:
                    logging.info(
                        "please allow the data export request received from Telegram on your device. "
                        "you can also wait for {} seconds.".format(e.seconds)
                    )
                    logging.info(
                        "press Enter key after allowing the data export request to continue.."
                    )
                    input()
                    logging.info("trying again.. ({})".format(retry + 2))
                except errors.TakeoutInvalidError:
                    logging.info(
                        "takeout invalidated. delete the session.session file and try again."
                    )
            else:
                logging.info("could not initiate takeout.")
                raise (Exception("could not initiate takeout."))
        else:
            return client

    def finish_takeout(self):
        self.client.__exit__(None, None, None)

    def _get_messages(self, group_id, offset_id, ids=None) -> Message:
        messages = self._fetch_messages(group_id, offset_id, ids)
        # https://docs.telethon.dev/en/latest/quick-references/objects-reference.html#message
        for m in messages:
            if not m:
                continue

            # Media.
            sticker = None
            med = None
            if m.media:
                # If it's a sticker, get the alt value (unicode emoji).
                if (
                    isinstance(m.media, telethon.tl.types.MessageMediaDocument)
                    and hasattr(m.media, "document")
                    and m.media.document.mime_type == "application/x-tgsticker"
                ):
                    alt = [
                        a.alt
                        for a in m.media.document.attributes
                        if isinstance(a, telethon.tl.types.DocumentAttributeSticker)
                    ]
                    if len(alt) > 0:
                        sticker = alt[0]
                elif isinstance(m.media, telethon.tl.types.MessageMediaPoll):
                    med = self._make_poll(m)
                else:
                    med = self._get_media(m, group_id)

            # Message.
            typ = "message"
            if m.action:
                if isinstance(m.action, telethon.tl.types.MessageActionChatAddUser):
                    typ = "user_joined"
                elif isinstance(
                    m.action, telethon.tl.types.MessageActionChatDeleteUser
                ):
                    typ = "user_left"

            yield Message(
                type=typ,
                id=m.id,
                date=m.date,
                edit_date=m.edit_date,
                content=sticker if sticker else m.raw_text,
                reply_to=m.reply_to_msg_id
                if m.reply_to and m.reply_to.reply_to_msg_id
                else None,
                user=self._get_user(m.sender) if m.sender else None,
                media=med,
                full=m.to_json(),
                chat_id=group_id,
                from_chat_id=(
                    m.fwd_from.from_id.channel_id
                    if m.fwd_from and m.fwd_from.from_id
                    else None
                ),
                from_chat=None,
            )

    def _fetch_messages(self, group, offset_id, ids=None) -> Message:
        try:
            if self.config.get("use_takeout", False):
                wait_time = 0
            else:
                wait_time = None
            messages = self.client.get_messages(
                group,
                offset_id=offset_id,
                limit=self.config["fetch_batch_size"],
                wait_time=wait_time,
                ids=ids,
                reverse=True,
            )
            return messages
        except errors.FloodWaitError as e:
            logging.info("flood waited: have to wait {} seconds".format(e.seconds))

    def _get_user(self, u) -> User:
        tags = []
        is_normal_user = isinstance(u, telethon.tl.types.User)

        if isinstance(u, telethon.tl.types.ChannelForbidden):
            return User(
                id=u.id,
                username=u.title,
                first_name=None,
                last_name=None,
                tags=tags,
                avatar=None,
                full=u.to_json(),
            )

        if is_normal_user:
            if u.bot:
                tags.append("bot")

        if u.scam:
            tags.append("scam")

        if u.fake:
            tags.append("fake")

        # Download sender's profile photo if it's not already cached.
        avatar = None
        if self.config["download_avatars"]:
            try:
                fname = self._download_avatar(u)
                avatar = fname
            except Exception as e:
                logging.error("error downloading avatar: #{}: {}".format(u.id, e))

        return User(
            id=u.id,
            username=u.username if u.username else str(u.id),
            first_name=u.first_name if is_normal_user else None,
            last_name=u.last_name if is_normal_user else None,
            tags=tags,
            avatar=avatar,
            full=u.to_json(),
        )

    def _make_poll(self, msg):
        if not msg.media.results or not msg.media.results.results:
            return None

        options = [
            {"label": a.text, "count": 0, "correct": False}
            for a in msg.media.poll.answers
        ]

        total = msg.media.results.total_voters
        if msg.media.results.results:
            for i, r in enumerate(msg.media.results.results):
                options[i]["count"] = r.voters
                options[i]["percent"] = r.voters / total * 100 if total > 0 else 0
                options[i]["correct"] = r.correct

        return Media(
            id=msg.id,
            type="poll",
            url=None,
            title=msg.media.poll.question,
            description=json.dumps(options),
            thumb=None,
            full=msg.media.to_json(),
        )

    def _get_media(self, msg, group_id):
        res = None
        if isinstance(
            msg.media, telethon.tl.types.MessageMediaWebPage
        ) and not isinstance(msg.media.webpage, telethon.tl.types.WebPageEmpty):
            try:
                media_file_path = self._download_media(msg, group_id)
                res = Media(
                    id=msg.id,
                    type="webpage",
                    url=msg.media.webpage.url,
                    title=msg.media.webpage.title,
                    description=msg.media.webpage.description
                    if msg.media.webpage.description
                    else None,
                    thumb=media_file_path,
                    full=msg.media.to_json(),
                )
            except Exception as e:
                logging.error("error downloading media: #{}: {}".format(msg.id, e))
        elif (
            isinstance(msg.media, telethon.tl.types.MessageMediaPhoto)
            or isinstance(msg.media, telethon.tl.types.MessageMediaDocument)
            or isinstance(msg.media, telethon.tl.types.MessageMediaContact)
        ):
            if self.config["download_media"]:
                # Filter by extensions?
                if len(self.config["media_mime_types"]) > 0:
                    if (
                        hasattr(msg, "file")
                        and hasattr(msg.file, "mime_type")
                        and msg.file.mime_type
                    ):
                        if msg.file.mime_type not in self.config["media_mime_types"]:
                            logging.info(
                                "skipping media #{} / {}".format(
                                    msg.file.name, msg.file.mime_type
                                )
                            )
                            return

                logging.info("downloading media #{}".format(msg.id))
                try:
                    media_file_path = self._download_media(msg, group_id)
                    res = Media(
                        id=msg.id,
                        type="photo",
                        url=media_file_path,
                        title=media_file_path,
                        description=None,
                        thumb=media_file_path,
                        full=msg.media.to_json(),
                    )
                except Exception as e:
                    logging.error("error downloading media: #{}: {}".format(msg.id, e))
        return res

    def _download_media(self, msg, group_id) -> [str, str, str]:
        media_path = os.path.join(
            self.config["media_dir"],
            "chats",
            f"{group_id}",
            f"{msg.id}",
        )
        # with tempfile.TemporaryDirectory() as tmpdirname:
        file_path = self.client.download_media(msg)
        if file_path:
            os.makedirs(media_path, exist_ok=True)
            media_file_path = shutil.move(file_path, media_path)
            return media_file_path

    def _get_file_ext(self, f) -> str:
        if "." in f:
            e = f.split(".")[-1]
            if len(e) < 6:
                return e

        return ".file"

    def _download_avatar(self, user):
        media_dir_path = os.path.join(
            self.config["media_dir"],
            "users",
            f"{user.id}",
        )
        media_file_path = os.path.join(
            media_dir_path,
            f"{user.username}.png",
        )

        if os.path.exists(media_file_path):
            return media_file_path

        photo_file_path = self.client.download_profile_photo(user, file=media_file_path)
        logging.info("download profile photo %s", photo_file_path)

        if not photo_file_path:
            avatar = generate_avarat(user.first_name[0] if user.first_name else "N")
            os.makedirs(media_dir_path, exist_ok=True)
            avatar.save(media_file_path)
            return media_file_path
        return photo_file_path

    def init_get_and_save_dialogs(self):
        # Get all dialogs for the authorized user, which also
        # syncs the entity cache to get latest entities
        # ref: https://docs.telethon.dev/en/latest/concepts/entities.html#getting-entities
        dialogs = self.client.get_dialogs()
        for dialog in dialogs:
            chat = Chat(
                id=dialog.id,
                date=dialog.date,
                archived=dialog.archived,
                is_channel=dialog.is_channel,
                is_group=dialog.is_group,
                is_user=dialog.is_user,
                name=dialog.name,
                title=dialog.title,
                pinned=dialog.pinned,
                full_dialog=dialog.dialog.to_json(),
                full_entity=dialog.entity.to_json(),
                username=(
                    getattr(dialog, "entity", None)
                    and getattr(dialog.entity, "username", None)
                ),
            )
            self.db.insert_chat(chat)

        self.db.commit()
        time.sleep(random.choice(range(1, 100)) / 70)
        logging.info("saved dialogs %s", len(dialogs))

    def _get_group_id(self, group):
        """
        Syncs the Entity cache and returns the Entity ID for the specified group,
        which can be a str/int for group ID, group name, or a group username.

        The authorized user must be a part of the group.
        """
        try:
            # If the passed group is a group ID, extract it.
            group = int(group)
        except ValueError:
            # Not a group ID, we have either a group name or
            # a group username: @group-username
            pass

        try:
            entity = self.client.get_entity(group)
        except ValueError:
            logging.critical(
                "the group: {} does not exist,"
                " or the authorized user is not a participant!".format(group)
            )
            # This is a critical error, so exit with code: 1
            exit(1)

        return entity.id


def generate_avarat(one_latter):
    avatar_size = (100, 100)
    # background_color = (255, 0, 255)
    background_color = tuple(random.randint(0, 255) for _ in range(3))
    avatar = Image.new("RGB", avatar_size, background_color)
    # Create a drawing context
    draw = ImageDraw.Draw(avatar)
    draw.text(
        (30, 25), one_latter.upper(), fill=(0, 0, 1), font_size=60, align="center"
    )
    return avatar
