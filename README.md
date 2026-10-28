# 🚜 AgriGuard V2 — Team Onboarding & Architecture Guide

Welcome to **AgriGuard V2**! If you are just joining the team, this document is your complete guide to understanding what we are building, how the codebase is wired together under the hood, why we made specific engineering decisions, and what tasks are currently up for grabs.

---

## 🎯 1. What Are We Building? (The 30-Second Pitch)

Farms face constant threats from both human intruders and stray/wild animals destroying crops. Traditional CCTV cameras just passively record footage—nobody is staring at a screen 24/7.

**AgriGuard V2** turns a standard camera feed into an **active, intelligent perimeter defense system**:
1. **Watches:** Pulls a live video stream from a smartphone (using the *IP Webcam* app) mounted on the farm.
2. **Thinks:** Runs real-time AI object detection (**YOLOv8**) on our laptop's NVIDIA GPU to identify what is in the frame.
3. **Decides:** Distinguishes between **safe farm livestock** (e.g., our own cows and sheep) vs. **intruders** (humans or unexpected animals like dogs, bears, or elephants).
4. **Acts:** The millisecond an intruder is spotted, the backend fires an HTTP signal over Wi-Fi to an **ESP32 microcontroller** to sound a physical alarm buzzer, while simultaneously saving a snapshot and logging the event to our **MongoDB** dashboard.

---

## 🧠 2. How a Single Video Frame Travels Through Our System

When you look at our code, everything revolves around **speed** and **non-blocking execution**. If our AI takes 500ms to save a file to the hard drive, the live video stream shouldn't freeze for half a second. 

To solve this, we built a **3-Thread Producer-Consumer Architecture**:

```text
  [📱 Smartphone IP Webcam]
              │
              ▼  (Wi-Fi MJPEG Stream)
┌─────────────────────────────────────────────────────────────────────────┐
│ THREAD 1: The Fast Camera Reader (stream_engine.py -> camera_reader)    │
│ • Problem: OpenCV buffers frames. If YOLO is slightly slower than the   │
│   camera FPS, the video feed lags behind real life by 5–10 seconds.     │
│ • Solution: This thread does NOTHING except constantly overwrite a      │
│   single variable (`raw_frame`) with the newest frame from the camera.  │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ (Protected by `raw_frame_lock`)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ THREAD 2: The AI Brain & Decision Loop (stream_engine.py -> start_loop) │
│ 1. Grabs the freshest `raw_frame` (skips any old buffered frames).      │
│ 2. Resizes to 640x480 and runs `yolov8m.pt` on the NVIDIA GPU (CUDA).   │
│ 3. Checks rules:                                                        │
│    - Is it a `person`? -> 🚨 INTRUSION! (Draws BLUE box)                │
│    - Is it an animal NOT in `EXPECTED_CLASSES`? -> 🚨 INTRUSION! (RED)  │
│ 4. Updates `latest_frame` for the web dashboard to display live.        │
└──────────┬───────────────────────────────────────────┬──────────────────┘
           │                                           │
           │ (If Intrusion + Cooldown Expired)         │ (Protected by `frame_lock`)
           ▼                                           ▼
┌──────────────────────────────────────┐    ┌─────────────────────────────┐
│ THREAD 3: Async Storage & IoT        │    │ MAIN THREAD: FastAPI Server │
│ • Fires HTTP GET to ESP32 Buzzer     │    │ • Streams `latest_frame` to │
│ • Spins up a background thread       │    │   browser (`/api/video_feed`)│
│   (`database.py`) to save the .jpg   │    │ • Serves `index.html` &     │
│   to `/uploads` and insert JSON      │    │   history (`/api/detections`)│
│   into MongoDB without lagging YOLO! │    └─────────────────────────────┘
└──────────────────────────────────────┘
```

---

## 📂 3. Codebase Tour: Which File Does What?

If you want to jump into the code, we only have **4 core Python files** and **1 HTML frontend**. Here is your map:

### 1. `config.py` — *The Control Knobs*
* **What it does:** Reads settings from our `.env` file and converts them into clean Python constants.
* **Key logic:**
  * Parses `EXPECTED_CLASSES="cow,sheep,horse,bird"` into a Python `set` so lookups take $O(1)$ time.
  * Smart-casts `CAMERA_STREAM_URL`: If you set it to `"0"`, it converts it to an integer `0` (so you can test using your own laptop's built-in webcam!). If you pass an `"http://..."` string, it connects to the smartphone IP camera.

### 2. `stream_engine.py` — *The AI & Threading Engine*
* **What it does:** This is the heart of the project. It manages the camera connection, runs the YOLOv8 model on the GPU, draws bounding boxes, and triggers alarms.
* **Why we use `yolov8m.pt` (COCO Pre-trained):**
  * *Backstory for new teammates:* We initially experimented with training a custom YOLO model on Colab (`best.pt`) and running dual models concurrently. However, because our custom dataset had zero "empty background" images, the custom model suffered from severe hallucinations (misidentifying empty grass and twigs as eagles/birds).
  * *The Pivot:* We switched to a **single pre-trained `yolov8m.pt` model** trained on the official COCO dataset. We filter it strictly to classes `[0, 14, 15, 16, 17, 18, 19, 20, 21]` (Humans + Birds, Cats, Dogs, Horses, Sheep, Cows, Elephants, Bears). This completely eliminated false-positive ghost detections and cut our GPU VRAM usage in half (~1.5GB on a 4GB GPU).

### 3. `database.py` — *The Non-Blocking Vault*
* **What it does:** Connects to our local MongoDB (`farm_intrusion_v2` database, `DetectionEvent` collection) and saves intrusion snapshots.
* **Key function:** `trigger_async_save(frame_bytes, detections, intrusion_reasons)`
  * Whenever an intrusion happens, `stream_engine.py` encodes the annotated frame (with the bounding boxes drawn on it) into JPEG bytes and hands it to `trigger_async_save()`.
  * This function spawns a separate daemon `threading.Thread` that writes `/uploads/<timestamp>.jpg` to disk and inserts the document into MongoDB in the background.

### 4. `main.py` — *The Web Server & API*
* **What it does:** Runs the FastAPI server that bridges the Python backend with our web dashboard.
* **Key mechanisms:**
  * `@asynccontextmanager def lifespan(app)`: Automatically launches `start_video_loop()` in a background thread the moment FastAPI boots up.
  * `app.mount("/uploads", ...)`: Exposes the `/uploads` folder so the frontend can display saved intrusion snapshots as `<img>` tags.
  * `generate_mjpeg()`: An infinite generator that pulls `get_latest_frame()`, encodes it as a JPEG, and yields it as a `multipart/x-mixed-replace` stream. If the camera isn't connected yet, it generates a black frame with `"Waiting for camera stream..."` so the browser never crashes.

### 5. `index.html` — *The Command Center Dashboard*
* **What it does:** Displays the live annotated video feed on one side and a real-time log of intrusion cards (with snapshot previews, timestamps, detected classes, and confidence scores) on the other.

---

## 🛠️ 4. How to Set This Up on Your Laptop (5 Minutes)

Want to run the project right now using your laptop's webcam? Follow these steps:

### Step 1: Clone & Install Dependencies
Make sure you have **Python 3.10+** and **MongoDB Community Server** running locally on port `27017`.
```bash
pip install -r requirements.txt
```
*(Note: If you have an NVIDIA GPU, make sure you have the CUDA version of PyTorch installed so `torch.cuda.is_available()` is `True`. If you don't have a dedicated GPU, change `model.to("cuda")` to `model.to("cpu")` in `stream_engine.py` for local testing).*

### Step 2: Create a `.env` File in the Root Folder
For testing at your desk without the phone camera or ESP32, paste this into `.env`:
```ini
PORT=8000
MONGO_URI=mongodb://127.0.0.1:27017/farm_intrusion_v2
MODEL_NAME=yolov8m.pt
CONFIDENCE_THRESHOLD=0.60
COOLDOWN_SECONDS=5.0

# Animals allowed on the farm (won't trigger the alarm)
EXPECTED_CLASSES=cow,sheep,horse,bird

# Set to 0 to use your laptop webcam!
CAMERA_STREAM_URL=0

# Dummy IP if ESP32 isn't plugged in yet (our try/except block safely ignores timeouts)
ESP32_ALARM_URL=http://192.168.1.100/alarm_on
```

### Step 3: Boot the Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **`http://localhost:8000`** in your browser. Wave your hand in front of your webcam—you should see a **Blue `PERSON` box** around you, a `🚨 INTRUSION DETECTED` alert in your terminal, and a new snapshot logged in the dashboard!

---

## 📊 5. Current Project Status & What's Left To Do

Here is where we stand today and how we are dividing the remaining work:

### ✅ What is 100% Built & Working
* **[Done] Multi-Threaded Video Engine:** Zero-lag frame grabbing (`camera_reader_thread`) + thread-safe locking (`raw_frame_lock`, `frame_lock`).
* **[Done] Single-Model COCO AI Pipeline:** Migrated to `yolov8m.pt` on CUDA with class filtering (`TARGET_CLASSES`) and custom allowlist evaluation (`EXPECTED_CLASSES`). Zero ghost hallucinations.
* **[Done] Annotated Snapshot Previews:** Intrusion images saved to `/uploads` now include the rendered bounding boxes and confidence labels directly on the image.
* **[Done] Asynchronous MongoDB Logging:** Background disk + DB writes (`trigger_async_save`) and REST endpoints (`GET /api/detections`, `DELETE /api/detections`).
* **[Done] Fault-Tolerant Hardware Cooldown:** Cooldown timer (`COOLDOWN_SECONDS`) + `0.8s` timeout on `requests.get(ESP32_ALARM_URL)` so the server runs smoothly even when the ESP32 is unplugged.

---

### 🚧 What's Left (Up for Grabs by the Team!)

#### 1. 🔌 Hardware Assembly (Once Robu Order Arrives)
We are ordering a **Pre-Soldered ESP32 Development Board (Type-C)**, a **PCB-Mounted Active Buzzer Module**, **20cm Female-to-Female DuPont Jumper Wires**, and a **USB Type-C Data Cable**.
* **No breadboard or soldering needed:** Simply peel 3 Female-to-Female jumper wires and connect the Buzzer directly to the ESP32 header pins:
  * Buzzer `VCC (+)` $
ightarrow$ ESP32 `5V / VIN` (or `3V3`)
  * Buzzer `GND (-)` $
ightarrow$ ESP32 `GND`
  * Buzzer `I/O (S)` $
ightarrow$ ESP32 `GPIO 18` (or any digital output pin)

#### 2. 💻 ESP32 Firmware (`alarm_server.ino` — *Needs to be flashed*)
We need to flash a lightweight C++ sketch using the **Arduino IDE** onto the ESP32. All the code needs to do is:
1. Connect to the same Wi-Fi hotspot as the laptop and smartphone.
2. Print its local IP address to the Serial Monitor (so we can put it into `.env` under `ESP32_ALARM_URL`).
3. Listen for `GET /alarm_on`, pull `GPIO 18` `HIGH` for 2–3 seconds to beep the buzzer, and return `200 OK`.

#### 3. 📱 Live Field Integration & Testing
* Install the **IP Webcam** Android app on the test phone.
* Connect the laptop, phone, and ESP32 to the same mobile hotspot.
* Update `CAMERA_STREAM_URL` and `ESP32_ALARM_URL` in `.env` with the real local IPs.
* Test end-to-end latency from someone stepping into the camera frame to the physical buzzer beeping.

#### 4. 🎨 Frontend / Dashboard Polish (Optional UI Enhancements)
* Add a manual **"Test Alarm"** button on the web dashboard to ping the ESP32 directly from the browser.
* Add an audio alert toggle in the browser dashboard alongside the physical ESP32 buzzer.
* Add filter tabs on the dashboard to filter history by `"Human Intrusions"` vs. `"Animal Intrusions"`.
