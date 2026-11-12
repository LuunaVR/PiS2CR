"""Press B through the dashboard API at random phases and log wall-clock times, for latency_report.py."""
import json
import random
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
count = int(sys.argv[2]) if len(sys.argv) > 2 else 60


def post(down):
    body = json.dumps({"button": "B", "down": down}).encode()
    request = urllib.request.Request(BASE + "/press", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=2).read()


with open(sys.argv[1], "w") as log:
    for _ in range(count):
        time.sleep(random.uniform(0.25, 0.45))
        log.write(f"down {time.time():.6f}\n")
        post(True)
        time.sleep(0.12)
        log.write(f"up {time.time():.6f}\n")
        post(False)
