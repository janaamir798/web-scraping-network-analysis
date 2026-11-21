# test_https.py - sends one HTTPS request and shows how long it took to succeed or fail
import time
import requests

url = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php?id=4328&r=1&s=2023-2024"
start = time.time()
try:
    r = requests.get(url, timeout=15)
    print("Status:", r.status_code, "after", round(time.time() - start, 1), "s")
except requests.exceptions.RequestException as error:
    print("Failed after", round(time.time() - start, 1), "s:", type(error).__name__)
    print(error)
