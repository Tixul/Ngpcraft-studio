// Direct desktop-core C ABI; no Libretro dependency.
#include "ngpc_core.h"
#include "bios_hle_data.hpp"
#include <array>
#include <vector>
#include <algorithm>
#include <cstring>

namespace {
ngpc_t* machine = nullptr;
std::vector<uint8_t> rom;
std::array<uint16_t, 160*152> native_pixels{};
std::array<uint8_t, 160*152*4> pixels{};
std::array<int16_t, 8192> samples{};
unsigned sample_frames = 0;
// NgpCraft Studio debugger: CPU breakpoints (VM_STEP), and a frame left
// unfinished by one of them, resumed by the next web_run.
std::vector<uint32_t> breakpoints;
bool mid_frame = false;
constexpr unsigned tail_size = 65536;
struct Save {
    uint32_t magic, version, rom_hash, capacity[2];
    ngpc_rtc_t rtc;
    uint8_t flash[2][tail_size];
    uint32_t checksum;
} save{};
uint32_t hash(const void* bytes, size_t n) {
    auto p = static_cast<const uint8_t*>(bytes);
    uint32_t h = 2166136261u;
    while (n--) h = (h ^ *p++) * 16777619u;
    return h;
}
uint32_t address(unsigned chip, uint32_t capacity) {
    return (chip ? 0x800000u : 0x200000u) + capacity - std::min(capacity, tail_size);
}
void sync_save() {
    if (!machine) return;
    std::memset(&save, 0, sizeof(save));
    save.magic = 0x5747504e; save.version = 1; save.rom_hash = hash(rom.data(), rom.size());
    ngpc_get_rtc(machine, &save.rtc);
    for (unsigned c = 0; c < 2; ++c) {
        save.capacity[c] = ngpc_flash_capacity(machine, c);
        if (save.capacity[c])
            ngpc_read_mem(machine, address(c, save.capacity[c]), save.flash[c], std::min(save.capacity[c], tail_size));
    }
    save.checksum = hash(&save, offsetof(Save, checksum));
}
void reset() {
    ngpc_reset(machine, NGPC_RESET_HANDOFF);
    // Desktop core/bios_fingerprint.py: the data comes from the player's ROM.
    static constexpr char title[16] = "METALSLUG2ND";
    if (rom.size() >= 0x08dcc4+64 && !std::memcmp(rom.data()+0x24, title, 16))
        ngpc_write_mem(machine, 0xa1c0, rom.data()+0x08dcc4, 64);
}
bool restore(const Save& s) {
    if (!machine || s.magic != 0x5747504e || s.version != 1 ||
        s.rom_hash != hash(rom.data(), rom.size()) ||
        s.checksum != hash(&s, offsetof(Save, checksum))) return false;
    // Validate all of the file before mutating any state.
    for (unsigned c = 0; c < 2; ++c) {
        size_t offset = c * 0x200000u;
        if (rom.size() <= offset) { if (s.capacity[c]) return false; continue; }
        // Small homebrews may expose no flash until the game identifies a chip.
        // Padded images may also adopt a chip smaller than the ROM file itself.
        // Validate the hardware range, not the host file length.
        if (s.capacity[c] && (s.capacity[c] < tail_size || s.capacity[c] > 0x200000)) return false;
    }
    for (unsigned c = 0; c < 2; ++c) if (s.capacity[c]) {
        ngpc_set_flash_size(machine, c, s.capacity[c]);
        ngpc_flash_restore(machine, address(c, s.capacity[c]), s.flash[c], std::min(s.capacity[c], tail_size));
    }
    ngpc_set_rtc(machine, &s.rtc);
    ngpc_flash_clear_dirty(machine);
    return true;
}
}
extern "C" {
void web_close() {
    if (machine) ngpc_destroy(machine);
    machine = nullptr; rom.clear(); breakpoints.clear(); mid_frame = false;
}
int web_load(const uint8_t* data, unsigned size) {
    if (!data || size < 64 || size > 0x400000) return 0;
    web_close();
    machine = ngpc_create();
    if (!machine) return 0;
    rom.assign(data, data+size);
    if (ngpc_load_rom(machine, data, size) || ngpc_load_bios(machine, bios_hle, sizeof(bios_hle))) {
        web_close(); return 0;
    }
    ngpc_set_timing_silicon(machine, 10, 8);
    ngpc_set_language(machine, 1);
    ngpc_set_k1ge_console(machine, 0);
    reset(); sync_save(); return 1;
}
int web_run(unsigned mask) {
    if (!machine) return -1;
    // Resuming a frame a breakpoint stopped: the pad was written when it
    // began, and the core never stops on the first instruction of a run.
    if (!mid_frame) {
        uint8_t pad = mask & 0x7f;
        ngpc_write_mem(machine, 0xb0, &pad, 1);
    }
    ngpc_summary_t summary{};
    ngpc_run_frames(machine, 1, 2000000, &summary);
    mid_frame = summary.stop_status == NGPC_BREAKPOINT;
    if (mid_frame) return summary.stop_status;
    ngpc_get_framebuffer(machine, native_pixels.data(), native_pixels.size());
    for (unsigned i = 0; i < native_pixels.size(); ++i) {
        auto p = native_pixels[i];
        pixels[i*4] = (p & 15)*17; pixels[i*4+1] = ((p>>4)&15)*17;
        pixels[i*4+2] = ((p>>8)&15)*17; pixels[i*4+3] = 255;
    }
    sample_frames = ngpc_get_audio(machine, samples.data(), samples.size()/2);
    return summary.stop_status;
}
void* web_video() { return pixels.data(); }
void* web_audio() { return samples.data(); }
unsigned web_audio_frames() { return sample_frames; }
void* web_save() { sync_save(); return &save; }
unsigned web_save_size() { return sizeof(Save); }
int web_save_restore(const void* p, unsigned size) {
    if (!p || size != sizeof(Save)) return 0;
    // Larger than Emscripten's default stack; keep this scratch buffer off-stack.
    static Save candidate; std::memcpy(&candidate, p, size);
    return restore(candidate);
}
void web_reset() {
    if (!machine) return;
    sync_save(); reset(); restore(save);
}
// NgpCraft Studio debugger: console memory access (CPU address space).
int web_read_mem(uint32_t addr, uint8_t* out, unsigned n) {
    if (!machine || !out) return 0;
    return ngpc_read_mem(machine, addr, out, n) == 0;
}
int web_write_mem(uint32_t addr, const uint8_t* in, unsigned n) {
    if (!machine || !in) return 0;
    return ngpc_write_mem(machine, addr, in, n) == 0;
}
// CPU breakpoints (program addresses); n = 0 clears them. web_run then
// returns NGPC_BREAKPOINT (40) with the frame unfinished; the next web_run
// resumes it.
int web_set_breakpoints(const uint32_t* pcs, unsigned n) {
    if (!machine) return 0;
    breakpoints.assign(pcs, pcs + (pcs ? n : 0));
    return ngpc_set_breakpoints(machine, breakpoints.data(), breakpoints.size()) == 0;
}
// A CPU register of the current bank (0..7 = XWA XBC XDE XHL XIX XIY XIZ XSP).
uint32_t web_cpu_reg(unsigned i) {
    if (!machine || i >= NGPC_NREG) return 0;
    ngpc_cpu_t cpu{};
    ngpc_get_cpu(machine, &cpu);
    return cpu.regs[i];
}
}
