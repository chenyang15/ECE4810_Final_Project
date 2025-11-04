#!/usr/bin/env python3
"""
train_enter_exit.py

- Expects /mnt/data/ultrasonic_centered_40.csv (one row per sample: window_id, sample_index (0..39), distance, label)
- Produces:
    - /mnt/data/ultrasonic_flattened_40.csv  (one row per window, columns d0..d39, label)
    - /mnt/data/enter_exit_model.joblib      (saved sklearn Pipeline with scaler + model)
    - /mnt/data/enter_exit_report.txt        (text summary)
"""

import os
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.ensemble import RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score
import joblib

# --- CONFIG ---
IN_CSV = "C:/Users/cy/Desktop/Monash_Engineering/ECE4810/Project/ultrasonic_centered_40.csv"
OUT_FLAT = "C:/Users/cy/Desktop/Monash_Engineering/ECE4810/Project/ultrasonic_flattened_40.csv"
MODEL_OUT = "C:/Users/cy/Desktop/Monash_Engineering/ECE4810/Project/bigbrainmodel.joblib"
REPORT_OUT = "C:/Users/cy/Desktop/Monash_Engineering/ECE4810/Project/report.txt"
RANDOM_SEED = 42
TEST_SIZE = 0.2
MODEL = "random_forest"  # choices: "random_forest", "mlp"
N_JOBS = -1

# --- Helpers ---
def pivot_windows(df, window_col=None, sample_col="sample_index", value_col=None, label_col="label"):
    """
    Pivot sample rows into one row per window:
      result columns: window_col, d0..d39, label
    """
    # detect names if None
    if window_col is None:
        # guess window column (contains 'window' or first column)
        possible = [c for c in df.columns if "window" in c.lower()]
        window_col = possible[0] if possible else df.columns[0]
    if value_col is None:
        # try to detect distance column
        candidates = ["distance","distance_cm","value","dist"]
        found = None
        for c in df.columns:
            if c.lower() in candidates:
                found = c
                break
        if found is None:
            # fallback numeric column that's not window/sample/label
            numeric = df.select_dtypes(include=[np.number]).columns.tolist()
            other = [c for c in numeric if c not in (sample_col,)]
            value_col = other[0] if other else df.columns[-1]
        else:
            value_col = found

    # pivot
    pivot = df.pivot(index=window_col, columns=sample_col, values=value_col)
    # rename columns to d0..d39 (sorted by column)
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    pivot.columns = [f"d{int(c)}" for c in pivot.columns]
    # bring label (take first non-null label per window)
    if label_col in df.columns:
        labels = df.groupby(window_col)[label_col].agg(lambda s: s.dropna().astype(str).iloc[0] if len(s.dropna())>0 else np.nan)
        pivot["label"] = labels
    else:
        pivot["label"] = np.nan
    pivot = pivot.reset_index()
    return pivot

# --- Main ---
def main():
    assert os.path.exists(IN_CSV), f"Input CSV not found: {IN_CSV}"

    df = pd.read_csv(IN_CSV)
    print(f"Loaded {len(df)} rows from {IN_CSV}. Columns: {list(df.columns)}")

    # Pivot to 1 row per window
    flat = pivot_windows(df, sample_col="sample_index", label_col="label")
    print(f"Pivoted to {len(flat)} windows, shape = {flat.shape}")

    # Save flattened CSV for inspection / training convenience
    flat.to_csv(OUT_FLAT, index=False)
    print("Saved flattened CSV to:", OUT_FLAT)

    # Drop windows with missing label
    if flat["label"].isna().any():
        n_missing = flat["label"].isna().sum()
        print(f"Warning: {n_missing} windows have missing label. They will be removed.")
        flat = flat.dropna(subset=["label"])
    if len(flat) == 0:
        raise RuntimeError("No labeled windows found after dropping unlabeled rows.")

    # Standardize label values: make lowercase and strip, then map common variants to ENTER/EXIT
    def normalize_label(lbl):
        s = str(lbl).strip().lower()
        if s in ("enter", "entry", "in"):
            return "ENTER"
        if s in ("exit", "egress", "out"):
            return "EXIT"
        # if labels are already one of these, map accordingly
        if "enter" in s:
            return "ENTER"
        if "exit" in s:
            return "EXIT"
        # fallback: return uppercase
        return str(lbl).upper()

    flat["label"] = flat["label"].apply(normalize_label)

    # Features
    feat_cols = [c for c in flat.columns if c.startswith("d")]
    X = flat[feat_cols].copy()
    y_raw = flat["label"].copy()

    # Fill NaNs in features: simple strategies -> median per column
    if X.isna().any().any():
        X = X.fillna(X.median(axis=0))

    # Encode labels
    le = LabelEncoder()
    y = le.fit_transform(y_raw)  # 0/1
    class_map = {i: c for i, c in enumerate(le.classes_)}
    print("Classes found and encoding:", class_map)

    # Train/test split (stratified)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_SEED, stratify=y
    )

    # Pipeline: scaler + model
    if MODEL == "random_forest":
        clf = RandomForestClassifier(n_estimators=200, random_state=RANDOM_SEED, n_jobs=N_JOBS)
    elif MODEL == "mlp":
        clf = MLPClassifier(hidden_layer_sizes=(128,64), max_iter=500, random_state=RANDOM_SEED)
    else:
        raise ValueError("Unsupported MODEL value. Use 'random_forest' or 'mlp'.")

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", clf)
    ])

    # Cross-validation on training set (3-fold stratified)
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=RANDOM_SEED)
    try:
        cv_scores = cross_val_score(pipeline, X_train, y_train, cv=cv, scoring="accuracy", n_jobs=N_JOBS)
        print(f"Cross-val accuracy (3-fold) on training set: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")
    except Exception as e:
        print("Warning: cross_val_score failed:", e)

    # Fit on train
    pipeline.fit(X_train, y_train)
    print("Model trained.")

    # Evaluate on test set
    y_pred = pipeline.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=le.classes_)
    cm = confusion_matrix(y_test, y_pred)

    print("Test accuracy:", acc)
    print("Classification report:\n", report)
    print("Confusion matrix:\n", cm)

    # Save model + label encoder + metadata
    saved_obj = {
        "pipeline": pipeline,
        "label_encoder": le,
        "feature_columns": feat_cols,
        "class_map": class_map
    }
    joblib.dump(saved_obj, MODEL_OUT)
    print("Saved model to:", MODEL_OUT)

    # Save a short text report
    with open(REPORT_OUT, "w") as f:
        f.write(f"Input CSV: {IN_CSV}\n")
        f.write(f"Flattened CSV: {OUT_FLAT}\n")
        f.write(f"Model path: {MODEL_OUT}\n\n")
        f.write(f"Train/test split: test_size={TEST_SIZE}\n")
        f.write(f"Classes: {class_map}\n\n")
        f.write(f"Test accuracy: {acc:.6f}\n\n")
        f.write("Classification report:\n")
        f.write(report)
        f.write("\nConfusion matrix:\n")
        f.write(np.array2string(cm))
    print("Saved text report to:", REPORT_OUT)

    # Done
    print("Training complete.")

if __name__ == "__main__":
    main()
