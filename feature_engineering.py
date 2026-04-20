"""
Feature engineering pipeline for DentTime.

Usage:
    python feature_engineering.py --input "Data Collection/data.csv" --output features/
"""

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from src.features.build_profiles import build_and_save
from src.features.feature_transformer import (
    FeatureTransformer,
    build_treatment_encoding,
    load_treatment_dict,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

ARTIFACTS_DIR = Path("src/features/artifacts")


def main():
    parser = argparse.ArgumentParser(description="DentTime feature engineering pipeline")
    parser.add_argument("--input", required=True, help="Path to anonymized data.csv")
    parser.add_argument("--output", required=True, help="Output directory for Parquet files")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Loading {args.input}")
    df = pd.read_csv(args.input)
    logging.info(f"Loaded {len(df):,} rows, {len(df.columns)} columns")

    # Time-based split — never random (prevents leakage)
    train_df = df[df["appt_year_month"] <= "2025-02"].copy()
    test_df = df[df["appt_year_month"] == "2025-04"].copy()
    logging.info(f"Train: {len(train_df):,} rows (≤ 2025-02)")
    logging.info(f"Test:  {len(test_df):,} rows (2025-04)")
    logging.info("Note: 2025-03 is absent from source data — documented gap")

    if len(train_df) == 0:
        raise ValueError("Train split is empty — check appt_year_month column and date range")
    if len(test_df) == 0:
        raise ValueError("Test split is empty — check appt_year_month column and date range")

    # Drop post-visit leakage columns before any further processing
    LEAKAGE_COLS = ["checkin_delay_min", "tx_record_offset_min", "receipt_offset_min"]
    leakage_present = [c for c in LEAKAGE_COLS if c in train_df.columns]
    if leakage_present:
        logging.info(f"Dropping post-visit leakage columns: {leakage_present}")
        train_df = train_df.drop(columns=leakage_present)
        test_df = test_df.drop(columns=leakage_present)

    # Build profiles from train split only (no test data leaks in)
    logging.info("Building doctor and clinic profiles from train split only...")
    build_and_save(train_df, ARTIFACTS_DIR)
    logging.info(f"Profiles written to {ARTIFACTS_DIR}/")

    # Build deterministic treatment encoding from dict keys (no train-data dependency)
    treatment_dict = load_treatment_dict(str(ARTIFACTS_DIR / "treatment_dict.json"))
    encoding = build_treatment_encoding(treatment_dict)
    encoding_path = ARTIFACTS_DIR / "treatment_encoding.json"
    with open(encoding_path, "w", encoding="utf-8") as f:
        json.dump(encoding, f, indent=2, sort_keys=True)
    logging.info(f"Treatment encoding written to {encoding_path} ({len(encoding)} classes)")

    transformer = FeatureTransformer(
        doctor_profile_path=str(ARTIFACTS_DIR / "doctor_profile.json"),
        clinic_profile_path=str(ARTIFACTS_DIR / "clinic_profile.json"),
        treatment_dict_path=str(ARTIFACTS_DIR / "treatment_dict.json"),
        treatment_encoding_path=str(encoding_path),
    )

    logging.info("Transforming train split...")
    train_features = transformer.transform(train_df)
    train_out = output_dir / "features_train.parquet"
    train_features.to_parquet(train_out, index=False)
    logging.info(f"Train features: {len(train_features):,} rows → {train_out}")

    logging.info("Transforming test split...")
    test_features = transformer.transform(test_df)
    test_out = output_dir / "features_test.parquet"
    test_features.to_parquet(test_out, index=False)
    logging.info(f"Test features:  {len(test_features):,} rows → {test_out}")

    # Feature stats sidecar (train only — becomes drift monitoring baseline)
    stats = {}
    for col in train_features.columns:
        col_stats: dict = {"null_rate": float(train_features[col].isna().mean())}
        if col == "treatment_class":
            unknown_int = encoding["UNKNOWN"]
            col_stats["unknown_rate"] = float(
                (train_features[col] == unknown_int).sum() / len(train_features)
            )
            col_stats["mean"] = float(train_features[col].mean())
        elif train_features[col].dtype == object:
            top5 = train_features[col].value_counts().head(5).to_dict()
            col_stats["top5"] = {str(k): int(v) for k, v in top5.items()}
        else:
            col_stats["mean"] = float(train_features[col].mean())
            if col == "appt_hour_bucket":
                col_stats["pct_sentinel"] = float(
                    (train_features[col] == -1).sum() / len(train_features)
                )
        stats[col] = col_stats

    stats_path = output_dir / "feature_stats.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    logging.info(f"Feature stats written to {stats_path}")

    # Duration class distribution
    dist = train_features["duration_class"].value_counts().sort_index().to_dict()
    logging.info(f"Duration class distribution (train): {dist}")
    logging.info("Done.")


if __name__ == "__main__":
    main()
