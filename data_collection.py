# data_collection.py
import RPi.GPIO as GPIO
import time
import csv
from datetime import datetime

# --- Ultrasonic Sensor Pins ---
TRIG = 23
ECHO = 24

# --- LED Pins (Optional) ---
LED_PIN = 18

GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
GPIO.setup(LED_PIN, GPIO.OUT)

def measure_distance():
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    start = time.time()
    stop = time.time()

    while GPIO.input(ECHO) == 0:
        start = time.time()

    while GPIO.input(ECHO) == 1:
        stop = time.time()

    elapsed = stop - start
    distance = (elapsed * 34300) / 2  # cm
    return round(distance, 2)

# --- Main Data Collection Loop ---
filename = "ultrasonic_data.csv"
print("Starting data collection... Press Ctrl+C to stop.")
print("Move towards and away from the sensor several times.")

with open(filename, mode='w', newline='') as file:
    writer = csv.writer(file)
    writer.writerow(["timestamp", "distance_cm"])

    try:
        while True:
            dist = measure_distance()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            writer.writerow([timestamp, dist])
            GPIO.output(LED_PIN, True)
            print(f"Time: {timestamp} | Distance: {dist} cm")
            time.sleep(0.05)
            GPIO.output(LED_PIN, False)
    except KeyboardInterrupt:
        print("\nData collection stopped.")
        GPIO.cleanup()
