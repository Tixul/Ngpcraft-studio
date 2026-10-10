"""Listen to one song: a ROM that only plays it (`preview-music`).

The project is copied (never modified) into a new folder, with one more scene
that starts the game: a plain 160x152 background and, as its On Init script,
GB Studio's Play Music of the song. The project's own scenes are not copied:
the export compiles every scene of a project (as GB Studio does), so one of
them still needing work for the NGPC (a sound effect not converted yet, a
refused event) would stop a preview that never plays it. The song plays through the same path as
in the game: its ngpcraft.json native_music binding, the native driver and
the PSG, nothing simulated. Used by NgpCraft Studio's "Listen" button.
"""
import json
import shutil
import struct
from pathlib import Path
import zlib

from .compiler import CompileError
from .project import SIDECAR, encode_resource, load_project

SCENE_ID = "ngpcraft-music-preview-scene"
BACKGROUND_ID = "ngpcraft-music-preview-background"
BACKDROP = (0x10, 0x18, 0x30)


def _png(path, width, height, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode_resource(data), encoding="utf-8")


def preview_project(root, music_id, dest):
    """Copy `root` into the new folder `dest` with the preview scene; returns dest."""
    root, dest = Path(root), Path(dest)
    if root.is_file():
        root = root.parent
    project = load_project(root)
    musics = {res["id"]: res for _, res in project.by_type["music"]}
    if music_id not in musics:
        raise CompileError(f"music {music_id!r}: no music resource has this id")
    if "native_music" not in project.ngpc["resources"].get(music_id, {}):
        raise CompileError(f"music {musics[music_id]['name']!r}: no NGPC version "
                           f"(ngpcraft.json resources.{music_id}.native_music): nothing to play")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise CompileError("preview destination must be new or empty")
    scenes = (root / "project" / "scenes").resolve()

    def ignore(folder, names):
        if Path(folder).resolve() == scenes:
            return set(names)  # the preview scene is the only one
        return {n for n in names if n in ("build", ".git", "node_modules")}

    shutil.copytree(root, dest, dirs_exist_ok=True, ignore=ignore)
    _png(dest / "assets/backgrounds/ngpcraft_music_preview.png", 160, 152, BACKDROP)
    _write(dest / "assets/backgrounds/ngpcraft_music_preview.png.gbsres",
           {"_resourceType": "background", "id": BACKGROUND_ID, "name": "ngpcraft_music_preview",
            "symbol": "bg_ngpcraft_music_preview", "tileColors": "", "filename": "ngpcraft_music_preview.png",
            "width": 20, "height": 19, "imageWidth": 160, "imageHeight": 152, "autoColor": False})
    index = max((s["_index"] for s in project.scenes), default=-1) + 1
    _write(dest / "project/scenes/ngpcraft_music_preview/scene.gbsres",
           {"_resourceType": "scene", "id": SCENE_ID, "_index": index, "type": "TOPDOWN",
            "name": "NgpCraft music preview", "symbol": "scene_ngpcraft_music_preview", "x": 0, "y": 0,
            "width": 20, "height": 19, "backgroundId": BACKGROUND_ID, "tilesetId": "",
            "colorModeOverride": "none", "paletteIds": [], "spritePaletteIds": [], "autoFadeSpeed": 1,
            "script": [{"command": "EVENT_ACTOR_HIDE", "args": {"actorId": "player"}, "id": "preview-hide"},
                       {"command": "EVENT_MUSIC_PLAY", "args": {"musicId": music_id}, "id": "preview-play"}],
            "playerHit1Script": [], "playerHit2Script": [], "playerHit3Script": [], "collisions": ""})
    # settings: the preview scene starts the game (every settings resource, as merged).
    for rel, res in project.by_type["settings"]:
        if "startSceneId" in res:
            _write(dest / rel, {**res, "startSceneId": SCENE_ID})
    if not any("startSceneId" in res for _, res in project.by_type["settings"]):
        rel, res = project.by_type["settings"][0]
        _write(dest / rel, {**res, "startSceneId": SCENE_ID})
    return dest


def build_preview(root, music_id, out):
    """`preview-music`: the preview project in out/project, its export in out/export."""
    from .pipeline import export_project
    out = Path(out)
    project = preview_project(root, music_id, out / "project")
    manifest = export_project(project, out / "export")
    return {"format": "ngpcraft-music-preview/1", "music": music_id, "project": str(project),
            "export": str(out / "export"), "features": manifest["features"]}
