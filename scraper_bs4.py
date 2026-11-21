# scraper_bs4.py
# Collects all Premier League 2023-2024 matches from the TheSportsDB website (HTML)
# using requests + BeautifulSoup, and records performance metrics.

import os
import csv
import time
from datetime import datetime

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ---------------- Settings ----------------
BASE_URL = "https://www.thesportsdb.com/season/4328-english-premier-league/2023-2024&r={}"
HEADERS = {"User-Agent": "Mozilla/5.0"}   # look like a normal browser
ROUNDS = 38
DELAY = 5                 # seconds between requests (12 requests/minute)
MAX_RETRIES = 4           # attempts per round
RETRY_WAIT = 60           # first wait after a failure, then doubled (60, 120, 240)
METHOD = "bs4"

# ---------------- Metrics counters ----------------
request_count = 0         # requests that reached the server
total_bytes = 0
latencies = []            # latency of each request in ms
wait_time = 0             # all seconds spent waiting (delays + retry pauses)
rate_limited = 0          # number of 429 responses
network_errors = 0        # requests that never reached the server (DNS, timeout...)


def get_result(home_goals, away_goals):
    """Return W/D/L from the home team's point of view."""
    if home_goals > away_goals:
        return "W"
    elif home_goals == away_goals:
        return "D"
    else:
        return "L"


def wait_before_retry(attempt, response=None):
    """Exponential back-off: 60 s, 120 s, 240 s... or the server's Retry-After."""
    global wait_time

    seconds = RETRY_WAIT * (2 ** (attempt - 1))
    if response is not None:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            seconds = int(retry_after)

    print(f"    waiting {seconds} s before retrying...")
    time.sleep(seconds)
    wait_time += seconds


def fetch_round(round_number):
    """Download the HTML page of one round, retrying on 429 or network errors."""
    global request_count, total_bytes, rate_limited, network_errors

    url = BASE_URL.format(round_number)

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
        except requests.exceptions.RequestException as error:
            network_errors += 1
            print(f"  Round {round_number}: network error ({type(error).__name__}), "
                  f"attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                wait_before_retry(attempt)
            continue

        latency_ms = (time.time() - start) * 1000
        request_count += 1
        total_bytes += len(response.content)
        latencies.append(latency_ms)

        if response.status_code == 200:
            return response.text, latency_ms

        if response.status_code == 429:
            rate_limited += 1
            print(f"  Round {round_number}: 429 Too Many Requests "
                  f"(Retry-After: {response.headers.get('Retry-After', 'none')}), "
                  f"attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                wait_before_retry(attempt, response)
        else:
            print(f"  Round {round_number}: error {response.status_code}")
            return None, latency_ms

    print(f"  Round {round_number}: gave up after {MAX_RETRIES} attempts")
    return None, None


def parse_round(html):
    """Extract the matches from one round's HTML page. Returns a list of dicts."""
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for row in soup.find_all("tr", class_="season-event-row"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        # Cell 3: score "0 - 3" -> skip matches without a score (not played)
        score = cells[3].get_text(strip=True)
        if " - " not in score:
            continue
        home_goals, away_goals = score.split(" - ")
        if not (home_goals.isdigit() and away_goals.isdigit()):
            continue
        home_goals, away_goals = int(home_goals), int(away_goals)

        # Cell 0: "11 Aug" + "23" -> "2023-08-11"
        date_text = cells[0].get_text(" ", strip=True)
        date = datetime.strptime(date_text, "%d %b %y").strftime("%Y-%m-%d")

        # Cell 1: "r01" -> 1
        round_number = int(cells[1].get_text(strip=True).lstrip("r"))

        # Cells 2 and 4: take only the full team name (large-screen link)
        home_team = cells[2].find("a", class_="d-lg-block").get_text(strip=True)
        away_team = cells[4].find("a", class_="d-lg-block").get_text(strip=True)

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

    for r in range(1, ROUNDS + 1):
        html, latency_ms = fetch_round(r)

        if html:
            round_matches = parse_round(html)
            matches.extend(round_matches)
            print(f"Round {r:2}: {len(round_matches)} matches  ({latency_ms:.0f} ms)")
            if len(round_matches) == 0:
                missing_rounds.append(r)
        else:
            missing_rounds.append(r)

        if r < ROUNDS:
            time.sleep(DELAY)
            wait_time += DELAY

    # Scraping time without any waiting (includes HTML parsing time)
    time_s = time.time() - start_total - wait_time

    df = pd.DataFrame(matches)
    df = df.sort_values(["date", "round", "home_team"]).reset_index(drop=True)
    df.to_csv(f"data/matches_{METHOD}.csv", index=False)

    save_metrics(time_s, len(df))

    print("\n--- Summary ---")
    print(f"Matches saved  : {len(df)}")
    print(f"Time (s)       : {time_s:.2f}  (waiting excluded: {wait_time} s)")
    print(f"Requests       : {request_count}")
    print(f"429 responses  : {rate_limited}")
    print(f"Network errors : {network_errors}")
    print(f"Bandwidth (KB) : {total_bytes / 1024:.2f}")
    print(f"Avg latency    : {sum(latencies) / len(latencies):.1f} ms")
    if missing_rounds:
        print(f"WARNING - missing rounds: {missing_rounds}")


if __name__ == "__main__":
    main()
