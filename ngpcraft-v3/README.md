# NgpCraft V3 compiler

The compiler behind NgpCraft Studio. It reads a **GB Studio 4.3.2 project**
(the `.gbsproj` and its resources, plus an optional `ngpcraft.json` with the
NGPC extensions) and exports C code for the **Neo Geo Pocket Color**,
specialised for that game: only the engine parts, events and assets the
project reaches are generated, no universal runtime. The Toshiba TLCS-900
toolchain then builds the ROM.

Python 3.10 or later, standard library only. The editor calls it as a
command-line program; it can also be used on its own.

## Usage

Run from this folder:

```bash
python -m ngpcraft check-toolchain --toolchain C:\t900
python -m ngpcraft export path/to/project --out out/export
python -m ngpcraft build-rom out/export --toolchain C:\t900
```

`export` writes the C sources, the build plan and reports (with `--debug`,
the symbols the editor's debugger reads); `build-rom` compiles and links
them into `rom/game.ngc`. The toolchain folder can also be given by
`NGPC_TOSHIBA_HOME`.

Other commands used by the editor:

| Command | Purpose |
|---|---|
| `plan` | what a project needs, and the GB Studio events that are refused |
| `convert-uge` | convert a hUGETracker song for the NGPC sound chip (lossy, with a report) |
| `convert-sfx` | convert an FX Hammer `.sav` or `.vgm` sound effect to native steps |
| `preview-music` | a ROM that only plays one song (*Listen*) |
| `palette-plan` | native NGPC palettes of a background or sprite sheet |
| `inspect-uge`, `trace-uge`, `inspect-ngps`, `resolve-ngps`, `export-music`, `export-held-score`, `export-music-library` | audio inspection and export |
| `compile-objects` | compile an export without linking |

`python -m ngpcraft <command> --help` gives the options.

## What it supports

All 151 events of GB Studio 4.3.2 are classified, each with its proof
(`ngpcraft/parity/gbstudio.json`): 95 identical, 52 adapted to the NGPC,
4 refused. The 128 engine fields are supported. Scene types: top-down,
platformer, adventure, shoot 'em up, point and click, logo. GB Studio event
plugins are compiled through GB Studio's own plugin loader (needs Node and
the released GB Studio sources); engine plugins are refused. The
differences from GB Studio are listed in `docs/ECARTS_GBSTUDIO.md` (French;
shown in the editor under *Help → NGPC differences from GB Studio*).

## Layout

| Path | Content |
|---|---|
| `ngpcraft/` | the compiler package: project reader, GBVM script compiler, scene and asset compilers, NGPC runtime units (`runtime/`), audio |
| `tests/` | unit tests and ROM tests |
| `tools/` | labs and probes that build ROMs and compare them with GB Studio, frame by frame; `tools/locations.py` says where outside resources are |
| `labs/`, `examples/` | small experiments and sample inputs |
| `toolchains/toshiba/` | where a local toolchain may be placed (not distributed) |

## Tests

```bash
python -m unittest discover -s tests
```

Without extra resources, the tests that need them are skipped:

- `NGC_TEST_TOSHIBA`: Toshiba toolchain folder (builds ROMs);
- `NGC_TEST_EMULATOR`: NgpCraft emulator checkout (runs ROMs on its native
  core) and `NGC_TEST_BIOS`: your own NGPC BIOS dump (not distributed);
- `NGPCRAFT_GBSTUDIO_REFERENCE`: the released GB Studio 4.3.2 sources, for
  the tests that compare with GB Studio; `NGPCRAFT_GBSTUDIO_CORPUS`: GB
  Studio projects used by some labs; `NGPCRAFT_GITHUB`: folder holding
  sibling checkouts (Sound Creator, driver sources);
- `NGC_TEST_NODE_DEPS`, `NGC_TEST_CLANG`, `NGC_TEST_OVER_REV_AUDIO`: optional
  oracles.

## License

MIT (`LICENSE`). Parts taken from GB Studio (GBVM, UGE helpers), the
NgpCraft NGPC base template (music driver) and NGPCraft Sound Creator keep
their notices: `ngpcraft/*LICENSE*.txt`, `ngpcraft/MUSIC_NOTICE.txt`.
