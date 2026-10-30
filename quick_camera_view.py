#!/usr/bin/env python3
"""
Direkte Kamera-Vorschau mit OpenCV (ohne Tkinter).
Drücke 'c' um die Kamera zu wechseln (0, 1, 2).
Drücke 'q' um zu beenden.
"""
import os
os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "0"
import cv2

def main():
    camera_index = 2
    cap = cv2.VideoCapture(camera_index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print(f"Starte Direkt-Vorschau für Kamera {camera_index} (Trust 1080P Webcam)...")
    print("Tastenkürzel im Videofenster:")
    print("  'c' -> Kamera wechseln (0 -> 1 -> 2 -> 0)")
    print("  'q' -> Beenden")

    while True:
        ret, frame = cap.read()
        if ret and frame is not None:
            # 180 Grad drehen
            rotated = cv2.rotate(frame, cv2.ROTATE_180)

            # Info-Text einblenden
            h, w = rotated.shape[:2]
            info = f"Kamera {camera_index} ({w}x{h}) - [c]: Wechseln | [q]: Beenden"
            cv2.putText(rotated, info, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

            cv2.imshow("Direct Camera Preview (180 deg rotated)", rotated)
        else:
            print(f"Kamera {camera_index}: Kein Frame...")

        key = cv2.waitKey(30) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('c'):
            camera_index = (camera_index + 1) % 3
            print(f"Wechsle zu Kamera {camera_index}...")
            cap.release()
            cap = cv2.VideoCapture(camera_index)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
