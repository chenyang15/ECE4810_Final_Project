#!/usr/bin/env python3
import eel, time, sys, random, threading, requests
import firebase_admin
from firebase_admin import credentials, db
import RPi.GPIO as GPIO
from statistics import mode
from collections import deque

eel.init("web")

cred = credentials.Certificate("ece4810-project-firebase-adminsdk-fbsvc-a5f24fb3b9.json")
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://ece4810-project-default-rtdb.asia-southeast1.firebasedatabase.app/"
})

BOT_TOKEN = "8465622476:AAHzFCDoL6KAPY20b4Wm3kkauyfU9bYVnDA"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- Ultrasonic pin configuration ---
GPIO.setmode(GPIO.BOARD)
PIN_TRIGGER = 7     # physical pin 7
PIN_ECHO    = 11    # physical pin 11
GPIO.setup(PIN_TRIGGER, GPIO.OUT)
GPIO.setup(PIN_ECHO, GPIO.IN)
GPIO.output(PIN_TRIGGER, GPIO.LOW)

otp_store = {}
maintenance_mode = False
run_monitor = True
monitor_thread = None

# -------------------- LOGIN --------------------
@eel.expose
def login(username, password):
    print(f"User tried: {username}, {password}")
    eel.show_status("Authenticating...")
    time.sleep(1)

    ref = db.reference(f"/forJoanne/{username}")
    user = ref.get()
    if not user or user.get("password") != password:
        eel.show_status("❌ Invalid credentials")
        return

    status = str(user.get("Status", "Customer"))
    chatid = str(user.get("chatid", "")).strip()

    otp = f"{random.randint(0,999999):06d}"
    otp_store[username] = (otp, time.time() + 60, status)
    print(f"Generated OTP for {username}: {otp}")

    try:
        if chatid:
            payload = {"chat_id": chatid, "text": f"Your GuoBank OTP: {otp}"}
            r = requests.post(f"{TELEGRAM_URL}/sendMessage", data=payload, timeout=8)
            print("Telegram:", r.status_code, r.text)
    except Exception as e:
        print("Telegram send error:", e)

    # tell JS to open OTP page
    eel.redirect_otp(username, status, 60)()

# -------------------- VERIFY OTP --------------------
def safe_call(func_name, *args):
    """Call a JS function safely without crashing if it isn't defined."""
    try:
        getattr(eel, func_name)(*args)()
    except Exception as e:
        print(f"[WARN] JS function '{func_name}' not available: {e}")

@eel.expose
def verify_otp(username, entered):
    otp_tuple = otp_store.get(username)
    if not otp_tuple:
        try:
            eel.otp_failed("❌ Session expired, please login again.")()
        except Exception:# -------------------- CHECK BALANCE --------------------

            print("JS function not available (page changed).")
        return

    otp, exp, status = otp_tuple
    if time.time() > exp:
        try:
            eel.otp_failed("⏰ OTP expired")()
        except Exception:
            print("JS function not available (page changed).")
        return

    if entered == otp:
        print(f"OTP verified for {username}")
        otp_store.pop(username, None)
        try:
            eel.redirect_main(username, status)()
        except Exception:
            print("JS redirect not available, probably reloading.")
    else:
        try:
            eel.otp_failed("❌ Wrong OTP")()
        except Exception:
            print("JS function not available (page changed).")

# -------------------- MAINTENANCE --------------------
@eel.expose
def toggle_maintenance(state):
    global maintenance_mode
    maintenance_mode = state
    eel.show_status("Maintenance ON" if state else "Maintenance OFF")
    
# -------------------- CHECK BALANCE --------------------
@eel.expose
def get_balance(username):
    try:
        ref = db.reference(f"/forJoanne/{username}/Account Balance (RM)")
        bal = ref.get()
        if bal is None:
            bal = 0
        print(f"Fetched balance for {username}: RM {bal}")
        return bal       # ✅ return value instead of eel callback
    except Exception as e:
        print("Balance fetch error:", e)
        return "Error"

# -------------------- ULTRASONIC MONITOR --------------------
# -------------------- ULTRASONIC MONITOR --------------------
from statistics import mode
from collections import deque

window = deque(maxlen=5)

def measure_distance():
    """Stable distance measurement using BOARD pins (7,11)."""
    # trigger a 10 µs pulse
    GPIO.output(PIN_TRIGGER, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(PIN_TRIGGER, GPIO.LOW)

    # wait for echo start
    pulse_start = time.time()
    timeout = pulse_start + 0.05  # 50 ms max wait
    while GPIO.input(PIN_ECHO) == 0:
        pulse_start = time.time()
        if time.time() > timeout:
            # no pulse detected
            return None

    # wait for echo end
    pulse_end = time.time()
    while GPIO.input(PIN_ECHO) == 1:
        pulse_end = time.time()
        if time.time() - pulse_start > 0.05:
            # echo stuck high or noise
            return None

    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150  # convert to cm
    return round(distance, 2)


def monitor_ultrasonic():
    """Continuously print distance and trigger lockdown check."""
    global run_monitor
    baseline = None
    print("Ultrasonic monitoring started...")

    while run_monitor:
        try:
            # gather multiple samples to reduce noise
            samples = []
            for _ in range(3):
                d = measure_distance()
                if d is not None:
                    samples.append(d)
                time.sleep(0.05)

            if not samples:
                print("[ULTRASONIC] No echo detected.")
                time.sleep(0.5)
                continue

            # use the mode (most common) for stability
            try:
                reading = mode(samples)
            except:
                reading = sum(samples) / len(samples)
            window.append(reading)
            try:
                d = mode(window)
            except:
                d = sum(window) / len(window)

            # establish baseline for closed door
            if baseline is None and d < 50:
                baseline = d

            print(f"[ULTRASONIC] Distance: {d:.2f} cm", end="")
            if baseline:
                print(f" | Baseline: {baseline:.2f} cm", end="")

            # --- Decision logic ---
            if baseline and not maintenance_mode:
                if d - baseline > 15:
                    print("  --> ALERT! Should trigger lockdown 🔒")
                    try:
                        eel.trigger_emergency_mode()()
                    except Exception as e:
                        print(" (UI not ready)", e)
                    publish_lockdown(True)
    
                else:
                    print("  (Normal)")
            else:
                print("  (Maintenance ON / baseline not set)")
        except Exception as e:
            print("Sensor read error:", e)

        time.sleep(0.5)



def on_close(route, websockets):
    print("UI closed. Cleaning up...")
    GPIO.cleanup()
    sys.exit()
    
# -------------------- MQTT LOCKDOWN SYNC --------------------
import paho.mqtt.client as mqtt
import json

# Configuration
MQTT_BROKER = "192.168.0.219"    # <-- change to your master Pi IP
MQTT_PORT = 1883
TOPIC_ATM = "GuoBank/ATM1/status"
TOPIC_MASTER = "GuoBank/Master/status"

lockdown_active = False
mqtt_client = None

def publish_lockdown(state: bool):
    """ATM publishes its lockdown state to the master."""
    global mqtt_client
    try:
        msg = json.dumps({"Lockdown": state})
        mqtt_client.publish(TOPIC_ATM, msg, qos=1, retain=True)
        print(f"[MQTT] Sent Lockdown={state}")
    except Exception as e:
        print("[MQTT] Publish failed:", e)

def on_message(client, userdata, msg):
    """Handle messages from the master Pi."""
    global lockdown_active
    try:
        payload = json.loads(msg.payload.decode())
        if "Lockdown" in payload:
            state = bool(payload["Lockdown"])
            print(f"[MQTT] Received Lockdown={state} from Master")
            if state and not lockdown_active:
                lockdown_active = True
                eel.trigger_emergency_mode()()
            elif not state and lockdown_active:
                lockdown_active = False
                sessionStorage_remove_lock()
    except Exception as e:
        print("[MQTT] Message error:", e)

def on_connect(client, userdata, flags, rc):
    print("[MQTT] Connected with code", rc)
    client.subscribe(TOPIC_MASTER)

def sessionStorage_remove_lock():
    """JS helper to clear lockdown overlay (called only on master unlock)."""
    try:
        eel.clearLock()()  # calls JS clearLock() in global.js
    except Exception as e:
        print("[EEL] Unlock call failed:", e)

def mqtt_loop():
    """Background MQTT listener."""
    global mqtt_client
    mqtt_client = mqtt.Client(client_id="ATM1")
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_forever()
   

## -------------------- START --------------------
import threading, time

def start_eel():
    try:
        eel.start(
            "index.html",
            size=(1000, 600),
            port=8000,
            mode=None,
            block=True,           # block=True so thread stays alive
            close_callback=None
        )
    except Exception as e:
        print("Eel stopped:", e)

if __name__ == "__main__":
    print("System ready.")

    # Start the Eel UI thread
    eel_thread = threading.Thread(target=start_eel, daemon=True)
    eel_thread.start()

    # Start ultrasonic monitor thread
    monitor_thread = threading.Thread(target=monitor_ultrasonic, daemon=True)
    monitor_thread.start()

    # start MQTT listener thread
    mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
    mqtt_thread.start()

    # Keep the program alive indefinitely
    try:
        while True:
            time.sleep(1)  # main heartbeat
    except KeyboardInterrupt:
        print("\nExiting gracefully...")
    finally:
        run_monitor = False
        GPIO.cleanup()
        sys.exit(0)



