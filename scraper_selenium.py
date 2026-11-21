# scraper_selenium.py
# Collects all Premier League 2023-2024 matches from the TheSportsDB website
# using a real Chrome browser (Selenium), and records performance metrics
# from Chrome's network log.

import os
import csv
import json
import time
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

# ---------------- Settings ----------------
BASE_URL = "https://www.thesportsdb.com/season/4328-english-premier-league/2023-2024&r={}"
ROUNDS = 38
DELAY = 5                 # seconds between page loads (12 pages/minute)
MAX_RETRIES = 4           # attempts per round
RETRY_WAIT = 60           # first wait after a failure, then doubled (60, 120, 240)
METHOD = "selenium"

# ---------------- Metrics counters ----------------
request_count = 0         # ALL HTTP requests made by the browser (HTML, images, CSS, JS...)
total_bytes = 0           # bytes received after decompression (same meaning as len(response.content))
wire_bytes = 0            # compressed bytes as sent on the network (extra information)
latencies = []            # latency of each HTTP request in ms
page_load_times = []      # time of each full page load in ms
wait_time = 0             # all seconds spent waiting (delays + retry pauses)
rate_limited = 0          # number of 429 responses
network_errors = 0        # page loads that failed (DNS, timeout...)


def get_result(home_goals, away_goals):
    """Return W/D/L from the home team's point of view."""
    if home_goals > away_goals:
        return "W"
    elif home_goals == away_goals:
        return "D"
    else:
        return "L"


def start_browser():
    """Start headless Chrome with network logging turned on."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")    # large screen -> full team names
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    os.environ["SE_OFFLINE"] = "true"       # use the cached driver, no online check
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    # Disable the cache: every page is loaded like a first visit
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
    return driver


def wait_before_retry(attempt):
    """Exponential back-off: 60 s, 120 s, 240 s..."""
    global wait_time
    seconds = RETRY_WAIT * (2 ** (attempt - 1))
    print(f"    waiting {seconds} s before retrying...")
    time.sleep(seconds)
    wait_time += seconds


def read_network_log(driver):
    """Read Chrome's network events since the last call and update the metrics.
    Returns the HTTP status code of the main page (None if it never arrived)."""
    global request_count, total_bytes, wire_bytes

    start_times = {}          # requestId -> time the request was sent
    page_status = None

    for entry in driver.get_log("performance"):
        message = json.loads(entry["message"])["message"]
        method = message["method"]
        params = message.get("params", {})

        if method == "Network.requestWillBeSent":
            if params["request"]["url"].startswith("http"):     # ignore data: URLs
                start_times[params["requestId"]] = params["timestamp"]

        elif method == "Network.responseReceived":
            if params["type"] == "Document":                    # the main HTML page
                page_status = params["response"]["status"]

        elif method == "Network.dataReceived":
            total_bytes += params["dataLength"]

        elif method == "Network.loadingFinished":
            request_id = params["requestId"]
            if request_id in start_times:
                request_count += 1
                wire_bytes += params["encodedDataLength"]
                latency_ms = (params["timestamp"] - start_times[request_id]) * 1000
                latencies.append(latency_ms)

    return page_status


def fetch_round(driver, round_number):
    """Load the page of one round in the browser, retrying on 429 or network errors."""
    global rate_limited, network_errors

    url = BASE_URL.format(round_number)

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            driver.get(url)
        except WebDriverException as error:
            read_network_log(driver)              # still count what was sent
            network_errors += 1
            print(f"  Round {round_number}: browser error ({type(error).__name__}), "
                  f"attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                wait_before_retry(attempt)
            continue

        load_ms = (time.time() - start) * 1000
        status = read_network_log(driver)

        if status == 200:
            page_load_times.append(load_ms)
            return True, load_ms

        if status is None:                        # Chrome showed an error page (e.g. DNS)
            network_errors += 1
            print(f"  Round {round_number}: page did not load, attempt {attempt}/{MAX_RETRIES}")
        elif status == 429:
            rate_limited += 1
            print(f"  Round {round_number}: 429 Too Many Requests, attempt {attempt}/{MAX_RETRIES}")
        else:
            print(f"  Round {round_number}: error {status}")
            return False, load_ms

        if attempt < MAX_RETRIES:
            wait_before_retry(attempt)

    print(f"  Round {round_number}: gave up after {MAX_RETRIES} attempts")
    return False, None


def parse_round(driver):
    """Extract the matches from the page currently open in the browser."""
    matches = []

    for row in driver.find_elements(By.CSS_SELECTOR, "tr.season-event-row"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 5:
            continue

        # Cell 3: score "0 - 3" -> skip matches without a score
        score = cells[3].text.strip()
        if " - " not in score:
            continue
        home_goals, away_goals = score.split(" - ")
        if not (home_goals.isdigit() and away_goals.isdigit()):
            continue
        home_goals, away_goals = int(home_goals), int(away_goals)

        # Cell 0: "11 Aug 23" -> "2023-08-11"
        date = datetime.strptime(cells[0].text.strip(), "%d %b %y").strftime("%Y-%m-%d")

        # Cell 1: "r01" -> 1
        round_number = int(cells[1].text.strip().lstrip("r"))

        # Cells 2 and 4: the full team name (large-screen link)
        home_team = cells[2].find_element(By.CSS_SELECTOR, "a.d-lg-block").text.strip()
        away_team = cells[4].find_element(By.CSS_SELECTOR, "a.d-lg-block").text.strip()

        matches.append({
            "date": date,
            "round": round_number,
            "home_team": home_team,
            "away_team": away_team,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "result": get_result(home_goals, away_goals),
        })

    return matches


def save_metrics(time_s, rows):
    """Append one line for this method to data/metrics.csv."""
    file_path = "data/metrics.csv"
    file_exists = os.path.exists(file_path)

    avg_latency = sum(latencies) / len(latencies)
    bandwidth_kb = total_bytes / 1024

    with open(file_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["method", "time_s", "requests", "bandwidth_kb",
                             "avg_latency_ms", "rows"])
        writer.writerow([METHOD, round(time_s, 2), request_count,
                         round(bandwidth_kb, 2), round(avg_latency, 1), rows])


def main():
    global wait_time

    matches = []
    missing_rounds = []
    start_total = time.time()

    driver = start_browser()
    startup_s = time.time() - start_total
    print(f"Browser started in {startup_s:.1f} s")

    try:
        for r in range(1, ROUNDS + 1):
            ok, load_ms = fetch_round(driver, r)

            if ok:
                round_matches = parse_round(driver)
                matches.extend(round_matches)
                print(f"Round {r:2}: {len(round_matches)} matches  (page load {load_ms:.0f} ms)")
                if len(round_matches) == 0:
                    missing_rounds.append(r)
            else:
                missing_rounds.append(r)

            if r < ROUNDS:
                time.sleep(DELAY)
                wait_time += DELAY
    finally:
        driver.quit()                             # always close Chrome, even after an error

    # Total time without waiting (includes browser startup and parsing)
    time_s = time.time() - start_total - wait_time

    df = pd.DataFrame(matches)
    df = df.sort_values(["date", "round", "home_team"]).reset_index(drop=True)
    df.to_csv(f"data/matches_{METHOD}.csv", index=False)

    save_metrics(time_s, len(df))

    print("\n--- Summary ---")
    print(f"Matches saved      : {len(df)}")
    print(f"Time (s)           : {time_s:.2f}  (waiting excluded: {wait_time} s)")
    print(f"Browser startup (s): {startup_s:.1f}")
    print(f"Requests (all)     : {request_count}  (~{request_count / ROUNDS:.0f} per page)")
    print(f"429 responses      : {rate_limited}")
    print(f"Network errors     : {network_errors}")
    print(f"Bandwidth (KB)     : {total_bytes / 1024:.2f}  (on the wire, compressed: {wire_bytes / 1024:.2f})")
    print(f"Avg latency        : {sum(latencies) / len(latencies):.1f} ms per request")
    print(f"Avg page load      : {sum(page_load_times) / len(page_load_times):.0f} ms per page")
    if missing_rounds:
        print(f"WARNING - missing rounds: {missing_rounds}")


if __name__ == "__main__":
    main()
