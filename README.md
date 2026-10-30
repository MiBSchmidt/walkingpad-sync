# WalkingPad Sync & OCR Workout Logger

Google Health Sync Service & automatische Trainingserfassung mit OCR für das **Skandika Walking Pad**.

## Features

1. **Hardware-optimierte Webcam-Integration:**
   - Liest die USB-Webcam (`cv2.VideoCapture(0)`) ein.
   - **180° Drehung:** Jedes Frame wird automatisch um 180 Grad gedreht (`cv2.rotate(frame, cv2.ROTATE_180)`), da die Kamera kopfüber montiert ist.
   - Flüssige Live-Vorschau in Tkinter mit interaktiver **ROI-Auswahl** (Region of Interest) per Maus auf dem Videobild. Die Position wird automatisch in `roi_config.json` gespeichert.

2. **Computer Vision & OCR (Tesseract):**
   - Vorverarbeitung der ROI (Graustufen, adaptive Skalierung, Kontrastnormalisierung, Otsu-Thresholding mit automatischer Invertierung für LED/Segmentanzeigen).
   - Auslesen von **Geschwindigkeit (km/h)**, **Distanz (km)** und **Schritten**.
   - Anzeige der erkannten Rohwerte zur visuellen Kontrolle in der GUI.

3. **State Machine & Pausen-Logik:**
   - **`IDLE` (Bereitschaft):** Geschwindigkeit ist 0 km/h. System wartet auf Trainingsbeginn.
   - **`RUNNING` (Laufend):** Sobald Tempo > 0 km/h erkannt wird, startet das Workout automatisch. Zeit, Schritte und Distanz laufen live mit.
   - **`PAUSED` (Pause):** Bei Stillstand (0 km/h) pausiert das Training.
     - *< 3 Minuten:* Nahtlose Fortsetzung, sobald das Band wieder anläuft (Pausenzeit zählt nicht zur aktiven Trainingsdauer).
     - *> 5 Minuten:* Automatischer Abschluss des Workouts.
   - **`FINISHED` (Beendet):** Überträgt die Gesamtdaten (Dauer abzüglich Pausen, Schritte, Distanz, Durchschnittsgeschwindigkeit) automatisch via Google Health API v4 und kehrt danach zu `IDLE` zurück.

4. **Manuelle Steuerung:**
   - Eingabefelder für manuelle Korrektur oder manuellen Upload.
   - Button zum sofortigen manuellen Abschließen & Senden eines Workouts.

---

## Starten

### Über Terminal:
```bash
./venv/bin/python app.py
```
*oder:*
```bash
./venv/bin/python walkingpad_logger.py
```
*oder:*
```bash
./run.command
```

### Per Doppelklick im Finder (macOS):
Doppelklick auf `run.command`.
*(Hinweis: Terminal muss in den macOS-Systemeinstellungen unter Datenschutz & Sicherheit Zugriff auf den Dokumente-Ordner besitzen).*
