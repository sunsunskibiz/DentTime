# Handoff Document — P3 Model Training & Evaluation
**DentTime Project | วันที่: 19 เมษายน 2025**

---

## 1. สรุปงานที่ทำ

รับผิดชอบส่วน Model Training และ Model Evaluation ของ DentTime
ครอบคลุมตั้งแต่การโหลดข้อมูลจริงจากคลินิก ทำความสะอาดข้อมูล
สร้าง features ฝึกโมเดล XGBoost และประเมินผล

---

## 2. ข้อมูลที่ใช้

| รายการ | รายละเอียด |
|---|---|
| แหล่งข้อมูล | ไฟล์ CSV จากคลินิกจริง 3 ไฟล์ |
| จำนวนข้อมูลรวม | 424,053 แถว |
| หลังทำความสะอาด | 331,347 แถว |
| ช่วงเวลา | มกราคม — เมษายน 2025 |

---

## 3. การแบ่งข้อมูล

| Set | ช่วงเวลา | จำนวน |
|---|---|---|
| Train | มกราคม — มีนาคม 2025 | 218,006 แถว |
| Val | 1—15 เมษายน 2025 | 49,773 แถว |
| Test | 16—30 เมษายน 2025 | 63,553 แถว |

---

## 4. Features ที่ใช้จริง

```python
FEATURE_COLS = [
    'treatment_count',        # จำนวนหัตถการ
    'has_complex_treatment',  # มีหัตถการซับซ้อนหรือไม่ (0/1)
    'tooth_count',            # จำนวนซี่ฟัน
    'time_of_day_enc',        # 0=เช้า, 1=บ่าย, 2=เย็น
    'is_first_case',          # เคสแรกของวันหรือไม่ (0/1)
    'branch_median_duration', # median duration ของสาขานั้น
    'scheduled_duration',     # เวลานัดที่จองไว้ (นาที)
    'total_amount',           # ราคาค่ารักษา
    'has_notes',              # มี notes หรือไม่ (0/1)
    'day_of_week'             # วันในสัปดาห์ (0=จันทร์, 6=อาทิตย์)
]
```

---

## 5. โมเดลที่ใช้

```python
XGBClassifier(
    n_estimators=300,
    max_depth=6,
    learning_rate=0.05,
    eval_metric='mlogloss',
    random_state=42
)
```

---

## 6. Class Labels

| Index | Duration |
|---|---|
| 0 | 15 นาที |
| 1 | 30 นาที |
| 2 | 45 นาที |
| 3 | 60 นาที |
| 4 | 75 นาที |
| 5 | 90 นาที |
| 6 | 105 นาที |

---

## 7. Baseline Metrics (Test Set)

| Metric | ค่า |
|---|---|
| Macro F1-score | 0.2296 |
| MAE | 19.67 นาที |
| Under-estimation Rate | 0.4520 |

### Degradation Thresholds (สำหรับ Phu)
- Macro F1 ลดลง > 0.05 → trigger retrain
- Under-estimation Rate > 0.55 → alert ทันที
- MAE เพิ่มขึ้น > 10 นาที → investigate

---

## 8. ไฟล์ที่ส่งให้ทีม

| ไฟล์ | รายละเอียด | ส่งให้ |
|---|---|---|
| `model.joblib` | Full model bundle | Phu (P5) |
| `baseline_metrics.json` | Metrics สำหรับ monitoring | Phu (P5) |
| `feature_columns.json` | รายชื่อ features | Phu (P5) / Sun (P2) |
| `reference_features.parquet` | Train set สำหรับ drift | Phu (P5) / Sun (P2) |
| `smoke_test_inputs.json` | 5 เคสสำหรับ smoke test | Phu (P5) |

---

## 9. วิธีโหลด model.joblib

```python
import joblib

bundle = joblib.load('outputs/model.joblib')
model  = bundle['model']
le     = bundle['label_encoder']
feats  = bundle['feature_cols']
mapping = bundle['index_to_class']
# mapping = {0:15, 1:30, 2:45, 3:60, 4:75, 5:90, 6:105}

# predict
pred_index = model.predict(X)
pred_minutes = le.inverse_transform(pred_index)
```

---

## 10. หมายเหตุ

- `license_no` ในข้อมูลจริงมี NaN มากกว่า 50% จึงใช้ `branch_id` แทน
- Baseline drift ให้เทียบกับ `reference_features.parquet` (Train set)
- โมเดลนี้เป็น PoC — F1 = 0.23 เหมาะสำหรับการทดสอบระบบ
- เมื่อมีข้อมูลเพิ่มขึ้น (12 เดือน) ควร retrain ใหม่