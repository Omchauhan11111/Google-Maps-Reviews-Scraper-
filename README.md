# Maps Review Extractor

A local Windows application that searches Google Maps in Chrome and exports public reviews to one CSV file. It supports one company at a time or a bulk CSV/XLSX upload. Docker and an external scraper binary are not used.

## Setup

Open PowerShell in this folder and run once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Google Chrome must be installed. Selenium starts Chrome locally and Selenium Manager obtains a compatible ChromeDriver when required.

## Run

Double-click `start.bat`. The application is available at <http://127.0.0.1:5000>.

Choose one input method:

- **Single company:** company name and optional location.
- **Bulk upload:** `.csv` or `.xlsx` with a company column and an optional location column.

Recognized company headers: `company`, `company_name`, `business`, `business_name`, `name`, and `title`.

Recognized location headers: `location`, `address`, `city`, `place`, `area`, and `locality`.

Example:

```csv
company,location
Ascentium,Ocean Financial Center Singapore 049315
Vistra Singapore,Republic Plaza Singapore 048619
```

## Output

Each job creates a downloadable UTF-8 CSV in `data/jobs/<job-id>/reviews.csv`. Every review row includes the matched company, address, confidence, Google review count, extracted review count, reviewer, rating, review text, date, and any public owner reply that Chrome renders.

## How It Works

The Flask app accepts the input, then one background worker opens a local Chrome session. For each company, it searches Google Maps, opens the reviews panel, expands visible review text, and keeps scrolling the reviews container until the number of rendered review cards stops increasing or Google reports that all visible reviews have loaded. It writes the resulting rows directly to CSV and closes Chrome when the job finishes.

The Google review total is read dynamically for every company. There is no fixed review-count limit in this project. The status panel compares Google's displayed count with the extracted count and warns when Chrome did not render every review.

Google can change its page structure, require a human check, or temporarily show fewer public reviews. In those cases the app reports an incomplete result instead of presenting it as complete. Use the tool only for data you are allowed to collect and process.
