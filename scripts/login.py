import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "data" / "chrome-profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("Opening Google Maps Chrome Browser with your Persistent Profile...")
print(f"Profile path: {PROFILE_DIR}")
print("Please Sign-in to your Google Account in the opened Chrome window.")
print("Once signed in, you can close the Chrome window or press Enter here.")
print("=" * 60)

options = Options()
options.add_argument(f"--user-data-dir={PROFILE_DIR}")
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(options=options)
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})

try:
    driver.get("https://accounts.google.com/ServiceLogin?continue=https://www.google.com/maps")
    input("\n--> Sign in in the browser, and then press ENTER in this terminal when finished...")
    print("Session saved successfully!")
except Exception as e:
    print(f"Error: {e}")
finally:
    try:
        driver.quit()
    except Exception:
        pass
    print("Done! You can now run start.bat and your scraper will use this signed-in profile.")
