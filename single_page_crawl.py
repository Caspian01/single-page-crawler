import asyncio
import sys
import pandas as pd
import streamlit as st
import plotly.express as px
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import time
import subprocess
import os

# Fix for Windows event loop policy issue
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Check if we're in Streamlit Cloud or similar environment
IS_CLOUD_DEPLOYMENT = os.environ.get('STREAMLIT_SHARING_MODE') == 'true' or 'streamlit.app' in os.environ.get('HOSTNAME', '')

class SimpleHTTPCrawler:
    """Fallback crawler using requests and BeautifulSoup"""
    
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def normalize_url(self, url):
        """Normalize URL by removing trailing slashes, fragments, and query params"""
        if not url:
            return ""
        return url.rstrip("/").split("#")[0].split("?")[0]

    def get_links(self, source_url):
        """Extract all internal links from a page using requests"""
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        new_source_url = f"{parsed.scheme}://{domain}"

        try:
            response = self.session.get(source_url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            links = soup.find_all(['a', 'link'], href=True)
            
            print(f"Found {len(links)} total links")

            link_list = []
            for link in links:
                try:
                    href = link.get('href')
                    anchor_text = link.get_text(strip=True) if link.name == 'a' else link.get('title', '')
                    
                    if not href or not anchor_text:
                        continue
                    elif new_source_url in href:
                        absolute_url = urljoin(source_url, href)
                        normalized_url = self.normalize_url(absolute_url)

                        element_info = {
                            "init_url": source_url,
                            "anchor_text": anchor_text,
                            "href": normalized_url,
                            "is_visible": True,  # Assume visible for HTTP crawler
                            "tag_name": link.name,
                            "class": ' '.join(link.get('class', [])),
                            "id": link.get('id', ''),
                            "title": link.get('title', ''),
                            "target": link.get('target', ''),
                        }
                        link_list.append(element_info)
                except Exception as e:
                    print(f"Error processing link: {e}")
                    continue

            return link_list

        except Exception as e:
            print(f"Error loading {source_url}: {e}")
            raise e

    def crawl_multiple_sources(self, source_urls):
        all_results = {}
        for source_url in source_urls:
            print(f"\nCrawling: {source_url}")
            results = self.get_links(source_url)
            all_results[source_url] = results
        return all_results

# Try to import Playwright for full functionality
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    
    # Test if browsers are available
    try:
        result = subprocess.run([
            sys.executable, "-m", "playwright", "show-browsers"
        ], capture_output=True, text=True, timeout=5)
        PLAYWRIGHT_AVAILABLE = result.returncode == 0 and "chromium" in result.stdout.lower()
    except:
        PLAYWRIGHT_AVAILABLE = False
        
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

class LinkCrawler:
    def __init__(self, headless=True, timeout=30000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None

    async def __aenter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise Exception("Playwright not available")
            
        self.playwright = await async_playwright().start()
        
        # Launch browser with additional args for cloud deployment
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ]
        )
        
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    def normalize_url(self, url):
        """Normalize URL by removing trailing slashes, fragments, and query params"""
        if not url:
            return ""
        return url.rstrip("/").split("#")[0].split("?")[0]

    async def get_links(self, source_url):
        """Extract all internal links from a page"""
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        new_source_url = f"{parsed.scheme}://{domain}"

        try:
            page = await self.context.new_page()
            page.set_default_timeout(self.timeout)

            print(f"Loading source page: {source_url}")
            await page.goto(source_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            links = await page.query_selector_all("[href]")
            print(f"Found {len(links)} total links")

            link_list = []
            for link in links:
                try:
                    href = await link.get_attribute("href")
                    anchor_text = await link.inner_text()
                    anchor_text = anchor_text.strip() if anchor_text else None

                    if not href or not anchor_text:
                        continue
                    elif new_source_url in href:
                        absolute_url = urljoin(source_url, href)
                        normalized_url = self.normalize_url(absolute_url)

                        element_info = {
                            "init_url": source_url,
                            "anchor_text": anchor_text,
                            "href": normalized_url,
                            "is_visible": await link.is_visible(),
                            "tag_name": await link.evaluate("el => el.tagName.toLowerCase()"),
                            "class": await link.get_attribute("class") or "",
                            "id": await link.get_attribute("id") or "",
                            "title": await link.get_attribute("title") or "",
                            "target": await link.get_attribute("target") or "",
                        }
                        link_list.append(element_info)
                except Exception as e:
                    print(f"Error processing link: {e}")
                    continue

            await page.close()
            return link_list

        except Exception as e:
            print(f"Error loading {source_url}: {e}")
            return []

    async def crawl_multiple_sources(self, source_urls):
        all_results = {}
        for source_url in source_urls:
            print(f"\nCrawling: {source_url}")
            results = await self.get_links(source_url)
            all_results[source_url] = results
        return all_results


async def main_playwright(source_url: str):
    """Main function using Playwright"""
    source_urls = [source_url]
    print("Playwright Link Crawler")
    print("=" * 50)

    async with LinkCrawler(headless=True) as crawler:
        results = await crawler.crawl_multiple_sources(source_urls)
    return results


def main_http(source_url: str):
    """Main function using HTTP requests"""
    source_urls = [source_url]
    print("HTTP Link Crawler")
    print("=" * 50)

    crawler = SimpleHTTPCrawler()
    results = crawler.crawl_multiple_sources(source_urls)
    return results


def process_results(results, source_url, limit):
    """Turn crawl results into a DataFrame for display"""
    link_list = results.get(source_url, [])

    if not link_list:
        return pd.DataFrame(columns=["init_url", "anchor_text", "href", "count"])

    df = pd.DataFrame(link_list)
    # Exclude [No text] and empty anchor texts
    df = df[df["anchor_text"].str.strip() != ""]
    df = df[df["anchor_text"] != "[No text]"]

    # Group by anchor_text
    df_grouped = (
        df.groupby(["init_url", "anchor_text", "href"])
        .size()
        .reset_index(name="count")
        .sort_values(by="count", ascending=False)
        .head(limit)
    )
    return df_grouped


# ================= STREAMLIT APP ===================
st.set_page_config(page_title="🔗 Link Distribution Dashboard", layout="wide")
st.title("🔗 Link Distribution Dashboard")

# Show crawler mode
if PLAYWRIGHT_AVAILABLE:
    st.success("🚀 Advanced mode: Using Playwright (can handle JavaScript)")
else:
    st.info("⚡ Basic mode: Using HTTP requests (faster, but no JavaScript support)")

st.sidebar.title("Crawler Settings")
url_input = st.sidebar.text_input("Enter a website URL to crawl:")
result_limit = st.sidebar.number_input("Max results to show:", min_value=1, max_value=100, value=10, step=1)
run_crawl = st.sidebar.button("🚀 Run Crawler")

if run_crawl and url_input:
    with st.spinner("Crawling website... please wait ⏳"):
        try:
            if PLAYWRIGHT_AVAILABLE:
                results = asyncio.run(main_playwright(url_input))
            else:
                results = main_http(url_input)
            df = process_results(results, url_input, result_limit)
        except Exception as e:
            st.error(f"❌ Error: {e}")
            st.info("💡 Make sure the URL is accessible and try again.")
            df = pd.DataFrame()

    if not df.empty:
        st.subheader("📊 Key Metrics")

        total_links = df["count"].sum()
        unique_texts = df["anchor_text"].nunique()
        top_anchor = df.iloc[0]["anchor_text"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Links", total_links)
        col2.metric("Unique Anchor Texts", unique_texts)
        col3.metric("Top Anchor Text", top_anchor[:50] + "..." if len(top_anchor) > 50 else top_anchor)

        st.subheader("📈 Anchor Text Distribution")

        fig = px.pie(
            df,
            values="count",
            names="anchor_text",
            title="Anchor Text Distribution",
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("🔍 Show Anchor Text Details"):
            st.dataframe(df, use_container_width=True, hide_index=True)

    else:
        st.info("ℹ️ No data available yet. Enter a URL and click 'Run Crawler' to get started!")

# Add some helpful information
with st.expander("ℹ️ How to use"):
    st.markdown("""
    1. **Enter a URL** in the sidebar (e.g., https://example.com)
    2. **Set the result limit** for how many results to display
    3. **Click 'Run Crawler'** to start analyzing links
    
    The tool will:
    - Extract all internal links from the page
    - Count occurrences of each anchor text
    - Display results in an interactive pie chart
    - Show detailed data in a table
    
    **Crawler Modes:**
    - **Advanced (Playwright)**: Handles JavaScript-rendered content
    - **Basic (HTTP)**: Faster, works with static HTML content
    """)

with st.expander("🔧 Troubleshooting"):
    st.markdown("""
    **Common issues:**
    - **403/404 errors**: The website blocks automated requests
    - **No results**: The page might not have internal links
    - **Slow loading**: Some sites take time to respond
    
    **Tips:**
    - Try different websites if one doesn't work
    - Basic mode is more reliable but less comprehensive
    - Some sites block crawlers - this is normal
    """)