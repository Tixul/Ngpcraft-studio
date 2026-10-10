import { createAction } from "@reduxjs/toolkit";

export type BuildType = "web" | "rom" | "pocket";
export type ProjectExportType = "src" | "data";

const buildGame = createAction(
  "buildGame/build",
  (
    {
      buildType = "web",
      exportBuild = false,
      debugEnabled = false,
      startSceneId,
      startX,
      startY,
      onlySelection,
      ngpcMusicPreviewId,
    }: {
      buildType?: BuildType;
      exportBuild?: boolean;
      debugEnabled?: boolean;
      startSceneId?: string;
      startX?: number;
      startY?: number;
      onlySelection?: boolean;
      // NgpCraft: a ROM that only plays this song (V3 preview-music).
      ngpcMusicPreviewId?: string;
    } = {
      buildType: "web",
      exportBuild: false,
      debugEnabled: false,
    },
  ) => {
    return {
      payload: {
        buildType,
        exportBuild,
        debugEnabled,
        startSceneId,
        startX,
        startY,
        onlySelection,
        ngpcMusicPreviewId,
      },
    };
  },
);

const deleteBuildCache = createAction("buildGame/deleteCache");
const ejectEngine = createAction("buildGame/ejectEngine");
const ejectWebTemplate = createAction("buildGame/ejectWebTemplate");
const exportProject = createAction<ProjectExportType>(
  "buildGame/exportProject",
);

const buildGameActions = {
  buildGame,
  deleteBuildCache,
  ejectEngine,
  ejectWebTemplate,
  exportProject,
};

export default buildGameActions;
