import type { VariableMapData } from "lib/compiler/compileData";
import type { EntityType } from "shared/lib/scripts/context";

export type DebuggerScriptContext = {
  address: number;
  bank: number;
  current: boolean;
  closestSymbol: string;
  closestGBVMSymbol?: {
    scriptSymbol: string;
    scriptEventId: string;
    sceneId: string;
    entityType: EntityType;
    entityId: string;
    scriptKey: string;
  };
  stackString: string;
};

export type DebuggerDataPacket =
  | {
      action: "initialized";
    }
  | {
      action: "update-globals";
      data: number[];
      vram: string;
      background: string;
      isPaused: boolean;
      scriptContexts: DebuggerScriptContext[];
      currentSceneSymbol: string;
      currentScriptSymbol: string;
      currentScriptAddr: number;
      currentScriptPCAddr: number;
    };

// The V3 debug build, as the Play window debugger (studio.js) reads it:
// export.json `debug` (labels, step context) and build-report.json `debug`
// (layout: SCRIPT_CTX field offsets cc900 linked, console addresses).
export interface NgpcDebugInfo {
  labels: { symbol: string; address: number }[];
  stepContext: { register: string; offset: number; bytes: number };
  layout: {
    sizeof: number;
    fields: Record<string, number>;
    bank_0_address: number;
    // Console address of each script bank (ngc_banks); labels are
    // bank * 0x10000 + offset. Absent from builds before several banks.
    bank_addresses?: number[];
    first_ctx: number;
    executing_ctx: number;
    CTXS: number;
    VM_STEP: number;
  };
}

export type DebuggerInitData = {
  memoryMap: Record<string, number>;
  globalVariables: Record<string, number>;
  pauseOnScriptChanged: boolean;
  pauseOnWatchedVariableChanged: boolean;
  breakpoints: string[];
  watchedVariables: string[];
  variableMap: Record<string, VariableMapData>;
  // NgpCraft: scene symbol of each V3 scene index (current scene display).
  ngpcSceneSymbols?: string[];
  // NgpCraft: V3 debug build (event symbols, contexts), lib/ngpcraft/v3Build.ts.
  ngpcDebug?: NgpcDebugInfo;
};

export enum BackgroundPreviewType {
  BACKGROUND,
  OVERLAY,
}
