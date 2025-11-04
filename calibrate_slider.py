#!/usr/bin/env python3
"""
slider_calibration.py
Calibrates the slider notch positions using the ultrasonic sensor.

- Move the slider slowly from one end to the other during CAL_TIME_S.
- Script will cluster distance readings into N_NOTCHES groups.
- Saves the sorted mean centers to digit_centers.pkl.
"""

import time
import joblib
import numpy as np
from gpiozero import DistanceSensor

# ---------- Configuration ----------
TRIG = 4
ECHO = 17

N_NOTCHES = 7          # number of notches (e.g. 0–5 + reset)
CAL_TIME_S = 12        # move slider end-to-end within this time (seconds)
SAMPLE_HZ  = 40        # samples per second

sensor = DistanceSensor(trigger=TRIG, echo=ECHO, max_distance=0.4)  # up to 40 cm range

print(f"[Cal] Starting calibration for {N_NOTCHES} notches.")
print(f"→ Move the slider slowly from one end to the other over ~{CAL_TIME_S}s...")

data = []
t_end = time.time() + CAL_TIME_S
dt = 1.0 / SAMPLE_HZ

while time.time() < t_end:
    d = sensor.distance * 100.0  # convert m → cm
    if d > 0.0:
        data.append(d)
    time.sleep(dt)

if len(data) < 10:
    print("[Cal] ERROR: Not enough samples — check sensor or wiring.")
    exit(1)

data = np.array(data)
print(f"[Cal] Collected {len(data)} samples.")

# ---------- Process data ----------
# Smooth outliers
data = np.clip(data, np.percentile(data, 2), np.percentile(data, 98))

# Use k-means clustering to find notch centers
from sklearn.cluster import KMeans
print("[Cal] Clustering distances...")
kmeans = KMeans(n_clusters=N_NOTCHES, n_init=20, random_state=42).fit(data.reshape(-1, 1))
centers = sorted(kmeans.cluster_centers_.flatten())

# ---------- Save results ----------
joblib.dump(centers, "digit_centers.pkl")
print(f"[Cal] Done. Learned centers (cm): {np.round(centers, 2).tolist()}")
print("[Cal] Saved to digit_centers.pkl.")


