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
from google.auth.exceptions import RefreshError

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


def get_git_commit_id() -> str:
    """Gibt die kurze Git-Commit-ID (z. B. 50203e2) zurück."""
    commit_file = BASE_DIR / "commit_id.txt"
    if commit_file.exists():
        try:
            val = commit_file.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    return "dev"


# ---------------------------------------------------------------------------
# Google Health API Authentifizierung & Upload
# ---------------------------------------------------------------------------
AUTH_OK = "ok"                      # Token gültig bzw. erfolgreich erneuert
AUTH_LOGIN_REQUIRED = "login"       # Kein/abgelaufener Token -> Browser-Login nötig
AUTH_NO_CLIENT_SECRET = "no_secret" # client_secret.json fehlt
AUTH_OFFLINE = "offline"            # Google nicht erreichbar (kein Internet)


def _save_credentials(creds: Credentials) -> None:
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def try_silent_credentials() -> Tuple[str, Optional[Credentials]]:
    """
    Prüft OHNE Browser, ob ein gültiger Login vorliegt, und erneuert den
    Zugriffstoken bei Bedarf über den Refresh-Token.
    """
    if not CLIENT_SECRETS_FILE.exists():
        return AUTH_NO_CLIENT_SECRET, None

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return AUTH_OK, creds

    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            return AUTH_OK, creds
        except RefreshError:
            # Refresh-Token abgelaufen/widerrufen (z. B. 7-Tage-Limit im
            # OAuth-Testmodus) -> alten Token verwerfen.
            TOKEN_FILE.unlink(missing_ok=True)
        except Exception:
            return AUTH_OFFLINE, None

    return AUTH_LOGIN_REQUIRED, None


def interactive_login() -> Credentials:
    """Öffnet den Browser für die Google-Anmeldung und speichert den Token."""
    if not CLIENT_SECRETS_FILE.exists():
        raise FileNotFoundError(
            f"client_secret.json nicht gefunden in:\n{BASE_DIR}\n"
            "Bitte erstelle oder kopiere die Google OAuth-Client-Secret-Datei dorthin."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
    # access_type=offline + prompt=consent garantiert einen Refresh-Token
    creds = flow.run_local_server(
        port=8080,
        access_type="offline",
        prompt="consent",
        timeout_seconds=300,
        success_message="Anmeldung erfolgreich – du kannst dieses Fenster schließen und zu WalkingPad Sync zurückkehren.",
    )
    _save_credentials(creds)
    return creds


def get_credentials() -> Credentials:
    """Lädt oder erneuert die OAuth-Credentials; meldet sich notfalls neu an."""
    state, creds = try_silent_credentials()
    if state == AUTH_OK and creds is not None:
        return creds
    if state == AUTH_OFFLINE:
        raise ConnectionError("Google ist nicht erreichbar. Bitte Internetverbindung prüfen.")
    return interactive_login()


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
            # Plausibilität: Auf einem Walking Pad läuft niemand 13 Stunden oder 59 Stunden!
            # Wenn v1 > 12 (z. B. 59:54), kann es kein hh:mm sein, sondern ist mm:ss
            if v1 > 12:
                return round(v1 + v2 / 60.0, 2)
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
                # Stunde:Minute (z. B. 01:15 -> 75.0 Min, oder 1:02 -> 62.0 Min)
                if v1 > 12:
                    result["duration_min"] = round(v1 + v2 / 60.0, 2)
                else:
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
            # Erlaube Übergang von < 60 Min auf >= 60 Min (z. B. von 59.9 Min auf 60.0 bis 70.0 Min)
            if current_gui_val >= 50.0 and 60.0 <= new_val <= 75.0:
                pass
            # Normaler Zeitschritt: max 3.5 Minuten vorwärts (Display-Zyklus dauert ~12-15s,
            # bei 1 verpassten Zyklus bis zu 2 Min) und max 1.5 Min rückwärts (Toleranz für minimale Ableseschwankung)
            elif diff > 3.5 or diff < -1.5:
                return None

        cand = self.candidates.get("duration")
        if cand and abs(cand["val"] - new_val) <= 0.35:
            cand["count"] += 1
            cand["val"] = new_val
            if cand["count"] >= 2:
                return new_val
        else:
            self.candidates["duration"] = {"val": new_val, "count": 1}
            return None


# ---------------------------------------------------------------------------
# Design-System: Farben, Schriften & ttk-Styles (dunkles Theme)
# ---------------------------------------------------------------------------
THEME = {
    "bg":        "#0F1216",   # Fensterhintergrund
    "card":      "#181C22",   # Karten
    "tile":      "#20252D",   # Werte-Kacheln / Eingabefelder
    "border":    "#2B323C",
    "text":      "#E8EAED",
    "muted":     "#8D96A3",
    "faint":     "#5C6573",
    "accent":    "#10B981",   # Smaragdgrün
    "accent_hi": "#059669",
    "warn":      "#F59E0B",
    "error":     "#F87171",
    "info":      "#60A5FA",
    "video_bg":  "#0A0C0F",
}
FONT = "Helvetica Neue"


def setup_styles(root: tk.Misc) -> None:
    """Konfiguriert ttk für das dunkle Theme (clam erlaubt freie Farben)."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    t = THEME
    style.configure(
        "Dark.TCheckbutton",
        background=t["card"], foreground=t["text"], font=(FONT, 11),
        indicatorbackground=t["tile"], indicatorforeground=t["accent"],
        focuscolor=t["card"], borderwidth=0,
    )
    style.map(
        "Dark.TCheckbutton",
        background=[("active", t["card"])],
        indicatorbackground=[("selected", t["tile"]), ("active", t["border"])],
    )
    style.configure(
        "Dark.TCombobox",
        fieldbackground=t["tile"], background=t["tile"], foreground=t["text"],
        arrowcolor=t["muted"], bordercolor=t["border"], lightcolor=t["tile"],
        darkcolor=t["tile"], selectbackground=t["tile"], selectforeground=t["text"],
        padding=4,
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", t["tile"])],
        foreground=[("readonly", t["text"])],
        selectbackground=[("readonly", t["tile"])],
        selectforeground=[("readonly", t["text"])],
    )
    root.option_add("*TCombobox*Listbox.background", t["tile"])
    root.option_add("*TCombobox*Listbox.foreground", t["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", t["accent_hi"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", (FONT, 11))


# ---------------------------------------------------------------------------
# Moderner, kontrastreicher Button für macOS (vermeidet hellgraues Auswaschen)
# ---------------------------------------------------------------------------
class ModernButton(tk.Frame):
    """
    Flacher Button mit sattem Hintergrund und Hover-Effekt.
    (Standard-tk.Button erzwingt auf macOS Aqua ein hellgraues Bezel.)
    """
    def __init__(
        self,
        parent,
        text: str,
        command=None,
        bg: str = THEME["accent_hi"],
        fg: str = "#FFFFFF",
        hover_bg: str = THEME["accent"],
        font=(FONT, 13, "bold"),
        padx: int = 14,
        pady: int = 10,
        **kwargs
    ):
        super().__init__(parent, bg=bg, cursor="hand2", **kwargs)
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg
        self.fg = fg
        self.enabled = True

        self.lbl = tk.Label(
            self, text=text, bg=bg, fg=fg, font=font,
            cursor="hand2", padx=padx, pady=pady
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
            self.configure(bg=THEME["border"], cursor="arrow")
            self.lbl.configure(bg=THEME["border"], fg=THEME["muted"], cursor="arrow")
        elif state == "normal":
            self.enabled = True
            self.configure(bg=self.bg, cursor="hand2")
            self.lbl.configure(bg=self.bg, fg=self.fg, cursor="hand2")

        if text is not None:
            self.lbl.configure(text=text)


def ghost_button(parent, text: str, command) -> ModernButton:
    """Dezenter Sekundär-Button."""
    return ModernButton(
        parent, text=text, command=command, bg=THEME["tile"], hover_bg=THEME["border"],
        fg=THEME["text"], font=(FONT, 11), padx=10, pady=6
    )


def make_card(parent, title: Optional[str] = None) -> Tuple[tk.Frame, tk.Frame]:
    """Erzeugt eine Karte mit optionaler Überschrift. Gibt (Karte, Inhalt) zurück."""
    card = tk.Frame(parent, bg=THEME["card"], highlightthickness=1,
                    highlightbackground=THEME["border"])
    body = tk.Frame(card, bg=THEME["card"])
    body.pack(fill="both", expand=True, padx=14, pady=12)
    if title:
        tk.Label(body, text=title.upper(), bg=THEME["card"], fg=THEME["muted"],
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 8))
    return card, body


def make_entry(parent, font_size: int = 13, width: int = 10, bg: str = THEME["tile"]) -> tk.Entry:
    """Flaches, dunkles Eingabefeld mit grünem Fokus-Rahmen."""
    return tk.Entry(
        parent, width=width, font=(FONT, font_size), bg=bg, fg=THEME["text"],
        insertbackground=THEME["text"], relief="flat", bd=0,
        highlightthickness=1, highlightbackground=THEME["border"],
        highlightcolor=THEME["accent"], selectbackground=THEME["accent_hi"],
        selectforeground="#FFFFFF",
    )


class MetricTile(tk.Frame):
    """Große Kachel für einen Trainingswert (editierbar) mit kurzem Aufleuchten bei OCR-Updates."""
    def __init__(self, parent, title: str, unit: str, initial: str):
        super().__init__(parent, bg=THEME["tile"], highlightthickness=1,
                         highlightbackground=THEME["border"])
        inner = tk.Frame(self, bg=THEME["tile"])
        inner.pack(fill="both", expand=True, padx=14, pady=10)

        head = tk.Frame(inner, bg=THEME["tile"])
        head.pack(fill="x")
        tk.Label(head, text=title.upper(), bg=THEME["tile"], fg=THEME["muted"],
                 font=(FONT, 10, "bold")).pack(side="left")
        self.lbl_unit = tk.Label(head, text=unit, bg=THEME["tile"], fg=THEME["faint"],
                                 font=(FONT, 10))
        self.lbl_unit.pack(side="right")

        self.entry = tk.Entry(
            inner, width=7, font=(FONT, 30, "bold"), bg=THEME["tile"], fg=THEME["text"],
            insertbackground=THEME["text"], relief="flat", bd=0, highlightthickness=0,
            selectbackground=THEME["accent_hi"], selectforeground="#FFFFFF",
        )
        self.entry.pack(fill="x", pady=(4, 0))
        self.entry.insert(0, initial)

        self._flash_job = None
        # Enter/Esc beendet die Bearbeitung, damit die Live-Erkennung wieder schreiben darf
        for key in ("<Return>", "<Escape>"):
            self.entry.bind(key, lambda e: self.winfo_toplevel().focus_set())
        self.entry.bind("<FocusIn>", lambda e: self.configure(highlightbackground=THEME["accent"]))
        self.entry.bind("<FocusOut>", lambda e: self.configure(highlightbackground=THEME["border"]))

    def set_unit(self, unit: str):
        self.lbl_unit.configure(text=unit)

    def flash(self):
        """Kurzes grünes Aufleuchten, wenn ein neuer Wert erkannt wurde."""
        if self.focus_get() is self.entry:
            return
        self.configure(highlightbackground=THEME["accent"])
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(
            600, lambda: self.configure(highlightbackground=THEME["border"]))


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
        self.title("Training prüfen")
        self.configure(bg=THEME["card"])
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
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.bind("<Return>", lambda e: self._on_submit())

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
        t = THEME
        main_box = tk.Frame(self, bg=t["card"])
        main_box.pack(fill="both", expand=True, padx=22, pady=20)

        # Header
        tk.Label(main_box, text="Training prüfen", bg=t["card"], fg=t["text"],
                 font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(main_box, text="Korrigiere die Werte bei Bedarf, bevor sie an Google Health gehen.",
                 bg=t["card"], fg=t["muted"], font=(FONT, 11)).pack(anchor="w", pady=(2, 16))

        # Formular
        form = tk.Frame(main_box, bg=t["card"])
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        def row_label(r: int, text: str):
            tk.Label(form, text=text, bg=t["card"], fg=t["muted"],
                     font=(FONT, 12)).grid(row=r, column=0, sticky="w", padx=(0, 18), pady=6)

        def unit_label(parent, text: str):
            tk.Label(parent, text=text, bg=t["card"], fg=t["faint"],
                     font=(FONT, 11)).pack(side="left", padx=(8, 0))

        def entry_row(r: int, label: str, initial: str, unit: Optional[str]) -> Tuple[tk.Frame, tk.Entry]:
            row_label(r, label)
            fr = tk.Frame(form, bg=t["card"])
            fr.grid(row=r, column=1, sticky="w", pady=6)
            ent = make_entry(fr, font_size=14, width=9)
            ent.pack(side="left", ipady=4)
            ent.insert(0, initial)
            ent.bind("<KeyRelease>", self._on_values_changed)
            if unit:
                unit_label(fr, unit)
            return fr, ent

        # 1. Dauer
        dur_frame, self.entry_dur = entry_row(0, "Dauer", str(self.initial_data.get("duration_str", "00:00")), None)
        self.chk_over_60 = ttk.Checkbutton(
            dur_frame, text="über 60 Min. (hh:mm)", variable=self.var_over_60,
            command=self._on_over_60_changed, style="Dark.TCheckbutton"
        )
        self.chk_over_60.pack(side="left", padx=(12, 0))

        self.lbl_dur_sub = tk.Label(form, text="", bg=t["card"], fg=t["info"], font=(FONT, 10))
        self.lbl_dur_sub.grid(row=1, column=1, sticky="w", pady=(0, 4))

        # 2.–5. Weitere Werte
        _, self.entry_spd = entry_row(2, "Tempo", str(self.initial_data.get("speed_str", "0.0")), "km/h")
        _, self.entry_dist = entry_row(3, "Distanz", str(self.initial_data.get("distance_str", "0.00")), "km")
        _, self.entry_steps = entry_row(4, "Schritte", str(self.initial_data.get("steps_str", "0")), None)
        _, self.entry_start = entry_row(
            5, "Startzeit",
            str(self.initial_data.get("start_time_str", datetime.now().strftime("%H:%M"))), "Uhr"
        )

        # Dynamische Vorschau-Karte
        preview_box = tk.Frame(main_box, bg=t["tile"])
        preview_box.pack(fill="x", pady=(16, 18))
        self.lbl_calc_period = tk.Label(preview_box, text="🕒 Zeitraum: --:-- bis --:-- Uhr",
                                        bg=t["tile"], fg=t["text"], font=(FONT, 12, "bold"))
        self.lbl_calc_period.pack(anchor="w", padx=14, pady=(10, 2))
        self.lbl_calc_calories = tk.Label(preview_box, text="🔥 Geschätzter Verbrauch: ~0 kcal",
                                          bg=t["tile"], fg=t["muted"], font=(FONT, 11))
        self.lbl_calc_calories.pack(anchor="w", padx=14, pady=(0, 10))

        # Buttons
        btn_bar = tk.Frame(main_box, bg=t["card"])
        btn_bar.pack(fill="x")
        ghost_button(btn_bar, "Abbrechen", self._on_cancel).pack(side="left")
        self.btn_submit = ModernButton(btn_bar, text="An Google Health senden  →", command=self._on_submit)
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
AUTH_RECHECK_MS = 30 * 60 * 1000  # Google-Login alle 30 Minuten still prüfen


class WalkingPadApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.commit_id = get_git_commit_id()
        self.root.title(f"WalkingPad Sync ({self.commit_id})")
        self.root.configure(bg=THEME["bg"])
        self.root.geometry("1060x700")
        self.root.minsize(980, 640)
        setup_styles(self.root)

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

        # Google-Login-Status
        self.auth_state: Optional[str] = None
        self.login_in_progress = False

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

        # Google-Login direkt beim Start prüfen (nicht erst beim Senden)
        self.root.after(400, lambda: self.check_auth(prompt_login=True))

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
        t = THEME
        outer = tk.Frame(self.root, bg=t["bg"])
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        # ==================== KOPFZEILE ====================
        header = tk.Frame(outer, bg=t["bg"])
        header.pack(fill="x", pady=(0, 14))

        title_box = tk.Frame(header, bg=t["bg"])
        title_box.pack(side="left")
        tk.Label(title_box, text="WalkingPad Sync", bg=t["bg"], fg=t["text"],
                 font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Display ablesen · prüfen · an Google Health senden",
                 bg=t["bg"], fg=t["muted"], font=(FONT, 11)).pack(anchor="w")

        # Google-Status (rechts oben)
        auth_box = tk.Frame(header, bg=t["card"], highlightthickness=1,
                            highlightbackground=t["border"])
        auth_box.pack(side="right", anchor="center")
        auth_inner = tk.Frame(auth_box, bg=t["card"])
        auth_inner.pack(padx=12, pady=8)
        self.lbl_auth_dot = tk.Label(auth_inner, text="●", bg=t["card"], fg=t["faint"],
                                     font=(FONT, 14))
        self.lbl_auth_dot.pack(side="left")
        self.lbl_auth_text = tk.Label(auth_inner, text="Google Health: prüfe Anmeldung…",
                                      bg=t["card"], fg=t["text"], font=(FONT, 11))
        self.lbl_auth_text.pack(side="left", padx=(6, 0))
        self.btn_login = ModernButton(
            auth_inner, text="Anmelden", command=self.start_login,
            font=(FONT, 11, "bold"), padx=10, pady=4
        )
        # wird nur bei Bedarf eingeblendet

        # ==================== INHALT: 2 Spalten ====================
        content = tk.Frame(outer, bg=t["bg"])
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=0)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        left_col = tk.Frame(content, bg=t["bg"])
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right_col = tk.Frame(content, bg=t["bg"])
        right_col.grid(row=0, column=1, sticky="nsew")

        # ---------- Kamera-Karte ----------
        cam_card, cam = make_card(left_col)
        cam_card.pack(fill="x")

        cam_header = tk.Frame(cam, bg=t["card"])
        cam_header.pack(fill="x", pady=(0, 8))
        tk.Label(cam_header, text="KAMERA", bg=t["card"], fg=t["muted"],
                 font=(FONT, 10, "bold")).pack(side="left")

        self.cam_names = [name for _, name in KNOWN_CAMERAS]
        default_str = self.cam_names[0]
        for idx, name in KNOWN_CAMERAS:
            if idx == self.camera_index:
                default_str = name
                break

        self.combo_cam = ttk.Combobox(cam_header, values=self.cam_names, state="readonly",
                                      width=26, style="Dark.TCombobox")
        self.combo_cam.set(default_str)
        self.combo_cam.pack(side="right")
        self.combo_cam.bind("<<ComboboxSelected>>", self.on_camera_combo_change)

        # Video-Container mit fester Größe (verhindert Aufblähen)
        self.video_w = 520
        self.video_h = 325
        video_container = tk.Frame(cam, width=self.video_w, height=self.video_h, bg=t["video_bg"])
        video_container.pack()
        video_container.pack_propagate(False)

        self.lbl_video = tk.Label(video_container, bg=t["video_bg"], text="Initialisiere Kamera…",
                                  fg=t["muted"], font=(FONT, 12), cursor="crosshair")
        self.lbl_video.pack(fill="both", expand=True)

        # Maus-Interaktion für ROI
        self.lbl_video.bind("<ButtonPress-1>", self.on_mouse_down)
        self.lbl_video.bind("<B1-Motion>", self.on_mouse_drag)
        self.lbl_video.bind("<ButtonRelease-1>", self.on_mouse_up)

        # Steuerung unter dem Video
        ctrl_bar = tk.Frame(cam, bg=t["card"])
        ctrl_bar.pack(fill="x", pady=(10, 0))
        tk.Label(ctrl_bar, text="Rahmen mit der Maus neu aufziehen", bg=t["card"],
                 fg=t["faint"], font=(FONT, 10)).pack(side="left")
        ghost_button(ctrl_bar, "Kamera-Freigabe", self.open_camera_settings).pack(side="right")
        ghost_button(ctrl_bar, "Rahmen zurücksetzen", self.reset_roi).pack(side="right", padx=(0, 6))

        # ---------- Erkennungs-Karte ----------
        ocr_card, ocr = make_card(left_col, "Erkennung")
        ocr_card.pack(fill="x", pady=(14, 0))

        preview_row = tk.Frame(ocr, bg=t["card"])
        preview_row.pack(fill="x")

        thresh_box = tk.Frame(preview_row, bg=t["video_bg"], width=160, height=56)
        thresh_box.pack(side="left", padx=(0, 12))
        thresh_box.pack_propagate(False)
        self.lbl_thresh_preview = tk.Label(thresh_box, bg=t["video_bg"], text="Warte auf Signal…",
                                           fg=t["faint"], font=(FONT, 10))
        self.lbl_thresh_preview.pack(fill="both", expand=True)

        info_sub = tk.Frame(preview_row, bg=t["card"])
        info_sub.pack(side="left", fill="both", expand=True)
        self.lbl_ocr_status = tk.Label(info_sub, text="Warte auf Display-Signal…", bg=t["card"],
                                       fg=t["info"], font=(FONT, 12, "bold"),
                                       anchor="w", justify="left", wraplength=320)
        self.lbl_ocr_status.pack(anchor="w", fill="x")
        self.lbl_ocr_raw = tk.Label(info_sub, text="Roh-Text: \"--\"", bg=t["card"], fg=t["faint"],
                                    font=("Menlo", 10), anchor="w")
        self.lbl_ocr_raw.pack(anchor="w", fill="x", pady=(4, 0))

        # ---------- Trainings-Karte (rechts) ----------
        train_card, train = make_card(right_col, "Training")
        train_card.pack(fill="both", expand=True)

        tiles = tk.Frame(train, bg=t["card"])
        tiles.pack(fill="x")
        tiles.columnconfigure(0, weight=1, uniform="tile")
        tiles.columnconfigure(1, weight=1, uniform="tile")

        self.tile_duration = MetricTile(tiles, "Dauer", "min:sek", "00:00")
        self.tile_speed = MetricTile(tiles, "Tempo", "km/h", "0.0")
        self.tile_distance = MetricTile(tiles, "Distanz", "km", "0.00")
        self.tile_steps = MetricTile(tiles, "Schritte", "", "0")
        self.tile_duration.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 10))
        self.tile_speed.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=(0, 10))
        self.tile_distance.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.tile_steps.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

        # Kompatible Namen für die bestehende Logik
        self.entry_duration = self.tile_duration.entry
        self.entry_speed = self.tile_speed.entry
        self.entry_distance = self.tile_distance.entry
        self.entry_steps = self.tile_steps.entry

        # Startzeit + Stunden-Umschalter
        opts = tk.Frame(train, bg=t["card"])
        opts.pack(fill="x", pady=(16, 0))
        tk.Label(opts, text="Startzeit", bg=t["card"], fg=t["muted"],
                 font=(FONT, 12)).pack(side="left")
        self.entry_time = make_entry(opts, font_size=14, width=6)
        self.entry_time.pack(side="left", padx=(10, 6), ipady=3)
        self.entry_time.insert(0, datetime.now().strftime("%H:%M"))
        tk.Label(opts, text="Uhr", bg=t["card"], fg=t["faint"], font=(FONT, 11)).pack(side="left")

        self.chk_over_60 = ttk.Checkbutton(
            opts, text="über 60 Min. (hh:mm)", variable=self.var_over_60,
            command=self.on_over_60_toggled, style="Dark.TCheckbutton"
        )
        self.chk_over_60.pack(side="right")

        tk.Label(train, text="Die Werte füllen sich automatisch, sobald das Display TIME, SPD, DIST "
                             "und STEP durchschaltet. Du kannst sie jederzeit überschreiben.",
                 bg=t["card"], fg=t["faint"], font=(FONT, 10), justify="left",
                 wraplength=400, anchor="w").pack(fill="x", pady=(14, 0))

        # Aktionen (unten in der Karte)
        actions = tk.Frame(train, bg=t["card"])
        actions.pack(side="bottom", fill="x")

        self.btn_submit = ModernButton(
            actions, text="An Google Health senden  →", command=self.submit_data,
            font=(FONT, 15, "bold"), pady=14
        )
        self.btn_submit.pack(fill="x")
        ghost_button(actions, "Neues Training – Werte zurücksetzen", self.clear_fields).pack(
            fill="x", pady=(8, 0))

        # Klick außerhalb eines Eingabefelds nimmt den Fokus weg
        def _blur(event):
            try:
                if not isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                    event.widget.winfo_toplevel().focus_set()
            except Exception:
                pass
        self.root.bind_all("<Button-1>", _blur, add="+")

        # ==================== FUSSZEILE ====================
        tk.Label(outer, text=f"Version {self.commit_id}", bg=t["bg"], fg=t["faint"],
                 font=(FONT, 9)).pack(anchor="e", pady=(8, 0))

    # -----------------------------------------------------------------------
    # Google-Anmeldung: Prüfung beim Start & Login per Button
    # -----------------------------------------------------------------------
    def _set_auth_ui(self, state: str, text: Optional[str] = None):
        self.auth_state = state
        colors = {
            AUTH_OK: THEME["accent"],
            AUTH_LOGIN_REQUIRED: THEME["warn"],
            AUTH_OFFLINE: THEME["warn"],
            AUTH_NO_CLIENT_SECRET: THEME["error"],
            "pending": THEME["info"],
            "error": THEME["error"],
        }
        texts = {
            AUTH_OK: "Google Health verbunden",
            AUTH_LOGIN_REQUIRED: "Google-Anmeldung erforderlich",
            AUTH_OFFLINE: "Google nicht erreichbar",
            AUTH_NO_CLIENT_SECRET: "client_secret.json fehlt",
            "pending": "Warte auf Anmeldung im Browser…",
            "error": "Anmeldung fehlgeschlagen",
        }
        self.lbl_auth_dot.configure(fg=colors.get(state, THEME["faint"]))
        self.lbl_auth_text.configure(text=text or texts.get(state, state))

        show_btn = state in (AUTH_LOGIN_REQUIRED, AUTH_OFFLINE, "error")
        if show_btn:
            self.btn_login.config(text="Erneut prüfen" if state == AUTH_OFFLINE else "Anmelden")
            if not self.btn_login.winfo_ismapped():
                self.btn_login.pack(side="left", padx=(12, 0))
        elif self.btn_login.winfo_ismapped():
            self.btn_login.pack_forget()

    def check_auth(self, prompt_login: bool = False):
        """Prüft im Hintergrund (ohne Browser), ob der Google-Login noch gültig ist."""
        if self.login_in_progress:
            return

        def _worker():
            state, _ = try_silent_credentials()
            self.root.after(0, lambda: self._on_auth_checked(state, prompt_login))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_auth_checked(self, state: str, prompt_login: bool):
        self._set_auth_ui(state)
        if state == AUTH_LOGIN_REQUIRED and prompt_login:
            if messagebox.askyesno(
                "Google-Anmeldung",
                "Deine Google-Anmeldung ist abgelaufen oder fehlt.\n\n"
                "Jetzt im Browser anmelden, damit das Senden an Google Health später direkt klappt?",
                parent=self.root,
            ):
                self.start_login()
        # Regelmäßig still nachprüfen, damit der Status aktuell bleibt
        if getattr(self, "_auth_recheck_job", None):
            self.root.after_cancel(self._auth_recheck_job)
        self._auth_recheck_job = self.root.after(AUTH_RECHECK_MS, self.check_auth)

    def start_login(self):
        """Startet den Browser-Login (oder prüft erneut, falls offline)."""
        if self.login_in_progress:
            return
        if self.auth_state == AUTH_OFFLINE:
            self._set_auth_ui("pending", "Prüfe Verbindung…")
            self.check_auth(prompt_login=True)
            return

        self.login_in_progress = True
        self._set_auth_ui("pending")

        def _worker():
            try:
                interactive_login()
                result, err = AUTH_OK, None
            except Exception as e:
                result, err = "error", str(e)
            self.root.after(0, lambda: self._on_login_done(result, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_login_done(self, state: str, err: Optional[str]):
        self.login_in_progress = False
        self._set_auth_ui(state)
        if err:
            messagebox.showerror("Google-Anmeldung", f"Die Anmeldung hat nicht geklappt:\n{err}",
                                 parent=self.root)

    def on_over_60_toggled(self, recalc: bool = True):
        is_over = self.var_over_60.get()
        self.tile_duration.set_unit("std:min" if is_over else "min:sek")

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
        self.tile_duration.set_unit("min:sek")
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
        self.lbl_ocr_status.config(text="Werte zurückgesetzt – bereit für das nächste Training.",
                                   fg=THEME["info"])

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
                     "(Klicke unten auf 'Kamera-Freigabe')",
                fg=THEME["error"],
                font=(FONT, 12, "bold"),
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
            # Wenn das Display 01:xx oder 1:xx anzeigt, aber die Checkbox noch auf < 60 Min steht:
            if not self.var_over_60.get() and time_display and re.match(r"^0?[1-9]:\d{2}$", time_display):
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
                    time_display = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
                    curr_dur = dur_val
                    # Bei erkanntem Stundenwechsel den neuen Stundenwert sofort verankern:
                    self.stabilizer.candidates["duration"] = {"val": dur_val, "count": 2}

            filtered_dur = self.stabilizer.filter_duration(dur_val, curr_dur)
            if filtered_dur is not None:
                if not time_display:
                    time_display = format_duration_display(filtered_dur, is_over_60=self.var_over_60.get())

                self._set_tile(self.tile_duration, time_display)
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
                self._set_tile(self.tile_speed, f"{filtered_spd:.1f}")
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
                self._set_tile(self.tile_distance, f"{filtered_dist:.2f}")
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
                self._set_tile(self.tile_steps, str(filtered_steps))
                self.last_ocr_data["steps"] = filtered_steps
                status_parts.append(f"Schritte: {filtered_steps}")

        if status_parts:
            self.lbl_ocr_status.config(
                text=f"✓ Erkannt  ·  {'  ·  '.join(status_parts)}",
                fg=THEME["accent"]
            )
        self.lbl_ocr_raw.config(text=f"Roh-Text: \"{parsed['raw']}\"")

    def _set_tile(self, tile: "MetricTile", text: str) -> None:
        """Schreibt einen erkannten Wert in die Kachel (nicht während der Nutzer tippt)."""
        if tile.focus_get() is tile.entry:
            return
        if tile.entry.get() != text:
            tile.entry.delete(0, tk.END)
            tile.entry.insert(0, text)
            tile.flash()

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
        self.tile_duration.set_unit("std:min" if res["is_over_60"] else "min:sek")

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
        self.btn_submit.config(state="disabled", text="Wird gesendet …")

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
        self.btn_submit.config(state="normal", text="An Google Health senden  →")
        self.check_auth()  # Login-Status nach dem Senden aktualisieren
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
