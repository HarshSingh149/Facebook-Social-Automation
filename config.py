# ================== CONFIGURATION ==================

# Telegram Bot Settings
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
ALLOWED_USER_ID = 123456789  # Replace with your Telegram User ID

# Playwright Settings
HEADLESS = True                     # True = runs in background (recommended)

# Joining Settings
MIN_DELAY = 15
MAX_DELAY = 45
DELAY_BETWEEN_GROUPS = (15, 45)     # Random delay in seconds
MAX_GROUPS_PER_RUN = 25
SCROLL_PAUSE = 3
MAX_SCROLLS = 8

# File Paths
SESSION_FILE = "facebook_session.json"
GROUPS_FILE = "groups.json"

# ================== END OF CONFIG ==================
