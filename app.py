from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
import datetime
import os
import subprocess
import requests
import re
import logging
import time
import json
import threading
import schedule
from functools import wraps
import hashlib
import psutil  # Untuk mendapatkan informasi sistem

app = Flask(__name__)
app.secret_key = "supersecretkey12345"  # Untuk sesi autentikasi

# Variabel global untuk menyimpan data jaringan sebelumnya
prev_net = psutil.net_io_counters()
prev_time = time.time()

def get_network_speed():
    global prev_net, prev_time
    # Ambil data jaringan saat ini
    current_net = psutil.net_io_counters()
    current_time = time.time()
    
    # Hitung selisih waktu
    time_diff = current_time - prev_time
    if time_diff == 0:
        time_diff = 1  # Hindari pembagian dengan nol
    
    # Hitung kecepatan (bytes per detik)
    upload_speed = (current_net.bytes_sent - prev_net.bytes_sent) / time_diff
    download_speed = (current_net.bytes_recv - prev_net.bytes_recv) / time_diff
    
    # Konversi ke Mbps
    upload_speed_mbps = (upload_speed * 8) / 1_000_000  # Bytes ke Mbps
    download_speed_mbps = (download_speed * 8) / 1_000_000  # Bytes ke Mbps
    
    # Update data sebelumnya
    prev_net = current_net
    prev_time = current_time
    
    return upload_speed_mbps, download_speed_mbps

# Setup logging
logging.basicConfig(level=logging.DEBUG, filename='streamhib.log', format='%(asctime)s %(levelname)s:%(message)s')
logger = logging.getLogger(__name__)

# Pastikan folder video ada
VIDEO_FOLDER = os.path.join(os.getcwd(), 'video')
if not os.path.exists(VIDEO_FOLDER):
    os.makedirs(VIDEO_FOLDER)

# Status live streaming
live_status = {
    "is_live": False,
    "session_name": "",
    "platform": "YOUTUBE",
    "stream_key": "",
    "date": "",
    "time": "",
    "duration": 0,
    "process": None,
    "video": ""
}

scheduled_streams = []
inactive_sessions = []

# File konfigurasi
CONFIG_FILE = "config.json"
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"gdrive_api_key": "", "telegram_token": "", "telegram_chat_id": ""}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f)

config = load_config()

# Progres download
download_progress = {
    "progress": 0,
    "speed": 0,
    "start_time": 0,
    "total_size": 0,
    "is_downloading": False,
    "message": "",
    "success": False
}

# File sesi
SESSIONS_FILE = "sessions.json"
def save_sessions():
    with open(SESSIONS_FILE, "w") as f:
        json.dump({"inactive": inactive_sessions, "scheduled": scheduled_streams}, f)

def load_sessions():
    global inactive_sessions, scheduled_streams
    try:
        with open(SESSIONS_FILE, "r") as f:
            data = json.load(f)
            inactive_sessions = data.get("inactive", [])
            scheduled_streams = data.get("scheduled", [])
    except FileNotFoundError:
        pass

load_sessions()

# Fungsi autentikasi
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Fungsi untuk mengirim notifikasi Telegram
def send_telegram_notification(message):
    token = config.get("telegram_token")
    chat_id = config.get("telegram_chat_id")
    if not token or not chat_id:
        logger.warning("Telegram token or chat ID not configured")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        response = requests.post(url, data=data)
        if response.status_code != 200:
            logger.error(f"Failed to send Telegram notification: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {str(e)}")

# Fungsi untuk mengekstrak ID file dari URL atau ID langsung
def extract_file_id(input_string):
    file_id_pattern = r'[a-zA-Z0-9_-]{10,}'
    url_pattern = r'(?:https?:\/\/)?drive\.google\.com\/(?:file\/d\/|open\?id=)([a-zA-Z0-9_-]+)'
    url_match = re.search(url_pattern, input_string)
    if url_match:
        return url_match.group(1)
    if re.match(file_id_pattern, input_string):
        return input_string
    return None

# Fungsi untuk mendapatkan daftar video dari folder
def get_video_list():
    try:
        return [f for f in os.listdir(VIDEO_FOLDER) if f.endswith(('.mp4', '.mkv', '.avi'))]
    except UnicodeDecodeError:
        return [f.encode('utf-8', errors='replace').decode('utf-8') for f in os.listdir(VIDEO_FOLDER) if f.endswith(('.mp4', '.mkv', '.avi'))]

# Fungsi untuk menjalankan proses download di thread terpisah
def download_in_background(input_string, file_id):
    global download_progress
    download_progress["is_downloading"] = True
    download_progress["success"] = False
    download_progress["message"] = ""
    try:
        metadata_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?key={config['gdrive_api_key']}&fields=name,size"
        logger.debug(f"Requesting metadata from: {metadata_url}")
        metadata_response = requests.get(metadata_url)
        
        if metadata_response.status_code != 200:
            error_msg = f"Failed to get file metadata: HTTP {metadata_response.status_code} - {metadata_response.text}"
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return

        if not metadata_response.text:
            error_msg = "Failed to get file metadata: Empty response from Google Drive API"
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return

        try:
            metadata = metadata_response.json()
        except ValueError as e:
            error_msg = f"Failed to parse metadata: {str(e)}"
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return

        if 'error' in metadata:
            error_msg = f"Failed to get file metadata: {metadata['error']['message']}"
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return

        file_name = metadata.get('name')
        file_size = int(metadata.get('size', 0))
        
        if not file_name:
            error_msg = "Failed to get file name from metadata"
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return

        file_path = os.path.join(VIDEO_FOLDER, file_name)
        if os.path.exists(file_path):
            error_msg = f"File {file_name} already exists in video folder. Please rename or delete the existing file."
            logger.error(error_msg)
            download_progress["message"] = error_msg
            send_telegram_notification(error_msg)
            return
        
        download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?key={config['gdrive_api_key']}&alt=media"
        logger.debug(f"Downloading file from: {download_url}")
        
        download_progress["start_time"] = time.time()
        download_progress["total_size"] = file_size
        with requests.get(download_url, stream=True) as r:
            if r.status_code != 200:
                error_msg = f"Failed to download: HTTP {r.status_code} - {r.text}"
                logger.error(error_msg)
                download_progress["message"] = error_msg
                send_telegram_notification(error_msg)
                return

            total_size = int(r.headers.get('content-length', file_size))
            downloaded = 0
            last_downloaded = 0
            last_time = download_progress["start_time"]
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        current_time = time.time()
                        if current_time - last_time >= 1:  # Update setiap detik
                            speed = (downloaded - last_downloaded) / (current_time - last_time)  # bytes/s
                            download_progress["speed"] = speed / 1024  # KB/s
                            last_downloaded = downloaded
                            last_time = current_time
                        download_progress["progress"] = int((downloaded / total_size) * 100)
        
        download_progress["progress"] = 100
        download_progress["speed"] = 0
        download_progress["success"] = True
        success_msg = f"Successfully downloaded {file_name} ({file_size} bytes)"
        logger.info(success_msg)
        download_progress["message"] = success_msg
        send_telegram_notification(f"Successfully downloaded {file_name} ({file_size / 1024 / 1024:.2f} MB)")
    except Exception as e:
        error_msg = f"Download failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        download_progress["message"] = error_msg
        send_telegram_notification(error_msg)
    finally:
        download_progress["is_downloading"] = False

# Otomatisasi jadwal streaming
def start_scheduled_stream(schedule):
    global live_status
    if live_status["is_live"]:
        logger.warning(f"Cannot start scheduled stream {schedule['session_name']}, another stream is active")
        send_telegram_notification(f"Failed to start scheduled stream {schedule['session_name']}: Another stream is active")
        return
    video_path = os.path.join(VIDEO_FOLDER, schedule["video"])
    if not os.path.exists(video_path):
        logger.error(f"Video {schedule['video']} not found for scheduled stream")
        send_telegram_notification(f"Failed to start scheduled stream {schedule['session_name']}: Video {schedule['video']} not found")
        return
    if schedule["platform"] == "YOUTUBE":
        stream_url = f"rtmp://a.rtmp.youtube.com/live2/{schedule['stream_key']}"
    elif schedule["platform"] == "FACEBOOK":
        stream_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{schedule['stream_key']}"
    else:
        logger.error(f"Unsupported platform for scheduled stream: {schedule['platform']}")
        send_telegram_notification(f"Failed to start scheduled stream {schedule['session_name']}: Unsupported platform")
        return

    ffmpeg_cmd = ['/usr/bin/ffmpeg', '-stream_loop', '-1', '-re', '-i', video_path, '-c:v', 'copy', '-c:a', 'copy', '-f', 'flv', stream_url]
    process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    live_status.update({
        "is_live": True,
        "session_name": schedule["session_name"],
        "platform": schedule["platform"],
        "stream_key": schedule["stream_key"],
        "date": datetime.datetime.now().strftime("%A, %d/%m/%Y"),
        "time": datetime.datetime.now().strftime("%H:%M"),
        "duration": 0,
        "process": process,
        "video": schedule["video"]
    })
    scheduled_streams.remove(schedule)
    save_sessions()
    logger.info(f"Started scheduled stream {schedule['session_name']} on {schedule['platform']}")
    send_telegram_notification(f"Started scheduled stream {schedule['session_name']} on {schedule['platform']}")

def check_scheduled_streams():
    for s in scheduled_streams:
        schedule_time = f"{s['date']} {s['time']}"
        schedule.every().day.at(s['time']).do(start_scheduled_stream, s)

def run_scheduler():
    check_scheduled_streams()
    while True:
        schedule.run_pending()
        time.sleep(60)

scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Hash password untuk keamanan
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        expected_hash = hashlib.sha256("pass12345".encode()).hexdigest()
        if username == 'admin' and hashed_password == expected_hash:
            session['logged_in'] = True
            logger.info("User logged in successfully")
            send_telegram_notification("User logged in successfully")
            return redirect(url_for('index'))
        else:
            logger.warning("Failed login attempt")
            send_telegram_notification("Failed login attempt")
            return render_template('login.html', error="Invalid username or password")
    return render_template('login.html', error=None)

@app.route('/logout')
@login_required
def logout():
    session.pop('logged_in', None)
    logger.info("User logged out")
    send_telegram_notification("User logged out")
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    videos = get_video_list()
    stats = {
        "live": 1 if live_status["is_live"] else 0,
        "scheduled": len(scheduled_streams),
        "inactive": len(inactive_sessions)
    }
    return render_template('index.html', status=live_status, videos=videos, scheduled=scheduled_streams, inactive=inactive_sessions, stats=stats)

@app.route('/system_usage', methods=['GET'])
@login_required
def system_usage():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    memory_usage = memory.percent
    disk = psutil.disk_usage('/')
    disk_usage = disk.percent
    upload_speed, download_speed = get_network_speed()
    return jsonify({
        "cpu": round(cpu_usage, 1),
        "memory": round(memory_usage, 1),
        "disk": round(disk_usage, 1),
        "upload": upload_speed,
        "download": download_speed
    })

@app.route('/set_api_key', methods=['POST'])
@login_required
def set_api_key():
    global config
    api_key = request.form.get('api_key')
    telegram_token = request.form.get('telegram_token')
    telegram_chat_id = request.form.get('telegram_chat_id')
    config["gdrive_api_key"] = api_key
    config["telegram_token"] = telegram_token
    config["telegram_chat_id"] = telegram_chat_id
    save_config(config)
    logger.info("API Key and Telegram settings updated")
    send_telegram_notification("API Key and Telegram settings updated")
    return jsonify({"message": "Settings saved successfully"})

@app.route('/download', methods=['POST'])
@login_required
def download():
    global download_progress
    if download_progress["is_downloading"]:
        return jsonify({"message": "Another download is in progress. Please wait until it completes."})

    input_string = request.form.get('file_id')
    
    if not input_string:
        return jsonify({"message": "File ID or URL is required"})

    file_id = extract_file_id(input_string)
    if not file_id:
        return jsonify({"message": "Invalid File ID or URL format. Please provide a valid Google Drive file ID or URL."})

    if not config["gdrive_api_key"]:
        return jsonify({"message": "Google Drive API Key is not set. Please set it in Settings."})

    # Reset progres download
    download_progress["progress"] = 0
    download_progress["speed"] = 0
    download_progress["start_time"] = 0
    download_progress["total_size"] = 0
    download_progress["is_downloading"] = True
    download_progress["message"] = ""
    download_progress["success"] = False

    # Jalankan download di thread terpisah
    download_thread = threading.Thread(target=download_in_background, args=(input_string, file_id))
    download_thread.start()

    return jsonify({"message": "Download started. Check progress in the UI."})

@app.route('/download_progress', methods=['GET'])
@login_required
def get_download_progress():
    remaining_bytes = (download_progress["total_size"] * (100 - download_progress["progress"])) / 100
    eta = remaining_bytes / (download_progress["speed"] * 1024) if download_progress["speed"] > 0 else 0
    return jsonify({
        "progress": download_progress["progress"],
        "speed": download_progress["speed"],
        "eta": int(eta),
        "is_downloading": download_progress["is_downloading"],
        "message": download_progress["message"],
        "success": download_progress["success"]
    })

@app.route('/rename_video', methods=['POST'])
@login_required
def rename_video():
    video = request.form.get('video')
    new_name = request.form.get('new_name')
    try:
        old_path = os.path.join(VIDEO_FOLDER, video)
        new_path = os.path.join(VIDEO_FOLDER, new_name)
        os.rename(old_path, new_path)
        logger.info(f"Renamed {video} to {new_name}")
        send_telegram_notification(f"Renamed video {video} to {new_name}")
        return jsonify({"message": f"Renamed {video} to {new_name}"})
    except Exception as e:
        logger.error(f"Failed to rename video: {str(e)}", exc_info=True)
        send_telegram_notification(f"Failed to rename video: {str(e)}")
        return jsonify({"message": f"Failed to rename: {str(e)}"})

@app.route('/delete_video', methods=['POST'])
@login_required
def delete_video():
    video = request.form.get('video')
    try:
        file_path = os.path.join(VIDEO_FOLDER, video)
        os.remove(file_path)
        logger.info(f"Deleted video {video}")
        send_telegram_notification(f"Deleted video {video}")
        return jsonify({"message": f"Deleted {video}"})
    except Exception as e:
        logger.error(f"Failed to delete video: {str(e)}", exc_info=True)
        send_telegram_notification(f"Failed to delete video: {str(e)}")
        return jsonify({"message": f"Failed to delete: {str(e)}"})

@app.route('/start_stream', methods=['POST'])
@login_required
def start_stream():
    global live_status
    if live_status["is_live"]:
        return jsonify({"message": "Stream already running"})

    video = request.form.get('video')
    session_name = request.form.get('session_name', 'Untitled Live Session')
    platform = request.form.get('platform', 'YOUTUBE')
    stream_key = request.form.get('stream_key', '')

    if not video or not stream_key:
        return jsonify({"message": "Video and stream key are required"})

    video_path = os.path.join(VIDEO_FOLDER, video)
    if not os.path.exists(video_path):
        return jsonify({"message": f"Video {video} not found"})

    if platform == "YOUTUBE":
        stream_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    elif platform == "FACEBOOK":
        stream_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{stream_key}"
    else:
        return jsonify({"message": "Unsupported platform"})

    ffmpeg_cmd = ['/usr/bin/ffmpeg', '-stream_loop', '-1', '-re', '-i', video_path, '-c:v', 'copy', '-c:a', 'copy', '-f', 'flv', stream_url]

    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        live_status["is_live"] = True
        live_status["session_name"] = session_name
        live_status["platform"] = platform
        live_status["stream_key"] = stream_key
        live_status["date"] = datetime.datetime.now().strftime("%A, %d/%m/%Y")
        live_status["time"] = datetime.datetime.now().strftime("%H:%M")
        live_status["duration"] = 0
        live_status["process"] = process
        live_status["video"] = video

        # Log output FFmpeg
        def log_ffmpeg_output():
            for line in process.stderr:
                logger.error(f"FFmpeg error for {session_name}: {line.decode().strip()}")

        threading.Thread(target=log_ffmpeg_output, daemon=True).start()

        response_status = live_status.copy()
        response_status.pop("process")
        logger.info(f"Started streaming {session_name} on {platform} with video {video}")
        send_telegram_notification(f"Started streaming {session_name} on {platform}")
        return jsonify({"message": f"Started streaming {session_name}", "status": response_status})
    except Exception as e:
        logger.error(f"Failed to start stream: {str(e)}", exc_info=True)
        send_telegram_notification(f"Failed to start stream {session_name}: {str(e)}")
        return jsonify({"message": f"Failed to start stream: {str(e)}"})

@app.route('/stop_stream', methods=['POST'])
@login_required
def stop_stream():
    global live_status
    if not live_status["is_live"] or not live_status["process"]:
        return jsonify({"message": "No stream running"})

    try:
        live_status["process"].terminate()
        live_status["process"].wait()
        session_name = live_status["session_name"]
        live_status["is_live"] = False
        live_status["process"] = None
        inactive_sessions.append(live_status.copy())
        live_status["video"] = ""
        save_sessions()
        logger.info(f"Stopped streaming {session_name}")
        send_telegram_notification(f"Stopped streaming {session_name}")
        return jsonify({"message": f"Stopped streaming {session_name}"})
    except Exception as e:
        logger.error(f"Failed to stop stream: {str(e)}", exc_info=True)
        send_telegram_notification(f"Failed to stop stream: {str(e)}")
        return jsonify({"message": f"Failed to stop stream: {str(e)}"})

@app.route('/schedule_stream', methods=['POST'])
@login_required
def schedule_stream():
    schedule = {
        "session_name": request.form.get('session_name', 'Untitled Session'),
        "platform": request.form.get('platform', 'YOUTUBE'),
        "stream_key": request.form.get('stream_key', 'your-stream-key'),
        "date": request.form.get('date', '2025-04-20'),
        "time": request.form.get('time', '10:00'),
        "duration": request.form.get('duration', '1'),
        "video": request.form.get('video', '')
    }
    scheduled_streams.append(schedule)
    save_sessions()
    check_scheduled_streams()
    logger.info(f"Scheduled stream {schedule['session_name']} for {schedule['date']} {schedule['time']}")
    send_telegram_notification(f"Scheduled stream {schedule['session_name']} for {schedule['date']} {schedule['time']}")
    return jsonify({"message": f"Scheduled {schedule['session_name']}"})

@app.route('/cancel_schedule', methods=['POST'])
@login_required
def cancel_schedule():
    session_name = request.form.get('session_name')
    global scheduled_streams
    scheduled_streams = [s for s in scheduled_streams if s["session_name"] != session_name]
    save_sessions()
    logger.info(f"Canceled scheduled stream {session_name}")
    send_telegram_notification(f"Canceled scheduled stream {session_name}")
    return jsonify({"message": f"Canceled {session_name}"})

@app.route('/restart_session', methods=['POST'])
@login_required
def restart_session():
    global live_status, inactive_sessions
    session_name = request.form.get('session_name')

    session_to_restart = next((session for session in inactive_sessions if session["session_name"] == session_name), None)
    if not session_to_restart:
        return jsonify({"message": f"Session {session_name} not found in inactive sessions"})

    if live_status["is_live"]:
        return jsonify({"message": "Another stream is already running"})

    video = session_to_restart.get("video", None)
    platform = session_to_restart["platform"]
    stream_key = session_to_restart["stream_key"]

    if not video:
        return jsonify({"message": f"Video information for session {session_name} not found"})

    video_path = os.path.join(VIDEO_FOLDER, video)
    if not os.path.exists(video_path):
        return jsonify({"message": f"Video {video} for session {session_name} not found"})

    if platform == "YOUTUBE":
        stream_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    elif platform == "FACEBOOK":
        stream_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{stream_key}"
    else:
        return jsonify({"message": "Unsupported platform"})

    ffmpeg_cmd = ['/usr/bin/ffmpeg', '-stream_loop', '-1', '-re', '-i', video_path, '-c:v', 'copy', '-c:a', 'copy', '-f', 'flv', stream_url]

    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        live_status["is_live"] = True
        live_status["session_name"] = session_name
        live_status["platform"] = platform
        live_status["stream_key"] = stream_key
        live_status["date"] = datetime.datetime.now().strftime("%A, %d/%m/%Y")
        live_status["time"] = datetime.datetime.now().strftime("%H:%M")
        live_status["duration"] = 0
        live_status["process"] = process
        live_status["video"] = video

        def log_ffmpeg_output():
            for line in process.stderr:
                logger.error(f"FFmpeg error for {session_name}: {line.decode().strip()}")

        threading.Thread(target=log_ffmpeg_output, daemon=True).start()

        inactive_sessions[:] = [s for s in inactive_sessions if s["session_name"] != session_name]
        save_sessions()
        response_status = live_status.copy()
        response_status.pop("process")
        logger.info(f"Restarted streaming {session_name} on {platform}")
        send_telegram_notification(f"Restarted streaming {session_name} on {platform}")
        return jsonify({"message": f"Restarted streaming {session_name}", "status": response_status})
    except Exception as e:
        logger.error(f"Failed to restart stream: {str(e)}", exc_info=True)
        send_telegram_notification(f"Failed to restart stream {session_name}: {str(e)}")
        return jsonify({"message": f"Failed to restart stream: {str(e)}"})

@app.route('/delete_session', methods=['POST'])
@login_required
def delete_session():
    session_name = request.form.get('session_name')
    global inactive_sessions
    inactive_sessions = [s for s in inactive_sessions if s["session_name"] != session_name]
    save_sessions()
    logger.info(f"Deleted session {session_name}")
    send_telegram_notification(f"Deleted session {session_name}")
    return jsonify({"message": f"Deleted {session_name}"})

@app.route('/video/<filename>')
@login_required
def serve_video(filename):
    return send_from_directory(VIDEO_FOLDER, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=4545, debug=False)  # Debug dimatikan untuk produksi
