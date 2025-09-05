import asyncio
import sys
import os
import subprocess
import pandas as pd
import streamlit as st
import plotly.express as px
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# Windows fix
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# =============================
# Try importing Playwright
# =============================
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "show-browsers"],
            capture_output=True,
            text=True,
            timeout=5
        )
        PLAYWRIGHT_AVAILABLE = result.returncode == 0 and "chromium" in result.stdout.lower()
    except Exception:
        PLAYWRIGHT_AVAILABLE = False

except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# =============================
# Playwright Link Crawler
# =============================
class PlaywrightCrawler:
    def __init__(self, headless=True, timeout=30000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor"
            ],
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
        if not url:
            return ""
        return url.rstrip("/").split("#")[0].split("?")[0]

    async def get_links(self, source_url):
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        base_url = f"{parsed.scheme}://{domain}"

        try:
            page = await self.context.new_page()
            page.set_default_timeout(self.timeout)

            await page.goto(source_url, wait_until="networkidle")
            await page.wait_for_timeout(3000)

            elements = await page.query_selector_all("[href]")

            link_list = []
            for el in elements:
                try:
                    href = await el.get_attribute("href")
                    anchor_text = (await el.inner_text() or "").strip()

                    if not href or not anchor_text:
                        continue
                    if base_url in href:
                        absolute_url = urljoin(source_url, href)
                        normalized_url = self.normalize_url(absolute_url)

                        link_list.append({
                            "init_url": source_url,
                            "anchor_text": anchor_text,
                            "href": normalized_url,
                            "is_visible": await el.is_visible(),
                            "tag_name": await el.evaluate("el => el.tagName.toLowerCase()"),
                            "class": await el.get_attribute("class") or "",
                            "id": await el.get_attribute("id") or "",
                            "title": await el.get_attribute("title") or "",
                            "target": await el.get_attribute("target") or "",
                        })
                except:
                    continue

            await page.close()
            return link_list

        except Exception as e:
            print(f"Error loading {source_url}: {e}")
            return []

    async def crawl(self, source_url):
        return {source_url: await self.get_links(source_url)}


# =============================
# Fallback HTTP Crawler
# =============================
class SimpleHTTPCrawler:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            )
        })

    def normalize_url(self, url):
        if not url:
            return ""
        return url.rstrip("/").split("#")[0].split("?")[0]

    def get_links(self, source_url):
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        base_url = f"{parsed.scheme}://{domain}"

        try:
            resp = self.session.get(source_url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")

            link_list = []
            for el in soup.find_all(["a", "link"], href=True):
                href = el.get("href")
                anchor_text = el.get_text(strip=True) or el.get("title", "")

                if not href or not anchor_text:
                    continue
                if base_url in href:
                    absolute_url = urljoin(source_url, href)
                    normalized_url = self.normalize_url(absolute_url)

                    link_list.append({
                        "init_url": source_url,
                        "anchor_text": anchor_text,
                        "href": normalized_url,
                        "is_visible": True,
                        "tag_name": el.name,
                        "class": " ".join(el.get("class", [])),
                        "id": el.get("id", ""),
                        "title": el.get("title", ""),
                        "target": el.get("target", ""),
                    })
            return {source_url: link_list}
        except Exception as e:
            print(f"Error loading {source_url}: {e}")
            return {source_url: []}


# =============================
# Helpers
# =============================
def process_results(results, source_url, limit, exact_anchor=None):
    links = results.get(source_url, [])
    if not links:
        return pd.DataFrame(columns=["init_url", "anchor_text", "href", "count"])

    df = pd.DataFrame(links)
    if "anchor_text" not in df.columns:
        df["anchor_text"] = ""
    df["anchor_text"] = df["anchor_text"].astype(str).str.strip()

    # only visible
    if "is_visible" in df.columns:
        df = df[df["is_visible"] == True]

    # no empty or [No text]
    df = df[df["anchor_text"] != ""]
    df = df[df["anchor_text"] != "[No text]"]

    # exact filter
    if exact_anchor:
        df = df[df["anchor_text"] == exact_anchor.strip()]

    grouped = df.groupby(["init_url", "anchor_text", "href"]).size().reset_index()
    grouped = grouped.rename(columns={0: "count"})
    grouped = grouped.sort_values("count", ascending=False).head(limit)

    return grouped[["init_url", "anchor_text", "href", "count"]]


# =============================
# Streamlit UI
# =============================
st.set_page_config(page_title="🔗 Link Distribution Dashboard", layout="wide")
st.title("🔗 Link Distribution Dashboard")

if PLAYWRIGHT_AVAILABLE:
    st.success("🚀 Using Playwright (JS-enabled crawling)")
else:
    st.info("⚡ Using fallback mode (requests + BeautifulSoup)")

st.sidebar.title("Crawler Settings")
url_input = st.sidebar.text_input("Enter a website URL to crawl:")
result_limit = st.sidebar.number_input("Max results to show:", 1, 100, 10)
exact_anchor_input = st.sidebar.text_input("Exact anchor text filter (optional):")
run_crawl = st.sidebar.button("🚀 Run Crawler")

if run_crawl and url_input:
    with st.spinner("Crawling website..."):
        try:
            if PLAYWRIGHT_AVAILABLE:
                results = asyncio.run(PlaywrightCrawler().crawl(url_input))
            else:
                results = SimpleHTTPCrawler().get_links(url_input)

            df = process_results(results, url_input, result_limit, exact_anchor_input or None)
        except Exception as e:
            st.error(f"❌ Error: {e}")
            df = pd.DataFrame()

    if not df.empty:
        st.subheader("📊 Key Metrics")

        total_links = df["count"].sum()
        unique_texts = df["anchor_text"].nunique()
        top_anchor = df.iloc[0]["anchor_text"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Links", total_links)
        col2.metric("Unique Anchor Texts", unique_texts)
        col3.metric("Top Anchor Text", top_anchor)

        st.subheader("📈 Anchor Text Distribution")
        fig = px.pie(df, values="count", names="anchor_text", title="Anchor Text Distribution")
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("🔍 Show Details"):
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No data found. Try another URL.")
