#!/usr/bin/env python3
"""
Prüft die macOS-Kamera-Berechtigung und öffnet bei Bedarf direkt
die passende Systemeinstellungs-Seite.
"""
import os
import sys
import subprocess

print("=== macOS Kamera-Berechtigungsprüfung ===")

# Umgebungsvariable setzen, damit OpenCV Berechtigung anfragt
os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "0"

import cv2

print("Versuche Zugriff auf Kamera 0 anzufordern...")
cap = cv2.VideoCapture(0)

if cap.isOpened():
    ret, frame = cap.read()
    if ret and frame is not None:
        print("\n🎉 ERFOLG! Kamera-Berechtigung ist erteilt.")
        print(f"Signal empfangen: {frame.shape[1]}x{frame.shape[0]} Pixel.")
        cap.release()
        sys.exit(0)
    else:
        print("\n⚠️ Kamera konnte geöffnet werden, aber liefert noch leere Frames.")
    cap.release()
else:
    print("\n❌ Kamera-Zugriff wurde von macOS blockiert ('not authorized').")

print("\nBitte aktiviere den Kamera-Zugriff in den macOS-Einstellungen:")
print("1. Öffne: Systemeinstellungen -> Datenschutz & Sicherheit -> Kamera")
print("2. Aktiviere dort den Schalter für 'Terminal' (oder deine IDE).")
print("\nÖffne jetzt die Systemeinstellungen für dich...")

try:
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"])
except Exception:
    pass

