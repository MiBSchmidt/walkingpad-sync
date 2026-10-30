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
    """Erstellt ein modernes macOS-App-Icon aus assets/app_icon.jpg mit iconutil."""
    iconset_dir = REPO_DIR / "AppIcon.iconset"
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True, exist_ok=True)

    src_img_path = REPO_DIR / "assets" / "app_icon.jpg"
    if src_img_path.exists():
        img = Image.open(src_img_path).convert("RGBA")
        # Quadratisch zuschneiden
        w, h = img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))

        size = 1024
        img = img.resize((size, size), Image.Resampling.LANCZOS)

        # macOS Squircle-Maske (moderne abgerundete Ecken)
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        margin = 40
        mask_draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=190, fill=255)

        final_icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        final_icon.paste(img, (0, 0), mask=mask)

        # Eleganter Rand
        border_draw = ImageDraw.Draw(final_icon)
        border_draw.rounded_rectangle(
            [margin, margin, size - margin, size - margin],
            radius=190,
            outline=(200, 210, 225, 220),
            width=4
        )
    else:
        final_icon = Image.new("RGBA", (1024, 1024), (16, 185, 129, 255))

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
        resized = final_icon.resize((s, s), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / name)

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

    # 2. Nativer Mach-O Launcher (Objective-C mit AVFoundation Kamera-Autorisierung)
    launcher_path = macos_dir / "WalkingPad Sync"
    launcher_src = f"""#import <AVFoundation/AVFoundation.h>
#import <Foundation/Foundation.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>

int main(int argc, char *argv[]) {{
    @autoreleasepool {{
        // Fordert die native macOS Kamera-Berechtigung für diese App an
        AVAuthorizationStatus status = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
        if (status == AVAuthorizationStatusNotDetermined) {{
            dispatch_semaphore_t sema = dispatch_semaphore_create(0);
            [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL granted) {{
                dispatch_semaphore_signal(sema);
            }}];
            dispatch_semaphore_wait(sema, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC));
        }}
    }}

    setenv("PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin", 1);
    setenv("OPENCV_AVFOUNDATION_SKIP_AUTH", "0", 1);

    const char *repo_dir = "{REPO_DIR}";
    chdir(repo_dir);

    char *python_path = "{REPO_DIR}/venv/bin/python3";
    char *script_path = "{REPO_DIR}/walkingpad_logger.py";

    char *args[] = {{
        python_path,
        script_path,
        NULL
    }};

    execv(args[0], args);
    perror("execv failed");
    return 1;
}}
"""
    tmp_m = REPO_DIR / "temp_launcher.m"
    with open(tmp_m, "w", encoding="utf-8") as f:
        f.write(launcher_src)

    print("Kompiliere nativen Mach-O Launcher mit AVFoundation...")
    try:
        subprocess.run([
            "clang", "-fobjc-arc",
            "-framework", "Foundation",
            "-framework", "AVFoundation",
            "-o", str(launcher_path),
            str(tmp_m)
        ], check=True)
    finally:
        if tmp_m.exists():
            tmp_m.unlink()

    os.chmod(launcher_path, 0o755)

    # 3. Icon erstellen
    icns_path = res_dir / "AppIcon.icns"
    print("Erstelle App-Icon...")
    create_app_icon(icns_path)

    # 4. Ad-hoc Codesign (Erforderlich für macOS TCC Kamera-Berechtigung)
    print("Entferne erweiterte Finder-Attribute...")
    subprocess.run(["xattr", "-cr", str(LOCAL_APP_PATH)], check=False)

    print("Signiere App-Bundle (ad-hoc codesign für Kamera-TCC)...")
    subprocess.run([
        "codesign", "--force", "--deep", "--sign", "-", str(LOCAL_APP_PATH)
    ], check=True)

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

