# This code tests scrape_results flow before doing it on all meets.
import asyncio
from playwright.async_api import async_playwright
from scraper import getMeetData, getMeetResults


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        await page.goto("https://www.athletic.net")
        await page.wait_for_timeout(2000)

        meet_info, divisions = await getMeetData(page, 269765)
        print("MEET INFO:")
        print(meet_info)

        results = await getMeetResults(page, 269765, divisions[0]["IDMeetDiv"], meet_info["jwtMeet"])
        print("FIRST RESULT:")
        print(results[0])

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())