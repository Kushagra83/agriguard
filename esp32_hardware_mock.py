import uvicorn
from fastapi import FastAPI

app = FastAPI(title="ESP32 Alarm Simulator")

@app.get("/alarm_on")
def trigger_alarm():
    """Mimics the ESP32 physical GPIO trigger."""
    print("\n" + "🔴"*25)
    print("🚨 [HARDWARE MOCK] PHYSICAL BUZZER ACTIVATED! 🚨")
    print("🔴"*25 + "\n")
    return {"status": "Alarm activated"}

if __name__ == "__main__":
    # Runs the mock buzzer on port 8080
    uvicorn.run(app, host="127.0.0.1", port=8080)