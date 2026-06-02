# Facebook Automation Telegram Bot

A Telegram-based automation bot for managing Facebook group workflows through a simple command interface.

This project demonstrates browser automation, session management, Telegram bot development, and automated web interaction using Python.

## Features

### Authentication
- Login using Facebook credentials
- Import existing Facebook session files
- Session validation and management
- Persistent login support

### Group Management
- Search Facebook groups by keyword
- Join discovered groups
- Specify the number of groups to join
- Stop join operations at any time
- Leave previously joined groups
- Track joined groups automatically

### Posting
- Publish text posts to saved groups
- Publish posts with image attachments
- Bulk posting workflow
- Progress reporting through Telegram

### Bot Commands

| Command | Description |
|----------|-------------|
| /start | Show available commands |
| /login | Login using Facebook credentials |
| /addsession | Import an existing Facebook session file |
| /search | Search groups by keyword |
| /join [number] | Join a specified number of groups |
| /stop | Stop join or leave operations |
| /leave | Leave previously joined groups |
| /post | Create posts in saved groups |
| /status | View session and group information |
| /cancel | Cancel active conversations |

## Installation

```bash
git clone https://github.com/yourusername/facebook-automation-bot.git
cd facebook-automation-bot
pip install -r requirements.txt
python bot.py
```

## Disclaimer

This software is provided for educational and research purposes only.

Users are responsible for complying with applicable laws, platform policies, and terms of service.

The author assumes no responsibility for misuse of this software or any consequences resulting from its use.

## License

MIT License
