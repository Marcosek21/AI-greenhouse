from flask import Flask, request, render_template, jsonify
from flask_cors import CORS
from flask import send_from_directory
import sqlite3
import requests
import base64
import os

UPLOAD_DIR = "uploads"
TEMP_DIR = "temp_parts"

# 🌦️ Konfiguracja API pogodowego
OPENWEATHER_API_KEY = "e9d7a75939071d2b14dc0a0c1e3c61b4"
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
    """Odbiera fragmenty Base64, składa i zapisuje plik JPG"""
    data = request.json
    filename = data.get('filename')
    part = data.get('part')
    total_parts = data.get('total_parts')
    encoded_data = data.get('data')

    if not all([filename, part, total_parts, encoded_data]):
        return jsonify({'error': 'Missing data'}), 400

    # Zapisz fragment do pliku tymczasowego
    part_path = os.path.join(TEMP_DIR, f"{filename}.part{part}")
    with open(part_path, "wb") as f:
        f.write(encoded_data.encode())

    print(f"📦 Otrzymano część {part}/{total_parts} pliku {filename}")

    # Jeśli to ostatni fragment → scal wszystko
    if int(part) == int(total_parts):
        output_file = os.path.join(UPLOAD_DIR, filename)
        with open(output_file, "wb") as out:
            for i in range(1, total_parts + 1):
                part_path = os.path.join(TEMP_DIR, f"{filename}.part{i}")
                with open(part_path, "rb") as p:
                    out.write(base64.b64decode(p.read()))
                os.remove(part_path)
        print(f"✅ Złożono plik: {output_file}")
        return jsonify({'status': 'done', 'file': f"/uploads/{filename}"}), 200

    return jsonify({'status': 'ok', 'message': f'Part {part}/{total_parts} received'}), 200


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

