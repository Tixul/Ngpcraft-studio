"""The one GB Studio reference every V3 tool compares against.

GB Studio 4.3.2 as RELEASED (tag v4.3.2, 2026-06-22), with the GBVM commit that
tag pins (appData/engine/gbvm = chrismaltby/gbvm@bd6f41cc5e05). Read only,
outside this repository (tools/locations.py). Decision of 2026-09-29 (docs/FORMAT_PROJET.md §1):
the develop snapshot (../_reference/gb-studio-develop-4.3.2) carries unreleased
work (project format 5.0.0, array variables, tilemap scenes, four array
events) that no published GB Studio can open.
"""
from pathlib import Path

from tools.locations import REFERENCE, ROOT  # noqa: F401 (ROOT kept for importers)
LABEL = "GB Studio 4.3.2 release (tag v4.3.2, gbvm bd6f41c; ../_reference/gb-studio-4.3.2)"
GBVM_COMMIT = "bd6f41cc5e05cbe6601dcc7f8e2db89bed527fe3"
