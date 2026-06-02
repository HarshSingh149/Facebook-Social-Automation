from playwright.async_api import async_playwright
import asyncio
import random
import json
import os
from config import MIN_DELAY, MAX_DELAY, MAX_GROUPS_PER_RUN, SCROLL_PAUSE, MAX_SCROLLS, SESSION_FILE

async def save_session(context):
    await context.storage_state(path=SESSION_FILE)

async def login_facebook(page, email, password, twofa_code=None):
    await page.goto("https://www.facebook.com/")
    await page.wait_for_timeout(3000)
    await page.fill('input[name="email"]', email)
    await page.fill('input[name="pass"]', password)
    await page.click('button[name="login"]')
    await page.wait_for_timeout(8000)

    # Handle 2FA if needed
    if twofa_code:
        try:
            await page.fill('input[name="approvals_code"]', twofa_code)
            await page.click('button[name="submit[Submit Code]"]')
            await page.wait_for_timeout(5000)
        except:
            pass

    # Save session
    await save_session(page.context)
    return True

async def search_groups(page, keyword):
    url = f"https://www.facebook.com/groups/search/?q={keyword.replace(' ', '%20')}"
    await page.goto(url)
    await page.wait_for_timeout(5000)

    groups = []
    for _ in range(MAX_SCROLLS):
        group_links = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a[href*="facebook.com/groups/"]'))
                .map(a => a.href)
                .filter(href => href.includes('/groups/') && !href.includes('/search/'))
        }''')

        for link in group_links:
            if link not in groups:
                groups.append(link)

        await page.evaluate('window.scrollBy(0, 800)')
        await page.wait_for_timeout(SCROLL_PAUSE * 1000)

    return list(set(groups))[:50]

async def join_groups(group_urls, update_callback=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        context_options = {}
        if os.path.exists(SESSION_FILE):
            context_options['storage_state'] = SESSION_FILE

        context = await browser.new_context(**context_options)
        page = await context.new_page()

        joined = 0
        for url in group_urls[:MAX_GROUPS_PER_RUN]:
            try:
                await page.goto(url, timeout=60000)
                await asyncio.sleep(random.uniform(4, 8))

                try:
                    join_button = await page.wait_for_selector(
                        '[aria-label="Join group"], button:has-text("Join")',
                        timeout=8000
                    )
                except:
                    join_button = None

                if join_button:
                    await join_button.click()
                    joined += 1
                    if update_callback:
                        await update_callback(f"✅ Joined group: {url}")
                else:
                    if update_callback:
                        await update_callback(f"⚠️ Already member or can't join: {url}")

                await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

            except Exception as e:
                if update_callback:
                    await update_callback(f"❌ Error with {url}: {str(e)[:100]}")

        await save_session(context)
        await browser.close()
        return joined
