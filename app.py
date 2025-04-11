from flask import Flask, render_template, request, jsonify, Response
import sqlite3
import os
from datetime import datetime
import threading
import time
import mediapipe as mp
import numpy as np

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static'

# Инициализация MediaPipe
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
pose = mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5)

# Глобальные переменные
current_exercise = "pushups"
current_angle = 0
is_correct_form = False

# Идеальные углы для упражнений
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
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 1,
            start_time TEXT NOT NULL,
            end_time TEXT,
            exercise_type TEXT NOT NULL,
            duration_sec INTEGER,
            correct_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            correct_percentage REAL DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workout_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            angle FLOAT NOT NULL,
            is_correct BOOLEAN NOT NULL,
            FOREIGN KEY(workout_id) REFERENCES workouts(id)
        )
    ''')
    
    conn.commit()
    conn.close()

def calculate_angle(a, b, c):
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)
    
    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians*180.0/np.pi)
    
    return angle if angle <= 180 else 360 - angle

@app.route('/')
def index():
    init_db()
    return render_template('index.html')

@app.route('/api/set_exercise', methods=['POST'])
def set_exercise():
    global current_exercise
    data = request.json
    current_exercise = data.get('exercise_type', 'pushups')
    return jsonify({'status': 'success'})

@app.route('/api/process_frame', methods=['POST'])
def process_frame():
    try:
        # Получаем данные кадра от клиента
        data = request.json
        landmarks = data.get('landmarks')
        
        if not landmarks:
            return jsonify({'status': 'error', 'message': 'No landmarks provided'}), 400
            
        # Рассчитываем угол
        shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
        elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
        wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
        
        angle = calculate_angle(
            [shoulder['x'], shoulder['y']],
            [elbow['x'], elbow['y']],
            [wrist['x'], wrist['y']]
        )
        
        # Проверяем правильность формы
        ideal = IDEAL_ANGLES[current_exercise]
        is_correct = ideal["min"] <= angle <= ideal["max"]
        
        return jsonify({
            'status': 'success',
            'angle': angle,
            'is_correct': is_correct
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def start_workout(exercise_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO workouts (
            start_time, 
            exercise_type,
            correct_count,
            total_count,
            correct_percentage
        ) VALUES (?, ?, 0, 0, 0)
    ''', (datetime.now().isoformat(), exercise_type))
    
    workout_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return workout_id

def save_workout_data(workout_id, angle, is_correct):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Добавляем запись о угле
    cursor.execute('''
        INSERT INTO workout_data (
            workout_id,
            timestamp,
            angle,
            is_correct
        ) VALUES (?, ?, ?, ?)
    ''', (workout_id, datetime.now().isoformat(), angle, is_correct))
    
    # Обновляем статистику тренировки
    cursor.execute('''
        UPDATE workouts 
        SET 
            total_count = total_count + 1,
            correct_count = correct_count + ?,
            correct_percentage = (correct_count + ?) * 100.0 / (total_count + 1)
        WHERE id = ?
    ''', (1 if is_correct else 0, 1 if is_correct else 0, workout_id))
    
    conn.commit()
    conn.close()

def end_workout(workout_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Рассчитываем длительность тренировки
    cursor.execute('''
        UPDATE workouts 
        SET 
            end_time = ?,
            duration_sec = ROUND((julianday(?) - julianday(start_time)) * 86400)
        WHERE id = ?
    ''', (datetime.now().isoformat(), datetime.now().isoformat(), workout_id))
    
    conn.commit()
    conn.close()

def get_workout_history(limit=10):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            id,
            start_time,
            end_time,
            exercise_type,
            duration_sec,
            correct_percentage
        FROM workouts
        ORDER BY start_time DESC
        LIMIT ?
    ''', (limit,))
    
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return history

@app.route('/api/start_workout', methods=['POST'])
def api_start_workout():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400
        
        exercise_type = data.get('exercise_type', 'pushups')
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем, что упражнение допустимо
        valid_exercises = ['pushups', 'squats', 'pullups']
        if exercise_type not in valid_exercises:
            return jsonify({'status': 'error', 'message': 'Invalid exercise type'}), 400
        
        # Вставляем новую тренировку
        cursor.execute('''
            INSERT INTO workouts (
                start_time, 
                exercise_type,
                correct_count,
                total_count,
                correct_percentage
            ) VALUES (?, ?, 0, 0, 0)
        ''', (datetime.now().isoformat(), exercise_type))
        
        workout_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'workout_id': workout_id,
            'exercise_type': exercise_type
        })
        
    except Exception as e:
        print(f"Error starting workout: {str(e)}")  # Логируем ошибку
        if conn:
            conn.close()
        return jsonify({
            'status': 'error',
            'message': f"Failed to start workout: {str(e)}"
        }), 500

@app.route('/api/workout_details')
def api_workout_details():
    workout_id = request.args.get('id')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем основную информацию о тренировке
        cursor.execute('''
            SELECT 
                correct_count,
                total_count,
                correct_percentage
            FROM workouts
            WHERE id = ?
        ''', (workout_id,))
        
        stats = dict(cursor.fetchone())
        
        # Получаем все углы для этой тренировки
        cursor.execute('''
            SELECT 
                timestamp,
                angle,
                is_correct
            FROM workout_data
            WHERE workout_id = ?
            ORDER BY timestamp
        ''', (workout_id,))
        
        angles = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'status': 'success',
            'stats': stats,
            'angles': angles
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
        
@app.route('/api/save_angle', methods=['POST'])
def api_save_angle():
    data = request.json
    workout_id = data['workout_id']
    angle = data['angle']
    is_correct = data['is_correct']
    
    try:
        save_workout_data(workout_id, angle, is_correct)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/end_workout', methods=['POST'])
def api_end_workout():
    data = request.json
    workout_id = data['workout_id']
    
    try:
        end_workout(workout_id)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/workout_history')
def api_workout_history():
    try:
        history = get_workout_history(limit=20)  # Увеличили лимит до 20
        return jsonify({
            'status': 'success',
            'workouts': history
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
