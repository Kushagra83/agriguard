import os
import threading
from datetime import datetime
from pymongo import MongoClient
from .config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client.get_default_database()
events_collection = db["DetectionEvent"]

# Ensure uploads directory exists
os.makedirs("uploads", exist_ok=True)

def save_event_background(frame_bytes: bytes, detections: list, intrusion_reasons: list):
    """Saves the image to disk and inserts the event into MongoDB asynchronously."""
    timestamp = datetime.now()
    filename = f"uploads/{timestamp.timestamp()}.jpg"
    
    with open(filename, "wb") as f:
        f.write(frame_bytes)
        
    events_collection.insert_one({
        "imagePath": filename,
        "capturedAt": timestamp,
        "detections": detections,
        "isIntrusion": True,
        "intrusionReasons": intrusion_reasons
    })
    print(f"\n[Storage] Saved event async: {filename}")

def trigger_async_save(frame_bytes, detections, intrusion_reasons):
    threading.Thread(
        target=save_event_background, 
        args=(frame_bytes, detections, intrusion_reasons),
        daemon=True
    ).start()