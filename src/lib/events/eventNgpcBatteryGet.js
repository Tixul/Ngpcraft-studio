/*
 * NgpCraft (NGPC only): store the main battery voltage the console measures
 * (Battery_Voltage, 0x6F80: 0..1023, SNK SDK SysWork.txt) in a variable.
 * Compiled by the NgpCraft V3 (docs/M8_NGPC_SYSTEM.md).
 */
const l10n = require("../helpers/l10n").default;

const id = "EVENT_NGPC_BATTERY_GET";
const groups = ["EVENT_GROUP_NGPC", "EVENT_GROUP_VARIABLES"];

const fields = [
  {
    key: "variable",
    label: l10n("FIELD_VARIABLE"),
    description: l10n("FIELD_NGPC_BATTERY_VARIABLE_DESC"),
    type: "variable",
    defaultValue: "LAST_VARIABLE",
  },
];

// GB Studio's own builder has no battery reading: the V3 compiles this event.
const compile = (_input, helpers) => {
  const { _addComment } = helpers;
  _addComment("NGPC Store Battery Voltage In Variable (NgpCraft V3 only)");
};

module.exports = {
  id,
  description: l10n("EVENT_NGPC_BATTERY_GET_DESC"),
  groups,
  fields,
  compile,
};
