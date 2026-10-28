import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8000"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/farm_intrusion_v2")
MODEL_NAME = os.getenv("MODEL_NAME", "yolov8n.pt")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.60"))
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", "5.0"))

raw_classes = os.getenv("EXPECTED_CLASSES", "cow,sheep,horse,bird")
EXPECTED_CLASSES = {c.strip().lower() for c in raw_classes.split(",") if c.strip()}

# If CAMERA_STREAM_URL is a digit (like 0), treat it as a local webcam int.
raw_cam = os.getenv("CAMERA_STREAM_URL", "0")
CAMERA_STREAM_URL = int(raw_cam) if raw_cam.isdigit() else raw_cam
ESP32_ALARM_URL = os.getenv("ESP32_ALARM_URL", "http://192.168.1.100/alarm_on")