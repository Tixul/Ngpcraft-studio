/*
 * NgpCraft: ngpcraft.json in the editor. Loaded with the project, written by
 * the project save, used by the build. Actions are "ngpcraft/..." so the
 * document is marked modified (documentState).
 *
 * Also holds view data that is NOT part of the project ("ngpcraftView/..."
 * actions, which do not mark the document modified): whether the V3 offers
 * "NGPC native" colours, the palette plans of backgrounds, and the
 * per-scene budget of the last build.
 */
import {
  createAction,
  createAsyncThunk,
  createSlice,
  PayloadAction,
} from "@reduxjs/toolkit";
import API from "renderer/lib/api";
import projectActions from "store/features/project/projectActions";
import { dropOrphanResources } from "./ngpcraftViewActions";
import {
  NgpcProjectExtension,
  NgpcProjectSettingKey,
  NgpcProjectSettings,
  setNgpcProjectSetting,
  setNgpcSoundEffects,
  NgpcNativeSfxStep,
  setNgpcResourceSetting,
} from "shared/lib/ngpcraft/projectExtension";
import type { NgpcPalettePlan } from "shared/lib/ngpcraft/palettePlan";
import type { NgpcSceneBudget } from "shared/lib/ngpcraft/sceneBudget";

export interface NgpcPalettePlanState {
  loading: boolean;
  plan?: NgpcPalettePlan;
  error?: string;
}

export interface NgpcraftState {
  extension: NgpcProjectExtension | null;
  nativeColorAvailable: boolean;
  palettePlans: Record<string, NgpcPalettePlanState>;
  // By scene id; empty until a build in this session.
  sceneBudgets: Record<string, NgpcSceneBudget>;
  // NGPC planes, advanced mode: the camera window of the scene view (scene
  // pixels, by scene id) and which planes the scene view draws.
  cameraPreview: Record<string, { x: number; y: number }>;
  planeVisibility: { scr1: boolean; scr2: boolean };
}

export const initialState: NgpcraftState = {
  extension: null,
  nativeColorAvailable: false,
  palettePlans: {},
  sceneBudgets: {},
  cameraPreview: {},
  planeVisibility: { scr1: true, scr2: true },
};

export const setSceneBudgets = createAction<NgpcSceneBudget[]>(
  "ngpcraftView/setSceneBudgets",
);

export { dropOrphanResources };

export const setCameraPreview = createAction<{
  sceneId: string;
  x: number;
  y: number;
}>("ngpcraftView/setCameraPreview");

export const setPlaneVisibility = createAction<{
  plane: "scr1" | "scr2";
  visible: boolean;
}>("ngpcraftView/setPlaneVisibility");

export const loadNativeColorSupport = createAsyncThunk<boolean>(
  "ngpcraftView/loadNativeColorSupport",
  () => API.ngpcraft.nativeColorSupport(),
);

export const loadPalettePlan = createAsyncThunk<
  { plan: NgpcPalettePlan } | { error: string },
  string
>("ngpcraftView/loadPalettePlan", (backgroundId) =>
  API.ngpcraft.palettePlan(backgroundId),
);

const ngpcraftSlice = createSlice({
  name: "ngpcraft",
  initialState,
  reducers: {
    setProjectSetting: (
      state,
      action: PayloadAction<{
        key: NgpcProjectSettingKey;
        value: NgpcProjectSettings[NgpcProjectSettingKey] | undefined;
      }>,
    ) => {
      state.extension = setNgpcProjectSetting(
        state.extension,
        action.payload.key,
        action.payload.value,
      );
    },
    setSoundEffects: (
      state,
      action: PayloadAction<{
        soundId: string;
        timelines: NgpcNativeSfxStep[][] | undefined;
      }>,
    ) => {
      state.extension = setNgpcSoundEffects(
        state.extension,
        action.payload.soundId,
        action.payload.timelines,
      );
    },
    setResourceSetting: (
      state,
      action: PayloadAction<{
        resourceId: string;
        key: string;
        value: unknown;
      }>,
    ) => {
      state.extension = setNgpcResourceSetting(
        state.extension,
        action.payload.resourceId,
        action.payload.key,
        action.payload.value,
      );
    },
  },
  extraReducers: (builder) =>
    builder
      .addCase(projectActions.loadProject.fulfilled, (state, action) => {
        state.extension = action.payload.ngpcraftExtension ?? null;
        state.palettePlans = {};
        state.sceneBudgets = {};
        state.cameraPreview = {};
      })
      .addCase(setCameraPreview, (state, action) => {
        state.cameraPreview[action.payload.sceneId] = {
          x: action.payload.x,
          y: action.payload.y,
        };
      })
      .addCase(setPlaneVisibility, (state, action) => {
        state.planeVisibility[action.payload.plane] = action.payload.visible;
      })
      .addCase(dropOrphanResources, (state, action) => {
        const resources = state.extension?.resources;
        if (
          !state.extension ||
          !resources ||
          typeof resources !== "object" ||
          action.payload.length === 0
        ) {
          return;
        }
        const drop = new Set(action.payload);
        state.extension = {
          ...state.extension,
          resources: Object.fromEntries(
            Object.entries(resources).filter(([id]) => !drop.has(id)),
          ),
        };
      })
      .addCase(setSceneBudgets, (state, action) => {
        state.sceneBudgets = Object.fromEntries(
          action.payload.map((budget) => [budget.id, budget]),
        );
      })
      .addCase(loadNativeColorSupport.fulfilled, (state, action) => {
        state.nativeColorAvailable = action.payload;
      })
      .addCase(loadNativeColorSupport.rejected, (state) => {
        state.nativeColorAvailable = false;
      })
      .addCase(loadPalettePlan.pending, (state, action) => {
        const previous = state.palettePlans[action.meta.arg];
        state.palettePlans[action.meta.arg] = { ...previous, loading: true };
      })
      .addCase(loadPalettePlan.fulfilled, (state, action) => {
        state.palettePlans[action.meta.arg] =
          "plan" in action.payload
            ? { loading: false, plan: action.payload.plan }
            : { loading: false, error: action.payload.error };
      })
      .addCase(loadPalettePlan.rejected, (state, action) => {
        state.palettePlans[action.meta.arg] = {
          loading: false,
          error: action.error.message ?? "palette-plan failed",
        };
      }),
});

export const { actions } = ngpcraftSlice;

export default ngpcraftSlice.reducer;
