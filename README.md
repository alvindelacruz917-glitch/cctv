# ESP32-CAM Remote CCTV (Windows + Flask + Waitress)

This project uploads JPEG snapshots from an AI Thinker ESP32-CAM to a Windows laptop. The laptop keeps only the latest frame in memory. A password-protected responsive dashboard refreshes the latest image automatically. The robot buttons are a safe API demonstration only; they do not drive hardware.

## Requirements
- Windows 10/11 laptop with Python 3.10+ and internet
- AI Thinker ESP32-CAM with working PSRAM, USB-to-serial programmer, stable 5 V supply
- Arduino IDE with Espressif ESP32 board support and `esp32-camera` (included with Espressif's board package)
- 2.4 GHz Wi-Fi for ESP32-CAM
- `cloudflared` for remote access

## 1. Install on Windows
Open PowerShell:

```powershell
mkdir "$HOME\Desktop\ESP32-Remote-CCTV"
```
Extract this project folder to `C:\Users\YOUR_USER\Desktop\ESP32-Remote-CCTV`, then:

```powershell
cd "$HOME\Desktop\ESP32-Remote-CCTV"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Generate separate random values with these commands and paste the results into `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Use the first for `FLASK_SECRET_KEY`, the second for `DASHBOARD_PASSWORD`, and the third for `CAMERA_API_KEY`. Set a private `DASHBOARD_USERNAME`. Never share `.env` or commit it to source control. Keep `HOST=127.0.0.1`.

For local testing, keep `SESSION_COOKIE_SECURE=false`. Start the app:

```powershell
python app.py
```
Open `http://127.0.0.1:5000`. Log in with the username/password in `.env`. The camera panel will wait until a frame arrives.

## 2. Test upload endpoint locally
This creates a tiny valid JPEG in a temporary file and uploads it using your actual camera API key. Run in a second PowerShell window from the project folder after activating `.venv`:

```powershell
$env:CAMERA_API_KEY = (Select-String '^CAMERA_API_KEY=' .env).Line.Split('=',2)[1]
@'
import os, re, urllib.request, urllib.error
from pathlib import Path
# A tiny JPEG test image, decoded from base64.
import base64
jpeg = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9k=')
req = urllib.request.Request('http://127.0.0.1:5000/api/frame', data=jpeg, headers={'Content-Type':'image/jpeg','X-Camera-Key':os.environ['CAMERA_API_KEY']}, method='POST')
try:
    print(urllib.request.urlopen(req, timeout=10).read().decode())
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode())
'@ | python -
Remove-Item Env:\CAMERA_API_KEY
```
A 200 response means the endpoint accepted the test JPEG. If the test image is rejected, use the ESP32 camera upload as the definitive frame test. You can also test the auth boundary by sending a request without `X-Camera-Key`; it should return HTTP 401.

## 3. Remote access with Cloudflare Quick Tunnel (testing only)
Install `cloudflared` using Cloudflare's official Windows instructions. Keep the Flask app running in one terminal. Open a second PowerShell window and run:

```powershell
cloudflared tunnel --url http://localhost:5000
```
Copy the generated `https://<random>.trycloudflare.com` URL. Open it on your phone using mobile data (turn Wi-Fi off for the test). Quick Tunnel URLs are temporary and change when restarted; Cloudflare describes them as testing/development only. Anyone who gets the URL can reach the login page, so use a strong password and do not publish the URL. If available in your installed `cloudflared` version, you can restrict access by email with `--allowed-mail your-email@example.com`.

For a stable hostname, create a named Cloudflare Tunnel and published application route in Cloudflare Zero Trust. A hostname under a domain you control is needed for a stable custom hostname. Map the public hostname to `http://localhost:5000`. No inbound router port-forward is required. Keep the tunnel process running.

When using HTTPS publicly, set `SESSION_COOKIE_SECURE=true` in `.env`, restart `app.py`, and use only the HTTPS public URL. This flag means the browser sends the login cookie only over HTTPS; local HTTP login may no longer work until set back to false for local-only testing. The tunnel encrypts browser-to-Cloudflare traffic and Cloudflare-to-tunnel traffic. The laptop origin is HTTP bound to loopback; do not bind Waitress to `0.0.0.0` or expose port 5000 directly.

## 4. Configure the ESP32-CAM
1. In Arduino IDE, install Espressif's `esp32` board package.
2. Select **AI Thinker ESP32-CAM** (or the matching board profile) and a suitable upload port.
3. Open `esp32_cam/esp32_cam.ino`.
4. Set `WIFI_SSID`, `WIFI_PASSWORD`, `SERVER_URL` and `CAMERA_API_KEY`. `SERVER_URL` must be the complete URL ending in `/api/frame`, for example `https://your-current-host.trycloudflare.com/api/frame`.
5. The sketch validates TLS using the embedded ISRG Root X1 and Google Trust Services Root R1 trust anchors. Public certificates can change. Before use, inspect the certificate chain for your actual tunnel hostname and verify the root certificate/fingerprint from an independent trusted source. If it chains to a different root, add that authentic root PEM to `ROOT_CA`. Never use `setInsecure()` and do not disable hostname/certificate validation. Certificate and clock errors are reported in Serial Monitor.
6. Upload firmware and open Serial Monitor at 115200 baud. The board needs a stable supply and PSRAM. It connects to Wi-Fi, waits for system time for TLS validation, captures 320x240 JPEG images and uploads approximately every two seconds.
7. The `CAMERA_API_KEY` in the sketch must exactly match `.env`. Keep the sketch private because it contains the Wi-Fi password and upload key.

Quick Tunnel hostnames change on restart, so update `SERVER_URL` and re-upload the firmware if the hostname changes. For a stable setup, use a named tunnel and your stable hostname.

## Endpoints
- `POST /api/frame` — ESP32 upload; `Content-Type: image/jpeg`, `X-Camera-Key: <CAMERA_API_KEY>`
- `GET /api/frame/latest` — login required, returns latest JPEG
- `GET /api/status` — login required, returns server/camera status
- `POST /api/robot/command` — login + CSRF required; JSON `{"command":"STOP"}` etc.; records only
- `GET /api/robot/status` — login required

## Security notes
- Change dashboard password and rotate the camera API key if exposed. Update `.env`, update firmware with the new key, and restart both.
- `SESSION_COOKIE_SECURE=true` for remote HTTPS use. Never share `.env`, Wi-Fi password, tunnel token, or API key.
- Image bytes stay in RAM and are overwritten by the next accepted frame. Restarting the server clears the frame.
- The basic rate limiter is process-memory based and keys off the peer address; it is not a substitute for a production WAF or persistent abuse controls.
- Quick Tunnels are not intended for production. Prefer a named tunnel and Cloudflare Access policy for stable use.
- Robot command endpoint is not wired to motors. Real motor control must include a controller, motor driver, hardware-specific firmware, command authorization, a command lease/deadman timeout, and automatic stop on timeout or disconnect. Do not connect motors until that fail-safe is implemented and tested.

## Troubleshooting
- **401 upload:** `X-Camera-Key` is missing or differs from `CAMERA_API_KEY` in `.env`.
- **403 robot/logout:** missing or stale CSRF token; refresh dashboard and retry.
- **413:** JPEG exceeds `MAX_FRAME_BYTES`; lower resolution/quality size or raise the cap cautiously (maximum 2 MB).
- **415:** upload content type must be `image/jpeg`.
- **429:** uploads are arriving too quickly; increase firmware interval or adjust `MIN_UPLOAD_INTERVAL_SECONDS` (minimum 0.1 seconds).
- **500:** inspect the laptop terminal traceback; check `.env` values and dependencies.
- **Timeout / no frames:** check Wi-Fi, internet, exact tunnel URL, server process, and matching API key. Confirm `SERVER_URL` ends in `/api/frame`.
- **TLS certificate error:** check ESP32 time/NTP, hostname spelling, and the real certificate chain. Add the correct authentic root CA; do not bypass TLS verification.
- **Camera init failure:** choose AI Thinker ESP32-CAM, check ribbon cable orientation, PSRAM setting, stable 5 V power and camera module seating.
- **Phone can't reach dashboard:** keep both app and tunnel processes running; test with mobile data. Quick Tunnel URL changes on restart.
- **No upload after internet outage:** firmware retries Wi-Fi and resumes upload; verify NTP and certificate validity after reconnect.

## Verification checklist
- [ ] `python app.py` starts Waitress and local login works.
- [ ] Dashboard responds at `http://127.0.0.1:5000`.
- [ ] Upload without API key gets 401.
- [ ] ESP32 serial monitor shows Wi-Fi connected and HTTP 200 on uploads.
- [ ] Dashboard image and timestamp update.
- [ ] HTTPS tunnel URL opens on phone with Wi-Fi disabled.
- [ ] Wrong dashboard password is rejected.
- [ ] Power-cycle or briefly disconnect Wi-Fi; uploads resume when connection returns.

Remote operation is not confirmed until these checks pass on your hardware and network.
