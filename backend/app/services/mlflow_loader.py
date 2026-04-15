import os
import tempfile
import mlflow
from mlflow.tracking import MlflowClient
import onnxruntime as ort

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "DentTimeModel")
MODEL_STAGE = os.getenv("MODEL_STAGE")  # Production / Staging
MODEL_VERSION = os.getenv("MODEL_VERSION")  # optional

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
client = MlflowClient()

def _resolve_model_uri():
    # เลือกใช้ stage ก่อน ถ้าไม่มีค่อยใช้ version
    if MODEL_STAGE:
        return f"models:/{MODEL_NAME}/{MODEL_STAGE}"
    elif MODEL_VERSION:
        return f"models:/{MODEL_NAME}/{MODEL_VERSION}"
    else:
        # fallback: latest version
        latest = client.get_latest_versions(MODEL_NAME)
        if not latest:
            raise RuntimeError("No model versions found")
        return f"models:/{MODEL_NAME}/{latest[0].version}"

def load_onnx_session():
    model_uri = _resolve_model_uri()

    # ดาวน์โหลด artifact จาก MLflow มาเป็นไฟล์ชั่วคราว
    local_dir = tempfile.mkdtemp()
    local_path = mlflow.artifacts.download_artifacts(
        artifact_uri=model_uri,
        dst_path=local_dir
    )

    # หาไฟล์ .onnx ภายในโฟลเดอร์
    onnx_path = None
    for root, _, files in os.walk(local_path):
        for f in files:
            if f.endswith(".onnx"):
                onnx_path = os.path.join(root, f)
                break
        if onnx_path:
            break

    if not onnx_path:
        raise RuntimeError("ONNX file not found in MLflow artifact")

    session = ort.InferenceSession(onnx_path)
    input_name = session.get_inputs()[0].name
    output_names = [o.name for o in session.get_outputs()]

    return session, input_name, output_names, model_uri

def get_model_info():
    # ดึง metadata จาก MLflow Registry
    if MODEL_STAGE:
        mv = client.get_latest_versions(MODEL_NAME, stages=[MODEL_STAGE])[0]
    elif MODEL_VERSION:
        mv = client.get_model_version(MODEL_NAME, MODEL_VERSION)
    else:
        mv = client.get_latest_versions(MODEL_NAME)[0]

    run_id = mv.run_id
    run = client.get_run(run_id)

    # metrics เช่น f1, accuracy ฯลฯ
    metrics = run.data.metrics
    params = run.data.params
    tags = run.data.tags

    return {
        "model_name": MODEL_NAME,
        "version": mv.version,
        "stage": mv.current_stage,
        "run_id": run_id,
        "metrics": metrics,   # {"f1_score": 0.87, ...}
        "params": params,
        "tags": tags,
    }