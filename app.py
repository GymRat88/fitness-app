from flask import Flask, render_template, request, jsonify
import sqlite3
import os
from datetime import datetime
import numpy as np

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static'

IDEAL_ANGLES = {
    "pushups": {"min": 80, "max": 100},
    "squats": {"min": 80, "max": 100},
    "pullups": {"min": 120, "max": 150}
}

def get_db_connection():
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                exercise_type TEXT NOT NULL,
                duration_sec INTEGER,
                correct_count INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                correct_percentage REAL DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workout_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                angle FLOAT NOT NULL,
                is_correct BOOLEAN NOT NULL,
                FOREIGN KEY(workout_id) REFERENCES workouts(id)
            )
        ''')

@app.route('/')
def index():
    init_db()
    return render_template('index.html')

@app.route('/api/start_workout', methods=['POST'])
def start_workout():
    try:
        data = request.json
        exercise_type = data.get('exercise_type', 'pushups')
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO workouts (start_time, exercise_type) 
                VALUES (?, ?)
            ''', (datetime.now().isoformat(), exercise_type))
            workout_id = cursor.lastrowid
            
        return jsonify({
            'status': 'success',
            'workout_id': workout_id
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/save_angle', methods=['POST'])
def save_angle():
    try:
        data = request.json
        with get_db_connection() as conn:
            # Сохраняем данные угла
            conn.execute('''
                INSERT INTO workout_data 
                (workout_id, timestamp, angle, is_correct)
                VALUES (?, ?, ?, ?)
            ''', (
                data['workout_id'],
                datetime.now().isoformat(),
                data['angle'],
                data['is_correct']
            ))
            
            # Обновляем статистику тренировки
            conn.execute('''
                UPDATE workouts SET
                    total_count = total_count + 1,
                    correct_count = correct_count + ?,
                    correct_percentage = (correct_count + ?) * 100.0 / (total_count + 1)
                WHERE id = ?
            ''', (1 if data['is_correct'] else 0, 
                 1 if data['is_correct'] else 0, 
                 data['workout_id']))
            
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/end_workout', methods=['POST'])
def end_workout():
    try:
        data = request.json
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE workouts SET
                    end_time = ?,
                    duration_sec = ROUND((julianday(?) - julianday(start_time)) * 86400)
                WHERE id = ?
            ''', (
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                data['workout_id']
            ))
            
            # Получаем статистику
            stats = conn.execute('''
                SELECT correct_count, total_count, correct_percentage
                FROM workouts WHERE id = ?
            ''', (data['workout_id'],)).fetchone()
            
        return jsonify({
            'status': 'success',
            'stats': dict(stats)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/workout_history')
def workout_history():
    try:
        with get_db_connection() as conn:
            workouts = conn.execute('''
                SELECT id, start_time, end_time, exercise_type,
                       duration_sec, correct_percentage
                FROM workouts
                ORDER BY start_time DESC
                LIMIT 20
            ''').fetchall()
            
        return jsonify({
            'status': 'success',
            'workouts': [dict(row) for row in workouts]
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    app.run(host='0.0.0.0', port=5000)
