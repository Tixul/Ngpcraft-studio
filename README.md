# NgpCraft Studio

NgpCraft Studio makes **Neo Geo Pocket Color** games with the
[GB Studio](https://www.gbstudio.dev) editor. It is a fork of GB Studio
4.3.2: you build scenes, actors and scripts exactly as in GB Studio, and the
project is compiled into an NGPC ROM by the **NgpCraft V3 compiler**
(`ngpcraft-v3/`), which turns each game into C code made only of what that
game uses, then builds it with the Toshiba TLCS-900 toolchain.

> Work in progress. Not affiliated with GB Studio, Chris Maltby or SNK.

## What works

- **GB Studio 4.3.2 compatibility**: projects open and save in the GB Studio
  format. All 151 events of GB Studio 4.3.2 are classified: 95 behave
  identically, 52 are adapted to the NGPC, 4 are refused (printer, Super
  Game Boy colours, *Weapon Attack*). The 128 engine fields are supported.
  GB Studio event plugins are compiled; engine plugins (Game Boy C code)
  are not. *Help → NGPC differences from GB Studio* lists every adaptation.
- **NGPC screen and hardware**: 160 × 152 scenes (144 px GB Studio scenes
  are shown between thin bands), 4-bit colours, a native colour mode with up
  to 16 palettes taken from the PNG, a per-scene NGPC budget (tiles,
  palettes, actors).
- **NGPC planes (advanced mode)**: an optional second background on the
  front plane, with its own scroll speed (fixed, fractions or a percentage
  of the camera) and looping, previewed in the scene view through a movable
  camera window.
- **Audio**: GB Studio songs (`.uge`, `.mod`) and sound effects (FX Hammer
  `.sav`, `.vgm`) are converted for the NGPC sound chip from the editor,
  with a report of what could not be kept; NGPCraft Sound Creator songs
  (`.ngps`) can be linked directly. *Listen* builds a ROM that only plays a
  song.
- **Play and debug**: the Play window runs the ROM in a built-in NGPC
  emulator (WebAssembly), with the GB Studio debugger (breakpoints on
  events, variables, script contexts).
- **Templates**: *NGPC Starter* (a first 160 × 152 scene with the player) and the two GB
  Studio samples, converted (audio and 152 px backgrounds), which build and
  run as they are.

## Requirements

- **Toshiba TLCS-900 toolchain** (`cc900`, `asm900`, `tulink`, `tuconv`), not
  distributed: set its folder in *Preferences → NgpCraft* (asked at first
  start). Linux and macOS run it with Wine.
- **Python 3.10 or later** to run the compiler from source (standard library
  only). The Windows package embeds Python.
- To work on the editor: **Node 21.7.1** (see `.nvmrc`) with Corepack
  (Yarn 4.4.1).

## Running from source

```bash
corepack enable
yarn install
yarn start
```

The editor finds the compiler in `ngpcraft-v3/`. Packages:
`yarn make:win`, `yarn make:linux`, `yarn make:mac`, `yarn make:mac-intel`
(the packaging step embeds the compiler, and Python on Windows).

## Tests

- Editor: `yarn test` (Jest). The NgpCraft tests are in `test/ngpcraft/`;
  those that build ROMs need the compiler and the Toshiba toolchain
  (`NGPC_TOSHIBA_HOME`, default `C:\t900`) and are skipped otherwise.
- Compiler: `cd ngpcraft-v3 && python -m unittest discover -s tests`. Tests
  that build and run ROMs need `NGC_TEST_TOSHIBA`, `NGC_TEST_EMULATOR`
  (NgpCraft emulator checkout) and `NGC_TEST_BIOS` (your own BIOS dump, not
  distributed); tests that compare with GB Studio need the released GB
  Studio 4.3.2 sources (`NGPCRAFT_GBSTUDIO_REFERENCE`, see
  `ngpcraft-v3/tools/locations.py`). Without them, those tests are skipped.

## Repository layout

| Path | Content |
|---|---|
| `src/`, `test/`, `appData/` | the editor (GB Studio 4.3.2 with the NgpCraft changes); NgpCraft code is mostly under `src/**/ngpcraft/` |
| `ngpcraft-v3/` | the NgpCraft V3 compiler (Python), its tests and labs |
| `ngpcraft-player/` | sources of the Play window emulator build (`appData/wasm/ngpcraft-web` is the built copy) |
| `appData/templates/ngpc/` | the *NGPC Starter* template |

Branches: `ngpcraft` is NgpCraft Studio, based on the GB Studio 4.3.2
release; `develop` follows GB Studio's own development (future 5.0, new
project format) and is not used by NgpCraft.

## License and credits

MIT. GB Studio is Copyright (c) 2019-2026 Chris Maltby and its contributors
(`LICENSE`); the NgpCraft additions are released under the same license
(`ngpcraft-v3/LICENSE`). Third-party notices: `ngpcraft-v3/ngpcraft/*LICENSE*.txt`,
`ngpcraft-v3/ngpcraft/MUSIC_NOTICE.txt`,
`appData/wasm/ngpcraft-web/THIRD_PARTY_NOTICES.txt`.

GB Studio: [gbstudio.dev](https://www.gbstudio.dev) ·
[source](https://github.com/chrismaltby/gb-studio).
