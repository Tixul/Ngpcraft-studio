/*
 * NgpCraft (NGPC only): switch the scene background to one of its colour
 * variants, recoloured copies of its PNG listed in the "NGPC native" panel of
 * the background (ngpcraft.json ngpc_color_variants). 0 = as drawn. Compiled
 * by the NgpCraft V3 (ngpcraft-v3/ngpcraft/native_variants.py); a number
 * without a variant leaves the colours unchanged.
 */
const l10n = require("../helpers/l10n").default;

const id = "EVENT_NGPC_BACKGROUND_VARIANT";
const groups = ["EVENT_GROUP_NGPC", "EVENT_GROUP_COLOR"];

const fields = [
  {
    key: "variant",
    label: l10n("FIELD_NGPC_BACKGROUND_VARIANT"),
    description: l10n("FIELD_NGPC_BACKGROUND_VARIANT_DESC"),
    type: "number",
    min: 0,
    max: 15,
    defaultValue: 1,
  },
];

const compile = (input, helpers) => {
  const { _addComment, _stackPushConst, _callNative, _stackPop } = helpers;
  _addComment(`NGPC background colour variant ${input.variant}`);
  _stackPushConst(input.variant);
  _callNative("ngc_bg_variant");
  _stackPop(1);
};

module.exports = {
  id,
  description: l10n("EVENT_NGPC_BACKGROUND_VARIANT_DESC"),
  groups,
  fields,
  compile,
};
