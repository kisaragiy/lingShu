"""Build CS Demo standalone EXE with PyInstaller.

Usage:
    python scripts/build_cs_demo_exe.py          # Build
    python scripts/build_cs_demo_exe.py --clean  # Clean + build

Requirements:
    pip install pyinstaller
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "scripts" / "cs_demo.spec"
DIST_DIR = ROOT / "dist"


def _generate_icon(size=64):
    """Generate a nice icon for the CS Demo exe."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  ⚠️  PIL not installed, skipping icon. Install: pip install Pillow")
        return None

    ico_path = ROOT / "scripts" / "cs_demo.ico"

    # Create icon with headset emoji style
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Blue circle background
    draw.ellipse([2, 2, size - 2, size - 2], fill=(37, 99, 235, 255))

    # White headset symbol
    # Headband
    draw.arc([size*0.25, size*0.15, size*0.75, size*0.55], start=0, end=180,
             fill=(255, 255, 255, 242), width=4)
    # Left earcup
    draw.rectangle([size*0.18, size*0.38, size*0.30, size*0.65],
                   fill=(255, 255, 255, 242))
    draw.rectangle([size*0.18, size*0.55, size*0.30, size*0.60],
                   fill=(37, 99, 235, 255))
    # Right earcup
    draw.rectangle([size*0.70, size*0.38, size*0.82, size*0.65],
                   fill=(255, 255, 255, 242))
    draw.rectangle([size*0.70, size*0.55, size*0.82, size*0.60],
                   fill=(37, 99, 235, 255))

    img.save(ico_path, format="ICO", sizes=[(size, size)])
    print("  ✅ Icon generated: %s" % ico_path)
    return ico_path


def clean():
    """Remove previous build artifacts."""
    for d in [ROOT / "build", DIST_DIR / "cs-demo"]:
        if d.exists():
            shutil.rmtree(d)
            print("  🗑️  Removed: %s" % d)
    for f in ROOT.glob("*.spec"):
        f.unlink()
        print("  🗑️  Removed: %s" % f)


def build():
    print("")
    print("  🎧 Building CS Demo standalone EXE")
    print("  " + ("=" * 50))
    print("")

    os.chdir(str(ROOT))

    # Step 1: Generate icon
    print("  [1/3] Generating icon...")
    icon = _generate_icon()

    # Step 2: Patch spec with icon path
    if icon and SPEC_FILE.exists():
        spec_content = SPEC_FILE.read_text(encoding="utf-8")
        if "icon=None" in spec_content:
            icon_str = str(icon).replace("\\", "/")
            spec_content = spec_content.replace("icon=None", f"icon=r'{icon_str}'")
            SPEC_FILE.write_text(spec_content, encoding="utf-8")
            print(f"  ✅ Spec patched with icon: {icon}")

    # Step 2: Run PyInstaller
    print("  [2/3] Running PyInstaller...")
    if not SPEC_FILE.exists():
        print("  ❌ Spec file not found: %s" % SPEC_FILE)
        sys.exit(1)

    cmd = ["pyinstaller", str(SPEC_FILE), "--clean", "--noconfirm"]
    print("  Command: %s" % " ".join(cmd))
    print("")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("  ❌ PyInstaller failed (exit code %d)" % result.returncode)
        sys.exit(1)

    # Step 3: Verify
    print("  [3/3] Verifying output...")
    exe_path = DIST_DIR / "cs-demo" / "cs-demo.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print("")
        print("  " + ("=" * 50))
        print("  ✅ Build complete!")
        print("  📦 %s" % exe_path)
        print("  💾 %.1f MB" % size_mb)
        print("  " + ("=" * 50))
        print("")
        print("  Run: %s" % exe_path)
        print("")
    else:
        print("  ⚠️  Output not found at expected path:")
        print("     %s" % exe_path)
        for f in DIST_DIR.rglob("*.exe"):
            print("     Found: %s" % f)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    build()
