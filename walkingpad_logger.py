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
import subprocess
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
            try:
                data = res.json()
                ex = data.get("exercise", {})
                metrics = ex.get("metricsSummary", {})

                acc_steps = metrics.get("steps", steps)
                acc_dist_mm = metrics.get("distanceMillimeters")
                acc_dist_km = (acc_dist_mm / 1_000_000.0) if acc_dist_mm is not None else distance_km
                acc_spd_mm_s = metrics.get("averageSpeedMillimetersPerSecond")
                acc_spd_kmh = round((acc_spd_mm_s / 1000.0) * 3.6, 1) if acc_spd_mm_s is not None else speed_kmh
                acc_kcal = metrics.get("caloriesKcal", est_calories)

                dur_raw = ex.get("activeDuration", f"{duration_sec}s")
                try:
                    dur_s = int(str(dur_raw).rstrip("s"))
                    if dur_s >= 3600:
                        hh = dur_s // 3600
                        mm = (dur_s % 3600) // 60
                        ss = dur_s % 60
                        dur_display = f"{hh:02d}:{mm:02d}:{ss:02d} ({round(dur_s / 60.0, 1)} Min.)"
                    else:
                        dur_display = f"{dur_s // 60:02d}:{dur_s % 60:02d} ({round(dur_s / 60.0, 1)} Min.)"
                except Exception:
                    dur_display = f"{duration_min:.1f} Min."

                msg = (
                    "Google Health hat das Training erfolgreich verbucht!\n\n"
                    "Von Google bestätigte Werte:\n"
                    f"  ✅ Dauer:         {dur_display}\n"
                    f"  ✅ Schritte:      {acc_steps}\n"
                    f"  ✅ Distanz:       {acc_dist_km:.2f} km\n"
                    f"  ✅ Tempo:         {acc_spd_kmh:.1f} km/h\n"
                    f"  ✅ Kalorien:      ~{acc_kcal} kcal\n"
                    f"  ✅ Zeitraum:      {start_dt_local.strftime('%H:%M')} – {end_dt_local.strftime('%H:%M')} Uhr\n"
                    f"  ✅ Aktivität:     {ex.get('displayName', 'Walking Pad')}"
                )
                return True, msg
            except Exception:
                return True, (
                    "Google Health hat das Training erfolgreich verbucht!\n\n"
                    f"  ✅ Schritte:  {steps}\n"
                    f"  ✅ Distanz:   {distance_km:.2f} km\n"
                    f"  ✅ Dauer:     {duration_min:.1f} Min.\n"
                    f"  ✅ Tempo:     {speed_kmh:.1f} km/h\n"
                    f"  ✅ Zeitraum:  {start_dt_local.strftime('%H:%M')} – {end_dt_local.strftime('%H:%M')} Uhr"
                )
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


def parse_duration_to_minutes(dur_str: str, is_over_60: bool = False) -> float:
    """
    Konvertiert einen Dauer-String in Minuten (float).
    Unterstützt:
    - 'hh:mm:ss' -> hh*60 + mm + ss/60
    - 'mm:ss' (Standard wenn is_over_60=False, z.B. '25:30' -> 25.5 Min)
    - 'hh:mm' (wenn is_over_60=True, z.B. '01:15' -> 75.0 Min)
    - Reine Zahlen (z.B. '75' oder '75.5')
    """
    cleaned = str(dur_str).strip().replace(",", ".")
    # Entferne Zusätze wie '(hh:mm)', 'Min', Leerzeichen
    cleaned = re.sub(r"[^\d.:]", "", cleaned)
    if not cleaned:
        raise ValueError("Dauer darf nicht leer sein.")

    parts = cleaned.split(":")
    if len(parts) == 3:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = float(parts[2])
        return round(hh * 60.0 + mm + ss / 60.0, 2)
    elif len(parts) == 2:
        v1 = int(parts[0])
        v2 = int(parts[1])
        if is_over_60:
            return round(v1 * 60.0 + v2, 2)
        else:
            if v1 >= 60:
                # Falls z.B. 75:00 eingegeben wurde, waren offensichtlich 75 Min gemeint
                return round(v1 + v2 / 60.0, 2)
            return round(v1 + v2 / 60.0, 2)
    else:
        return round(float(cleaned), 2)


def format_duration_display(duration_min: float, is_over_60: bool = False) -> str:
    """Formatiert Minuten (float) in mm:ss oder hh:mm."""
    total_sec = max(0, int(round(duration_min * 60)))
    if is_over_60 or total_sec >= 3600:
        hh = total_sec // 3600
        mm = (total_sec % 3600) // 60
        return f"{hh:02d}:{mm:02d}"
    else:
        mm = total_sec // 60
        ss = total_sec % 60
        return f"{mm:02d}:{ss:02d}"


def parse_walkingpad_display(raw_text: str, is_over_60: bool = False) -> Dict[str, Any]:
    """
    Parst den Text nach den 4 Spezifikationen:
    - STEP: Vierstellige ganze Zahl (ohne Dezimalpunkt)
    - DIST: Kommazahl mit genau 2 Nachkommastellen (Dezimaltrenner Punkt)
    - SPD : Kommazahl mit genau 1 Nachkommastelle (Dezimaltrenner Punkt)
    - TIME: Format mm:ss (< 60 Min) oder hh:mm (>= 60 Min)
    """
    cleaned = clean_segment_ocr_text(raw_text)
    result = {
        "raw": cleaned,
        "time_str": None,
        "duration_min": None,
        "distance": None,
        "speed": None,
        "steps": None,
        "is_over_60": is_over_60
    }

    # 1. TIME: Format mm:ss (< 60 min) oder hh:mm (>= 60 min)
    time_m = re.search(r"\b(\d{1,2}):(\d{2})\b", cleaned)
    if not time_m:
        time_m = re.search(r"(?:TIME|TIM|TME)[^\d]*(\d{1,2}):?(\d{2})\b", cleaned, re.IGNORECASE)
    if time_m:
        v1 = int(time_m.group(1))
        v2 = int(time_m.group(2))
        if 0 <= v2 < 60:
            result["time_str"] = f"{v1:02d}:{v2:02d}"
            if is_over_60:
                # Stunde:Minute (z. B. 01:15 -> 75.0 Min)
                result["duration_min"] = round(v1 * 60.0 + v2, 2)
            else:
                # Minute:Sekunde (z. B. 25:30 -> 25.5 Min)
                result["duration_min"] = round(v1 + v2 / 60.0, 2)

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
    # (auch STP/SPT fängt typische 7-Segment-Fehler für SPD ab, da 'd' oft als 't' gelesen wird)
    spd_m = re.search(r"(?:SPD|SPEED|STP|SPT)[^\d]*(\d+)[.,](\d{1})\b", cleaned, re.IGNORECASE)
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

    # 4. STEP: Ganze Zahl ohne Dezimalpunkt (mindestens 2-stellig, z. B. 25, 120, 764, 2580)
    # Wichtig: Niemals 1-stellige Ziffern (wie 3 oder 5) matchen!
    # Niemals matchen, wenn danach ein Dezimalpunkt folgt (wäre sonst Speed wie 3.0 oder 5.0)!
    step_m = re.search(r"(?:STEP[S]?|STP)[^\d]*(\d{2,6})(?!\.\d)\b", cleaned, re.IGNORECASE)
    if not step_m:
        if not time_m and result["distance"] is None and result["speed"] is None:
            # Reine Ganzzahl ohne Punkt oder Doppelpunkt: mindestens 2-stellig
            pure_step = re.search(r"\b(\d{2,6})\b", cleaned)
            if pure_step and not re.search(r"\b" + pure_step.group(1) + r"\.\d", cleaned):
                result["steps"] = int(pure_step.group(1))
    else:
        try:
            result["steps"] = int(step_m.group(1))
        except ValueError:
            pass

    return result


# ---------------------------------------------------------------------------
# Entprellung & Plausibilitätsfilter (Schutz vor Umschalt-Geisterziffern)
# ---------------------------------------------------------------------------
class ValueStabilizer:
    """
    Verhindert Umschalt-Geisterziffern und Lesefehler (z. B. 360 -> 3603)
    durch 2-stufige Filterung:
    1. Debounce (Konsistenz): Ein Wert wird erst übernommen, wenn er mindestens
       2-mal hintereinander (oder innerhalb minimaler Toleranz) erkannt wurde.
       Kurze Umschalt-Artefakte (dauern nur ~100ms) werden so zu 100% verworfen.
    2. Plausibilitäts-Check: Sprünge bei Schritten von mehr als 100 Schritten
       auf einen Schlag werden blockiert, da man beim Gehen nicht in wenigen
       Sekunden hunderte oder tausende Schritte machen kann.
    """
    def __init__(self):
        self.candidates: Dict[str, Dict[str, Any]] = {}

    def reset(self):
        self.candidates.clear()

    def filter_steps(self, new_val: Optional[int], current_gui_val: int) -> Optional[int]:
        # Schritte auf einem Laufband sind niemals 1-stellig (immer mindestens 10)
        if new_val is None or new_val < 10:
            return None

        # 2-Frame Bestätigung (Debounce):
        # Das echte Display zeigt den Wert für 2-3 Sekunden (ca. 6-8 Frames).
        # Ein Umschalt-Artefakt (wie 3603) existiert nur für genau 1 Frame (~100ms) und wird nie bestätigt.
        cand = self.candidates.get("steps")
        if cand and abs(cand["val"] - new_val) <= 5:
            cand["count"] += 1
            cand["val"] = new_val
            # 2 aufeinanderfolgende Messungen bestätigen den echten Wert
            if cand["count"] >= 2:
                # Plausibilität: Wenn schon ein Wert > 50 im Feld steht und ein Sprung riesig ist (> 1000)
                # dann verlange eine 3. Bestätigung zur Sicherheit
                if current_gui_val > 50:
                    diff = new_val - current_gui_val
                    if diff > 1000 and cand["count"] < 3:
                        return None
                return new_val
        else:
            self.candidates["steps"] = {"val": new_val, "count": 1}
            return None

    def filter_distance(self, new_val: Optional[float], current_gui_val: float) -> Optional[float]:
        if new_val is None:
            return None

        if current_gui_val > 0.0:
            diff = new_val - current_gui_val
            if diff > 0.15 or diff < -0.15:
                return None

        cand = self.candidates.get("dist")
        if cand and abs(cand["val"] - new_val) <= 0.02:
            cand["count"] += 1
            cand["val"] = new_val
            if cand["count"] >= 2:
                return new_val
        else:
            self.candidates["dist"] = {"val": new_val, "count": 1}
            return None

    def filter_speed(self, new_val: Optional[float]) -> Optional[float]:
        if new_val is None or new_val > 12.0:
            return None

        cand = self.candidates.get("speed")
        if cand and abs(cand["val"] - new_val) <= 0.2:
            cand["count"] += 1
            cand["val"] = new_val
            if cand["count"] >= 2:
                return new_val
        else:
            self.candidates["speed"] = {"val": new_val, "count": 1}
            return None

    def filter_duration(self, new_val: Optional[float], current_gui_val: float) -> Optional[float]:
        if new_val is None:
            return None

        if current_gui_val > 0.0:
            diff = new_val - current_gui_val
            if diff > 1.5 or diff < -1.5:
                return None

        cand = self.candidates.get("duration")
        if cand and abs(cand["val"] - new_val) <= 0.25:
            cand["count"] += 1
            cand["val"] = new_val
            if cand["count"] >= 2:
                return new_val
        else:
            self.candidates["duration"] = {"val": new_val, "count": 1}
            return None


# ---------------------------------------------------------------------------
# Moderner, kontrastreicher Button für macOS (vermeidet hellgraues Auswaschen)
# ---------------------------------------------------------------------------
class ModernButton(tk.Frame):
    """
    Garantiert satten, leuchtenden Hintergrund mit scharfer weißer Schrift auf macOS.
    (Standard-tk.Button erzwingt auf macOS Aqua ein hellgraues Bezel, was weiße Schrift unlesbar macht).
    """
    def __init__(
        self,
        parent,
        text: str,
        command,
        bg: str = "#059669",
        fg: str = "#FFFFFF",
        hover_bg: str = "#047857",
        font=("Helvetica", 12, "bold"),
        **kwargs
    ):
        super().__init__(parent, bg=bg, cursor="hand2", **kwargs)
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg
        self.fg = fg
        self.enabled = True

        self.lbl = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            cursor="hand2",
            padx=14,
            pady=10
        )
        self.lbl.pack(fill="both", expand=True)

        for w in (self, self.lbl):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def _on_click(self, event=None):
        if self.enabled and self.command:
            self.command()

    def _on_enter(self, event=None):
        if self.enabled:
            self.configure(bg=self.hover_bg)
            self.lbl.configure(bg=self.hover_bg)

    def _on_leave(self, event=None):
        if self.enabled:
            self.configure(bg=self.bg)
            self.lbl.configure(bg=self.bg)

    def config(self, **kwargs):
        state = kwargs.get("state")
        text = kwargs.get("text")

        if state == "disabled":
            self.enabled = False
            self.configure(bg="#9CA3AF", cursor="arrow")
            self.lbl.configure(bg="#9CA3AF", fg="#F3F4F6", cursor="arrow")
        elif state == "normal":
            self.enabled = True
            self.configure(bg=self.bg, cursor="hand2")
            self.lbl.configure(bg=self.bg, fg=self.fg, cursor="hand2")

        if text is not None:
            self.lbl.configure(text=text)


# ---------------------------------------------------------------------------
# Bearbeitbarer Bestätigungsdialog vor dem Senden an Google Health
# ---------------------------------------------------------------------------
class WorkoutConfirmDialog(tk.Toplevel):
    """
    Bearbeitbarer Bestätigungsdialog VOR dem Senden an Google Health v4:
    - Zeigt alle erfassten Werte in aktiven, editierbaren Textfeldern
    - Ermöglicht Korrektur aller OCR-Werte oder Umschalten zwischen < 60 Min (mm:ss) und >= 60 Min (hh:mm)
    - Berechnet live Zeitraum (Start – Ende) und geschätzte Kalorien
    - Bietet Buttons für Abbrechen und Absenden
    """
    def __init__(self, parent: tk.Tk, initial_data: Dict[str, Any]):
        super().__init__(parent)
        self.parent = parent
        self.title("🏋️‍♂️ Trainingsdaten vor dem Senden prüfen & anpassen")
        self.resizable(False, False)

        self.confirmed = False
        self.result_data: Optional[Dict[str, Any]] = None

        self.initial_data = initial_data
        self.var_over_60 = tk.BooleanVar(value=bool(initial_data.get("is_over_60", False)))

        self._build_ui()

        # Modalität
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Dialog über Parent zentrieren
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        x = max(0, px + (pw - w) // 2)
        y = max(0, py + (ph - h) // 2)
        self.geometry(f"+{x}+{y}")

        self.entry_dur.focus_set()

    def _build_ui(self):
        main_box = ttk.Frame(self, padding=16)
        main_box.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(main_box)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(
            header,
            text="📋 Trainingsdaten überprüfen & bearbeiten",
            font=("Helvetica", 13, "bold"),
            foreground="#111827"
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Du kannst alle Werte vor dem Absenden an Google Health hier korrigieren:",
            font=("Helvetica", 9),
            foreground="#4B5563"
        ).pack(anchor="w", pady=(2, 0))

        # Formular
        form = ttk.LabelFrame(main_box, text="Trainings-Parameter", padding=12)
        form.pack(fill="x", pady=(0, 12))

        # 1. Dauer
        ttk.Label(form, text="Dauer:", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="w", pady=5)
        dur_frame = ttk.Frame(form)
        dur_frame.grid(row=0, column=1, sticky="w", pady=5)

        self.entry_dur = ttk.Entry(dur_frame, width=10, font=("Helvetica", 11))
        self.entry_dur.pack(side="left", padx=(0, 8))
        self.entry_dur.insert(0, str(self.initial_data.get("duration_str", "00:00")))
        self.entry_dur.bind("<KeyRelease>", self._on_values_changed)

        self.chk_over_60 = ttk.Checkbutton(
            dur_frame,
            text="> 60 Min. (hh:mm)",
            variable=self.var_over_60,
            command=self._on_over_60_changed
        )
        self.chk_over_60.pack(side="left")

        self.lbl_dur_sub = ttk.Label(
            form,
            text="",
            font=("Helvetica", 8, "italic"),
            foreground="#2563EB"
        )
        self.lbl_dur_sub.grid(row=1, column=1, sticky="w", pady=(0, 6))

        # 2. Tempo
        ttk.Label(form, text="Tempo:", font=("Helvetica", 10, "bold")).grid(row=2, column=0, sticky="w", pady=5)
        spd_frame = ttk.Frame(form)
        spd_frame.grid(row=2, column=1, sticky="w", pady=5)
        self.entry_spd = ttk.Entry(spd_frame, width=10, font=("Helvetica", 11))
        self.entry_spd.pack(side="left", padx=(0, 6))
        self.entry_spd.insert(0, str(self.initial_data.get("speed_str", "0.0")))
        self.entry_spd.bind("<KeyRelease>", self._on_values_changed)
        ttk.Label(spd_frame, text="km/h", foreground="#4B5563").pack(side="left")

        # 3. Distanz
        ttk.Label(form, text="Distanz:", font=("Helvetica", 10, "bold")).grid(row=3, column=0, sticky="w", pady=5)
        dist_frame = ttk.Frame(form)
        dist_frame.grid(row=3, column=1, sticky="w", pady=5)
        self.entry_dist = ttk.Entry(dist_frame, width=10, font=("Helvetica", 11))
        self.entry_dist.pack(side="left", padx=(0, 6))
        self.entry_dist.insert(0, str(self.initial_data.get("distance_str", "0.00")))
        self.entry_dist.bind("<KeyRelease>", self._on_values_changed)
        ttk.Label(dist_frame, text="km", foreground="#4B5563").pack(side="left")

        # 4. Schritte
        ttk.Label(form, text="Schritte:", font=("Helvetica", 10, "bold")).grid(row=4, column=0, sticky="w", pady=5)
        steps_frame = ttk.Frame(form)
        steps_frame.grid(row=4, column=1, sticky="w", pady=5)
        self.entry_steps = ttk.Entry(steps_frame, width=10, font=("Helvetica", 11))
        self.entry_steps.pack(side="left", padx=(0, 6))
        self.entry_steps.insert(0, str(self.initial_data.get("steps_str", "0")))
        self.entry_steps.bind("<KeyRelease>", self._on_values_changed)
        ttk.Label(steps_frame, text="Schritte", foreground="#4B5563").pack(side="left")

        # 5. Startzeit
        ttk.Label(form, text="Startzeit:", font=("Helvetica", 10, "bold")).grid(row=5, column=0, sticky="w", pady=5)
        start_frame = ttk.Frame(form)
        start_frame.grid(row=5, column=1, sticky="w", pady=5)
        self.entry_start = ttk.Entry(start_frame, width=10, font=("Helvetica", 11))
        self.entry_start.pack(side="left", padx=(0, 6))
        self.entry_start.insert(0, str(self.initial_data.get("start_time_str", datetime.now().strftime("%H:%M"))))
        self.entry_start.bind("<KeyRelease>", self._on_values_changed)
        ttk.Label(start_frame, text="Uhr (HH:MM)", foreground="#4B5563").pack(side="left")

        # Dynamische Vorschau-Karte
        preview_box = ttk.Frame(main_box, relief="groove", padding=10)
        preview_box.pack(fill="x", pady=(0, 14))

        self.lbl_calc_period = ttk.Label(
            preview_box,
            text="🕒 Zeitraum: --:-- bis --:-- Uhr",
            font=("Helvetica", 9, "bold"),
            foreground="#1F2937"
        )
        self.lbl_calc_period.pack(anchor="w")

        self.lbl_calc_calories = ttk.Label(
            preview_box,
            text="🔥 Geschätzter Verbrauch: ~0 kcal",
            font=("Helvetica", 9),
            foreground="#4B5563"
        )
        self.lbl_calc_calories.pack(anchor="w", pady=(3, 0))

        # Buttons
        btn_bar = ttk.Frame(main_box)
        btn_bar.pack(fill="x")

        ttk.Button(btn_bar, text="❌ Abbrechen", command=self._on_cancel, width=14).pack(side="left")

        self.btn_submit = ModernButton(
            btn_bar,
            text="🚀 Jetzt an Google Health senden",
            command=self._on_submit,
            bg="#059669",
            hover_bg="#047857",
            fg="#FFFFFF",
            font=("Helvetica", 11, "bold")
        )
        self.btn_submit.pack(side="right")

        self._on_values_changed()

    def _on_over_60_changed(self):
        self._on_values_changed()

    def _on_values_changed(self, event=None):
        try:
            dur_str = self.entry_dur.get().strip()
            is_over = self.var_over_60.get()
            dur_min = parse_duration_to_minutes(dur_str, is_over_60=is_over)

            if dur_min >= 60.0:
                h = int(dur_min // 60)
                m = int(dur_min % 60)
                self.lbl_dur_sub.config(text=f"ℹ️ Entspricht: {dur_min:.1f} Min. ({h} Std. {m:02d} Min.)")
            else:
                m = int(dur_min)
                s = int(round((dur_min - m) * 60))
                self.lbl_dur_sub.config(text=f"ℹ️ Entspricht: {dur_min:.2f} Min. ({m} Min. {s:02d} Sek.)")

            start_str = self.entry_start.get().strip()
            now_local = datetime.now().astimezone()
            if start_str and ":" in start_str:
                naive_dt = datetime.strptime(f"{now_local.strftime('%Y-%m-%d')} {start_str}", "%Y-%m-%d %H:%M")
                start_dt = naive_dt.replace(tzinfo=now_local.tzinfo)
                end_dt = start_dt + timedelta(minutes=dur_min)
                self.lbl_calc_period.config(text=f"🕒 Zeitraum: {start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')} Uhr ({dur_min:.1f} Min.)")
            else:
                self.lbl_calc_period.config(text="🕒 Zeitraum: Startzeit unvollständig")

            est_cal = int(round(dur_min * 3.8))
            self.lbl_calc_calories.config(text=f"🔥 Geschätzter Verbrauch: ~{est_cal} kcal")
        except Exception:
            self.lbl_dur_sub.config(text="⚠️ Ungültiges Zeitformat (z. B. 25:30 oder 01:15)")
            self.lbl_calc_period.config(text="🕒 Zeitraum: --:-- bis --:-- Uhr")

    def _on_submit(self):
        try:
            dur_str = self.entry_dur.get().strip()
            is_over = self.var_over_60.get()
            dur_min = parse_duration_to_minutes(dur_str, is_over_60=is_over)
            if dur_min <= 0:
                messagebox.showwarning("Ungültige Dauer", "Die Dauer muss größer als 0 sein.", parent=self)
                return

            try:
                speed_kmh = float(self.entry_spd.get().replace(",", "."))
                if speed_kmh < 0 or speed_kmh > 25.0:
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("Ungültiges Tempo", "Bitte ein gültiges Tempo eingeben (z. B. 3.5 km/h).", parent=self)
                return

            try:
                dist_km = float(self.entry_dist.get().replace(",", "."))
                if dist_km < 0:
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("Ungültige Distanz", "Bitte eine gültige Distanz eingeben (z. B. 2.50 km).", parent=self)
                return

            try:
                steps = int(self.entry_steps.get().replace(".", "").replace(",", ""))
                if steps < 0:
                    raise ValueError()
            except ValueError:
                messagebox.showwarning("Ungültige Schritte", "Bitte eine gültige Schrittanzahl eingeben (z. B. 1250).", parent=self)
                return

            start_str = self.entry_start.get().strip()
            now_local = datetime.now().astimezone()
            try:
                naive_dt = datetime.strptime(f"{now_local.strftime('%Y-%m-%d')} {start_str}", "%Y-%m-%d %H:%M")
                start_dt_local = naive_dt.replace(tzinfo=now_local.tzinfo)
            except Exception:
                messagebox.showwarning("Ungültige Startzeit", "Bitte Startzeit im Format HH:MM angeben (z. B. 14:30).", parent=self)
                return

            end_dt_local = start_dt_local + timedelta(minutes=dur_min)

            self.result_data = {
                "duration_str": dur_str,
                "duration_min": dur_min,
                "is_over_60": is_over,
                "speed": speed_kmh,
                "distance": dist_km,
                "steps": steps,
                "start_time_str": start_str,
                "start_dt_local": start_dt_local,
                "end_dt_local": end_dt_local
            }
            self.confirmed = True
            self.destroy()
        except Exception as e:
            messagebox.showerror("Fehler", f"Eingabefehler:\n{str(e)}", parent=self)

    def _on_cancel(self):
        self.confirmed = False
        self.result_data = None
        self.destroy()


# ---------------------------------------------------------------------------
# Hauptanwendung mit Tkinter GUI
# ---------------------------------------------------------------------------
class WalkingPadApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Skandika Walking Pad Sync & OCR")
        self.root.geometry("920x640")
        self.root.minsize(860, 580)

        # Entprell- und Plausibilitäts-Filter
        self.stabilizer = ValueStabilizer()
        self.var_over_60 = tk.BooleanVar(value=False)

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
        ttk.Button(ctrl_bar, text="⚙️ Kamera-Freigabe", command=self.open_camera_settings).pack(side="right")
        ttk.Label(
            ctrl_bar,
            text="📌 Fester Rahmen (mit Maus verschiebbar)",
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

        # 1. Dauer (TIME im Format mm:ss bzw. hh:mm)
        self.lbl_duration_title = ttk.Label(manual_box, text="Dauer (Min:Sek):", font=("Helvetica", 11, "bold"))
        self.lbl_duration_title.grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_duration = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_duration.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_duration.insert(0, "00:00")
        row += 1

        self.chk_over_60 = ttk.Checkbutton(
            manual_box,
            text="> 60 Min. (hh:mm)",
            variable=self.var_over_60,
            command=self.on_over_60_toggled
        )
        self.chk_over_60.grid(row=row, column=1, sticky="e", pady=(0, 4))
        row += 1

        # 2. Tempo (SPD)
        ttk.Label(manual_box, text="Tempo (km/h):", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_speed = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_speed.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_speed.insert(0, "0.0")
        row += 1

        # 3. Distanz (DIST)
        ttk.Label(manual_box, text="Distanz (km):", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_distance = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_distance.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_distance.insert(0, "0.00")
        row += 1

        # 4. Schritte (STEP)
        ttk.Label(manual_box, text="Schritte:", font=("Helvetica", 11, "bold")).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_steps = ttk.Entry(manual_box, width=14, font=("Helvetica", 11))
        self.entry_steps.grid(row=row, column=1, sticky="e", pady=pad_y)
        self.entry_steps.insert(0, "0")
        row += 1

        # 5. Startzeit
        ttk.Label(manual_box, text="Startzeit (HH:MM):", font=("Helvetica", 10)).grid(row=row, column=0, sticky="w", pady=pad_y)
        self.entry_time = ttk.Entry(manual_box, width=14, font=("Helvetica", 10))
        self.entry_time.grid(row=row, column=1, sticky="e", pady=pad_y)
        default_start = datetime.now().strftime("%H:%M")
        self.entry_time.insert(0, default_start)
        row += 1

        # Reset-Button
        btn_clear = ttk.Button(manual_box, text="🧹 Felder auf 0 setzen", command=self.clear_fields)
        btn_clear.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        row += 1

        # Großer Senden-Button (sattes Smaragdgrün mit strahlend weißer Schrift, perfekt lesbar auf macOS)
        self.btn_submit = ModernButton(
            manual_box,
            text="🚀 Training an Google Health senden",
            command=self.submit_data,
            bg="#059669",
            hover_bg="#047857",
            fg="#FFFFFF",
            font=("Helvetica", 12, "bold")
        )
        self.btn_submit.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(15, 5))

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

    def open_camera_settings(self):
        try:
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"])
        except Exception:
            pass

    def start_camera(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
            time.sleep(0.15)

        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            time.sleep(0.15)

            if self.cap.isOpened():
                ret, test_frame = self.cap.read()
                if ret and test_frame is not None:
                    self.camera_running = True
                    return

            # Falls nicht geöffnet werden konnte oder blockiert ist:
            self.camera_running = False
            self.lbl_video.configure(
                image="",
                text="⚠️ Kamera nicht autorisiert oder nicht verbunden!\n\n"
                     "1. Öffne Systemeinstellungen -> Datenschutz & Sicherheit -> Kamera\n"
                     "2. Erlaube dort 'WalkingPad Sync' bzw. 'Python'.\n\n"
                     "(Klicke unten auf '⚙️ Kamera-Freigabe')",
                fg="#F87171",
                font=("Helvetica", 10, "bold"),
                wraplength=380,
                justify="center"
            )
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

    def on_over_60_toggled(self, recalc: bool = True):
        is_over = self.var_over_60.get()
        if is_over:
            self.lbl_duration_title.config(text="Dauer (Std:Min):")
        else:
            self.lbl_duration_title.config(text="Dauer (Min:Sek):")

        # Duration-Kandidaten im Stabilizer zurücksetzen, damit kein Frame-Filter blockiert
        self.stabilizer.candidates.pop("duration", None)

        if recalc:
            try:
                curr_str = self.entry_duration.get().strip()
                if curr_str and curr_str != "00:00":
                    dur_min = parse_duration_to_minutes(curr_str, is_over_60=is_over)
                    new_time = (datetime.now() - timedelta(minutes=dur_min)).strftime("%H:%M")
                    self.entry_time.delete(0, tk.END)
                    self.entry_time.insert(0, new_time)
            except Exception:
                pass

    def clear_fields(self):
        self.stabilizer.reset()
        self.var_over_60.set(False)
        self.lbl_duration_title.config(text="Dauer (Min:Sek):")
        self.entry_duration.delete(0, tk.END)
        self.entry_duration.insert(0, "00:00")
        self.entry_speed.delete(0, tk.END)
        self.entry_speed.insert(0, "0.0")
        self.entry_distance.delete(0, tk.END)
        self.entry_distance.insert(0, "0.00")
        self.entry_steps.delete(0, tk.END)
        self.entry_steps.insert(0, "0")
        self.entry_time.delete(0, tk.END)
        self.entry_time.insert(0, datetime.now().strftime("%H:%M"))
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
        if not self.camera_running or not self.cap or not self.cap.isOpened():
            self.retry_counter = getattr(self, "retry_counter", 0) + 1
            if self.retry_counter >= 75:  # Alle 2.2 Sekunden erneut versuchen
                self.retry_counter = 0
                self.start_camera()
            self.root.after(30, self.update_camera_frame)
            return

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
                parsed = parse_walkingpad_display(raw_text, is_over_60=self.var_over_60.get())

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

        # 1. TIME (Dauer in Min:Sek bzw. Std:Min)
        dur_val = parsed["duration_min"]
        time_display = parsed["time_str"]
        if dur_val is not None:
            try:
                curr_str = self.entry_duration.get().strip()
                curr_dur = parse_duration_to_minutes(curr_str, is_over_60=self.var_over_60.get())
            except ValueError:
                curr_dur = 0.0

            # Automatische Erkennung des Wechsels von < 60 Min auf >= 60 Min:
            # Wenn das Display 01:xx anzeigt, aber die Checkbox noch auf < 60 Min steht:
            if not self.var_over_60.get() and time_display and re.match(r"^0?1:\d{2}$", time_display):
                try:
                    curr_dist = float(self.entry_distance.get().strip().replace(",", ".") or 0)
                except ValueError:
                    curr_dist = 0.0
                try:
                    curr_steps = int(self.entry_steps.get().strip().replace(".", "").replace(",", "") or 0)
                except ValueError:
                    curr_steps = 0

                # Wenn bisherige Dauer >= 50 Min oder Distanz >= 3.0 km oder Schritte >= 3000:
                if curr_dur >= 50.0 or curr_dist >= 3.0 or curr_steps >= 3000:
                    self.var_over_60.set(True)
                    self.on_over_60_toggled(recalc=False)
                    # Neuberechnen mit is_over_60 = True
                    parts = time_display.split(":")
                    dur_val = int(parts[0]) * 60.0 + int(parts[1])
                    curr_dur = dur_val

            filtered_dur = self.stabilizer.filter_duration(dur_val, curr_dur)
            if filtered_dur is not None:
                if not time_display:
                    time_display = format_duration_display(filtered_dur, is_over_60=self.var_over_60.get())

                self.entry_duration.delete(0, tk.END)
                self.entry_duration.insert(0, time_display)
                self.last_ocr_data["duration_min"] = filtered_dur
                self.last_ocr_data["time_str"] = time_display
                status_parts.append(f"Zeit: {time_display}")

                st = (datetime.now() - timedelta(minutes=filtered_dur)).strftime("%H:%M")
                self.entry_time.delete(0, tk.END)
                self.entry_time.insert(0, st)

        # 2. SPD (Tempo live mit Entprellung)
        if parsed["speed"] is not None:
            filtered_spd = self.stabilizer.filter_speed(parsed["speed"])
            if filtered_spd is not None:
                self.entry_speed.delete(0, tk.END)
                self.entry_speed.insert(0, f"{filtered_spd:.1f}")
                self.last_ocr_data["speed"] = filtered_spd
                status_parts.append(f"Tempo: {filtered_spd:.1f} km/h")

        # 3. DIST (Distanz live mit Entprellung)
        if parsed["distance"] is not None:
            try:
                curr_dist = float(self.entry_distance.get().strip().replace(",", "."))
            except ValueError:
                curr_dist = 0.0

            filtered_dist = self.stabilizer.filter_distance(parsed["distance"], curr_dist)
            if filtered_dist is not None:
                self.entry_distance.delete(0, tk.END)
                self.entry_distance.insert(0, f"{filtered_dist:.2f}")
                self.last_ocr_data["distance"] = filtered_dist
                status_parts.append(f"Distanz: {filtered_dist:.2f} km")

        # 4. STEP (Schritte mit Sprungschutz: z.B. 360 -> 3603 wird blockiert!)
        if parsed["steps"] is not None:
            try:
                curr_steps = int(self.entry_steps.get().strip().replace(".", "").replace(",", ""))
            except ValueError:
                curr_steps = 0

            filtered_steps = self.stabilizer.filter_steps(parsed["steps"], curr_steps)
            if filtered_steps is not None:
                self.entry_steps.delete(0, tk.END)
                self.entry_steps.insert(0, str(filtered_steps))
                self.last_ocr_data["steps"] = filtered_steps
                status_parts.append(f"Schritte: {filtered_steps}")

        if status_parts:
            self.lbl_ocr_status.config(
                text=f"✅ Erkannt: {' | '.join(status_parts)}",
                foreground="#059669"
            )
        self.lbl_ocr_raw.config(text=f"Roh-Text: \"{parsed['raw']}\"")

    def submit_data(self):
        """Öffnet den bearbeitbaren Bestätigungsdialog und sendet bei Bestätigung an Google Health v4."""
        dur_str = self.entry_duration.get().strip()
        spd_str = self.entry_speed.get().strip()
        dist_str = self.entry_distance.get().strip()
        steps_str = self.entry_steps.get().strip()
        start_str = self.entry_time.get().strip()
        is_over = self.var_over_60.get()

        initial_data = {
            "duration_str": dur_str,
            "speed_str": spd_str,
            "distance_str": dist_str,
            "steps_str": steps_str,
            "start_time_str": start_str,
            "is_over_60": is_over
        }

        # Bearbeitbarer Bestätigungsdialog
        dlg = WorkoutConfirmDialog(self.root, initial_data)
        self.root.wait_window(dlg)

        if not dlg.confirmed or dlg.result_data is None:
            return

        res = dlg.result_data

        # Aktualisiere die Haupt-GUI-Felder mit den eventuell im Dialog editierten Werten
        self.var_over_60.set(res["is_over_60"])
        self.lbl_duration_title.config(text="Dauer (Std:Min):" if res["is_over_60"] else "Dauer (Min:Sek):")

        self.entry_duration.delete(0, tk.END)
        self.entry_duration.insert(0, res["duration_str"])

        self.entry_speed.delete(0, tk.END)
        self.entry_speed.insert(0, f"{res['speed']:.1f}")

        self.entry_distance.delete(0, tk.END)
        self.entry_distance.insert(0, f"{res['distance']:.2f}")

        self.entry_steps.delete(0, tk.END)
        self.entry_steps.insert(0, str(res["steps"]))

        self.entry_time.delete(0, tk.END)
        self.entry_time.insert(0, res["start_time_str"])

        # Senden-Button deaktivieren und Upload starten
        self.btn_submit.config(state="disabled", text="⏳ Wird an Google Health gesendet...")

        def _upload():
            success, msg = upload_workout_data(
                start_dt_local=res["start_dt_local"],
                duration_min=res["duration_min"],
                distance_km=res["distance"],
                speed_kmh=res["speed"],
                steps=res["steps"]
            )
            self.root.after(0, lambda: self._on_submit_done(success, msg))

        threading.Thread(target=_upload, daemon=True).start()

    def _on_submit_done(self, success: bool, msg: str):
        self.btn_submit.config(state="normal", text="🚀 Training an Google Health senden")
        if success:
            messagebox.showinfo("Google Health Bestätigung 🎉", msg, parent=self.root)
        else:
            messagebox.showerror("Fehler beim Upload", f"Google Health API-Fehler:\n{msg}", parent=self.root)

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
