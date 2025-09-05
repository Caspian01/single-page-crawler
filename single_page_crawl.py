import asyncio
import sys
import pandas as pd
import streamlit as st
import plotly.express as px
from urllib.parse import urljoin, urlparse
import subprocess
import os

# Fix for Windows event loop policy issue
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def install_playwright_browsers():
    """Install Playwright browsers if they don't exist"""
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], 
                      check=True, capture_output=True, text=True)
        subprocess.run([sys.executable, "-m", "playwright", "install-deps"], 
                      check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        st.error(f"Failed to install browsers: {e}")
        return False
    except Exception as e:
        st.error(f"Error installing browsers: {e}")
        return False

# Try to import playwright, install browsers if needed
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    st.error("Playwright is not installed. Please install it using: pip install playwright")
    PLAYWRIGHT_AVAILABLE = False

class LinkCrawler:
    def __init__(self, headless=True, timeout=300000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None

    async def __aenter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise Exception("Playwright is not available")
        
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
        except Exception as e:
            # Try to install browsers if launch fails
            if "Executable doesn't exist" in str(e):
                st.info("Installing browser dependencies... This may take a moment.")
                if install_playwright_browsers():
                    # Retry after installation
                    self.playwright = await async_playwright().start()
                    self.browser = await self.playwright.chromium.launch(headless=self.headless)
                else:
                    raise Exception("Could not install required browser dependencies")
            else:
                raise e
                
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
            await page.goto(source_url, wait_until="networkidle")
            await page.wait_for_timeout(5000)  # wait for dynamic content

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


async def main(source_url: str):
    source_urls = [source_url]
    print("Playwright Link Crawler")
    print("=" * 50)

    async with LinkCrawler(headless=True) as crawler:
        results = await crawler.crawl_multiple_sources(source_urls)
    return results


def process_results(results, source_url, limit):
    """Turn crawl results into a DataFrame for display"""
    link_list = results.get(source_url, [])

    if not link_list:
        return pd.DataFrame(columns=["init_url", "anchor_text", "href", "count"])

    df = pd.DataFrame(link_list)
    # Exclude [No text]
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

# Check if Playwright is available
if not PLAYWRIGHT_AVAILABLE:
    st.error("❌ Playwright is not installed. Please install it first.")
    st.stop()

st.sidebar.title("Crawler Settings")
url_input = st.sidebar.text_input("Enter a website URL to crawl:")
result_limit = st.sidebar.number_input("Max results to show:", min_value=1, max_value=100, value=10, step=1)
run_crawl = st.sidebar.button("🚀 Run Crawler")

if run_crawl and url_input:
    with st.spinner("Crawling website... please wait ⏳"):
        try:
            results = asyncio.run(main(url_input))
            df = process_results(results, url_input, result_limit)
        except Exception as e:
            st.error(f"❌ Error: {e}")
            st.info("💡 If you're seeing browser installation errors, try restarting the app.")
            df = pd.DataFrame()

    if not df.empty:
        st.subheader("Key Metrics")

        total_links = df["count"].sum()
        unique_texts = df["anchor_text"].nunique()
        top_anchor = df.iloc[0]["anchor_text"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Links", total_links)
        col2.metric("Unique Anchor Texts", unique_texts)
        col3.metric("Top Anchor Text", top_anchor)

        st.subheader("Anchor Text Distribution")

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
        st.info("No data available yet. Try crawling first.")