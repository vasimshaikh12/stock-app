import re
from functools import lru_cache
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dash import Dash, dcc, html, Input, Output, State, ALL
from urllib.parse import quote_plus
import os
from dash.exceptions import PreventUpdate
from groq_chatbot import GroqChatbot
from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
csv_path = os.path.join(BASE_DIR, "master_merged_equity_list.csv")

# =========================================================
# Screener helpers
# =========================================================

# Global mapping: Ticker -> List of possible Screener codes
# This will be populated from CSV data
TICKER_TO_SCREENER_CODES = {}

# Special cases where Screener code is not obvious
# For BSE stocks, try multiple codes: BSE_CODE, BSE_SYMBOL, or company name variations
# Note: Most BSE stocks will be handled automatically via CSV data mapping
SCREENER_SPECIAL = {
    # Format: "TICKER": [list of codes to try in order]
    # "RAJESH.BO": ["RAJESH", "544291", "RAJESHPOWER"],  # Will be handled by CSV data
    # add more overrides here if needed for stocks not in CSV
}

def get_screener_codes_for_ticker(symbol):
    """
    Get all possible Screener codes for a ticker symbol.
    Returns a list of codes to try in order of preference.
    """
    # Check special cases first
    if symbol in SCREENER_SPECIAL:
        special_codes = SCREENER_SPECIAL[symbol]
        # Handle both list and single string formats
        if isinstance(special_codes, list):
            return special_codes
        else:
            return [special_codes]
    
    # Check if we have a mapping from CSV data
    if symbol in TICKER_TO_SCREENER_CODES:
        return TICKER_TO_SCREENER_CODES[symbol]
    
    # Fallback: try to extract code from ticker
    codes = []
    if symbol and symbol.endswith(".NS"):
        codes.append(symbol[:-3])  # NSE symbol
    elif symbol and symbol.endswith(".BO"):
        if symbol[:-3].isdigit():
            codes.append(symbol[:-3])  # BSE numeric code
        else:
            codes.append(symbol[:-3])  # BSE symbol
    elif symbol and "." in symbol:
        codes.append(symbol.split(".")[0])
    else:
        codes.append(symbol)
    
    return codes if codes else [None]

def ticker_to_screener_code(symbol):
    """
    Convert a yfinance-style ticker to Screener company code.
    Returns the first/preferred code (usually NSE symbol).
    """
    codes = get_screener_codes_for_ticker(symbol)
    return codes[0] if codes else None

def screener_base(code):
    return f"https://www.screener.in/company/{code}/"

@lru_cache(maxsize=128)
def fetch_screener_html(symbol):
    """
    Download Screener consolidated page HTML for a symbol.
    Tries multiple possible codes (NSE symbol, BSE symbol, BSE code) until one works.
    Cached to avoid repeated network calls.
    """
    codes = get_screener_codes_for_ticker(symbol)
    if not codes or codes[0] is None:
        print(f"DEBUG: Could not convert {symbol} to Screener code")
        return None
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    # Try each code in order
    for code in codes:
        if not code:
            continue
            
        # Try consolidated URL first
        url = screener_base(code) + "consolidated/"
        try:
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                # Check if page contains error message or redirect
                resp_text_lower = resp.text.lower()
                # Only check for specific error phrases to avoid false positives
                if ("page not found" in resp_text_lower or "does not exist" in resp_text_lower):
                    print(f"DEBUG: {symbol} -> {code}: Page indicates not found (200 but error message)")
                    continue  # Try next code
                # Check if final URL was redirected to 404 page
                if "404" in resp.url.lower() or "not-found" in resp.url.lower():
                    print(f"DEBUG: {symbol} -> {code}: Redirected to 404 page")
                    continue  # Try next code
                return resp.text
            elif resp.status_code == 404:
                # Try non-consolidated URL as fallback
                url_fallback = screener_base(code)
                resp_fallback = requests.get(url_fallback, headers=headers, timeout=15, allow_redirects=True)
                if resp_fallback.status_code == 200:
                    # Check for specific error messages only
                    fallback_text_lower = resp_fallback.text.lower()
                    if "page not found" not in fallback_text_lower and "does not exist" not in fallback_text_lower:
                        return resp_fallback.text
                print(f"DEBUG: {symbol} -> {code}: Both URLs returned 404 or error")
                continue  # Try next code
            else:
                print(f"DEBUG: {symbol} -> {code}: HTTP {resp.status_code}")
                continue  # Try next code
        except requests.exceptions.Timeout:
            print(f"DEBUG: {symbol} -> {code}: Timeout")
            continue  # Try next code
        except Exception as e:
            print(f"DEBUG: {symbol} -> {code}: Exception {type(e).__name__}: {str(e)}")
            continue  # Try next code
    
    # If all codes failed
    print(f"DEBUG: {symbol} not found on Screener.in. Tried codes: {codes}")
    return None

def _num(s):
    if not s:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None

# =========================================================
# Fundamentals (key metrics + YoY) from Screener
# =========================================================

@lru_cache(maxsize=128)
def fetch_screener_metrics(symbol):
    """
    Fetch core fundamentals + simple YoY from :
    - Market Cap, Current Price, High/Low, P/E, Book Value, Price/Book,
      Dividend Yield, ROCE, ROE, Face Value, 52W High/Low.
    - Sales YoY % (last full year vs previous year).
    """
    html_text = fetch_screener_html(symbol)
    
    # Initialize variables for 52-week high/low
    high_52 = None
    low_52 = None
    
    if not html_text:
        # Try BSE fallback if we have BSE code
        print(f"DEBUG: Screener failed for {symbol}, trying BSE fallback...")
        codes = get_screener_codes_for_ticker(symbol)
        bse_code = None
        
        # Look for numeric BSE code in the codes list
        for code in codes:
            if code and code.isdigit():
                bse_code = code
                break
        
        if bse_code:
            bse_data = fetch_bse_metrics(bse_code)
            if bse_data:
                print(f"DEBUG: Got data from BSE for {symbol}")
                # Return minimal data from BSE
                code = ticker_to_screener_code(symbol)
                return {
                    "Market Cap": "N/A",
                    "Current Price": bse_data.get("Current Price", "N/A"),
                    "P/E Ratio": "N/A",
                    "Book Value": "N/A",
                    "Price / Book": "N/A",
                    "Dividend Yield": "N/A",
                    "ROCE": "N/A",
                    "ROE": "N/A",
                    "Face Value": "N/A",
                    "52-Week High": bse_data.get("52-Week High", "N/A"),
                    "52-Week Low": bse_data.get("52-Week Low", "N/A"),
                    "Sales YoY %": "N/A",
                    "Net Profit YoY %": "N/A",
                    "Links": {
                        "Screener": screener_base(code) if code else None,
                        "Google Finance": f"https://www.google.com/finance/quote/{symbol}:NSE",
                        "BSE": f"https://www.bseindia.com/stock-share-price/{bse_code}/",
                    },
                }
        
        # If BSE also fails, return empty
        return {}

    soup = BeautifulSoup(html_text, "lxml")

    # ---------- key metrics list (top bullets) ----------
    key_vals = {}
    for li in soup.find_all("li"):
        spans = li.find_all("span")
        if len(spans) < 2:
            continue
        label = spans[0].get_text(strip=True)
        value = spans[1].get_text(strip=True)
        if label in [
            "Market Cap",
            "Current Price",
            "High / Low",
            "High/Low",
            "Stock P/E",
            "P/E",
            "Book Value",
            "Dividend Yield",
            "ROCE",
            "ROCE 3Yr",
            "ROE",
            "ROE 3Yr",
            "Face Value",
            "Price to Book value",
            "Price to book",
        ]:
            key_vals[label] = value

    def get_any(*labels):
        for lb in labels:
            if lb in key_vals:
                return key_vals[lb]
        return None

    mcap = get_any("Market Cap")
    current_price = get_any("Current Price")
    high_low = get_any("High / Low", "High/Low")
    pe = get_any("Stock P/E", "P/E")
    book_value = get_any("Book Value")
    dy = get_any("Dividend Yield")
    roce = get_any("ROCE", "ROCE 3Yr")
    roe = get_any("ROE", "ROE 3Yr")
    fv = get_any("Face Value")
    pb_raw = get_any("Price to Book value", "Price to book")

    # ---------- Google Finance Fallback ----------
    # If key metrics are missing (e.g. Market Cap, Price), try Google Finance
    if not mcap or not current_price:
        print(f"DEBUG: Missing key metrics for {symbol}, trying Google Finance fallback...")
        gf_data = fetch_google_finance_metrics(symbol)
        if gf_data:
            if not mcap: mcap = gf_data.get("Market Cap")
            if not current_price: current_price = gf_data.get("Current Price")
            if not pe: pe = gf_data.get("P/E Ratio")
            if not high_52: high_52 = gf_data.get("52-Week High")
            if not low_52: low_52 = gf_data.get("52-Week Low")
            # Google Finance doesn't easily give Book Value / ROCE / ROE in the summary, 
            # but we can at least get price and PE.

    # ---------- Price / Book ----------
    pb = pb_raw
    if pb is None:
        cp_num = _num(current_price)
        bv_num = _num(book_value)
        if cp_num is not None and bv_num not in (None, 0):
            pb = f"{cp_num / bv_num:.2f}"

    # ---------- 52 week high/low ----------
    # (Existing logic handles Screener format, if GF data is used it might be different, 
    # but let's assume we parse it cleanly or leave it as is if from Screener)
    if high_low and not high_52:
        txt = high_low.replace("₹", "")
        parts = txt.split("/")
        if len(parts) == 2:
            high_52 = parts[0].strip()
            low_52 = parts[1].strip()

    # ---------- YoY from Profit & Loss table ----------
    yoy_sales = None
    yoy_profit = None
    try:
        tables = pd.read_html(html_text)
        pl_df = None
        for t in tables:
            first_col = t.iloc[:, 0].astype(str)
            if any(first_col.str.contains("Sales", case=False)) and any(
                first_col.str.contains("Net Profit", case=False)
            ):
                pl_df = t
                break

        if pl_df is not None:
            pl_df = pl_df.copy()
            pl_df.rename(columns={pl_df.columns[0]: "Particulars"}, inplace=True)
            pl_df.set_index("Particulars", inplace=True)
            cols = list(pl_df.columns)
            if len(cols) >= 3:
                prev_col, last_col = cols[-3], cols[-2]

                def yoy(row_name):
                    if row_name not in pl_df.index:
                        return None
                    prev_val = str(pl_df.loc[row_name, prev_col]).replace(",", "")
                    last_val = str(pl_df.loc[row_name, last_col]).replace(",", "")
                    try:
                        prev_f = float(prev_val)
                        last_f = float(last_val)
                        if prev_f == 0:
                            return None
                        return (last_f / prev_f - 1.0) * 100.0
                    except Exception:
                        return None

                yoy_sales = yoy("Sales")
                yoy_profit = yoy("Net Profit")
    except Exception:
        pass

    def pct_fmt(x):
        return f"{x:.1f} %" if x is not None else "N/A"

    def nz(x):
        return x if x is not None else "N/A"

    code = ticker_to_screener_code(symbol)
    
    # Generate External Links
    # MoneyControl: Search URL is usually reliable to find the stock
    mc_url = f"https://www.moneycontrol.com/india/stockpricequote/{symbol}" 
    # Actually MC URLs are complex. Best to use their search or a known pattern if possible.
    # But a search link is safest:
    mc_search_url = f"https://www.moneycontrol.com/news/business/stocks/{symbol}.html" # Not quite
    # Let's use Google Search for MoneyControl as a proxy or just the Google Finance link
    gf_url = f"https://www.google.com/finance/quote/{symbol}:NSE"
    
    # Try to construct a decent MoneyControl link. 
    # Often it's difficult without the specific MC ID. 
    # We will use a Google Search link for MoneyControl as a fallback or the generic search.
    mc_search = f"https://www.moneycontrol.com/mccode/common/autosuggest_search_link.php?search_query={symbol}&classic=true"
    
    links = {
        "Screener": screener_base(code) if code else None,
        "Google Finance": gf_url,
        "MoneyControl": mc_search, # This will likely redirect or show search results
    }

    return {
        "Market Cap": nz(mcap),
        "Current Price": nz(current_price),
        "P/E Ratio": nz(pe),
        "Book Value": nz(book_value),
        "Price / Book": nz(pb),
        "Dividend Yield": nz(dy),
        "ROCE": nz(roce),
        "ROE": nz(roe),
        "Face Value": nz(fv),
        "52-Week High": nz(high_52),
        "52-Week Low": nz(low_52),
        "Sales YoY %": pct_fmt(yoy_sales),
        "Net Profit YoY %": pct_fmt(yoy_profit),
        "Links": links, # Return the dictionary of links
    }

def fetch_google_finance_metrics(symbol):
    """
    Fallback: Fetch basic metrics from Google Finance.
    """
    try:
        # Try NSE first
        url = f"https://www.google.com/finance/quote/{symbol}:NSE"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            # Try BSE
            url = f"https://www.google.com/finance/quote/{symbol}:BOM"
            resp = requests.get(url, headers=headers, timeout=10)
            
        if resp.status_code != 200:
            return None
            
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Google Finance classes change, but often they use specific structures.
        # We can look for text labels.
        
        data = {}
        
        # Helper to find value by label
        def find_val(label):
            # Look for a div containing the label, then find the sibling or child with value
            # This is tricky as structure varies. 
            # Strategy: Find text, go up to parent, find next sibling or specific class.
            try:
                # Common pattern in GF: <div class="gyFHrc"><div class="mfs7Fc">Market cap</div><div class="P6K39c">18.50T</div></div>
                # We look for the label text
                elem = soup.find(string=re.compile(f"^{label}", re.I))
                if elem:
                    # Go up to the container
                    parent = elem.parent
                    # Usually the value is in a sibling or the next div
                    # Try next sibling
                    val_div = parent.find_next_sibling("div")
                    if val_div:
                        return val_div.get_text(strip=True)
                    
                    # Or sometimes it's up another level
                    grandparent = parent.parent
                    val_div = grandparent.find("div", class_=re.compile("P6K39c|YMlKec")) # Common classes
                    if val_div:
                        return val_div.get_text(strip=True)
            except:
                pass
            return None

        # Current Price
        # Usually in a large font class "YMlKec fxKbKc"
        price_div = soup.find("div", class_="YMlKec fxKbKc")
        if price_div:
            data["Current Price"] = price_div.get_text(strip=True).replace("₹", "")
            
        data["Market Cap"] = find_val("Market cap")
        data["P/E Ratio"] = find_val("P/E ratio")
        data["52-Week High"] = find_val("52-wk high")
        data["52-Week Low"] = find_val("52-wk low")
        
        # Clean up
        for k, v in data.items():
            if v:
                data[k] = v.replace("₹", "").strip()
                
        return data
        
    except Exception as e:
        print(f"Error fetching Google Finance data for {symbol}: {e}")
        return None

def fetch_bse_metrics(bse_code, symbol_name=""):
    """
    Fallback: Fetch basic metrics from BSE website.
    BSE code should be the numeric code (e.g., 544291).
    """
    try:
        # BSE stock quote page
        # Format: https://www.bseindia.com/stock-share-price/company-name/SYMBOL/CODE/
        # We'll try the API endpoint that BSE uses
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.bseindia.com/"
        }
        
        # Try BSE's stock quote API
        api_url = f"https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w?scripcode={bse_code}&flag=0&fromdate=&todate=&seriesid="
        
        resp = requests.get(api_url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            try:
                data_json = resp.json()
                if data_json and len(data_json) > 0:
                    latest = data_json[0]
                    
                    result = {}
                    # Map BSE fields to our format
                    if 'CurrRate' in latest:
                        result['Current Price'] = str(latest['CurrRate'])
                    if 'High' in latest:
                        result['52-Week High'] = str(latest['High'])
                    if 'Low' in latest:
                        result['52-Week Low'] = str(latest['Low'])
                    
                    return result if result else None
            except:
                pass
        
        # Alternative: Try the main stock page and scrape
        # This is less reliable but can work as last resort
        stock_url = f"https://www.bseindia.com/stock-share-price/{bse_code}/"
        resp = requests.get(stock_url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            result = {}
            
            # Try to find price - BSE uses various formats
            # Look for elements with specific IDs or classes
            price_elem = soup.find("span", {"id": "idcrval"}) or soup.find("strong", {"id": "idcrval"})
            if price_elem:
                result['Current Price'] = price_elem.get_text(strip=True).replace(',', '')
            
            return result if result else None
            
    except Exception as e:
        print(f"Error fetching BSE data for code {bse_code}: {e}")
        return None


# =========================================================
# Section parsers: P&L, Balance Sheet, Cash Flows, Shareholding, Announcements
# =========================================================

def parse_pl_table(html_text):
    """Return the Profit & Loss table as a DataFrame."""
    try:
        tables = pd.read_html(html_text)
    except Exception:
        return None

    for t in tables:
        first_col = t.iloc[:, 0].astype(str)
        if any(first_col.str.contains("Sales", case=False)) and any(
            first_col.str.contains("Net Profit", case=False)
        ):
            return t
    return None

def parse_bs_table(html_text):
    """Return the Balance Sheet table as a DataFrame."""
    try:
        tables = pd.read_html(html_text)
    except Exception:
        return None

    for t in tables:
        first_col = t.iloc[:, 0].astype(str)
        if any(first_col.str.contains("Equity Capital", case=False)) and any(
            first_col.str.contains("Total Assets", case=False)
        ):
            return t
    return None

def parse_cf_table(html_text):
    """Return the Cash Flows table as a DataFrame."""
    try:
        tables = pd.read_html(html_text)
    except Exception:
        return None

    for t in tables:
        first_col = t.iloc[:, 0].astype(str)
        if any(first_col.str.contains("Cash from Operating Activity", case=False)) and any(
            first_col.str.contains("Net Cash Flow", case=False)
        ):
            return t
    return None

def parse_shareholding_table(html_text):
    """Return the Shareholding Pattern table as a DataFrame."""
    try:
        tables = pd.read_html(html_text)
    except Exception:
        return None

    for t in tables:
        first_col = t.iloc[:, 0].astype(str)
        if any(first_col.str.contains("Promoters", case=False)) or any(
            first_col.str.contains("Promoter", case=False)
        ):
            return t
    return None

def parse_announcements(html_text, max_items=5):
    """
    Parse recent announcements (documents/news) from  'Documents' section.
    Returns a list of dicts: [{title, detail, url}, ...].
    We try to mimic 's view: title on first line, date+summary below.
    """
    soup = BeautifulSoup(html_text, "lxml")

    # Try to locate a section heading containing 'Announcements'
    ann_section = None
    for h in soup.find_all(["h2", "h3"]):
        txt = h.get_text(strip=True).lower()
        if "announcement" in txt:
            ann_section = h.parent
            break

    # Fallback: look for a <ul> whose class mentions 'announcement'
    if not ann_section:
        for ul in soup.find_all("ul"):
            cls = " ".join(ul.get("class", [])).lower()
            if "announcement" in cls:
                ann_section = ul
                break

    if not ann_section:
        return []

    items = []
    for li in ann_section.find_all("li", limit=max_items):
        a = li.find("a")
        if not a:
            continue

        # Text inside the clickable link
        raw_title = a.get_text(" ", strip=True)

        # Try to separate heading from trailing date/summary in the link text itself
        parts = re.split(r"\s+-\s+", raw_title, maxsplit=1)
        if len(parts) == 2:
            short_title, rest_in_title = parts[0].strip(), parts[1].strip()
        else:
            short_title, rest_in_title = raw_title.strip(), ""

        # Full LI text (title + date + description, etc.)
        full_text = li.get_text(" ", strip=True)

        # Whatever is not in the link text we treat as secondary line
        after_text = full_text.replace(raw_title, "").strip()

        # Combine any remaining pieces into a single "detail" line
        detail_parts = [p for p in [rest_in_title, after_text] if p]
        detail = " - ".join(detail_parts)

        href = a.get("href")
        if href and not href.startswith("http"):
            href = "https://www.screener.in" + href

        items.append(
            {
                "title": short_title,
                "detail": detail,
                "url": href,
            }
        )

    return items

# =========================================================
# Dash theme + layout
# =========================================================

THEME = {
    "bg": "#f5f5f5",
    "card_bg": "#ffffff",
    "primary": "#1167b1",
    "accent": "#0f4c81",
    "text": "#222222",
    "muted": "#777777",
    "border": "#e0e0e0",
}

def card(children, style=None):
    base_style = {
        "backgroundColor": THEME["card_bg"],
        "border": f"1px solid {THEME['border']}",
        "borderRadius": "8px",
        "padding": "16px",
        "marginBottom": "20px",
        "boxShadow": "0 2px 6px rgba(0,0,0,0.03)",
    }
    if style:
        base_style.update(style)
    return html.Div(children, style=base_style)

app = Dash(__name__)
server = app.server

# Initialize Groq Chatbot
# GROQ_API_KEY = "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"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
chatbot = GroqChatbot(GROQ_API_KEY)

# Helper to create a stock dropdown (used when user clicks + add button)
def make_stock_dropdown(index):
    return html.Div(
        style={
            "display": "flex",
            "alignItems": "center",
            "backgroundColor": "#fff",
            "border": "1px solid #ddd",
            "borderRadius": "4px",
            "padding": "4px",
            "marginRight": "10px",
            "marginBottom": "8px",
        },
        children=[
            dcc.Dropdown(
                id={"type": "stock-dropdown", "index": index},
                options=dropdown_options,
                value=None,
                placeholder="Select stock...",
                style={
                    "width": "400px",
                    "border": "none",
                },
            ),
            html.Button(
                "✕",
                id={"type": "remove-stock-btn", "index": index},
                n_clicks=0,
                style={
                    "backgroundColor": "transparent",
                    "border": "none",
                    "color": "#ff4d4f",
                    "cursor": "pointer",
                    "fontSize": "16px",
                    "fontWeight": "bold",
                    "marginLeft": "4px",
                    "padding": "0 6px",
                },
                title="Remove stock",
            ),
        ],
    )

# =========================================================
# 🔹 Load master list of all listed companies for dropdown
# =========================================================

# Load CSV using relative path with error handling
try:
    master_df = pd.read_csv(csv_path)
    
    # Normalize column names (strip whitespace, handle case variations)
    master_df.columns = master_df.columns.str.strip()
    
    # Try to find Ticker and CompanyName columns
    # Priority: exact match first (case-sensitive), then case-insensitive, then partial match
    ticker_col = None
    name_col = None
    
    # First, try exact case-sensitive matches (highest priority)
    if 'Ticker' in master_df.columns:
        ticker_col = 'Ticker'
    if 'CompanyName' in master_df.columns:
        name_col = 'CompanyName'
    
    # If exact match not found, try case-insensitive exact match
    if ticker_col is None:
        for col in master_df.columns:
            if col.strip().lower() == 'ticker':
                ticker_col = col
                break
    
    if name_col is None:
        for col in master_df.columns:
            if col.strip().lower() == 'companyname':
                name_col = col
                break
    
    # If still not found, try partial matches (but avoid NSE_SYMBOL, BSE_SYMBOL, NSE_NAME, BSE_NAME)
    if ticker_col is None:
        for col in master_df.columns:
            col_lower = col.lower().strip()
            # Prefer columns that are exactly "ticker" or start with "ticker"
            if col_lower == 'ticker' or (col_lower.startswith('ticker') and '_' not in col):
                ticker_col = col
                break
            # Only use "symbol" if it's not NSE_SYMBOL or BSE_SYMBOL
            elif 'symbol' in col_lower and 'nse' not in col_lower and 'bse' not in col_lower:
                ticker_col = col
                break
    
    if name_col is None:
        for col in master_df.columns:
            col_lower = col.lower().strip()
            # Prefer "companyname" or columns that start with "company" (but not BSE_NAME, NSE_NAME)
            if col_lower == 'companyname' or (col_lower.startswith('company') and 'bse' not in col_lower and 'nse' not in col_lower):
                name_col = col
                break
            # Fallback to any column with "name" but not NSE_NAME or BSE_NAME
            elif 'name' in col_lower and 'nse' not in col_lower and 'bse' not in col_lower and col_lower != 'name':
                name_col = col
                break
    
    # Check if we found the required columns
    print(f"DEBUG: Column detection - ticker_col: {ticker_col}, name_col: {name_col}")
    print(f"DEBUG: Available columns: {list(master_df.columns)}")
    
    if ticker_col is None or name_col is None:
        print(f"Warning: Could not find required columns. Available columns: {list(master_df.columns)}")
        print(f"Looking for 'Ticker' or 'Symbol' and 'CompanyName' or 'Name'")
        # Create empty dataframe with default structure
        master_df = pd.DataFrame(columns=['Ticker', 'CompanyName'])
        ticker_col = 'Ticker'
        name_col = 'CompanyName'
    else:
        # Rename columns to standard names for easier use (but keep all original columns for mapping)
        if ticker_col != 'Ticker':
            print(f"DEBUG: Renaming '{ticker_col}' to 'Ticker'")
            master_df = master_df.rename(columns={ticker_col: 'Ticker'})
            ticker_col = 'Ticker'
        if name_col != 'CompanyName':
            print(f"DEBUG: Renaming '{name_col}' to 'CompanyName'")
            master_df = master_df.rename(columns={name_col: 'CompanyName'})
            name_col = 'CompanyName'
        print(f"DEBUG: After renaming, columns: {list(master_df.columns)}")
        print(f"DEBUG: Sample data (first 3 rows):")
        if not master_df.empty:
            cols_to_show = ['Ticker', 'CompanyName']
            if 'NSE_SYMBOL' in master_df.columns:
                cols_to_show.append('NSE_SYMBOL')
            if 'BSE_SYMBOL' in master_df.columns:
                cols_to_show.append('BSE_SYMBOL')
            if 'BSE_CODE' in master_df.columns:
                cols_to_show.append('BSE_CODE')
            print(master_df[cols_to_show].head(3).to_string())
    
    # Keep only rows that actually have Ticker + CompanyName
    if not master_df.empty and 'Ticker' in master_df.columns and 'CompanyName' in master_df.columns:
        print(f"DEBUG: Found columns 'Ticker' and 'CompanyName'. Total rows before filtering: {len(master_df)}")
        master_df = master_df[
            master_df["Ticker"].notna() & master_df["CompanyName"].notna()
        ].copy()
        print(f"DEBUG: Rows after filtering (non-null Ticker and CompanyName): {len(master_df)}")
        
        # Build ticker -> Screener codes mapping from CSV data
        # Prefer NSE_SYMBOL, then BSE_SYMBOL, then BSE_CODE
        TICKER_TO_SCREENER_CODES.clear()  # Clear existing mappings
        
        for _, row in master_df.iterrows():
            ticker = row['Ticker']
            codes = []
            
            # Check for NSE_SYMBOL (preferred)
            if 'NSE_SYMBOL' in master_df.columns and pd.notna(row.get('NSE_SYMBOL', None)):
                nse_sym = str(row['NSE_SYMBOL']).strip()
                if nse_sym and nse_sym != 'nan':
                    codes.append(nse_sym)
            
            # Check for BSE_CODE (numeric, reliable for BSE stocks)
            if 'BSE_CODE' in master_df.columns and pd.notna(row.get('BSE_CODE', None)):
                bse_code = str(row['BSE_CODE']).strip()
                # Handle float values like 544291.0 -> 544291
                if '.' in bse_code:
                    bse_code = bse_code.split('.')[0]
                if bse_code and bse_code != 'nan' and bse_code.isdigit():
                    codes.append(bse_code)

            # Check for BSE_SYMBOL (fallback if code fails)
            if 'BSE_SYMBOL' in master_df.columns and pd.notna(row.get('BSE_SYMBOL', None)):
                bse_sym = str(row['BSE_SYMBOL']).strip()
                if bse_sym and bse_sym != 'nan':
                    codes.append(bse_sym)
            
            # Fallback: extract from Ticker itself
            if not codes:
                if ticker and ticker.endswith(".NS"):
                    codes.append(ticker[:-3])
                elif ticker and ticker.endswith(".BO"):
                    codes.append(ticker[:-3])
                elif ticker and "." in ticker:
                    codes.append(ticker.split(".")[0])
                else:
                    codes.append(ticker)
            
            if codes:
                TICKER_TO_SCREENER_CODES[ticker] = codes
        
        print(f"DEBUG: Created mapping for {len(TICKER_TO_SCREENER_CODES)} tickers")
        # Show a few examples
        sample_tickers = list(TICKER_TO_SCREENER_CODES.keys())[:3]
        for ticker in sample_tickers:
            print(f"DEBUG: {ticker} -> {TICKER_TO_SCREENER_CODES[ticker]}")
    else:
        print(f"DEBUG: Missing required columns. Available: {list(master_df.columns)}")
        master_df = pd.DataFrame(columns=['Ticker', 'CompanyName'])
        
except Exception as e:
    print(f"Error loading CSV file: {e}")
    print(f"CSV path: {csv_path}")
    import traceback
    traceback.print_exc()
    # Create empty dataframe as fallback
    master_df = pd.DataFrame(columns=['Ticker', 'CompanyName'])

# Create dropdown options
print(f"DEBUG: Creating dropdown options. DataFrame empty: {master_df.empty}, Has Ticker: {'Ticker' in master_df.columns if not master_df.empty else False}, Has CompanyName: {'CompanyName' in master_df.columns if not master_df.empty else False}")
if not master_df.empty and 'Ticker' in master_df.columns and 'CompanyName' in master_df.columns:
    dropdown_options = [
        {
            "label": f"{row['CompanyName']} ({row['Ticker']})",
            "value": row["Ticker"],
        }
        for _, row in master_df.iterrows()
    ]
    
    # Used everywhere to show nice names instead of raw tickers
    ticker_to_name = dict(zip(master_df["Ticker"], master_df["CompanyName"]))
    print(f"DEBUG: Created {len(dropdown_options)} dropdown options")
    print(f"DEBUG: First 3 options: {dropdown_options[:3]}")
else:
    dropdown_options = []
    ticker_to_name = {}
    print("Warning: No valid stock data loaded. Please check your CSV file.")
    print(f"DEBUG: master_df.empty: {master_df.empty}, Has Ticker: {'Ticker' in master_df.columns if not master_df.empty else False}, Has CompanyName: {'CompanyName' in master_df.columns if not master_df.empty else False}")

app.layout = html.Div(
    style={
        "backgroundColor": THEME["bg"],
        "minHeight": "100vh",
        "padding": "0",
        "fontFamily": "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    },
    children=[
        # Top bar
        html.Div(
            style={
                "backgroundColor": THEME["primary"],
                "color": "white",
                "padding": "12px 32px",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "space-between",
            },
            children=[
                html.Div(
                    [
                        html.Span(
                            "Screener Fundamental & Financials Dashboard",
                            style={"fontSize": "22px", "fontWeight": "600"},
                        ),
                        html.Span(
                            "  · fundamentals, P&L, Balance Sheet, Cash Flow, Shareholding, Announcements",
                            style={"fontSize": "13px", "color": "#dfe8f5", "marginLeft": "8px"},
                        ),
                    ]
                ),
                html.Span(
                    f"v2.1 | Loaded {len(dropdown_options)} stocks",
                    style={"fontSize": "12px", "opacity": 0.8},
                ),
            ],
        ),
        html.Div(
            style={"padding": "24px 32px"},
            children=[
                # Stock selector
                card(
                    children=[
                        html.Div("Select Stocks", style={"fontWeight": "600", "marginBottom": "6px"}),
                        html.Div(
                            style={
                                "display": "flex",
                                "flexWrap": "wrap",
                                "alignItems": "center",
                                "gap": "10px",
                            },
                            children=[
                                html.Div(
                                    id="stock-dropdowns-wrapper",
                                    style={"display": "contents"},
                                    children=[make_stock_dropdown(0)],
                                ),
                                html.Button(
                                    "＋ Add stock",
                                    id="add-stock-btn",
                                    n_clicks=0,
                                    style={
                                        "backgroundColor": THEME["primary"],
                                        "color": "white",
                                        "border": "none",
                                        "borderRadius": "20px",
                                        "padding": "6px 14px",
                                        "cursor": "pointer",
                                        "fontSize": "12px",
                                        "height": "32px",
                                        "marginBottom": "8px",
                                    },
                                ),
                            ],
                        ),
                        html.Div(
                            "Select 1 or more stocks to pull Screener data into the dashboard.",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginTop": "4px",
                            },
                        ),
                    ]
                ),
                # Fundamentals comparison
                card(
                    children=[
                        html.Div(
                            "Fundamental Comparison ( key metrics)",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(id="metrics-table"),
                    ]
                ),
                # Profit & Loss
                card(
                    children=[
                        html.Div(
                            "Profit & Loss (from )",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Consolidated figures in Rs. Crores. Each selected stock shown separately.",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginBottom": "10px",
                            },
                        ),
                        html.Div(id="pl-section"),
                    ]
                ),
                # Balance Sheet
                card(
                    children=[
                        html.Div(
                            "Balance Sheet (from )",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Consolidated figures in Rs. Crores (Equity Capital, Reserves, Borrowings, Assets, etc.).",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginBottom": "10px",
                            },
                        ),
                        html.Div(id="bs-section"),
                    ]
                ),
                # Cash Flows
                card(
                    children=[
                        html.Div(
                            "Cash Flows (from )",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Consolidated cash flow: CFO, CFI, CFF, Net Cash Flow.",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginBottom": "10px",
                            },
                        ),
                        html.Div(id="cf-section"),
                    ]
                ),
                # Shareholding Pattern
                card(
                    children=[
                        html.Div(
                            "Shareholding Pattern (from )",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Numbers in percentages: Promoters, FIIs, DIIs, Public, etc.",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginBottom": "10px",
                            },
                        ),
                        html.Div(id="shp-section"),
                    ]
                ),
                # Announcements / Documents
                card(
                    children=[
                        html.Div(
                            "Documents / Announcements (from  / BSE/NSE)",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Top 5 latest company announcements as shown on  (links open in new tab).",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginBottom": "10px",
                            },
                        ),
                        html.Div(id="ann-section"),
                    ]
                ),
                html.Div(
                    id="invalid-tickers",
                    style={"color": "red", "marginTop": "10px", "fontSize": "13px"},
                ),
            ],
        ),
        # Chatbot UI Components
        # Floating chat button
        html.Div(
            id="chat-button",
            children="💬",
            n_clicks=0,
            style={
                "position": "fixed",
                "bottom": "20px",
                "right": "20px",
                "width": "60px",
                "height": "60px",
                "borderRadius": "50%",
                "backgroundColor": "#25D366",
                "color": "white",
                "fontSize": "28px",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "center",
                "cursor": "pointer",
                "boxShadow": "0 4px 12px rgba(0,0,0,0.15)",
                "zIndex": "1000",
                "transition": "all 0.3s ease",
            },
        ),
        # Chat panel (hidden by default)
        html.Div(
            id="chat-panel",
            style={
                "position": "fixed",
                "bottom": "90px",
                "right": "20px",
                "width": "400px",
                "height": "600px",
                "backgroundColor": "white",
                "borderRadius": "12px",
                "boxShadow": "0 8px 24px rgba(0,0,0,0.2)",
                "display": "none",
                "flexDirection": "column",
                "zIndex": "999",
                "overflow": "hidden",
            },
            children=[
                # Chat header
                html.Div(
                    style={
                        "background": f"linear-gradient(135deg, {THEME['primary']} 0%, {THEME['accent']} 100%)",
                        "color": "white",
                        "padding": "18px 20px",
                        "fontWeight": "600",
                        "fontSize": "17px",
                        "display": "flex",
                        "justifyContent": "space-between",
                        "alignItems": "center",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.1)",
                    },
                    children=[
                        html.Span("📈 Stock Market Assistant", style={"letterSpacing": "0.3px"}),
                        html.Div(
                            "✕",
                            id="close-chat",
                            n_clicks=0,
                            style={
                                "cursor": "pointer",
                                "fontSize": "22px",
                                "fontWeight": "bold",
                                "width": "28px",
                                "height": "28px",
                                "display": "flex",
                                "alignItems": "center",
                                "justifyContent": "center",
                                "borderRadius": "50%",
                                "transition": "background-color 0.2s ease",
                            },
                        ),
                    ],
                ),
                # Chat messages area
                html.Div(
                    id="chat-messages",
                    style={
                        "flex": "1",
                        "overflowY": "auto",
                        "padding": "16px",
                        "backgroundColor": "#f8f9fa",
                    },
                    children=[\
                        html.Div(
                            [
                                # Welcome message
                                html.Div(
                                    "👋 Hello! I'm your AI stock market assistant. Ask me about stocks, comparisons, or investment strategies!",
                                    style={
                                        "backgroundColor": "#ffffff",
                                        "padding": "12px 16px",
                                        "borderRadius": "16px",
                                        "marginBottom": "12px",
                                        "maxWidth": "85%",
                                        "fontSize": "14px",
                                        "lineHeight": "1.5",
                                        "boxShadow": "0 2px 4px rgba(0,0,0,0.08)",
                                        "border": "1px solid #e9ecef",
                                    },
                                ),
                                # Quick action buttons
                                html.Div(
                                    [
                                        html.Div(
                                            "Quick questions:",
                                            style={
                                                "fontSize": "11px",
                                                "color": "#6c757d",
                                                "marginBottom": "8px",
                                                "fontWeight": "500",
                                            },
                                        ),
                                        html.Button(
                                            "Which stock is better for long-term?",
                                            id={"type": "quick-question", "index": 0},
                                            n_clicks=0,
                                            style={
                                                "backgroundColor": "#ffffff",
                                                "color": THEME["primary"],
                                                "border": f"1px solid {THEME['primary']}",
                                                "borderRadius": "16px",
                                                "padding": "6px 12px",
                                                "fontSize": "11px",
                                                "cursor": "pointer",
                                                "marginRight": "6px",
                                                "marginBottom": "6px",
                                                "display": "inline-block",
                                                "transition": "all 0.2s ease",
                                            },
                                        ),
                                        html.Button(
                                            "Compare selected stocks",
                                            id={"type": "quick-question", "index": 1},
                                            n_clicks=0,
                                            style={
                                                "backgroundColor": "#ffffff",
                                                "color": THEME["primary"],
                                                "border": f"1px solid {THEME['primary']}",
                                                "borderRadius": "16px",
                                                "padding": "6px 12px",
                                                "fontSize": "11px",
                                                "cursor": "pointer",
                                                "marginRight": "6px",
                                                "marginBottom": "6px",
                                                "display": "inline-block",
                                                "transition": "all 0.2s ease",
                                            },
                                        ),
                                        html.Button(
                                            "What are the key metrics to consider?",
                                            id={"type": "quick-question", "index": 2},
                                            n_clicks=0,
                                            style={
                                                "backgroundColor": "#ffffff",
                                                "color": THEME["primary"],
                                                "border": f"1px solid {THEME['primary']}",
                                                "borderRadius": "16px",
                                                "padding": "6px 12px",
                                                "fontSize": "11px",
                                                "cursor": "pointer",
                                                "marginBottom": "6px",
                                                "display": "inline-block",
                                                "transition": "all 0.2s ease",
                                            },
                                        ),
                                    ],
                                    style={"marginBottom": "10px"},
                                ),
                            ]
                        )
                    ],
                ),
                # Chat input area
                html.Div(
                    style={
                        "padding": "12px",
                        "borderTop": "1px solid #dee2e6",
                        "backgroundColor": "white",
                        "display": "flex",
                        "gap": "8px",
                    },
                    children=[
                        dcc.Input(
                            id="chat-input",
                            type="text",
                            placeholder="Ask about stocks...",
                            style={
                                "flex": "1",
                                "padding": "10px 12px",
                                "border": "1px solid #ced4da",
                                "borderRadius": "20px",
                                "fontSize": "13px",
                                "outline": "none",
                            },
                            n_submit=0,
                        ),
                        html.Button(
                            "Send",
                            id="send-chat",
                            n_clicks=0,
                            style={
                                "backgroundColor": THEME["primary"],
                                "color": "white",
                                "border": "none",
                                "borderRadius": "20px",
                                "padding": "10px 20px",
                                "cursor": "pointer",
                                "fontSize": "13px",
                                "fontWeight": "600",
                            },
                        ),
                    ],
                ),
            ],
        ),
        # Hidden store for chat state
        dcc.Store(id="chat-history", data=[]),
    ],
)

# =========================================================
# Helper: convert DataFrame -> Dash HTML table
# =========================================================

def df_to_dash_table(df, max_cols=None):
    if df is None or df.empty:
        return html.Div("No data available.")

    if max_cols is not None and df.shape[1] > max_cols:
        df = df.iloc[:, :max_cols]

    df = df.copy().fillna("")
    headers = list(df.columns)
    rows = df.values.tolist()

    return html.Table(
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "fontSize": "13px",
        },
        children=[
            html.Thead(
                html.Tr(
                    [
                        html.Th(
                            h,
                            style={
                                "borderBottom": f"2px solid {THEME['border']}",
                                "textAlign": "left",
                                "padding": "6px 8px",
                                "backgroundColor": "#f2f6fb",
                                "fontWeight": "600",
                            },
                        )
                        for h in headers
                    ]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(
                                str(cell),
                                style={
                                    "borderBottom": f"1px solid {THEME['border']}",
                                    "padding": "5px 8px",
                                },
                            )
                            for cell in row
                        ]
                    )
                    for row in rows
                ]
            ),
        ],
    )

# =========================================================
# Callback: build all sections
# =========================================================

from dash import callback_context

@app.callback(
    Output("stock-dropdowns-wrapper", "children"),
    [Input("add-stock-btn", "n_clicks"),
     Input({"type": "remove-stock-btn", "index": ALL}, "n_clicks")],
    State("stock-dropdowns-wrapper", "children"),
    prevent_initial_call=True,
)
def manage_stock_dropdowns(add_clicks, remove_clicks, existing_children):
    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate

    triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]

    # Handle Add Button
    if triggered_id == "add-stock-btn":
        if existing_children is None:
            existing_children = []
        # Find the max index to ensure unique IDs
        new_index = 0
        if existing_children:
            # Extract indices from existing children to find max
            # This is a bit hacky but works for simple lists
            new_index = len(existing_children) + int(str(add_clicks) if add_clicks else 0) # simple increment
            # A better way is to rely on the length if we don't care about stable IDs for *removed* items re-appearing
            # But for simplicity, just len + random or timestamp is safer, but here len is fine if we don't rely on persistence too much.
            # Let's just use a counter based on length + n_clicks to avoid collision if possible, or just uuid.
            # For this simple app, len + n_clicks is "unique enough" for new items.
            import time
            new_index = int(time.time() * 1000)

        existing_children.append(make_stock_dropdown(new_index))
        return existing_children

    # Handle Remove Button
    # triggered_id will be a JSON string like '{"index":0,"type":"remove-stock-btn"}'
    import json
    try:
        button_id = json.loads(triggered_id)
        if button_id.get("type") == "remove-stock-btn":
            index_to_remove = button_id["index"]
            
            # Filter out the child with the matching index
            # We need to inspect the children structure. 
            # Each child is a Div -> children[1] is the button -> id has index
            
            new_children = []
            for child in existing_children:
                # child['props']['children'][1]['props']['id']['index']
                try:
                    # Dash component structure as dict
                    # The button is the second child (index 1)
                    btn_id = child['props']['children'][1]['props']['id']
                    if btn_id['index'] != index_to_remove:
                        new_children.append(child)
                except Exception:
                    # If structure doesn't match, keep it to be safe
                    new_children.append(child)
            
            # If all removed, maybe keep one? Or allow empty.
            if not new_children:
                # Optional: Always keep at least one
                # new_children.append(make_stock_dropdown(0))
                pass
                
            return new_children
            
    except Exception as e:
        print(f"Error parsing trigger: {e}")
        raise PreventUpdate

    raise PreventUpdate


@app.callback(
    [
        Output("metrics-table", "children"),
        Output("pl-section", "children"),
        Output("bs-section", "children"),
        Output("cf-section", "children"),
        Output("shp-section", "children"),
        Output("ann-section", "children"),
        Output("invalid-tickers", "children"),
    ],
    Input({"type": "stock-dropdown", "index": ALL}, "value"),
)
def update_dashboard(selected_dropdown_values):
    # Convert dropdown values (list) to unique list of tickers
    selected_symbols = []
    if selected_dropdown_values:
        for val in selected_dropdown_values:
            if val and val not in selected_symbols:
                selected_symbols.append(val)

    if not selected_symbols:
        return (
            html.Div("Please select at least one stock."),
            "",
            "",
            "",
            "",
            "",
            "",
        )

    metrics_data = []
    invalid = []
    pl_blocks = []
    bs_blocks = []
    cf_blocks = []
    shp_blocks = []
    ann_blocks = []

    for symbol in selected_symbols:
        html_text = fetch_screener_html(symbol)
        if not html_text:
            invalid.append(symbol)
            continue

        fundamentals = fetch_screener_metrics(symbol)
        name = ticker_to_name.get(symbol, symbol)

        # ------- fundamentals row -------
        row = {"Name": name, "Symbol": symbol}
        row.update(fundamentals)
        metrics_data.append(row)

        # ------- detailed tables -------
        pl_df = parse_pl_table(html_text)
        bs_df = parse_bs_table(html_text)
        cf_df = parse_cf_table(html_text)
        shp_df = parse_shareholding_table(html_text)

        pl_blocks.append(
            html.Div(
                [
                    html.H4(name, style={"marginTop": "12px", "marginBottom": "6px"}),
                    df_to_dash_table(pl_df, max_cols=10),
                ]
            )
        )

        bs_blocks.append(
            html.Div(
                [
                    html.H4(name, style={"marginTop": "12px", "marginBottom": "6px"}),
                    df_to_dash_table(bs_df, max_cols=10),
                ]
            )
        )

        cf_blocks.append(
            html.Div(
                [
                    html.H4(name, style={"marginTop": "12px", "marginBottom": "6px"}),
                    df_to_dash_table(cf_df, max_cols=10),
                ]
            )
        )

        shp_blocks.append(
            html.Div(
                [
                    html.H4(name, style={"marginTop": "12px", "marginBottom": "6px"}),
                    df_to_dash_table(shp_df, max_cols=10),
                ]
            )
        )

        # ------- announcements / documents -------
        ann_list = parse_announcements(html_text, max_items=5)
        if ann_list:
            ann_items = []
            for item in ann_list:
                # Build message text for WhatsApp
                base_msg = f"{name}\n{item['title']}\n{item['detail']}\n{item['url']}"
                wa_text = quote_plus(base_msg)   # URL-encode
                wa_link = f"https://wa.me/?text={wa_text}"

                ann_items.append(
                    html.Div(
                        [
                            # Title (clickable, like )
                            html.A(
                                item["title"],
                                href=item["url"],
                                target="_blank",
                                style={
                                    "display": "block",
                                    "fontWeight": "500",
                                    "textDecoration": "none",
                                    "color": "#1a0dab",
                                    "marginBottom": "2px",
                                },
                            ),
                            # Detail line: date + short description
                            html.Div(
                                item["detail"],
                                style={
                                    "fontSize": "12px",
                                    "color": THEME["muted"],
                                    "marginBottom": "4px",
                                },
                            ),
                            # WhatsApp share link
                            html.A(
                                "Send on WhatsApp",
                                href=wa_link,
                                target="_blank",
                                style={
                                    "fontSize": "12px",
                                    "border": "1px solid #25D366",
                                    "borderRadius": "12px",
                                    "padding": "2px 8px",
                                    "color": "#25D366",
                                    "textDecoration": "none",
                                },
                            ),
                        ],
                        style={"marginBottom": "10px"},
                    )
                )

            ann_ui = html.Div(ann_items)
        else:
            ann_ui = html.Div(
                "No recent announcements found on Screener.",
                style={"fontSize": "12px", "color": THEME["muted"]},
            )

        ann_blocks.append(
            html.Div(
                [
                    html.H4(name, style={"marginTop": "12px", "marginBottom": "6px"}),
                    ann_ui,
                ]
            )
        )

    # ------- build metrics comparison table -------
    # Create metrics table
    # We want to show metrics side-by-side for each stock
    # Transpose the data: Rows = Metrics, Cols = Stocks
    
    if not metrics_data:
        metrics_table = html.Div("No metrics available.")
        
    # Extract all unique keys (metrics)
    all_metrics = [
        "Market Cap", "Current Price", "P/E Ratio", "Book Value", 
        "Price / Book", "Dividend Yield", "ROCE", "ROE", 
        "Face Value", "52-Week High", "52-Week Low", 
        "Sales YoY %", "Net Profit YoY %"
    ]
    
    # Build header row
    header = [html.Th("Metric", style={"textAlign": "left", "padding": "8px", "backgroundColor": "#f2f6fb"})]
    for d in metrics_data:
        header.append(html.Th(d["Symbol"], style={"textAlign": "left", "padding": "8px", "backgroundColor": "#f2f6fb"}))
        
    rows = []
    for m in all_metrics:
        cells = [html.Td(m, style={"fontWeight": "500", "padding": "6px 8px", "borderBottom": "1px solid #eee"})]
        for d in metrics_data:
            val = d.get(m, "N/A")
            cells.append(html.Td(val, style={"padding": "6px 8px", "borderBottom": "1px solid #eee"}))
        rows.append(html.Tr(cells))
        
    # Add "External Links" row
    link_cells = [html.Td("External Links", style={"fontWeight": "500", "padding": "6px 8px", "borderBottom": "1px solid #eee"})]
    for d in metrics_data:
        links = {
            "Screener": d.get("Screener Company Page"),
            "Google Finance": d.get("Google Finance Link"), # Assuming this key exists or will be added
            "MoneyControl": d.get("MoneyControl Link") # Assuming this key exists or will be added
        }
        buttons = []
        
        if links.get("Screener"):
            buttons.append(html.A("Screener", href=links["Screener"], target="_blank", style={"marginRight": "8px", "color": THEME["primary"], "fontSize": "11px", "textDecoration": "none", "border": "1px solid " + THEME["primary"], "padding": "2px 6px", "borderRadius": "4px"}))
            
        if links.get("Google Finance"):
            buttons.append(html.A("Google", href=links["Google Finance"], target="_blank", style={"marginRight": "8px", "color": "#ea4335", "fontSize": "11px", "textDecoration": "none", "border": "1px solid #ea4335", "padding": "2px 6px", "borderRadius": "4px"}))
            
        if links.get("MoneyControl"):
            buttons.append(html.A("MoneyControl", href=links["MoneyControl"], target="_blank", style={"color": "#28a745", "fontSize": "11px", "textDecoration": "none", "border": "1px solid #28a745", "padding": "2px 6px", "borderRadius": "4px"}))
            
        link_cells.append(html.Td(buttons, style={"padding": "6px 8px", "borderBottom": "1px solid #eee"}))
    rows.append(html.Tr(link_cells))

    metrics_table = html.Table(
        children=[
            html.Thead(html.Tr(header)),
            html.Tbody(rows)
        ],
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "13px"}
    )

    warn = ""
    if invalid:
        warn = html.Div([
            html.Span("⚠ Could not fetch data for: ", style={"fontWeight": "600"}),
            html.Span(", ".join(invalid), style={"fontFamily": "monospace"}),
            html.Br(),
            html.Span(
                "Note: Some stocks may not be available on Screener.in (e.g., new listings, special series like RE). Try selecting other stocks.",
                style={"fontSize": "11px", "color": THEME["muted"], "fontStyle": "italic", "marginTop": "4px", "display": "block"}
            )
        ])

    return (
        metrics_table,
        pl_blocks,
        bs_blocks,
        cf_blocks,
        shp_blocks,
        ann_blocks,
        warn,
    )

# =========================================================
# Chatbot Callbacks
# =========================================================

@app.callback(
    Output("chat-panel", "style"),
    [Input("chat-button", "n_clicks"),
     Input("close-chat", "n_clicks")],
    State("chat-panel", "style"),
    prevent_initial_call=True,
)
def toggle_chat_panel(open_clicks, close_clicks, current_style):
    """Toggle chat panel visibility"""
    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate
    
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if trigger_id == "chat-button":
        # Open chat
        current_style["display"] = "flex"
    elif trigger_id == "close-chat":
        # Close chat
        current_style["display"] = "none"
    
    return current_style


@app.callback(
    [Output("chat-messages", "children"),
     Output("chat-input", "value"),
     Output("chat-history", "data")],
    [Input("send-chat", "n_clicks"),
     Input("chat-input", "n_submit")],
    [State("chat-input", "value"),
     State("chat-history", "data"),
     State({"type": "stock-dropdown", "index": ALL}, "value")],
    prevent_initial_call=True,
)
def handle_chat_message(send_clicks, input_submit, user_message, chat_history, selected_stocks):
    """Handle user messages and generate AI responses"""
    if not user_message or user_message.strip() == "":
        raise PreventUpdate
    
    # Get current stock data for context
    stocks_data = []
    if selected_stocks:
        unique_stocks = [s for s in selected_stocks if s]
        for symbol in unique_stocks:
            try:
                metrics = fetch_screener_metrics(symbol)
                if metrics:
                    name = ticker_to_name.get(symbol, symbol)
                    stock_info = {"Name": name, "Symbol": symbol}
                    stock_info.update(metrics)
                    stocks_data.append(stock_info)
            except:
                pass
    
    # Generate AI response
    try:
        ai_response = chatbot.generate_response(user_message, stocks_data)
    except Exception as e:
        ai_response = f"Sorry, I encountered an error: {str(e)}"
    
    # Update chat history
    if chat_history is None:
        chat_history = []
    
    chat_history.append({"role": "user", "content": user_message})
    chat_history.append({"role": "assistant", "content": ai_response})
    
    # Build message UI
    messages = [
        html.Div(
            [
                # Welcome message
                html.Div(
                    "👋 Hello! I'm your AI stock market assistant. Ask me about stocks, comparisons, or investment strategies!",
                    style={
                        "backgroundColor": "#ffffff",
                        "padding": "12px 16px",
                        "borderRadius": "16px",
                        "marginBottom": "12px",
                        "maxWidth": "85%",
                        "fontSize": "14px",
                        "lineHeight": "1.5",
                        "boxShadow": "0 2px 4px rgba(0,0,0,0.08)",
                        "border": "1px solid #e9ecef",
                    },
                ),
                # Quick action buttons
                html.Div(
                    [
                        html.Div(
                            "Quick questions:",
                            style={
                                "fontSize": "11px",
                                "color": "#6c757d",
                                "marginBottom": "8px",
                                "fontWeight": "500",
                            },
                        ),
                        html.Button(
                            "Which stock is better for long-term?",
                            id={"type": "quick-question", "index": 0},
                            n_clicks=0,
                            style={
                                "backgroundColor": "#ffffff",
                                "color": THEME["primary"],
                                "border": f"1px solid {THEME['primary']}",
                                "borderRadius": "16px",
                                "padding": "6px 12px",
                                "fontSize": "11px",
                                "cursor": "pointer",
                                "marginRight": "6px",
                                "marginBottom": "6px",
                                "display": "inline-block",
                                "transition": "all 0.2s ease",
                            },
                        ),
                        html.Button(
                            "Compare selected stocks",
                            id={"type": "quick-question", "index": 1},
                            n_clicks=0,
                            style={
                                "backgroundColor": "#ffffff",
                                "color": THEME["primary"],
                                "border": f"1px solid {THEME['primary']}",
                                "borderRadius": "16px",
                                "padding": "6px 12px",
                                "fontSize": "11px",
                                "cursor": "pointer",
                                "marginRight": "6px",
                                "marginBottom": "6px",
                                "display": "inline-block",
                                "transition": "all 0.2s ease",
                            },
                        ),
                        html.Button(
                            "What are the key metrics to consider?",
                            id={"type": "quick-question", "index": 2},
                            n_clicks=0,
                            style={
                                "backgroundColor": "#ffffff",
                                "color": THEME["primary"],
                                "border": f"1px solid {THEME['primary']}",
                                "borderRadius": "16px",
                                "padding": "6px 12px",
                                "fontSize": "11px",
                                "cursor": "pointer",
                                "marginBottom": "6px",
                                "display": "inline-block",
                                "transition": "all 0.2s ease",
                            },
                        ),
                    ],
                    style={"marginBottom": "10px"},
                ),
            ]
        )
    ]
    
    for msg in chat_history:
        if msg["role"] == "user":
            messages.append(
                html.Div(
                    msg["content"],
                    style={
                        "backgroundColor": THEME["primary"],
                        "color": "white",
                        "padding": "10px 14px",
                        "borderRadius": "16px 16px 4px 16px",
                        "marginBottom": "12px",
                        "marginLeft": "auto",
                        "maxWidth": "80%",
                        "fontSize": "14px",
                        "lineHeight": "1.5",
                        "textAlign": "left",
                        "boxShadow": "0 2px 6px rgba(17, 103, 177, 0.3)",
                        "wordWrap": "break-word",
                    },
                )
            )
        else:
            # Use dcc.Markdown for AI responses to render markdown properly
            messages.append(
                html.Div(
                    dcc.Markdown(
                        msg["content"],
                        style={
                            "margin": "0",
                            "fontSize": "14px",
                            "lineHeight": "1.6",
                            "color": "#212529",
                        },
                    ),
                    style={
                        "backgroundColor": "#ffffff",
                        "padding": "12px 16px",
                        "borderRadius": "16px 16px 16px 4px",
                        "marginBottom": "12px",
                        "maxWidth": "85%",
                        "boxShadow": "0 2px 4px rgba(0,0,0,0.08)",
                        "border": "1px solid #e9ecef",
                        "wordWrap": "break-word",
                    },
                )
            )
    
    return messages, "", chat_history


# Callback to handle quick question buttons
@app.callback(
    Output("chat-input", "value", allow_duplicate=True),
    Input({"type": "quick-question", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_quick_question(n_clicks_list):
    """Auto-fill chat input when quick question button is clicked"""
    ctx = callback_context
    if not ctx.triggered:
        raise PreventUpdate
    
    # Get which button was clicked
    trigger = ctx.triggered[0]
    if not trigger["value"]:  # No actual click
        raise PreventUpdate
    
    # Extract the button index from the trigger ID
    import json
    button_id = json.loads(trigger["prop_id"].split(".")[0])
    index = button_id["index"]
    
    # Map index to question text
    questions = {
        0: "Which stock is better for long-term?",
        1: "Compare selected stocks",
        2: "What are the key metrics to consider?",
    }
    
    return questions.get(index, "")

if __name__ == "__main__":
    app.run(debug=True)
