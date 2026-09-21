"""Local Chrome-based Google Maps review extractor."""

from __future__ import annotations

import csv
import io
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, jsonify, render_template, request, send_file
from openpyxl import load_workbook
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT / "data" / "jobs"
CHROME_PROFILE_DIR = ROOT / "data" / "chrome-profile"
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
COMPANY_ALIASES = {"company", "company name", "company_name", "business", "business name", "business_name", "name", "title"}
LOCATION_ALIASES = {"location", "address", "city", "place", "area", "locality"}
OUTPUT_COLUMNS = [
    "company", "address", "match_confidence", "google_review_count", "extracted_reviews",
    "reviewer", "rating", "review", "date", "owner_reply", "replied_by", "reply_date",
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
ACTIVE_JOB: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def find_column(headers: list[str], aliases: set[str]) -> str | None:
    normalized = {normalize(header): header for header in headers}
    return next((normalized[alias] for alias in aliases if alias in normalized), None)


def read_upload(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".csv":
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text), dialect=dialect)]

    workbook = load_workbook(path, read_only=True, data_only=True)
    values = workbook.active.iter_rows(values_only=True)
    headers = [str(value or "").strip() for value in next(values, ())]
    return [{headers[index]: str(value or "").strip() for index, value in enumerate(row) if index < len(headers)} for row in values]


def match_confidence(query: str, title: str, location: str, address: str) -> str:
    name_score = SequenceMatcher(None, normalize(query), normalize(title)).ratio()
    address_score = SequenceMatcher(None, normalize(location), normalize(address)).ratio() if location else 1.0
    return f"{(name_score * 0.8 + address_score * 0.2) * 100:.2f}%"


def update_job(job_id: str, **values: object) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def chrome_driver(profile_dir: Path) -> webdriver.Chrome:
    options = Options()
    if profile_dir:
        options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Enable headless mode in Docker or when HEADLESS=1 environment variable is set
    if os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes") or os.name != "nt":
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
    return driver


def first_text(driver: webdriver.Chrome, selectors: list[str]) -> str:
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            text = element.text.strip()
            if text:
                return text
    return ""


def select_search_result(driver: webdriver.Chrome) -> None:
    """Open the first Maps result when a name/location query returns a list."""
    title = first_text(driver, ["h1.DUwDvf", "h1"])
    if title and normalize(title) != "results":
        return
    for result in driver.find_elements(By.CSS_SELECTOR, ".Nv2PK a.hfpxzc, a.hfpxzc[href*='/place/']"):
        if not result.is_displayed():
            continue
        href = result.get_attribute("href")
        if not href:
            continue
        driver.get(href)
        for _ in range(12):
            time.sleep(0.5)
            title = first_text(driver, ["h1.DUwDvf", "h1"])
            if title and normalize(title) != "results":
                return


def review_total(driver: webdriver.Chrome) -> int:
    source = driver.page_source
    matches = re.findall(r"aria-label=[\"']([\d,]+) reviews[\"']", source, re.I)
    if not matches:
        matches = re.findall(r"\b([\d,]+) reviews\b", driver.find_element(By.TAG_NAME, "body").text, re.I)
    try:
        return int(matches[0].replace(",", "")) if matches else 0
    except ValueError:
        return 0


def clean_address(addr: str) -> str:
    return re.sub(r"^[^\w\d]+", "", addr or "").strip()


def dismiss_dialogs(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script("""
            const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
            const target = buttons.find(b => {
                const txt = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase().trim();
                return txt === 'dismiss' || txt === 'stay signed out' || txt === 'reject all' || txt === 'not now';
            });
            if (target) target.click();
        """)
    except Exception:
        pass


def click_more_reviews(driver: webdriver.Chrome) -> bool:
    dismiss_dialogs(driver)
    elements = driver.find_elements(By.XPATH, "//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more reviews')]")
    for element in elements:
        if not element.is_displayed():
            continue
        try:
            driver.execute_script("arguments[0].click()", element)
            return True
        except WebDriverException:
            continue
    return False


def sort_by_newest(driver: webdriver.Chrome) -> None:
    try:
        dismiss_dialogs(driver)
        sort_btns = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'Sort') or contains(., 'Sort')]")
        if sort_btns:
            driver.execute_script("arguments[0].click();", sort_btns[0])
            time.sleep(1.5)
            menu_items = driver.find_elements(By.XPATH, "//div[@role='menuitemradio' or @role='menuitem']//div[contains(text(), 'Newest')]/ancestor::div[@role='menuitemradio' or @role='menuitem'] | //div[@role='menuitemradio' or @role='menuitem'][contains(., 'Newest')]")
            if menu_items:
                driver.execute_script("arguments[0].click();", menu_items[0])
                time.sleep(2)
    except Exception:
        pass


def open_reviews(driver: webdriver.Chrome) -> None:
    dismiss_dialogs(driver)
    for selector in (".F7nice", "[aria-label*='reviews' i]", "[aria-label*='stars' i]"):
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if elements:
            try:
                driver.execute_script("arguments[0].click()", elements[0])
                break
            except WebDriverException:
                pass
    time.sleep(2)
    dismiss_dialogs(driver)
    click_more_reviews(driver)
    time.sleep(2)


def extract_visible_reviews(driver: webdriver.Chrome) -> list[dict[str, str]]:
    try:
        return driver.execute_script(r"""
            const moreButtons = document.querySelectorAll('div.jftiEf button.w8nwRe, div.jftiEf .MyEned button, div.jftiEf .wiI7pd button');
            moreButtons.forEach(button => {
                try {
                    const text = (button.innerText || '').trim().toLowerCase();
                    const aria = (button.getAttribute('aria-label') || '').toLowerCase();
                    if ((text === 'more' || text === 'see more' || button.classList.contains('w8nwRe')) 
                        && !aria.includes('action') 
                        && !aria.includes('by') 
                        && !aria.includes('photo') 
                        && !aria.includes('profile')) {
                        button.click();
                    }
                } catch(e) {}
            });

            const cards = document.querySelectorAll('div.jftiEf');
            const items = [];
            for (let i = 0; i < cards.length; i++) {
                const card = cards[i];
                const text = selector => card.querySelector(selector)?.innerText?.trim() || '';
                const attr = selector => card.querySelector(selector)?.getAttribute('aria-label') || '';
                const reviewer = card.querySelector('.d4r55')?.innerText?.trim() || card.querySelector('button.al6Kxe')?.innerText?.trim() || '';
                const rating = (attr('.kvMYJc, [role="img"][aria-label*="star"]').match(/[0-5](?:\.\d+)?/) || [''])[0];
                const review = text('.wiI7pd, .MyEned span, .Jtu6Td span');
                const date = text('.rsqaWe');
                const reviewId = card.getAttribute('data-review-id') || card.getAttribute('data-jslog') || '';
                const whole = card.innerText || '';
                const ownerMarker = /Response from the owner|Owner response/i.exec(whole);
                const ownerReply = ownerMarker ? whole.slice(ownerMarker.index).replace(/^(Response from the owner|Owner response)\s*/i, '').trim() : '';

                if (reviewer || review) {
                    items.push({
                        review_id: reviewId,
                        reviewer: reviewer,
                        rating: rating,
                        review: review,
                        date: date,
                        owner_reply: ownerReply,
                        replied_by: '',
                        reply_date: ''
                    });
                }
            }
            return items;
        """) or []
    except Exception:
        return []


def scroll_and_extract_reviews(driver: webdriver.Chrome, expected: int, progress_callback=None) -> list[dict[str, str]]:
    all_reviews = []
    seen = set()
    unchanged = 0
    max_unchanged = 16
    max_loops = max(350, (expected // 4) + 60) if expected else 600
    main_window = driver.current_window_handle

    for loop in range(max_loops):
        # Auto-close any extraneous tabs if opened accidentally
        try:
            if len(driver.window_handles) > 1:
                for handle in list(driver.window_handles):
                    if handle != main_window:
                        driver.switch_to.window(handle)
                        driver.close()
                driver.switch_to.window(main_window)
        except Exception:
            pass

        dismiss_dialogs(driver)
        visible_cards = extract_visible_reviews(driver)
        new_items = 0

        for card in visible_cards:
            rev_id = card.get("review_id", "").strip()
            reviewer = card.get("reviewer", "").strip()
            date = card.get("date", "").strip()
            review_text = card.get("review", "").strip()

            # Unique key combining review_id, reviewer, date and snippet
            if rev_id:
                key = ("id", rev_id)
            else:
                key = ("content", normalize(reviewer), normalize(date), normalize(review_text)[:60])

            if key not in seen and (reviewer or review_text):
                seen.add(key)
                all_reviews.append({
                    "reviewer": reviewer,
                    "rating": card.get("rating", ""),
                    "review": review_text,
                    "date": date,
                    "owner_reply": card.get("owner_reply", ""),
                    "replied_by": "",
                    "reply_date": ""
                })
                new_items += 1

        if new_items > 0:
            unchanged = 0
            if progress_callback:
                progress_callback(len(all_reviews), expected)
        else:
            unchanged += 1

        if expected and len(all_reviews) >= expected:
            break

        if unchanged >= max_unchanged:
            # Check if loading spinner is still spinning
            is_loading = driver.execute_script("""
                return Boolean(document.querySelector('.q67rwe, .m6QErb .loading, div[aria-label*="Loading"]'));
            """)
            if not is_loading or unchanged >= (max_unchanged + 6):
                break

        # Scroll down
        driver.execute_script("""
            const cards = document.querySelectorAll('div.jftiEf');
            if (cards.length > 0) {
                const lastCard = cards[cards.length - 1];
                lastCard.scrollIntoView({block: 'center'});
                let node = lastCard.parentElement;
                while (node) {
                    const style = getComputedStyle(node);
                    if (/(auto|scroll)/.test(style.overflowY)) {
                        node.scrollTop = node.scrollHeight;
                        node.dispatchEvent(new Event('scroll', { bubbles: true }));
                        break;
                    }
                    node = node.parentElement;
                }
            }
        """)
        time.sleep(1.5)

    return all_reviews


def scrape_place(driver: webdriver.Chrome, company: str, location: str, progress_callback=None) -> tuple[dict[str, str], list[dict[str, str]]]:
    query = f"{company}, {location}" if location else company
    driver.get(f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}")
    time.sleep(4)
    select_search_result(driver)
    title = first_text(driver, ["h1.DUwDvf", "h1"])
    address = clean_address(first_text(driver, ["button[data-item-id='address']", "button[aria-label*='Address']"]))
    open_reviews(driver)
    sort_by_newest(driver)
    expected = review_total(driver)
    reviews = scroll_and_extract_reviews(driver, expected, progress_callback=progress_callback)
    place = {
        "company": title or company,
        "address": address or location,
        "match_confidence": match_confidence(company, title or company, location, address or location),
        "google_review_count": str(expected) if expected else "",
        "extracted_reviews": str(len(reviews)),
    }
    return place, reviews


def run_job(job_id: str, rows: list[dict[str, str]], company_column: str, location_column: str | None) -> None:
    global ACTIVE_JOB
    job_dir = JOBS_DIR / job_id
    output_path = job_dir / "reviews.csv"
    driver = None
    try:
        inputs = []
        for row in rows:
            company = row.get(company_column, "").strip()
            location = row.get(location_column, "").strip() if location_column else ""
            if company:
                inputs.append((company, location))
        if not inputs:
            raise ValueError("No valid company names were found.")
        update_job(job_id, status="running", message="Opening local Chrome and loading Google Maps reviews.")
        driver = chrome_driver(CHROME_PROFILE_DIR)
        total_reviews = 0
        matched = 0
        incomplete = 0
        with output_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for index, (company, location) in enumerate(inputs, start=1):
                def on_progress(count: int, target: int):
                    target_str = f" / {target}" if target else ""
                    update_job(job_id, message=f"Scraping {index} of {len(inputs)}: {company} ({count}{target_str} reviews)")

                update_job(job_id, message=f"Scraping {index} of {len(inputs)}: {company}")
                place, reviews = scrape_place(driver, company, location, progress_callback=on_progress)
                if place["company"]:
                    matched += 1
                expected = int(place["google_review_count"] or 0)
                if expected and len(reviews) < expected:
                    incomplete += 1
                if not reviews:
                    writer.writerow(place)
                for review in reviews:
                    writer.writerow({**place, **review})
                total_reviews += len(reviews)
        message = "Scraping complete."
        if incomplete:
            message = f"Scraping complete with {incomplete} incomplete company result(s). Google did not render every public review in Chrome."
        update_job(job_id, status="complete", message=message, completed_at=now(), companies=len(inputs), matched=matched, reviews=total_reviews, incomplete_companies=incomplete, reviews_file=str(output_path))
    except Exception as error:
        update_job(job_id, status="failed", message=f"{type(error).__name__}: {error}", completed_at=now())
    finally:
        if driver:
            driver.quit()
        with JOBS_LOCK:
            if ACTIVE_JOB == job_id:
                ACTIVE_JOB = None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "active_job": ACTIVE_JOB})


@app.post("/api/jobs")
def create_job():
    global ACTIVE_JOB
    upload = request.files.get("file")
    company = request.form.get("company", "").strip()
    location = request.form.get("location", "").strip()
    is_upload = bool(upload and upload.filename)
    if not is_upload and not company:
        return jsonify({"error": "Enter a company name or upload a CSV/Excel file."}), 400
    with JOBS_LOCK:
        if ACTIVE_JOB:
            return jsonify({"error": "A scraping job is already running."}), 409
        job_id = uuid.uuid4().hex[:12]
        ACTIVE_JOB = job_id
        JOBS[job_id] = {"id": job_id, "status": "preparing", "message": "Preparing job.", "created_at": now()}
    try:
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        if is_upload:
            extension = Path(upload.filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError("Only CSV and XLSX files are supported.")
            input_path = job_dir / f"input{extension}"
            upload.save(input_path)
            rows = read_upload(input_path)
            if not rows:
                raise ValueError("The uploaded file is empty.")
            headers = list(rows[0])
            company_column = find_column(headers, COMPANY_ALIASES)
            location_column = find_column(headers, LOCATION_ALIASES)
            if not company_column:
                raise ValueError("The file needs a company column.")
        else:
            rows = [{"company": company, "location": location}]
            company_column, location_column = "company", "location"
        update_job(job_id, rows=len(rows))
        threading.Thread(target=run_job, args=(job_id, rows, company_column, location_column), daemon=True).start()
        return jsonify({"job_id": job_id}), 202
    except Exception as error:
        update_job(job_id, status="failed", message=str(error), completed_at=now())
        with JOBS_LOCK:
            ACTIVE_JOB = None
        return jsonify({"error": str(error)}), 400


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        payload = {key: value for key, value in job.items() if key != "reviews_file"}
    payload["download"] = f"/api/jobs/{job_id}/download" if job.get("reviews_file") else None
    return jsonify(payload)


@app.get("/api/jobs/<job_id>/preview")
def preview(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        path = job and job.get("reviews_file")
    if not path or not Path(path).is_file():
        return jsonify({"error": "The preview is not ready."}), 404
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
        return jsonify({
            "total": len(rows),
            "reviews": rows[:50],
        })
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@app.get("/api/jobs/<job_id>/download")
def download(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        path = job and job.get("reviews_file")
    if not path or not Path(path).is_file():
        return jsonify({"error": "The CSV is not ready."}), 404
    return send_file(path, as_attachment=True, download_name=f"reviews-{job_id}.csv")


def create_app() -> Flask:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return app


