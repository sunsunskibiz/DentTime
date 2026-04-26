# DentTime — Integrated ML System (Merge-Fixed)

รีโปนี้คือเวอร์ชันที่จัดโครงสร้างใหม่ให้ **เหลือแหล่งรันจริงเพียงชุดเดียว** สำหรับงาน DentTime หลังจาก merge โค้ดจากหลาย pipeline เข้าด้วยกัน

## โครงสร้างที่ใช้รันจริง

- `app/` — FastAPI inference + `/predict` + `/actual` + `/metrics`
- `frontend/` — React/Vite UI
- `monitoring/` — state + metric computation
- `prometheus/` — scrape config + alert rules
- `grafana/` — dashboard provisioning
- `src/features/` — feature-engineering code ที่ใช้จริงตอน inference
- `src/features/artifacts/` — runtime JSON artifacts จาก FE handoff
- `artifacts/` — model + baseline metrics + smoke inputs
- `data/` — SQLite runtime DB + reference data for drift monitoring

## โฟลเดอร์ที่เก็บไว้เพื่ออ้างอิง/พัฒนาต่อ

- `airflow/` — P1/P2 pipeline orchestration
- `data_collection/` — ingestion/validation handoff
- `Trianing/` — training notebook / scripts เดิม
- `docs/`, `c4/`, `tests/` — เอกสาร + diagram + unit tests

> หมายเหตุ: โครงสร้างเก่าอย่าง `backend/`, `docker/`, `Monitoring-Alerting/` **ไม่นำมาใช้เป็น entrypoint หลักอีกแล้ว** เพื่อกันสับสนเวลา merge รอบถัดไป

---

## Quick Start (แนะนำที่สุด)

### หลัง clone ใหม่หรือเปิดคอมใหม่

```bash
cd DentTime-main-merge-fixed

docker compose up --build -d
```

เปิดใช้งานที่

- Frontend: `http://localhost:5173`
- FastAPI Docs: `http://localhost:8000/docs`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

หยุดระบบ

```bash
docker compose down
```

---

## คำสั่งตรวจว่าระบบพร้อม

```bash
docker compose ps
docker compose logs frontend --tail=100
docker compose exec api python smoke_test_integration.py
docker compose exec api python monitoring/update_metrics.py
```

ถ้าทุกอย่างปกติจะเห็น

- `/predict` ตอบ `success`
- `/actual` ตอบ `logged`
- smoke test ลงท้ายด้วย `metrics: ok`
- Prometheus target เป็น `UP`
- Grafana dashboard มีข้อมูล ไม่ใช่ `No data`

---

## ถ้า clone ไปอีกโฟลเดอร์หนึ่งใหม่ ต้องติดตั้งอะไรใหม่บ้าง

ถ้าใช้ **Docker Compose**:
- ไม่ต้อง `pip install` ทีละแพ็กเกจเอง
- ไม่ต้อง `npm install` เอง
- รันแค่

```bash
docker compose up --build -d
```

เพราะ Docker จะสร้าง environment ใหม่ทั้งหมดให้อัตโนมัติ

ถ้าอยากรันแบบ local manual:

### Backend
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev -- --host
```

---

## จุดสำคัญของการ merge fix รอบนี้

1. ใช้ **FastAPI app ตัวเดียว** ที่ `app/main.py`
2. ใช้ **frontend ตัวเดียว** ที่ `frontend/`
3. ใช้ **docker-compose ตัวเดียว** ที่ root
4. ใช้ artifact จริงจาก FE handoff ที่ `src/features/artifacts/*.json`
5. ใช้ model จริง + baseline จริงจาก `artifacts/`
6. มี smoke test แบบ end-to-end: `predict -> actual -> metrics`

---

## ทดสอบ Monitoring

1. เปิด `http://localhost:5173` แล้วกด predict 2–3 ครั้ง
2. รัน

```bash
docker compose exec api python monitoring/update_metrics.py
```

3. เปิดดู
- Prometheus: query เช่น `denttime_logged_predictions_total`, `denttime_feature_psi`, `denttime_macro_f1`
- Grafana: DentTime Monitoring Dashboard

---

## ข้อควรระวัง

- warning เรื่อง `model_version` ของ Pydantic เป็น warning ไม่ใช่ fatal error
- warning เรื่อง XGBoost / sklearn version mismatch หมายถึง model ถูกสร้างจากคนละเวอร์ชัน แต่ระบบยังรันได้ ถ้าจะ production-hardening ค่อย re-export model จากเวอร์ชันปัจจุบัน
- ถ้า Grafana ขึ้น `No data` ให้รัน smoke test ก่อน แล้วตามด้วย `docker compose exec api python monitoring/update_metrics.py`

---

## แนวทางพัฒนาต่อโดยไม่พังอีก

- เพิ่ม endpoint ใหม่ใน `app/main.py` หรือแยก router ใต้ `app/`
- แก้ feature engineering ใต้ `src/features/` เท่านั้น
- แก้ monitoring logic ที่ `monitoring/update_metrics.py`
- ห้ามสร้าง compose ใหม่ซ้ำซ้อน ถ้าไม่จำเป็น
- ถ้าจะเพิ่ม pipeline ใหม่ ให้เสียบผ่านโครงสร้างนี้แทนการสร้าง subtree ใหม่
