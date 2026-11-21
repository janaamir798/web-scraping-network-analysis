# test_tor.py - compares a direct connection with a connection through Tor
# Usage: python test_tor.py        -> both tests
#        python test_tor.py tor    -> Tor test only
import sys
import time
import requests

TOR = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
API_URL = "https://www.thesportsdb.com/api/v1/json/123/eventsround.php?id=4328&r=1&s=2023-2024"
IP_SERVICES = ["https://httpbin.org/ip", "https://api.ipify.org?format=json"]


def show_ip(proxies):
    """Ask IP services which IP they see. Show the answer even if it is an error."""
    for service in IP_SERVICES:
        try:
            r = requests.get(service, proxies=proxies, timeout=30)
            data = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
            ip = data.get("origin") or data.get("ip")
            if r.status_code == 200 and ip:
                print(f"IP seen by {service.split('/')[2]}: {ip}")
                return
            print(f"{service.split('/')[2]} answered status {r.status_code}: {r.text[:150]!r}")
        except requests.exceptions.RequestException as error:
            print(f"{service.split('/')[2]} failed: {type(error).__name__}")


def test(name, proxies):
    print(f"\n=== {name} ===")
    show_ip(proxies)

    for i in range(3):
        start = time.time()
        try:
            r = requests.get(API_URL, proxies=proxies, timeout=30)
            latency_ms = (time.time() - start) * 1000
            print(f"Request {i + 1}: status {r.status_code}, {latency_ms:.0f} ms, "
                  f"{len(r.content) / 1024:.1f} KB")
            if r.status_code != 200:
                print("   Response starts with:", r.text[:150].replace("\n", " "))
        except requests.exceptions.RequestException as error:
            print(f"Request {i + 1}: failed ({type(error).__name__})")
        time.sleep(5)


if len(sys.argv) < 2 or sys.argv[1] != "tor":
    test("Direct connection", None)
test("Through Tor", TOR)
