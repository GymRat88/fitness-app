let detector;
let video;
let canvas;
let ctx;
let workoutId;
let correctCount = 0;
let totalCount = 0;

async function init() {
    try {
        await setupCamera();
        await loadModel();
        setupUI();
        detectPose();
    } catch (err) {
        alert(`Ошибка инициализации: ${err.message}`);
        console.error(err);
    }
}

async function setupCamera() {
    video = document.getElementById('video');
    canvas = document.getElementById('output');
    ctx = canvas.getContext('2d');
    
    const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
        audio: false
    });
    video.srcObject = stream;
    
    return new Promise((resolve) => {
        video.onloadedmetadata = () => {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            resolve();
        };
    });
}

async function loadModel() {
    await tf.ready();
    detector = await poseDetection.createDetector(
        poseDetection.SupportedModels.MoveNet,
        {
            modelType: poseDetection.movenet.modelType.SINGLEPOSE_THUNDER,
            enableSmoothing: true
        }
    );
}

function setupUI() {
    document.getElementById('start').addEventListener('click', startWorkout);
    document.getElementById('end').addEventListener('click', endWorkout);
}

async function startWorkout() {
    const exercise = document.getElementById('exercise').value;
    const response = await fetch('/api/start_workout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exercise_type: exercise })
    });
    
    const data = await response.json();
    if (data.status === 'success') {
        workoutId = data.workout_id;
        document.getElementById('start').disabled = true;
        document.getElementById('end').disabled = false;
        correctCount = 0;
        totalCount = 0;
    }
}

async function endWorkout() {
    await fetch('/api/end_workout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workout_id: workoutId })
    });
    
    document.getElementById('start').disabled = false;
    document.getElementById('end').disabled = true;
}

async function detectPose() {
    if (!detector) return;
    
    try {
        const poses = await detector.estimatePoses(video);
        drawPose(poses);
        updateStats(poses);
    } catch (err) {
        console.error('Ошибка детекции:', err);
    }
    
    requestAnimationFrame(detectPose);
}

function drawPose(poses) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    if (poses.length > 0) {
        const keypoints = poses[0].keypoints;
        
        // Draw skeleton
        const connections = [
            [5, 6], [5, 7], [6, 8], [7, 9], [8, 10],
            [5, 11], [6, 12], [11, 12], [11, 13],
            [12, 14], [13, 15], [14, 16]
        ];
        
        ctx.strokeStyle = 'green';
        ctx.lineWidth = 2;
        
        connections.forEach(([i, j]) => {
            const kp1 = keypoints[i];
            const kp2 = keypoints[j];
            if (kp1.score > 0.3 && kp2.score > 0.3) {
                ctx.beginPath();
                ctx.moveTo(kp1.x, kp1.y);
                ctx.lineTo(kp2.x, kp2.y);
                ctx.stroke();
            }
        });
        
        // Draw keypoints
        keypoints.forEach(kp => {
            if (kp.score > 0.3) {
                ctx.beginPath();
                ctx.arc(kp.x, kp.y, 5, 0, 2 * Math.PI);
                ctx.fillStyle = 'red';
                ctx.fill();
            }
        });
    }
}

function updateStats(poses) {
    if (poses.length > 0 && workoutId) {
        const keypoints = poses[0].keypoints;
        const angle = calculateAngle(keypoints);
        const isCorrect = checkCorrectness(angle);
        
        document.getElementById('angle').textContent = `${angle.toFixed(1)}°`;
        document.getElementById('correct').textContent = correctCount;
        document.getElementById('total').textContent = totalCount;
        
        // Send to server
        fetch('/api/save_angle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workout_id: workoutId,
                angle: angle,
                is_correct: isCorrect
            })
        });
    }
}

function calculateAngle(keypoints) {
    // Simplified angle calculation (adjust for your needs)
    const leftShoulder = keypoints[5];
    const leftElbow = keypoints[7];
    const leftWrist = keypoints[9];
    
    if (leftShoulder.score > 0.3 && leftElbow.score > 0.3 && leftWrist.score > 0.3) {
        const angle = Math.atan2(
            leftWrist.y - leftElbow.y,
            leftWrist.x - leftElbow.x
        ) - Math.atan2(
            leftShoulder.y - leftElbow.y,
            leftShoulder.x - leftElbow.x
        );
        return Math.abs(angle * (180 / Math.PI));
    }
    return 0;
}

function checkCorrectness(angle) {
    const exercise = document.getElementById('exercise').value;
    let isCorrect = false;
    
    if (exercise === 'pushups') {
        isCorrect = angle > 80 && angle < 100;
    } else if (exercise === 'squats') {
        isCorrect = angle > 70 && angle < 110;
    } else if (exercise === 'pullups') {
        isCorrect = angle > 120 && angle < 150;
    }
    
    if (isCorrect) correctCount++;
    totalCount++;
    
    return isCorrect;
}

// Initialize when page loads
document.addEventListener('DOMContentLoaded', init);
