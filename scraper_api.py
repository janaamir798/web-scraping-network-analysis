# scraper_api.py
# Collects all Premier League 2023-2024 matches from the TheSportsDB API
# and records performance metrics (time, requests, bandwidth, latency).

import os
import sys
import csv
import time
import requests
import pandas as pd

# ---------------- Settings ----------------
BASE_URL = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php"
LEAGUE_ID = 4328          # English Premier League
SEASON = "2023-2024"
ROUNDS = 38
DELAY = 5                 # seconds between requests (12 requests/minute)
MAX_RETRIES = 4           # attempts per round
RETRY_WAIT = 60           # first wait after a failure, then doubled (60, 120, 240)
METHOD = "api"

# Optional: run through Tor with "python scraper_api.py tor"
USE_TOR = len(sys.argv) > 1 and sys.argv[1] == "tor"
PROXIES = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"} if USE_TOR else None
if USE_TOR:
    METHOD = "api_tor"

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
            seconds = int(retry_after)            # the server knows best

    print(f"    waiting {seconds} s before retrying...")
    time.sleep(seconds)
    wait_time += seconds


def fetch_round(round_number):
    """Request one round, retrying on 429 or network errors. Returns the JSON."""
    global request_count, total_bytes, rate_limited, network_errors

    params = {"id": LEAGUE_ID, "r": round_number, "s": SEASON}

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            response = requests.get(BASE_URL, params=params, proxies=PROXIES, timeout=15)
        except requests.exceptions.RequestException as error:
            # No answer at all: DNS failure, timeout, connection reset...
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
            return response.json(), latency_ms

        if response.status_code == 429:           # the server refuses: too many requests
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


def save_metrics(time_s, rows):
    """Append one line for this method to data/metrics.csv."""
    file_path = "data/metrics.csv"
    file_exists = os.path.exists(file_path)

    avg_latency = sum(latencies) / len(latencies)
    bandwidth_kb = total_bytes / 1024

    with open(file_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:                       # write the header only once
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
        data, latency_ms = fetch_round(r)

        if data and data.get("events"):
            for event in data["events"]:
                if event["intHomeScore"] is None:     # skip matches with no score
                    continue
                home_goals = int(event["intHomeScore"])
                away_goals = int(event["intAwayScore"])
                matches.append({
                    "date": event["dateEvent"],
                    "round": int(event["intRound"]),
                    "home_team": event["strHomeTeam"],
                    "away_team": event["strAwayTeam"],
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "result": get_result(home_goals, away_goals),
                })
            print(f"Round {r:2}: {len(data['events'])} matches  ({latency_ms:.0f} ms)")
        else:
            missing_rounds.append(r)

        if r < ROUNDS:                            # polite delay (rate limiting)
            time.sleep(DELAY)
            wait_time += DELAY

    # Scraping time without any waiting
    time_s = time.time() - start_total - wait_time

    # Save the matches, sorted by date (important for the ML part later)
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
