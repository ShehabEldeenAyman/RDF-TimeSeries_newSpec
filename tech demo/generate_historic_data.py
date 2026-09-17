"""Generates synthetic hourly temperature readings into data/system1.csv."""

import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 23, 0, 0, tzinfo=timezone.utc)
INTERVAL = timedelta(hours=1)
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_data.csv"

ANNUAL_MEAN_C = 11.0
ANNUAL_AMPLITUDE_C = 10.0
DAILY_AMPLITUDE_C = 3.5
NOISE_STDDEV_C = 0.5


def temperature_at(ts: datetime) -> float:
    day_of_year = ts.timetuple().tm_yday
    seasonal = ANNUAL_AMPLITUDE_C * math.sin(2 * math.pi * (day_of_year - 100) / 365)
    daily = DAILY_AMPLITUDE_C * math.sin(2 * math.pi * (ts.hour - 9) / 24)
    noise = random.gauss(0, NOISE_STDDEV_C)
    return round(ANNUAL_MEAN_C + seasonal + daily + noise, 2)


def main():
    print(f"Started generating data at {datetime.now()}")
    random.seed(42)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "temperature_C"])
        ts = START
        while ts <= END:
            writer.writerow([ts.strftime("%Y-%m-%dT%H:%M:%SZ"), temperature_at(ts)])
            ts += INTERVAL
    print(f"Finished generating data at {datetime.now()}, wrote to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
