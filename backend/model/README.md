# Model Folder

Place the trained ONNX model file here.

Expected file:
- model.onnx

Requirements:
- Input shape: (1, N)
- Input dtype: float32
- Feature order:
  [tooth_count, is_first_case]

Note:
This model will be loaded by FastAPI backend for inference.