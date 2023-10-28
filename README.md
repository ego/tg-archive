# tg-archive keep your [Telegram messaging app](https://telegram.org) group, chats, channels data on-premises.

**tg-archive** is a tool for exporting Telegram group chats into static websites, preserving chat history like local backup or archives.


## Preview
The [@fossunited](https://tg.fossunited.org) Telegram group archive.

![image](https://user-images.githubusercontent.com/547147/111869398-44188980-89a5-11eb-936f-01d98276ba6a.png)


## How it works
tg-archive uses the [Telethon](https://github.com/LonamiWebs/Telethon) Telegram API client to periodically sync messages from a group to a local SQLite database (file), downloading only new messages since the last sync. It then generates a static archive website of messages to be published anywhere.

## Features
- Periodically sync Telegram group messages to a local DB.
- Download user avatars locally.
- Download and embed media (files, documents, photos).
- Renders poll results.
- Use emoji alternatives in place of stickers.
- Single file Jinja HTML template for generating the static site.
- Year / Month / Day indexes with deep linking across pages.
- "In reply to" on replies with links to parent messages across pages.
- RSS / Atom feed of recent messages.

## Install
- Get [Telegram API credentials](https://my.telegram.org/auth?to=apps). Normal user account API and not the Bot API.
  - If this page produces an alert stating only "ERROR", disconnect from any proxy/vpn and try again in a different browser.
- Install from PyPi `pip3 install tg-archive` (only original Kailash Nadh version).

To use this updated version install it from source

1. `git clone git@github.com:ego/tg-archive.git`
2. `cd tg-archive/`
3. `python3 -m venv .venv`
4. `. .venv/bin/activate`
5. `python3 -m pip install -e .`

### Usage

1. `tg-archive --new --path=telegram` (creates a new site. `cd` into `telegram/` and edit `config.yaml`).
1. `tg-archive --sync` (syncs data into `data.sqlite`).
  Note: First time connection will prompt for your phone number + a Telegram auth code sent to the app. On successful auth, a `session.session` file is created. DO NOT SHARE this session file publicly as it contains the API authorization for your account.
1. `tg-archive --build` (builds the static site into the `site` directory, which can be published)

### Customization
Edit the generated `template.html` and static assets in the `./static` directory to customize the site.

### Note
- The sync can be stopped (Ctrl+C) any time to be resumed later.
- Setup a cron job to periodically sync messages and re-publish the archive.
- Downloading large media files and long message history from large groups continuously may run into Telegram API's rate limits. Watch the debug output.

Licensed under the MIT license.

### Updates

* Fix bug in `setup.py` for local develop

* Added multi groups config feature

```YAML
group: [
  "-100",
  "-123",
]
```
* Added `view_timezone: ""` to have ability insert into DB time in UTC but show view_timezone on UI

* Added backup for backup feature `database_backup_dir: ""` =)

* Update a lot codebase `__init__.py`, `sync.py`, `db.py`

* Added `chats` table and sync for it.

* Use composite primary key for `messages` table `PRIMARY KEY (id, chat_id)`

* Added additional column `full` for all tables for raw data from API

* Added code formatters `flake8, isort, black`

* Clean and update codebase to Python community standards PEP8 ect.


### Roadmap

* Fix async feature use_takeout

* Move codebase to more consistent Telegram API wrapper [pyrogram](https://pyrogram.org)

* Add web server for local develop

* Improve UI
