#!/usr/bin/env python3
import sys
import os
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Aufruf aus dem .app-Bundle fixen, damit relative Pfade (client_secret.json) 
# immer im Ordner der App liegen, auch wenn sie doppelgeklickt wird.
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).resolve().parent.parent / "Resources"
    # Falls direkt im MacOS-Ordner:
    if not (BASE_DIR / "client_secret.json").exists():
        BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).parent

CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
]
API_BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
DEFAULT_STRIDE_M = 0.62


def get_credentials() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(f"client_secret.json nicht gefunden in:\n{BASE_DIR}")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


class WalkingPadApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Skandika Walking Pad Sync")
        self.root.geometry("380x420")
        self.root.minsize(380, 420)
        
        # Modernes Layout mit Padding
        pad_options = {"padx": 15, "pady": 6}

        tk.Label(root, text="🏃‍♂️ Skandika Walking Pad Logger", font=("Helvetica", 14, "bold")).pack(pady=10)

        # 1. Dauer
        frame_dur = tk.Frame(root)
        frame_dur.pack(fill="x", **pad_options)
        tk.Label(frame_dur, text="Dauer (Minuten):", width=18, anchor="w").pack(side="left")
        self.entry_duration = tk.Entry(frame_dur, width=12)
        self.entry_duration.pack(side="right")
        self.entry_duration.insert(0, "30")
        self.entry_duration.bind("<KeyRelease>", self.calculate_defaults)

        # 2. Geschwindigkeit
        frame_spd = tk.Frame(root)
        frame_spd.pack(fill="x", **pad_options)
        tk.Label(frame_spd, text="Tempo (km/h):", width=18, anchor="w").pack(side="left")
        self.entry_speed = tk.Entry(frame_spd, width=12)
        self.entry_speed.pack(side="right")
        self.entry_speed.insert(0, "3.2")
        self.entry_speed.bind("<KeyRelease>", self.calculate_defaults)

        # 3. Distanz
        frame_dist = tk.Frame(root)
        frame_dist.pack(fill="x", **pad_options)
        tk.Label(frame_dist, text="Distanz (km):", width=18, anchor="w").pack(side="left")
        self.entry_distance = tk.Entry(frame_dist, width=12)
        self.entry_distance.pack(side="right")
        self.entry_distance.insert(0, "1.6")
        self.entry_distance.bind("<KeyRelease>", self.calculate_steps_only)

        # 4. Schritte
        frame_steps = tk.Frame(root)
        frame_steps.pack(fill="x", **pad_options)
        tk.Label(frame_steps, text="Schritte:", width=18, anchor="w").pack(side="left")
        self.entry_steps = tk.Entry(frame_steps, width=12)
        self.entry_steps.pack(side="right")
        self.entry_steps.insert(0, "2580")

        # 5. Startzeit (HH:MM)
        frame_time = tk.Frame(root)
        frame_time.pack(fill="x", **pad_options)
        tk.Label(frame_time, text="Startzeit (HH:MM):", width=18, anchor="w").pack(side="left")
        self.entry_time = tk.Entry(frame_time, width=12)
        self.entry_time.pack(side="right")
        # Standard: Jetzt minus eingetragene Dauer
        default_start = (datetime.now() - timedelta(minutes=30)).strftime("%H:%M")
        self.entry_time.insert(0, default_start)

        # Live-Berechnung einmalig anstoßen
        self.calculate_defaults()

        # Absende-Button
        self.btn_submit = tk.Button(root, text="An Google Health senden", bg="#10B981", fg="white", 
                                    font=("Helvetica", 11, "bold"), command=self.submit_data)
        self.btn_submit.pack(pady=20, ipadx=10, ipady=5)

    def calculate_defaults(self, event=None):
        """Berechnet Distanz und Schritte automatisch aus Dauer und Tempo"""
        try:
            dur = float(self.entry_duration.get().replace(",", "."))
            spd = float(self.entry_speed.get().replace(",", "."))
            dist = round(spd * (dur / 60.0), 2)
            
            self.entry_distance.delete(0, tk.END)
            self.entry_distance.insert(0, str(dist))
            
            steps = int((dist * 1000) / DEFAULT_STRIDE_M)
            self.entry_steps.delete(0, tk.END)
            self.entry_steps.insert(0, str(steps))
        except ValueError:
            pass

    def calculate_steps_only(self, event=None):
        """Passt die Schritte an, falls die Distanz manuell überschrieben wird"""
        try:
            dist = float(self.entry_distance.get().replace(",", "."))
            steps = int((dist * 1000) / DEFAULT_STRIDE_M)
            self.entry_steps.delete(0, tk.END)
            self.entry_steps.insert(0, str(steps))
        except ValueError:
            pass

    def submit_data(self):
        try:
            duration_min = float(self.entry_duration.get().replace(",", "."))
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

            duration_sec = int(duration_min * 60)
            end_dt_local = start_dt_local + timedelta(seconds=duration_sec)
            
            utc_offset_sec = int(start_dt_local.astimezone().utcoffset().total_seconds())
            offset_str = f"{utc_offset_sec}s"
            
            start_utc_str = start_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc_str = end_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            distance_mm = int(round(distance_km * 1_000_000))
            speed_mm_per_s = round((speed_kmh / 3.6) * 1000, 2)
            est_calories = int(round(duration_min * 3.8))

            # Authentifizierung & API-Call
            creds = get_credentials()
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
                        "steps": steps,
                        "distanceMillimeters": distance_mm,
                        "averageSpeedMillimetersPerSecond": speed_mm_per_s,
                        "caloriesKcal": est_calories
                    }
                }
            }

            res = requests.post(exercise_url, headers=headers, json=exercise_payload)
            if res.status_code == 401 and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                headers["Authorization"] = f"Bearer {creds.token}"
                res = requests.post(exercise_url, headers=headers, json=exercise_payload)
            
            if res.ok:
                messagebox.showinfo("Erfolg 🎉", f"Training erfolgreich übertragen!\n\n• {steps} Schritte\n• {distance_km} km\n• {duration_min} Minuten")
                self.root.destroy()
            else:
                messagebox.showerror("Fehler", f"API-Fehler ({res.status_code}):\n{res.text}")

        except Exception as e:
            messagebox.showerror("Fehler", f"Ungültige Eingabe oder Verbindungsfehler:\n{str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = WalkingPadApp(root)
    root.mainloop()