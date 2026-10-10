"""Where the V3 tools and tests find what lives outside the V3.

Layout since 2026-10-10: the V3 is the `ngpcraft-v3/` folder of the NgpCraft
Studio repository (GitHub/Ngpcraft-studio/ngpcraft-v3). Not in that
repository, and each settable by an environment variable:
- GB Studio 4.3.2 as released, the reference every tool compares against
  (NGPCRAFT_GBSTUDIO_REFERENCE; tools/reference.py);
- the corpus of GB Studio projects used by some labs (games, never copied
  into the repository; NGPCRAFT_GBSTUDIO_CORPUS);
- sibling checkouts in the GitHub folder (Sound Creator, driver_custom...;
  NGPCRAFT_GITHUB).
The two first default to the former engine folder GitHub/NgpCraft/_Engine.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # ngpcraft-v3
STUDIO = ROOT.parent  # the NgpCraft Studio repository (GB Studio fork)
GITHUB = Path(os.environ.get("NGPCRAFT_GITHUB") or STUDIO.parent)
ENGINE_DATA = GITHUB / "NgpCraft" / "_Engine"
REFERENCE = Path(os.environ.get("NGPCRAFT_GBSTUDIO_REFERENCE")
                 or ENGINE_DATA / "_reference" / "gb-studio-4.3.2")
CORPUS = Path(os.environ.get("NGPCRAFT_GBSTUDIO_CORPUS")
              or ENGINE_DATA / "gbstudio_project_for_test")
