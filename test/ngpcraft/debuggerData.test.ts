/* eslint-disable camelcase */ // Keys of V3 / GB Studio file formats.
/*
 * NgpCraft F9: debugger data built from a V3 build. The panel reads values
 * by position, so the symbols must come in V3 index order.
 */
import { ngpcDebuggerData } from "lib/ngpcraft/debuggerData";
import type { V3BuildResult } from "lib/ngpcraft/v3Build";
import type { ProjectResources } from "shared/lib/resources/types";

const project = {
  variables: {
    variables: [
      { id: "id-b", name: "Beta", symbol: "var_beta" },
      { id: "id-a", name: "Alpha", symbol: "var_alpha" },
    ],
    constants: [],
  },
  settings: {
    debuggerPauseOnWatchedVariableChanged: true,
    debuggerWatchedVariables: ["id-a"],
  },
} as unknown as ProjectResources;

const result = {
  variables: { VAR_BETA: 1, VAR_TEMP_0: 2, VAR_ALPHA: 0 },
  scriptMemoryAddress: 0x42d8,
  sceneIndexAddress: 0x556e,
  scenes: [
    { id: "scene-a", name: "Outside" },
    { id: "scene-b", name: "Cave" },
  ],
} as unknown as V3BuildResult;

test("variables are listed in V3 index order with their project names", () => {
  const { initData, symbols } = ngpcDebuggerData(result, project);
  expect(Object.keys(symbols.variableMap)).toEqual([
    "VAR_ALPHA",
    "VAR_BETA",
    "VAR_TEMP_0",
  ]);
  expect(symbols.variableMap.VAR_ALPHA.name).toBe("Alpha");
  // A V3 variable unknown to the project keeps its symbol as name.
  expect(symbols.variableMap.VAR_TEMP_0.name).toBe("VAR_TEMP_0");
  expect(initData.memoryMap).toEqual({
    _script_memory: 0x42d8,
    _ngc_scene: 0x556e,
  });
  // ngc_scene holds the V3 scene index: symbols in that order.
  expect(initData.ngpcSceneSymbols).toEqual(["scene-a", "scene-b"]);
  expect(symbols.sceneMap["scene-b"].name).toBe("Cave");
  expect(initData.globalVariables.MAX_GLOBAL_VARS).toBe(3);
  expect(initData.variableMap["id-a"].symbol).toBe("VAR_ALPHA");
  expect(initData.watchedVariables).toEqual(["id-a"]);
});

test("no _script_memory symbol: no memory map, nothing is read", () => {
  const { initData } = ngpcDebuggerData(
    { ...result, scriptMemoryAddress: undefined, sceneIndexAddress: undefined },
    project,
  );
  expect(initData.memoryMap).toEqual({});
});
