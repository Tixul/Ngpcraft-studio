/*
 * NgpCraft: the camera window of a scene in NGPC planes (advanced) mode, a
 * 160 x view-height frame dragged by its label over the scene view (the
 * frame itself lets clicks through to actors and triggers). The second background
 * preview is drawn for this camera. View data only (ngpcraftView), clamped
 * like GB Studio's scroll; not saved in the project.
 */
import React, { useCallback, useRef } from "react";
import styled from "styled-components";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { setCameraPreview } from "store/features/ngpcraft/ngpcraftState";
import {
  clampCamera,
  VIEW_W,
  viewHeight,
} from "shared/lib/ngpcraft/secondBackground";
import l10n from "shared/lib/lang/l10n";

const Frame = styled.div`
  position: absolute;
  box-sizing: border-box;
  border: 1px solid #ffd400;
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.6),
    inset 0 0 0 1px rgba(0, 0, 0, 0.6);
  pointer-events: none;
  z-index: 50;
`;

const Label = styled.div`
  position: absolute;
  top: 1px;
  left: 1px;
  padding: 0 2px;
  font-size: 7px;
  line-height: 9px;
  color: #000;
  background: #ffd400;
  cursor: move;
  pointer-events: all;
  white-space: nowrap;
`;

interface NgpcCameraWindowProps {
  sceneId: string;
  width: number; // scene pixels
  height: number; // scene pixels
}

export const NgpcCameraWindow = ({
  sceneId,
  width,
  height,
}: NgpcCameraWindowProps) => {
  const dispatch = useAppDispatch();
  const zoom = useAppSelector((state) => state.editor.zoom) / 100;
  const stored = useAppSelector(
    (state) => state.ngpcraft.cameraPreview[sceneId],
  );
  const camera = clampCamera(stored?.x ?? 0, stored?.y ?? 0, width, height);
  const drag = useRef<
    { mouseX: number; mouseY: number; x: number; y: number } | undefined
  >(undefined);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) {
        return;
      }
      e.stopPropagation();
      e.preventDefault();
      drag.current = {
        mouseX: e.clientX,
        mouseY: e.clientY,
        x: camera.x,
        y: camera.y,
      };
      const onMove = (ev: MouseEvent) => {
        const start = drag.current;
        if (!start) {
          return;
        }
        const next = clampCamera(
          start.x + (ev.clientX - start.mouseX) / zoom,
          start.y + (ev.clientY - start.mouseY) / zoom,
          width,
          height,
        );
        dispatch(setCameraPreview({ sceneId, ...next }));
      };
      const onUp = () => {
        drag.current = undefined;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [camera.x, camera.y, dispatch, height, sceneId, width, zoom],
  );

  return (
    <Frame
      style={{
        left: camera.x,
        top: camera.y,
        width: VIEW_W,
        height: viewHeight(height),
      }}
    >
      <Label
        title={l10n("FIELD_NGPC_CAMERA_WINDOW_TT")}
        onMouseDown={onMouseDown}
      >
        {l10n("FIELD_NGPC_CAMERA_WINDOW")} {camera.x},{camera.y}
      </Label>
    </Frame>
  );
};
