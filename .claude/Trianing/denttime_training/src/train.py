import joblib
import numpy as np
from xgboost import XGBClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_sample_weight

from preprocess import FEATURE_COLS, DURATION_CLASSES


def split_data(df):
    """แบ่งข้อมูลแบบ Chronological Train/Val/Test"""
    df = df.copy()
    df['month'] = df['check_in_time'].dt.month
    df['day']   = df['check_in_time'].dt.day

    train_df = df[df['month'] <= 3]
    val_df   = df[(df['month'] == 4) & (df['day'] <= 15)]
    test_df  = df[(df['month'] == 4) & (df['day'] > 15)]

    print(f"Train : {len(train_df):,} แถว")
    print(f"Val   : {len(val_df):,} แถว")
    print(f"Test  : {len(test_df):,} แถว")

    return train_df, val_df, test_df


def build_label_encoder():
    """สร้าง LabelEncoder สำหรับ duration class"""
    le = LabelEncoder()
    le.fit(DURATION_CLASSES)
    return le


def train_model(X_train, y_train_enc, X_val, y_val_enc):
    """Train XGBoost model"""
    model = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        eval_metric='mlogloss',
        random_state=42,
        verbosity=0,
        n_jobs=-1
    )

    model.fit(
        X_train, y_train_enc,
        eval_set=[(X_val, y_val_enc)],
        verbose=50
    )

    return model


def save_model(model, le, output_path='outputs/model.joblib'):
    """Save model bundle ให้ Phu โหลดใช้ได้เลย"""
    model_bundle = {
        "model":          model,
        "label_encoder":  le,
        "feature_cols":   FEATURE_COLS,
        "index_to_class": {int(i): int(c) for i, c in enumerate(le.classes_)}
    }
    joblib.dump(model_bundle, output_path)
    print(f"✅ Saved: {output_path}")
    return model_bundle