"""Delivered GB Studio events and the script frame (compileEntityEvents.ts).

Each handler is the `compile` function of the event's file in
src/lib/events/, translated line for line. An event is delivered only with its
contract, generator, laboratory and ROM probe (cahier §6.2); the delivered
set is ngpcraft.parity.IMPLEMENTED_EVENTS, which must equal HANDLERS.
"""
import math

from ..compiler import CompileError
from ..project_sfx import compile_event as compile_sfx_event, compile_text_event as compile_text_sound_event
from .builder import _UNDEFINED, ScriptBuilder


def _event_music_play(input_, b):
    """eventMusicPlay.js, GB Studio 4.3.2 published."""
    b.music_play(input_.get("musicId"))


def _event_music_stop(input_, b):
    """eventMusicStop.js, GB Studio 4.3.2 published."""
    b.music_stop()


def _event_link_transfer(input_, b):
    """eventLinkTransfer.js. vm_sio.c copies at most LINK_MAX_PACKET_LENGTH = 32
    bytes, silently: a packet over 16 variables is refused (docs/M6_LINK.md)."""
    size = input_.get("size")
    if type(size) not in (int, float) or size != int(size) or not 0 <= size <= 16:
        raise CompileError(f"Link Transfer: packet size {size!r} must be an integer 0..16 "
                           "(GBVM sends at most 32 bytes = 16 variables)")
    b.link_transfer(input_.get("sendVariable"), input_.get("receiveVariable"), int(size))


def _event_mute_channel(input_, b):
    """eventMuteChannel.js: `input.channels || []`, then indexOf (strict
    equality on numbers). A non-array value is refused (indexOf would search
    a string)."""
    channels = input_.get("channels")
    if not channels:
        channels = []
    if not isinstance(channels, list):
        raise CompileError(f"Mute Channel: channels {channels!r} must be a list of 0..3")
    has = lambda k: any(type(c) in (int, float) and c == k for c in channels)
    b.music_set_mute_mask(has(0), has(1), has(2), has(3))


def _event_actor_get_direction(input_, b):
    """eventActorGetDirection.js: enum direction, not angle256."""
    b.actor_set_active(input_.get("actorId"))
    b.actor_get_direction(input_.get("direction"))


def _event_actor_get_position(input_, b):
    """eventActorGetPosition.js."""
    b.actor_set_active(input_.get("actorId"))
    b.actor_get_position(input_.get("vectorX"), input_.get("vectorY"), input_.get("units", _UNDEFINED))


def _event_actor_set_position_relative(input_, b):
    """eventActorSetPositionRelative.js."""
    b.actor_set_position_relative_by_script_values(input_.get("actorId"), input_.get("x"),
                                                  input_.get("y"), input_.get("units", _UNDEFINED))


def _event_if_actor_direction(input_, b):
    """eventIfActorDirection.js."""
    b.if_actor_direction_script_value(input_.get("actorId"), input_.get("direction"),
                                     input_.get("true"), [] if input_.get("__disableElse") else input_.get("false"))


def _event_wait(input_, b):
    """src/lib/events/eventWait.js"""
    units = input_.get("units")
    if units == "frames":
        duration = input_.get("frames") if input_.get("frames") is not None else 1
    else:
        duration = input_.get("time") if input_.get("time") is not None else 0.5
    b.wait_script_value(duration, units if units is not None else "time")


def _event_set_value(input_, b):
    """src/lib/events/eventVariableSetToValue.js"""
    b.variable_set_to_script_value(input_.get("variable"), input_.get("value"))


def _event_variable_math_evaluate(input_, b):
    """eventVariableMathEvaluate.js, published 4.3.2."""
    expression = input_.get("expression")
    if expression is None or expression is False or expression == "" or expression == 0:
        expression = "0"
    b.variable_evaluate_expression(input_.get("variable"), expression)


def _event_fade_in(input_, b):
    """src/lib/events/eventFadeIn.js"""
    b.idle()
    b.fade_in(input_.get("speed", _UNDEFINED))


def _event_fade_out(input_, b):
    """src/lib/events/eventFadeOut.js"""
    b.fade_out(input_.get("speed", _UNDEFINED))


def _event_replace_tile_xy(input_, b):
    """src/lib/events/eventReplaceTileXY.js"""
    b.replace_tile_xy_script_value(input_.get("x"), input_.get("y"), input_.get("tilesetId"),
                                   input_.get("tileIndex"), input_.get("tileSize", _UNDEFINED))


def _event_replace_tile_xy_sequence(input_, b):
    """src/lib/events/eventReplaceTileXYSequence.js"""
    to_var = b._declare_local("to_var", 1, True)
    b._add_comment("Calculate max frame")
    tile_index, frames = input_.get("tileIndex"), input_.get("frames")
    if input_.get("tileSize") == "16px":
        b.variable_set_to_script_value(to_var, {"type": "add", "valueA": tile_index, "valueB": {
            "type": "mul", "valueA": {"type": "sub", "valueA": frames, "valueB": {"type": "number", "value": 1}},
            "valueB": {"type": "number", "value": 2}}})
    else:
        b.variable_set_to_script_value(to_var, {"type": "add", "valueA": tile_index, "valueB": {
            "type": "sub", "valueA": frames, "valueB": {"type": "number", "value": 1}}})
    b._add_nl()
    variable = input_.get("variable")
    b.if_variable_compare_script_value(variable, ".LT", tile_index,
                                       lambda: b.variable_set_to_script_value(variable, tile_index))
    b.replace_tile_xy_script_value(input_.get("x"), input_.get("y"), input_.get("tilesetId"),
                                   {"type": "variable", "value": variable}, input_.get("tileSize", _UNDEFINED))
    if input_.get("tileSize") == "16px":
        b.variable_add(variable, 2)
    else:
        b.variable_inc(variable)
    b.if_variable_compare(variable, ".GT", to_var, lambda: b.variable_set_to_script_value(variable, tile_index))
    b.mark_locals_used(to_var)


def _event_overlay_show(input_, b):
    """src/lib/events/eventOverlayShow.js"""
    b.overlay_show(input_.get("color", _UNDEFINED), input_.get("x", _UNDEFINED), input_.get("y", _UNDEFINED))


def _event_overlay_move_to(input_, b):
    """src/lib/events/eventOverlayMoveTo.js"""
    b.overlay_move_to(input_.get("x", _UNDEFINED), input_.get("y", _UNDEFINED), input_.get("speed", _UNDEFINED))


def _event_choice(input_, b):
    """src/lib/events/eventTextChoice.js"""
    b.text_choice(input_.get("variable"), {"trueText": input_.get("trueText"), "falseText": input_.get("falseText")})


def _event_camera_move_to(input_, b):
    from .custom_events import _js_number
    b.camera_move_to_script_values(input_.get("x"), input_.get("y"),
                                   _js_number(input_.get("speed", _UNDEFINED)), input_.get("units", "tiles"))


def _event_camera_lock(input_, b):
    from .custom_events import _js_number
    b.camera_lock(_js_number(input_.get("speed", _UNDEFINED)), input_.get("axis"), input_.get("preventScroll"))


def _event_camera_set_lock(input_, b):
    """src/lib/events/eventCameraSetLock.js: cameraLock(0, axis, preventScroll)."""
    b.camera_lock(0, input_.get("axis"), input_.get("preventScroll"))


def _event_camera_set_bounds(input_, b):
    """src/lib/events/eventCameraSetBounds.js"""
    b.camera_set_bounds_to_script_values(input_.get("x"), input_.get("y"), input_.get("width"), input_.get("height"),
                                         input_.get("units", _UNDEFINED))


def _event_camera_property_set(input_, b):
    """src/lib/events/eventCameraPropertySet.js"""
    b.camera_set_property_to_script_value(input_.get("property", _UNDEFINED), input_.get("value"))


def _event_menu(input_, b):
    """src/lib/events/eventMenu.js, one to eight authored options."""
    count = input_.get("items")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
        raise CompileError("EVENT_MENU: items must be an integer between 1 and 8")
    options = [input_.get(f"option{i}") for i in range(1, count + 1)]
    if any(option is not None and not isinstance(option, str) for option in options):
        raise CompileError("EVENT_MENU: option labels must be strings")
    # Missing layout passes through the released textMenu default (menu).
    b.text_menu(input_.get("variable"), options, input_.get("layout", "menu"),
                input_.get("cancelOnLastOption", False), input_.get("cancelOnB", False))


def _event_text(input_, b):
    """src/lib/events/eventTextDialogue.js. Avatars: docs/M4_AVATARS.md. The
    box at the top uses overlay_cut_scanline (docs/M6_SCANLINE_CUTOFF.md)."""
    from .builder import NotDelivered
    from .text import close_delay_frames, js_truthy, lex_text

    def nn(key, default):          # input.key ?? default
        value = input_.get(key)
        return default if value is None else value

    def speed(key):                # passed as is: undefined -> the default parameter
        if key in input_ and input_[key] is None:
            raise CompileError(f"{key} is null (GB Studio would write 'null' into the script)")
        return input_.get(key, _UNDEFINED)

    text = input_.get("text")
    text = text if js_truthy(text) else " "
    # closeWhen notModal and font changes (!F:id!, a font variable): delivered,
    # docs/M4_TEXT_MODES.md (the core's ui_update prints; ui_text.used_fonts).
    b.text_dialogue(text, input_.get("avatarId"), nn("minHeight", 4), nn("maxHeight", 7),
                    nn("position", "bottom"), nn("showFrame", True), nn("clearPrevious", True),
                    nn("textX", 1), nn("textY", 1), nn("textHeight", 5), speed("speedIn"), speed("speedOut"),
                    nn("closeWhen", "key"), nn("closeButton", "a"), close_delay_frames(input_))


def _event_switch_scene(input_, b):
    """src/lib/events/eventSceneSwitch.js"""
    b.scene_switch_using_script_values(input_.get("sceneId"), input_.get("x"), input_.get("y"),
                                       input_.get("direction", _UNDEFINED), input_.get("fadeSpeed", _UNDEFINED))


def _event_scene_push_state(input_, b):
    """src/lib/events/eventScenePushState.js"""
    b.scene_push_state()


def _event_scene_pop_state(input_, b):
    """src/lib/events/eventScenePopState.js"""
    b.scene_pop_state(input_.get("fadeSpeed", _UNDEFINED))
    b.script_end()


def _event_scene_pop_all_state(input_, b):
    """src/lib/events/eventScenePopAllState.js"""
    b.scene_pop_all_state(input_.get("fadeSpeed", _UNDEFINED))
    b.script_end()


def _event_scene_reset_state(input_, b):
    """src/lib/events/eventSceneResetState.js"""
    b.scene_reset_state()


def _event_set_input_script(input_, b):
    """src/lib/events/eventInputScriptSet.js (event.symbol: editableSymbol)."""
    event = b.event
    b.input_script_set(input_.get("input"), input_.get("override") is not False, input_.get("true"),
                       event["symbol"] if "symbol" in event else None)


def _event_remove_input_script(input_, b):
    """src/lib/events/eventInputScriptRemove.js"""
    b.input_script_remove(input_.get("input"))


def _event_call_custom_event(input_, b):
    """src/lib/events/eventCallCustomEvent.js"""
    b.call_script(input_.get("customEventId"), input_)


def _event_inc_value(input_, b):
    """src/lib/events/eventVariableInc.js"""
    b.variable_inc(input_.get("variable"))


def _event_dec_value(input_, b):
    """src/lib/events/eventVariableDec.js"""
    b.variable_dec(input_.get("variable"))


def _event_if(input_, b):
    """src/lib/events/eventIf.js"""
    true_path = input_.get("true")
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_script_value(input_.get("condition"), true_path, false_path)


# eventIfVariableValue.js / eventIfVariableCompare.js operationLookup; an
# unknown operator is undefined there (an assembler error later): refused.
_OPERATION_LOOKUP = {"==": ".EQ", "!=": ".NE", "<": ".LT", ">": ".GT", "<=": ".LTE", ">=": ".GTE"}


def _operation(input_):
    operator = input_.get("operator")
    if operator not in _OPERATION_LOOKUP:
        raise CompileError(f"operator {operator!r} (GB Studio would write 'undefined')")
    return _OPERATION_LOOKUP[operator]


def _paths(input_):
    return input_.get("true"), ([] if input_.get("__disableElse") else input_.get("false"))


def _event_if_variable_value(input_, b):
    """src/lib/events/eventIfVariableValue.js (deprecated, still compiled)"""
    true_path, false_path = _paths(input_)
    b.if_variable_value(input_.get("variable"), _operation(input_), input_.get("comparator") or 0,
                        true_path, false_path)


def _event_if_variable_compare(input_, b):
    """src/lib/events/eventIfVariableCompare.js (deprecated, still compiled)"""
    true_path, false_path = _paths(input_)
    b.if_variable_compare(input_.get("vectorX"), _operation(input_), input_.get("vectorY"), true_path, false_path)


def _event_if_variable_true(input_, b):
    """src/lib/events/eventIfVariableTrue.js (deprecated, still compiled)"""
    true_path, false_path = _paths(input_)
    b.if_variable_true(input_.get("variable"), true_path, false_path)


def _event_if_variable_false(input_, b):
    """src/lib/events/eventIfVariableFalse.js: ifVariableTrue with the paths swapped"""
    true_path, false_path = _paths(input_)
    b.if_variable_true(input_.get("variable"), false_path, true_path)


def _event_if_expression(input_, b):
    """src/lib/events/eventIfExpression.js (deprecated, still compiled)"""
    true_path, false_path = _paths(input_)
    b.if_expression(input_.get("expression") or "0", true_path, false_path)


def _event_text_set_animation_speed(input_, b):
    """src/lib/events/eventTextSetAnimationSpeed.js"""
    b.text_set_anim_speed(input_.get("speedIn", _UNDEFINED), input_.get("speedOut", _UNDEFINED),
                          input_.get("speed", _UNDEFINED), input_.get("allowFastForward", _UNDEFINED))


def _event_gbvm_script(input_, b):
    """src/lib/events/eventGBVMScript.js"""
    from .text import js_truthy
    if js_truthy(input_.get("script")):
        b.append_raw(input_["script"])
    if js_truthy(input_.get("references")):
        b.compile_referenced_assets(input_["references"])


def _event_thread_start(input_, b):
    """src/lib/events/eventThreadStart.js"""
    b.thread_start(input_.get("variable"), input_.get("true"))


def _event_thread_stop(input_, b):
    """src/lib/events/eventThreadStop.js"""
    b.thread_terminate(input_.get("variable"))


def _event_actor_set_collision_box(input_, b):
    """src/lib/events/eventActorSetCollisionBox.js"""
    b.actor_set_bound_to_script_values(input_.get("actorId"), input_.get("x"), input_.get("y"),
                                       input_.get("width"), input_.get("height"))


def _event_actor_effects(input_, b):
    """src/lib/events/eventActorEffects.js"""
    effect = input_.get("effect")
    units = input_.get("units", "pixels")
    if effect == "splitIn":
        b.actor_fx_split_in(input_.get("actorId"), input_.get("distance"), input_.get("speed"), units)
    elif effect == "splitOut":
        b.actor_fx_split_out(input_.get("actorId"), input_.get("distance"), input_.get("speed"), units)
    elif effect == "flicker":
        if input_.get("timeUnits") == "frames":
            frames = input_.get("frames") if type(input_.get("frames")) in (int, float) else 30
        else:
            seconds = input_.get("time") if type(input_.get("time")) in (int, float) else 0.5
            frames = math.ceil(seconds * 60)
        b.actor_fx_flicker(input_.get("actorId"), frames)


def _event_actor_invoke(input_, b):
    """src/lib/events/eventActorInvoke.js (deprecated, still compiled)"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_invoke()


def _event_actor_start_update(input_, b):
    """src/lib/events/eventActorStartUpdateScript.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_start_update()


def _event_if_current_scene_is(input_, b):
    """src/lib/events/eventIfCurrentSceneIs.js"""
    true_path, false_path = _paths(input_)
    b.if_current_scene_is(input_.get("sceneId"), true_path, false_path)


def _event_camera_set_position(input_, b):
    """src/lib/events/eventCameraSetPosition.js: cameraMoveToScriptValues(x, y, 0, units)."""
    b.camera_move_to_script_values(input_.get("x"), input_.get("y"), 0,
                                   input_["units"] if "units" in input_ else "tiles")


def _event_actor_variables(method):
    """eventActorSetPositionToVariables.js / eventActorMoveToVariables.js."""
    def compile_(input_, b):
        b.actor_set_active(input_.get("actorId"))
        getattr(b, method)(input_.get("vectorX"), input_.get("vectorY"), "tiles")
    return compile_


def _event_actor_set_frame_to_variable(input_, b):
    """src/lib/events/eventActorSetFrameToVariable.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_frame_to_variable(input_.get("variable"))


def _event_save_data(input_, b):
    """src/lib/events/eventDataSave.js"""
    b.data_save(input_.get("saveSlot", _UNDEFINED), input_.get("true"), input_.get("load"))


def _event_load_data(input_, b):
    """src/lib/events/eventDataLoad.js"""
    b.data_load(input_.get("saveSlot", _UNDEFINED))


def _event_clear_data(input_, b):
    """src/lib/events/eventDataClear.js"""
    b.data_clear(input_.get("saveSlot", _UNDEFINED))


def _event_peek_data(input_, b):
    """src/lib/events/eventDataPeek.js"""
    b.data_peek(input_.get("saveSlot", _UNDEFINED), input_.get("variableSource"), input_.get("variableDest"))


def _event_if_saved_data(input_, b):
    """src/lib/events/eventIfDataSaved.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_data_saved(input_.get("saveSlot", _UNDEFINED), input_.get("true"), false_path)


def _is_const_script_value(value):
    """shared/lib/scriptValue/types.ts isConstScriptValue."""
    if not isinstance(value, dict):
        return False
    if value.get("type") == "number" and type(value.get("value")) in (int, float):
        return True
    return value.get("type") == "constant" and isinstance(value.get("value"), str)


def _event_switch(input_, b):
    """src/lib/events/eventSwitch.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    choices = input_.get("choices")
    # Array(input.choices).fill(): a non-number would give a single case.
    if type(choices) is not int or choices < 0:
        raise CompileError(f"choices = {choices!r} is not a whole number of cases")
    cases = []
    for i in range(choices):
        value = input_.get(f"value{i}")
        value = value if _is_const_script_value(value) else {"type": "number", "value": i + 1}
        cases.append((value, input_.get(f"true{i}")))
    b.case_variable_const_value(input_.get("variable"), cases, false_path)


def _js_or_zero(value):
    """JavaScript `value || 0`: falsy values (0, "", null, undefined, NaN, false) give 0."""
    if value is None or value is False or value == "" or (type(value) in (int, float) and (value == 0 or value != value)):
        return 0
    return value


_VARIABLE_MATH_OPERATIONS = {"add": ".ADD", "sub": ".SUB", "mul": ".MUL", "div": ".DIV", "mod": ".MOD"}


def _event_variable_math(input_, b):
    """src/lib/events/eventVariableMath.js"""
    from .custom_events import _js_number
    value = _js_or_zero(input_.get("value"))
    min_ = _js_or_zero(input_.get("minValue"))
    # Math.min(32767, Math.max(0, (maxValue || 0) - min)) + 1, on JavaScript numbers.
    span = _js_number(_js_or_zero(input_.get("maxValue"))) - _js_number(min_)
    if span != span:
        raise CompileError(f"random range from {input_.get('minValue')!r} to {input_.get('maxValue')!r} is not a number "
                           "(GB Studio would write NaN)")
    range_ = min(32767, max(0, span)) + 1
    other = input_.get("other")
    if other == "true":
        value = 1
    elif other == "false":
        value = 0
    operation = input_.get("operation")
    if not operation or operation == "set":
        if other == "var":
            b.variable_copy(input_.get("vectorX"), input_.get("vectorY"))
        elif other == "rnd":
            b.variable_set_to_random(input_.get("vectorX"), min_, range_)
        else:
            b.variable_set_to_value(input_.get("vectorX"), value)
        return
    if operation not in _VARIABLE_MATH_OPERATIONS:
        raise CompileError(f"operation {operation!r} (GB Studio would write 'undefined')")
    op = _VARIABLE_MATH_OPERATIONS[operation]
    clamp = bool(input_.get("clamp"))
    if other == "var":
        b.variables_operation(input_.get("vectorX"), op, input_.get("vectorY"), clamp)
    elif other == "rnd":
        b.variable_random_operation(input_.get("vectorX"), op, min_, range_, clamp)
    elif value != 0 or type(value) is not int:     # `value !== 0`: the string "0" still operates
        b.variable_value_operation(input_.get("vectorX"), op, value, clamp)


def _event_seed_rng(input_, b):
    """src/lib/events/eventSeedRng.js"""
    b.seed_rng()


def _event_if_input(input_, b):
    """src/lib/events/eventIfInput.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_input(input_.get("input"), input_.get("true"), false_path)


def _event_if_color_supported(input_, b):
    """src/lib/events/eventIfColorSupported.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_device_cgb(input_.get("true"), false_path)


def _event_if_device_sgb(input_, b):
    """src/lib/events/eventIfDeviceSGB.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_device_sgb(input_.get("true"), false_path)


def _event_if_device_gba(input_, b):
    """src/lib/events/eventIfDeviceGBA.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_device_gba(input_.get("true"), false_path)


def _event_rate_limit(input_, b):
    """src/lib/events/eventRateLimit.js (`input.units ?? "time"`)."""
    units = input_.get("units")
    b.rate_limit_const_value(input_.get("frames") if units == "frames" else input_.get("time"),
                             "time" if units is None else units, input_.get("variable"), input_.get("true"))


def _event_set_timer_script(input_, b):
    """src/lib/events/eventTimerScriptSet.js (event.symbol: editableSymbol)."""
    if input_.get("units") == "frames":
        frames = input_.get("frames") if type(input_.get("frames")) in (int, float) else 30
    else:
        duration = input_.get("duration") if type(input_.get("duration")) in (int, float) else 10.0
        frames = math.ceil(duration * 60)
    timer = input_.get("timer")
    b.timer_script_set(frames, input_.get("script"), b.event["symbol"] if "symbol" in b.event else None,
                       1 if timer is None else timer)


def _event_timer_restart(input_, b):
    """src/lib/events/eventTimerRestart.js"""
    timer = input_.get("timer")
    b.timer_restart(1 if timer is None else timer)


def _event_timer_disable(input_, b):
    """src/lib/events/eventTimerDisable.js"""
    timer = input_.get("timer")
    b.timer_disable(1 if timer is None else timer)


def _event_actor_set_animate(input_, b):
    """src/lib/events/eventActorSetAnimate.js: actorSetActive, then
    deprecatedAPI actorSetAnimate, which emits nothing in GB Studio 4.3.2
    (console.error "actorSetAnimate not implemented")."""
    b.actor_set_active(input_.get("actorId"))
    b.warn("Actor Set Animate has no effect in GB Studio 4.3.2 (deprecated, not implemented): "
           "compiled as GB Studio does; use Actor Set Animation Speed or Set Animation State instead")


def _event_await_input(input_, b):
    """src/lib/events/eventInputAwait.js"""
    b.input_await(input_.get("input"))


def _event_if_actor_relative(input_, b):
    """src/lib/events/eventIfActorRelativeToActor.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.actor_set_active(input_.get("actorId"))
    b.if_actor_relative_to_actor(input_.get("operation"), input_.get("otherActorId"),
                                 input_.get("true"), false_path)


def _event_if_actor_at_position(input_, b):
    """src/lib/events/eventIfActorAtPosition.js"""
    false_path = [] if input_.get("__disableElse") else input_.get("false")
    b.if_actor_at_position_by_script_values(input_.get("actorId"), input_.get("x"), input_.get("y"),
                                             input_.get("true"), false_path, input_.get("units", _UNDEFINED))


def _event_camera_shake(input_, b):
    """eventCameraShake.js: numeric duration fields (not script values)."""
    import math
    duration = input_.get("frames") if input_.get("units") == "frames" else input_.get("time")
    numeric = type(duration) in (int, float)
    frames = (duration if numeric else 30) if input_.get("units") == "frames" else math.ceil((duration if numeric else 0.5) * 60)
    if frames > 0:
        direction = input_.get("shakeDirection")
        b.camera_shake_script_value(direction != "vertical", direction in ("vertical", "diagonal"),
                                    frames, input_.get("magnitude"))


def _event_loop(input_, b):
    """src/lib/events/eventLoop.js"""
    loop_id = b.get_next_label()
    b.label_define(loop_id)
    b.compile_events(input_.get("true"))
    b.label_goto(loop_id)


def _event_group(input_, b):
    """src/lib/events/eventGroup.js"""
    b.compile_events(input_.get("true"))


_COMPARISONS = {"==": ".EQ", "!=": ".NE", "<": ".LT", ">": ".GT", "<=": ".LTE", ">=": ".GTE"}


def _event_if_actor_distance(input_, b):
    """src/lib/events/eventIfActorDistanceFromActor.js."""
    comparison = input_.get("operator")
    if comparison not in _COMPARISONS:
        raise CompileError(f"distance comparison {comparison!r} is invalid")
    b.if_actor_distance_script_value_from_actor(input_.get("actorId"), input_.get("distance"),
        _COMPARISONS[comparison], input_.get("otherActorId"), input_.get("true"),
        [] if input_.get("__disableElse") else input_.get("false"))


def _event_loop_for(input_, b):
    """src/lib/events/eventLoopFor.js; the bound and step remain live ScriptValues."""
    operations = {"+=": ".ADD", "-=": ".SUB", "*=": ".MUL", "/=": ".DIV", "%=": ".MOD"}
    comparison, operation = input_.get("comparison"), input_.get("operation")
    if comparison not in _COMPARISONS:
        raise CompileError(f"loop comparison {comparison!r} is invalid")
    if operation not in operations:
        raise CompileError(f"loop operation {operation!r} is invalid")
    loop_id = b.get_next_label()
    variable = input_.get("variable")
    b.variable_set_to_script_value(variable, input_.get("from"))
    b.label_define(loop_id)
    def body():
        b.compile_events(input_.get("true"))
        b.variables_script_value_operation(variable, operations[operation], input_.get("value"))
        b.label_goto(loop_id)
    b.if_variable_compare_script_value(variable, _COMPARISONS[comparison], input_.get("to"), body)


def _event_actor_deactivate(input_, b):
    """src/lib/events/eventActorDeactivate.js"""
    b.actor_deactivate(input_.get("actorId"))


def _event_actor_activate(input_, b):
    """src/lib/events/eventActorActivate.js"""
    b.actor_activate(input_.get("actorId"))


def _event_actor_set_position(input_, b):
    """src/lib/events/eventActorSetPosition.js"""
    b.actor_set_position_to_script_values(input_.get("actorId"), input_.get("x"), input_.get("y"),
                                          input_.get("units", _UNDEFINED))


def _event_actor_set_direction(input_, b):
    """src/lib/events/eventActorSetDirection.js"""
    b.actor_set_direction_to_script_value(input_.get("actorId"), input_.get("direction"))


def _event_actor_set_frame(input_, b):
    """src/lib/events/eventActorSetFrame.js"""
    b.actor_set_frame_to_script_value(input_.get("actorId"), input_.get("frame"))


def _event_actor_set_movement_speed(input_, b):
    """src/lib/events/eventActorSetMovementSpeed.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_movement_speed(input_.get("speed", _UNDEFINED))


def _event_actor_set_animation_speed(input_, b):
    """src/lib/events/eventActorSetAnimationSpeed.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_animation_speed(input_.get("speed", _UNDEFINED))


def _event_actor_hide(input_, b):
    """src/lib/events/eventActorHide.js"""
    b.actor_hide(input_.get("actorId"))


def _event_actor_show(input_, b):
    """src/lib/events/eventActorShow.js"""
    b.actor_show(input_.get("actorId"))


def _event_actor_collisions_disable(input_, b):
    """src/lib/events/eventActorCollisionsDisable.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_collisions(False)


def _event_actor_collisions_enable(input_, b):
    """src/lib/events/eventActorCollisionsEnable.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_collisions(True)


def _event_launch_projectile(input_, b):
    """src/lib/events/eventLaunchProjectile.js, waitUntilAfterInitFade in model.
    Refused instead of compiled to nothing or to `undefined`: an unknown
    directionType, or the field the mode reads missing (no JS default)."""
    from ..projectiles import FIELDS
    kind, other, shortcut, field = _projectile_launch_kind(input_)
    b.actor_set_active(input_.get("actorId"))
    index = b.get_projectile_index({k: input_[k] for k in FIELDS if k in input_})
    if index < 0:
        return
    _launch_projectile(input_, b, index, kind, other, shortcut, field)


def _projectile_launch_kind(input_):
    """The direction mode of eventLaunchProjectile(Slot).js, checked as for Launch Projectile."""
    def field(key):
        if key not in input_:
            raise CompileError(f"{key} missing for directionType {kind!r} (GB Studio would compile undefined)")
        return input_[key]
    kind = input_.get("directionType")
    if kind not in ("direction", "angle", "anglevar", "actor", "target"):
        raise CompileError(f"directionType {kind!r} unknown (GB Studio would launch nothing)")
    other = "otherActorId" if kind == "actor" else "targetActorId"
    shortcut = kind in ("actor", "target") and input_.get("actorId") == input_.get(other)
    if not shortcut:
        field({"direction": "direction", "angle": "angle", "anglevar": "angleVariable"}.get(kind, other))
    return kind, other, shortcut, field


def _event_launch_projectile_slot(input_, b):
    """src/lib/events/eventLaunchProjectileSlot.js: the slot is the type index."""
    kind, other, shortcut, field = _projectile_launch_kind(input_)
    b.actor_set_active(input_.get("actorId"))
    index = input_.get("slot")
    if type(index) is not int or not 0 <= index < 5:
        if type(index) in (int, float) and index < 0:
            return                      # compile: projectileIndex < 0 launches nothing
        raise CompileError(f"projectile slot {index!r}: expected 0..4 (MAX_PROJECTILE_DEFS)")
    _launch_projectile(input_, b, index, kind, other, shortcut, field)


def _event_load_projectile_slot(input_, b):
    """src/lib/events/eventLoadProjectile.js (EVENT_LOAD_PROJECTILE_SLOT)."""
    from ..projectiles import FIELDS
    b.load_projectile(input_.get("slot"), {k: input_[k] for k in FIELDS if k in input_})


def _launch_projectile(input_, b, index, kind, other, shortcut, field):
    offsets = {k: input_[k] for k in ("x", "y") if k in input_}   # JS defaults x = 0, y = 0
    if kind == "direction":
        b.launch_projectile_in_direction(index, **offsets, direction=field("direction"))
    elif kind == "angle":
        b.launch_projectile_in_angle(index, **offsets, angle=field("angle"))
    elif kind == "anglevar":
        b.launch_projectile_in_angle_variable(index, **offsets, angle_variable=field("angleVariable"))
    else:
        if shortcut:
            b.launch_projectile_in_source_actor_direction(index, **offsets)
        elif kind == "actor":
            b.launch_projectile_in_actor_direction(index, **offsets, actor_id=field(other))
        else:
            b.launch_projectile_towards_actor(index, **offsets, other_actor_id=field(other))


def _event_actor_emote(input_, b):
    """src/lib/events/eventActorEmote.js, waitUntilAfterInitFade already in model."""
    b.actor_set_active(input_.get("actorId"))
    b.actor_emote(input_.get("emoteId"))


def _event_actor_set_sprite(input_, b):
    """src/lib/events/eventActorSetSprite.js."""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_sprite(input_.get("spriteSheetId"))


def _event_actor_set_state(input_, b):
    """src/lib/events/eventActorSetState.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_set_state(input_.get("spriteStateId"), input_.get("loopAnim", _UNDEFINED))


def _event_actor_stop_update(input_, b):
    """src/lib/events/eventActorStopUpdateScript.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_stop_update()


def _event_actor_push(input_, b):
    """eventActorPush.js: actor entity only; direction comes from the player."""
    entity = b.options["entity"]
    if b.options["entityType"] == "actor" and entity is not None and "id" in entity:
        b.actor_set_active(entity["id"])
        b.actor_push(input_.get("continue", _UNDEFINED))


def _event_actor_move_cancel(input_, b):
    """src/lib/events/eventActorMoveCancel.js"""
    b.actor_set_active(input_.get("actorId"))
    b.actor_move_cancel()


def _event_actor_move_to(input_, b):
    """src/lib/events/eventActorMoveTo.js"""
    b.actor_move_to_script_values(input_.get("actorId"), input_.get("x"), input_.get("y"),
                                  input_.get("collideWith"), input_.get("moveType", _UNDEFINED),
                                  input_.get("units", _UNDEFINED), input_.get("lockDirection", _UNDEFINED))


def _event_actor_move_relative(input_, b):
    """src/lib/events/eventActorMoveRelative.js"""
    b.actor_move_relative_by_script_values(input_.get("actorId"), input_.get("x"), input_.get("y"),
                                           input_.get("collideWith"), input_.get("moveType", _UNDEFINED),
                                           input_.get("units", _UNDEFINED), input_.get("lockDirection", _UNDEFINED))


# Actor events need the scene engine (M2): an export without it refuses them.
ACTOR_EVENTS = {
    "EVENT_ACTOR_GET_POSITION": _event_actor_get_position,
    "EVENT_ACTOR_SET_POSITION_RELATIVE": _event_actor_set_position_relative,
    "EVENT_IF_ACTOR_DIRECTION": _event_if_actor_direction,
    "EVENT_LAUNCH_PROJECTILE": _event_launch_projectile,
    "EVENT_ACTOR_EMOTE": _event_actor_emote,
    "EVENT_ACTOR_SET_SPRITE": _event_actor_set_sprite,
    "EVENT_ACTOR_SET_STATE": _event_actor_set_state,
    "EVENT_ACTOR_STOP_UPDATE": _event_actor_stop_update,
    "EVENT_ACTOR_PUSH": _event_actor_push,
    "EVENT_ACTOR_MOVE_CANCEL": _event_actor_move_cancel,
    "EVENT_ACTOR_MOVE_TO": _event_actor_move_to,
    "EVENT_ACTOR_MOVE_RELATIVE": _event_actor_move_relative,
    "EVENT_ACTOR_DEACTIVATE": _event_actor_deactivate,
    "EVENT_ACTOR_ACTIVATE": _event_actor_activate,
    "EVENT_ACTOR_SET_POSITION": _event_actor_set_position,
    "EVENT_ACTOR_SET_DIRECTION": _event_actor_set_direction,
    "EVENT_ACTOR_SET_FRAME": _event_actor_set_frame,
    "EVENT_ACTOR_SET_MOVEMENT_SPEED": _event_actor_set_movement_speed,
    "EVENT_ACTOR_SET_ANIMATION_SPEED": _event_actor_set_animation_speed,
    "EVENT_ACTOR_HIDE": _event_actor_hide,
    "EVENT_ACTOR_SHOW": _event_actor_show,
    "EVENT_ACTOR_COLLISIONS_DISABLE": _event_actor_collisions_disable,
    "EVENT_ACTOR_COLLISIONS_ENABLE": _event_actor_collisions_enable,
}

def _event_platformer_state_set(input_, b):
    """src/lib/events/eventPlatformerStateSet.js"""
    values = {"fall": "PLATFORM_FALL_STATE", "ground": "PLATFORM_GROUND_STATE", "jump": "PLATFORM_JUMP_STATE",
              "dash": "PLATFORM_DASH_STATE", "ladder": "PLATFORM_LADDER_STATE", "wall": "PLATFORM_WALL_STATE",
              "knockback": "PLATFORM_KNOCKBACK_STATE", "blank": "PLATFORM_BLANK_STATE",
              "run": "PLATFORM_RUN_STATE", "float": "PLATFORM_FLOAT_STATE"}
    b._add_comment("Set Platformer State")
    state = input_.get("state")
    b._set_const_mem_int8("plat_next_state", values[state] if state in values else "PLATFORM_FALL_STATE")


def _event_adventure_state_set(input_, b):
    """src/lib/events/eventAdventureStateSet.js"""
    values = {"ground": "ADVENTURE_GROUND_STATE", "dash": "ADVENTURE_DASH_STATE",
              "knockback": "ADVENTURE_KNOCKBACK_STATE", "blank": "ADVENTURE_BLANK_STATE",
              "run": "ADVENTURE_RUN_STATE", "push": "ADVENTURE_PUSH_STATE"}
    b._add_comment("Set ADVENTURE State")
    state = input_.get("state")
    b._set_const_mem_int8("adv_next_state", values[state] if state in values else "ADVENTURE_GROUND_STATE")


def _event_player_bounce(input_, b):
    """src/lib/events/eventPlatformerBounce.js"""
    b.player_bounce(input_.get("height"))


def _event_engine_field_set(input_, b):
    """src/lib/events/eventEngineFieldSet.js"""
    if not input_.get("value"):
        b.engine_field_set_to_default(input_.get("engineFieldKey"))
    else:
        b.engine_field_set_to_script_value(input_.get("engineFieldKey"), input_.get("value"))


def _event_engine_field_store(input_, b):
    """src/lib/events/eventEngineFieldStore.js"""
    b.engine_field_store_in_variable(input_.get("engineFieldKey"), input_.get("value"))


# Platformer events: write platform.c variables (engine memory, scene_platform.py).
PLATFORM_EVENTS = {
    "EVENT_PLATFORMER_STATE_SET": _event_platformer_state_set,
    "EVENT_ADVENTURE_STATE_SET": _event_adventure_state_set,
    "EVENT_PLAYER_BOUNCE": _event_player_bounce,
    "EVENT_ENGINE_FIELD_SET": _event_engine_field_set,
    "EVENT_ENGINE_FIELD_STORE": _event_engine_field_store,
}

def _flag_mask(input_):
    """Published handlers test JavaScript truthiness for each of 16 fields."""
    from .text import js_truthy
    return sum(1 << index for index in range(16) if js_truthy(input_.get(f"flag{index + 1}")))


def _event_if_flags_compare(input_, b):
    """eventIfVariableFlagsCompare.js; refuse invalid indices instead of mask zero."""
    flag=input_.get("flag")
    if type(flag) not in (int,float) or not 0 <= flag <= 15 or int(flag)!=flag:
        raise CompileError(f"flag index {flag!r} must be a number in 0..15")
    b.if_variable_bitwise_value(input_.get("variable"), ".B_AND", 1 << int(flag),
        input_.get("true"), [] if input_.get("__disableElse") else input_.get("false"))


# EVENT_NGPC_CLOCK_GET (src/lib/events/eventNgpcClockGet.js): which variables "parts" shows.
NGPC_CLOCK_PARTS = {"time": ("hour", "minute", "second"),
                    "date": ("year", "month", "day", "weekday"),
                    "datetime": ("year", "month", "day", "weekday", "hour", "minute", "second")}


def _event_ngpc_clock_get(input_, b):
    parts = input_.get("parts", "time")
    if parts not in NGPC_CLOCK_PARTS:
        raise CompileError(f"clock parts {parts!r}: expected {sorted(NGPC_CLOCK_PARTS)}")
    b.ngpc_clock_get({field: input_.get("variable" + field.capitalize())
                      for field in NGPC_CLOCK_PARTS[parts]})


def _event_ngpc_if_language(input_, b):
    true_path, false_path = _paths(input_)
    b.ngpc_if_language(input_.get("language", "english"), true_path, false_path)


HANDLERS = {
    # NGPC event of the NgpCraft Studio editor (src/lib/events/eventNgpcBackgroundVariant.js).
    "EVENT_NGPC_BACKGROUND_VARIANT": lambda input_, b: b.ngpc_background_variant(input_.get("variant")),
    # NGPC system events (src/lib/events/eventNgpc*.js, docs/M8_NGPC_SYSTEM.md).
    "EVENT_NGPC_CLOCK_GET": _event_ngpc_clock_get,
    "EVENT_NGPC_IF_LANGUAGE": _event_ngpc_if_language,
    "EVENT_NGPC_BATTERY_GET": lambda input_, b: b.ngpc_battery_get(input_.get("variable")),
    "EVENT_NGPC_SHUTDOWN": lambda input_, b: b.ngpc_shutdown(),
    "EVENT_SET_FLAGS": lambda input_, b: b.variable_set_to_value(input_.get("variable"), _flag_mask(input_)),
    "EVENT_ADD_FLAGS": lambda input_, b: b.variable_add_flags(input_.get("variable"), _flag_mask(input_)),
    "EVENT_CLEAR_FLAGS": lambda input_, b: b.variable_clear_flags(input_.get("variable"), _flag_mask(input_)),
    "EVENT_IF_FLAGS_COMPARE": _event_if_flags_compare,
    "EVENT_SCRIPT_LOCK": lambda input_, b: b.lock(),
    "EVENT_SCRIPT_UNLOCK": lambda input_, b: b.unlock(),
    "EVENT_IDLE": lambda input_, b: b.idle(),
    "EVENT_RESET_VARIABLES": lambda input_, b: b.variables_reset(),
    "EVENT_PLAYER_SET_SPRITE": lambda input_, b: b.player_set_sprite(input_.get("spriteSheetId")),
    "EVENT_IF_ACTOR_DISTANCE_FROM_ACTOR": _event_if_actor_distance,
    "EVENT_LOOP_WHILE": lambda input_, b: b.while_script_value(input_.get("condition"), input_.get("true")),
    "EVENT_LOOP_FOR": _event_loop_for,
    "EVENT_ACTOR_GET_DIRECTION": _event_actor_get_direction,
    "EVENT_SOUND_PLAY_EFFECT": compile_sfx_event,
    "EVENT_TEXT_SET_SOUND_EFFECT": compile_text_sound_event,   # docs/M5_TEXT_SOUND.md
    "EVENT_MUSIC_PLAY": _event_music_play,
    "EVENT_MUSIC_STOP": _event_music_stop,
    "EVENT_MUTE_CHANNEL": _event_mute_channel,           # docs/M5_MUTE_CHANNEL.md
    # eventLinkStart.js / eventLinkJoin.js / eventLinkClose.js / eventLinkTransfer.js (docs/M6_LINK.md)
    "EVENT_LINK_HOST": lambda input_, b: b.link_host(),
    "EVENT_LINK_JOIN": lambda input_, b: b.link_join(),
    "EVENT_LINK_CLOSE": lambda input_, b: b.link_close(),
    "EVENT_LINK_TRANSFER": _event_link_transfer,
    # eventOverlaySetScanlineCutoff.js (docs/M6_SCANLINE_CUTOFF.md)
    "EVENT_OVERLAY_SET_SCANLINE_CUTOFF": lambda input_, b: b.overlay_set_scanline_cutoff(
        input_.get("y"), input_["units"] if "units" in input_ else "pixels"),
    # eventMusicRoutine.js (event.symbol: editableSymbol); docs/M5_MUSIC_ROUTINE.md
    "EVENT_SET_MUSIC_ROUTINE": lambda input_, b: b.music_routine_set(
        input_.get("routine"), input_.get("true"), b.event["symbol"] if "symbol" in b.event else None),
    **ACTOR_EVENTS,
    **PLATFORM_EVENTS,
    "EVENT_WAIT": _event_wait,
    "EVENT_SET_VALUE": _event_set_value,
    "EVENT_VARIABLE_MATH_EVALUATE": _event_variable_math_evaluate,
    "EVENT_FADE_IN": _event_fade_in,
    "EVENT_FADE_OUT": _event_fade_out,
    "EVENT_INC_VALUE": _event_inc_value,
    "EVENT_DEC_VALUE": _event_dec_value,
    "EVENT_CAMERA_SHAKE": _event_camera_shake,
    "EVENT_IF": _event_if,
    "EVENT_SWITCH": _event_switch,
    "EVENT_VARIABLE_MATH": _event_variable_math,
    "EVENT_RNG_SEED": _event_seed_rng,
    "EVENT_IF_INPUT": _event_if_input,
    "EVENT_AWAIT_INPUT": _event_await_input,
    "EVENT_RATE_LIMIT": _event_rate_limit,
    "EVENT_ACTOR_SET_ANIMATE": _event_actor_set_animate,
    "EVENT_SET_TIMER_SCRIPT": _event_set_timer_script,
    "EVENT_GBVM_SCRIPT": _event_gbvm_script,
    "EVENT_THREAD_START": _event_thread_start,
    "EVENT_THREAD_STOP": _event_thread_stop,
    "EVENT_TIMER_RESTART": _event_timer_restart,
    "EVENT_TIMER_DISABLE": _event_timer_disable,
    "EVENT_IF_COLOR_SUPPORTED": _event_if_color_supported,
    "EVENT_IF_ACTOR_RELATIVE_TO_ACTOR": _event_if_actor_relative,
    "EVENT_IF_ACTOR_AT_POSITION": _event_if_actor_at_position,
    "EVENT_LOOP": _event_loop,
    "EVENT_GROUP": _event_group,
    "EVENT_SWITCH_SCENE": _event_switch_scene,
    "EVENT_TEXT": _event_text,
    "EVENT_CHOICE": _event_choice,
    "EVENT_MENU": _event_menu,
    "EVENT_CAMERA_MOVE_TO": _event_camera_move_to,
    "EVENT_CAMERA_LOCK": _event_camera_lock,
    "EVENT_CAMERA_SET_LOCK": _event_camera_set_lock,
    "EVENT_SET_FONT": lambda input_, b: b.set_font(input_.get("fontId")),        # eventSetFont.js
    # eventTextDraw.js
    "EVENT_TEXT_DRAW": lambda input_, b: b.text_draw(*(input_.get(k, _UNDEFINED) for k in ("text", "x", "y", "location"))),
    # eventSetDialogueFrame.js
    "EVENT_SET_DIALOGUE_FRAME": lambda input_, b: b.dialogue_frame_set_tiles(input_.get("tilesetId")),
    # eventDataTable.js
    "EVENT_DATA_TABLE": lambda input_, b: b.variable_data_table_lookup(input_.get("indexVariable"), input_.get("data")),
    "EVENT_DIALOGUE_CLOSE_NONMODAL": lambda input_, b: b.text_close_non_modal(input_.get("speed", _UNDEFINED)),
    "EVENT_CAMERA_PROPERTY_SET": _event_camera_property_set,
    "EVENT_CAMERA_SET_BOUNDS": _event_camera_set_bounds,
    "EVENT_OVERLAY_SHOW": _event_overlay_show,
    "EVENT_OVERLAY_MOVE_TO": _event_overlay_move_to,
    "EVENT_REPLACE_TILE_XY_SEQUENCE": _event_replace_tile_xy_sequence,
    "EVENT_REPLACE_TILE_XY": _event_replace_tile_xy,
    "EVENT_IF_DEVICE_SGB": _event_if_device_sgb,
    "EVENT_IF_DEVICE_GBA": _event_if_device_gba,
    # Deprecated in the editor, compiled by GB Studio 4.3.2 (deprecatedAPI.ts):
    # GB Studio 3/4 projects keep them (docs/M4_DEPRECATED_EVENTS.md).
    "EVENT_IF_VALUE": _event_if_variable_value,
    "EVENT_IF_VALUE_COMPARE": _event_if_variable_compare,
    "EVENT_IF_TRUE": _event_if_variable_true,
    "EVENT_IF_FALSE": _event_if_variable_false,
    "EVENT_SET_TRUE": lambda input_, b: b.variable_set_to_true(input_.get("variable")),
    "EVENT_SET_FALSE": lambda input_, b: b.variable_set_to_false(input_.get("variable")),
    "EVENT_IF_EXPRESSION": _event_if_expression,
    "EVENT_TEXT_SET_ANIMATION_SPEED": _event_text_set_animation_speed,
    "EVENT_ACTOR_START_UPDATE": _event_actor_start_update,
    "EVENT_ACTOR_INVOKE": _event_actor_invoke,
    "EVENT_ACTOR_EFFECTS": _event_actor_effects,
    "EVENT_ACTOR_SET_COLLISION_BOX": _event_actor_set_collision_box,
    "EVENT_LAUNCH_PROJECTILE_SLOT": _event_launch_projectile_slot,
    "EVENT_SCENE_UPDATE_PAUSE": lambda input_, b: b.scene_update_pause(),     # eventSceneUpdatePause.js
    "EVENT_SCENE_UPDATE_RESUME": lambda input_, b: b.scene_update_resume(),   # eventSceneUpdateResume.js
    "EVENT_LOAD_PROJECTILE_SLOT": _event_load_projectile_slot,
    "EVENT_OVERLAY_HIDE": lambda input_, b: b.overlay_hide(),          # eventOverlayHide.js
    "EVENT_SHOW_SPRITES": lambda input_, b: b.sprites_show(),          # eventSpritesShow.js
    "EVENT_HIDE_SPRITES": lambda input_, b: b.sprites_hide(),          # eventSpritesHide.js
    "EVENT_IF_CURRENT_SCENE_IS": _event_if_current_scene_is,
    "EVENT_CAMERA_SET_POSITION": _event_camera_set_position,
    "EVENT_GOTO_LABEL": lambda input_, b: b.label_goto(input_.get("label")),      # eventLabelGoto.js
    "EVENT_DEFINE_LABEL": lambda input_, b: b.label_define(input_.get("label")),  # eventLabelDefine.js
    "EVENT_ACTOR_SET_POSITION_TO_VALUE": _event_actor_variables("actor_set_position_to_variables"),
    "EVENT_ACTOR_MOVE_TO_VALUE": _event_actor_variables("actor_move_to_variables"),
    "EVENT_ACTOR_SET_FRAME_TO_VALUE": _event_actor_set_frame_to_variable,
    "EVENT_COMMENT": lambda input_, b: None,                  # eventComment.js: compile = () => {}
    "EVENT_STOP": lambda input_, b: b.script_end(),           # eventScriptStop.js
    # eventPaletteSetBackground.js / eventPaletteSetUI.js (docs/M4_PALETTES.md)
    "EVENT_PALETTE_SET_BACKGROUND": lambda input_, b: b.palette_set_background(
        [input_.get(f"palette{i}") for i in range(8)]),
    "EVENT_PALETTE_SET_UI": lambda input_, b: b.palette_set_ui(input_.get("palette")),
    "EVENT_PALETTE_SET_SPRITE": lambda input_, b: b.palette_set_sprite(
        [input_.get(f"palette{i}") for i in range(8)]),
    "EVENT_PALETTE_SET_EMOTE": lambda input_, b: b.palette_set_emote(input_.get("palette")),
    "EVENT_CALL_CUSTOM_EVENT": _event_call_custom_event,
    "EVENT_SET_PLATFORMER_CALLBACK_SCRIPT": lambda input_, b: b.platform_callback(input_.get("event"), input_.get("script")),
    "EVENT_REMOVE_PLATFORMER_CALLBACK_SCRIPT": lambda input_, b: b.platform_callback(input_.get("event"), remove=True),
    "EVENT_SET_ADVENTURE_CALLBACK_SCRIPT": lambda input_, b: b.platform_callback(input_.get("event"), input_.get("script"),
                                                                                 kind="adventure"),
    "EVENT_REMOVE_ADVENTURE_CALLBACK_SCRIPT": lambda input_, b: b.platform_callback(input_.get("event"), remove=True,
                                                                                    kind="adventure"),
    "EVENT_SET_INPUT_SCRIPT": _event_set_input_script,
    "EVENT_REMOVE_INPUT_SCRIPT": _event_remove_input_script,
    "EVENT_SCENE_PUSH_STATE": _event_scene_push_state,
    "EVENT_SCENE_POP_STATE": _event_scene_pop_state,
    "EVENT_SCENE_POP_ALL_STATE": _event_scene_pop_all_state,
    "EVENT_SCENE_RESET_STATE": _event_scene_reset_state,
    "EVENT_SAVE_DATA": _event_save_data,
    "EVENT_LOAD_DATA": _event_load_data,
    "EVENT_CLEAR_DATA": _event_clear_data,
    "EVENT_PEEK_DATA": _event_peek_data,
    "EVENT_IF_SAVED_DATA": _event_if_saved_data,
}


def _commented(event):
    value = (event.get("args") or {}).get("__comment")
    return value not in (None, False, 0, "") and value == value


def compile_events(b, events, sources, entities):
    """compileEventsWithScriptBuilder, for the delivered handlers.

    A handler compiles its branches through b.compile_events / _compile_path
    (options.compileEvents, as in GB Studio); a branch is located under its
    event: `<event>.children.<key>[<i>]`."""
    def compile_branch(builder, path):
        event, where = parents[-1]
        children = event.get("children") or {}
        # A plugin's branch comes back from the bridge as a copy: equal, not identical.
        keys = [k for k, v in children.items() if v is path] or [k for k, v in children.items() if v == path]
        here = f"{where}.children.{keys[0]}" if keys else f"{where}.children"
        path = path or []                  # compileEventsWithScriptBuilder(subInput = [])
        compile_events(builder, path, [f"{here}[{i}]" for i in range(len(path))], entities)

    if "entities" not in b.options:
        b.options["entities"] = entities     # custom scripts compile their own threads (custom_events.py)
    parents, previous = [], b.options.get("compileEvents")
    b.options["compileEvents"] = compile_branch
    try:
        _compile_list(b, events, sources, entities, parents)
    finally:
        b.options["compileEvents"] = previous


def _conditional(command, plugins):
    """handlerCommon.ts isConditional: a top-level field of type "events"."""
    if command in plugins:
        return bool(plugins[command].events_by_id[command].get("isConditional"))
    global _CONDITIONAL
    if _CONDITIONAL is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "gbstudio_event_fields.json"
        _CONDITIONAL = {k for k, v in json.loads(path.read_text(encoding="utf-8"))["events"].items()
                        if v["conditional"]}
    return command in _CONDITIONAL


_CONDITIONAL = None


def _compile_list(b, events, sources, entities, parents):
    for event, where in zip(events, sources):
        command = event["command"]
        if _commented(event):
            continue
        # loadScriptEventHandlers.ts: plugins load after the core events and replace them by id.
        plugins = b.options["pluginEvents"] if "pluginEvents" in b.options else {}
        if command in plugins or command in HANDLERS:
            # compileEntityEvents.ts: debug symbols around each compiled event.
            debug = b.options.get("debugEnabled")
            if debug:
                b.add_debug_symbol(b.options["scriptSymbolName"], (str(event["id"]) if "id" in event else "undefined"))
            parents.append((event, where))
            outer, b.source = b.source, where      # b.warn() locates its warnings here
            outer_event, b.event = b.event, event  # helpers.event (editable symbols)
            try:
                if command in plugins:             # docs/M7_PLUGINS.md
                    plugins[command].compile(command, event, b)
                else:
                    HANDLERS[command]({**(event.get("args") or {}), **(event.get("children") or {})}, b)
            except CompileError as exc:
                if str(exc).startswith(f"{where}."):
                    raise                  # already located in a branch
                raise CompileError(f"{where}: {command}: {exc}") from None
            finally:
                parents.pop()
                b.source = outer
                b.event = outer_event
            if debug and _conditional(command, plugins):
                b.add_debug_end_symbol(b.options["scriptSymbolName"], (str(event["id"]) if "id" in event else "undefined"))
        elif command == "INTERNAL_SET_CONTEXT":
            args = event["args"]
            b.options["entity"] = entities[args["entityId"]]
            b.options["entityType"] = args["entityType"]
            b.options["entityScriptKey"] = str(args["scriptKey"])
        elif command == "INTERNAL_SET_SPRITE_MODE":
            b.set_sprite_mode(event["args"]["mode"])
        elif command != "EVENT_END":
            # GB Studio only warns ("No compiler for command") and emits nothing.
            raise CompileError(f"{where}: {command} is not delivered yet")


def _script_symbol(name, shared):
    # compileData.ts uses one symbol registry for all scene/entity scripts.
    # Migration can produce identical actor symbols in different scenes.
    return ScriptBuilder([], shared)._get_available_symbol(name)


def compile_scene_init(init, scene, scene_index, variables_lookup, variable_alias_lookup, entities, shared=None):
    """bankSceneEvents -> compileScript(initScript, "scene", scene, sceneIndex,
    loop=false, lock=true, "script") -> compileEntityEvents.

    Returns (symbol, assembly text). entityIndex is the scene index for the
    whole thread, actors included: INTERNAL_SET_CONTEXT does not change it
    (compileEntityEvents.ts), so an actor's local is named S<i>A<i>_...
    exactly as in GB Studio.
    """
    name = _script_symbol(f"{scene['symbol']}_init", shared)
    return name, compile_entity_events(name, init.events, init.sources, {
        "scene": scene, "sceneIndex": scene_index, "entityType": "scene", "entityIndex": scene_index,
        "entityScriptKey": "script", "entity": entities[scene["id"]], "scriptSymbolName": name,
        "variablesLookup": variables_lookup, "variableAliasLookup": variable_alias_lookup, **(shared or {}),
    }, entities, lock=True)


def compile_entity_events(name, events, sources, options, entities, lock=False, loop=False, is_function=False):
    """compileEntityEvents.ts, top-level script with optional automatic loop;
    is_function: a custom script, ended by VM_RET_FAR(_N) after releasing its locals."""
    b = ScriptBuilder([], {**options, "entities": entities})
    loop_id = b.get_next_label() if loop else None
    if loop and events:
        b._label(loop_id)
    compile_events(b, events, sources, entities)
    b._pack_locals()
    if loop and events and len(b.output) > 1:
        b.idle()
        b._jump(loop_id)
    if is_function:
        b.unreserve_locals()
        b.return_far()
    else:
        b.script_end()
    return b.to_script_string(name, lock)


def compile_actor_interaction(update, scene, scene_index, variables_lookup, aliases, entities, shared=None):
    name = _script_symbol(f"{update.actor['symbol']}_interact", shared)
    return name, compile_entity_events(name, update.events, update.sources, {
        "scene": scene, "sceneIndex": scene_index, "entityType": "actor",
        "entityIndex": update.actor_index, "entityScriptKey": "script", "entity": update.actor,
        "scriptSymbolName": name, "variablesLookup": variables_lookup, "variableAliasLookup": aliases,
        **(shared or {}),
    }, entities, lock=True)


def compile_actor_update(update, scene, scene_index, variables_lookup, aliases, entities, shared=None):
    name = _script_symbol(f"{update.actor['symbol']}_update", shared)
    return name, compile_entity_events(name, update.events, update.sources, {
        "scene": scene, "sceneIndex": scene_index, "entityType": "actor",
        "entityIndex": update.actor_index, "entityScriptKey": "updateScript", "entity": update.actor,
        "scriptSymbolName": name, "variablesLookup": variables_lookup, "variableAliasLookup": aliases,
        **(shared or {}),
    }, entities, loop=True)


def _compile_parameter_paths(b, entity, paths, path, entities, collapse):
    """compileData.combineScripts + ScriptBuilder.ifParamValue, shared by hits/triggers."""
    for value, key in paths:
        events = entity[key]
        sources = [f"{path}:{key}[{i}]" for i in range(len(events))]
        if collapse and len(paths) == 1:
            compile_events(b, events, sources, entities)
        else:
            local = b._declare_local("param0_value", 1, True)
            yes, end = b.get_next_label(), b.get_next_label()
            b._add_comment(f"If Parameter 0 Equals {value}")
            b._add_cmd("VM_GET_TLOCAL", local, 0)
            b._if_const(".EQ", local, value, yes, 0)
            b._jump(end)
            b._label(yes)
            compile_events(b, events, sources, entities)
            b._stop()
            b._label(end)
            b._add_nl()


def compile_trigger(trigger, scene, scene_index, variables_lookup, aliases, entities, shared=None):
    entity = trigger.trigger
    name = _script_symbol(f"{entity['symbol']}_interact", shared)
    b = ScriptBuilder([], {"scene": scene, "sceneIndex": scene_index, "entityType": "trigger",
        "entityIndex": trigger.index, "entityScriptKey": "script", "entity": entity,
        "scriptSymbolName": name, "variablesLookup": variables_lookup, "variableAliasLookup": aliases,
        **(shared or {})})
    paths = [(value, key) for value, key in ((1, "script"), (2, "leaveScript")) if entity[key]]
    _compile_parameter_paths(b, entity, paths, trigger.path, entities, True)
    b._pack_locals()
    b.script_end()
    return name, b.to_script_string(name, True)


def compile_collision(hit, scene, scene_index, variables_lookup, aliases, entities, shared=None):
    name = _script_symbol(f"{hit.entity['symbol']}_{'p_hit1' if hit.entity_type == 'scene' else 'interact'}", shared)
    b = ScriptBuilder([], {"scene": scene, "sceneIndex": scene_index, "entityType": hit.entity_type,
        "entityIndex": scene_index if hit.entity_type == 'scene' else hit.index - 1,
        "entityScriptKey": "playerHit1Script" if hit.entity_type == 'scene' else "script", "entity": hit.entity,
        "scriptSymbolName": name, "variablesLookup": variables_lookup, "variableAliasLookup": aliases,
        **(shared or {})})
    _compile_parameter_paths(b, hit.entity, hit.branches, hit.path, entities, False)
    b._pack_locals()
    b.script_end()
    return name, b.to_script_string(name, False)
