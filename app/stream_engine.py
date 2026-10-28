import torch
import cv2
import time
import requests
import threading
from ultralytics import YOLO
from .config import CONFIDENCE_THRESHOLD, EXPECTED_CLASSES, CAMERA_STREAM_URL, ESP32_ALARM_URL, COOLDOWN_SECONDS, MODEL_NAME
from .database import trigger_async_save

latest_frame = None
frame_lock = threading.Lock()

raw_frame = None
raw_frame_lock = threading.Lock()

# Target classes to monitor from COCO:
# 0: person, 14: bird, 15: cat, 16: dog, 17: horse, 18: sheep, 19: cow, 20: elephant, 21: bear
TARGET_CLASSES = [0, 14, 15, 16, 17, 18, 19, 20, 21]

def camera_reader_thread():
    """Reads frames from camera continuously to clear OpenCV buffer and eliminate latency."""
    global raw_frame
    print(f"Connecting to camera source: {CAMERA_STREAM_URL}")
    cap = cv2.VideoCapture(CAMERA_STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    while True:
        ret, frame = cap.read()
        if ret:
            with raw_frame_lock:
                raw_frame = frame
        else:
            time.sleep(0.01)

def start_video_loop():
    global latest_frame, raw_frame
    
    # 1. Start background frame puller
    reader = threading.Thread(target=camera_reader_thread, daemon=True)
    reader.start()
    
    print("\n--- HARDWARE & MODEL CHECK ---")
    print(f"CUDA Available: {torch.cuda.is_available()}")
    
    # 2. Load Single YOLOv8 Medium model onto GPU
    model_file = MODEL_NAME if MODEL_NAME else "yolov8m.pt"
    print(f"Loading Single Model: {model_file}...")
    model = YOLO(model_file)
    model.to("cuda")
    print(f"Model running on: {model.device}")
    print("------------------------------\n")
    
    last_alarm_time = 0.0
    
    while True:
        with raw_frame_lock:
            if raw_frame is None:
                frame = None
            else:
                frame = raw_frame.copy()
                
        if frame is None:
            time.sleep(0.05)
            continue

        # Keep resolution at 640x480 for smooth real-time performance
        frame = cv2.resize(frame, (640, 480))
        clean_frame = frame.copy()

        # 3. Single-pass inference filtered only to target animals + person
        results = model.predict(frame, classes=TARGET_CLASSES, conf=CONFIDENCE_THRESHOLD, verbose=False)
        
        is_intrusion = False
        detections = []
        reasons = []

        if results and results[0].boxes:
            for box in results[0].boxes:
                conf = float(box.conf[0].item())
                cls_id = int(box.cls[0].item())
                cls_name = model.names[cls_id].lower()
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                # Differentiate styling: Blue for person, Red/Amber for animals
                if cls_name == "person":
                    box_color = (255, 0, 0) # Blue
                    label = f"PERSON {conf:.2f}"
                    is_intrusion = True
                    reasons.append(f"Human intruder detected (conf: {conf:.2f})")
                else:
                    box_color = (0, 0, 255) # Red
                    label = f"{cls_name.upper()} {conf:.2f}"
                    if cls_name not in EXPECTED_CLASSES:
                        is_intrusion = True
                        reasons.append(f"Unexpected animal '{cls_name}' (conf: {conf:.2f})")

                # Annotate frame
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                cv2.putText(frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, box_color, 2)

                detections.append({
                    "className": cls_name,
                    "confidence": round(conf, 4),
                    "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
                })

        # Update stream for dashboard
        with frame_lock:
            latest_frame = frame.copy()

        # 4. Trigger Hardware Alert & Database Save
        current_time = time.time()
        if is_intrusion and (current_time - last_alarm_time > COOLDOWN_SECONDS):
            last_alarm_time = current_time
            print(f"\n🚨 INTRUSION DETECTED: {reasons}")
            
            try:
                requests.get(ESP32_ALARM_URL, timeout=0.8)
            except Exception:
                pass
                
            success, buffer = cv2.imencode('.jpg', frame)
            if success:
                trigger_async_save(buffer.tobytes(), detections, reasons)

def get_latest_frame():
    """Safely retrieves the most recent annotated frame for the web server."""
    with frame_lock:
        if latest_frame is not None:
            return latest_frame.copy()
    return None