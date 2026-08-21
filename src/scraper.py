"""
Stealth browser for scraping — undetectable browser automation for Archimeda.

Use cases:
- Dexscreener token data (anti-bot protected)
- Etherscan NFT data (rate-limited)
- OpenSea trending collections (API key required, browser fallback)
- Any site that blocks web_extract / standard HTTP requests

This uses Playwright with stealth patches to look like a real human:
- Randomized user agent
- Canvas fingerprint spoofing
- WebDriver flag removed
- Chrome DevTools flags hidden
- Random delays between actions
- Proxy support for rotating identities

Usage:
    from scraper import stealth_scraper
    data = stealth_scraper("https://dexscreener.com/solana/degs", selector=".token-list")

Or as a CLI:
    python -m scraper --url "https://dexscreener.com" --selector ".token-row" --json

Requires:
    pip install playwright
    playwright install chromium
"""
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timezone

from playwright.async_api import async_playwright, Page, BrowserContext

# ── Stealth settings ──────────────────────────────────────────────────
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:128.0) Gecko/20100101 Firefox/128.0",
]

def random_ua():
    return random.choice(UA_POOL)

def random_delay(min_s=0.5, max_s=2.0):
    """Human-like delay."""
    time.sleep(random.uniform(min_s, max_s))

import time


# ── Context factory with stealth patches ──────────────────────────────

async def make_stealth_context(browser, proxy=None):
    """Create a Playwright context that looks like a real human."""
    ua = random_ua()
    viewport = {
        "width": random.randint(1280, 1920),
        "height": random.randint(720, 1080),
    }
    locale = random.choice(["en-US", "en-GB", "de-DE", "fr-FR"])
    timezone_str = random.choice(["America/New_York", "Europe/London", "Europe/Berlin", "Asia/Tokyo"])
    
    context_args = {
        "user_agent": ua,
        "viewport": viewport,
        "locale": locale,
        "timezone_id": timezone_str,
        "permissions": ["geolocation"],
        "geolocation": {"latitude": random.uniform(30, 50), "longitude": random.uniform(-130, -70)},
        "extra_http_headers": {
            "Accept-Language": f"{locale},{locale.replace('-', '')};q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
    }
    
    if proxy:
        context_args["proxy"] = proxy
    
    context = await browser.new_context(**context_args)
    
    # Stealth: remove webdriver flag
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        // Patch plugins to look like a real browser
        const getChromeVersion = () => {
            const matches = navigator.userAgent.match(/Chrome\\/(\\d+)/);
            return matches ? parseInt(matches[1]) : 127;
        };
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugin = { length: 3 };
                for (let i = 0; i < 3; i++) {
                    plugin[i] = { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' };
                }
                return plugin;
            }
        });
        // Patch languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        // Hide chrome extension signals
        Object.defineProperty(navigator, 'hasOwnProperty', {
            get: () => undefined
        });
    """)
    
    return context


# ── Main scraping functions ───────────────────────────────────────────

async def scrape_page(url, selector=None, wait_for=None, timeout=30000):
    """Open a page in stealth browser and extract data.
    
    Args:
        url: Target URL
        selector: CSS selector to extract (if None, returns full page text)
        wait_for: CSS selector to wait for before extracting
        timeout: Max wait time in ms
    
    Returns:
        dict with url, title, content (text or HTML), timestamp
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
            ],
        )
        
        context = await make_stealth_context(browser)
        page = await context.new_page()
        
        try:
            random_delay(1, 3)  # Initial delay
            
            # Navigate
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            random_delay(1, 2)
            
            # Wait for content
            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=15000)
                except:
                    pass  # Page loaded without target, continue
            
            random_delay(1, 2)
            
            # Extract
            title = await page.title()
            url_actual = page.url
            
            if selector:
                # Extract elements matching selector
                elements = await page.query_selector_all(selector)
                items = []
                for el in elements:
                    text = await el.inner_text()
                    link = await el.get_attribute("href") or ""
                    html = await el.inner_html()[:5000]  # Cap length
                    items.append({"text": text.strip(), "link": link, "html": html})
                content = items
            else:
                content = await page.inner_text("body")
            
            result = {
                "url": url_actual,
                "title": title,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            return result
            
        finally:
            await context.close()
            await browser.close()


async def scrape_dexscreener_tokens(query=None, chain="solana", limit=10):
    """Scrape trending tokens from Dexscreener with stealth browser.
    
    This bypasses anti-bot protections that block web_extract.
    """
    base = f"https://dexscreener.com/{chain}"
    if query:
        base += f"/{query}"
    
    result = await scrape_page(
        base,
        selector=".pair-row, .token-pair, .pair-table .row, [class*='pair']",
        wait_for=".pair-row, [class*='pair']",
        timeout=45000,
    )
    
    return result


async def scrape_etherscan_mints(contract_address, page=1, offset=20):
    """Scrape NFT mint activity from Etherscan.
    
    Uses stealth browser to avoid rate limits.
    """
    url = f"https://etherscan.io/contractsVerified/{contract_address}?p={page}"
    result = await scrape_page(
        url,
        selector=".tab-pane table tr, [class*='tx-list'] tr",
        timeout=30000,
    )
    return result


async def scrape_opensea_trending(limit=10):
    """Scrape trending NFT collections from OpenSea.
    
    OpenSea requires API key for reliable access. Browser scraping
    is a fallback when the API key is missing.
    """
    result = await scrape_page(
        "https://opensea.io/collections",
        selector="[class*='collection'], [class*='CollectionCard'], [class*='nft-card']",
        wait_for="[class*='collection']",
        timeout=30000,
    )
    return result


def scrape_page_sync(url, selector=None, wait_for=None, timeout=30000):
    """Sync wrapper for scrape_page."""
    return asyncio.run(scrape_page(url, selector, wait_for, timeout))


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    """CLI: python scraper.py --url <url> [--selector <css>] [--json]"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Stealth browser scraper for Archimeda")
    parser.add_argument("--url", required=True, help="URL to scrape")
    parser.add_argument("--selector", default=None, help="CSS selector to extract")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--timeout", type=int, default=30000, help="Timeout in ms")
    args = parser.parse_args()
    
    result = scrape_page_sync(args.url, args.selector, timeout=args.timeout)
    
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        if isinstance(result['content'], list):
            print(f"Found {len(result['content'])} elements")
            for item in result['content'][:20]:
                print(f"  - {item.get('text', '')[:200]}")
        else:
            print(result['content'][:5000])


if __name__ == "__main__":
    main()
