from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
from flask import send_from_directory
import sqlite3
import requests
import base64
import os
from dotenv import load_dotenv
import zlib
import binascii

load_dotenv()
UPLOAD_DIR = "uploads"
TEMP_DIR = "temp_parts"

# 🌦️ Konfiguracja API pogodowego
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
POZNAN_LAT = 52.4064
POZNAN_LON = 16.9252


os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)

def get_data():
    conn = sqlite3.connect('czujniki.db')
    c = conn.cursor()
    c.execute("""
        SELECT temperature, humidity, water_level, soil_1, soil_2, light, battery, timestamp
        FROM czujniki ORDER BY timestamp DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    return rows

@app.route('/')
def index():
    data = get_data()
    return render_template('index.html', data=data)

@app.route('/api/data', methods=['POST'])
def receive_data():
    data = request.json
    conn = sqlite3.connect('czujniki.db')
    c = conn.cursor()
    c.execute("""
        INSERT INTO czujniki (
            temperature, humidity, water_level, soil_1, soil_2, light, battery
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('temperature'),
        data.get('humidity'),
        data.get('water_level'),
        data.get('soil_1'),
        data.get('soil_2'),
        data.get('light'),
        data.get('battery')
    ))
    conn.commit()
    conn.close()
    return {'status': 'ok'}, 200

@app.route('/api/latest')
def latest_data():
    row = get_data()[0] if get_data() else None
    if row:
        return jsonify({
            'temperature': row[0],
            'humidity': row[1],
            'water_level': row[2],
            'soil_1': row[3],
            'soil_2': row[4],
            'light': row[5],
            'battery': row[6],
            'timestamp': row[7]
        })
    return jsonify({})

@app.route('/api/table-data')
def table_data():
    rows = get_data()
    return jsonify([
        {
            'temperature': r[0],
            'humidity': r[1],
            'water_level': r[2],
            'soil_1': r[3],
            'soil_2': r[4],
            'light': r[5],
            'battery': r[6],
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

@app.route('/api/upload', methods=['POST'])
def upload_image_part():
    """Odbiera fragmenty Base64, weryfikuje CRC i składa plik JPG"""
    data = request.json
    filename = data.get('filename')
    part = data.get('part')
    total_parts = data.get('total_parts')
    encoded_data = data.get('data')
    crc_sent = data.get('crc32')

    if not all([filename, part, total_parts, encoded_data, crc_sent is not None]):
        return jsonify({'status': 'error', 'message': 'Missing data'}), 400

    # 🔹 Oczyszczenie danych base64
    clean_data = encoded_data.strip().replace("\n", "").replace("\r", "")

    # 🔹 Oblicz CRC32 po stronie serwera
    crc_calc = zlib.crc32(clean_data.encode("utf-8")) & 0xFFFFFFFF

    if crc_calc != int(crc_sent):
        print(f"❌ CRC mismatch for part {part}/{total_parts} of {filename}")
        return jsonify({
            'status': 'error',
            'part': part,
            'message': f'CRC mismatch (sent={crc_sent}, calc={crc_calc})'
        }), 400

    # 🔹 Zapisz część jeśli CRC OK
    part_path = os.path.join(TEMP_DIR, f"{filename}.part{part}")
    with open(part_path, "wb") as f:
        f.write(clean_data.encode("utf-8"))

    print(f"📦 Received part {part}/{total_parts} for {filename} (CRC OK)")

    # 🔹 Jeśli to ostatni fragment — scal plik
    if int(part) == int(total_parts):
        output_file = os.path.join(UPLOAD_DIR, filename)
        with open(output_file, "wb") as out:
            for i in range(1, total_parts + 1):
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

    # 🔹 W przeciwnym razie tylko potwierdź odbiór fragmentu
    return jsonify({
        'status': 'ok',
        'part': part,
        'message': 'CRC OK, part received'
    }), 200


@app.route('/uploads/<path:filename>')
def serve_uploaded_image(filename):
    """Udostępnia zdjęcia do podglądu przez przeglądarkę"""
    return send_from_directory(UPLOAD_DIR, filename)

@app.route('/api/gallery', methods=['GET'])
def gallery():
    """Zwraca listę wszystkich zdjęć w folderze uploads"""
    files = []
    for f in os.listdir("uploads"):
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            files.append({
                "name": f,
                "url": f"/uploads/{f}"
            })
    files.sort(key=lambda x: os.path.getmtime(os.path.join("uploads", x["name"])), reverse=True)
    return jsonify(files)

from datetime import datetime

@app.route('/api/weather', methods=['GET'])
def get_weather():
    """Pobiera aktualne dane pogodowe z OpenWeatherMap dla Poznania"""
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

