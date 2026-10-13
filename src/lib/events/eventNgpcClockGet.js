/*
 * NgpCraft (NGPC only): store the console clock (the calendar the player sets
 * in the NGPC's own menu) in variables. One read for the whole event, so its
 * values come from one instant. Compiled by the NgpCraft V3
 * (ngpcraft-v3/ngpcraft/gbvm/ngpc_system.py, docs/M8_NGPC_SYSTEM.md).
 */
const l10n = require("../helpers/l10n").default;

const id = "EVENT_NGPC_CLOCK_GET";
const groups = ["EVENT_GROUP_NGPC", "EVENT_GROUP_VARIABLES"];

const TIME = ["time", "datetime"];
const DATE = ["date", "datetime"];

const variable = (key, label, parts) => ({
  key,
  label: l10n(label),
  type: "variable",
  defaultValue: "LAST_VARIABLE",
  width: "50%",
  conditions: [{ key: "parts", in: parts }],
});

const fields = [
  {
    key: "parts",
    label: l10n("FIELD_NGPC_CLOCK_PARTS"),
    type: "select",
    options: [
      ["time", l10n("FIELD_NGPC_CLOCK_TIME")],
      ["date", l10n("FIELD_NGPC_CLOCK_DATE")],
      ["datetime", l10n("FIELD_NGPC_CLOCK_DATETIME")],
    ],
    defaultValue: "time",
  },
  {
    type: "group",
    fields: [
      variable("variableYear", "FIELD_NGPC_CLOCK_YEAR", DATE),
      variable("variableMonth", "FIELD_NGPC_CLOCK_MONTH", DATE),
    ],
  },
  {
    type: "group",
    fields: [
      variable("variableDay", "FIELD_NGPC_CLOCK_DAY", DATE),
      variable("variableWeekday", "FIELD_NGPC_CLOCK_WEEKDAY", DATE),
    ],
  },
  {
    type: "group",
    fields: [
      variable("variableHour", "FIELD_NGPC_CLOCK_HOUR", TIME),
      variable("variableMinute", "FIELD_NGPC_CLOCK_MINUTE", TIME),
    ],
  },
  {
    type: "group",
    fields: [variable("variableSecond", "FIELD_NGPC_CLOCK_SECOND", TIME)],
  },
];

// GB Studio's own builder has no NGPC clock: the V3 compiles this event.
const compile = (_input, helpers) => {
  const { _addComment } = helpers;
  _addComment("NGPC Store Clock In Variables (NgpCraft V3 only)");
};

module.exports = {
  id,
  description: l10n("EVENT_NGPC_CLOCK_GET_DESC"),
  groups,
  fields,
  compile,
};
