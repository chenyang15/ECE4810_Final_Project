#!/usr/bin/env python3
"""
cashbox_first_thonny.py


Integrated program with LED feedback:
- Slider accepted LED (BCM 6) lights for 2s on each accepted digit.
- Cashbox progress LEDs (BCM 13,19,26) track pattern progress.
- Note: ALARM output uses BCM 5 (change ALARM_PIN if needed).
"""


import time
import os
import csv
import joblib
import numpy as np
import statistics
import json
import sys
import math
from dataclasses import dataclass
from typing import Optional, List, Tuple
from threading import Lock, Thread
from collections import deque
from gpiozero import DistanceSensor, LED
import requests


# ===== Integration hooks (safe no-ops; master can override at runtime) =====
def on_sos_detect():
    """Called when the cashbox SOS pattern is detected."""
    pass


def on_slider_unlock():
    """Called when the slider PIN is accepted (door unlocked)."""
    pass
# ============================================================================

def on_slider_relock():
    pass



# ------------------- CONFIG -------------------
# Pins (BCM)
TRIG_LOCK = 4        # slider sensor TRIG
ECHO_LOCK = 17       # slider sensor ECHO
TRIG_CASH = 22       # cashbox sensor TRIG
ECHO_CASH = 27       # cashbox sensor ECHO


# LEDs / Alarm (BCM)
LED_R_PIN, LED_G_PIN, LED_B_PIN = 16, 20, 21


# Cashbox progress LEDs (user requested)
CASH_LED_PINS = [13, 19, 26]   # 1st, 2nd, 3rd progress LEDs


# Slider accepted LED (user requested)
SLIDER_ACCEPT_LED_PIN = 6


# Alarm output
ALARM_PIN = 5


# Lock / slider parameters
DEFAULT_CENTERS = [5.49, 6.86, 8.62, 11.03, 13.27, 15.54, 17.79]
CENTERS_FILE = "digit_centers.pkl"
PIN = [2, 5, 1, 3]       # passcode digits (do not include RESET digit)
DWELL_MS = 2000
SAMPLE_HZ_LOCK = 40
OPEN_HOLD_S = 17.0
HYSTERESIS_CM = 0.4
HOME_MARGIN_CM = 1.0
MIN_HOME_CM = 3.2
RESET_DWELL_MS = DWELL_MS + 250
RESET_COOLDOWN_S = 2.0
SLOW_DIGIT = None
SLOW_DWELL_MS = DWELL_MS + 300


# Cashbox parameters
CASH_DWELL_MS = 400
CASH_REQUIRE_LEAVE_MS = 300
CASH_EVENT_GAP_S = 10.0
CASH_WINDOW_S = 12.0
CASH_LOCKOUT_S = 15.0
CASH_SAMPLE_HZ = 20.0
CASH_SMOOTH_N = 7
CASH_MAX_DETECT_CM = 30.0


# Mode & multiplexing tuning
LOCK_LEAVE_CONFIRM_MS = 120      # debounce before switching to slider (ms)
LOCK_REENTRY_DELAY_S = 5.0       # cooldown after returning to cashbox (s)
ULTRASONIC_SETTLE_S = 0.06       # hold trigger_lock this long after reading (s)


# Debug toggles
DEBUG_LOCK = False
DEBUG_CASH = False


# ------------------- Adaptive learning (slider) tuning -------------------
LEARN_ENABLED = True              # set False to disable online learning
SAMPLES_PER_DIGIT = 150
MIN_SAMPLES_TO_UPDATE = 30
MAX_STD_CM = 0.8
MAX_SHIFT_PER_UPDATE_CM = 0.6
PERSIST_PATH = CENTERS_FILE


# ------------------- Hardware init -------------------
LED_R, LED_G, LED_B = LED(LED_R_PIN), LED(LED_G_PIN), LED(LED_B_PIN)
ALARM_OUT = LED(ALARM_PIN)


# progress LEDs and slider accept LED
CASH_LEDS = [LED(pin) for pin in CASH_LED_PINS]
SLIDER_ACCEPT_LED = LED(SLIDER_ACCEPT_LED_PIN)


def set_leds(r=False, g=False, b=False):
    try:
        LED_R.value, LED_G.value, LED_B.value = int(bool(r)), int(bool(g)), int(bool(b))
    except Exception:
        pass


def flash_alarm_bg(duration_s=6.0):
    """Non-blocking alarm flash (runs in background thread)."""
    def job():
        end = time.time() + duration_s
        while time.time() < end:
            try:
                ALARM_OUT.on()
                set_leds(r=True, g=False, b=False)
                time.sleep(0.18)
                ALARM_OUT.off()
                set_leds(r=False, g=False, b=False)
                time.sleep(0.18)
            except Exception:
                time.sleep(0.1)
        set_leds(r=False, g=False, b=True)
    Thread(target=job, daemon=True).start()


def cooldown_blink_bg(duration_s):
    """Non-blocking cooldown blink (green/blue) to indicate re-entry delay."""
    def job():
        end = time.time() + duration_s
        while time.time() < end:
            set_leds(r=False, g=True, b=False)
            time.sleep(0.35)
            set_leds(r=False, g=False, b=True)
            time.sleep(0.35)
    Thread(target=job, daemon=True).start()


def slider_accept_light_bg(duration_s=2.0):
    """Light the slider accepted LED for duration_s seconds (non-blocking)."""
    def job():
        try:
            SLIDER_ACCEPT_LED.on()
            time.sleep(duration_s)
            SLIDER_ACCEPT_LED.off()
        except Exception:
            pass
    Thread(target=job, daemon=True).start()


def update_cashbox_progress_leds(count: int):
    """
    Light the first `count` progress LEDs (0..3).
    count=0 => all off
    count=1 => LED1 on
    count=2 => LED1+LED2 on
    count=3 => all three on
    """
    try:
        for i, led in enumerate(CASH_LEDS):
            if i < count:
                led.on()
            else:
                led.off()
    except Exception:
        pass


# Combined CSV logging
LOG_FILE = "integrated_log.csv"
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp","system","event","detail","distance_cm","digit_or_comp","state"])


def log_event(system, event, detail="", dist=None, digit_or_comp=None, state=None):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        w.writerow([ts, system, event, detail,
                    f"{dist:.2f}" if isinstance(dist,(int,float)) else "",
                    digit_or_comp if digit_or_comp is not None else "", state if state else ""])
    print(f"{ts} | {system} | {event} | {detail} | d={dist if dist is not None else 'NA'} | idx={digit_or_comp} | state={state}")


# ------------------- Calibration load (lock centers) -------------------
try:
    DIGIT_CENTERS = joblib.load(CENTERS_FILE)
    print(f"[Lock] Loaded {CENTERS_FILE}: {DIGIT_CENTERS}")
except Exception as e:
    DIGIT_CENTERS = sorted(DEFAULT_CENTERS)
    print(f"[Lock] Could not load {CENTERS_FILE} ({e}). Using defaults: {DIGIT_CENTERS}")


N_NOTCHES = len(DIGIT_CENTERS)
RESET_DIGIT = N_NOTCHES - 1
HOME_MAX_CM = max(MIN_HOME_CM, DIGIT_CENTERS[0] - HOME_MARGIN_CM)
print(f"[Lock] HOME_MAX_CM = {HOME_MAX_CM:.2f} cm (digit0={DIGIT_CENTERS[0]:.2f})")


# Build per-digit acceptance bands
accept_band = []
for i in range(N_NOTCHES):
    if i == 0:
        half = (DIGIT_CENTERS[1] - DIGIT_CENTERS[0]) / 2.0
    elif i == N_NOTCHES - 1:
        half = (DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]) / 2.0
    else:
        left = DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]
        right = DIGIT_CENTERS[i+1] - DIGIT_CENTERS[i]
        half = min(left, right) / 2.0
    accept_band.append(max(half, 0.6))
print(f"[Lock] accept bands (cm): {['{:.2f}'.format(b) for b in accept_band]}")


# ------------------- Calibration loader for cashbox -------------------
@dataclass
class Pins:
    trig: int
    echo: int


@dataclass
class Bands:
    L_center_cm: float
    M_center_cm: float
    R_center_cm: float
    L_max_cm: float
    M_min_cm: float
    M_max_cm: float
    R_min_cm: float


@dataclass
class Calib:
    sample_hz: float
    min_cm: float
    max_cm: float


def load_calibration(path="horizontal_bands.json") -> Tuple[Pins, Calib, Bands]:
    """Load horizontal_bands.json and return Pins, Calib, Bands."""
    with open(path, "r") as f:
        data = json.load(f)


    # pins
    pins = Pins(**data["pins"])


    # calib: expect keys sample_hz, min_cm, max_cm
    cdict = data.get("calib", {})
    calib = Calib(
        sample_hz=cdict.get("sample_hz", CASH_SAMPLE_HZ),
        min_cm=cdict.get("min_cm", 2.0),
        max_cm=cdict.get("max_cm", 200.0)
    )


    # bands
    bands = Bands(**data["bands"])


    return pins, calib, bands


def median_push(buf, x, n):
    buf.append(x)
    if len(buf) > n:
        buf.pop(0)
    return statistics.median(buf)


def band_of(x: float, b: Bands) -> Optional[str]:
    if x < b.L_max_cm: return "L"
    if b.M_min_cm <= x <= b.M_max_cm: return "M"
    if x > b.R_min_cm: return "R"
    return None


# ------------------- Trigger lock for time-multiplexing -------------------
trigger_lock = Lock()


def read_sensor_serialized(sensor, retries=1):
    """Read a DistanceSensor safely under trigger_lock, retry optionally, then wait ULTRASONIC_SETTLE_S."""
    with trigger_lock:
        raw = None
        try:
            raw = sensor.distance
        except Exception:
            raw = None
        if raw is None and retries:
            # quick retry
            time.sleep(0.01)
            try:
                raw = sensor.distance
            except Exception:
                raw = None
        time.sleep(ULTRASONIC_SETTLE_S)
    return raw


# ------------------- Initialize sensors (lazy cashbox) -------------------
try:
    sensor_lock = DistanceSensor(trigger=TRIG_LOCK, echo=ECHO_LOCK, max_distance=0.4)
    print("[HW] Lock sensor initialized.")
except Exception as e:
    print("[ERROR] Lock sensor init failed:", e)
    sensor_lock = None


# ------------------- Adaptive learning structures -------------------
_digit_buffers = [deque(maxlen=SAMPLES_PER_DIGIT) for _ in range(N_NOTCHES)]


def save_digit_centers(path=PERSIST_PATH):
    try:
        joblib.dump(DIGIT_CENTERS, path)
        print(f"[LEARN] Saved updated centers to {path}: {DIGIT_CENTERS}")
    except Exception as e:
        print(f"[LEARN] Failed to save centers: {e}")


def maybe_update_digit_center(digit: int):
    """Attempt to update center for 'digit' using samples in buffer."""
    if not LEARN_ENABLED:
        return False
    buf = list(_digit_buffers[digit])
    if len(buf) < MIN_SAMPLES_TO_UPDATE:
        return False
    med = statistics.median(buf)
    std = statistics.pstdev(buf) if len(buf) > 1 else 0.0
    if std > MAX_STD_CM:
        if DEBUG_LOCK:
            print(f"[LEARN] digit {digit}: std {std:.2f} > {MAX_STD_CM:.2f}, skipping")
        return False
    old = DIGIT_CENTERS[digit]
    shift = med - old
    if abs(shift) > MAX_SHIFT_PER_UPDATE_CM:
        shift = math.copysign(MAX_SHIFT_PER_UPDATE_CM, shift)
    new_center = old + shift
    # sanity: preserve ordering by small margin
    left_ok = (digit == 0) or (new_center > DIGIT_CENTERS[digit-1] + 0.2)
    right_ok = (digit == N_NOTCHES-1) or (new_center < DIGIT_CENTERS[digit+1] - 0.2)
    if not (left_ok and right_ok):
        if DEBUG_LOCK:
            print(f"[LEARN] digit {digit}: proposed {new_center:.2f} violates ordering, skip")
        return False
    # commit
    DIGIT_CENTERS[digit] = new_center
    # recompute accept_band
    for i in range(N_NOTCHES):
        if i == 0:
            half = (DIGIT_CENTERS[1] - DIGIT_CENTERS[0]) / 2.0
        elif i == N_NOTCHES - 1:
            half = (DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]) / 2.0
        else:
            left = DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]
            right = DIGIT_CENTERS[i+1] - DIGIT_CENTERS[i]
            half = min(left, right) / 2.0
        accept_band[i] = max(half, 0.6)
    save_digit_centers(PERSIST_PATH)
    print(f"[LEARN] digit {digit} center updated: {old:.2f} -> {new_center:.2f} (med={med:.2f}, std={std:.2f})")
    log_event("LEARN", "center_update", detail=f"d{digit} {old:.2f}->{new_center:.2f}", dist=med, digit_or_comp=digit)
    return True


# ------------------- Slider mode (original logic with learning hook) -------------------
def run_slider_mode(sensor_lock_obj):
    dt = 1.0 / SAMPLE_HZ_LOCK
    STATE = "ARMED"
    buffer = []
    last_digit = None
    entering_since = None
    open_since = None
    last_accepted_center = None
    last_reset_ts = 0.0


    def nearest_digit_candidate(d_cm):
        diffs = [abs(d_cm - c) for c in DIGIT_CENTERS]
        return int(np.argmin(diffs))


    def dwell_required_ms_for(d):
        if d == RESET_DIGIT:
            return RESET_DWELL_MS
        if SLOW_DIGIT is not None and d == SLOW_DIGIT:
            return SLOW_DWELL_MS
        return DWELL_MS


    def is_within_accept_band(d_cm, digit):
        c = DIGIT_CENTERS[digit]
        return abs(d_cm - c) <= accept_band[digit]


    def accept_digit_local(digit, dist_cm):
        nonlocal buffer, last_accepted_center
        buffer.append(digit)
        last_accepted_center = DIGIT_CENTERS[digit]
        log_event("LOCK", "digit_accepted", "held", dist=dist_cm, digit_or_comp=digit, state=STATE)
        print(f"[LOCK] Accepted digit: {digit} (buffer={buffer})")
        # light slider accept LED for 2 seconds (non-blocking)
        slider_accept_light_bg(2.0)
        # record for learning
        try:
            if LEARN_ENABLED:
                _digit_buffers[digit].append(dist_cm)
                maybe_update_digit_center(digit)
        except Exception as e:
            if DEBUG_LOCK:
                print("[LEARN] error recording sample:", e)


    def check_pin_and_act():
        nonlocal buffer, STATE, open_since
        if buffer[-len(PIN):] == PIN:
            STATE = "OPEN"
            open_since = time.time()
            set_leds(r=False, g=True, b=False)
            log_event("LOCK", "unlock", "PIN correct", state=STATE)
            print("[LOCK] PIN correct → UNLOCK")
            buffer.clear()
            return True
        else:
            log_event("LOCK", "unlock_failed", "PIN incorrect", state=STATE)
            print("[LOCK] PIN incorrect — flashing RED")
            for _ in range(2):
                set_leds(r=True, g=False, b=False); time.sleep(0.12)
                set_leds(r=False, g=False, b=False); time.sleep(0.12)
            set_leds(r=False, g=False, b=True)
            buffer.clear()
            return False


    try:
        while True:
            
            raw = read_sensor_serialized(sensor_lock_obj)
            now = time.time()
            if raw is None:
                time.sleep(dt); continue
            d_cm = raw * 100.0
            if DEBUG_LOCK:
                print(f"[SLIDER DBG] d={d_cm:5.2f} cm  buf={buffer}", end="\r")

            # print("state check") 
            # OPEN handling
            if STATE == "OPEN":
                  
                if (now - open_since) > OPEN_HOLD_S:
                    try:
                        on_slider_relock()
                    except Exception as _hook_err:
                        print("[HOOK] on_slider_relock() failed:",_hook_err)
                    STATE = "ARMED"
                    buffer = []
                    last_accepted_center = None
                    set_leds(r=False, g=False, b=True)
                    log_event("LOCK", "state_rearmed", "re-armed after open hold", dist=d_cm)
                    return "unlock"
                time.sleep(dt); continue


            # hysteresis after accept
            if last_accepted_center is not None:
                if abs(d_cm - last_accepted_center) <= HYSTERESIS_CM:
                    time.sleep(dt); continue
                else:
                    last_accepted_center = None


            cand = nearest_digit_candidate(d_cm)
            if not is_within_accept_band(d_cm, cand):
                time.sleep(dt); continue
            digit = cand


            # RESET notch handling
            if digit == RESET_DIGIT:
                if last_digit != RESET_DIGIT:
                    last_digit = RESET_DIGIT
                    entering_since = now
                else:
                    if entering_since and (now - entering_since)*1000.0 >= dwell_required_ms_for(RESET_DIGIT):
                        if (now - last_reset_ts) > RESET_COOLDOWN_S:
                            log_event("LOCK","reset","buffer_cleared_by_reset",dist=d_cm,digit_or_comp=RESET_DIGIT)
                            print("\n[LOCK] RESET notch detected -> returning to CASHBOX.")
                            for _ in range(2):
                                set_leds(r=True,g=False,b=False); time.sleep(0.12)
                                set_leds(r=False,g=False,b=False); time.sleep(0.08)
                            set_leds(r=False,g=False,b=True)
                            return "reset"
                time.sleep(dt); continue


            # normal digits
            if digit != last_digit:
                last_digit = digit
                entering_since = now
            else:
                if entering_since and (now - entering_since)*1000.0 >= dwell_required_ms_for(digit):
                    accept_digit_local(digit, d_cm)
                    entering_since = None
                    last_digit = None
                    if len(buffer) >= len(PIN):
                        if check_pin_and_act():
                            try:
                                on_slider_unlock()
                                # return "unlock"
                                # ensure the green "OPEN" LED stays visible for the configured time
                                print(f"[SYSTEM] Keeping GREEN for {OPEN_HOLD_S}s after unlock.")
                                end = time.time() + OPEN_HOLD_S
                                while time.time() < end:
                                    set_leds(r=False, g=True, b=False)
                                    time.sleep(0.08)
                            except Exception as _hook_err:
                                print("[HOOK] on_slider_unlock() failed:", _hook_err)


                            # return "unlock"
            time.sleep(dt)
    except KeyboardInterrupt:
        return "cancel"
    except Exception as ex:
        log_event("LOCK","error",detail=str(ex))
        print("[LOCK] Exception:", ex)
        return "error"


# ------------------- Guided recalibration helper -------------------
def guided_recalibrate_single_pass(sensor_obj, cfg_samples=15, per_sample_secs=0.08):
    """
    Manual routine to sample each notch in order and overwrite DIGIT_CENTERS.
    Call from Thonny if you want to perform a guided recalibration.
    """
    print("[CAL] Starting guided recalibration. Follow prompts.")
    new_centers = []
    for d in range(N_NOTCHES):
        input(f"[CAL] Move slider to digit index {d} and press Enter to sample {cfg_samples} times...")
        samples = []
        for _ in range(cfg_samples):
            raw = read_sensor_serialized(sensor_obj)
            if raw is not None:
                samples.append(raw * 100.0)
            time.sleep(per_sample_secs)
        if not samples:
            raise RuntimeError(f"No samples for digit {d}")
        c = statistics.median(samples)
        new_centers.append(c)
        print(f"[CAL] digit {d} median = {c:.2f}")
    # sanity check ordering
    if any(new_centers[i] >= new_centers[i+1] for i in range(len(new_centers)-1)):
        print("[CAL] Recalibration failed ordering check. Aborting.")
        return False
    for i in range(N_NOTCHES):
        DIGIT_CENTERS[i] = new_centers[i]
    # recompute accept_band
    for i in range(N_NOTCHES):
        if i == 0:
            half = (DIGIT_CENTERS[1] - DIGIT_CENTERS[0]) / 2.0
        elif i == N_NOTCHES - 1:
            half = (DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]) / 2.0
        else:
            left = DIGIT_CENTERS[i] - DIGIT_CENTERS[i-1]
            right = DIGIT_CENTERS[i+1] - DIGIT_CENTERS[i]
            half = min(left, right) / 2.0
        accept_band[i] = max(half, 0.6)
    save_digit_centers(PERSIST_PATH)
    print("[CAL] Recalibration committed.")
    return True


# ------------------- Cashbox primary loop with lock monitoring -------------------
def monitor_cashbox_then_switch():
    # load cashbox calibration file
    try:
        pins, calib, bands = load_calibration("horizontal_bands.json")
    except Exception as e:
        log_event("SYSTEM", "fatal", detail=f"failed_load_horizontal_bands: {e}")
        print("[FATAL] horizontal_bands.json load failed:", e)
        return


    # initialize cashbox sensor
    try:
        sensor_cash = DistanceSensor(trigger=TRIG_CASH, echo=ECHO_CASH, max_distance=calib.max_cm/100.0)
        print("[HW] Cashbox sensor initialized.")
    except Exception as e:
        log_event("CASHBOX","error",detail=f"sensor_init_failed: {e}")
        print("[ERROR] Cashbox sensor init failed:", e)
        return


    # runtime config
    class Cfg:
        pattern = ["L","R","M"]
        dwell_ms = CASH_DWELL_MS
        require_leave_ms = CASH_REQUIRE_LEAVE_MS
        event_gap_max_s = CASH_EVENT_GAP_S
        window_sec = CASH_WINDOW_S
        lockout_sec = CASH_LOCKOUT_S
        sample_hz = CASH_SAMPLE_HZ
        smooth_n = CASH_SMOOTH_N
        max_detect_cm = CASH_MAX_DETECT_CM
        debug = DEBUG_CASH
        enable_firebase = False
        firebase_url = ""


    cfg = Cfg()
    dt_cash = 1.0 / cfg.sample_hz


    dist_buf = []
    current_band = None
    band_enter_time = None
    last_accept_time_by_band = {'L':0.0,'M':0.0,'R':0.0}
    seq = []
    last_evt_ts = 0.0
    last_trigger = 0.0


    lock_was_home = True
    lock_left_since = None


    print("[SYSTEM] Starting in CASHBOX mode (primary).")
    set_leds(r=False, g=False, b=True)
    update_cashbox_progress_leds(0)  # ensure progress LEDs off


    try:
        while True:
            now = time.time()


            # --- read cashbox (serialized) ---
            raw_cash = None
            try:
                raw_cash = read_sensor_serialized(sensor_cash)
            except Exception:
                raw_cash = None


            if raw_cash is not None:
                x = raw_cash * 100.0
                if x <= cfg.max_detect_cm and (calib.min_cm < x < calib.max_cm):
                    m = median_push(dist_buf, x, cfg.smooth_n)
                    b = band_of(m, bands)
                    if cfg.debug:
                        print(f"[CASHBOX DBG] {m:5.1f} cm band={b}")
                    if b != current_band:
                        current_band = b
                        band_enter_time = now
                    if b is not None and band_enter_time is not None:
                        if (now - band_enter_time)*1000.0 >= cfg.dwell_ms:
                            if (now - last_accept_time_by_band[b])*1000.0 >= cfg.require_leave_ms:
                                band_enter_time = None
                                last_accept_time_by_band[b] = now
                                if seq and (now - last_evt_ts) > cfg.event_gap_max_s:
                                    seq = []
                                seq.append((b, now))
                                last_evt_ts = now
                                # Update progress LEDs: light number of accepted events (cap at 3)
                                progress_count = min(len(seq), 3)
                                update_cashbox_progress_leds(progress_count)
                                log_event("CASHBOX","event",detail=f"band_{b}",dist=m,digit_or_comp=b,state="ARMED")
                                print(f"[CASHBOX] Event: {b}  Seq={[z for z,_ in seq]}")
                                seq = [(z,t) for (z,t) in seq if now - t <= cfg.window_sec]
                                labels = [z for (z, _) in seq]
                                if len(labels) >= len(cfg.pattern):
                                    if labels[-len(cfg.pattern):] == cfg.pattern and (now - last_trigger) >= cfg.lockout_sec:
                                        print("[CASHBOX] >>> MATCH! SOS Pattern Detected!")
                                        log_event("CASHBOX","pattern_match","sos",dist=None)
                                        if cfg.enable_firebase:
                                            try:
                                                requests.patch(cfg.firebase_url, json={"mode":"EMERGENCY","last_event_ts":int(time.time()*1000)}, timeout=4)
                                            except Exception as e:
                                                log_event("CASHBOX","firebase_failed",detail=str(e))
                                        last_trigger = now
                                        seq = []
                                        update_cashbox_progress_leds(0)
                                        flash_alarm_bg(duration_s=6.0)
                                        try:
                                            on_sos_detect()
                                        except Exception as _hook_err:
                                            print("[HOOK] on_sos_detect() failed:", _hook_err)




            # --- Monitor lock sensor (serialized read) ---
            if sensor_lock is not None:
                raw_lock = read_sensor_serialized(sensor_lock)
                if raw_lock is not None:
                    d_lock = raw_lock * 100.0
                    is_home = d_lock < HOME_MAX_CM
                    if DEBUG_LOCK:
                        print(f"[LOCK MON] d={d_lock:5.2f}cm is_home={is_home}", end="\r")
                    if is_home:
                        lock_was_home = True
                        lock_left_since = None
                    else:
                        if lock_was_home:
                            lock_left_since = now
                            lock_was_home = False
                        else:
                            if lock_left_since and (now - lock_left_since)*1000.0 >= LOCK_LEAVE_CONFIRM_MS:
                                # confirmed leave -> switch to slider mode
                                log_event("SYSTEM","mode_switch","cashbox->slider",dist=d_lock)
                                print("\n[SYSTEM] Slider left HOME — switching to SLIDER mode.")
                                set_leds(r=True, g=False, b=False)
                                result = run_slider_mode(sensor_lock)
                                log_event("SYSTEM","mode_switch_back",detail=result)
                                print(f"[SYSTEM] Slider mode ended (reason={result}). Returning to CASHBOX mode.")

                                # If the slider returned because of a successful unlock, keep GREEN for OPEN_HOLD_S
                                # if result == "unlock":
                                    

                                # Now show CASHBOX (blue) and do cooldown blink before resuming detection
                                set_leds(r=False, g=False, b=True)
                                cooldown_blink_bg(LOCK_REENTRY_DELAY_S)
                                time.sleep(LOCK_REENTRY_DELAY_S)

                                # clear cashbox buffers so we don't immediately repeat detections
                                dist_buf.clear()
                                seq.clear()
                                current_band = None
                                update_cashbox_progress_leds(0)
                                lock_was_home = True
                                lock_left_since = None

            time.sleep(dt_cash)
    except KeyboardInterrupt:
        print("\n[Main] Interrupted by user.")
    finally:
        try:
            sensor_cash.close()
        except Exception:
            pass
        set_leds(r=False, g=False, b=False)
        ALARM_OUT.off()
        update_cashbox_progress_leds(0)
        SLIDER_ACCEPT_LED.off()


# ------------------- Run -------------------
if __name__ == "__main__":
    print("Starting cashbox-first program with LEDs (Thonny).")
    print("If GPIO permission errors occur, restart Thonny with sudo.")
    print("Wiring notes: Cashbox progress LEDs -> BCM 13,19,26. Slider accept LED -> BCM 6. Alarm -> BCM 5 (change ALARM_PIN if needed).")
    monitor_cashbox_then_switch()








