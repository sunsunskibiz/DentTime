## Backend/Frontend Overview
```bash
DentTime/
├── backend/
│   └── app/
│       ├── main.py                   # FastAPI application entrypoint (lifespan & router registration)
│       ├── routers/                 # API endpoints (prediction, options, etc.)
│       └── services/
│           └── model_loader.py      # Utility for loading model artifacts at startup
│
├── frontend/                       # Vite-based frontend application
│   ├── public/                      # Static assets served as-is
│   ├── src/
│   │   ├── assets/                  # Images and static resources
│   │   ├── components/             # Reusable UI components (used across pages)
│   │   ├── pages/                  # Page-level views (Login, Prediction, etc.)
│   │   ├── App.tsx                 # Root React component
│   │   ├── main.tsx                # Application entry point
│   │
│   ├── index.html                  # Vite HTML template
│   ├── package.json                # Frontend dependencies and scripts
│   └── README.md
│
├── src/
│   └── features/
│       ├── feature_transformer.py   # Feature engineering pipeline for model input
│       └── artifacts/               # Precomputed artifacts used during inference
│           ├── doctor_profile.json
│           ├── clinic_profile.json
│           └── treatment_encoding.json
│
├── artifacts/
│   └── model.joblib                # Serialized model bundle (model + metadata)
│
├── docker/
│   ├── Dockerfile.backend          # Backend service container definition
│   └── compose/
│       └── frontend-backend.yml    # Docker Compose setup for full-stack services
│
└── README.md
```
## Run the Project
```bash
docker compose -f docker/compose/frontend-backend.yml up --build
```
## Stop the Project
```bash
docker compose -f docker/compose/frontend-backend.yml down
```
### Test Backend with fastapi
http://localhost:8000/docs

### Test Frontend
http://localhost:5173
