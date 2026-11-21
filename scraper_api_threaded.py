# scraper_api_threaded.py
# Same data as scraper_api.py, but several rounds are requested at the same time
# using a pool of threads. Usage: python scraper_api_threaded.py <workers>

import os
import sys
import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import pandas as pd

# ---------------- Settings ----------------
BASE_URL = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php"
LEAGUE_ID = 4328
SEASON = "2023-2024"
ROUNDS = 38
MAX_RETRIES = 4
RETRY_WAIT = 60           # first wait after a failure, then doubled (60, 120, 240)
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 4    # number of threads
METHOD = f"api_threaded_{WORKERS}"

# ---------------- Metrics counters (shared by all threads) ----------------
lock = threading.Lock()   # only one thread at a time may update the counters
request_count = 0
total_bytes = 0
latencies = []
rate_limited = 0
network_errors = 0


def get_result(home_goals, away_goals):
    """Return W/D/L from the home team's point of view."""
    if home_goals > away_goals:
        return "W"
    elif home_goals == away_goals:
        return "D"
    else:
        return "L"


def fetch_round(round_number):
    """Run inside a thread: request one round, retrying on 429 or network errors."""
    global request_count, total_bytes, rate_limited, network_errors

    params = {"id": LEAGUE_ID, "r": round_number, "s": SEASON}
    thread_name = threading.current_thread().name

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.time()
        try:
            response = requests.get(BASE_URL, params=params, timeout=15)
        except requests.exceptions.RequestException as error:
            with lock:
                network_errors += 1
            print(f"  Round {round_number}: network error ({type(error).__name__}) [{thread_name}]")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT * (2 ** (attempt - 1)))
            continue

        latency_ms = (time.time() - start) * 1000
        with lock:
            request_count += 1
            total_bytes += len(response.content)
            latencies.append(latency_ms)

        if response.status_code == 200:
            print(f"Round {round_number:2}: OK  ({latency_ms:.0f} ms)  [{thread_name}]")
            return round_number, response.json()

        if response.status_code == 429:
            with lock:
                rate_limited += 1
            seconds = RETRY_WAIT * (2 ** (attempt - 1))
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                seconds = int(retry_after)
            print(f"  Round {round_number}: 429 Too Many Requests, attempt {attempt}/{MAX_RETRIES} "
                  f"[{thread_name}]")
            if attempt < MAX_RETRIES:
                print(f"    [{thread_name}] waiting {seconds} s...")
                time.sleep(seconds)
        else:
            print(f"  Round {round_number}: error {response.status_code} [{thread_name}]")
            return round_number, None

    print(f"  Round {round_number}: gave up after {MAX_RETRIES} attempts [{thread_name}]")
    return round_number, None


def parse_events(data):
    """Turn the JSON of one round into a list of match dicts (same columns as before)."""
    matches = []
    for event in data.get("events") or []:
        if event["intHomeScore"] is None:
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
    print(f"Starting with {WORKERS} threads...\n")
    start_total = time.time()

    # Give the 38 rounds to the pool: each free thread takes the next round
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="worker") as executor:
        results = list(executor.map(fetch_round, range(1, ROUNDS + 1)))

    # Real elapsed time, from the first request to the last answer (including any waiting)
    time_s = time.time() - start_total

    matches = []
    missing_rounds = []
    for round_number, data in results:
        if data and data.get("events"):
            matches.extend(parse_events(data))
        else:
            missing_rounds.append(round_number)

    df = pd.DataFrame(matches)
    df = df.sort_values(["date", "round", "home_team"]).reset_index(drop=True)
    df.to_csv(f"data/matches_{METHOD}.csv", index=False)

    save_metrics(time_s, len(df))

    print("\n--- Summary ---")
    print(f"Threads        : {WORKERS}")
    print(f"Matches saved  : {len(df)}")
    print(f"Time (s)       : {time_s:.2f}  (real elapsed time, including any waiting)")
    print(f"Requests       : {request_count}")
    print(f"429 responses  : {rate_limited}")
    print(f"Network errors : {network_errors}")
    print(f"Bandwidth (KB) : {total_bytes / 1024:.2f}")
    print(f"Avg latency    : {sum(latencies) / len(latencies):.1f} ms")
    if missing_rounds:
        print(f"WARNING - missing rounds: {missing_rounds}")


if __name__ == "__main__":
    main()
