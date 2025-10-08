# --- distutils shim for Python 3.11/3.12+ environments ---
try:
    import distutils  # noqa
except ModuleNotFoundError:
    import setuptools, sys
    sys.modules["distutils"] = setuptools._distutils
    sys.modules["distutils.version"] = setuptools._distutils.version
# ---------------------------------------------------------

import os, time, random, atexit, re
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# ---------------- CONFIG ----------------
HEADLESS = True
DELAY = (2.5, 4.5)

uc.Chrome.__del__ = lambda self: None

PRICE_SELECTORS = [
    "div.Nx9bqj", "span.VU-ZEz", "div._30jeq3", "div.CxhGGd",
    "meta[itemprop='price']", "meta[property='product:price:amount']",
]
CURR_RX = re.compile(r"(?:₹|Rs\.?)\s*([\d,]+)")

# -------- helpers ----------
def to_float_amt(txt):
    if not txt: return None
    m = CURR_RX.search(txt)
    if not m: return None
    return float(m.group(1).replace(",", ""))

def build_driver():
    opts = uc.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=en-US")
    if HEADLESS:
        opts.add_argument("--headless=new")
    d = uc.Chrome(options=opts)
    atexit.register(lambda dd=d: dd.quit())
    return d

def extract_price(driver):
    for sel in PRICE_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = el.get_attribute("content") if el.tag_name == "meta" else el.text
            amt = to_float_amt(txt)
            if amt: return amt
        except: pass
    return to_float_amt(driver.page_source)

def extract_stars_ratings_reviews(driver):
    """Stars + ratings + reviews (stable fallback logic)."""
    html = driver.page_source
    soup = BeautifulSoup(html, "lxml")

    stars = None
    ratings = None
    reviews = None

    # 1) meta ratingValue
    try:
        meta = soup.select_one('meta[itemprop="ratingValue"]')
        if meta and meta.get("content"):
            stars = float(meta["content"])
    except: pass

    full_text = soup.get_text(" ", strip=True)

    # 2) visible "3.6 ★"
    if stars is None:
        m_star = re.search(r'([0-5](?:\.\d)?)\s*★', full_text)
        if m_star:
            stars = float(m_star.group(1))

    # 3) pattern like "4.5 981 ratings"
    if stars is None:
        m_before = re.search(r'([0-5](?:\.\d)?)\s+[,\d]+\s+ratings', full_text, flags=re.I)
        if m_before:
            stars = float(m_before.group(1))

    # ratings + reviews
    m_counts = re.search(r'([\d,]+)\s*ratings?.*?([\d,]+)\s*reviews?', full_text, flags=re.I)
    if m_counts:
        try: ratings = float(m_counts.group(1).replace(",", ""))
        except: ratings = None
        try: reviews = float(m_counts.group(2).replace(",", ""))
        except: reviews = None

    return (
        stars if stars is not None else "N/A",
        ratings if ratings is not None else "N/A",
        reviews if reviews is not None else "N/A"
    )

# ------------- scraping -------------
def scrape_file(uploaded_file):
    df = pd.read_excel(uploaded_file)
    link_col = "url" if "url" in df.columns else ("Link" if "Link" in df.columns else None)
    if not link_col:
        st.error("Excel me 'Link' ya 'url' column hona chahiye.")
        return None

    for c in ["selling_price", "stars", "ratings", "reviews", "status", "error"]:
        if c not in df.columns: df[c] = None

    d = build_driver()
    wait = WebDriverWait(d, 25)

    try:
        total = len(df)
        prog = st.progress(0, text="Starting…")
        for i, url in enumerate(df[link_col].astype(str), start=1):
            if not (isinstance(url, str) and url.startswith("http")):
                prog.progress(i/total, text=f"Skip {i}/{total}")
                continue
            try:
                d.get(url)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                time.sleep(1.2)

                price = extract_price(d)
                stars, ratings, reviews = extract_stars_ratings_reviews(d)

                df.loc[df.index[i-1], ["selling_price","stars","ratings","reviews","status","error"]] = \
                    [price, stars, ratings, reviews, "OK", None]
                prog.progress(i/total, text=f"Done {i}/{total}")
            except Exception as e:
                df.loc[df.index[i-1], ["status","error"]] = ["FAIL", str(e)]
                prog.progress(i/total, text=f"Error {i}/{total}")
            time.sleep(random.uniform(*DELAY))
    finally:
        try: d.quit()
        except: pass

    return df

# ---------------- STREAMLIT UI ----------------
st.title("📊 Flipkart Product Scraper")

uploaded_file = st.file_uploader("Upload your Excel file (must have 'Link' or 'url' column)", type=["xlsx","xls","csv"])

if uploaded_file is not None:
    if st.button("🚀 Start Scraping"):
        result_df = scrape_file(uploaded_file)
        if result_df is not None:
            st.success("✅ Scraping Completed!")
            st.dataframe(result_df)

            out_file = "scraped_results.xlsx"
            result_df.to_excel(out_file, index=False)
            with open(out_file, "rb") as f:
                st.download_button("⬇️ Download Excel", f, file_name="scraped_results.xlsx")
