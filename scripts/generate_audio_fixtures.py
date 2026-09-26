"""Write the synthetic test clips to fixtures/audio/.

Usage:  python scripts/generate_audio_fixtures.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.audio_factory import FIXTURES  # noqa: E402

out = ROOT / "fixtures" / "audio"
out.mkdir(parents=True, exist_ok=True)
for name, build in FIXTURES.items():
    (out / name).write_bytes(build())
    print(f"wrote {out / name}")
