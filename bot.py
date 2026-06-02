import asyncio
import json
import os
import random
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from playwright.async_api import async_playwright
import config

# ─── Conversation states ───────────────────────────────────────────────────────
EMAIL, PASSWORD = range(2)
POST_TEXT, POST_IMAGE = range(2, 4)
WAIT_SESSION_FILE = 4

# ─── Global stop flags ─────────────────────────────────────────────────────────
# Key: user_id → asyncio.Event; set() signals the running task to stop
_stop_flags: dict[int, asyncio.Event] = {}

def get_stop_flag(user_id: int) -> asyncio.Event:
    if user_id not in _stop_flags:
        _stop_flags[user_id] = asyncio.Event()
    return _stop_flags[user_id]

# ─── Helpers ───────────────────────────────────────────────────────────────────

def auth_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ALLOWED_USER_ID:
            await update.message.reply_text("⛔ Unauthorized!")
            return ConversationHandler.END
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def session_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not os.path.exists(config.SESSION_FILE):
            await update.message.reply_text("❌ Not logged in! Use /login first.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def make_browser_context(storage=True):
    """Returns (browser_launch_args, context_options) tuple."""
    launch_args = [
        '--no-sandbox', '--disable-setuid-sandbox',
        '--disable-blink-features=AutomationControlled',
    ]
    ctx_opts = {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "locale": "en-US",
        "viewport": {"width": 1280, "height": 800},
    }
    if storage and os.path.exists(config.SESSION_FILE):
        ctx_opts["storage_state"] = config.SESSION_FILE
    return launch_args, ctx_opts

# ─── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Facebook Group Bot*\n\n"
        "📋 *Commands:*\n"
        "/login — Login to Facebook\n"
        "/addsession — Upload existing session file\n"
        "/search `<keyword>` — Find groups\n"
        "/join `[number]` — Join groups (e.g. /join 15)\n"
        "/stop — Stop joining/leaving mid-process\n"
        "/leave — Leave all joined groups\n"
        "/post — Post to saved groups\n"
        "/status — Show session & groups info\n"
        "/cancel — Cancel current operation",
        parse_mode="Markdown"
    )

# ─── /status ───────────────────────────────────────────────────────────────────

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = "✅ Logged in" if os.path.exists(config.SESSION_FILE) else "❌ Not logged in"
    groups_count = 0
    if os.path.exists(config.GROUPS_FILE):
        with open(config.GROUPS_FILE) as f:
            try:
                groups_count = len(json.load(f))
            except:
                pass
    await update.message.reply_text(
        f"📊 *Status*\n\n"
        f"Session: {session}\n"
        f"Groups saved: {groups_count}",
        parse_mode="Markdown"
    )

# ─── /addsession ───────────────────────────────────────────────────────────────

@auth_required
async def addsession_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📁 Send me your *facebook_session.json* file.\n\n"
        "The file must be a valid Playwright storage state exported from Facebook.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return WAIT_SESSION_FILE

async def addsession_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc or not doc.file_name.endswith(".json"):
        await update.message.reply_text(
            "❌ Please send a `.json` file. Try again or /cancel."
        )
        return WAIT_SESSION_FILE

    try:
        file = await doc.get_file()
        raw = await file.download_as_bytearray()
        content = raw.decode("utf-8")
        data = json.loads(content)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Couldn't read the file: {str(e)[:100]}\nMake sure it's valid JSON. Try again or /cancel."
        )
        return WAIT_SESSION_FILE

    # Structural validation
    error = _validate_session(data)
    if error:
        await update.message.reply_text(
            f"❌ Invalid session file:\n{error}\n\nTry again or /cancel."
        )
        return WAIT_SESSION_FILE

    # Check for key auth cookies
    fb_cookies = [c for c in data["cookies"] if ".facebook.com" in c.get("domain", "")]
    auth_cookies = [c for c in fb_cookies if c.get("name") in ("c_user", "xs", "fr")]

    if len(auth_cookies) < 2:
        context.user_data['pending_session'] = content
        await update.message.reply_text(
            "⚠️ Session is missing key auth cookies (`c_user`, `xs`).\n"
            "It may not work properly.\n\n"
            "Send *yes* to save anyway, or /cancel to abort.",
            parse_mode="Markdown"
        )
        return WAIT_SESSION_FILE

    _write_session(content)
    await update.message.reply_text(
        f"✅ *Session loaded!*\n\n"
        f"🍪 Facebook cookies: {len(fb_cookies)}\n"
        f"You can now use /search, /join, and /post.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def addsession_confirm_weak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "yes":
        content = context.user_data.get('pending_session', '')
        if content:
            _write_session(content)
            await update.message.reply_text("✅ Session saved. Check /status to verify.")
        else:
            await update.message.reply_text("❌ Something went wrong. Please try /addsession again.")
    else:
        await update.message.reply_text("Cancelled. Use /addsession to try a different file.")
    return ConversationHandler.END

def _validate_session(data: dict) -> str:
    if not isinstance(data, dict):
        return "File must be a JSON object."
    if "cookies" not in data:
        return "Missing required `cookies` field."
    if not isinstance(data["cookies"], list) or len(data["cookies"]) == 0:
        return "`cookies` list is empty or invalid."
    for i, c in enumerate(data["cookies"]):
        for field in ("name", "value", "domain"):
            if field not in c:
                return f"Cookie #{i+1} is missing `{field}` field."
    fb = [c for c in data["cookies"] if "facebook.com" in c.get("domain", "")]
    if not fb:
        return "No facebook.com cookies found — is this a Facebook session?"
    return ""

def _write_session(content: str):
    with open(config.SESSION_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

# ─── /login ────────────────────────────────────────────────────────────────────

@auth_required
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📧 Send your Facebook email or phone number:")
    return EMAIL

async def login_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['email'] = update.message.text.strip()
    await update.message.reply_text("🔑 Send your password:")
    return PASSWORD

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['password'] = update.message.text.strip()
    await update.message.reply_text("🔄 Logging in... Please wait (~30 seconds).")
    success = await perform_login(context.user_data['email'], context.user_data['password'], update)
    if success:
        await update.message.reply_text("✅ Login successful! Session saved.\nUse /search to find groups.")
    else:
        await update.message.reply_text("❌ Login failed. Check credentials and try /login again.")
    return ConversationHandler.END

async def perform_login(email, password, update):
    try:
        launch_args, ctx_opts = make_browser_context(storage=False)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            await page.goto("https://www.facebook.com", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Dismiss cookie popup
            for sel in [
                '[data-testid="cookie-policy-manage-dialog-accept-button"]',
                'button:has-text("Allow all cookies")',
                'button:has-text("Accept All")',
                'button:has-text("Allow Essential and Optional Cookies")',
                '[data-cookiebanner="accept_button"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await page.wait_for_timeout(1500)
                        break
                except:
                    continue

            await page.wait_for_selector('input[name="email"]', timeout=20000)
            await page.fill('input[name="email"]', "")
            await page.type('input[name="email"]', email, delay=50)
            await page.wait_for_timeout(400)
            await page.fill('input[name="pass"]', "")
            await page.type('input[name="pass"]', password, delay=50)
            await page.wait_for_timeout(600)

            # Try clicking login — 4 fallback strategies
            clicked = False
            for sel in ['button[name="login"]', 'input[type="submit"]',
                        'button:has-text("Log in")', 'button:has-text("Log In")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=3000):
                        await btn.click()
                        clicked = True
                        break
                except:
                    continue
            if not clicked:
                await page.press('input[name="pass"]', "Enter")

            await page.wait_for_timeout(10000)

            # 2FA check
            if await page.locator('input[name="approvals_code"]').count() > 0:
                await update.message.reply_text("⚠️ 2FA required — unsupported for now. Disable 2FA and retry.")
                await browser.close()
                return False

            current_url = page.url
            logged_in = (
                "facebook.com" in current_url
                and "login" not in current_url
                and "checkpoint" not in current_url
            )
            if not logged_in:
                try:
                    await page.wait_for_selector(
                        '[aria-label="Your profile"], [data-testid="nav-small-profile-pic"]',
                        timeout=5000
                    )
                    logged_in = True
                except:
                    pass

            if logged_in:
                await ctx.storage_state(path=config.SESSION_FILE)
                await browser.close()
                return True

            await browser.close()
            return False

    except Exception as e:
        print(f"Login error: {e}")
        return False

# ─── /search ───────────────────────────────────────────────────────────────────

@auth_required
@session_required
async def search_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = ' '.join(context.args).strip()
    if not keyword:
        await update.message.reply_text("Usage: /search <keyword>")
        return

    await update.message.reply_text(f"🔍 Searching groups for: *{keyword}*...", parse_mode="Markdown")

    try:
        launch_args, ctx_opts = make_browser_context()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            url = f"https://www.facebook.com/groups/search/?q={keyword.replace(' ', '%20')}"
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            groups = []
            for _ in range(config.MAX_SCROLLS):
                links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="facebook.com/groups/"]'))
                        .map(a => a.href.split('?')[0])
                        .filter(h =>
                            h.includes('/groups/') &&
                            !h.includes('/search') &&
                            !h.includes('/members') &&
                            !h.includes('/about') &&
                            !h.endsWith('/groups/')
                        )
                }""")
                for link in links:
                    if link not in groups:
                        groups.append(link)
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(2500)

            groups = list(set(groups))[:50]
            with open(config.GROUPS_FILE, 'w') as f:
                json.dump(groups, f, indent=2)

            await browser.close()

        if groups:
            await update.message.reply_text(
                f"✅ Found *{len(groups)}* groups for '{keyword}'!\n"
                f"Use /join to join them, or /post to post directly.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("⚠️ No groups found. Try a different keyword.")

    except Exception as e:
        await update.message.reply_text(f"❌ Search error: {str(e)[:200]}")

# ─── /join ─────────────────────────────────────────────────────────────────────

@auth_required
@session_required
async def start_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(config.GROUPS_FILE):
        await update.message.reply_text("❌ No groups saved. Use /search <keyword> first.")
        return
    with open(config.GROUPS_FILE) as f:
        groups = json.load(f)
    if not groups:
        await update.message.reply_text("❌ Groups list is empty.")
        return

    # Parse optional count: /join 15
    requested = config.MAX_GROUPS_PER_RUN
    if context.args:
        try:
            requested = int(context.args[0])
            if requested <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Usage: /join [number]  e.g. /join 15")
            return

    count = min(requested, len(groups), config.MAX_GROUPS_PER_RUN)

    # Reset stop flag for this user
    flag = get_stop_flag(update.effective_user.id)
    flag.clear()

    await update.message.reply_text(
        f"🚀 Starting to join *{count}* groups...\n"
        f"Send /stop at any time to stop early.",
        parse_mode="Markdown"
    )
    asyncio.create_task(run_joiner(groups, count, update))

async def run_joiner(groups, count, update):
    user_id = update.effective_user.id
    stop_flag = get_stop_flag(user_id)

    try:
        launch_args, ctx_opts = make_browser_context()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            joined = 0
            joined_urls = []

            for url in groups[:count]:
                # Check stop flag before each group
                if stop_flag.is_set():
                    await update.message.reply_text(
                        f"🛑 Stopped by user.\n✅ Joined so far: {joined}"
                    )
                    break

                try:
                    await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(random.randint(4000, 8000))

                    join_button = None
                    for sel in [
                        '[aria-label="Join group"]', 'div[aria-label="Join group"]',
                        'button:has-text("Join Group")', 'button:has-text("Join")',
                        'a:has-text("Join Group")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.count() > 0:
                                join_button = btn
                                break
                        except:
                            continue

                    if join_button:
                        await join_button.click()
                        joined += 1
                        joined_urls.append(url)
                        await update.message.reply_text(f"✅ Joined ({joined}/{count}): {url}")
                    else:
                        await update.message.reply_text(f"⚠️ Already member or closed: {url}")

                    # Interruptible sleep — checks stop flag every second
                    for _ in range(random.randint(*config.DELAY_BETWEEN_GROUPS)):
                        if stop_flag.is_set():
                            break
                        await asyncio.sleep(1)

                except Exception as e:
                    await update.message.reply_text(f"❌ Error: {str(e)[:100]}")

            # Save joined groups list for /leave
            _save_joined_groups(user_id, joined_urls)

            await ctx.storage_state(path=config.SESSION_FILE)
            await browser.close()

            if not stop_flag.is_set():
                await update.message.reply_text(
                    f"🎉 Done! Joined *{joined}/{count}* groups.\n"
                    f"Use /post to post to them, or /leave to leave all joined groups.",
                    parse_mode="Markdown"
                )

    except Exception as e:
        await update.message.reply_text(f"❌ Join error: {str(e)}")

# ─── Joined groups persistence ─────────────────────────────────────────────────

JOINED_FILE = "joined_groups.json"

def _save_joined_groups(user_id: int, new_urls: list):
    """Append newly joined group URLs to the persistent joined list."""
    existing = _load_joined_groups()
    for url in new_urls:
        if url not in existing:
            existing.append(url)
    with open(JOINED_FILE, 'w') as f:
        json.dump(existing, f, indent=2)

def _load_joined_groups() -> list:
    if not os.path.exists(JOINED_FILE):
        return []
    with open(JOINED_FILE) as f:
        try:
            return json.load(f)
        except:
            return []

# ─── /stop ─────────────────────────────────────────────────────────────────────

@auth_required
async def stop_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flag = get_stop_flag(update.effective_user.id)
    if flag.is_set():
        await update.message.reply_text("⚠️ No process is running.")
    else:
        flag.set()
        await update.message.reply_text("🛑 Stop signal sent! Will stop after the current group finishes.")

# ─── /leave ────────────────────────────────────────────────────────────────────

@auth_required
@session_required
async def start_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    joined = _load_joined_groups()
    if not joined:
        await update.message.reply_text(
            "❌ No joined groups on record.\n"
            "Groups are tracked automatically when you use /join."
        )
        return

    # Reset stop flag
    flag = get_stop_flag(update.effective_user.id)
    flag.clear()

    await update.message.reply_text(
        f"👋 Leaving *{len(joined)}* joined groups...\n"
        f"Send /stop to stop early.",
        parse_mode="Markdown"
    )
    asyncio.create_task(run_leaver(joined, update))

async def run_leaver(groups, update):
    user_id = update.effective_user.id
    stop_flag = get_stop_flag(user_id)

    try:
        launch_args, ctx_opts = make_browser_context()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            left = 0
            failed = 0
            remaining = list(groups)

            for url in groups:
                if stop_flag.is_set():
                    await update.message.reply_text(f"🛑 Stopped. Left {left} groups so far.")
                    break

                try:
                    await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(random.randint(3000, 5000))

                    # Find the "Joined" / membership button to open leave menu
                    leave_clicked = False
                    for sel in [
                        '[aria-label="Joined"]',
                        'div[aria-label="Joined"]',
                        'button:has-text("Joined")',
                        '[aria-label="Member"]',
                        'button:has-text("Member")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=4000):
                                await btn.click()
                                await page.wait_for_timeout(1500)
                                leave_clicked = True
                                break
                        except:
                            continue

                    if not leave_clicked:
                        await update.message.reply_text(f"⚠️ Not a member or can't find button: {url}")
                        failed += 1
                        continue

                    # Click "Leave group" from the dropdown
                    leave_confirmed = False
                    for sel in [
                        'span:has-text("Leave group")',
                        '[aria-label="Leave group"]',
                        'div[role="menuitem"]:has-text("Leave")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=3000):
                                await btn.click()
                                await page.wait_for_timeout(1500)
                                leave_confirmed = True
                                break
                        except:
                            continue

                    if not leave_confirmed:
                        await update.message.reply_text(f"⚠️ Couldn't find Leave option: {url}")
                        failed += 1
                        continue

                    # Confirm in the dialog if it appears
                    for sel in [
                        'div[aria-label="Leave Group"]',
                        'button:has-text("Leave Group")',
                        'div[role="dialog"] button:has-text("Leave")',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=3000):
                                await btn.click()
                                break
                        except:
                            continue

                    await page.wait_for_timeout(2000)
                    left += 1
                    remaining.remove(url)
                    await update.message.reply_text(f"👋 Left ({left}): {url}")

                    # Interruptible delay
                    for _ in range(random.randint(5, 15)):
                        if stop_flag.is_set():
                            break
                        await asyncio.sleep(1)

                except Exception as e:
                    failed += 1
                    await update.message.reply_text(f"❌ Error leaving {url}: {str(e)[:100]}")

            # Update joined_groups.json — remove successfully left groups
            with open(JOINED_FILE, 'w') as f:
                json.dump(remaining, f, indent=2)

            await ctx.storage_state(path=config.SESSION_FILE)
            await browser.close()

            if not stop_flag.is_set():
                await update.message.reply_text(
                    f"✅ *Leave complete!*\n\n"
                    f"👋 Left: {left}\n"
                    f"❌ Failed: {failed}",
                    parse_mode="Markdown"
                )

    except Exception as e:
        await update.message.reply_text(f"❌ Leave error: {str(e)}")

# ─── /post ─────────────────────────────────────────────────────────────────────

@auth_required
@session_required
async def post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(config.GROUPS_FILE):
        await update.message.reply_text("❌ No groups saved. Use /search <keyword> first.")
        return ConversationHandler.END
    with open(config.GROUPS_FILE) as f:
        groups = json.load(f)
    if not groups:
        await update.message.reply_text("❌ Groups list is empty.")
        return ConversationHandler.END

    context.user_data['post_groups'] = groups
    await update.message.reply_text(
        f"📝 You have *{len(groups)}* groups saved.\n\n"
        "Send me the *text* you want to post to all groups.\n"
        "Or send /cancel to abort.",
        parse_mode="Markdown"
    )
    return POST_TEXT

async def post_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['post_text'] = update.message.text.strip()
    await update.message.reply_text(
        "📸 Now send a *photo* to attach to the post, or send /skip to post text only.",
        parse_mode="Markdown"
    )
    return POST_IMAGE

async def post_receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Download the photo
    photo = update.message.photo[-1]  # highest resolution
    file = await photo.get_file()
    img_path = "/tmp/fb_post_image.jpg"
    await file.download_to_drive(img_path)
    context.user_data['post_image'] = img_path

    groups = context.user_data['post_groups']
    text = context.user_data['post_text']
    await update.message.reply_text(
        f"🚀 Starting to post to *{min(len(groups), config.MAX_GROUPS_PER_RUN)}* groups with image...",
        parse_mode="Markdown"
    )
    asyncio.create_task(run_poster(groups, text, img_path, update))
    return ConversationHandler.END

async def post_skip_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = context.user_data['post_groups']
    text = context.user_data['post_text']
    await update.message.reply_text(
        f"🚀 Starting to post to *{min(len(groups), config.MAX_GROUPS_PER_RUN)}* groups...",
        parse_mode="Markdown"
    )
    asyncio.create_task(run_poster(groups, text, None, update))
    return ConversationHandler.END

async def run_poster(groups, post_text, image_path, update):
    try:
        launch_args, ctx_opts = make_browser_context()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            posted = 0
            failed = 0
            for url in groups[:config.MAX_GROUPS_PER_RUN]:
                try:
                    await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(random.randint(3000, 6000))

                    # Click the "Write something..." composer box
                    composer_opened = False
                    for sel in [
                        '//span[contains(text(), "Write something...")]',
                        '//span[contains(text(), "What\'s on your mind")]',
                        '[aria-label="Write something..."]',
                        '[aria-label="Create a public post…"]',
                        '[data-testid="status-attachment-mentions-input"]',
                    ]:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=4000):
                                await el.click()
                                composer_opened = True
                                break
                        except:
                            continue

                    if not composer_opened:
                        await update.message.reply_text(f"⚠️ Can't open composer: {url}")
                        failed += 1
                        continue

                    await page.wait_for_timeout(2000)

                    # Type post text into the dialog
                    text_area = None
                    for sel in [
                        "//div[@role='dialog']//div[@contenteditable='true']",
                        "div[contenteditable='true'][role='textbox']",
                        "div[contenteditable='true']",
                    ]:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=4000):
                                text_area = el
                                break
                        except:
                            continue

                    if not text_area:
                        await update.message.reply_text(f"⚠️ Can't find text box: {url}")
                        failed += 1
                        continue

                    await text_area.click()
                    await text_area.fill(post_text)
                    await page.wait_for_timeout(1500)

                    # Attach image if provided
                    if image_path and os.path.exists(image_path):
                        try:
                            # Click photo/video button
                            for photo_sel in [
                                '[aria-label="Photo/video"]',
                                'button:has-text("Photo")',
                                '[data-testid="photo-video-button"]',
                            ]:
                                try:
                                    btn = page.locator(photo_sel).first
                                    if await btn.is_visible(timeout=3000):
                                        await btn.click()
                                        await page.wait_for_timeout(1500)
                                        break
                                except:
                                    continue

                            # Upload file
                            async with page.expect_file_chooser() as fc_info:
                                await page.locator('input[type="file"]').first.click()
                            file_chooser = await fc_info.value
                            await file_chooser.set_files(image_path)
                            await page.wait_for_timeout(4000)  # wait for upload + thumbnail
                        except Exception as img_err:
                            print(f"Image attach error: {img_err}")

                    # Click Post button
                    post_clicked = False
                    for sel in [
                        "//div[@role='dialog']//div[@aria-label='Post']",
                        "//div[@role='dialog']//div[@aria-label='Share']",
                        'button:has-text("Post")',
                        '[aria-label="Post"]',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible(timeout=4000):
                                await btn.click()
                                post_clicked = True
                                break
                        except:
                            continue

                    if not post_clicked:
                        await update.message.reply_text(f"⚠️ Couldn't click Post button: {url}")
                        failed += 1
                        continue

                    # Wait for post to complete
                    await page.wait_for_timeout(5000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except:
                        pass

                    posted += 1
                    await update.message.reply_text(f"✅ Posted ({posted}): {url}")
                    await asyncio.sleep(random.randint(*config.DELAY_BETWEEN_GROUPS))

                except Exception as e:
                    failed += 1
                    await update.message.reply_text(f"❌ Failed: {url}\n{str(e)[:120]}")

            await ctx.storage_state(path=config.SESSION_FILE)
            await browser.close()
            await update.message.reply_text(
                f"🎉 *Posting complete!*\n\n"
                f"✅ Posted: {posted}\n"
                f"❌ Failed: {failed}",
                parse_mode="Markdown"
            )

    except Exception as e:
        await update.message.reply_text(f"❌ Post session error: {str(e)}")

# ─── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            EMAIL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, login_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    post_conv = ConversationHandler(
        entry_points=[CommandHandler('post', post_start)],
        states={
            POST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_receive_text)],
            POST_IMAGE: [
                MessageHandler(filters.PHOTO, post_receive_image),
                CommandHandler('skip', post_skip_image),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    addsession_conv = ConversationHandler(
        entry_points=[CommandHandler('addsession', addsession_start)],
        states={
            WAIT_SESSION_FILE: [
                MessageHandler(filters.Document.FileExtension("json"), addsession_receive),
                MessageHandler(filters.TEXT & ~filters.COMMAND, addsession_confirm_weak),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(login_conv)
    app.add_handler(addsession_conv)
    app.add_handler(CommandHandler("search", search_groups))
    app.add_handler(CommandHandler("join", start_join))
    app.add_handler(CommandHandler("stop", stop_process))
    app.add_handler(CommandHandler("leave", start_leave))
    app.add_handler(post_conv)

    print("🤖 Telegram Bot Started... Ready!")
    app.run_polling()

if __name__ == "__main__":
    main()
