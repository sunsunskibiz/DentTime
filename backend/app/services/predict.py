import numpy as np
from app.services.mlflow_loader import load_onnx_session

# load at start
session, input_name, output_names, MODEL_URI = load_onnx_session()

def _preprocess(symptoms, doctors):
    """
    TODO
    """
    tooth_count = len(symptoms)
    is_first_case = 1 if len(doctors) > 0 else 0

    features = np.array([[tooth_count, is_first_case]], dtype=np.float32)
    return features

def predict(data):
    features = _preprocess(data["symptoms"], data["doctors"])

    outputs = session.run(output_names, {input_name: features})

    # สมมติ output[0] คือ label, output[1] คือ prob (ถ้ามี)
    pred = outputs[0][0]
    prob = None

    if len(outputs) > 1:
        prob = float(np.max(outputs[1]))

    return int(pred), prob