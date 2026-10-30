#!/usr/bin/env python3
"""
Schnell-Diagnose: Macht einen Schnappschuss von allen erreichbaren Kameras.
"""
import os
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")
import cv2
from pathlib import Path

out_dir = Path(__file__).parent / "camera_snapshots"
out_dir.mkdir(exist_ok=True)

print("=== Kamera-Diagnose ===")
for idx in range(4):
    cap = cv2.VideoCapture(idx)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret and frame is not None:
            # Auch um 180 Grad gedreht speichern
            rotated = cv2.rotate(frame, cv2.ROTATE_180)
            file_path = out_dir / f"camera_{idx}.jpg"
            cv2.imwrite(str(file_path), rotated)
            h, w = frame.shape[:2]
            print(f"✅ Kamera {idx}: Bild gespeichert nach '{file_path.name}' ({w}x{h}, Helligkeit: {frame.mean():.1f})")
        else:
            print(f"⚠️ Kamera {idx}: Geöffnet, liefert aber keinen Frame.")
        cap.release()
    else:
        print(f"❌ Kamera {idx}: Nicht verfügbar.")

print("\nFertig! Schau dir bei Bedarf die Bilder im Ordner 'camera_snapshots' an.")

