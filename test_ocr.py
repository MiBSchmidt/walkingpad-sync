#!/usr/bin/env python3
"""
Test-Tool: Walking Pad Display OCR Reader (7-Segment & Ruhiger Rahmen)
--------------------------------------------------------------------
Testet die Webcam-Erfassung (180° gedreht), morphologische 7-Segment-Verbindung
und Anzeige der 4 Werte:
- STEP (vierstellige ganze Zahl)
- DIST (Distanz mit 2 Nachkommastellen)
- SPD  (Tempo mit 1 Nachkommastelle)
- TIME (mm:ss)
"""

import os
import sys
import time
import json
import shutil
import threading
from pathlib import Path

os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "0"

import cv2
import numpy as np
import pytesseract
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from walkingpad_logger import (
    preprocess_segment_display,
    parse_walkingpad_display,
    BASE_DIR,
    KNOWN_CAMERAS
)

CONFIG_FILE = BASE_DIR / "camera_config.json"

for candidate in [
    shutil.which("tesseract"),
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
    "/usr/bin/tesseract"
]:
    if candidate and Path(candidate).exists():
        pytesseract.pytesseract.tesseract_cmd = str(candidate)
        break


class DisplayOCRTestApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🔍 Walking Pad Display OCR - 7-Segment Tester")
        self.root.geometry("920x680")

        self.camera_index = 0
        self.roi = [0.40, 0.20, 0.68, 0.50]
        self.var_over_60 = tk.BooleanVar(value=False)
        self.load_config()

        self.cap = None
        self.running = True
        self.current_frame = None
        self.ocr_in_progress = False

        self.drag_start = None
        self.drag_current = None

        self.setup_ui()
        self.start_camera()

        self.root.after(30, self.update_frame)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        self.roi = [0.40, 0.20, 0.68, 0.50]
        self.save_config()

    def setup_ui(self):
        top_bar = ttk.Frame(self.root, padding=10)
        top_bar.pack(fill="x")

        ttk.Label(top_bar, text="Kamera:", font=("Helvetica", 11, "bold")).pack(side="left", padx=(0, 5))

        self.cam_names = [name for _, name in KNOWN_CAMERAS]
        default_str = self.cam_names[0]
        for idx, name in KNOWN_CAMERAS:
            if idx == self.camera_index:
                default_str = name
                break

        self.cam_combo = ttk.Combobox(top_bar, values=self.cam_names, state="readonly", width=34)
        self.cam_combo.set(default_str)
        self.cam_combo.pack(side="left", padx=5)
        self.cam_combo.bind("<<ComboboxSelected>>", self.on_camera_selected)

        ttk.Label(top_bar, text="📌 Ruhiger, fixierter Rahmen", font=("Helvetica", 10, "bold"), foreground="#059669").pack(side="left", padx=10)

        mid_frame = ttk.Frame(self.root, padding=10)
        mid_frame.pack(fill="both", expand=True)

        stream_box = ttk.LabelFrame(mid_frame, text="1. Kamerabild (180° gedreht, Fester Rahmen)", padding=5)
        stream_box.pack(side="left", fill="both", expand=True, padx=(0, 5))

        self.video_w = 460
        self.video_h = 320
        video_container = tk.Frame(stream_box, width=self.video_w, height=self.video_h, bg="#111827")
        video_container.pack(fill="both", expand=False)
        video_container.pack_propagate(False)

        self.lbl_video = tk.Label(video_container, bg="#111827", text="Initialisiere Kamera...", fg="#9CA3AF")
        self.lbl_video.pack(fill="both", expand=True)

        self.lbl_video.bind("<ButtonPress-1>", self.on_mouse_down)
        self.lbl_video.bind("<B1-Motion>", self.on_mouse_drag)
        self.lbl_video.bind("<ButtonRelease-1>", self.on_mouse_up)

        right_panel = ttk.Frame(mid_frame, width=340)
        right_panel.pack(side="right", fill="both", expand=False, padx=(5, 0))

        thresh_box = ttk.LabelFrame(right_panel, text="2. 7-Segment-Filter (Binarisiert & Verbunden)", padding=5)
        thresh_box.pack(fill="both", expand=True)

        self.lbl_thresh = tk.Label(thresh_box, bg="#000000", text="Warte auf OCR...", fg="#6B7280")
        self.lbl_thresh.pack(fill="both", expand=True)

        res_box = ttk.LabelFrame(right_panel, text="3. Erkannte Werte", padding=10)
        res_box.pack(fill="x", pady=(10, 0))

        self.lbl_time = ttk.Label(res_box, text="TIME : --:--", font=("Helvetica", 13, "bold"), foreground="#2563EB")
        self.lbl_time.pack(anchor="w", pady=2)
        ttk.Checkbutton(res_box, text="> 60 Min. (hh:mm)", variable=self.var_over_60).pack(anchor="w", pady=(0, 4))

        self.lbl_spd = ttk.Label(res_box, text="SPD  : --.- km/h", font=("Helvetica", 13, "bold"), foreground="#10B981")
        self.lbl_spd.pack(anchor="w", pady=2)

        self.lbl_dist = ttk.Label(res_box, text="DIST : -.-- km", font=("Helvetica", 13, "bold"))
        self.lbl_dist.pack(anchor="w", pady=2)

        self.lbl_steps = ttk.Label(res_box, text="STEP : ----", font=("Helvetica", 13, "bold"))
        self.lbl_steps.pack(anchor="w", pady=2)

        self.lbl_raw = ttk.Label(res_box, text="Tesseract-Rohausgabe:\n\"--\"", font=("Courier", 10), wraplength=280)
        self.lbl_raw.pack(anchor="w", pady=(8, 0))

        bottom_bar = ttk.Frame(self.root, padding=10)
        bottom_bar.pack(fill="x")
        ttk.Button(bottom_bar, text="🎯 Bereich zentrieren", command=self.reset_roi).pack(side="left")

    def on_camera_selected(self, event=None):
        selected = self.cam_combo.get()
        for idx, name in KNOWN_CAMERAS:
            if name == selected and idx != self.camera_index:
                self.camera_index = idx
                self.save_config()
                self.start_camera()
                break

    def start_camera(self):
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
            time.sleep(0.2)

        try:
            self.cap = cv2.VideoCapture(self.camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            time.sleep(0.2)
        except Exception as e:
            print("Fehler beim Starten:", e)

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

    def update_frame(self):
        if not self.running:
            return

        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                # 180 Grad drehen
                frame = cv2.rotate(frame, cv2.ROTATE_180)
                self.current_frame = frame.copy()

                vw = self.video_w
                vh = self.video_h
                display_frame = cv2.resize(frame, (vw, vh), interpolation=cv2.INTER_LINEAR)

                # Fester, ruhiger Rahmen (grün)
                px1 = int(self.roi[0] * vw)
                py1 = int(self.roi[1] * vh)
                px2 = int(self.roi[2] * vw)
                py2 = int(self.roi[3] * vh)

                cv2.rectangle(display_frame, (px1, py1), (px2, py2), (16, 185, 129), 2)
                cv2.putText(display_frame, "Display-Bereich", (px1, max(18, py1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (16, 185, 129), 2)

                if self.drag_start and self.drag_current:
                    dx0, dy0 = self.drag_start
                    dx1, dy1 = self.drag_current
                    cv2.rectangle(display_frame, (dx0, dy0), (dx1, dy1), (245, 158, 11), 2)

                rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                self._photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
                self.lbl_video.configure(image=self._photo, text="")
                self.lbl_video.image = self._photo

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

        self.root.after(30, self.update_frame)

    def _run_ocr_worker(self, crop_bgr: np.ndarray):
        try:
            thresh = preprocess_segment_display(crop_bgr)
            if thresh is not None:
                tw = max(100, self.lbl_thresh.winfo_width())
                th = max(80, self.lbl_thresh.winfo_height())
                t_resized = cv2.resize(thresh, (tw, th), interpolation=cv2.INTER_NEAREST)
                thresh_photo = ImageTk.PhotoImage(image=Image.fromarray(t_resized))

                custom_config = r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.:SPDTIMEstep"
                raw_text = pytesseract.image_to_string(thresh, config=custom_config).strip()
                parsed = parse_walkingpad_display(raw_text, is_over_60=self.var_over_60.get())

                def _update_ui():
                    self.lbl_thresh.configure(image=thresh_photo, text="")
                    self.lbl_thresh.image = thresh_photo

                    if parsed["time_str"]:
                        mode_str = " (hh:mm)" if self.var_over_60.get() else " (mm:ss)"
                        dur_info = f" -> {parsed['duration_min']:.1f} Min." if parsed['duration_min'] is not None else ""
                        self.lbl_time.config(text=f"TIME : {parsed['time_str']}{mode_str}{dur_info}")
                    if parsed["speed"] is not None:
                        self.lbl_spd.config(text=f"SPD  : {parsed['speed']:.1f} km/h")
                    if parsed["distance"] is not None:
                        self.lbl_dist.config(text=f"DIST : {parsed['distance']:.2f} km")
                    if parsed["steps"] is not None:
                        self.lbl_steps.config(text=f"STEP : {parsed['steps']}")

                    self.lbl_raw.config(text=f"Tesseract-Rohausgabe:\n\"{raw_text}\"")

                self.root.after(0, _update_ui)
        except Exception:
            pass
        finally:
            time.sleep(0.35)
            self.ocr_in_progress = False

    def on_close(self):
        self.running = False
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = DisplayOCRTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
