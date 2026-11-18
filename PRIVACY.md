# Datenschutzerklärung – WalkingPad Sync

*Stand: 18. November 2026*

WalkingPad Sync ist eine private, quelloffene Desktop-Anwendung für macOS. Sie liest per Webcam die Anzeige eines Walking Pads (Laufband) ab und überträgt die Trainingswerte auf Wunsch an den **eigenen** Google-Health-Account des Nutzers. Es gibt keinen Server, kein Benutzerkonto beim Entwickler und keine Weitergabe von Daten an Dritte.

## 1. Welche Daten verarbeitet werden

| Daten | Zweck | Wo sie bleiben |
| :--- | :--- | :--- |
| **Kamerabild** des Laufband-Displays | Ablesen der Werte per Texterkennung (OCR) | Nur im Arbeitsspeicher des eigenen Macs. Bilder werden weder gespeichert noch übertragen. |
| **Trainingswerte** (Dauer, Tempo, Distanz, Schritte, Start-/Endzeit, geschätzte Kalorien) | Anzeige und Übertragung an Google Health | Lokal in der App; an Google Health nur nach ausdrücklicher Bestätigung durch den Nutzer |
| **Google-OAuth-Token** (`token.json`) | Dauerhafte Anmeldung bei der Google Health API | Ausschließlich lokal im Programmordner auf dem eigenen Mac |
| **Kamera-Einstellungen** (`camera_config.json`) | Merken der gewählten Kamera und des Display-Bereichs | Ausschließlich lokal |

Die Texterkennung erfolgt vollständig lokal mit Tesseract OCR. Es werden keine Bilder oder Daten an externe Dienste zur Auswertung gesendet. Das separate Diagnose-Werkzeug `diagnose_cameras.py` speichert auf Aufruf Testbilder der Kameras im lokalen Programmordner; diese verlassen den Rechner nicht.

## 2. Nutzung von Google-Nutzerdaten

Die App greift über die **Google Health API** auf den Google-Account des Nutzers zu, und zwar mit folgenden Berechtigungen:

- `googlehealth.activity_and_fitness.writeonly` – um ein Training (Aktivität „Gehen“) im eigenen Google-Health-Konto anzulegen.
- `googlehealth.activity_and_fitness.readonly` – die App liest damit ausschließlich die Bestätigung, die Google auf ein gerade gesendetes Training zurückgibt, um die übernommenen Werte anzuzeigen. Es werden keine weiteren Gesundheits- oder Fitnessdaten abgerufen.

Daten aus dem Google-Account werden **nicht** gespeichert (außer dem Anmelde-Token, siehe oben), **nicht** an Dritte weitergegeben, **nicht** für Werbung verwendet und **nicht** zum Training von KI- oder Machine-Learning-Modellen genutzt.

Die Nutzung und Übertragung von Informationen, die von Google-APIs empfangen werden, entspricht der [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), einschließlich der Anforderungen zur eingeschränkten Nutzung („Limited Use“).

## 3. Weitergabe an Dritte

Es findet keine Weitergabe statt. Die einzige Datenübertragung ist die vom Nutzer ausgelöste Übermittlung der Trainingswerte direkt vom eigenen Rechner an Google (`health.googleapis.com`). Für die Verarbeitung dort gilt die [Datenschutzerklärung von Google](https://policies.google.com/privacy).

## 4. Speicherdauer und Löschung

- Kamerabilder werden nicht gespeichert.
- Der Anmelde-Token bleibt lokal gespeichert, bis die Datei `token.json` gelöscht wird.
- Der Zugriff der App kann jederzeit unter [myaccount.google.com/connections](https://myaccount.google.com/connections) widerrufen werden.
- Bereits übertragene Trainings lassen sich in Google Health bzw. der zugehörigen App löschen.

## 5. Kontakt

Fragen zum Datenschutz bitte als Issue im Repository stellen:
[github.com/MiBSchmidt/walkingpad-sync/issues](https://github.com/MiBSchmidt/walkingpad-sync/issues)

---

## Privacy Policy (English summary)

WalkingPad Sync is a private, open-source macOS desktop app. Camera images of the treadmill display are processed **locally in memory only** (local OCR) and are never stored or transmitted. Workout values (duration, speed, distance, steps, time range, estimated calories) are sent **only** to the user's own Google Health account, and only after the user explicitly confirms. The OAuth token is stored locally on the user's computer. No data is shared with third parties, used for advertising, or used to train AI/ML models. The app requests write access to create workouts and read access solely to display Google's confirmation of the submitted workout. The app's use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. Access can be revoked at any time at [myaccount.google.com/connections](https://myaccount.google.com/connections).
