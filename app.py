
import os
import time
import hmac
import secrets
import threading
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    session,
    jsonify,
    make_response,
    abort,
)
from waitress import serve
from dotenv import load_dotenv


# ==========================================
# CONFIGURATION
# ==========================================
load_dotenv()


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value or value.startswith("REPLACE_WITH_"):
        raise RuntimeError(
            f"Missing or placeholder environment variable: {name}"
        )
    return value


FLASK_SECRET_KEY = required_env("FLASK_SECRET_KEY")
DASHBOARD_USERNAME = required_env("DASHBOARD_USERNAME")
DASHBOARD_PASSWORD = required_env("DASHBOARD_PASSWORD")
CAMERA_API_KEY = required_env("CAMERA_API_KEY")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))

MAX_FRAME_BYTES = int(os.getenv("MAX_FRAME_BYTES", "150000"))
MIN_UPLOAD_INTERVAL_SECONDS = float(
    os.getenv("MIN_UPLOAD_INTERVAL_SECONDS", "0.5")
)

if not 1024 <= MAX_FRAME_BYTES <= 2_000_000:
    raise RuntimeError("MAX_FRAME_BYTES must be between 1024 and 2000000")

if MIN_UPLOAD_INTERVAL_SECONDS < 0.1:
    raise RuntimeError("MIN_UPLOAD_INTERVAL_SECONDS must be at least 0.1")


# ==========================================
# FLASK APP
# ==========================================
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    MAX_CONTENT_LENGTH=MAX_FRAME_BYTES,
)

# Latest JPEG stored in RAM
latest_frame = None
latest_frame_time = None
last_upload_by_ip = {}
robot_last_command = "NONE"
robot_last_command_time = None

frame_lock = threading.Lock()


# ==========================================
# SECURITY HEADERS
# ==========================================
@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"

    # FIX: blob: allows JavaScript Blob URLs for camera images.
    # Keep scripts and styles restricted to the same origin.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    response.headers["Cache-Control"] = "no-store"
    return response


# ==========================================
# AUTHENTICATION HELPERS
# ==========================================
def is_logged_in():
    return session.get("authenticated") is True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def camera_key_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied_key = request.headers.get("X-Camera-Key", "")

        if not hmac.compare_digest(supplied_key, CAMERA_API_KEY):
            return jsonify({"error": "Invalid camera key"}), 401

        return view(*args, **kwargs)
    return wrapped


def csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = session.get("csrf_token", "")
        supplied = (
            request.headers.get("X-CSRF-Token", "")
            or request.form.get("csrf", "")
        )

        if (
            not expected
            or not supplied
            or not hmac.compare_digest(expected, supplied)
        ):
            return jsonify({"error": "Invalid CSRF token"}), 400

        return view(*args, **kwargs)
    return wrapped


# ==========================================
# LOGIN / LOGOUT
# ==========================================
@app.route("/", methods=["GET", "POST"])
def index():
    error = None

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        username_ok = hmac.compare_digest(
            username, DASHBOARD_USERNAME
        )
        password_ok = hmac.compare_digest(
            password, DASHBOARD_PASSWORD
        )

        if username_ok and password_ok:
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))

        error = "Invalid username or password."

    authenticated = is_logged_in()

    if authenticated and not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)

    return render_template(
        "index.html",
        authenticated=authenticated,
        csrf_token=session.get("csrf_token", ""),
        error=error,
    )


@app.route("/logout", methods=["POST"])
@login_required
@csrf_required
def logout():
    session.clear()
    return redirect(url_for("index"))


# ==========================================
# ESP32-CAM UPLOAD ENDPOINT
# ==========================================
@app.route("/api/frame", methods=["POST"])
@camera_key_required
def receive_frame():
    global latest_frame, latest_frame_time

    content_type = request.headers.get("Content-Type", "")
    if content_type.split(";")[0].strip().lower() != "image/jpeg":
        return jsonify({"error": "Content-Type must be image/jpeg"}), 415

    content_length = request.content_length
    if content_length is None or content_length <= 0:
        return jsonify({"error": "Empty frame"}), 400

    if content_length > MAX_FRAME_BYTES:
        return jsonify({"error": "Frame too large"}), 413

    client_ip = request.remote_addr or "unknown"
    now = time.monotonic()

    with frame_lock:
        previous = last_upload_by_ip.get(client_ip, 0)

        if now - previous < MIN_UPLOAD_INTERVAL_SECONDS:
            return jsonify({"error": "Upload rate limit"}), 429

        last_upload_by_ip[client_ip] = now

    frame = request.get_data(cache=False)

    if not frame:
        return jsonify({"error": "Empty frame"}), 400

    if len(frame) > MAX_FRAME_BYTES:
        return jsonify({"error": "Frame too large"}), 413

    # Basic JPEG marker validation
    if len(frame) < 4 or not (
        frame.startswith(b"\xff\xd8")
        and frame.endswith(b"\xff\xd9")
    ):
        return jsonify({"error": "Invalid JPEG data"}), 400

    with frame_lock:
        latest_frame = frame
        latest_frame_time = datetime.now(timezone.utc)

    return jsonify({
        "ok": True,
        "bytes": len(frame),
        "received_at": latest_frame_time.isoformat(),
    }), 200


# ==========================================
# GET LATEST FRAME FOR AUTHENTICATED DASHBOARD
# ==========================================
@app.route("/api/frame/latest", methods=["GET"])
@login_required
def get_latest_frame():
    with frame_lock:
        frame = latest_frame

    if frame is None:
        return jsonify({"error": "No frame received yet"}), 404

    response = make_response(frame)
    response.headers["Content-Type"] = "image/jpeg"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ==========================================
# CAMERA / SERVER STATUS
# ==========================================
@app.route("/api/status", methods=["GET"])
@login_required
def api_status():
    with frame_lock:
        received_at = latest_frame_time

    connected = False
    last_frame_received = None

    if received_at is not None:
        age = (
            datetime.now(timezone.utc) - received_at
        ).total_seconds()

        connected = age <= 15
        last_frame_received = received_at.isoformat()

    return jsonify({
        "server": "online",
        "camera": "connected" if connected else "waiting",
        "last_frame_received": last_frame_received,
    })


# ==========================================
# ROBOT TEST COMMANDS
# NOTE: This does not control real motors.
# ==========================================
@app.route("/api/robot/command", methods=["POST"])
@login_required
@csrf_required
def robot_command():
    global robot_last_command, robot_last_command_time

    data = request.get_json(silent=True) or {}
    command = str(data.get("command", "")).upper()

    allowed = {
        "FORWARD",
        "BACKWARD",
        "LEFT",
        "RIGHT",
        "STOP",
    }

    if command not in allowed:
        return jsonify({"error": "Invalid command"}), 400

    robot_last_command = command
    robot_last_command_time = datetime.now(timezone.utc).isoformat()

    return jsonify({
        "ok": True,
        "command": robot_last_command,
        "message": "Test command recorded. No physical hardware is connected.",
        "timestamp": robot_last_command_time,
    })


@app.route("/api/robot/status", methods=["GET"])
@login_required
def robot_status():
    return jsonify({
        "command": robot_last_command,
        "timestamp": robot_last_command_time,
        "hardware_connected": False,
    })


# ==========================================
# ERROR HANDLERS
# ==========================================
@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Request exceeds maximum frame size"}), 413


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint not found"}), 404
    return "Page not found", 404


# ==========================================
# START SERVER
# ==========================================
if __name__ == "__main__":
    print("ESP32 Remote CCTV server starting...")
    print(f"Local address: http://{HOST}:{PORT}")
    print(f"Maximum JPEG size: {MAX_FRAME_BYTES} bytes")
    print(f"Minimum upload interval: {MIN_UPLOAD_INTERVAL_SECONDS}s")

    serve(app, host=HOST, port=PORT, threads=8)
