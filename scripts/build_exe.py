"""Build script for 灵枢 (LingShu) — PyInstaller .exe packaging.

Usage:
    python scripts/build_exe.py          # Build the exe
    python scripts/build_exe.py --clean  # Clean build

Requirements:
    pip install pyinstaller Pillow
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = ROOT / "scripts" / "lingShu.spec"
DIST_DIR = ROOT / "dist"
ICON_SVG = ROOT / "scripts" / "icon.svg"
ICON_ICO = ROOT / "scripts" / "icon.ico"


def svg_to_ico(svg_path: Path, ico_path: Path, size: int = 64):
    """Generate a simple ICO icon (programmatic, no SVG parsing needed)."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Purple circle background
        draw.ellipse([2, 2, size - 2, size - 2], fill=(124, 92, 252, 255))
        # Lightning bolt (white)
        bolt = [(size*0.55, size*0.15), (size*0.38, size*0.50), (size*0.52, size*0.50),
                (size*0.42, size*0.85), (size*0.70, size*0.45), (size*0.55, size*0.45)]
        draw.polygon(bolt, fill=(255, 255, 255, 242))
        # Save
        img.save(ico_path, format="ICO", sizes=[(size, size)])
        print("  ✅ Icon generated: %s" % ico_path)
        return True
    except Exception as e:
        print("  ⚠️  Icon generation failed: %s" % e)
        return False


def clean_build():
    """Remove previous build artifacts."""
    dirs = [ROOT / "build", DIST_DIR / "lingShu"]
    for d in dirs:
        if d.exists():
            shutil.rmtree(d)
            print("  🗑️  Removed: %s" % d)
    files = list(ROOT.glob("*.spec"))
    for f in files:
        f.unlink()
        print("  🗑️  Removed: %s" % f)


def build():
    print("")
    print("  ⚡ Building 灵枢 (LingShu Agent)")
    print("  " + ("-" * 50))
    print("")

    # Ensure CWD is project root
    os.chdir(str(ROOT))

    # Step 1: Generate icon
    print("  [1/3] Generating icon...")
    ico_ok = svg_to_ico(ICON_SVG, ICON_ICO)

    # Step 2: Run PyInstaller
    print("  [2/3] Running PyInstaller...")
    if not SPEC_FILE.exists():
        print("  ❌ Spec file not found: %s" % SPEC_FILE)
        sys.exit(1)

    cmd = ["pyinstaller", str(SPEC_FILE), "--clean"]

    print("  Command: %s" % " ".join(cmd))
    print("")

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print("  ❌ PyInstaller failed (exit code %d)" % result.returncode)
        sys.exit(1)

    # Step 3: Verify
    print("  [3/3] Verifying output...")
    exe_path = DIST_DIR / "lingShu" / "lingShu.exe"
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
    else:
        print("  ⚠️  Output not found at expected path:")
        print("     %s" % exe_path)
        # Try to find the exe
        for f in DIST_DIR.rglob("*.exe"):
            print("     Found: %s" % f)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean_build()
    build()
