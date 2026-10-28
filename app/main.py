import numpy as np
import threading
import cv2
import time
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Import the getter function to fix the blank video feed
from .stream_engine import start_video_loop, get_latest_frame
from .database import events_collection

# Ensure the uploads directory exists
os.makedirs("uploads", exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the continuous video stream in a background thread
    video_thread = threading.Thread(target=start_video_loop, daemon=True)
    video_thread.start()
    yield

# 1. Initialize the FastAPI app FIRST
app = FastAPI(title="AgriGuard V2", lifespan=lifespan)

# 2. THEN mount the static folder so the frontend can load image previews
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def generate_mjpeg():
    """Yields JPEG frames to the browser, fetching dynamically to avoid stale state."""
    while True:
        # Use the getter function to grab the latest frame safely
        frame = get_latest_frame()
        
        # If the camera hasn't captured anything yet, generate a placeholder frame
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "Waiting for camera stream...", (50, 240), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            time.sleep(0.1) # Don't spin too fast on empty frames
            
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            time.sleep(0.1)
            continue
            
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        
        # Cap stream framerate slightly to keep server responsive
        # time.sleep(0.03)

@app.get("/api/video_feed")
def video_feed():
    """Streams the live annotated video to the frontend dashboard."""
    return StreamingResponse(generate_mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/detections")
def get_detections(limit: int = 20):
    """Fetches the latest detection events for the frontend."""
    events = list(events_collection.find().sort("capturedAt", -1).limit(limit))
    for event in events:
        event["_id"] = str(event["_id"])
    return {"count": len(events), "events": events}

@app.delete("/api/detections")
def clear_detections():
    """Clears the history database."""
    events_collection.delete_many({})
    return {"status": "history cleared"}

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serves the main HTML interface."""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()