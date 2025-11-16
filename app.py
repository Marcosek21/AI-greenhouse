from flask import Flask, request, render_template, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import requests
import base64
import os
from dotenv import load_dotenv
import zlib
import math
import json
from datetime import datetime

load_dotenv()

UPLOAD_DIR = "uploads"
TEMP_DIR = "temp_parts"

# 🌦️ Konfiguracja API pogodowego
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
POZNAN_LAT = 52.4064
POZNAN_LON = 16.9252

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

CONFIG_FILE = "config.json"
CONTROL_FILE = "control_state.json"

# Domyślne parametry zbiornika (prostopadłościan)
DEFAULT_CONFIG = {
    "bucket_height": 45.0,   # cm
    "bucket_width": 41.0,    # cm
    "bucket_length": 27.0    # cm
}

# Domyślny stan sterowania
DEFAULT_CONTROL = {
    "mode": "auto",      # auto | manual | off
    "roof": False,
    "valve_1": False,
    "valve_2": False,
    "light": False,
    "heater": False,
    "pump": False,
    "pump_ack": False,   # potwierdzenie, że szklarnia odebrała zlecenie pompy
    "pump_work_time": 10 # sekundy pracy pompy na cykl
}

app = Flask(__name__)
CORS(app)


# =================== POMOCNICZE ===================

def calculate_battery_level(v_bat: float) -> float | None:
    """Oblicza procent naładowania baterii na podstawie napięcia."""
    if v_bat is None:
        return None
    level = (v_bat - 8.1) / 4.5 * 100.0
    return max(0.0, min(100.0, level))


def calculate_water_volume_rectangular(height_cm, width_cm, length_cm, water_distance_cm):
    """
    Oblicza objętość wody (litry) w pojemniku prostopadłościennym.
    height_cm     - wysokość zbiornika (cm)
    width_cm      - szerokość podstawy (cm)
    length_cm     - długość podstawy (cm)
    water_distance_cm - odległość czujnika od lustra wody (cm)
    """
    if None in (height_cm, width_cm, length_cm, water_distance_cm):
        return None

    # wysokość słupa wody
    h_water = max(0.0, height_cm - water_distance_cm)

    # objętość w cm^3
    volume_cm3 = width_cm * length_cm * h_water

    # litry
    return round(volume_cm3 / 1000.0, 2)


def get_data():
    conn = sqlite3.connect('czujniki.db')
    c = conn.cursor()
    c.execute("""
        SELECT temperature, humidity, water_distance, soil_1, soil_2, light, battery_voltage, timestamp
        FROM czujniki
        ORDER BY timestamp DESC
        LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    return rows


# =================== WIDOK GŁÓWNY ===================

@app.route('/')
def index():
    data = get_data()
    return render_template('index.html', data=data)


# =================== DANE Z CZUJNIKÓW ===================

@app.route('/api/data', methods=['POST'])
def receive_data():
    """Odbiera dane pomiarowe ze szklarni (bezpośrednie wartości z czujników)."""
    data = request.json or {}

    temperature = data.get('temperature')
    humidity = data.get('humidity')
    soil_1 = data.get('soil_1')
    soil_2 = data.get('soil_2')
    light = data.get('light')
    battery_voltage = data.get('battery_voltage')
    water_distance = data.get('water_distance')  # przyjmujemy, że w METRACH

    conn = sqlite3.connect('czujniki.db')
    c = conn.cursor()
    c.execute("""
        INSERT INTO czujniki (
            temperature, humidity, soil_1, soil_2, light, battery_voltage, water_distance
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        temperature, humidity, soil_1, soil_2, light, battery_voltage, water_distance
    ))
    conn.commit()
    conn.close()

    return jsonify({'status': 'ok'}), 200


@app.route('/api/latest')
def latest_data():
    """Zwraca najnowszy zapis z czujników + przeliczenia (bateria, woda)."""
    data = get_data()
    if not data:
        return jsonify({})

    row = data[0]  # ostatni pomiar
    config = load_config()

    # water_distance zapisane jest w METRACH → na potrzeby obliczeń przeliczamy na cm
    water_distance_m = row[2]
    water_distance_cm = water_distance_m * 100.0 if water_distance_m is not None else None

    # obliczenie objętości aktualnej
    water_volume = calculate_water_volume_rectangular(
        config["bucket_height"],
        config["bucket_width"],
        config["bucket_length"],
        water_distance_cm
    )

    # objętość maksymalna w litrach
    max_volume_l = round(
        (config["bucket_height"] * config["bucket_width"] * config["bucket_length"]) / 1000.0,
        2
    )
    if max_volume_l and water_volume is not None:
        water_percent = round((water_volume / max_volume_l) * 100.0, 1)
    else:
        water_percent = 0.0

    return jsonify({
        'temperature': row[0],
        'humidity': row[1],
        'water_distance': water_distance_m,              # w metrach
        'water_volume': water_volume,                    # w litrach
        'water_percent': water_percent,                  # %
        'soil_1': row[3],
        'soil_2': row[4],
        'light': row[5],
        'battery_voltage': row[6],
        'battery': calculate_battery_level(row[6]),      # % baterii
        'timestamp': row[7]
    })


@app.route('/api/table-data')
def table_data():
    """Zwraca zestaw danych do tabeli (ostatnie 20 pomiarów)."""
    rows = get_data()
    return jsonify([
        {
            'temperature': r[0],
            'humidity': r[1],
            'water_distance': r[2],                 # w metrach
            'soil_1': r[3],
            'soil_2': r[4],
            'light': r[5],
            'battery_voltage': r[6],                # napięcie
            'battery': calculate_battery_level(r[6]),
            'timestamp': r[7]
        } for r in rows
    ])


@app.route('/api/chart-data')
def chart_data():
    rows = get_data()
    rows.reverse()  # najstarsze dane najpierw
    return jsonify({
        'labels': [r[7] for r in rows],
        'temperature': [r[0] for r in rows],
        'humidity': [r[1] for r in rows]
    })


# =================== UPLOAD ZDJĘĆ ===================

@app.route('/api/upload', methods=['POST'])
def upload_image_part():
    """Odbiera fragmenty Base64, weryfikuje CRC i składa plik JPG."""
    data = request.json or {}
    filename = data.get('filename')
    part = data.get('part')
    total_parts = data.get('total_parts')
    encoded_data = data.get('data')
    crc_sent = data.get('crc32')

    if not all([filename, part, total_parts, encoded_data]) or crc_sent is None:
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400

    clean_data = str(encoded_data).strip().replace("\n", "").replace("\r", "")
    crc_calc = zlib.crc32(clean_data.encode("utf-8")) & 0xFFFFFFFF

    if crc_calc != int(crc_sent):
        print(f"❌ CRC mismatch for part {part}/{total_parts} of {filename}")
        return jsonify({
            'status': 'error',
            'part': part,
            'message': f'CRC mismatch (sent={crc_sent}, calc={crc_calc})'
        }), 400

    part_path = os.path.join(TEMP_DIR, f"{filename}.part{part}")
    with open(part_path, "wb") as f:
        f.write(clean_data.encode("utf-8"))

    print(f"📦 Received part {part}/{total_parts} for {filename} (CRC OK)")

    if int(part) == int(total_parts):
        output_file = os.path.join(UPLOAD_DIR, filename)
        with open(output_file, "wb") as out:
            for i in range(1, int(total_parts) + 1):
                part_path = os.path.join(TEMP_DIR, f"{filename}.part{i}")
                with open(part_path, "rb") as p:
                    part_data = p.read().decode("utf-8").strip()
                    missing_padding = len(part_data) % 4
                    if missing_padding:
                        part_data += "=" * (4 - missing_padding)
                    out.write(base64.b64decode(part_data))
                os.remove(part_path)
        print(f"✅ File assembled successfully: {output_file}")
        return jsonify({'status': 'done', 'file': f"/uploads/{filename}"}), 200

    return jsonify({
        'status': 'ok',
        'part': part,
        'message': 'CRC OK, part received'
    }), 200


@app.route('/uploads/<path:filename>')
def serve_uploaded_image(filename):
    """Udostępnia zdjęcia do podglądu przez przeglądarkę."""
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/api/gallery', methods=['GET'])
def gallery():
    """Zwraca listę wszystkich zdjęć w folderze uploads."""
    files = []
    for f in os.listdir(UPLOAD_DIR):
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            files.append({
                "name": f,
                "url": f"/uploads/{f}"
            })
    files.sort(key=lambda x: os.path.getmtime(os.path.join(UPLOAD_DIR, x["name"])), reverse=True)
    return jsonify(files)


# =================== POGODA ===================

@app.route('/api/weather', methods=['GET'])
def get_weather():
    """Pobiera aktualne dane pogodowe z OpenWeatherMap dla Poznania."""
    url = (
        f"https://api.openweathermap.org/data/2.5/weather?"
        f"lat={POZNAN_LAT}&lon={POZNAN_LON}&appid={OPENWEATHER_API_KEY}&units=metric&lang=pl"
    )

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        temperature = data.get("main", {}).get("temp")
        condition = data.get("weather", [{}])[0].get("main", "")
        description = data.get("weather", [{}])[0].get("description", "")
        is_raining = condition.lower() in ["rain", "drizzle", "thunderstorm", "snow"]

        now = datetime.now()
        return jsonify({
            "city": "Poznań",
            "temperature": temperature,
            "condition": condition,
            "description": description,
            "is_raining": is_raining,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(now.timestamp())
        })

    except Exception as e:
        return jsonify({"error": "Błąd pobierania danych pogodowych", "details": str(e)}), 500


# =================== KONFIGURACJA ZBIORNIKA ===================

def load_config():
    """Wczytuje konfigurację zbiornika z pliku JSON i uzupełnia brakujące pola."""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)

    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v

    return cfg


def save_config(data):
    """Zapisuje konfigurację zbiornika do pliku JSON."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


@app.route('/api/config', methods=['GET'])
def get_config():
    """Zwraca aktualną konfigurację pojemnika."""
    return jsonify(load_config())


@app.route('/api/config', methods=['POST'])
def update_config():
    """Aktualizuje konfigurację pojemnika (wysokość, szerokość, długość)."""
    data = request.json or {}
    config = load_config()

    config['bucket_height'] = float(data.get('bucket_height', config['bucket_height']))
    config['bucket_width'] = float(data.get('bucket_width', config['bucket_width']))
    config['bucket_length'] = float(data.get('bucket_length', config['bucket_length']))

    save_config(config)
    return jsonify({"status": "ok", "message": "Konfiguracja zapisana pomyślnie."})


# =================== STEROWANIE SZKLARNIĄ ===================

def load_control():
    """Wczytuje stan sterowania z pliku i uzupełnia brakujące pola."""
    if not os.path.exists(CONTROL_FILE):
        with open(CONTROL_FILE, "w") as f:
            json.dump(DEFAULT_CONTROL, f, indent=2)
        return DEFAULT_CONTROL.copy()

    with open(CONTROL_FILE, "r") as f:
        state = json.load(f)

    # Uzupełnij brakujące klucze po aktualizacjach
    for k, v in DEFAULT_CONTROL.items():
        if k not in state:
            state[k] = v

    return state


def save_control(data):
    """Zapisuje stan sterowania do pliku JSON."""
    with open(CONTROL_FILE, "w") as f:
        json.dump(data, f, indent=2)


@app.route('/api/control', methods=['GET'])
def get_control_for_web():
    """Stan sterowania dla panelu WWW (bez logiki ACK/one-shot)."""
    state = load_control()
    return jsonify(state)


@app.route('/api/control', methods=['POST'])
def update_control():
    """Aktualizuje stan sterowania z panelu WWW."""
    data = request.json or {}
    state = load_control()

    # Tryb pracy
    mode = data.get("mode")
    if mode in ["manual", "auto", "off"]:
        state["mode"] = mode

    # Proste elementy sterowania
    for key in ["roof", "valve_1", "valve_2", "light", "heater"]:
        if key in data:
            state[key] = bool(data[key])

    # Czas pracy pompy
    if "pump_work_time" in data:
        try:
            t = int(data["pump_work_time"])
            if t > 0:
                state["pump_work_time"] = t
        except (TypeError, ValueError):
            pass

    # One-shot pompy – żądanie z panelu
    if "pump" in data:
        # Pompa dostępna tylko w trybie manual i gdy co najmniej jeden zawór otwarty
        if state["mode"] == "manual" and (state["valve_1"] or state["valve_2"]):
            if bool(data["pump"]):
                state["pump"] = True
                state["pump_ack"] = False
        else:
            state["pump"] = False
            state["pump_ack"] = False

    save_control(state)
    return jsonify({"status": "ok", "message": "Stan sterowania zaktualizowany."})


@app.route('/api/control-device', methods=['GET'])
def get_control_for_device():
    """
    Stan sterowania dla szklarni (mikrokontroler).
    Pompa działa jako one-shot:
      - jeśli pump == True → ten GET zwróci pump=True, a zaraz potem zapisze pump=False, pump_ack=True
      - kolejne GET-y będą miały pump=False, dopóki panel ponownie nie zażąda pompki.
    """
    state = load_control()

    # Zabezpieczenie – pompa wyłączona, jeśli oba zawory są zamknięte
    if not state["valve_1"] and not state["valve_2"]:
        state["pump"] = False

    # Kopia stanu do odesłania
    response_state = state.copy()

    # Logika one-shot pompy
    if state["pump"] is True:
        # Urządzenie dostaje informację o włączeniu pompy w tym cyklu
        # Po wysłaniu resetujemy stan pompy i ustawiamy ACK
        state["pump"] = False
        state["pump_ack"] = True
        save_control(state)

    return jsonify(response_state)


# =================== MAIN ===================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
