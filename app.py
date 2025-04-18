import os
import cv2
import numpy as np
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import mediapipe as mp
import eventlet
import base64
import threading

eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet')

# Инициализация MediaPipe
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
pose = mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5)

# Глобальные переменные
current_exercise = "pushups"
current_angle = 0
is_correct_form = False
clients = {}

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
    
    cursor.execute('DROP TABLE IF EXISTS workouts')
    cursor.execute('DROP TABLE IF EXISTS workout_data')
    
    cursor.execute('''
        CREATE TABLE workouts (
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
        CREATE TABLE workout_data (
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
    print("Database initialized with correct schema")

def calculate_angle(a, b, c):
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)
    
    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians*180.0/np.pi)
    
    return angle if angle <= 180 else 360 - angle

def process_frame(frame, exercise_type):
    global current_angle, is_correct_form
    
    try:
        # Конвертируем и обрабатываем кадр
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(frame_rgb)
        
        if results.pose_landmarks:
            landmarks = results.pose_landmarks.landmark
            
            if exercise_type == "pushups":
                shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
                wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
            elif exercise_type == "squats":
                shoulder = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
                elbow = landmarks[mp_pose.PoseLandmark.LEFT_KNEE.value]
                wrist = landmarks[mp_pose.PoseLandmark.LEFT_ANKLE.value]
            else:  # pullups
                shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
                wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
            
            # Рассчитываем угол
            current_angle = calculate_angle(
                [shoulder.x * frame.shape[1], shoulder.y * frame.shape[0]],
                [elbow.x * frame.shape[1], elbow.y * frame.shape[0]],
                [wrist.x * frame.shape[1], wrist.y * frame.shape[0]]
            )
            
            # Проверяем правильность формы
            ideal = IDEAL_ANGLES[exercise_type]
            is_correct_form = ideal["min"] <= current_angle <= ideal["max"]
            
            # Отрисовываем скелет
            color = (0, 255, 0) if is_correct_form else (0, 0, 255)
            mp_drawing.draw_landmarks(
                frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS,
                mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=2),
                mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=2)
            )
            
            # Добавляем текст с углом
            cv2.putText(frame, f"Angle: {current_angle:.1f}°", (20, 50), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
            
            return frame, current_angle, is_correct_form
        
    except Exception as e:
        print(f"Error processing frame: {e}")
    
    return frame, 0, False

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

@app.route('/api/start_workout', methods=['POST'])
def api_start_workout():
    try:
        data = request.get_json()
        exercise_type = data.get('exercise_type', 'pushups')
        
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
        
        return jsonify({
            'status': 'success',
            'workout_id': workout_id,
            'exercise_type': exercise_type
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f"Failed to start workout: {str(e)}"
        }), 500

@app.route('/api/save_angle', methods=['POST'])
def api_save_angle():
    data = request.json
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO workout_data (
                workout_id,
                timestamp,
                angle,
                is_correct
            ) VALUES (?, ?, ?, ?)
        ''', (data['workout_id'], datetime.now().isoformat(), data['angle'], data['is_correct']))
        
        cursor.execute('''
            UPDATE workouts 
            SET 
                total_count = total_count + 1,
                correct_count = correct_count + ?,
                correct_percentage = (correct_count + ?) * 100.0 / (total_count + 1)
            WHERE id = ?
        ''', (1 if data['is_correct'] else 0, 1 if data['is_correct'] else 0, data['workout_id']))
        
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/end_workout', methods=['POST'])
def api_end_workout():
    data = request.json
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE workouts 
            SET 
                end_time = ?,
                duration_sec = ROUND((julianday(?) - julianday(start_time)) * 86400)
            WHERE id = ?
        ''', (datetime.now().isoformat(), datetime.now().isoformat(), data['workout_id']))
        
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/workout_history')
def api_workout_history():
    try:
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
            LIMIT 20
        ''')
        
        history = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'status': 'success',
            'workouts': history
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/workout_details')
def api_workout_details():
    workout_id = request.args.get('id')
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                correct_count,
                total_count,
                correct_percentage
            FROM workouts
            WHERE id = ?
        ''', (workout_id,))
        
        stats = dict(cursor.fetchone())
        
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

@socketio.on('connect')
def handle_connect():
    print('Client connected:', request.sid)
    clients[request.sid] = {'exercise': 'pushups'}

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected:', request.sid)
    clients.pop(request.sid, None)

@socketio.on('set_exercise')
def handle_set_exercise(data):
    if request.sid in clients:
        clients[request.sid]['exercise'] = data.get('exercise_type', 'pushups')

@socketio.on('video_frame')
def handle_video_frame(data):
    try:
        if request.sid not in clients:
            return
            
        # Декодируем кадр
        frame_data = data['frame'].split(',')[1]
        nparr = np.frombuffer(base64.b64decode(frame_data), np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        # Обрабатываем кадр
        exercise_type = clients[request.sid]['exercise']
        processed_frame, angle, is_correct = process_frame(frame, exercise_type)
        
        # Отправляем результаты обратно клиенту
        socketio.emit('pose_data', {
            'angle': angle,
            'is_correct': is_correct,
            'exercise_type': exercise_type
        }, room=request.sid)
        
    except Exception as e:
        print(f"Error processing video frame: {e}")

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
