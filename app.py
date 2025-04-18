from flask import Flask, render_template, request, jsonify, Response
import cv2
import numpy as np
import sqlite3
import os
from datetime import datetime
import threading
import time
import mediapipe as mp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder
import asyncio
import json

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
pc = None

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

@app.route('/')
def index():
    init_db()
    return render_template('index.html')

@app.route('/api/webrtc_offer', methods=['POST'])
async def webrtc_offer():
    global pc, current_exercise
    
    params = await request.json
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    pc = RTCPeerConnection()
    
    @pc.on("track")
    def on_track(track):
        print(f"Track received: {track.kind}")
        
        if track.kind == "video":
            @track.on("frame")
            def on_frame(frame):
                try:
                    img = frame.to_ndarray(format="bgr24")
                    process_frame(img)
                except Exception as e:
                    print(f"Frame processing error: {e}")
    
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    return jsonify({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })

def process_frame(frame):
    global current_angle, is_correct_form, current_exercise
    
    img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = pose.process(img_rgb)
    
    if results.pose_landmarks:
        landmarks = results.pose_landmarks.landmark
        
        if current_exercise == "pushups":
            shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
            elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
            wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
        elif current_exercise == "squats":
            shoulder = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
            elbow = landmarks[mp_pose.PoseLandmark.LEFT_KNEE.value]
            wrist = landmarks[mp_pose.PoseLandmark.LEFT_ANKLE.value]
        else:  # pullups
            shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
            elbow = landmarks[mp_pose.PoseLandmark.LEFT_ELBOW.value]
            wrist = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
        
        current_angle = calculate_angle(
            [shoulder.x, shoulder.y],
            [elbow.x, elbow.y],
            [wrist.x, wrist.y]
        )
        
        ideal = IDEAL_ANGLES[current_exercise]
        is_correct_form = ideal["min"] <= current_angle <= ideal["max"]

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

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
