#!/usr/bin/env python3
import json, time, statistics
from dataclasses import dataclass, asdict
from gpiozero import DistanceSensor

@dataclass
class PinConfig:
    trig: int = 22     # GPIO 22
    echo: int = 27     # GPIO 27

@dataclass
class CalibConfig:
    sample_hz: float = 40.0
    min_cm: float = 2.0
    max_cm: float = 30.0          
    hold_seconds: float = 2.0
    band_margin_cm: float = 0.5   
    # Snapshot settings
    n_snapshots_per_section: int = 5
    snapshot_secs: float = 0.20   

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
class CalibResult:
    pins: PinConfig
    calib: CalibConfig
    bands: Bands
    meta: dict

def _snapshot(sensor: DistanceSensor, secs: float, hz: float, min_cm: float, max_cm: float) -> float:
    """
    Take readings for `secs` seconds at `hz` and return the median of valid readings.
    """
    buf = []
    dt = 1.0 / hz
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            d = sensor.distance * 100.0  # meters -> cm
        except Exception:
            d = None
        if d is not None and min_cm < d < max_cm:
            buf.append(d)
        time.sleep(dt)
    if not buf:
        raise RuntimeError("No samples captured in snapshot—check wiring/aim/range.")
    return statistics.median(buf)

def _collect_section(sensor: DistanceSensor, cfg: CalibConfig, label: str):
    """
    Collect `cfg.n_snapshots_per_section` snapshots for a section.
    Return (average_of_medians, list_of_medians).
    """
    medians = []
    n = cfg.n_snapshots_per_section
    print(f"\n[Cal] Collecting {n} snapshots for {label} section.")
    for i in range(1, n+1):
        input(f"[Cal] Position hand (or object) at {label} section, then press Enter to take snapshot {i}/{n}…")
        snap_median = _snapshot(sensor, cfg.snapshot_secs, cfg.sample_hz, cfg.min_cm, cfg.max_cm)
        medians.append(snap_median)
        print(f"[Cal]  Snapshot {i}: median = {snap_median:.2f} cm")
    avg = statistics.mean(medians)
    print(f"[Cal] {label} average of {n} medians: {avg:.2f} cm")
    return avg, medians

def main():
    pins = PinConfig()
    cfg  = CalibConfig()

    # gpiozero expects meters for max_distance
    sensor = DistanceSensor(trigger=pins.trig, echo=pins.echo, max_distance=cfg.max_cm/100.0)

    print("\n[Cal] Mount: single ultrasonic on LEFT wall, facing RIGHT across the 3 sections.")
    print(f"[Cal] Snapshot settings: {cfg.n_snapshots_per_section} snapshots/section, "
          f"{cfg.snapshot_secs}s per snapshot at {cfg.sample_hz}Hz")
    print(f"[Cal] Valid range: {cfg.min_cm:.1f}–{cfg.max_cm:.1f} cm")

    # LEFT
    Lc_avg, L_medians = _collect_section(sensor, cfg, "LEFT")

    # MIDDLE
    Mc_avg, M_medians = _collect_section(sensor, cfg, "MIDDLE")

    # RIGHT
    Rc_avg, R_medians = _collect_section(sensor, cfg, "RIGHT")

    # Enforce order (should be L < M < R)
    centers = sorted([("L", Lc_avg), ("M", Mc_avg), ("R", Rc_avg)], key=lambda t: t[1])
    labels  = "".join([lab for lab,_ in centers])
    if labels != "LMR":
        print(f"[Warn] Measured order was {labels}. Check mount/alignment; proceeding with sorted values.")
    Lc, Mc, Rc = [v for _,v in centers]

    mid_LM = (Lc+Mc)/2.0
    mid_MR = (Mc+Rc)/2.0
    margin = cfg.band_margin_cm

    bands = Bands(
        L_center_cm=Lc, M_center_cm=Mc, R_center_cm=Rc,
        L_max_cm   = mid_LM - margin,
        M_min_cm   = mid_LM + margin,
        M_max_cm   = mid_MR - margin,
        R_min_cm   = mid_MR + margin,
    )

    result = CalibResult(
        pins=pins,
        calib=cfg,
        bands=bands,
        meta={
            "note": "Distances in centimeters. Bands classify: x<L_max -> L; M_min<=x<=M_max -> M; x>R_min -> R.",
            "centers_cm": {"L": Lc, "M": Mc, "R": Rc},
            "midpoints_cm": {"LM": mid_LM, "MR": mid_MR},
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "snapshots": {
                "L_medians": L_medians,
                "M_medians": M_medians,
                "R_medians": R_medians,
                "L_avg": Lc_avg,
                "M_avg": Mc_avg,
                "R_avg": Rc_avg,
                "n_snapshots": cfg.n_snapshots_per_section,
                "snapshot_secs": cfg.snapshot_secs
            }
        }
    )

    with open("horizontal_bands.json", "w") as f:
        json.dump({
            "pins": asdict(result.pins),
            "calib": asdict(result.calib),
            "bands": asdict(result.bands),
            "meta":  result.meta
        }, f, indent=2)

    print("\n[Cal] Saved calibration → horizontal_bands.json")
    print(f"[Cal] Bands:")
    print(f"      L center ≈ {Lc:.2f}, L_max = {bands.L_max_cm:.2f}")
    print(f"      M center ≈ {Mc:.2f}, M_min = {bands.M_min_cm:.2f}, M_max = {bands.M_max_cm:.2f}")
    print(f"      R center ≈ {Rc:.2f}, R_min = {bands.R_min_cm:.2f}")
    print("[Cal] Done.")

if __name__ == "__main__":
    main()
