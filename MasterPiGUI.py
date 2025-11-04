#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding="utf-8")

import eel, threading, time, requests
import paho.mqtt.client as mqtt
import firebase_admin
from firebase_admin import credentials, db
from Encryption import custom_encrypt, custom_decrypt

# === Bring in your sensor/ML program ===
import MasterPiCounterSlider as mpcs  # <-- your full file with the 2 small hooks added

# ==========================================================
# ---------------- CONFIG ----------------------------------
# ==========================================================
BROKER_IP = "192.168.128.211"        # Master (this Pi) IP
PORT = 1883

THINGSPEAK_WRITE_API = "MK66YI8UIUG8XW8T"
THINGSPEAK_URL = f"https://api.thingspeak.com/update?api_key={THINGSPEAK_WRITE_API}"

TELEGRAM_TOKEN = "8335658770:AAG3FPnD9qB_89Jtdyo0I6FNUFgVj9PnHHA"
STAFF_CHAT_ID = "8178934019"      # staff channel id (fixed by spec)

# Firebase setup
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://ece4810-project-default-rtdb.asia-southeast1.firebasedatabase.app/"
})
ROOT_PATH = "forJoanne"
STATUS_PATH = "STAPH"

KEY_NUMBER = 73

# Eel frontend
eel.init("web")

# ==========================================================
# ---------------- GLOBAL STATE (MQTT) ----------------------
# ==========================================================
Lockdown = 0
BackdoorLock = 0
CurrentNoOfPpl = 0
TotalNoOfPpl = 0
state_lock = threading.Lock()

TOPIC_SLAVES = "GuoBank/+/status"
TOPIC_BROADCAST = "GuoBank/system/lockdown"
mqtt_client = None

# ==========================================================
# ---------------- HELPERS ---------------------------------
# ==========================================================
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": STAFF_CHAT_ID, "text": msg}, timeout=5)
        print("Telegram →", msg, r.status_code)
    except Exception as e:
        print("Telegram error:", e)

def send_to_thingspeak_loop():
    while True:
        time.sleep(15)
        with state_lock:
            params = {
                "field1": Lockdown,
                "field2": BackdoorLock,
                "field3": CurrentNoOfPpl,
                "field4": TotalNoOfPpl,
            }
        try:
            r = requests.get(THINGSPEAK_URL, params=params, timeout=5)
            print("ThingSpeak update:", params, "→", r.status_code)
        except Exception as e:
            print("ThingSpeak error:", e)

def broadcast_state():
    """Broadcast the full state to all slaves (they at least care about Lockdown)."""
    global mqtt_client
    msg = f"Lockdown:{Lockdown};BackdoorLock:{BackdoorLock};CurrentNoOfPpl:{CurrentNoOfPpl};TotalNoOfPpl:{TotalNoOfPpl}"
    try:
        mqtt_client.publish(TOPIC_BROADCAST, msg, qos=1, retain=False)
        print(f"[MQTT] Broadcast → {TOPIC_BROADCAST} : {msg}")
    except Exception as e:
        print("[MQTT] Publish error:", e)

# ==========================================================
# ---------------- MQTT ------------------------------------
# ==========================================================
def on_message(client, userdata, msg):
    global Lockdown, BackdoorLock, CurrentNoOfPpl, TotalNoOfPpl
    try:
        text = msg.payload.decode().strip()
        print(f"MQTT recv ({msg.topic}): {text}")
        updates = {}
        for pair in text.split(";"):
            if ":" in pair:
                k, v = pair.split(":", 1)
                updates[k.strip()] = v.strip()

        with state_lock:
            if "Lockdown" in updates:
                new_state = int(updates["Lockdown"])
                if new_state != Lockdown:
                    Lockdown = new_state
                    broadcast_state()
                    if Lockdown == 1:
                        send_telegram("🚨 Lockdown Activated (from slave)!")
                    else:
                        send_telegram("✅ Lockdown Cleared (from slave).")

            if "BackdoorLock" in updates:
                BackdoorLock = int(updates["BackdoorLock"])
            if "CurrentNoOfPpl" in updates:
                CurrentNoOfPpl = int(updates["CurrentNoOfPpl"])
            if "TotalNoOfPpl" in updates:
                TotalNoOfPpl = int(updates["TotalNoOfPpl"])

        print(f"[STATE] L={Lockdown}, B={BackdoorLock}, C={CurrentNoOfPpl}, T={TotalNoOfPpl}")

    except Exception as e:
        print("[MQTT] on_message error:", e)

def mqtt_loop():
    global mqtt_client
    try:
        mqtt_client = mqtt.Client(client_id="MasterPi")
        mqtt_client.on_message = on_message
        mqtt_client.connect(BROKER_IP, PORT, 60)
        mqtt_client.subscribe(TOPIC_SLAVES)
        print("Subscribed to:", TOPIC_SLAVES)

        threading.Thread(target=send_to_thingspeak_loop, daemon=True).start()
        mqtt_client.loop_forever()
    except Exception as e:
        print("[MQTT] Connection error:", e)

# ==========================================================
# ---------------- FIREBASE / BANKING ----------------------
# ==========================================================
def add_user(username, password, role, initial_balance):
    chatid = "1100732379" if role.lower() == "customer" else "8178934019"
    ref = db.reference(f"{ROOT_PATH}/{username}")
    if ref.get() is not None:
        return "User already exists"
    try:
        encrypted_pass = custom_encrypt(password, KEY_NUMBER)
        ref.set({
            "password": encrypted_pass,
            "Account Balance (RM)": float(initial_balance),
            "Status": role.capitalize(),
            "chatid": chatid
        })
        print(f"[Firebase] Created {role} '{username}' with balance {initial_balance}")
        return "✅ User created successfully"
    except Exception as e:
        print("[Firebase] Error creating user:", e)
        return "❌ Error creating user"

def validate_user(username: str, password: str):
    """
    Returns user dict on success, None on failure.
    Defensive against missing fields and decrypt errors.
    """
    try:
        ref = db.reference(f"{ROOT_PATH}/{username}")
        user = ref.get()
        if not user:
            print(f"[Firebase] Validation: user '{username}' not found")
            return None

        enc = user.get("password")
        if not enc:
            print(f"[Firebase] Validation: user '{username}' has no password field")
            return None

        try:
            dec = custom_decrypt(enc, KEY_NUMBER)
        except Exception as de:
            print(f"[Firebase] Validation: decrypt failed for '{username}': {de}")
            return None

        if dec == password:
            print(f"[Firebase] Validation: user '{username}' OK, role={user.get('Status')}")
            return user

        print(f"[Firebase] Validation: wrong password for '{username}'")
        return None

    except Exception as e:
        # IMPORTANT: never reference an undefined variable like `text` here
        print(f"[Firebase] Validation error: {e}")
        return None



# ===== Eel endpoints =====
@eel.expose
def create_user(username, password, role, initial_balance):
    msg = add_user(username, password, role, initial_balance)
    eel.show_status(msg)()

@eel.expose
def login(username, password):
    """
    Eel entrypoint. Redirects on success, shows status on failure.
    """
    user = validate_user(username, password)
    if not user:
        try:
            eel.show_status("❌ Invalid credentials")()
        except Exception as js_e:
            print("[Eel] show_status failed:", js_e)
        return

    role = user.get("Status", "Customer")
    try:
        eel.redirect_main(username, role)()
    except Exception as js_e:
        print("[Eel] redirect_main failed:", js_e)
    print(f"[Auth] {username} logged in as {role}")


@eel.expose
def get_balance(username):
    try:
        return db.reference(f"{ROOT_PATH}/{username}/Account Balance (RM)").get() or 0
    except Exception as e:
        print("[Firebase] Balance error:", e)
        return 0

@eel.expose
def deposit_money(username, amount):
    try:
        ref = db.reference(f"{ROOT_PATH}/{username}")
        user = ref.get()
        if not user: return eel.show_status("❌ User not found")()
        bal = float(user.get("Account Balance (RM)", 0))
        new_bal = bal + float(amount)
        ref.update({"Account Balance (RM)": new_bal})
        eel.show_status(f"✅ Deposited RM {amount}. New balance: RM {new_bal}")()
    except Exception as e:
        print("Deposit error:", e); eel.show_status("❌ Deposit failed.")()

@eel.expose
def withdraw_money(username, amount):
    try:
        ref = db.reference(f"{ROOT_PATH}/{username}")
        user = ref.get()
        if not user: return eel.show_status("❌ User not found")()
        bal = float(user.get("Account Balance (RM)", 0))
        amount = float(amount)
        if amount > bal: return eel.show_status("❌ Insufficient funds.")()
        new_bal = bal - amount
        ref.update({"Account Balance (RM)": new_bal})
        eel.show_status(f"✅ Withdrew RM {amount}. New balance: RM {new_bal}")()
    except Exception as e:
        print("Withdraw error:", e); eel.show_status("❌ Withdraw failed.")()

@eel.expose
def staff_clear_lockdown(username, password):
    global Lockdown
    user = validate_user(username, password)
    if not user or user.get("Status") != "Staff":
        eel.show_status("❌ Invalid staff credentials")(); return
    with state_lock: Lockdown = 0
    broadcast_state()
    send_telegram(f"✅ {username} cleared the lockdown.")
    eel.show_status("✅ Lockdown cleared")()

@eel.expose
def staff_trigger_lockdown(username, password):
    global Lockdown
    user = validate_user(username, password)
    if not user or user.get("Status") != "Staff":
        eel.show_status("❌ Invalid staff credentials")(); return
    with state_lock: Lockdown = 1
    broadcast_state()
    send_telegram(f"🚨 {username} triggered a lockdown!")
    eel.show_status("🚨 Lockdown triggered!")()

# ==========================================================
# ---------------- HOOK WIRING (from sensor file) ----------
# ==========================================================
def _hook_sos_detect():
    """Called by MasterPiCounterSlider when SOS pattern is detected."""
    global Lockdown
    with state_lock:
        if Lockdown != 1:
            Lockdown = 1
            print("[HOOK] SOS → setting Lockdown=1 and broadcasting to slaves.")
            broadcast_state()
            send_telegram("🚨 SOS detected by cashbox! Lockdown activated.")

def _hook_slider_unlock():
    global BackdoorLock
    """Called by MasterPiCounterSlider when slider PIN accepted."""
    # Telegram only; feel free to also publish BackdoorLock:0 if you wish
    BackdoorLock = 1
    broadcast_state()
    send_telegram("🔓 Back door unlocked (slider PIN accepted).")
    
def _hook_slider_relock():
    """Called when the open window expires and door re-arms."""
    global BackdoorLock
    
    BackdoorLock = 0
    broadcast_state()
    send_telegram("🔒 Back door re-locked.")




# Attach hooks
mpcs.on_sos_detect = _hook_sos_detect
mpcs.on_slider_unlock = _hook_slider_unlock
mpcs.on_slider_relock = _hook_slider_relock

# ==========================================================
# ---------------- STARTUP ---------------------------------
# ==========================================================
def start_eel():
    try:
        eel.start("landing.html", size=(1000, 600), port=8001, mode='default', block=True, close_callback=None)
    except SystemExit:
        # Frontend may try to exit on navigation; just re-launch UI
        start_eel()
    except Exception as e:
        print("[Eel] Startup error:", e)

if __name__ == "__main__":
    print("MasterPi Web System starting...")

    # 1) MQTT + ThingSpeak
    threading.Thread(target=mqtt_loop, daemon=True).start()

    # 2) Start your sensor/ML loop in background
    threading.Thread(target=mpcs.monitor_cashbox_then_switch, daemon=True).start()

    # 3) Frontend (Eel)
    start_eel()

    # keep alive (if Eel ever returns)
    while True:
        time.sleep(1)



