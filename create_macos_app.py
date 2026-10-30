#!/usr/bin/env python3
"""
Erstellt eine native macOS-Anwendung: 'WalkingPad Sync.app'
- Startet ohne Terminal-Fenster
- Eigener Eintrag im Dock (kann dauerhaft in der Startleiste abgelegt werden)
- Enthält eigenes App-Icon (icns)
- NSCameraUsageDescription für native Kamera-Berechtigung
- Installiert nach ~/Applications und /Applications
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

REPO_DIR = Path(__file__).parent.resolve()
APP_NAME = "WalkingPad Sync.app"
LOCAL_APP_PATH = REPO_DIR / APP_NAME
USER_APPS_DIR = Path.home() / "Applications"
SYS_APPS_DIR = Path("/Applications")


def create_app_icon(output_icns: Path):
    """Erstellt ein modernes macOS-App-Icon im Squircle-Design mit iconutil."""
    iconset_dir = REPO_DIR / "AppIcon.iconset"
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True, exist_ok=True)

    # 1024x1024 Master Icon
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Squircle Hintergrund (macOS Style mit abgerundeten Ecken)
    margin = 80
    bg_color_top = (17, 24, 39)     # #111827 Dark Slate
    accent_green = (16, 185, 129)   # #10B981 Emerald
    cyan_glow = (59, 130, 246)      # #3B82F6 Blue

    # Dunkler abgerundeter Kasten
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=200,
        fill=bg_color_top,
        outline=(55, 65, 81),
        width=12
    )

    # Laufband-Bodenplatte (Perspektive)
    draw.polygon([
        (260, 720), (764, 720), (840, 790), (184, 790)
    ], fill=(31, 41, 55))

    # Laufband-Band mit Speed-Streifen
    draw.polygon([
        (280, 730), (744, 730), (810, 780), (214, 780)
    ], fill=accent_green)

    # Weiß leuchtendes Display (wie auf dem Walking Pad)
    draw.rounded_rectangle(
        [312, 280, 712, 440],
        radius=30,
        fill=(0, 0, 0),
        outline=accent_green,
        width=8
    )

    # Display-Text (Digital 7-Segment stilisierte Balken)
    # 0 6 : 1 3
    # Zeichnen von "WALKING PAD" und LED-Elementen
    draw.text((360, 310), "06:13", fill=(255, 255, 255))
    draw.text((450, 400), "TIME", fill=accent_green)

    # Stilisierter Läufer / Schritt-Symbol (Dynamische Figur)
    # Kopf
    draw.ellipse([480, 480, 544, 544], fill=(255, 255, 255))
    # Körper & Beine in Bewegung
    draw.line([(512, 544), (512, 640)], fill=(255, 255, 255), width=24)
    # Schritt nach vorne
    draw.line([(512, 630), (600, 720)], fill=accent_green, width=22)
    # Schritt nach hinten
    draw.line([(512, 630), (430, 710)], fill=(255, 255, 255), width=20)
    # Arme
    draw.line([(512, 570), (590, 600)], fill=(255, 255, 255), width=18)
    draw.line([(512, 570), (440, 610)], fill=accent_green, width=18)

    # Alle Standardgrößen für macOS iconset erzeugen
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png")
    ]

    for s, name in sizes:
        resized = img.resize((s, s), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / name)

    # iconutil aufrufen
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(output_icns)], check=True)
    finally:
        shutil.rmtree(iconset_dir, ignore_errors=True)


def build_app():
    print(f"Erstelle {APP_NAME}...")

    contents = LOCAL_APP_PATH / "Contents"
    macos_dir = contents / "MacOS"
    res_dir = contents / "Resources"

    if LOCAL_APP_PATH.exists():
        shutil.rmtree(LOCAL_APP_PATH)

    macos_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)

    # 1. Info.plist
    info_plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>WalkingPad Sync</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.mischmi.walkingpad-sync</string>
    <key>CFBundleName</key>
    <string>WalkingPad Sync</string>
    <key>CFBundleDisplayName</key>
    <string>WalkingPad Sync</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSCameraUsageDescription</key>
    <string>WalkingPad Sync benötigt Zugriff auf die Webcam, um das LED-Display des Laufbands per OCR abzulesen.</string>
</dict>
</plist>
"""
    with open(contents / "Info.plist", "w", encoding="utf-8") as f:
        f.write(info_plist_content)

    # 2. Launcher Binary (Shell Executable ohne Terminal)
    launcher_path = macos_dir / "WalkingPad Sync"
    launcher_script = f"""#!/usr/bin/env bash
# WalkingPad Sync Native App Launcher

REPO_DIR="{REPO_DIR}"
cd "$REPO_DIR" || exit 1

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export OPENCV_AVFOUNDATION_SKIP_AUTH="0"

# Startet Python direkt im Hintergrund ohne Terminal-Fenster
exec "$REPO_DIR/venv/bin/python3" "$REPO_DIR/walkingpad_logger.py"
"""
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(launcher_script)

    os.chmod(launcher_path, 0o755)

    # 3. Icon erstellen
    icns_path = res_dir / "AppIcon.icns"
    print("Erstelle App-Icon...")
    create_app_icon(icns_path)

    print(f"✅ {APP_NAME} erfolgreich erstellt!")

    # 4. Nach ~/Applications kopieren
    USER_APPS_DIR.mkdir(parents=True, exist_ok=True)
    target_user_app = USER_APPS_DIR / APP_NAME
    if target_user_app.exists():
        shutil.rmtree(target_user_app)
    shutil.copytree(LOCAL_APP_PATH, target_user_app)
    print(f"✅ Installiert in Benutzer-Programme: {target_user_app}")

    # 5. Nach /Applications kopieren (falls Schreibrechte vorhanden)
    try:
        target_sys_app = SYS_APPS_DIR / APP_NAME
        if target_sys_app.exists():
            shutil.rmtree(target_sys_app)
        shutil.copytree(LOCAL_APP_PATH, target_sys_app)
        print(f"✅ Installiert in System-Programme: {target_sys_app}")
    except Exception as e:
        print(f"Hinweis zu /Applications: {e} (Installation in ~/Applications ist aktiv)")


if __name__ == "__main__":
    build_app()
