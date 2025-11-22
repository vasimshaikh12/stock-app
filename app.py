import re
from functools import lru_cache
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dash import Dash, dcc, html, Input, Output
from urllib.parse import quote_plus
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
csv_path = os.path.join(BASE_DIR, "master_merged_equity_list.csv")

# =========================================================
# Screener helpers
# =========================================================

# Special cases where Screener code is not obvious
SCREENER_SPECIAL = {
    "RAJESH.BO": "544291",      # Rajesh Power Services (BSE numeric code)
    # add more overrides here if needed
}

def ticker_to_screener_code(symbol):
    """
    Convert a yfinance-style ticker to Screener company code.
    Works for most NSE tickers, plus special mapping for tricky ones.
    """
    if symbol in SCREENER_SPECIAL:
        return SCREENER_SPECIAL[symbol]
    if symbol and symbol.endswith(".NS"):
        return symbol[:-3]
    if symbol and symbol.endswith(".BO") and symbol[:-3].isdigit():
        # example: "500325.BO" -> "500325"
        return symbol[:-3]
    if symbol and "." in symbol:
        return symbol.split(".")[0]
    return symbol or None

def screener_base(code):
    return f"https://www.screener.in/company/{code}/"

@lru_cache(maxsize=128)
def fetch_screener_html(symbol):
    """
    Download Screener consolidated page HTML for a symbol.
    Cached to avoid repeated network calls.
    """
    code = ticker_to_screener_code(symbol)
    if not code:
        return None
    url = screener_base(code) + "consolidated/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
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
    Fetch core fundamentals + simple YoY from Screener:
    - Market Cap, Current Price, High/Low, P/E, Book Value, Price/Book,
      Dividend Yield, ROCE, ROE, Face Value, 52W High/Low.
    - Sales YoY %, Net Profit YoY % (last full year vs previous year).
    """
    html_text = fetch_screener_html(symbol)
    if not html_text:
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

    # ---------- Price / Book ----------
    pb = pb_raw
    if pb is None:
        cp_num = _num(current_price)
        bv_num = _num(book_value)
        if cp_num is not None and bv_num not in (None, 0):
            pb = f"{cp_num / bv_num:.2f}"

    # ---------- 52 week high/low ----------
    high_52, low_52 = None, None
    if high_low:
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
    links = {
        "Screener Company Page": screener_base(code) if code else None,
        "Screener Balance Sheet": (screener_base(code) + "consolidated/#balance-sheet") if code else None,
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
        "Screener Company Page": links.get("Screener Company Page"),
        "Screener Balance Sheet": links.get("Screener Balance Sheet"),
    }

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
    Parse recent announcements (documents/news) from Screener 'Documents' section.
    Returns a list of dicts: [{title, detail, url}, ...].
    We try to mimic Screener's view: title on first line, date+summary below.
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

# =========================================================
# 🔹 Load master list of all listed companies for dropdown
# =========================================================

# Load CSV using relative path
master_df = pd.read_csv(csv_path)

# Keep only rows that actually have Ticker + CompanyName
master_df = master_df[
    master_df["Ticker"].notna() & master_df["CompanyName"].notna()
].copy()

dropdown_options = [
    {
        "label": f"{row['CompanyName']} ({row['Ticker']})",
        "value": row["Ticker"],
    }
    for _, row in master_df.iterrows()
]

# Used everywhere to show nice names instead of raw tickers
ticker_to_name = dict(zip(master_df["Ticker"], master_df["CompanyName"]))

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
                    "For educational / analysis use",
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
                        dcc.Dropdown(
                            id="stock-dropdown",
                            options=dropdown_options,
                            # default: first two tickers from your master file
                            value=[opt["value"] for opt in dropdown_options[:2]],
                            multi=True,
                            placeholder="Search or select stocks...",
                        ),
                        html.Div(
                            "Select 1 or more stocks to pull Screener data into the dashboard.",
                            style={
                                "fontSize": "12px",
                                "color": THEME["muted"],
                                "marginTop": "8px",
                            },
                        ),
                    ]
                ),
                # Fundamentals comparison
                card(
                    children=[
                        html.Div(
                            "Fundamental Comparison (Screener key metrics)",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(id="metrics-table"),
                    ]
                ),
                # Profit & Loss
                card(
                    children=[
                        html.Div(
                            "Profit & Loss (from Screener)",
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
                            "Balance Sheet (from Screener)",
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
                            "Cash Flows (from Screener)",
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
                            "Shareholding Pattern (from Screener)",
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
                            "Documents / Announcements (from Screener / BSE/NSE)",
                            style={"fontWeight": "600", "marginBottom": "4px", "fontSize": "16px"},
                        ),
                        html.Div(
                            "Top 5 latest company announcements as shown on Screener (links open in new tab).",
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
    [Input("stock-dropdown", "value")],
)
def update_dashboard(selected_symbols):
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
                            # Title (clickable, like Screener)
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
    metric_keys = [
        "Market Cap",
        "Current Price",
        "P/E Ratio",
        "Book Value",
        "Price / Book",
        "Dividend Yield",
        "ROCE",
        "ROE",
        "Face Value",
        "52-Week High",
        "52-Week Low",
        "Sales YoY %",
        "Net Profit YoY %",
        "Screener Company Page",
        "Screener Balance Sheet",
    ]

    headers = ["Metric"] + [
        f"{row['Name']} ({row['Symbol']})" for row in metrics_data
    ]

    body_rows = []
    for metric in metric_keys:
        row_cells = [metric]
        for stock_row in metrics_data:
            val = stock_row.get(metric, "N/A")
            if metric in ["Screener Company Page", "Screener Balance Sheet"]:
                if isinstance(val, str):
                    label = "Open Screener" if "Company" in metric else "Open Balance Sheet"
                    val = html.A(label, href=val, target="_blank")
                else:
                    val = "N/A"
            row_cells.append(val)
        body_rows.append(row_cells)

    metrics_table = html.Table(
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
                                cell,
                                style={
                                    "borderBottom": f"1px solid {THEME['border']}",
                                    "padding": "5px 8px",
                                    "fontWeight": "600" if j == 0 else "400",
                                    "whiteSpace": "nowrap" if j == 0 else "normal",
                                },
                            )
                            for j, cell in enumerate(row)
                        ]
                    )
                    for row in body_rows
                ]
            ),
        ],
    )

    warn = ""
    if invalid:
        warn = "⚠ Could not fetch data for: " + ", ".join(invalid)

    return (
        metrics_table,
        pl_blocks,
        bs_blocks,
        cf_blocks,
        shp_blocks,
        ann_blocks,
        warn,
    )

if __name__ == "__main__":
    app.run(debug=True)
