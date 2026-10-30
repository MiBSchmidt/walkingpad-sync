#!/usr/bin/env python3
"""
Skandika Walking Pad Sync & OCR Workout Logger
---------------------------------------------
Stabile Display-Erfassung mit 7-Segment-Weißfilterung,
stillem (nicht springendem) Erkennungsbereich und direkter Übertragung
in die Textfelder sowie Synchronisierung mit Google Health v4.
"""

import sys
import os
import re
import json
import time
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# AVFoundation-Autorisierung auf macOS
os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "0"

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np
import pytesseract
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ---------------------------------------------------------------------------
# Basis-Pfade und Google-Health-Konfiguration
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent.parent / "Resources"
    if not (BASE_DIR / "client_secret.json").exists():
        BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).parent.resolve()

CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "token.json"
CONFIG_FILE = BASE_DIR / "camera_config.json"

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
]
API_BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
DEFAULT_STRIDE_M = 0.62  # ca. 62 cm Schrittlänge beim Walking Pad

KNOWN_CAMERAS = [
    (0, "Kamera 0 (Laufband Webcam)"),
    (1, "Kamera 1 (FaceTime HD-Kamera)"),
    (2, "Kamera 2 (Weitere Kamera)")
]

# Tesseract Executable lokalisieren
for candidate in [
    shutil.which("tesseract"),
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
    "/usr/bin/tesseract"
]:
    if candidate and Path(candidate).exists():
        pytesseract.pytesseract.tesseract_cmd = str(candidate)
        break


# ---------------------------------------------------------------------------
# Google Health API Authentifizierung & Upload
# ---------------------------------------------------------------------------
def get_credentials() -> Credentials:
    """Lädt oder erneuert die OAuth-Credentials für Google Health v4."""
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"client_secret.json nicht gefunden in:\n{BASE_DIR}\n"
                    "Bitte erstelle oder kopiere die Google OAuth-Client-Secret-Datei dorthin."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def upload_workout_data(
    start_dt_local: datetime,
    duration_min: float,
    distance_km: float,
    speed_kmh: float,
    steps: int
) -> Tuple[bool, str]:
    """Sendet das Training an die Google Health API v4."""
    try:
        creds = get_credentials()
        duration_sec = max(1, int(duration_min * 60))
        end_dt_local = start_dt_local + timedelta(seconds=duration_sec)

        utc_offset_sec = int(start_dt_local.astimezone().utcoffset().total_seconds())
        offset_str = f"{utc_offset_sec}s"

        start_utc_str = start_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc_str = end_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        distance_mm = int(round(distance_km * 1_000_000))
        speed_mm_per_s = round((speed_kmh / 3.6) * 1000, 2)
        est_calories = int(round(duration_min * 3.8))

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json"
        }

        exercise_url = f"{API_BASE_URL}/exercise/dataPoints"
        exercise_payload = {
            "exercise": {
                "interval": {
                    "startTime": start_utc_str,
                    "startUtcOffset": offset_str,
                    "endTime": end_utc_str,
                    "endUtcOffset": offset_str
                },
                "exerciseType": "WALKING",
                "displayName": "Skandika Walking Pad",
                "activeDuration": f"{duration_sec}s",
                "metricsSummary": {
                    "steps": int(steps),
                    "distanceMillimeters": distance_mm,
                    "averageSpeedMillimetersPerSecond": speed_mm_per_s,
                    "caloriesKcal": est_calories
                }
            }
        }

        res = requests.post(exercise_url, headers=headers, json=exercise_payload, timeout=15)
        if res.status_code == 401 and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            headers["Authorization"] = f"Bearer {creds.token}"
            res = requests.post(exercise_url, headers=headers, json=exercise_payload, timeout=15)

        if res.ok:
            return True, f"Training übertragen:\n• {steps} Schritte\n• {distance_km:.2f} km\n• {duration_min:.1f} Min.\n• {speed_kmh:.1f} km/h"
        else:
            return False, f"API-Fehler ({res.status_code}):\n{res.text}"
    except Exception as e:
        return False, f"Verbindungsfehler: {str(e)}"


# ---------------------------------------------------------------------------
# Bildvorverarbeitung für 7-Segment-Displays (Weißfilter & Segment-Verbindung)
# ---------------------------------------------------------------------------
def preprocess_segment_display(roi_bgr: np.ndarray) -> Optional[np.ndarray]:
    """
    Optimiert leuchtend weiße 7-Segment-LED-Ziffern für Tesseract:
    - Isoliert automatisch die leuchtenden Segmente im Erfassungsbereich
    - Passt die Box dynamisch an (Toleranz für leicht verschobenes Laufband)
    - Morphologisches Schließen verbindet die Segment-Balken zu soliden Ziffern
    - Invertierung: Schwarze Schrift auf weißem Grund mit Rand-Polsterung
    """
    if roi_bgr is None or roi_bgr.size == 0:
        return None

    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    max_val = int(np.max(gray))
    if max_val < 90:
        return None

    # Dynamische Schwelle für leuchtend weiße Ziffern
    thresh_val = max(130, int(max_val * 0.65))
    _, mask = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)

    # Konturen der leuchtenden Ziffernteile finden
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    valid_boxes = [cv2.boundingRect(c) for c in cnts if cv2.contourArea(c) > 20]

    if valid_boxes:
        min_x = max(0, min(b[0] for b in valid_boxes) - 15)
        min_y = max(0, min(b[1] for b in valid_boxes) - 15)
        max_x = min(roi_bgr.shape[1], max(b[0] + b[2] for b in valid_boxes) + 15)
        max_y = min(roi_bgr.shape[0], max(b[1] + b[3] for b in valid_boxes) + 15)
        target = gray[min_y:max_y, min_x:max_x]
    else:
        target = gray

    th, tw = target.shape[:2]
    if th < 120:
        scale = max(2.0, 140.0 / float(th))
        target = cv2.resize(target, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    _, sub_bin = cv2.threshold(target, thresh_val, 255, cv2.THRESH_BINARY)

    # Morphologisches Schließen verbindet die Segment-Lücken
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(sub_bin, cv2.MORPH_CLOSE, kernel)

    # Invertieren für Tesseract (schwarze Ziffern auf weißem Grund)
    inv = cv2.bitwise_not(closed)

    # Weißer Rand um das Bild für beste Erkennungsrate bei Tesseract
    bordered = cv2.copyMakeBorder(inv, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return bordered


def clean_segment_ocr_text(raw_text: str) -> str:
    """Korrigiert typische 7-Segment-Fehlinterpretationen von Tesseract."""
    cleaned = raw_text.replace(",", ".").replace(";", ":").strip()
    replacements = {
        'O': '0', 'o': '0', 'D': '0',
        'I': '1', 'l': '1', '|': '1', '!': '1', 'i': '1',
        'Z': '2',
        'S': '5', 's': '5',
        'B': '8',
        'g': '9', 'q': '9'
    }
    for old, new in replacements.items():
        cleaned = re.sub(r'(?<![A-Za-z])' + re.escape(old) + r'(?![A-Za-z])', new, cleaned)
    return cleaned


def parse_walkingpad_display(raw_text: str) -> Dict[str, Any]:
    """
    Parst den Text nach den 4 Spezifikationen:
    - STEP: Vierstellige ganze Zahl
    - DIST: Kommazahl mit genau 2 Nachkommastellen (Dezimaltrenner Punkt)
    - SPD : Kommazahl mit genau 1 Nachkommastelle (Dezimaltrenner Punkt)
    - TIME: Format mm:ss (oder TIME gefolgt von Ziffern)
    """
    cleaned = clean_segment_ocr_text(raw_text)
    result = {
        "raw": cleaned,
        "time_str": None,
        "duration_min": None,
        "distance": None,
        "speed": None,
        "steps": None
    }

    # 1. TIME: Format mm:ss oder mit "TIME" davor
    time_m = re.search(r"\b(\d{1,2}):(\d{2})\b", cleaned)
    if not time_m:
        time_m = re.search(r"(?:TIME|TIM|TME)[^\d]*(\d{1,2}):?(\d{2})\b", cleaned, re.IGNORECASE)
    if time_m:
        mm = int(time_m.group(1))
        ss = int(time_m.group(2))
        if 0 <= ss < 60:
            result["time_str"] = f"{mm:02d}:{ss:02d}"
            result["duration_min"] = round(mm + ss / 60.0, 2)

    # 2. DIST: mit Punkt/Komma ODER mit "DIST" davor
    dist_m = re.search(r"(?:DIST|DST)[^\d]*(\d+)[.,](\d{2})\b", cleaned, re.IGNORECASE)
    if not dist_m:
        dist_m = re.search(r"\b(\d+)\.(\d{2})\b", cleaned)
    if not dist_m:
        dist_no_dot = re.search(r"(?:DIST|DST)[^\d]*(\d{1,2})(\d{2})\b", cleaned, re.IGNORECASE)
        if dist_no_dot:
            result["distance"] = round(float(f"{dist_no_dot.group(1)}.{dist_no_dot.group(2)}"), 2)
    elif dist_m:
        try:
            result["distance"] = round(float(f"{dist_m.group(1)}.{dist_m.group(2)}"), 2)
        except ValueError:
            pass

    # 3. SPD: mit Punkt/Komma ODER mit "SPD" davor
    spd_m = re.search(r"(?:SPD|SPEED)[^\d]*(\d+)[.,](\d{1})\b", cleaned, re.IGNORECASE)
    if not spd_m:
        spd_no_dot = re.search(r"(?:SPD|SPEED)[^\d]*(\d{1,2})(\d{1})\b", cleaned, re.IGNORECASE)
        if spd_no_dot:
            cand = float(f"{spd_no_dot.group(1)}.{spd_no_dot.group(2)}")
            if cand <= 14.0:
                result["speed"] = cand
    else:
        try:
            cand = float(f"{spd_m.group(1)}.{spd_m.group(2)}")
            if cand <= 14.0:
                result["speed"] = cand
        except ValueError:
            pass

    if result["speed"] is None:
        spd_candidates = re.findall(r"\b(\d+\.\d{1})\b", cleaned)
        for cand_str in spd_candidates:
            if not re.search(r"\b" + re.escape(cand_str) + r"\d\b", cleaned):
                try:
                    val = float(cand_str)
                    if val <= 14.0:
                        result["speed"] = val
                        break
                except ValueError:
                    pass

    # 4. STEP: Vierstellige ganze Zahl
    step_m = re.search(r"(?:STEP[S]?|STP)[^\d]*(\d{3,5})\b", cleaned, re.IGNORECASE)
    if not step_m:
        if not time_m and result["distance"] is None:
            pure_step = re.search(r"\b(\d{4})\b", cleaned)
            if pure_step:
                result["steps"] = int(pure_step.group(1))
    else:
        try:
            result["steps"] = int(step_m.group(1))
        except ValueError:
            pass

    return result


# ---------------------------------------------------------------------------
# Hauptanwendung mit Tkinter GUI
# ---------------------------------------------------------------------------
class WalkingPadApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Skandika Walking Pad Sync & OCR")
        self.root.geometry("920x640")
        self.root.minsize(860, 580)

        # Standard-Kamera: Kamera 0
        self.camera_index = 0
        # Stabiler, fester Display-Bereich (relative Koordinaten)
        # Großzügig um das Display zentriert, damit leichte Verschiebungen des Bandes abgedeckt sind!
        self.roi = [0.40, 0.20, 0.68, 0.50]
        self.load_config()

        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_running = False
        self.current_frame: Optional[np.ndarray] = None
        self.ocr_in_progress = False

        # Letzte OCR-Werte
        self.last_ocr_data = {
            "time_str": None,
            "duration_min": None,
            "distance": None,
            "speed": None,
            "steps": None,
            "raw": "-"
        }

        # Maus-Interaktion für ROI
        self.drag_start = None
        self.drag_current = None

        self.build_gui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Kamera starten
        self.start_camera()

        # Update-Schleife
        self.root.after(30, self.update_camera_frame)

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.camera_index = int(data.get("camera_index", 0))
                    roi = data.get("roi")
                    if isinstance(roi, list) and len(roi) == 4:
                        self.roi = [float(v) for v in roi]
            except Exception:
                pass

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "camera_index": self.camera_index,
                    "roi": self.roi
                }, f, indent=2)
        except Exception as e:
            print("Fehler beim Speichern der Config:", e)

    def reset_roi(self):
        # Großzügiger, stabiler Standardbereich
        self.roi = [0.40, 0.20, 0.68, 0.50]
        self.save_config()

    def build_gui(self):
        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill="both", expand=True)

        left_col = ttk.Frame(main_frame)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))

        right_col = ttk.Frame(main_frame, width=380)
        right_col.pack(side="right", fill="both", expand=False)

        # ==================== LINKE SPALTE: Webcam & Fester Rahmen ====================
        cam_header = ttk.Frame(left_col)
        cam_header.pack(fill="x", pady=(0, 5))

        ttk.Label(cam_header, text="📹 Kamera (180° gedreht, Weißfilter)", font=("Helvetica", 12, "bold")).pack(side="left")

        self.cam_names = [name for _, name in KNOWN_CAMERAS]
        default_str = self.cam_names[0]
        for idx, name in KNOWN_CAMERAS:
            if idx == self.camera_index:
                default_str = name
                break

        self.combo_cam = ttk.Combobox(cam_header, values=self.cam_names, state="readonly", width=28)
        self.combo_cam.set(default_str)
        self.combo_cam.pack(side="right")
        self.combo_cam.bind("<<ComboboxSelected>>", self.on_camera_combo_change)

        # Video-Container mit fester Größe (verhindert Aufblähen)
        self.video_w = 460
        self.video_h = 300
        video_container = tk.Frame(left_col, width=self.video_w, height=self.video_h, bg="#111827")
        video_container.pack(fill="both", expand=False)
        video_container.pack_propagate(False)

        self.lbl_video = tk.Label(video_container, bg="#111827", text="Initialisiere Kamera...", fg="#9CA3AF")
        self.lbl_video.pack(fill="both", expand=True)

        # Maus-Interaktion für ROI
        self.lbl_video.bind("<ButtonPress-1>", self.on_mouse_down)
        self.lbl_video.bind("<B1-Motion>", self.on_mouse_drag)
        self.lbl_video.bind("<ButtonRelease-1>", self.on_mouse_up)

        # Steuerung unter dem Video
        ctrl_bar = ttk.Frame(left_col)
        ctrl_bar.pack(fill="x", pady=4)

        ttk.Button(ctrl_bar, text="🎯 Bereich zentrieren", command=self.reset_roi).pack(side="left")
        ttk.Label(
            ctrl_bar,
            text="📌 Fester Rahmen (mit Maus verschiebbar, springt nicht)",
            foreground="#059669",
            font=("Helvetica", 9, "bold")
        ).pack(side="left", padx=8)

        # Live-OCR-Debug-Box
        ocr_box = ttk.LabelFrame(left_col, text="🔍 Live aus Display abgelesen (7-Segment Filter)", padding=8)
        ocr_box.pack(fill="x", pady=(4, 0))

        preview_row = ttk.Frame(ocr_box)
        preview_row.pack(fill="x", pady=2)

        self.lbl_thresh_preview = tk.Label(
            preview_row,
            bg="#111827",
            text="Warten auf\nSignal...",
            fg="#9CA3AF",
            font=("Helvetica", 9),
            width=20,
            height=3
        )
        self.lbl_thresh_preview.pack(side="left", padx=(0, 10))

        info_sub = ttk.Frame(preview_row)
        info_sub.pack(side="left", fill="both", expand=True)

        self.lbl_ocr_status = ttk.Label(
            info_sub,
            text="Warten auf Display-Signal...",
            font=("Helvetica", 10, "bold"),
            foreground="#2563EB"
        )
        self.lbl_ocr_status.pack(anchor="w", pady=1)

        self.lbl_ocr_raw = ttk.Label(info_sub, text="Roh-Text: \"--\"", font=("Courier", 9), foreground="#4B5563")
        self.lbl_ocr_raw.pack(anchor="w", pady=1)

        # ==================== RECHTE SPALTE: Textfelder & Senden ====================
        manual_box = ttk.LabelFrame(right_col, text="Automatisch befüllte Trainingsdaten", padding=12)
        manual_box.pack(fill="both", expand=True)

        ttk.Label(
            manual_box,
            text="Die Felder füllen sich automatisch, wenn das Display\ndie Werte (TIME, SPD, DIST, STEP) durchschaltet.",
            font=("Helvetica", 9, "italic"),
            foreground="#4B5563"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        row = 1
        pad_y = 6

        # 1. Dauer (TIME im Format mm:ss)
        ttk.Label(manual_box, text="Dauer (Min:Sek):", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_duration = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_duration.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_duration.insert(0, "30:00")
        row += 1

        # 2. Tempo (SPD)
        ttk.Label(manual_box, text="Tempo (km/h):", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_speed = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_speed.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_speed.insert(0, "3.2")
        row += 1

        # 3. Distanz (DIST)
        ttk.Label(manual_box, text="Distanz (km):", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_distance = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_distance.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_distance.insert(0, "1.60")
        row += 1

        # 4. Schritte (STEP)
        ttk.Label(manual_box, text="Schritte:", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_steps = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_steps.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_steps.insert(0, "2580")
        row += 1

        # 5. Startzeit
        ttk.Label(manual_box, text="Startzeit (HH:MM):", font=("Helvetica", 10)).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_time = ttk.Entry(manual_box, width=14, font=("Helvetica", 10))
        self.entry_time.grid(row=row, column=1, sticky="e", pady=pad_y)
        default_start = (datetime.now() - timedelta(minutes=30)).strftime("%H:%M")
        self.entry_time.insert(0, default_start)
        row += 1

        # Reset-Button
        btn_clear = ttk.Button(manual_box, text="🧹 Felder auf 0 setzen", command=self.clear_fields)
        btn_clear.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        row += 1

        # Großer Senden-Button
        self.btn_submit = tk.Button(
            manual_box,
            text="🚀 Training an Google Health senden",
            bg="#10B981",
            fg="white",
            activebackground="#059669",
            activeforeground="white",
            font=("Helvetica", 12, "bold"),
            relief="raised",
            bd=3,
            cursor="hand2",
            command=self.submit_data
        )
        self.btn_submit.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(15, 5), ipady=8)

    def on_camera_combo_change(self, event=None):
        val = self.combo_cam.get()
        try:
            for idx, name in KNOWN_CAMERAS:
                if name == val and idx != self.camera_index:
                    self.camera_index = idx
                    self.save_config()
                    self.start_camera()
                    break
        except Exception as e:
            print("Fehler beim Wechsel der Kamera:", e)

    def start_camera(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
            time.sleep(0.2)

        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            time.sleep(0.2)
            self.camera_running = True
        except Exception as e:
            print("Fehler beim Starten der Kamera:", e)

    def stop_camera(self):
        self.camera_running = False
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def clear_fields(self):
        self.entry_duration.delete(0, tk.END)
        self.entry_duration.insert(0, "00:00")
        self.entry_speed.delete(0, tk.END)
        self.entry_speed.insert(0, "0.0")
        self.entry_distance.delete(0, tk.END)
        self.entry_distance.insert(0, "0.00")
        self.entry_steps.delete(0, tk.END)
        self.entry_steps.insert(0, "0")
        self.last_ocr_data = {"time_str": None, "duration_min": None, "distance": None, "speed": None, "steps": None, "raw": "-"}

    # -----------------------------------------------------------------------
    # Maus-Interaktion für ROI (falls nötig verschiebbar)
    # -----------------------------------------------------------------------
    def on_mouse_down(self, event):
        self.drag_start = (event.x, event.y)
        self.drag_current = None

    def on_mouse_drag(self, event):
        if self.drag_start:
            self.drag_current = (event.x, event.y)

    def on_mouse_up(self, event):
        if not self.drag_start:
            return
        x0, y0 = self.drag_start
        x1, y1 = event.x, event.y
        self.drag_start = None
        self.drag_current = None

        if abs(x1 - x0) >= 15 and abs(y1 - y0) >= 15:
            w = self.video_w
            h = self.video_h
            rx0 = max(0.0, min(1.0, min(x0, x1) / float(w)))
            ry0 = max(0.0, min(1.0, min(y0, y1) / float(h)))
            rx1 = max(0.0, min(1.0, max(x0, x1) / float(w)))
            ry1 = max(0.0, min(1.0, max(y0, y1) / float(h)))
            self.roi = [rx0, ry0, rx1, ry1]
            self.save_config()

    # -----------------------------------------------------------------------
    # Video Frame & OCR
    # -----------------------------------------------------------------------
    def update_camera_frame(self):
        if self.camera_running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                # 180 Grad drehen
                frame = cv2.rotate(frame, cv2.ROTATE_180)
                self.current_frame = frame.copy()

                vw = self.video_w
                vh = self.video_h
                display_frame = cv2.resize(frame, (vw, vh), interpolation=cv2.INTER_LINEAR)

                # FESTER, STABILER RAHMEN (kein Springen!)
                px1 = int(self.roi[0] * vw)
                py1 = int(self.roi[1] * vh)
                px2 = int(self.roi[2] * vw)
                py2 = int(self.roi[3] * vh)

                cv2.rectangle(display_frame, (px1, py1), (px2, py2), (16, 185, 129), 2)
                cv2.putText(
                    display_frame, "Display-Bereich", (px1, max(18, py1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (16, 185, 129), 2
                )

                if self.drag_start and self.drag_current:
                    dx0, dy0 = self.drag_start
                    dx1, dy1 = self.drag_current
                    cv2.rectangle(display_frame, (dx0, dy0), (dx1, dy1), (245, 158, 11), 2)

                rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                self._photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                self.lbl_video.configure(image=self._photo, text="")
                self.lbl_video.image = self._photo

                # Asynchrone OCR auf dem festen Bereich (alle 400ms)
                if not self.ocr_in_progress:
                    fh, fw = frame.shape[:2]
                    fx1 = int(self.roi[0] * fw)
                    fy1 = int(self.roi[1] * fh)
                    fx2 = int(self.roi[2] * fw)
                    fy2 = int(self.roi[3] * fh)
                    fx1, fx2 = max(0, min(fx1, fx2)), min(fw, max(fx1, fx2))
                    fy1, fy2 = max(0, min(fy1, fy2)), min(fh, max(fy1, fy2))

                    if (fx2 - fx1) > 20 and (fy2 - fy1) > 20:
                        crop = frame[fy1:fy2, fx1:fx2].copy()
                        self.ocr_in_progress = True
                        threading.Thread(target=self._run_ocr_worker, args=(crop,), daemon=True).start()

        self.root.after(30, self.update_camera_frame)

    def _run_ocr_worker(self, crop_bgr: np.ndarray):
        try:
            # 7-Segment-Weißfilterung
            thresh = preprocess_segment_display(crop_bgr)
            if thresh is not None:
                # Thumbnail für die Vorschau erstellen (invertiert oder schwarz auf weiß)
                th, tw = thresh.shape[:2]
                scale = min(150.0 / max(1, tw), 50.0 / max(1, th))
                nw, nh = max(10, int(tw * scale)), max(10, int(th * scale))
                t_resized = cv2.resize(thresh, (nw, nh), interpolation=cv2.INTER_NEAREST)
                thresh_photo = ImageTk.PhotoImage(image=Image.fromarray(t_resized))

                # Tesseract OCR mit Ziffern- und Satzzeichen-Whitelist
                custom_config = r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.:SPDTIMEstep"
                raw_text = pytesseract.image_to_string(thresh, config=custom_config).strip()
                parsed = parse_walkingpad_display(raw_text)

                self.root.after(0, lambda: self._apply_ocr_results(parsed, thresh_photo))
        except Exception:
            pass
        finally:
            time.sleep(0.35)
            self.ocr_in_progress = False

    def _apply_ocr_results(self, parsed: Dict[str, Any], thresh_photo=None):
        """Aktualisiert die Textfelder sanft, sobald Werte erkannt werden."""
        if thresh_photo is not None:
            self._thresh_photo = thresh_photo
            self.lbl_thresh_preview.configure(image=thresh_photo, text="")
            self.lbl_thresh_preview.image = thresh_photo

        status_parts = []

        # 1. TIME (Dauer in Min:Sek, z. B. 06:13 oder 25:30)
        if parsed["time_str"] is not None:
            self.entry_duration.delete(0, tk.END)
            self.entry_duration.insert(0, parsed["time_str"])
            self.last_ocr_data["duration_min"] = parsed["duration_min"]
            self.last_ocr_data["time_str"] = parsed["time_str"]
            status_parts.append(f"Zeit: {parsed['time_str']}")

            dur_m = parsed["duration_min"] or 0.0
            st = (datetime.now() - timedelta(minutes=dur_m)).strftime("%H:%M")
            self.entry_time.delete(0, tk.END)
            self.entry_time.insert(0, st)
        elif parsed["duration_min"] is not None:
            mm = int(parsed["duration_min"])
            ss = int(round((parsed["duration_min"] - mm) * 60))
            time_str = f"{mm:02d}:{ss:02d}"
            self.entry_duration.delete(0, tk.END)
            self.entry_duration.insert(0, time_str)
            self.last_ocr_data["duration_min"] = parsed["duration_min"]
            self.last_ocr_data["time_str"] = time_str
            status_parts.append(f"Zeit: {time_str}")

        # 2. SPD
        if parsed["speed"] is not None:
            self.entry_speed.delete(0, tk.END)
            self.entry_speed.insert(0, f"{parsed['speed']:.1f}")
            self.last_ocr_data["speed"] = parsed["speed"]
            status_parts.append(f"Tempo: {parsed['speed']:.1f} km/h")

        # 3. DIST
        if parsed["distance"] is not None:
            self.entry_distance.delete(0, tk.END)
            self.entry_distance.insert(0, f"{parsed['distance']:.2f}")
            self.last_ocr_data["distance"] = parsed["distance"]
            status_parts.append(f"Distanz: {parsed['distance']:.2f} km")

        # 4. STEP
        if parsed["steps"] is not None:
            self.entry_steps.delete(0, tk.END)
            self.entry_steps.insert(0, str(parsed["steps"]))
            self.last_ocr_data["steps"] = parsed["steps"]
            status_parts.append(f"Schritte: {parsed['steps']}")

        if status_parts:
            self.lbl_ocr_status.config(
                text=f"✅ Erkannt: {' | '.join(status_parts)}",
                foreground="#059669"
            )
        self.lbl_ocr_raw.config(text=f"Roh-Text: \"{parsed['raw']}\"")

    def submit_data(self):
        """Liest die Werte aus den Textfeldern und sendet sie an Google Health v4."""
        try:
            dur_str = self.entry_duration.get().strip().replace(",", ".")
            if ":" in dur_str:
                parts = dur_str.split(":")
                mm = int(parts[0])
                ss = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                duration_min = round(mm + ss / 60.0, 2)
            else:
                duration_min = float(dur_str)

            speed_kmh = float(self.entry_speed.get().replace(",", "."))
            distance_km = float(self.entry_distance.get().replace(",", "."))
            steps = int(self.entry_steps.get().replace(".", "").replace(",", ""))

            time_str = self.entry_time.get().strip()
            now_local = datetime.now().astimezone()

            if time_str:
                naive_dt = datetime.strptime(f"{now_local.strftime('%Y-%m-%d')} {time_str}", "%Y-%m-%d %H:%M")
                start_dt_local = naive_dt.replace(tzinfo=now_local.tzinfo)
            else:
                start_dt_local = now_local - timedelta(minutes=duration_min)

            self.btn_submit.config(state="disabled", text="⏳ Wird an Google Health gesendet...")

            def _upload():
                success, msg = upload_workout_data(
                    start_dt_local=start_dt_local,
                    duration_min=duration_min,
                    distance_km=distance_km,
                    speed_kmh=speed_kmh,
                    steps=steps
                )
                self.root.after(0, lambda: self._on_submit_done(success, msg))

            threading.Thread(target=_upload, daemon=True).start()

        except Exception as e:
            messagebox.showerror("Fehler", f"Ungültige Eingabe in den Feldern:\n{str(e)}")

    def _on_submit_done(self, success: bool, msg: str):
        self.btn_submit.config(state="normal", text="🚀 Training an Google Health senden")
        if success:
            messagebox.showinfo("Erfolg 🎉", f"Training erfolgreich an Google Health übertragen!\n\n{msg}")
        else:
            messagebox.showerror("Fehler beim Upload", f"Google Health API-Fehler:\n{msg}")

    def on_close(self):
        self.stop_camera()
        self.root.destroy()


def main():
    try:
        root = tk.Tk()
        app = WalkingPadApp(root)
        root.mainloop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            with open(BASE_DIR / "error.log", "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            pass


if __name__ == "__main__":
    main()
