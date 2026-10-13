/*
 * NgpCraft (NGPC only): branch on the language chosen in the console's own
 * menu (English or Japanese), for a bilingual game without a language menu.
 * Compiled by the NgpCraft V3 (Language, 0x6F87: 0 = Japanese, 1 = English,
 * SNK SDK SysWork.txt; docs/M8_NGPC_SYSTEM.md).
 */
const l10n = require("../helpers/l10n").default;

const id = "EVENT_NGPC_IF_LANGUAGE";
const groups = ["EVENT_GROUP_NGPC", "EVENT_GROUP_CONTROL_FLOW"];

const fields = [
  {
    key: "language",
    label: l10n("FIELD_NGPC_LANGUAGE"),
    type: "select",
    options: [
      ["english", l10n("FIELD_NGPC_LANGUAGE_ENGLISH")],
      ["japanese", l10n("FIELD_NGPC_LANGUAGE_JAPANESE")],
    ],
    defaultValue: "english",
  },
  {
    key: "true",
    label: l10n("FIELD_TRUE"),
    description: l10n("FIELD_TRUE_DESC"),
    type: "events",
  },
  {
    key: "__collapseElse",
    label: l10n("FIELD_ELSE"),
    type: "collapsable",
    defaultValue: true,
    conditions: [
      {
        key: "__disableElse",
        ne: true,
      },
    ],
  },
  {
    key: "false",
    label: l10n("FIELD_FALSE"),
    description: l10n("FIELD_FALSE_DESC"),
    conditions: [
      {
        key: "__collapseElse",
        ne: true,
      },
      {
        key: "__disableElse",
        ne: true,
      },
    ],
    type: "events",
  },
];

// GB Studio's own builder has no console language: the V3 compiles this event.
const compile = (input, helpers) => {
  const { _addComment } = helpers;
  _addComment(`NGPC If Console Language ${input.language} (NgpCraft V3 only)`);
};

module.exports = {
  id,
  description: l10n("EVENT_NGPC_IF_LANGUAGE_DESC"),
  groups,
  fields,
  compile,
};
