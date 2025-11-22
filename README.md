# Screener Fundamental & Financials Dashboard

A comprehensive Dash web application for analyzing Indian stock fundamentals using data from Screener.in. This dashboard provides detailed financial analysis including P&L statements, Balance Sheets, Cash Flows, Shareholding Patterns, and company announcements.

## Features

- **Fundamental Comparison**: Compare key metrics (Market Cap, P/E Ratio, ROE, ROCE, etc.) across multiple stocks
- **Profit & Loss Statements**: View consolidated P&L data in Rs. Crores
- **Balance Sheet Analysis**: Analyze equity capital, reserves, borrowings, and total assets
- **Cash Flow Statements**: Review operating, investing, and financing cash flows
- **Shareholding Pattern**: View promoter, FII, DII, and public shareholding percentages
- **Company Announcements**: Latest announcements with WhatsApp sharing capability
- **Multi-Stock Comparison**: Select and compare multiple stocks simultaneously

## Prerequisites

- Python 3.8 or higher
- `master_merged_equity_list.csv` file in the project root directory

## Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd screener-dash-app
```

2. Create a virtual environment:
```bash
python -m venv venv
```

3. Activate the virtual environment:
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

4. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Ensure `master_merged_equity_list.csv` is in the project root directory
2. Run the application:
```bash
python app.py
```

3. Open your web browser and navigate to `http://127.0.0.1:8050/`

4. Select one or more stocks from the dropdown to view their financial data

## Project Structure

```
screener-dash-app/
├── app.py                          # Main Dash application
├── requirements.txt                # Python dependencies
├── master_merged_equity_list.csv   # Stock ticker master list
├── assets/
│   └── styles.css                  # Custom CSS styles
├── scripts/
│   └── setup_venv.sh              # Virtual environment setup script
└── tests/
    └── test_app.py                 # Unit tests
```

## Data Source

This application fetches data from [Screener.in](https://www.screener.in), a popular Indian stock analysis platform. The data is scraped in real-time when you select stocks.

## Notes

- This tool is for educational and analysis purposes only
- Data fetching depends on Screener.in's availability and structure
- Some stocks may not be available if they're not listed on Screener.in
- The application uses caching to minimize network requests

## License

This project is for educational use only.
