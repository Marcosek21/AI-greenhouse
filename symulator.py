import requests
import random
import time
import json

API_URL = "http://127.0.0.1:5000/api/data"  # lub IP serwera, np. http://192.168.1.41:5000/api/data

def generate_sensor_data():
    """Generuje losowe dane symulujące pomiary czujników w szklarni."""
    temperature = round(random.uniform(18.0, 28.0), 1)
    humidity = round(random.uniform(45.0, 80.0), 1)
    soil_1 = round(random.uniform(30.0, 60.0), 1)
    soil_2 = round(random.uniform(25.0, 55.0), 1)
    light = round(random.uniform(200, 800), 0)  # lux
    battery_voltage = round(random.uniform(10.8, 12.6), 2)  # napięcie akumulatora
    water_distance = round(random.uniform(5.0, 25.0), 1)  # cm – od czujnika do lustra wody

    return {
        "temperature": temperature,
        "humidity": humidity,
        "soil_1": soil_1,
        "soil_2": soil_2,
        "light": light,
        "battery_voltage": battery_voltage,
        "water_distance": water_distance
    }

def send_data():
    """Wysyła dane do API /api/data."""
    data = generate_sensor_data()
    try:
        response = requests.post(API_URL, json=data, timeout=5)
        if response.status_code == 200:
            print(f"✅ Dane wysłane: {json.dumps(data)}")
        else:
            print(f"❌ Błąd: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Brak połączenia z serwerem: {e}")

if __name__ == "__main__":
    print("🌿 Symulacja szklarni – aktywna.")
    print("Wysyłanie danych co 10 sekund... (Ctrl+C aby zakończyć)\n")
    while True:
        send_data()
        time.sleep(10)
