/*
 * NgpCraft (NGPC only): switch the console off, through the system call the
 * SNK SDK prescribes (VECT_SHUTDOWN, SysCall.txt). For a "Quit" menu entry.
 * Compiled by the NgpCraft V3 (docs/M8_NGPC_SYSTEM.md).
 */
const l10n = require("../helpers/l10n").default;

const id = "EVENT_NGPC_SHUTDOWN";
const groups = ["EVENT_GROUP_NGPC", "EVENT_GROUP_MISC"];

const fields = [
  {
    label: l10n("EVENT_NGPC_SHUTDOWN_DESC"),
  },
];

// GB Studio's own builder has no power off: the V3 compiles this event.
const compile = (_input, helpers) => {
  const { _addComment } = helpers;
  _addComment("NGPC Switch Off (NgpCraft V3 only)");
};

module.exports = {
  id,
  description: l10n("EVENT_NGPC_SHUTDOWN_DESC"),
  groups,
  fields,
  compile,
};
