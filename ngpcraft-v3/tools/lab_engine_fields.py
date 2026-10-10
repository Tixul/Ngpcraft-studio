"""Engine fields (engine.json; catalogue parity/gbstudio.json "engine_fields")
proved in native ROMs against the original C (docs/M6_ENGINE_FIELDS.md).

A campaign is one scene and one input schedule: its base, then variants that
change engine field values. A variant proves its fields when
  - its ROM matches the original C at every tick (tools/platform_oracle.py,
    tools/genre_oracle.py; tools/lab_projectile_pool.py for the projectile
    kernel), and
  - its reference trace differs from the trace it is compared to (the base, or
    the variant named by `vs`): the field was exercised, not merely present.
An `inert` variant must instead leave the trace unchanged: a GB Studio no-op
the port reproduces (runtime variables reset by the state's init,
DASH_FROM_LADDER = 0x3 in platform.c).

  python -B -m tools.lab_engine_fields --out <new dir> --toolchain C:\\t900
         --emulator <dir> --bios <file> --clang <clang.exe> [--campaign NAME]
"""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

from tools.demo_gbs_scene import write_json

UP, DOWN, LEFT, RIGHT, A, B = 1, 2, 4, 8, 16, 32
HURT = "Hurt"           # a gbs2 sprite state (statesOrder index 3): animation fields load it


def v(*proves, vs=None, inert=False, state=None, **fields):
    """A variant: the engine fields it proves, its field values (over the campaign
    base), the trace it is compared to, the platformer state its trigger sets."""
    return {"proves": proves, "vs": vs, "inert": inert, "state": state, "fields": fields}


def cam(x):
    """Scene On Init: an instant Camera Move To (tiles) that stops the follow."""
    return [{"command": "EVENT_CAMERA_MOVE_TO", "id": "cam-fix",
             "args": {"x": {"type": "number", "value": x}, "y": {"type": "number", "value": 0},
                      "speed": 0, "units": "tiles"}}]


def adventure_state(key, state):
    return [{"command": "EVENT_ADVENTURE_STATE_SET", "args": {"state": state}, "id": key}]


SHELF = {(x, 11): 1 for x in range(8, 17)}                    # one-way platform (COLLISION_TOP)
LADDER = {(12, y): 0x10 for y in range(5, 14)}
SHORT_LADDER = {(12, y): 0x10 for y in range(10, 14)}
WALL = {(14, y): 15 for y in range(2, 14)}
CHIMNEY = {**{(14, y): 15 for y in range(0, 10)}, **{(18, y): 15 for y in range(0, 14)}}
DASH_B = {"FEAT_PLATFORM_DASH": 1, "INPUT_PLATFORM_DASH": "DASH_INPUT_B"}
ADV_DASH_A = {"FEAT_ADVENTURE_DASH": 1, "INPUT_ADVENTURE_DASH": "DASH_INPUT_A"}
DASH_SCHEDULE = [(20, 60, RIGHT), (30, 32, B), (50, 52, B), (80, 84, A), (88, 90, B), (92, 95, A),
                 (120, 180, LEFT), (130, 132, B), (136, 139, A), (200, 260, RIGHT), (230, 232, B),
                 (300, 380, RIGHT), (330, 332, B)]
MOVES = [(10, 40, UP | LEFT), (50, 80, DOWN | RIGHT), (90, 120, UP | RIGHT), (130, 160, DOWN | LEFT),
         (170, 200, LEFT), (210, 240, RIGHT), (250, 280, UP), (290, 320, DOWN)]

CAMPAIGNS = {
    # platform_oracle.SCHEDULE: walk, run, jumps, slopes, a fall, a drop-through attempt.
    "plat-move": dict(engine="platform", variants={
        "walk_vel": v("plat_walk_vel", plat_walk_vel=4000), "walk_acc": v("plat_walk_acc", plat_walk_acc=400),
        "min_vel": v("plat_min_vel", plat_min_vel=1500), "turn_acc": v("plat_turn_acc", plat_turn_acc=200),
        "dec": v("plat_dec", plat_dec=60), "grav": v("plat_grav", plat_grav=1200),
        "max_fall_vel": v("plat_max_fall_vel", plat_max_fall_vel=6000), "air_dec": v("plat_air_dec", plat_air_dec=30),
        "air_control": v("plat_air_control", plat_air_control=0),
        "run_off": v("FEAT_PLATFORM_RUN", FEAT_PLATFORM_RUN=0),
        "run_on_a": v("INPUT_PLATFORM_RUN", INPUT_PLATFORM_RUN="INPUT_A"),
        "run_smooth": v("PLATFORM_RUN_STYLE", PLATFORM_RUN_STYLE="RUN_STYLE_SMOOTH"),
        "run_instant": v("PLATFORM_RUN_STYLE", PLATFORM_RUN_STYLE="RUN_STYLE_INSTANT"),
        "run_vel": v("plat_run_vel", plat_run_vel=7000), "run_acc": v("plat_run_acc", plat_run_acc=500),
        "jump_off": v("FEAT_PLATFORM_JUMP", FEAT_PLATFORM_JUMP=0),
        "jump_on_b": v("INPUT_PLATFORM_JUMP", INPUT_PLATFORM_JUMP="INPUT_B"),
        "jump_vel": v("plat_jump_vel", plat_jump_vel=12000),
        "jump_hold": v("plat_jump_hold_vel", "plat_jump_hold_frames", plat_jump_hold_vel=1500, plat_jump_hold_frames=10),
        "hold_grav": v("plat_hold_grav", plat_hold_grav=1200), "run_boost": v("plat_run_boost", plat_run_boost=2000),
        "slopes_off": v("FEAT_PLATFORM_SLOPES", FEAT_PLATFORM_SLOPES=0),
        # State variables: platform_init sets them, as in GBVM; the project value changes nothing.
        **{f"state_{key}": v(key, inert=True, **{key: value}) for key, value in (
            ("plat_vel_x", 3000), ("plat_vel_y", -8000), ("plat_grounded", 1), ("plat_is_actor_attached", 1),
            ("plat_run_stage", 2), ("plat_jump_type", 1), ("plat_extra_jumps_counter", 3),
            ("plat_wall_jump_counter", 2), ("plat_wall_col", 1), ("plat_next_state", "PLATFORM_JUMP_STATE"))}}),
    # Camera fixed by a Camera Move To: the player reaches the screen edges.
    "plat-camera-right": dict(engine="platform", script=cam(2), variants={
        "right": v("plat_camera_block", plat_camera_block="PLATFORM_CAMERA_LOCK_SCREEN_RIGHT"),
        "both": v("plat_camera_block", plat_camera_block="PLATFORM_CAMERA_LOCK_SCREEN_BOTH")}),
    "plat-camera-left": dict(engine="platform", script=cam(35), variants={
        "left": v("plat_camera_block", plat_camera_block="PLATFORM_CAMERA_LOCK_SCREEN_LEFT")}),
    # Ladder: climb, jump, descend with a drop, walk off the bottom, sides.
    "plat-ladder": dict(engine="platform", tiles=LADDER, frames=520, schedule=[
        (20, 62, RIGHT), (95, 115, UP), (125, 128, A), (150, 170, UP), (175, 215, DOWN), (182, 185, A),
        (220, 230, UP), (240, 244, RIGHT), (270, 300, LEFT), (300, 325, UP), (335, 338, DOWN | A), (360, 380, UP),
        (390, 394, LEFT), (420, 450, RIGHT), (470, 500, LEFT)], variants={
        "off": v("FEAT_PLATFORM_LADDERS", FEAT_PLATFORM_LADDERS=0), "climb_vel": v("plat_climb_vel", plat_climb_vel=2000),
        "jump": v("FEAT_PLATFORM_LADDERS_JUMP", FEAT_PLATFORM_LADDERS_JUMP=1),
        "drop": v("FEAT_PLATFORM_LADDERS_DROP", FEAT_PLATFORM_LADDERS_DROP=0),
        "walk_off": v("FEAT_PLATFORM_LADDERS_WALK_OFF", FEAT_PLATFORM_LADDERS_WALK_OFF=0),
        "anim": v("PLATFORM_LADDERS_ANIM", PLATFORM_LADDERS_ANIM=HURT)}),
    "plat-ladder-top": dict(engine="platform", tiles=SHORT_LADDER, frames=420, schedule=[
        (20, 62, RIGHT), (95, 150, UP), (200, 215, UP), (215, 230, DOWN), (222, 225, A), (260, 280, UP),
        (290, 330, DOWN)], variants={"hop": v("FEAT_PLATFORM_LADDERS_HOP", FEAT_PLATFORM_LADDERS_HOP=0)}),
    # platform.c: DASH_FROM_LADDER is 0x3, so the ladder box changes nothing (GB Studio 4.3.2).
    "plat-ladder-dash": dict(engine="platform", tiles=LADDER, frames=200, base=DASH_B,
                             schedule=[(20, 62, RIGHT), (95, 115, UP), (121, 123, B)],
                             variants={"from_ladder": v("plat_dash_from", inert=True, plat_dash_from=7)}),
    # One-way shelf: jumps onto it, double jumps, float, running jump with a turn.
    "plat-air": dict(engine="platform", tiles=SHELF, frames=400, schedule=[
        (20, 140, RIGHT), (40, 44, A), (52, 56, A), (64, 68, A), (80, 110, A | UP), (170, 230, RIGHT | B),
        (200, 204, A), (204, 225, LEFT)], variants={
        "double": v("FEAT_PLATFORM_DOUBLE_JUMP", FEAT_PLATFORM_DOUBLE_JUMP=1),
        "extra_jumps": v("plat_extra_jumps", vs="double", FEAT_PLATFORM_DOUBLE_JUMP=1, plat_extra_jumps=3),
        "reduction": v("plat_jump_reduction", vs="extra_jumps", FEAT_PLATFORM_DOUBLE_JUMP=1, plat_extra_jumps=3,
                       plat_jump_reduction=3000),
        "float": v("FEAT_PLATFORM_FLOAT", FEAT_PLATFORM_FLOAT=1),
        "float_vel": v("plat_float_vel", vs="float", FEAT_PLATFORM_FLOAT=1, plat_float_vel=300),
        "float_inactive": v("plat_float_active", vs="float", FEAT_PLATFORM_FLOAT=1, plat_float_active=0),
        "float_up": v("INPUT_PLATFORM_FLOAT", vs="float", FEAT_PLATFORM_FLOAT=1, INPUT_PLATFORM_FLOAT="FLOAT_INPUT_HOLD_UP"),
        "float_anim": v("PLATFORM_FLOAT_ANIM", vs="float", FEAT_PLATFORM_FLOAT=1, PLATFORM_FLOAT_ANIM=HURT),
        "fall_anim": v("PLATFORM_FALL_ANIM", PLATFORM_FALL_ANIM=HURT),
        "run_anim": v("PLATFORM_RUN_ANIM", PLATFORM_RUN_ANIM=HURT),
        "jump_anim": v("PLATFORM_JUMP_ANIM", PLATFORM_JUMP_ANIM=HURT),
        "turn_control": v("plat_turn_control", plat_turn_control=0)}),
    "plat-drop": dict(engine="platform", tiles=SHELF, frames=200, schedule=[
        (20, 50, RIGHT), (40, 44, A), (80, 100, DOWN), (84, 87, A), (86, 96, A)], variants={
        "off": v("FEAT_PLATFORM_DROP_THROUGH", FEAT_PLATFORM_DROP_THROUGH=0),
        "inactive": v("plat_drop_through_active", plat_drop_through_active=0),
        "down_hold": v("INPUT_PLATFORM_DROP_THROUGH", INPUT_PLATFORM_DROP_THROUGH="DROP_THRU_INPUT_DOWN_HOLD"),
        "down_tap": v("INPUT_PLATFORM_DROP_THROUGH", INPUT_PLATFORM_DROP_THROUGH="DROP_THRU_INPUT_DOWN_TAP")}),
    # Both held since the air: only "down + jump held" drops on landing.
    "plat-drop-held": dict(engine="platform", tiles=SHELF, frames=160, schedule=[
        (20, 50, RIGHT), (40, 120, A), (50, 120, DOWN)], variants={
        "jump_hold": v("INPUT_PLATFORM_DROP_THROUGH", INPUT_PLATFORM_DROP_THROUGH="DROP_THRU_INPUT_DOWN_JUMP_HOLD")}),
    # Walk off the shelf, jump 5 frames later (coyote) / 2 frames before landing (buffer).
    "plat-coyote": dict(engine="platform", tiles=SHELF, frames=220, schedule=[
        (20, 50, RIGHT), (40, 44, A), (80, 200, RIGHT), (156, 159, A)], variants={
        "on": v(FEAT_PLATFORM_COYOTE_TIME=1),
        "frames": v("FEAT_PLATFORM_COYOTE_TIME", "plat_coyote_frames", FEAT_PLATFORM_COYOTE_TIME=1, plat_coyote_frames=8)}),
    "plat-buffer": dict(engine="platform", tiles=SHELF, frames=220, schedule=[
        (20, 50, RIGHT), (40, 44, A), (80, 200, RIGHT), (157, 159, A)], variants={
        "on": v(FEAT_PLATFORM_COYOTE_TIME=1),
        "frames": v("plat_jump_buffer_frames", vs="on", FEAT_PLATFORM_COYOTE_TIME=1, plat_jump_buffer_frames=12)}),
    "plat-dash": dict(engine="platform", tiles=SHELF, frames=420, base=DASH_B, schedule=DASH_SCHEDULE, variants={
        "off": v("FEAT_PLATFORM_DASH", FEAT_PLATFORM_DASH=0), "inactive": v("plat_dash_active", plat_dash_active=0),
        "anim": v("PLATFORM_DASH_ANIM", PLATFORM_DASH_ANIM=HURT),
        "input_a": v("INPUT_PLATFORM_DASH", INPUT_PLATFORM_DASH="DASH_INPUT_A"),
        "input_down_jump": v("INPUT_PLATFORM_DASH", INPUT_PLATFORM_DASH="DASH_INPUT_DOWN_JUMP"),
        "input_double_tap": v("INPUT_PLATFORM_DASH", INPUT_PLATFORM_DASH="DASH_INPUT_DOUBLE_TAP"),
        "input_interact": v("INPUT_PLATFORM_DASH", "INPUT_PLATFORM_INTERACT", INPUT_PLATFORM_DASH="DASH_INPUT_INTERACT",
                            INPUT_PLATFORM_INTERACT="INPUT_DOWN"),
        "from_ground": v("plat_dash_from", plat_dash_from=1), "from_air": v("plat_dash_from", plat_dash_from=2),
        "through_walls": v("plat_dash_mask", plat_dash_mask=239), "dist": v("plat_dash_dist", plat_dash_dist=4000),
        "frames": v("plat_dash_frames", plat_dash_frames=6), "ready": v("plat_dash_ready_frames", plat_dash_ready_frames=60),
        "deadzone": v("plat_dash_deadzone", plat_dash_deadzone=10),
        "jump_from_none": v("plat_dash_jump_from", plat_dash_jump_from=0),
        "jump_from_air": v("plat_dash_jump_from", plat_dash_jump_from=3),
        "gravity": v("FEAT_PLATFORM_DASH_USE_GRAVITY", FEAT_PLATFORM_DASH_USE_GRAVITY=1)}),
    "plat-dash-solid": dict(engine="platform", platform=("solid", 96, 136, 104), frames=300, base=DASH_B, schedule=[
        (20, 140, RIGHT), (50, 52, B), (150, 154, A), (150, 200, RIGHT), (160, 162, B), (220, 280, LEFT),
        (240, 242, B)], variants={"through_actors": v("plat_dash_mask", plat_dash_mask=251)}),
    "plat-wall": dict(engine="platform", tiles=WALL, frames=300, base={"FEAT_PLATFORM_WALL_JUMP": 1}, schedule=[
        (20, 140, RIGHT), (70, 74, A), (95, 98, A), (100, 130, RIGHT), (120, 123, A), (150, 154, A),
        (160, 200, RIGHT), (175, 178, A), (185, 188, A)], variants={
        "off": v("FEAT_PLATFORM_WALL_JUMP", FEAT_PLATFORM_WALL_JUMP=0), "kick": v("plat_wall_kick", plat_wall_kick=2500),
        "slide": v("plat_wall_slide", plat_wall_slide=1),
        "slide_vel": v("plat_wall_slide_vel", vs="slide", plat_wall_slide=1, plat_wall_slide_vel=300),
        "slide_anim": v("PLATFORM_WALL_SLIDE_ANIM", vs="slide", plat_wall_slide=1, PLATFORM_WALL_SLIDE_ANIM=HURT)}),
    # Two facing walls: chained wall jumps without landing.
    "plat-chimney": dict(engine="platform", tiles=CHIMNEY, frames=260,
                         base={"FEAT_PLATFORM_WALL_JUMP": 1, "plat_jump_vel": 26000}, schedule=[
        (20, 121, RIGHT), (100, 103, A), (110, 113, A), (121, 150, LEFT), (128, 131, A), (150, 175, RIGHT),
        (156, 159, A)], variants={"wall_jump_max": v("plat_wall_jump_max", plat_wall_jump_max=3)}),
    # Set Platformer State from triggers (platform_oracle scripted "effects").
    "plat-states": dict(engine="scripted", kind="effects", variants={
        "knockback": v(state="knockback"),
        "knockback_off": v("FEAT_PLATFORM_KNOCKBACK", vs="knockback", state="knockback", FEAT_PLATFORM_KNOCKBACK=0),
        "knockback_vel_x": v("plat_knockback_vel_x", vs="knockback", state="knockback", plat_knockback_vel_x=3000),
        "knockback_vel_y": v("plat_knockback_vel_y", vs="knockback", state="knockback", plat_knockback_vel_y=4000),
        "knockback_frames": v("plat_knockback_frames", vs="knockback", state="knockback", plat_knockback_frames=8),
        "knockback_anim": v("PLATFORM_KNOCKBACK_ANIM", vs="knockback", state="knockback", PLATFORM_KNOCKBACK_ANIM=""),
        "blank": v(state="blank"),
        "blank_off": v("FEAT_PLATFORM_BLANK", vs="blank", state="blank", FEAT_PLATFORM_BLANK=0),
        "blank_grav": v("plat_blank_grav", vs="blank", state="blank", plat_blank_grav=900),
        "blank_anim": v("PLATFORM_BLANK_ANIM", vs="blank", state="blank", PLATFORM_BLANK_ANIM="")}),
    # Trigger, sign to talk to, hurting actor (scripted "basic"), extra presses inside the trigger.
    "plat-inputs": dict(engine="scripted", kind="basic", schedule=[
        (20, 40, RIGHT), (48, 50, A), (60, 160, RIGHT), (200, 260, LEFT), (285, 287, UP), (300, 302, UP),
        (305, 307, A), (310, 312, B), (320, 340, LEFT), (345, 347, A)], variants={
        "force_none": v("INPUT_PLATFORM_FORCE_TRIGGER", INPUT_PLATFORM_FORCE_TRIGGER="FALSE"),
        "force_a": v("INPUT_PLATFORM_FORCE_TRIGGER", INPUT_PLATFORM_FORCE_TRIGGER="INPUT_A_PRESSED"),
        "force_b": v("INPUT_PLATFORM_FORCE_TRIGGER", INPUT_PLATFORM_FORCE_TRIGGER="INPUT_B_PRESSED"),
        "interact_b": v("INPUT_PLATFORM_INTERACT", INPUT_PLATFORM_INTERACT="INPUT_B"),
        "interact_up": v("INPUT_PLATFORM_INTERACT", INPUT_PLATFORM_INTERACT="INPUT_UP")}),
    "plat-dash-trigger": dict(engine="scripted", kind="basic", base=DASH_B, schedule=[
        (20, 40, RIGHT), (60, 160, RIGHT), (64, 66, B), (80, 82, B), (200, 260, LEFT), (205, 207, B), (225, 227, B)],
        variants={"through_triggers": v("plat_dash_mask", plat_dash_mask=247)}),
    # Adventure (genre_oracle, Sample Town without its actors).
    "adv-move": dict(engine="genre", kind="adventure", frames=400, schedule=MOVES + [
        (330, 350, RIGHT), (350, 370, LEFT), (370, 390, UP), (390, 400, DOWN)], variants={
        "walk_vel": v("adv_walk_vel", adv_walk_vel=2000), "walk_acc": v("adv_walk_acc", adv_walk_acc=200),
        "dec": v("adv_dec", adv_dec=100), "turn_acc": v("adv_turn_acc", adv_turn_acc=100),
        "four_way": v("INPUT_ADVENTURE_MOVE_TYPE", INPUT_ADVENTURE_MOVE_TYPE="MOVE_TYPE_4_WAY"),
        "horizontal": v("INPUT_ADVENTURE_DIRECTION_TYPE", INPUT_ADVENTURE_DIRECTION_TYPE="DIRECTION_TYPE_HORIZONTAL"),
        # adventure_init sets them, as in GBVM.
        "state_vel_x": v("adv_vel_x", inert=True, adv_vel_x=500),
        "state_vel_y": v("adv_vel_y", inert=True, adv_vel_y=500),
        "state_next": v("adv_next_state", inert=True, adv_next_state="ADVENTURE_RUN_STATE")}),
    "adv-knockback": dict(engine="genre", kind="adventure", frames=200, schedule=[(10, 200, RIGHT)],
                          triggers=[(14, 18, 1, 5, adventure_state("kb", "knockback"), None)], variants={
        "off": v("FEAT_ADVENTURE_KNOCKBACK", FEAT_ADVENTURE_KNOCKBACK=0),
        "vel_x": v("adv_knockback_vel_x", adv_knockback_vel_x=2000),
        "frames": v("adv_knockback_frames", adv_knockback_frames=30),
        "anim": v("ADVENTURE_KNOCKBACK_ANIM", ADVENTURE_KNOCKBACK_ANIM=HURT)}),
    "adv-knockback-diagonal": dict(engine="genre", kind="adventure", frames=160, schedule=[(10, 150, UP | RIGHT)],
                                   triggers=[(18, 15, 1, 6, adventure_state("kb", "knockback"), None)],
                                   variants={"vel_y": v("adv_knockback_vel_y", adv_knockback_vel_y=2000)}),
    "adv-blank": dict(engine="genre", kind="adventure", frames=160, schedule=[(10, 150, RIGHT)],
                      triggers=[(14, 18, 1, 5, adventure_state("bl", "blank"), None)], variants={
        "off": v("FEAT_ADVENTURE_BLANK", FEAT_ADVENTURE_BLANK=0), "anim": v("ADVENTURE_BLANK_ANIM", ADVENTURE_BLANK_ANIM=HURT)}),
    "adv-run": dict(engine="genre", kind="adventure", frames=200, base={"FEAT_ADVENTURE_RUN": 1}, schedule=[
        (10, 90, RIGHT), (30, 70, B), (100, 180, DOWN | LEFT), (120, 190, B)], variants={
        "off": v("FEAT_ADVENTURE_RUN", FEAT_ADVENTURE_RUN=0), "anim": v("ADVENTURE_RUN_ANIM", ADVENTURE_RUN_ANIM=HURT),
        "on_a": v("INPUT_ADVENTURE_RUN", INPUT_ADVENTURE_RUN="INPUT_A"), "vel": v("adv_run_vel", adv_run_vel=9000),
        "acc": v("adv_run_acc", adv_run_acc=200)}),
    "adv-dash": dict(engine="genre", kind="adventure", frames=200, base=ADV_DASH_A, schedule=[
        (10, 30, RIGHT), (20, 21, A), (30, 33, A), (60, 80, DOWN), (70, 71, A), (90, 91, A), (120, 140, LEFT),
        (130, 131, A), (150, 154, LEFT), (158, 170, LEFT), (165, 166, B)], variants={
        "off": v("FEAT_ADVENTURE_DASH", FEAT_ADVENTURE_DASH=0), "inactive": v("adv_dash_active", adv_dash_active=0),
        "anim": v("ADVENTURE_DASH_ANIM", ADVENTURE_DASH_ANIM=HURT),
        "input_b": v("INPUT_ADVENTURE_DASH", INPUT_ADVENTURE_DASH="DASH_INPUT_B"),
        "input_double_tap": v("INPUT_ADVENTURE_DASH", INPUT_ADVENTURE_DASH="DASH_INPUT_DOUBLE_TAP"),
        "input_interact": v("INPUT_ADVENTURE_DASH", "INPUT_ADVENTURE_INTERACT", INPUT_ADVENTURE_DASH="DASH_INPUT_INTERACT",
                            INPUT_ADVENTURE_INTERACT="INPUT_B"),
        "dist": v("adv_dash_dist", adv_dash_dist=4000), "frames": v("adv_dash_frames", adv_dash_frames=6),
        "ready": v("adv_dash_ready_frames", adv_dash_ready_frames=60),
        "deadzone": v("adv_dash_deadzone", adv_dash_deadzone=5)}),
    "adv-dash-wall": dict(engine="genre", kind="adventure", frames=120, base=ADV_DASH_A,
                          walls=[(18, y, 15) for y in range(17, 24)], schedule=[(10, 20, RIGHT), (14, 15, A)],
                          variants={"through_walls": v("adv_dash_mask", adv_dash_mask=239)}),
    "adv-dash-trigger": dict(engine="genre", kind="adventure", frames=120, base=ADV_DASH_A,
                             triggers=[(16, 19, 1, 3, "1", "2")], schedule=[(10, 20, RIGHT), (14, 15, A)],
                             variants={"through_triggers": v("adv_dash_mask", adv_dash_mask=247)}),
    "adv-dash-solid": dict(engine="genre", kind="adventure", frames=120, base=ADV_DASH_A,
                           actors=[(128, 160, "", "3", ("solid",))], schedule=[(10, 20, RIGHT), (14, 15, A)],
                           variants={"through_actors": v("adv_dash_mask", adv_dash_mask=251)}),
    "adv-push": dict(engine="genre", kind="adventure", frames=160, base={"FEAT_ADVENTURE_PUSH": 1},
                     schedule=[(10, 120, LEFT), (125, 135, RIGHT)], variants={
        "off": v("FEAT_ADVENTURE_PUSH", FEAT_ADVENTURE_PUSH=0), "anim": v("ADVENTURE_PUSH_ANIM", ADVENTURE_PUSH_ANIM=HURT),
        "delay": v("adv_push_delay_frames", adv_push_delay_frames=20)}),
    # Shoot'em up (Space Battle).
    "shmup-move": dict(engine="genre", kind="shmup", frames=340, schedule=MOVES,
                       base={"SHOOTER_MOVEMENT_TYPE": "MOVEMENT_TYPE_FREE"}, variants={
        "locked": v("SHOOTER_MOVEMENT_TYPE", SHOOTER_MOVEMENT_TYPE="MOVEMENT_TYPE_LOCK_PERPENDICULAR")}),
    "shmup-scroll": dict(engine="genre", kind="shmup", frames=300, schedule=[],
                         variants={"speed": v("shooter_scroll_speed", shooter_scroll_speed=255)}),
    "shmup-triggers": dict(engine="genre", kind="shmup", frames=140, schedule=[],
                           triggers=[(20, 0, 1, 18, "1", "2"), (25, 0, 1, 18, "3", None)], variants={
        "screen": v("SHOOTER_TRIGGER_ACTIVATION", SHOOTER_TRIGGER_ACTIVATION="ON_SCREEN_ENTER")}),
    "shmup-wall": dict(engine="genre", kind="shmup", frames=100, schedule=[(2, 95, DOWN)],
                       walls=[(x, 12, 15) for x in range(14)], player_hits=True, variants={
        "group_2": v("SHOOTER_WALL_COLLISION_GROUP", SHOOTER_WALL_COLLISION_GROUP="COLLISION_GROUP_2")}),
    # Projectile kernel against GBVM projectiles.c (tools/lab_projectile_pool.py).
    "projectile-spread": dict(engine="projectiles", variants={
        "spread_2": v("PROJECTILES_COLLISION_SPREAD", PROJECTILES_COLLISION_SPREAD="SPREAD_2"),
        "every_frame": v("PROJECTILES_COLLISION_SPREAD", PROJECTILES_COLLISION_SPREAD="EVERY_FRAME")}),
}

# Proved elsewhere, against a reference of the same original code.
ELSEWHERE = {
    "topdown_grid": "tests/test_topdown.py: 8 and 16 px grids, native ROM against the topdown.c model",
    "INPUT_TOPDOWN_INTERACT": "tests/test_topdown.py: A and B, native ROM against the topdown.c model",
    "fade_style": "docs/ECARTS_GBSTUDIO.md §13: fades to white and black",
}


def proved_fields():
    """{engine field: [campaign/variant]} of the variants that prove it."""
    out = {}
    for name, campaign in CAMPAIGNS.items():
        for key, variant in campaign["variants"].items():
            for field in variant["proves"]:
                out.setdefault(field, []).append(f"{name}/{key}")
    return out


def _fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def run_variant(out, campaign, fields, state, toolchain, emulator, bios, clang):
    """(mismatches, trace fingerprint) of one ROM (or projectile kernel) run."""
    engine = campaign["engine"]
    with contextlib.redirect_stdout(io.StringIO()):
        if engine == "platform":
            from tools.platform_oracle import SCHEDULE, run
            result = run(out, toolchain, emulator, bios, clang, frames=campaign.get("frames", 720),
                         schedule=[list(s) for s in campaign.get("schedule", SCHEDULE)], fields=fields or None,
                         script=campaign.get("script"), tiles=campaign.get("tiles"), platform=campaign.get("platform"))
            comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
            return result["mismatches"], _fingerprint(comparison["reference"])
        if engine == "scripted":
            from tools.platform_oracle import EFFECTS, SCRIPTED, run_scripted
            default = EFFECTS if campaign["kind"] == "effects" else SCRIPTED
            result = run_scripted(out, toolchain, emulator, bios, clang, frames=campaign.get("frames", 420),
                                  schedule=[list(s) for s in campaign.get("schedule", default)], kind=campaign["kind"],
                                  state=state or "jump", fields=fields or None)
            comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
            return result["mismatches"] + (not result["same_order"]), _fingerprint(comparison["reference"], comparison["log"])
        if engine == "genre":
            from tools.genre_oracle import run
            case = {k: campaign[k] for k in ("walls", "triggers", "actors", "player_hits") if k in campaign}
            result = run(out, campaign["kind"], toolchain, emulator, bios, clang, campaign["frames"],
                         [list(s) for s in campaign["schedule"]], keep_actors=False, fields=fields or None, **case)
            comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
            return result["mismatches"], _fingerprint(comparison["reference"], comparison["log"])
        if engine == "projectiles":
            from tools.lab_projectile_pool import run
            result = run(out, clang, spread=fields.get("PROJECTILES_COLLISION_SPREAD", "SPREAD_4"))
            return int(result["returncode"] != 0), _fingerprint(result["stdout"])
    raise ValueError(f"unknown engine {engine!r}")


def run_campaign(out, name, toolchain, emulator, bios, clang):
    campaign = CAMPAIGNS[name]
    base = dict(campaign.get("base", {}))
    traces, rows = {}, []
    mismatches, traces["base"] = run_variant(Path(out) / name / "base", campaign, base, None,
                                             toolchain, emulator, bios, clang)
    rows.append({"variant": "base", "mismatches": mismatches, "proves": [], "ok": mismatches == 0})
    for key, variant in campaign["variants"].items():
        mismatches, traces[key] = run_variant(Path(out) / name / key, campaign, {**base, **variant["fields"]},
                                              variant["state"], toolchain, emulator, bios, clang)
        changed = traces[key] != traces[variant["vs"] or "base"]
        rows.append({"variant": key, "mismatches": mismatches, "vs": variant["vs"] or "base", "changed": changed,
                     "inert": variant["inert"], "proves": list(variant["proves"]),
                     # A variant proving nothing is a comparison point: matching is enough.
                     "ok": mismatches == 0 and (changed != variant["inert"] or not variant["proves"])})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--toolchain", required=True)
    p.add_argument("--emulator", required=True)
    p.add_argument("--bios", required=True)
    p.add_argument("--clang", required=True)
    p.add_argument("--campaign", choices=tuple(CAMPAIGNS), action="append")
    a = p.parse_args()
    if a.out.exists():
        p.error("output must be new")
    report = {name: run_campaign(a.out, name, a.toolchain, a.emulator, a.bios, a.clang)
              for name in a.campaign or CAMPAIGNS}
    write_json(a.out / "engine-fields.json", report)
    failed = [f"{name}/{row['variant']}" for name, rows in report.items() for row in rows if not row["ok"]]
    print(json.dumps({"variants": sum(len(rows) for rows in report.values()), "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
