"""PSG state image by image: the reference for the music on the Z80 (docs/PLAN_MUSIQUE_Z80.md Z2).

A built export (made public by tools/lab_cadence.expose_statics when --expose is given, so that
audio.c's statics are in the symbol table) runs on the native core with held buttons; after
every video frame the T6W28's registers are read (emulator ApuState, minus the free-running
noise shifter) with the driver's song frame and its drop counter. Two traces compare as
the sequence of distinct states (what is heard, in order) and image by image (when).

  python -B -m tools.lab_music_z80 <built export> --out <new json> --frames 1800
         --emulator <Ngpcraft_emulator> --bios <bios> [--inputs run.json] [--expose <new dir>]

Corpus proof (--corpus <project>): a copy of the project whose start scene (--scene, a light
one: no lost frame) plays every native song in turn (--song-frames each) with every distinct
sound effect of the project at fixed frames, a stop and a replay; exported with
engine_speedups false (the C driver) and true (the Z80 when eligible), the ordered PSG writes
of both ROMs must be equal.

  python -B -m tools.lab_music_z80 --corpus <project> --scene NAME --out <new dir>
         --emulator <Ngpcraft_emulator> --bios <bios>
"""
import argparse
import json
from pathlib import Path
import sys

from tools.lab_cadence import held_at
from tools.probe_rom import power_on_ram

STATE_FIELDS = ("square_vol_left", "square_vol_right", "square_period", "noise_vol_left", "noise_vol_right",
                "noise_tap", "noise_period_select", "noise_period_extra")


def _state(apu):
    out = []
    for name in STATE_FIELDS:
        value = getattr(apu, name)
        out.append(tuple(value) if hasattr(value, "__len__") else int(value))
    return tuple(out)


def trace(export, frames, emulator, bios, inputs=()):
    """-> {"states": [state per frame], "song_frame": [...], "drops": int|None, "writes": int}."""
    rom = (export / "rom/game.ngc").read_bytes()
    sym = json.loads((export / "rom/build-report.json").read_text(encoding="utf-8"))["link_audit"]["public_symbols"]
    if str(emulator) not in sys.path:
        sys.path.append(str(emulator))       # after the V3: the emulator's tools package would hide ours
    from core import native
    m = native.NativeMachine(rom, bios=Path(bios).read_bytes())
    m.set_timing_silicon(word_wait=10, bios_wait=8)
    m.reset(bios_handoff=True)
    power_on_ram(m, "noise")
    # The Z80's RAM starts random on a console too (power_on_ram covers the user RAM only): a
    # byte the driver reads before writing it shows here.
    import random
    rng = random.Random(0x2780)
    m.write(0x7000, bytes(rng.randrange(256) for _ in range(0x1000)))
    song = sym.get("_s_bgm_song_frame", {}).get("address")
    drops = sym.get("_s_sound_drops", {}).get("address")
    states, song_frames, writes = [], [], []
    seen = m.apu_write_count()
    for k in range(frames):
        held = held_at(list(inputs), k)
        m.write(0x00B0, bytes([held]))
        m.write(0x6F82, bytes([held]))
        m.run_frames(1)
        states.append(_state(m.apu_state()))
        song_frames.append(int.from_bytes(bytes(m.read(song, 4)), "little") if song else None)
        count = m.apu_write_count()
        if count - seen > m.APU_LOG_SIZE:
            raise RuntimeError("more PSG writes in one frame than the emulator's log holds")
        writes += [(w.address, w.value) for w in m.apu_writes(count - seen)] if count > seen else []
        seen = count
    return {"states": states, "song_frame": song_frames, "writes": writes,
            "drops": int.from_bytes(bytes(m.read(drops, 2)), "little") if drops else None,
            "write_count": m.apu_write_count()}


# Driver variables read by the trace (audio.c statics), made public in the copy.
TRACED = ("static u32 s_bgm_song_frame;", "static u16 s_sound_drops;")


def exposed(export, out, toolchain):
    """A copy of the export with its static functions and TRACED public (lab_cadence)."""
    import shutil
    from tools.lab_cadence import build_exposed, expose_statics
    from tools.lab_multi_banks import rehash
    if out.exists():
        raise SystemExit(f"{out}: must be new")
    shutil.copytree(export, out, ignore=shutil.ignore_patterns("rom", "objects"))
    expose_statics(out)
    audio = out / "src/audio.c"
    text = audio.read_text(encoding="utf-8")
    for line in TRACED:
        if text.count(line) == 1:
            text = text.replace(line, line[len("static "):])
    audio.write_text(text, encoding="utf-8")
    rehash(out, "src/audio.c")
    build_exposed(out, toolchain)
    return out


def distinct(states):
    out = []
    for s in states:
        if not out or out[-1] != s:
            out.append(s)
    return out


def compare_writes(a, b):
    """The PSG writes in order (address, value): the strict criterion, timing aside."""
    wa, wb = a["writes"], b["writes"]
    n = min(len(wa), len(wb))
    first = next((k for k in range(n) if tuple(wa[k]) != tuple(wb[k])), None)
    return {"writes": [len(wa), len(wb)], "first_write_difference": first,
            "equal_prefix": n if first is None else first}


def compare(a, b):
    """Heard (distinct states in order) and timed (same state at the same frame) equality."""
    da, db = distinct(a["states"]), distinct(b["states"])
    n = min(len(a["states"]), len(b["states"]))
    timed = [k for k in range(n) if a["states"][k] != b["states"][k]]
    first = next((k for k in range(min(len(da), len(db))) if da[k] != db[k]), None)
    return {"heard_equal": da == db, "distinct": [len(da), len(db)], "first_heard_difference": first,
            "frames_differing": len(timed), "first_frame_differing": timed[0] if timed else None}


def corpus_project(source, out, scene, song_frames=600, effects=True):
    """The project copy with the start scene's script replaced (module docstring)."""
    import re
    import shutil
    from tools.demo_gbs_scene import write_json
    from tools.demo_scene_switch import ev, num
    project = out / "project"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns("build", "build_export"))
    ngpc = json.loads((project / "ngpcraft.json").read_text(encoding="utf-8"))
    songs = [k for k, v in ngpc.get("resources", {}).items() if "native_music" in v]
    found, folder = {}, None
    for path in sorted((project / "project").rglob("*.gbsres")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'"command": "EVENT_SOUND_PLAY_EFFECT",\s*"args": (\{.*?\})', text, re.S):
            args = json.loads(m[1])
            found.setdefault(json.dumps(args, sort_keys=True), args)
        data = json.loads(text)
        if data.get("_resourceType") == "scene" and data.get("name") == scene:
            folder = path
    if folder is None:
        raise SystemExit(f"unknown scene {scene!r}")
    effects = list(found.values()) if effects else []
    wait = lambda k, n: ev(f"wait-{k}", "EVENT_WAIT", units="frames", frames=num(n))
    # Power-on and the scene load first: a frame either build loses there (the two loads do not
    # cost the same) would shift the effects (ticks) against the music (VBlanks) for good.
    script, k = [wait(0, 60)], 0
    for i, song in enumerate(songs):
        script.append(ev(f"play-{i}", "EVENT_MUSIC_PLAY", musicId=song, loop=True))
        step = song_frames // (len(effects) + 1)
        for j, args in enumerate(effects):
            k += 1
            script += [wait(k, step), ev(f"sfx-{i}-{j}", "EVENT_SOUND_PLAY_EFFECT", **dict(args, wait=False))]
        k += 1
        script.append(wait(k, song_frames - step * len(effects)))
    if songs:
        k += 1
        script += [ev("stop", "EVENT_MUSIC_STOP"), wait(k, 30), ev("replay", "EVENT_MUSIC_PLAY", musicId=songs[0], loop=True),
                   wait(k + 1, 120)]
    data = json.loads(folder.read_text(encoding="utf-8"))
    data["script"] = script
    # Nothing else runs: no actor, no trigger, so that neither build loses a frame (a lost
    # frame steps two music frames in one tick but the effects once: their order would differ
    # for a reason that is not the driver's).
    for key in ("actors", "triggers"):
        if key in data:
            data[key] = []
    for key in [k for k in data if k.endswith("Script") and k != "script"]:
        data[key] = []
    write_json(folder, data)
    settings = project / "project/settings.gbsres"
    sdata = json.loads(settings.read_text(encoding="utf-8"))
    sdata["startSceneId"] = data["id"]
    write_json(settings, sdata)
    frames = len(songs) * song_frames + 260
    return project, frames, {"songs": len(songs), "effects": len(effects)}


def random_stream(rng, noise, length):
    """A valid stream of the transcribed opcodes (music_z80.ops), every value range including
    the clamps and the faults, its end last; -> (bytes, loop offset at an opcode)."""
    out, starts = [], []
    while len(out) < length:
        for _ in range(rng.randint(0, 4)):          # zero-time opcodes, then one timed opcode
            starts.append(len(out))
            kind = rng.choice("f0 f4 f5 f6 f7 f8 fb fe4 fe5 fex".split())
            if kind == "f0":
                out += [0xF0, rng.choice([0, 3, 9, 15, 16, 40])]
            elif kind == "f4":
                out += [0xF4, rng.choice([0, 1, 2, 7])]
            elif kind == "f5":
                out += [0xF5, rng.randrange(256)]
            elif kind == "f6":
                kind6 = rng.choice([1, 1, 1, 2, 0])
                out += [0xF6, kind6, rng.choice([8, 20, 30]) if kind6 == 0 else rng.choice([0, 1, 2, 3])]
            elif kind == "f7":
                out += [0xF7, rng.choice([0, 1, 5, 14, 15, 30])]
            elif kind == "f8":
                bend = rng.choice([0, 1, -1, 7, -7, 300, -300, 1500, -1500, 32767, -32768])
                out += [0xF8, bend & 0xFF, (bend >> 8) & 0xFF]
            elif kind == "fb":
                out += [rng.choice([0xFB, 0xFC, 0xFD]), rng.randrange(256)]
            elif kind == "fe4":
                out += [0xFE, 4, rng.choice([0, 2, 7, 15] * 8 + [16])]
            elif kind == "fe5":
                div = rng.choice([1, 2, 100, 500, 1023] * 6 + [0, 1024]) if not noise or rng.random() < 0.03 else None
                if div is None:
                    continue
                out += [0xFE, 5, div & 0xFF, div >> 8]
            else:
                out += [0xFE, rng.choice([6, 7, 9]), rng.randrange(256)]
        starts.append(len(out))
        kind = rng.choice("note note note off hold".split())
        if kind == "note":
            out += [rng.choice(list(range(1, 52)) * 3 + [52, 60]), rng.choice([0, 1, 1, 2, 3, 5])]
        elif kind == "off":
            out += [0xFF, rng.choice([0, 1, 2, 4])]
        else:
            out += [0xFE, 3, rng.choice([1, 1, 2, 3, 6] * 10 + [0])]
    starts.append(len(out))
    out.append(0x00)
    loop = rng.choice(starts)
    return out, loop


def randomise_streams(text, seed):
    """audio.c with every song channel replaced by a random stream (same seed, same streams)."""
    import random
    import re
    rng = random.Random(seed)
    loops = {}
    def channel(m):
        name = m[1]
        data, loop = random_stream(rng, name.endswith("ch3"), rng.randint(60, 400))
        loops[name] = loop
        return f"static const unsigned char {name}[] = {{\n    " + ", ".join(map(str, data)) + "\n};"
    text = re.sub(r"static const unsigned char (ngc_music_track\d+_ch\d)\[\] = \{.*?\};", channel, text, flags=re.S)
    def track(m):
        names = [n.strip() for n in m[1].split(",")]
        offsets = ", ".join(str(loops.get(n, 0)) for n in names)
        return m[0].replace("{" + m[2] + "}}", "{" + offsets + "}}")
    return re.sub(r"\{ngc_music_track\d+_notes, \{([^}]*)\}, \{([^}]*)\}\}", track, text)


def corpus(source, out, scene, emulator, bios, toolchain, song_frames=600, random_seed=None, effects=True):
    """Both drivers on the corpus project -> comparison of the ordered PSG writes."""
    from ngpcraft.pipeline import export_project
    from ngpcraft.toolchain import build_rom
    from tools.lab_speedups import set_speedups
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out}: output must be new")
    project, frames, content = corpus_project(Path(source), out, scene, song_frames, effects)
    traces, reports = {}, {}
    if random_seed is not None:
        # Random streams in place of the songs' (both builds, before the Z80 transform).
        import ngpcraft.project_music as project_music
        original = project_music.generate_library
        def generate(*args, **kwargs):
            audio = original(*args, **kwargs)
            audio["audio.c"] = randomise_streams(audio["audio.c"], random_seed)
            return audio
        project_music.generate_library = generate
        content["random_seed"] = random_seed
    for value in (False, True):
        set_speedups(project, value)
        export = out / ("z80" if value else "c")
        export_project(project, export)
        build_rom(export, toolchain)
        reports[value] = json.loads((export / "export.json").read_text(encoding="utf-8")).get("music_z80")
        # The Z80 side runs 60 frames longer: it starts a frame later (its program initialises
        # its voices first); every C write must be found, in order, at the start of its writes.
        traces[value] = trace(export, frames + (60 if value else 0), emulator, bios)
        (out / f"{'z80' if value else 'c'}_writes.json").write_text(json.dumps(traces[value]["writes"]), encoding="utf-8")
    result = {"schema": "ngpcraft-music-z80-corpus/1", **content, "frames": frames,
              "music_z80": reports[True], **compare_writes(traces[False], traces[True]),
              "c_is_prefix": [tuple(w) for w in traces[True]["writes"][:len(traces[False]["writes"])]] ==
                             [tuple(w) for w in traces[False]["writes"]],
              "silicon_timing": [10, 8], "ram": "noise", "console_validated": False}
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export", type=Path, nargs="?")
    p.add_argument("--corpus", type=Path, help="project: the corpus proof (module docstring)")
    p.add_argument("--scene")
    p.add_argument("--song-frames", type=int, default=600)
    p.add_argument("--random", type=int, help="seed: random streams in place of the songs'")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frames", type=int, default=1800)
    p.add_argument("--emulator", type=Path, required=True)
    p.add_argument("--bios", type=Path, required=True)
    p.add_argument("--inputs", type=Path)
    p.add_argument("--expose", type=Path, help="new dir: trace a copy with audio.c's statics public")
    p.add_argument("--toolchain", type=Path, default=Path(r"C:\t900"))
    a = p.parse_args()
    if a.corpus:
        result = corpus(a.corpus, a.out, a.scene, a.emulator, a.bios, a.toolchain, a.song_frames, a.random)
        print(json.dumps(result))
        raise SystemExit(0 if result["c_is_prefix"] else 1)
    if a.out.exists():
        raise SystemExit(f"{a.out}: must be new")
    export = exposed(a.export, a.expose, a.toolchain) if a.expose else a.export
    inputs = json.loads(a.inputs.read_text(encoding="utf-8")) if a.inputs else []
    result = trace(export, a.frames, a.emulator, a.bios, inputs)
    summary = {"frames": a.frames, "distinct_states": len(distinct(result["states"])),
               "drops": result["drops"], "apu_writes": result["write_count"],
               "song_frames": [result["song_frame"][0], result["song_frame"][-1]]}
    a.out.write_text(json.dumps({**summary, "states": result["states"], "song_frame": result["song_frame"],
                                 "writes": result["writes"]}),
                     encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
