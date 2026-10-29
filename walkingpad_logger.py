#!/usr/bin/env python3
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

BASE_DIR = Path(__file__).parent
CLIENT_SECRETS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "token.json"

# Offizielle Scopes der neuen Google Health API v4
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.writeonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
]

API_BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
DEFAULT_STRIDE_M = 0.62  # ca. 62 cm Schrittlänge auf dem Walking Pad


def get_credentials() -> Credentials:
    """Führt beim ersten Start den Browser-Login durch und erneuert Tokens danach automatisch."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Erneuere Google Access Token...")
            creds.refresh(Request())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"❌ Datei nicht gefunden: {CLIENT_SECRETS_FILE}\n"
                    "Bitte speichere deine heruntergeladene JSON-Datei als 'client_secret.json' im Skript-Ordner!"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            creds = flow.run_local_server(port=8080)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print("✅ Authentifizierung erfolgreich! Token lokal gespeichert.\n")

    return creds


def test_connection(creds: Credentials) -> bool:
    """Liest testweise die letzten Trainings aus, um die API-Verbindung zu prüfen."""
    url = f"{API_BASE_URL}/exercise/dataPoints?pageSize=3"
    headers = {"Authorization": f"Bearer {creds.token}"}
    
    print("📡 Teste Verbindung zur Google Health API v4...")
    res = requests.get(url, headers=headers)
    
    if res.ok:
        data = res.json()
        points = data.get("dataPoints", [])
        print(f"✅ Verbindung steht! ({len(points)} letzte Trainings gefunden)\n")
        return True
    else:
        print(f"⚠️ API-Antwort ({res.status_code}): {res.text}\n")
        return False


def log_walking_pad_workout(
    creds: Credentials,
    start_dt_local: datetime,
    duration_min: float,
    distance_km: float,
    speed_kmh: float,
    steps: int
):
    """Schreibt das Laufband-Training samt Schritten, Tempo und Distanz in die Google Health API v4."""
    duration_sec = int(duration_min * 60)
    end_dt_local = start_dt_local + timedelta(seconds=duration_sec)

    # Lokalen UTC-Offset berechnen (z. B. +3600s im Winter / +7200s im Sommer)
    utc_offset_sec = int(start_dt_local.astimezone().utcoffset().total_seconds())
    offset_str = f"{utc_offset_sec}s"

    # Zeiten ins geforderte UTC-RFC3339 Format ("...Z") umrechnen
    start_utc_str = start_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc_str = end_dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Einheiten für Google Health v4 umrechnen (Millimeter & Millimeter/Sekunde)
    distance_mm = int(round(distance_km * 1_000_000))
    speed_mm_per_s = round((speed_kmh / 3.6) * 1000, 2)
    # Grobe Kalorienschätzung (ca. 3.5 kcal pro Minute beim Gehen), da manche Ansichten das Feld erwarten
    est_calories = int(round(duration_min * 3.8))

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json"
    }

    # 1. Exercise-Session Payload (bündelt Dauer, Schritte, Distanz und Geschwindigkeit)
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

    print("🚀 Sende Training an Google Health API...")
    res_ex = requests.post(exercise_url, headers=headers, json=exercise_payload)

    if res_ex.ok:
        print("🎉 Training erfolgreich eingetragen!")
        print(json.dumps(res_ex.json(), indent=2))
    else:
        print(f"❌ Fehler beim Eintragen des Trainings ({res_ex.status_code}):")
        print(res_ex.text)


def prompt_float(prompt_text: str, default: float = None) -> float:
    val = input(prompt_text).strip().replace(",", ".")
    if not val and default is not None:
        return default
    return float(val) if val else 0.0


def main():
    print("=== 🏃‍♂️ Skandika Walking Pad -> Google Health v4 Logger ===\n")
    
    creds = get_credentials()
    test_connection(creds)

    duration_min = prompt_float("1. Dauer in Minuten (z. B. 45): ")
    if duration_min <= 0:
        print("Abbruch.")
        return

    speed_kmh = prompt_float("2. Geschwindigkeit in km/h (z. B. 3.2): ", 3.0)
    
    calc_dist = round(speed_kmh * (duration_min / 60.0), 2)
    distance_km = prompt_float(f"3. Entfernung in km [Enter für berechnete {calc_dist} km]: ", calc_dist)

    # Wenn Distanz manuell geändert wurde und Speed nicht dazu passt, Speed ggf. nachziehen
    if distance_km > 0 and speed_kmh == 0:
        speed_kmh = round(distance_km / (duration_min / 60.0), 2)

    est_steps = int((distance_km * 1000) / DEFAULT_STRIDE_M)
    steps_in = input(f"4. Schritte [Enter für geschätzte {est_steps} Schritte]: ").strip()
    steps = int(steps_in) if steps_in else est_steps

    now_local = datetime.now().astimezone()
    default_start = now_local - timedelta(minutes=duration_min)

    start_str = input(f"5. Startzeit HH:MM [Enter für {default_start.strftime('%H:%M')} Uhr]: ").strip()
    date_str = input(f"6. Datum YYYY-MM-DD [Enter für heute, {now_local.strftime('%Y-%m-%d')}]: ").strip() or now_local.strftime("%Y-%m-%d")

    if start_str:
        naive_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
        start_dt_local = naive_dt.replace(tzinfo=now_local.tzinfo)
    else:
        start_dt_local = default_start

    print("\n--- Zusammenfassung ---")
    print(f"Start     : {start_dt_local.strftime('%Y-%m-%d %H:%M')} Uhr ({duration_min:.0f} Min.)")
    print(f"Tempo     : {speed_kmh:.2f} km/h")
    print(f"Distanz   : {distance_km:.2f} km")
    print(f"Schritte  : {steps}")

    confirm = input("\nJetzt an Google Health senden? (J/n): ").strip().lower()
    if confirm in ("", "j", "ja", "y", "yes"):
        log_walking_pad_workout(creds, start_dt_local, duration_min, distance_km, speed_kmh, steps)


if __name__ == "__main__":
    main()