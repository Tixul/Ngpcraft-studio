/*
 * NgpCraft: debugger data for a V3 build. The Play window (studio.js) reads
 * the GBVM global variables at _script_memory + 2 * index; the debugger
 * panel lists them by symbol, in index order (it reads values by position).
 */
import keyBy from "lodash/keyBy";
import type { SceneMapData, VariableMapData } from "lib/compiler/compileData";
import type { DebuggerInitData } from "shared/lib/debugger/types";
import type { ProjectResources } from "shared/lib/resources/types";
import type { V3BuildResult } from "lib/ngpcraft/v3Build";

const SCRIPT_MEMORY_SYMBOL = "_script_memory";
const CURRENT_SCENE_SYMBOL = "_ngc_scene";
const MAX_GLOBAL_VARS = "MAX_GLOBAL_VARS";

export const ngpcDebuggerData = (
  result: V3BuildResult,
  project: ProjectResources,
): {
  initData: DebuggerInitData;
  symbols: {
    variableMap: Record<string, VariableMapData>;
    sceneMap: Record<string, SceneMapData>;
    gbvmScripts: Record<string, string>;
  };
} => {
  const projectVariables = keyBy(
    project.variables.variables.filter((v) => v.symbol),
    (v) => v.symbol.toUpperCase(),
  );
  const ordered = Object.entries(result.variables).sort((a, b) => a[1] - b[1]);
  const bySymbol: Record<string, VariableMapData> = {};
  const byId: Record<string, VariableMapData> = {};
  for (const [symbol] of ordered) {
    const variable = projectVariables[symbol];
    const data: VariableMapData = {
      id: variable?.id ?? symbol,
      name: variable?.name || symbol,
      symbol,
      isLocal: false,
      entityType: "scene",
      entityId: "",
      sceneId: "",
    };
    bySymbol[symbol] = data;
    byId[data.id] = data;
  }
  const maxIndex = ordered.length ? ordered[ordered.length - 1][1] : -1;
  // The scene id is its symbol: unique, and the panel only uses the map.
  const sceneMap: Record<string, SceneMapData> = {};
  for (const scene of result.scenes) {
    sceneMap[scene.id] = { id: scene.id, name: scene.name, symbol: scene.id };
  }
  const memoryMap: Record<string, number> = {};
  if (result.scriptMemoryAddress !== undefined) {
    memoryMap[SCRIPT_MEMORY_SYMBOL] = result.scriptMemoryAddress;
  }
  if (result.sceneIndexAddress !== undefined) {
    memoryMap[CURRENT_SCENE_SYMBOL] = result.sceneIndexAddress;
  }
  const settings = project.settings;
  return {
    initData: {
      memoryMap,
      ngpcSceneSymbols: result.scenes.map((scene) => scene.id),
      ...(result.debug && { ngpcDebug: result.debug }),
      globalVariables: { ...result.variables, [MAX_GLOBAL_VARS]: maxIndex + 1 },
      variableMap: byId,
      pauseOnScriptChanged: false,
      pauseOnWatchedVariableChanged:
        settings.debuggerPauseOnWatchedVariableChanged,
      breakpoints: [],
      watchedVariables: settings.debuggerWatchedVariables,
    },
    symbols: {
      variableMap: bySymbol,
      sceneMap,
      gbvmScripts: result.gbvmScripts,
    },
  };
};
