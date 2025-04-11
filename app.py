from flask import Flask, render_template, request, jsonify
import sqlite3
import os
from datetime import datetime
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.config['DATABASE'] = 'instance/users.db'
app.config['UPLOAD_FOLDER'] = 'static/uploads'

os.makedirs('instance', exist_ok=True)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_type TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration INTEGER,
                correct_count INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                accuracy REAL DEFAULT 0
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS angles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER,
                timestamp TEXT NOT NULL,
                angle REAL NOT NULL,
                is_correct BOOLEAN NOT NULL,
                FOREIGN KEY(workout_id) REFERENCES workouts(id)
            )
        ''')
        db.commit()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start_workout', methods=['POST'])
def start_workout():
    data = request.json
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO workouts (exercise_type, start_time)
            VALUES (?, ?)
        ''', (data['exercise_type'], datetime.now().isoformat()))
        db.commit()
        return jsonify({
            'status': 'success',
            'workout_id': cursor.lastrowid
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/save_angle', methods=['POST'])
def save_angle():
    data = request.json
    try:
        db = get_db()
        db.execute('''
            INSERT INTO angles (workout_id, timestamp, angle, is_correct)
            VALUES (?, ?, ?, ?)
        ''', (
            data['workout_id'],
            datetime.now().isoformat(),
            data['angle'],
            data['is_correct']
        ))
        
        # Update workout stats
        db.execute('''
            UPDATE workouts SET
                total_count = total_count + 1,
                correct_count = correct_count + ?,
                accuracy = ROUND((correct_count + ?) * 100.0 / (total_count + 1), 1)
            WHERE id = ?
        ''', (1 if data['is_correct'] else 0, 1 if data['is_correct'] else 0, data['workout_id']))
        
        db.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/workout_history')
def workout_history():
    try:
        db = get_db()
        workouts = db.execute('''
            SELECT id, start_time, exercise_type, duration, accuracy
            FROM workouts
            ORDER BY start_time DESC
            LIMIT 5
        ''').fetchall()
        return jsonify({
            'status': 'success',
            'workouts': [dict(row) for row in workouts]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
