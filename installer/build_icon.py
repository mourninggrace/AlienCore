"""
AlienCore - installer/build_icon.py
Render the alien-head canvas drawing (the same one the system tray uses)
to assets/icon.ico at the resolutions Windows wants for taskbar / explorer
/ jump-list / tile.

Generated icon is checked in so a fresh clone can build the installer
without Pillow installed; rerun this only when you redesign the icon.

Run from project root:
    python installer/build_icon.py
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("FATAL: Pillow not installed.  pip install Pillow", file=sys.stderr)
    sys.exit(2)

# Use the tray.py canvas drawing as the source — same alien head, no
# external PNG file needed.
PROJECT_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from gui.tray import _get_alien_icon  # noqa: E402
from core.constants import COLOR_COOL  # noqa: E402

ICO_PATH = os.path.join(PROJECT_ROOT, "assets", "icon.ico")
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256)]


def main():
    # _get_alien_icon returns a PIL Image; the cool-color variant is the
    # neutral idle look that fits all install / explorer contexts.
    src = _get_alien_icon(COLOR_COOL)
    if src.size[0] < 256:
        # Up-rez the source to the largest target so Pillow's .ico
        # writer can downsample cleanly.
        src = src.resize((256, 256), Image.LANCZOS)
    os.makedirs(os.path.dirname(ICO_PATH), exist_ok=True)
    src.save(ICO_PATH, format="ICO", sizes=ICO_SIZES)
    print(f"Wrote {ICO_PATH}  ({len(ICO_SIZES)} embedded sizes)")


if __name__ == "__main__":
    main()
