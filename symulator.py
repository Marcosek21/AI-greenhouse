import requests
import random
import time

URL = 'http://localhost:5000/api/data'  # Zmień na IP serwera, jeśli testujesz zdalnie

def losowe_dane():
    return {
        "temperature": round(random.uniform(18.0, 35.0), 1),     # °C
        "humidity": round(random.uniform(30.0, 90.0), 1),        # %
        "water_level": round(random.uniform(0.0, 100.0), 1),     # %
        "soil_1": round(random.uniform(20.0, 60.0), 2),          # %
        "soil_2": round(random.uniform(20.0, 60.0), 2),          # %
        "light": round(random.uniform(1000, 50000), 2),          # lux
        "battery": round(random.uniform(0.0, 100.0), 2)          # %
    }

while True:
    dane = losowe_dane()
    try:
        response = requests.post(URL, json=dane)
        print(f"Wysłano: {dane} → Status: {response.status_code}")
    except Exception as e:
        print(f"Błąd wysyłania danych: {e}")
    time.sleep(5)  # co 5 sekund

