# This file is for any code temporarily need to change values in the db.
# test_tf.py
# Purpose: Tests whether Playwright can successfully call GetMeetData
#          for a known TF meet and get a valid response back.

import asyncio
from playwright.async_api import async_playwright
from scraper import getMeetDataTF, getMeetResultsTF

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TEST_MEET_ID = 617289

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path=CHROME_PATH
        )
        page = await browser.new_page()

        client = await page.context.new_cdp_session(page)
        await client.send("Network.enable")

        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(page)
        
        meet_info, events_dict, event_divs = await getMeetDataTF(page, TEST_MEET_ID)
        if not meet_info:
            print("getMeetDataTF failed")
            return
            
        print(f"Got {len(event_divs)} combos, {len(events_dict)} events")
        
        jwt_token = meet_info.get("jwtMeet", "")
        for event_div in event_divs[:3]:
            event_id = event_div.get("e")
            div_id = event_div.get("d")
            event_info = events_dict.get(event_id)
            if not event_info:
                continue
            results = await getMeetResultsTF(
                page, TEST_MEET_ID, div_id,
                event_info["event_short"], event_info["gender"], jwt_token
            )
            print(f"{event_info['event_short']} div {div_id}: {len(results)} results")

        input("Press Enter to close...")
        await browser.close()

asyncio.run(test())