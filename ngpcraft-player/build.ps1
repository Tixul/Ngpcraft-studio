# NgpCraft Studio: rebuild the Play window emulator (WebAssembly).
# Compiles bridge.cpp with the NgpCraft desktop core (sibling checkout, not
# copied) into ../appData/wasm/ngpcraft-web/ngpcraft.mjs + ngpcraft.wasm.
# Tools: emsdk from -Emsdk or the EMSDK environment variable (set by
# emsdk_env), Ninja from Qt by default.
param(
    [string]$DesktopSource = (Join-Path $PSScriptRoot '..\..\Ngpcraft_emulator'),
    [string]$Emsdk = $env:EMSDK,
    [string]$Ninja = 'C:\Qt\Tools\Ninja\ninja.exe'
)
# emcmake prints its command on stderr: rely on exit codes, not on stderr.
$ErrorActionPreference = 'Continue'
if (!$Emsdk) { throw 'emsdk not given: pass -Emsdk <folder> or set EMSDK.' }
$emcmake = Join-Path $Emsdk 'upstream\emscripten\emcmake.bat'
if (!(Test-Path -LiteralPath $emcmake)) { throw "Emscripten not found: $emcmake" }
if (!(Test-Path -LiteralPath (Join-Path $DesktopSource 'cpp\include\ngpc_core.h'))) {
    throw "Desktop core not found: $DesktopSource"
}
$build = Join-Path $PSScriptRoot 'build'
& $emcmake cmake -S $PSScriptRoot -B $build -G Ninja "-DCMAKE_MAKE_PROGRAM=$Ninja" `
    "-DNGPCRAFT_DESKTOP=$DesktopSource" -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE) { throw 'CMake configuration failed.' }
cmake --build $build --parallel
if ($LASTEXITCODE) { throw 'Compilation failed.' }
