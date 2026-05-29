# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 5/28/2026
# File Title: scraper.py
# Purpose: Scrapes meet results from Athletic.net and saves them to a 
# JSON file for later use in the predictor


import asyncio
from playwright.async_api import async_playwright
import json
from database import createTables, saveAthlete, saveResult, saveMeet, countRows

# getMeetResults
# Purpose: Function to scrape meet results from Athletic.net for a given 
# race and division
# arguments: page (Playwright page object), (should be ints) meet_id is the ID of the meet, div_id is the 
# ID of the race division, jwt_token is the authentication token we get from the cookies to
# avoid a 403 error when we call the API.
# output: list of dictionaries, one per athlete. Each dictionary contains 
# the athlete's name, time, and other relevant info. This is the raw 
# data that will be used to train the predictor.
async def getMeetResults(page, meet_id: int, div_id: int, jwt_token: str):

    # Navigate to the meet results page for the specified meet and division.
    await page.goto(f"https://www.athletic.net/CrossCountry/meet/{meet_id}/results/{div_id}")
    await page.wait_for_timeout(3000)

    # Use page.evaluate to run JavaScript in the context of the page to 
    # fetch the results data from the API endpoint. Avoids issues with the 
    # page navigating away before we can read the data.
    data = await page.evaluate("""
        // Comments here are double slashes as this is JS.
        // This function runs in the browser context, so we can use 
        // fetch to call the API directly. async allows us to 
        // wait for the response before returning.
        // Pass div_id as an argument rather than concatenating it into the string.
        // This avoids syntax errors and is cleaner.
        async (args) => {
            // Call the API endpoint that returns the results data for the 
            // specified division.
            // Contains three parameters of a HTTP request. Post - sned data.
            // Headers - what we're sending. Body - data we're sending, the divId.
            const response = await fetch('/api/v1/Meet/GetResultsData3', {
                method: 'POST',
                 headers: {
                    'Content-Type': 'application/json',
                    'anettokens': args.token,
                    'anet-appinfo': 'web:web:0:240'
                },
                body: JSON.stringify({divId: args.divId})
            });
            // Return it to the Python context as a JavaScript object, 
            // which will be converted to a Python dictionary.
            return {
                status: response.status,
                text: await response.text()
            };
        }
    """, {"divId": div_id, "token": jwt_token})  # Pass div_id as an argument to the function. jwt_token is the authentication token we got from the cookies so we don't get a 403 error.

    # Parse the results from the response text
    import json
    parsed = json.loads(data.get('text', '{}'))
    return parsed.get("resultsXC", [])


# getMeetData
# Purpose: Fetches meet metadaata and division list from athletic.net for a
# given meet ID. Returns meet info.
# Arguments: page is the Playwright page object. We pass it so we don't have
# to keep opening a new page.meet_id is the ID of the meet to fetch data for.
# Output: Makes a tuple of meet_info and divisions.
# meet_info is a dictionary with keys: meet_id, div_id, meet_name,
# course_name, distance, gps_lat, gps_long, state. 
# divisions is a list of dictionaries, each with keys: IDMeetDiv, Distance.
async def getMeetData(page, meet_id: int):

    # Navigate to the meet info page for the specified meet ID to get valid
    # cookies and tokens.
    await page.goto(f"https://www.athletic.net/CrossCountry/meet/{meet_id}/info")
    await page.wait_for_timeout(3000)

    # Make the API call directly from the browser context.
    # This uses the browser's own cookies and tokens, so we don't have 
    # to worry about authentication issues.
    data = await page.evaluate("""
        async (meetId) => {
            const response = await fetch('/api/v1/Meet/GetMeetData?meetId=' + meetId + '&sport=xc');
            return await response.json();
        }
    """, meet_id)  # Pass meet_id as an argument to the function to avoid syntax issue with string concatenation.

    # Extract meet info and divisions from the API response
    meet_info = data.get("meet", {})
    meet_info["jwtMeet"] = data.get("jwtMeet", "") # Add the jwt token to the meet_info dictionary so we can use it later to authenticate our results API request.
    divisions = data.get("xcDivisions", [])

    return meet_info, divisions

# Tests the scraper
async def main():

    createTables()

    meet_id = 269765
    
    async with async_playwright() as p:

        # Opens a new chrome browser, headless=False so browser is visible. 
        # args to make it less detectable as a bot.
        browser = await p.chromium.launch(
            headless = False,
            args = ["--disable-blink-features=AutomationControlled"]
        )

        # Fresh browser profile to make it less detectable as a bot, 
        # with user agent spoofing to look like a real browser.
        context = await browser.new_context(
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport = {"width": 1280, "height": 800},
            java_script_enabled = True
        )

        # Opens a new tabe in the browser, this is where we will navigate to 
        # the meet results page and scrape the data
        page = await context.new_page()


        # Remove webdriver property to make it less detectable as a bot.
        # For a bot it would be set to true.
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # Step 1: fetch meet metadata and division list
        meet_data, divisions = await getMeetData(page, meet_id)
        print(f"Meet: {meet_data.get('Name')} on {meet_data.get('MeetDate')}")
        print(f"Found {len(divisions)} divisions")

        # Step 2: save each division to the meets table
        for div in divisions:
            saveMeet(meet_data, div)

        # Get jwt_token to authenticate API request. This is necessary to 
        # avoid a 403 error when we try to fetch the results data.
        jwt_token = meet_data.get("jwtMeet", "")
        # Step 3: fetch and save results for every division in the meet
        for div in divisions:
            div_id = div["IDMeetDiv"]
            print(f"Scraping {div['DivName']} - {div_id}")
            results = await getMeetResults(page, meet_id, div_id, jwt_token)
            print(f"Got {len(results)} results")

            # Step 4: save each athlete and result to the database
            for result in results:
                if not result.get("AthleteID"):
                    print(f"Skipping result with no AthleteID")
                    continue
                saveAthlete(result)
                saveResult(result, meet_data)

        print("done - check xc.db to verify data was saved")

        countRows()

        await browser.close()

# Only runs main() if this script is run directly, not if it's imported.
if __name__ == "__main__":
    asyncio.run(main())