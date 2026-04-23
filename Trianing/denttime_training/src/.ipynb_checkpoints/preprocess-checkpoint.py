import pandas as pd
import numpy as np

# === Constants ===
COMPLEX_KEYWORDS = [
    'root', 'endo', 'รากฟัน', 'crown', 'ครอบ', 'bridge', 'สะพาน',
    'implant', 'ฝัง', 'extract', 'ถอน', 'ผ่าตัด', 'surgery'
]

FEATURE_COLS = [
    'treatment_count',
    'has_complex_treatment',
    'tooth_count',
    'time_of_day_enc',
    'is_first_case',
    'branch_median_duration',
    'scheduled_duration',
    'total_amount',
    'has_notes',
    'day_of_week'
]

DURATION_CLASSES = [15, 30, 45, 60, 75, 90, 105]


def load_and_merge(file_paths: list) -> pd.DataFrame:
    """โหลดและรวมหลายไฟล์ CSV เป็น DataFrame เดียว"""
    dfs = [pd.read_csv(f) for f in file_paths]
    df = pd.concat(dfs, ignore_index=True)
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """กรองข้อมูลที่ไม่สมบูรณ์และ duration ผิดปกติออก"""
    df = df.dropna(subset=['receipt_time', 'check_in_time', 'treatment'])

    df['receipt_time']        = pd.to_datetime(df['receipt_time'])
    df['check_in_time']       = pd.to_datetime(df['check_in_time'])
    df['appointment_start']   = pd.to_datetime(df['appointment_start'])
    df['appointment_end']     = pd.to_datetime(df['appointment_end'])

    df['actual_duration_min'] = (
        df['receipt_time'] - df['check_in_time']
    ).dt.total_seconds() / 60

    df = df[
        (df['actual_duration_min'] >= 10) &
        (df['actual_duration_min'] <= 150)
    ]

    return df.reset_index(drop=True)


def assign_duration_class(minutes: float) -> int:
    """แปลง duration จริง (นาที) เป็น class label"""
    if minutes <= 22:    return 15
    elif minutes <= 37:  return 30
    elif minutes <= 52:  return 45
    elif minutes <= 67:  return 60
    elif minutes <= 82:  return 75
    elif minutes <= 97:  return 90
    else:                return 105


def has_complex(text: str) -> int:
    """ตรวจว่ามีหัตถการซับซ้อนหรือไม่"""
    if pd.isna(text):
        return 0
    text_lower = str(text).lower()
    return int(any(kw in text_lower for kw in COMPLEX_KEYWORDS))


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """สร้าง features ทั้งหมดจาก raw data"""
    df = df.copy()

    # Treatment features
    df['treatment_count']       = df['treatment'].apply(
        lambda x: len(str(x).split(',')) if pd.notna(x) else 1
    )
    df['has_complex_treatment'] = df['treatment'].apply(has_complex)

    # Tooth count
    df['tooth_count'] = df['tooth_no'].apply(
        lambda x: len(str(x).split(',')) if pd.notna(x) else 1
    )

    # Time features
    df['hour']           = df['check_in_time'].dt.hour
    df['time_of_day_enc'] = df['hour'].apply(
        lambda h: 0 if 6 <= h < 12 else (1 if 12 <= h < 17 else 2)
    )
    df['day_of_week'] = df['check_in_time'].dt.dayofweek

    # Is first case
    df['date'] = df['check_in_time'].dt.date
    df = df.sort_values(['branch_id', 'check_in_time'])
    df['is_first_case'] = (
        df.groupby(['branch_id', 'date']).cumcount() == 0
    ).astype(int)

    # Branch median duration
    branch_stats   = df.groupby('branch_id')['actual_duration_min'].median()
    global_median  = df['actual_duration_min'].median()
    df['branch_median_duration'] = df['branch_id'].map(branch_stats).fillna(global_median)

    # Scheduled duration
    df['scheduled_duration'] = (
        df['appointment_end'] - df['appointment_start']
    ).dt.total_seconds() / 60

    # Amount and notes
    df['total_amount'] = pd.to_numeric(df['total_amount'], errors='coerce').fillna(0)
    df['has_notes']    = df['notes'].notna().astype(int)

    # Duration class label
    df['duration_class'] = df['actual_duration_min'].apply(assign_duration_class)

    # กรอง scheduled_duration ผิดปกติ
    df = df[
        (df['scheduled_duration'] >= 5) &
        (df['scheduled_duration'] <= 180)
    ]

    return df.reset_index(drop=True)