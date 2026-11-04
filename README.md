# 🏃‍♂️ WalkingPad Sync & OCR Workout Logger

Desktop-Anwendung für macOS zur automatischen Display-Erfassung und Synchronisierung des **Skandika Walking Pads** (und baugleichen Laufbändern) mit der **Google Health API v4**.

Da viele Walking Pads und kompakte Schreibtisch-Laufbänder keine offene Schnittstelle oder Bluetooth-Export bieten, liest eine montierte USB-Webcam die leuchtende LED-Segmentanzeige per Computer Vision und Texterkennung (OCR) live ab. Die Trainingsdaten werden übersichtlich dargestellt und können mit einem Klick an Google Health übertragen werden.

---

## 📸 Funktionsweise

```
+-------------------+        +--------------------+        +---------------------+
|   Walking Pad     |        |   USB-Webcam       |        |  OpenCV & Tesseract |
|   LED-Display     | -----> |  (180° gedreht,    | -----> |  - 7-Segment-Filter |
| (TIME, SPD, DIST, |        |   Kamera 0)        |        |  - Segment-Schluss  |
|  STEP im Wechsel) |        +--------------------+        |  - RegEx-Extraktion |
+-------------------+                                      +----------+----------+
                                                                      |
                                                                      v
                                                           +---------------------+
                                                           |   Tkinter GUI       |
                                                           | - Live-Kamerabild   |
                                                           | - Filter-Vorschau   |
                                                           | - Auto-Textfelder   |
                                                           +----------+----------+
                                                                      |
                                                                      v
                                                           +---------------------+
                                                           | Google Health API v4|
                                                           |  (Workout Upload)   |
                                                           +---------------------+
```

1. **Hardware & Bildaufnahme:**
   - Nutzt die angeschlossene USB-Webcam (Standard: `Kamera 0`).
   - Da Webcams über dem Band meist kopfüber montiert werden, wird jedes Frame automatisch um **180° gedreht** (`cv2.ROTATE_180`).
   - Die Fenstergröße ist fest verankert (kein ungewolltes Aufblähen des Videofensters).

2. **Ruhiger, stabiler Erfassungsbereich (ROI):**
   - Ein grüner Rahmen markiert den Display-Bereich (`camera_config.json`).
   - **Kein Springen:** Der Rahmen bleibt fest und wandert nicht bei Lichtreflexen im Raum.
   - Großzügig vorkonfiguriert ($x: 40\% - 68\%$, $y: 20\% - 50\%$), damit das Display auch bei leichten Vibrationen oder Bandverschiebungen im Bild bleibt.
   - Kann bei Bedarf jederzeit per Maus im Kamerabild neu aufgezogen oder per Button `🎯 Bereich zentrieren` zurückgesetzt werden.

3. **7-Segment LED-Bildverarbeitung:**
   - **Weißsegment-Isolation:** Filtert die leuchtend weißen LEDs zuverlässig heraus.
   - **Morphologisches Schließen:** Verbindet die Segmentbalken (`cv2.morphologyEx(..., cv2.MORPH_CLOSE)`), sodass Tesseract vollständige Zahlen statt zerhackter Segmente erkennt.
   - **Kontrast & Polsterung:** Optimale Invertierung (schwarze Schrift auf weißem Grund) mit Randpolsterung.
   - **Live-Vorschau:** Ein separates Vorschaufenster („7-Segment-Filter“) zeigt in Echtzeit das binarisierte Display.

4. **Werte & Formate:**
   - **`TIME` (Dauer):**
     - **Unter 60 Minuten:** Display zeigt `mm:ss` (Minuten:Sekunden, z. B. `25:30` -> 25,5 Min.).
     - **Ab 60 Minuten:** Das Walking Pad wechselt automatisch auf `hh:mm` (Stunden:Minuten, z. B. `01:15` -> 75 Min.). Die App erkennt dies automatisch anhand von Distanz-/Schritt-Schwellen und Vorlaufzeit, und bietet eine Checkbox `> 60 Min. (hh:mm)` zur manuellen Umschaltung.
   - **`SPD` (Tempo):** Dezimalzahl mit 1 Nachkommastelle in km/h (z. B. `3.2`, `4.0`).
   - **`DIST` (Distanz):** Dezimalzahl mit 2 Nachkommastellen in km (z. B. `1.25`, `0.80`).
   - **`STEP` (Schritte):** Ganze Zahlen (z. B. `120`, `764`, `2580`) mit Entprellung und Umschalt-Artefaktfilter (verhindert Sprünge durch Displaywechsel wie 360 -> 3603).
   - Alle Werte werden unabhängig voneinander live in die entsprechenden Felder geschrieben, sobald das Laufband das Display durchschaltet.

5. **Automatische Startzeit-Berechnung:**
   - Die Startzeit wird automatisch berechnet: $\text{Startzeit} = \text{Aktuelle Uhrzeit} - \text{Trainingsdauer}$.
   - Beim Beenden des Workouts um 16:00 Uhr mit 25 Min. Dauer wird das Training für 15:35 bis 16:00 Uhr datiert.
   - Kann jederzeit manuell angepasst werden.

6. **Bearbeitbarer Bestätigungsdialog & Google Health API v4 Upload:**
   - Klick auf **`🚀 Training an Google Health senden`** öffnet ein **editierbares Bestätigungsfenster**:
     - Alle Werte (Dauer, Format, Tempo, Distanz, Schritte, Startzeit) können vor dem endgültigen Absenden nochmals überprüft und bei Bedarf korrigiert werden.
     - Dynamische Live-Vorschau von berechnetem Trainingszeitraum (`Start – Ende`) und geschätztem Kalorienverbrauch.
   - Nach Bestätigung werden die Daten an Google Health v4 übertragen und die von Google akzeptierten Werte im Erfolgsdialog angezeigt.

---

## 🛠 Voraussetzungen & Systemanforderungen

- **Betriebssystem:** macOS (Apple Silicon oder Intel)
- **Python:** Python 3.10+ (getestet mit Python 3.15)
- **Tesseract OCR:** 
  ```bash
  brew install tesseract
  ```
- **macOS Berechtigungen:**
  - **Kamera:** *Systemeinstellungen* $\rightarrow$ *Datenschutz & Sicherheit* $\rightarrow$ *Kamera* $\rightarrow$ Terminal (bzw. Python) erlauben.
  - **Dateizugriff:** *Systemeinstellungen* $\rightarrow$ *Datenschutz & Sicherheit* $\rightarrow$ *Dateien & Ordner* $\rightarrow$ Terminal Zugriff auf Dokumente erlauben.

---

## 🚀 Installation & Einrichtung

### 1. Repository klonen & virtuelle Umgebung einrichten
```bash
git clone https://github.com/mischmi/walkingpad-sync.git
cd walkingpad-sync

# Virtuelle Umgebung erstellen
python3 -m venv venv
source venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt
```

Falls `requirements.txt` nicht vorhanden ist:
```bash
pip install opencv-python pillow pytesseract requests google-auth google-auth-oauthlib numpy
```

### 2. Google Health API einrichten
1. Erstelle ein Projekt in der [Google Cloud Console](https://console.cloud.google.com/).
2. Aktiviere die **Google Health API v4** (bzw. Fitness API).
3. Erstelle **OAuth 2.0-Client-IDs** (Desktop-Anwendung).
4. Lade die JSON-Datei herunter und speichere sie im Projektordner als:
   ```
   client_secret.json
   ```
*(Beim ersten Senden öffnet sich der Browser zur Anmeldung; der Login wird in `token.json` gespeichert).*

---

## ▶️ App starten

### Option A: Als native macOS App (Im Programme-Ordner / Dock)
Die App liegt direkt als eigenständige macOS-Anwendung **`WalkingPad Sync.app`** im Benutzer-Programme-Ordner (`~/Applications` bzw. `/Applications`) und kann dauerhaft ins **Dock** gezogen werden.

Zum Erstellen oder Aktualisieren der App einfach ausführen:
```bash
./venv/bin/python create_macos_app.py
```

### Option B: Per Doppelklick im Finder
Die Datei **`run.command`** im Projektordner doppelklicken.

### Option C: Über das Terminal
```bash
./venv/bin/python app.py
```

---

## 🗂 Projektstruktur

| Datei / Verzeichnis | Beschreibung |
| :--- | :--- |
| **`walkingpad_logger.py`** | Hauptanwendung (Tkinter GUI, Kamera-Loop, 7-Segment-Filter, OCR-Parser, Google Health Upload). |
| **`app.py`** | Schlanker Launcher zum Starten von `walkingpad_logger`. |
| **`run.command`** | macOS-Shellskript für bequemen Start per Doppelklick im Finder. |
| **`camera_config.json`** | Speichert die gewählte Kamera-ID (Standard: `0`) und die Display-Koordinaten (ROI). |
| **`test_ocr.py`** | Standalone-Testtool zur Überprüfung von Kamera und OCR ohne Google-Upload. |
| **`test_ocr.command`** | Doppelklick-Starter für das OCR-Testtool. |
| **`quick_camera_view.py`** | Schneller OpenCV-Kameratest mit Tastatursteuerung (`r` = ROI, `c` = Kamera wechseln, `q` = Schließen). |
| **`diagnose_cameras.py`** | Diagnose-Skript, das alle angeschlossenen Videoquellen auflistet. |
| **`client_secret.json`** | *(Lokal)* Google Cloud OAuth-Konfiguration (in `.gitignore`). |
| **`token.json`** | *(Lokal)* Gespeichertes OAuth-Zugriffstoken für Google Health (in `.gitignore`). |

---

## 💡 Tipps zur optimalen Erkennung

1. **Beleuchtung:** Das Walking Pad besitzt leuchtende LEDs. Zu grelles, direktes Deckenlicht oder Sonnenreflexionen auf der spiegelnden Display-Abdeckung können die Ablesung stören. Eine leicht geneigte Kameraposition verhindert Direktreflexionen.
2. **Bereich anpassen:** Sollte der grüne Rahmen nicht genau auf dem Display liegen, einfach mit der Maus im Videobild ein neues Rechteck aufziehen. Die Position wird automatisch in `camera_config.json` gespeichert.
3. **Manuelle Korrektur:** Alle Felder (Dauer, Tempo, Distanz, Schritte, Startzeit) können vor dem Absenden jederzeit manuell im Textfeld überschrieben werden.
4. **Felder zurücksetzen:** Der Button *„🧹 Felder auf 0 setzen“* leert alle Werte für das nächste Training.

---

## 📄 Lizenz

Dieses Projekt steht unter der [MIT License](LICENSE).
