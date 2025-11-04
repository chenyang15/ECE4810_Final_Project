#!/usr/bin/env python3
"""
bank_gate_main_mqtt.py

Door-only version:
- monitors one ultrasonic sensor (door)
- uses a 15-sample pre-trigger buffer + 65 post samples -> 80-sample window
- classifies as ENTER/EXIT using rf_artifacts.joblib
- maintains entered/exited/current counts
- lights green LED briefly on accepted detection
- red LED indicates emergency mode
- sends status packages via MQTT to a master Raspberry Pi
"""
import os
import time
import threading
from collections import deque
import joblib
import numpy as np
import requests
import RPi.GPIO as GPIO
import json
import datetime
import socket
import paho.mqtt.client as mqtt

# ---------------------
# CONFIG
# ---------------------
GPIO_MODE = "BOARD"  # "BOARD" or "BCM"

DOOR_TRIG = 7
DOOR_ECHO = 11

GREEN_LED_PIN = 37
RED_LED_PIN = 38

ML_ARTIFACT = "rf_artifacts.joblib"

BOT_TOKEN = "8335658770:AAG3FPnD9qB_89Jtdyo0I6FNUFgVj9PnHHA"           
STAFF_CHAT_ID = "8178934019"       

# MQTT configuration
MQTT_BROKER = "192.168.1.100"   # <-- change to master Pi's IP
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60
MQTT_TOPIC_PREFIX = "bank/gates"
DEVICE_ID = socket.gethostname()
MQTT_QOS = 1
MQTT_RETAIN = False

# Sampling & trigger tuning
SAMPLE_INTERVAL = 0.05
RESAMPLE_LEN = 80
PRE_SAMPLES = 15
POST_SAMPLES = RESAMPLE_LEN - PRE_SAMPLES
BUFFER_SECONDS = 8.0
MAX_TIMEOUT_FILL = 999.0
TRIGGER_SHORT_SEC = 0.25
TRIGGER_BASELINE_SEC = 2.0
TRIGGER_DELTA_CM = 20.0
TRIGGER_CONSECUTIVE = 2
COOLDOWN_SEC = 1.5

CONFIDENCE_THRESHOLD = 0.55
GREEN_LED_PULSE = 0.35

# Derived values
samples_per_sec = max(1, int(round(1.0 / SAMPLE_INTERVAL)))
buffer_size = max(RESAMPLE_LEN * 2, int(BUFFER_SECONDS * samples_per_sec))
short_n = max(1, int(TRIGGER_SHORT_SEC * samples_per_sec))
baseline_n = max(short_n + 1, int(TRIGGER_BASELINE_SEC * samples_per_sec))

# Globals
artifacts = None
entered_count = 0
exited_count = 0
count_lock = threading.Lock()
emergency_mode = False
emergency_lock = threading.Lock()

door_buffer = deque(maxlen=buffer_size)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

mqtt_client = None
mqtt_connected_ev = threading.Event()


# ---------------------
# GPIO setup
# ---------------------
def gpio_setup():
    mode = GPIO.BOARD if GPIO_MODE == "BOARD" else GPIO.BCM
    GPIO.setmode(mode)
    GPIO.setup(DOOR_TRIG, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(DOOR_ECHO, GPIO.IN)
    GPIO.setup(GREEN_LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RED_LED_PIN, GPIO.OUT, initial=GPIO.LOW)


def gpio_cleanup():
    GPIO.output(GREEN_LED_PIN, GPIO.LOW)
    GPIO.output(RED_LED_PIN, GPIO.LOW)
    GPIO.cleanup()


def set_led_state(label):
    if label == "collect":
        GPIO.output(GREEN_LED_PIN, GPIO.HIGH)
        GPIO.output(RED_LED_PIN, GPIO.LOW)
    elif label == "emergency":
        GPIO.output(GREEN_LED_PIN, GPIO.LOW)
        GPIO.output(RED_LED_PIN, GPIO.HIGH)
    else:
        GPIO.output(GREEN_LED_PIN, GPIO.LOW)
        GPIO.output(RED_LED_PIN, GPIO.LOW)


def pulse_green(duration=GREEN_LED_PULSE):
    def _pulse():
        try:
            set_led_state("collect")
            time.sleep(duration)
        finally:
            set_led_state("emergency" if emergency_mode else None)
    threading.Thread(target=_pulse, daemon=True).start()


# ---------------------
# Ultrasonic measurement
# ---------------------
def measure_distance_gpio(trig_pin, echo_pin):
    GPIO.output(trig_pin, True)
    time.sleep(0.00001)
    GPIO.output(trig_pin, False)

    start = time.time()
    timeout = start + 0.05
    while GPIO.input(echo_pin) == 0 and time.time() < timeout:
        start = time.time()

    timeout2 = time.time() + 0.25
    stop = time.time()
    while GPIO.input(echo_pin) == 1 and time.time() < timeout2:
        stop = time.time()

    elapsed = stop - start
    if elapsed <= 0:
        return None
    distance = (elapsed * 34300) / 2
    return round(distance, 2)


# ---------------------
# ML classifier
# ---------------------
def load_artifacts():
    global artifacts
    if artifacts is None:
        path = os.path.join(BASE_DIR, ML_ARTIFACT)
        if not os.path.exists(path):
            raise FileNotFoundError(f"ML artifact not found: {path}")
        artifacts = joblib.load(path)
        print("Loaded ML artifacts from", path)
    return artifacts


def ensure_vector(vec, length):
    arr = np.array([float(x) if str(x) != "" else np.nan for x in vec], dtype=float)
    if len(arr) < length:
        last = arr[-1] if len(arr) else MAX_TIMEOUT_FILL
        pad = np.full(length - len(arr), last, dtype=float)
        return np.concatenate([arr, pad])
    elif len(arr) > length:
        return arr[:length]
    return arr


def classify_window(distances):
    art = load_artifacts()
    clf = art["model"]
    scaler = art["scaler"]
    le = art["label_encoder"]
    reslen = art.get("resample_len", RESAMPLE_LEN)

    vec = ensure_vector(distances, reslen).reshape(1, -1)
    vec_s = scaler.transform(vec)
    probs = clf.predict_proba(vec_s)[0]
    idx = int(np.argmax(probs))
    label = le.inverse_transform([idx])[0]
    return label, float(probs[idx])


# ---------------------
# Network: Telegram & MQTT
# ---------------------
def send_telegram(text):
    if not BOT_TOKEN or not STAFF_CHAT_ID:
        print("Telegram not configured. Skipping:", text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": STAFF_CHAT_ID, "text": text}, timeout=5)
    except Exception as e:
        print("Telegram exception:", e)


def mqtt_on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT connected to broker.")
        mqtt_connected_ev.set()
    else:
        print("MQTT connect failed, rc=", rc)


def mqtt_on_disconnect(client, userdata, rc):
    print("MQTT disconnected (rc=%s)." % rc)
    mqtt_connected_ev.clear()


def mqtt_setup_and_start():
    global mqtt_client
    mqtt_client = mqtt.Client(client_id=f"{DEVICE_ID}-slave")
    mqtt_client.on_connect = mqtt_on_connect
    mqtt_client.on_disconnect = mqtt_on_disconnect
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
    except Exception as e:
        print("MQTT connect exception:", e)
    mqtt_client.loop_start()


def publish_status(entered_val=None, exited_val=None, current_cnt=None, emergency=None):
    if mqtt_client is None:
        print("MQTT not initialized. Skipping publish.")
        return

    payload = {
        "device_id": DEVICE_ID,
        "ts": datetime.datetime.now().isoformat(),
        "entered": int(entered_val) if entered_val is not None else 0,
        "exited": int(exited_val) if exited_val is not None else 0,
        "current_count": int(current_cnt) if current_cnt is not None else 0,
        "emergency": bool(emergency)
    }

    topic = f"{MQTT_TOPIC_PREFIX}/{DEVICE_ID}/status"
    try:
        mqtt_client.publish(topic, json.dumps(payload), qos=MQTT_QOS, retain=MQTT_RETAIN)
        print("Published MQTT:", payload)
    except Exception as e:
        print("MQTT publish failed:", e)


# ---------------------
# Door sensor worker
# ---------------------
def sensor_worker_door(name, trig_pin, echo_pin, buffer):
    global entered_count, exited_count
    trigger_count = 0
    last_action_time = 0
    print(f"{name} worker starting. Buffer size {buffer.maxlen}.")
    while True:
        d = measure_distance_gpio(trig_pin, echo_pin)
        if d is None:
            d = MAX_TIMEOUT_FILL
        buffer.append(float(d))

        if len(buffer) >= (baseline_n + short_n):
            recent = list(buffer)[-short_n:]
            baseline_window = list(buffer)[-(short_n + baseline_n):-short_n]
            delta = abs(np.mean(recent) - np.mean(baseline_window))

            if delta >= TRIGGER_DELTA_CM:
                trigger_count += 1
            else:
                trigger_count = 0

            now = time.time()
            if trigger_count >= TRIGGER_CONSECUTIVE and (now - last_action_time) > COOLDOWN_SEC:
                buf_list = list(buffer)
                pre_part = buf_list[-PRE_SAMPLES:] if len(buf_list) >= PRE_SAMPLES else [MAX_TIMEOUT_FILL] * (PRE_SAMPLES - len(buf_list)) + buf_list
                post_part = []
                for _ in range(POST_SAMPLES):
                    t0 = time.perf_counter()
                    dd = measure_distance_gpio(trig_pin, echo_pin) or MAX_TIMEOUT_FILL
                    post_part.append(float(dd))
                    time.sleep(max(0, SAMPLE_INTERVAL - (time.perf_counter() - t0)))

                window_samples = (pre_part + post_part)[:RESAMPLE_LEN]
                set_led_state("collect")

                try:
                    label, prob = classify_window(window_samples)
                except Exception as e:
                    print("Classification failed:", e)
                    label, prob = "UNKNOWN", 0.0

                label_up = str(label).upper()
                heuristic = "ENTER" if window_samples[-1] - window_samples[0] < 0 else "EXIT"

                accept = prob >= CONFIDENCE_THRESHOLD or (label_up == heuristic and prob >= 0.6)

                if accept and label_up in ("ENTER", "EXIT"):
                    with count_lock:
                        if label_up == "ENTER":
                            entered_count += 1
                        elif label_up == "EXIT":
                            exited_count += 1
                        current_count = max(0, entered_count - exited_count)

                    pulse_green(GREEN_LED_PULSE)

                    txt = f"{label_up} (p={prob:.2f}) | Entered={entered_count}, Exited={exited_count}, Current={current_count}"
                    print(txt)
                    send_telegram(txt)
                    publish_status(entered_val=entered_count, exited_val=exited_count,
                                   current_cnt=current_count, emergency=emergency_mode)
                else:
                    print(f"Low-confidence: {label} (p={prob:.2f}) heuristic={heuristic} ignored.")

                set_led_state("emergency" if emergency_mode else None)
                last_action_time = time.time()
                trigger_count = 0

        time.sleep(SAMPLE_INTERVAL)


# ---------------------
# Emergency toggle
# ---------------------
def set_emergency(on=True):
    global emergency_mode
    with emergency_lock:
        emergency_mode = bool(on)
    set_led_state("emergency" if emergency_mode else None)
    msg = "EMERGENCY MODE ACTIVATED" if emergency_mode else "Emergency mode cleared"
    print(msg)
    send_telegram(msg)
    publish_status(entered_val=entered_count, exited_val=exited_count,
                   current_cnt=(entered_count - exited_count), emergency=emergency_mode)


# ---------------------
# Main
# ---------------------
def main():
    print("Starting bank gate controller (door-only, MQTT slave demo)...")
    gpio_setup()
    set_led_state(None)

    # Reset counts at startup
    global entered_count, exited_count
    with count_lock:
        entered_count = 0
        exited_count = 0
    print("Counters reset: entered=0, exited=0")

    try:
        load_artifacts()
    except Exception as e:
        print("ML load warning:", e)

    mqtt_setup_and_start()
    publish_status(entered_val=entered_count, exited_val=exited_count,
                   current_cnt=(entered_count - exited_count), emergency=emergency_mode)

    t = threading.Thread(target=sensor_worker_door, args=("DOOR", DOOR_TRIG, DOOR_ECHO, door_buffer), daemon=True)
    t.start()

    try:
        while True:
            time.sleep(5)
            with count_lock:
                e, x = entered_count, exited_count
                c = e - x
            print(f"[STATUS] entered={e} exited={x} current={c} emergency={emergency_mode}")
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        gpio_cleanup()


if __name__ == "__main__":
    main()
