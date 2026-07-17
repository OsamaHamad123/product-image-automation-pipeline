# diagnose_search.py
import asyncio
import aiohttp
import urllib.parse
from bs4 import BeautifulSoup
import json
import config

query = "Nellara Matta Rice"

async def test_google():
    print("\n--- Testing Google Custom Search API ---")
    key = config.GOOGLE_SEARCH_API_KEY
    cx = config.GOOGLE_SEARCH_CX
    print(f"Key: {key[:8]}... | CX: {cx[:8]}...")
    if not key or not cx:
        print("❌ Google Search API Key or CX is missing in .env!")
        return
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": key,
        "cx": cx,
        "q": query,
        "searchType": "image",
        "gl": "ae"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as r:
                print(f"Status Code: {r.status}")
                if r.status == 200:
                    data = await r.json()
                    items = data.get("items", [])
                    print(f"✅ Success! Found {len(items)} results.")
                    if items:
                        print(f"Sample Result Title: {items[0].get('title')}")
                        print(f"Sample Result URL: {items[0].get('link')}")
                else:
                    text = await r.text()
                    print(f"❌ Failed. Response: {text}")
    except Exception as e:
        print(f"❌ Exception: {e}")

async def test_bing_scrape():
    print("\n--- Testing Bing Scrape (curl_cffi) ---")
    try:
        from curl_cffi.requests import AsyncSession
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://www.bing.com/images/search?q={encoded_query}&cc=AE"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with AsyncSession(impersonate="chrome120") as s:
            response = await s.get(url, headers=headers, timeout=10)
            print(f"Status Code: {response.status_code}")
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                links = soup.find_all("a", class_="iusc")
                print(f"✅ Success! Found {len(links)} results.")
            else:
                print(f"❌ Failed (Blocked/Captcha). Status code: {response.status_code}")
    except Exception as e:
        print(f"❌ Exception: {e}")

async def test_duckduckgo_scrape():
    print("\n--- Testing DuckDuckGo Scrape (curl_cffi) ---")
    try:
        from curl_cffi.requests import AsyncSession
        async with AsyncSession(impersonate="chrome120") as session:
            encoded_query = urllib.parse.quote_plus(query)
            url_init = f"https://duckduckgo.com/?q={encoded_query}&iax=images&ia=images"
            response = await session.get(url_init, timeout=10)
            print(f"Init Status Code: {response.status_code}")
            import re
            match = re.search(r'vqd=["\']([0-9-]+)["\']', response.text)
            if not match:
                print("❌ Failed to get vqd token (DuckDuckGo blocked/captcha).")
                return
            vqd = match.group(1)
            api_url = "https://duckduckgo.com/i.js"
            params = {"l": "us-en", "o": "json", "q": query, "vqd": vqd, "f": ",,,", "p": "1"}
            response2 = await session.get(api_url, params=params, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=10)
            print(f"API Status Code: {response2.status_code}")
            if response2.status_code == 200:
                payload = response2.json()
                results = payload.get("results", [])
                print(f"✅ Success! Found {len(results)} results.")
            else:
                print(f"❌ API Request failed.")
    except Exception as e:
        print(f"❌ Exception: {e}")

async def main():
    await test_google()
    await test_bing_scrape()
    await test_duckduckgo_scrape()

if __name__ == "__main__":
    asyncio.run(main())
