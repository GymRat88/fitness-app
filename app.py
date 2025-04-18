import os
import cv2
import numpy as np
import sqlite3
from datetime import datetime
import mediapipe as mp
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import base64
import eventlet
import logging

# Настройка eventlet
eventlet.monkey_patch()

# Инициализация Flask и SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация MediaPipe
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
pose = mp_pose.Pose(
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
    model_complexity=1
)

# Конфигурация упражнений
EXERCISE_CONFIG = {
    "pushups": {
        "min_angle": 80,
        "max_angle": 100,
        "points": {
            "a": mp_pose.PoseLandmark.LEFT_SHOULDER,
            "b": mp_pose.PoseLandmark.LEFT_ELBOW,
            "c": mp_pose.PoseLandmark.LEFT_WRIST
        },
        "name": "Отжимания"
    },
    "squats": {
        "min_angle": 80,
        "max_angle": 100,
        "points": {
            "a": mp_pose.PoseLandmark.LEFT_HIP,
            "b": mp_pose.PoseLandmark.LEFT_KNEE,
            "c": mp_pose.PoseLandmark.LEFT_ANKLE
        },
        "name": "Приседания"
    },
    "pullups": {
        "min_angle": 120,
        "max_angle": 150,
        "points": {
            "a": mp_pose.PoseLandmark.LEFT_SHOULDER,
            "b": mp_pose.PoseLandmark.LEFT_ELBOW,
            "c": mp_pose.PoseLandmark.LEFT_WRIST
        },
        "name": "Подтягивания"
    }
}

# Инициализация базы данных
def get_db_connection():
    conn = sqlite3.connect('fitness.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Таблица тренировок
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
        
        # Таблица данных тренировки
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workout_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                angle REAL NOT NULL,
                is_correct BOOLEAN NOT NULL,
                FOREIGN KEY(workout_id) REFERENCES workouts(id)
            )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
    finally:
        if conn:
            conn.close()

# Расчет угла между тремя точками
def calculate_angle(a, b, c):
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)
    
    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians*180.0/np.pi)
    
    return angle if angle <= 180 else 360 - angle

# Обработка кадра с MediaPipe
def process_frame(frame, exercise_type="pushups"):
    try:
        if frame is None or frame.size == 0:
            logger.warning("Empty frame received")
            return None, 0, False

        # Конвертация и обработка кадра
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(frame_rgb)
        
        if not results.pose_landmarks:
            return frame, 0, False

        # Получаем конфигурацию для текущего упражнения
        config = EXERCISE_CONFIG.get(exercise_type, EXERCISE_CONFIG["pushups"])
        landmarks = results.pose_landmarks.landmark

        # Получаем точки для расчета угла
        a = [
            landmarks[config["points"]["a"].x * frame.shape[1],
            landmarks[config["points"]["a"].y * frame.shape[0]
        ]
        b = [
            landmarks[config["points"]["b"].x * frame.shape[1],
            landmarks[config["points"]["b"].y * frame.shape[0]
        ]
        c = [
            landmarks[config["points"]["c"].x * frame.shape[1],
            landmarks[config["points"]["c"].y * frame.shape[0]
        ]

        # Рассчитываем угол
        angle = calculate_angle(a, b, c)
        is_correct = config["min_angle"] <= angle <= config["max_angle"]
        
        # Отрисовываем скелет
        color = (0, 255, 0) if is_correct else (0, 0, 255)
        mp_drawing.draw_landmarks(
            frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS,
            mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=2),
            mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=2)
        )

        # Добавляем информацию об угле
        cv2.putText(frame, f"{config['name']}: {angle:.1f}°", (20, 50), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(frame, "CORRECT" if is_correct else "INCORRECT", (20, 80), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        return frame, angle, is_correct

    except Exception as e:
        logger.error(f"Error processing frame: {str(e)}")
        return frame, 0, False

# Маршруты Flask
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/workout_history')
def get_workout_history():
    try:
        conn = get_db_connection()
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
        logger.error(f"Error fetching workout history: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/start_workout', methods=['POST'])
def start_workout():
    try:
        data = request.get_json()
        exercise_type = data.get('exercise_type', 'pushups')
        
        if exercise_type not in EXERCISE_CONFIG:
            return jsonify({'status': 'error', 'message': 'Invalid exercise type'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO workouts (start_time, exercise_type)
            VALUES (?, ?)
        ''', (datetime.now().isoformat(), exercise_type))
        
        workout_id = cursor.lastrowid
        conn.commit()
        
        return jsonify({
            'status': 'success',
            'workout_id': workout_id,
            'exercise_type': exercise_type
        })
    except Exception as e:
        logger.error(f"Error starting workout: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/end_workout', methods=['POST'])
def end_workout():
    try:
        data = request.get_json()
        workout_id = data.get('workout_id')
        
        if not workout_id:
            return jsonify({'status': 'error', 'message': 'Workout ID is required'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE workouts 
            SET end_time = ?,
                duration_sec = ROUND((julianday(?) - julianday(start_time)) * 86400)
            WHERE id = ?
        ''', (datetime.now().isoformat(), datetime.now().isoformat(), workout_id))
        
        conn.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error ending workout: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/save_angle', methods=['POST'])
def save_angle():
    try:
        data = request.get_json()
        workout_id = data.get('workout_id')
        angle = data.get('angle')
        is_correct = data.get('is_correct', False)
        
        if not all([workout_id, angle is not None]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Сохраняем данные угла
        cursor.execute('''
            INSERT INTO workout_data (workout_id, timestamp, angle, is_correct)
            VALUES (?, ?, ?, ?)
        ''', (workout_id, datetime.now().isoformat(), angle, is_correct))
        
        # Обновляем статистику тренировки
        cursor.execute('''
            UPDATE workouts 
            SET total_count = total_count + 1,
                correct_count = correct_count + ?,
                correct_percentage = (SELECT (SUM(is_correct) * 100.0 / COUNT(*)) 
                                     FROM workout_data 
                                     WHERE workout_id = ?)
            WHERE id = ?
        ''', (1 if is_correct else 0, workout_id, workout_id))
        
        conn.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error saving angle data: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn:
            conn.close()

# Обработчики WebSocket
@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('connection_response', {'status': 'connected'}, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

@socketio.on('video_frame')
def handle_video_frame(data):
    try:
        # Декодируем изображение из base64
        frame_data = data['frame'].split(',')[1]
        nparr = np.frombuffer(base64.b64decode(frame_data), np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            logger.warning("Failed to decode frame")
            return
        
        # Обрабатываем кадр
        exercise_type = data.get('exercise_type', 'pushups')
        processed_frame, angle, is_correct = process_frame(frame, exercise_type)
        
        if processed_frame is None:
            return
        
        # Кодируем обработанный кадр в base64
        _, buffer = cv2.imencode('.jpg', processed_frame)
        processed_frame_data = base64.b64encode(buffer).decode('utf-8')
        
        # Отправляем результаты обратно клиенту
        socketio.emit('processed_frame', {
            'frame': 'data:image/jpeg;base64,' + processed_frame_data,
            'angle': angle,
            'is_correct': is_correct,
            'exercise_type': exercise_type
        }, room=request.sid)
        
    except Exception as e:
        logger.error(f"Error processing video frame: {str(e)}")

# Запуск приложения
if __name__ == '__main__':
    init_db()
    socketio.run(app, 
                 host='0.0.0.0', 
                 port=int(os.environ.get('PORT', 5000)), 
                 debug=os.environ.get('DEBUG', 'False') == 'True',
                 use_reloader=False)
