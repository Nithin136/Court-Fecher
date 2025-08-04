from flask import Flask, render_template, request, jsonify, send_file
import requests
from bs4 import BeautifulSoup
import sqlite3
import json
import os
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Database setup
def init_db():
    conn = sqlite3.connect('court_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_type TEXT,
            case_number TEXT,
            filing_year TEXT,
            query_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            raw_response TEXT,
            parsed_data TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()


class CourtDataFetcher:
    def __init__(self):
        self.base_url = "https://delhihighcourt.nic.in"
        self.search_url = f"{self.base_url}/case_status.asp"

    def setup_driver(self):
        """Setup Chrome driver with appropriate options"""
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")

        try:
            driver = webdriver.Chrome(options=chrome_options)
            return driver
        except Exception as e:
            logger.error(f"Error setting up Chrome driver: {e}")
            return None

    def fetch_case_data(self, case_type, case_number, filing_year):
        """Fetch case data from Delhi High Court"""
        driver = self.setup_driver()
        if not driver:
            return {"error": "Failed to initialize browser"}

        try:
            # Navigate to the case status page
            driver.get(self.search_url)

            # Wait for page to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "case_type"))
            )

            # Fill the form
            case_type_select = Select(driver.find_element(By.NAME, "case_type"))
            case_type_select.select_by_visible_text(case_type)

            case_number_input = driver.find_element(By.NAME, "case_no")
            case_number_input.clear()
            case_number_input.send_keys(case_number)

            year_input = driver.find_element(By.NAME, "case_year")
            year_input.clear()
            year_input.send_keys(filing_year)

            # Handle CAPTCHA if present (manual approach for demo)
            # In production, you might use a CAPTCHA solving service
            captcha_element = driver.find_elements(By.NAME, "captcha")
            if captcha_element:
                logger.info("CAPTCHA detected - manual intervention required")
                # For demo purposes, we'll skip CAPTCHA validation
                # In real implementation, you'd integrate with a CAPTCHA solving service

            # Submit the form
            submit_button = driver.find_element(By.NAME, "Submit")
            submit_button.click()

            # Wait for results
            time.sleep(3)

            # Parse the results
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')

            # Extract case details (this will vary based on actual site structure)
            case_data = self.parse_case_details(soup)

            return case_data

        except Exception as e:
            logger.error(f"Error fetching case data: {e}")
            return {"error": str(e)}
        finally:
            driver.quit()

    def parse_case_details(self, soup):
        """Parse case details from the HTML response"""
        try:
            case_data = {
                "parties": [],
                "filing_date": None,
                "next_hearing_date": None,
                "orders": [],
                "status": "Active"
            }

            # Look for common patterns in court websites
            # This is a generic parser - you'll need to adapt based on actual site structure

            # Find party names
            party_elements = soup.find_all(['td', 'div'], text=re.compile(r'vs\.?|v/s', re.I))
            for element in party_elements:
                parent = element.parent
                if parent:
                    text = parent.get_text(strip=True)
                    if 'vs' in text.lower() or 'v/s' in text.lower():
                        parties = re.split(r'\s+vs?\.?\s+|\s+v/s\s+', text, flags=re.I)
                        case_data["parties"] = [p.strip() for p in parties if p.strip()]
                        break

            # Find dates
            date_pattern = r'\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b'
            dates = re.findall(date_pattern, soup.get_text())
            if dates:
                case_data["filing_date"] = dates[0] if len(dates) > 0 else None
                case_data["next_hearing_date"] = dates[-1] if len(dates) > 1 else None

            # Find order/judgment links
            pdf_links = soup.find_all('a', href=re.compile(r'\.pdf$', re.I))
            for link in pdf_links:
                href = link.get('href')
                if href:
                    full_url = urljoin(self.base_url, href)
                    case_data["orders"].append({
                        "title": link.get_text(strip=True) or "Order Document",
                        "url": full_url,
                        "date": None  # Would need to parse from context
                    })

            return case_data

        except Exception as e:
            logger.error(f"Error parsing case details: {e}")
            return {"error": "Failed to parse case details"}


def save_query_to_db(case_type, case_number, filing_year, raw_response, parsed_data, status):
    """Save query and response to database"""
    conn = sqlite3.connect('court_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO queries (case_type, case_number, filing_year, raw_response, parsed_data, status)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (case_type, case_number, filing_year, str(raw_response), json.dumps(parsed_data), status))
    conn.commit()
    conn.close()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search', methods=['POST'])
def search_case():
    try:
        data = request.get_json()
        case_type = data.get('case_type')
        case_number = data.get('case_number')
        filing_year = data.get('filing_year')

        if not all([case_type, case_number, filing_year]):
            return jsonify({"error": "All fields are required"}), 400

        # Initialize fetcher
        fetcher = CourtDataFetcher()

        # Fetch case data
        case_data = fetcher.fetch_case_data(case_type, case_number, filing_year)

        # Save to database
        status = "success" if "error" not in case_data else "error"
        save_query_to_db(case_type, case_number, filing_year, "", case_data, status)

        return jsonify(case_data)

    except Exception as e:
        logger.error(f"Error in search_case: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/download_pdf')
def download_pdf():
    """Download PDF from court website"""
    try:
        pdf_url = request.args.get('url')
        if not pdf_url:
            return jsonify({"error": "PDF URL required"}), 400

        response = requests.get(pdf_url, stream=True)
        if response.status_code == 200:
            filename = os.path.basename(urlparse(pdf_url).path) or "document.pdf"

            # Save temporarily
            temp_path = f"/tmp/{filename}"
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            return send_file(temp_path, as_attachment=True, download_name=filename)
        else:
            return jsonify({"error": "Failed to download PDF"}), 400

    except Exception as e:
        logger.error(f"Error downloading PDF: {e}")
        return jsonify({"error": "Download failed"}), 500


@app.route('/history')
def get_history():
    """Get query history"""
    try:
        conn = sqlite3.connect('court_data.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT case_type, case_number, filing_year, query_timestamp, status
            FROM queries
            ORDER BY query_timestamp DESC
            LIMIT 50
        ''')

        history = []
        for row in cursor.fetchall():
            history.append({
                "case_type": row[0],
                "case_number": row[1],
                "filing_year": row[2],
                "timestamp": row[3],
                "status": row[4]
            })

        conn.close()
        return jsonify(history)

    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({"error": "Failed to fetch history"}), 500


if __name__ == '__main__':
    # Initialize database
    init_db()

    # Run the app
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)