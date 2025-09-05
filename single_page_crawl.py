import asyncio
import os
import sqlite3
from matplotlib import pyplot as plt
import pandas as pd
from playwright.async_api import async_playwright
from urllib.parse import urljoin, urlparse
import json
import sys
import streamlit as st
import plotly.express as px

# Fix for Windows event loop policy issue
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

class LinkCrawler:
    def __init__(self, headless=True, timeout=300000):
        self.headless = headless
        self.timeout = timeout
        self.browser = None
        self.context = None
    
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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
        return url.rstrip('/').split('#')[0].split('?')[0]
    
    async def get_links_to_target(self, source_url):
        """
        Find all links on source_url that point to target_url
        Returns list of dictionaries with 'url', 'anchor_text', and 'element_info'
        """
        parsed = urlparse(source_url)
        domain = parsed.netloc.replace("www.", "")
        new_source_url = f"{parsed.scheme}://{domain}"
        
        try:
            # Create new page
            async with async_playwright() as p:

                page = await self.context.new_page()
                
                # Set page timeout
                page.set_default_timeout(self.timeout)
                
                print(f"Loading source page: {source_url}")
                
                # Navigate to source page
                await page.goto(source_url, wait_until='networkidle')
                
                # Wait a bit more for any dynamic content
                await page.wait_for_timeout(10000)
                
                print("Page loaded, searching for links...")
                
                links = await page.query_selector_all("[href]")
                
                print(f"Found {len(links)} total links on the page")

                link_list = []
                
                for link in links:
                    try:
                        href = await link.get_attribute('href')

                        anchor_text = await link.inner_text()
                        anchor_text = anchor_text.strip() if anchor_text else None

                        if not href or not anchor_text:
                            continue
                        elif new_source_url in href:

                            absolute_url = urljoin(source_url, href)
                            normalized_url = self.normalize_url(absolute_url)
                            
                            # Get additional element info
                            element_info = {
                                'init_url': source_url,
                                'anchor_text': anchor_text,
                                'href': normalized_url,
                                'is_visible': await link.is_visible(),
                                'tag_name': await link.evaluate('el => el.tagName.toLowerCase()'),
                                'class': await link.get_attribute('class') or '',
                                'id': await link.get_attribute('id') or '',
                                'title': await link.get_attribute('title') or '',
                                'target': await link.get_attribute('target') or '',
                            }

                            link_list.append(element_info)
                            
                    except Exception as e:
                        print(f"Error processing individual link: {e}")
                        continue
                
                await page.close()
            
        except Exception as e:
            print(f"Error loading page {source_url}: {e}")
            return []

        return link_list
    
    async def crawl_multiple_sources(self, source_urls):
        """Crawl multiple source URLs for links to target"""
        all_results = {}
        
        for source_url in source_urls:
            print(f"\n{'='*60}")
            print(f"Crawling: {source_url}")
            print(f"{'='*60}")
            
            results = await self.get_links_to_target(source_url)
            all_results[source_url] = results
        
        return all_results
    
def init_db(db_path="crawler.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            init_url TEXT,
            anchor_text TEXT,
            href TEXT,
            is_visible BOOLEAN,
            tag_name TEXT,
            class TEXT,
            id_attr TEXT,
            title TEXT,
            target TEXT
        )
    """)
    
    conn.commit()
    return conn, cursor

    
def store_links(results, source_url):
    conn, cursor = init_db()

    link_list = results[source_url]

    # Check if link already exists in DB
    cursor.execute(
        """
        SELECT 1 FROM links
        WHERE init_url = ? AND anchor_text = ? AND href = ?
        LIMIT 1
        """,
        (link_list[0]["init_url"], link_list[0]["anchor_text"], link_list[0]["href"]),
    )
    exists = cursor.fetchone()

    if not exists:
        for link in link_list:
            cursor.execute(
                """
                INSERT INTO links 
                (init_url, anchor_text, href, is_visible, tag_name, class, id_attr, title, target)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link["init_url"],
                    link["anchor_text"],
                    link["href"],
                    link["is_visible"],
                    link["tag_name"],
                    link["class"],
                    link["id"],
                    link["title"],
                    link["target"],
                ),
            )
            conn.commit()

    return conn

async def main(source_url: str):
    source_urls = [source_url]

    print("Playwright Link Crawler")
    print("=" * 50)
    print(f"Source URL(s): {', '.join(source_urls)}")
    print("=" * 50)

    try:
        async with LinkCrawler(headless=True) as crawler:
            results = await crawler.crawl_multiple_sources(source_urls)
    except KeyboardInterrupt:
        print("\nCrawling interrupted by user")
        return
    except Exception as e:
        print(f"Error during crawling: {e}")
        sys.exit(1)

    db_con = store_links(results, source_url)
    db_con.close()

def get_data(url, limit):
    if not os.path.exists("crawler.db"):
        return pd.DataFrame(columns=["init_url", "anchor_text", "href", "count"])

    conn = sqlite3.connect("crawler.db")
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT init_url, anchor_text, href, COUNT(*) as count
        FROM links
        WHERE init_url = ? AND anchor_text != '[No text]'
        GROUP BY anchor_text
        ORDER BY count DESC
        LIMIT {limit}
        """,
        (url,),
    )
    rows = cursor.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["init_url", "anchor_text", "href", "count"])


if __name__ == "__main__":
    st.set_page_config(page_title="🔗 Link Distribution Dashboard", layout="wide")
    st.title("🔗 Link Distribution Dashboard")

    # Sidebar inputs
    st.sidebar.title("Crawler Settings")
    url_input = st.sidebar.text_input("Enter a website URL to crawl:")
    result_limit = st.sidebar.number_input("Max results to show:", min_value=1, max_value=100, value=10, step=1)
    run_crawl = st.sidebar.button("🚀 Run Crawler")

    if run_crawl and url_input:
        # Delete database only when starting a new crawl
        if os.path.exists("crawler.db"):
            os.remove("crawler.db")

        with st.spinner("Crawling website... please wait ⏳"):
            try:
                asyncio.run(main(url_input))
            except Exception as e:
                st.error(f"Error: {e}")

        # Pass result_limit to query
        df = get_data(url_input, result_limit)

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

            with st.expander("🔎 Show Anchor Text Details"):
                st.dataframe(df, use_container_width=True, hide_index=True)

        else:
            st.info("No data available yet. Try crawling first.")


            