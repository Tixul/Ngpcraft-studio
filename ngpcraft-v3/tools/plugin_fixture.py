"""A GB Studio event plugin written for the tests (docs/M7_PLUGINS.md).

It goes through every path of the bridge: a deprecated helper (variablesAdd,
GB Studio's own body calling V3's variablesOperation), GB Studio's _rpn chain,
a temporary local, an event branch of the project compiled by V3 (truePath),
and JavaScript callbacks given as branches (falsePath, ifVariableTrue).

With A, B, amount: A = A + B + amount + 7; then A >= 10 runs the project's
`true` events, else C = 99; then E = E + 1 while A is not 0.
An amount above 1000 throws (a plugin's own error).
"""
from pathlib import Path

EVENT_ID = "NGC_EVENT_PLUGIN_SUM"

SOURCE = r'''const id = "NGC_EVENT_PLUGIN_SUM";
const groups = ["NgpCraft tests"];
const name = "Plugin sum";

const fields = [
  { key: "a", type: "variable", defaultValue: "LAST_VARIABLE" },
  { key: "b", type: "variable", defaultValue: "LAST_VARIABLE" },
  { key: "c", type: "variable", defaultValue: "LAST_VARIABLE" },
  { key: "e", type: "variable", defaultValue: "LAST_VARIABLE" },
  { key: "amount", type: "number", defaultValue: 3 },
  { key: "true", type: "events" },
];

const compile = (input, helpers) => {
  const { _addComment, variablesAdd, _rpn, _declareLocal, _setConst, ifVariableValue,
          ifVariableTrue, variableSetToValue, variableInc } = helpers;
  if (input.amount > 1000) {
    throw new Error("amount above 1000");
  }
  _addComment("NGC plugin sum");
  variablesAdd(input.a, input.b, false);
  _rpn().refVariable(input.a).int16(input.amount).operator(".ADD").refSetVariable(input.a).stop();
  const tmp = _declareLocal("plugin_tmp", 1, true);
  _setConst(tmp, 7);
  _rpn().refVariable(input.a).ref(tmp).operator(".ADD").refSetVariable(input.a).stop();
  ifVariableValue(input.a, ".GTE", 10, input.true, () => {
    variableSetToValue(input.c, 99);
  });
  ifVariableTrue(input.a, () => {
    variableInc(input.e);
  });
};

module.exports = { id, name, groups, fields, compile };
'''


def write_plugin(project, folder="NgcSum"):
    """The plugin in `<project>/plugins/<folder>/events/` (GB Studio's glob)."""
    path = Path(project) / "plugins" / folder / "events" / "eventNgcSum.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE, encoding="utf-8")
    return path


def event(a, b, c, e, amount, true_events, event_id="plugin-sum"):
    return {"id": event_id, "command": EVENT_ID,
            "args": {"a": str(a), "b": str(b), "c": str(c), "e": str(e), "amount": amount},
            "children": {"true": true_events}}
