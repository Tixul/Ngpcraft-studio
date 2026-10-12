/*
 * NgpCraft: the second background (SCR1) drawn over the main one in the scene
 * view. Its offset follows the camera at its own rate (V3 second_stream.py),
 * so it is drawn for a camera: the camera window of a selected scene (exact
 * everywhere for that position), otherwise the camera of each screen of the
 * scene grid. A looping axis repeats the image. Colours: the second
 * background's tile colours with the scene palettes (V3 background_source);
 * GB colour index 0 is transparent. A native-colour (or GB Studio
 * "Automatic") second background shows its own colours on 4 bits,
 * transparent on its backdrop colour (shared/lib/ngpcraft/nativeLayer).
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import styled from "styled-components";
import { DMG_PALETTE, TILE_SIZE } from "consts";
import { useAppSelector } from "store/hooks";
import { backgroundSelectors } from "store/features/entities/entitiesSelectors";
import { hex2NgpcRgb } from "shared/lib/ngpcraft/color";
import { assetURL } from "shared/lib/helpers/assets";
import { MonoBGPPalette, Palette } from "shared/lib/resources/types";
import { useSecondBackground } from "./useSecondBackground";
import { isNgpcNativeBackground } from "./NgpcNativePalettes";
import { toNativeFrontLayer } from "shared/lib/ngpcraft/nativeLayer";
import {
  SecondBackgroundConfig,
  secondBackgroundForCamera,
  secondBackgroundScreens,
} from "shared/lib/ngpcraft/secondBackground";

const Canvas = styled.canvas`
  position: absolute;
  top: 0;
  left: 0;
  image-rendering: pixelated;
  pointer-events: none;
`;

// Same thresholds as ColorizedImage.worker (GB Studio's colour index).
const indexColour = (g: number) => {
  if (g < 65) {
    return 3;
  }
  if (g < 130) {
    return 2;
  }
  if (g < 205) {
    return 1;
  }
  return 0;
};

interface NgpcSecondBackgroundPreviewProps {
  sceneId: string;
  width: number; // tiles
  height: number; // tiles
  palettes: Palette[];
  previewAsMono?: boolean;
  monoBGP?: MonoBGPPalette;
  // Camera window position (scene pixels), or undefined: screen by screen.
  camera?: { x: number; y: number };
}

export const NgpcSecondBackgroundPreview = ({
  sceneId,
  width,
  height,
  palettes,
  previewAsMono,
  monoBGP,
  camera,
}: NgpcSecondBackgroundPreviewProps) => {
  const second = useSecondBackground(sceneId);
  const visible = useAppSelector(
    (state) => state.ngpcraft.planeVisibility.scr1,
  );
  const background = useAppSelector((state) =>
    second
      ? backgroundSelectors.selectById(state, second.background_id)
      : undefined,
  );
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [layer, setLayer] = useState<HTMLCanvasElement>();
  const sceneW = width * TILE_SIZE;
  const sceneH = height * TILE_SIZE;
  const src = background ? assetURL("backgrounds", background) : "";
  const tiles = background?.tileColors;
  const nativeColours = useAppSelector(
    (state) =>
      !!background &&
      (isNgpcNativeBackground(state.ngpcraft.extension, background.id) ||
        (!!background.autoColor && !previewAsMono)),
  );

  // SCR1 colourised once per image / palettes, index 0 transparent.
  useEffect(() => {
    if (!src) {
      setLayer(undefined);
      return;
    }
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) {
        return;
      }
      const w = image.width;
      const h = image.height;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        return;
      }
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, w, h);
      const pixels = data.data;
      if (nativeColours) {
        toNativeFrontLayer(pixels);
        ctx.putImageData(data, 0, 0);
        setLayer(canvas);
        return;
      }
      const rgb = palettes.map((p) => p.colors.map(hex2NgpcRgb));
      const dmg = DMG_PALETTE.colors.map(hex2NgpcRgb);
      const bgp = monoBGP ?? [0, 1, 2, 3];
      const tileW = Math.floor(w / 8);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = (x + y * w) * 4;
          const index = indexColour(pixels[i + 1]);
          if (index === 0) {
            pixels[i + 3] = 0;
            continue;
          }
          const tile = tiles?.[(x >> 3) + (y >> 3) * tileW] ?? 0;
          const color = previewAsMono
            ? dmg[bgp[index]]
            : (rgb[tile & 0x7] ?? rgb[0])[index];
          pixels[i] = color.r;
          pixels[i + 1] = color.g;
          pixels[i + 2] = color.b;
          pixels[i + 3] = 255;
        }
      }
      ctx.putImageData(data, 0, 0);
      setLayer(canvas);
    };
    image.src = src;
    return () => {
      cancelled = true;
    };
  }, [src, tiles, palettes, previewAsMono, monoBGP, nativeColours]);

  const config: SecondBackgroundConfig | undefined = useMemo(
    () =>
      second && {
        rateX: second.scroll_x,
        rateY: second.scroll_y,
        loopX: !!second.loop_x,
        loopY: !!second.loop_y,
      },
    [second],
  );
  const cameraX = camera?.x;
  const cameraY = camera?.y;

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx || !layer || !config) {
      return;
    }
    ctx.clearRect(0, 0, sceneW, sceneH);
    const screens =
      cameraX !== undefined && cameraY !== undefined
        ? secondBackgroundForCamera(
            sceneW,
            sceneH,
            layer.width,
            layer.height,
            config,
            cameraX,
            cameraY,
          )
        : secondBackgroundScreens(
            sceneW,
            sceneH,
            layer.width,
            layer.height,
            config,
          );
    const pattern =
      config.loopX || config.loopY
        ? ctx.createPattern(
            layer,
            config.loopX && config.loopY
              ? "repeat"
              : config.loopX
                ? "repeat-x"
                : "repeat-y",
          )
        : null;
    for (const s of screens) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(s.dx, s.dy, s.w, s.h);
      ctx.clip();
      if (pattern) {
        ctx.translate(s.tx, s.ty);
        ctx.fillStyle = pattern;
        ctx.fillRect(s.dx - s.tx, s.dy - s.ty, s.w, s.h);
      } else {
        ctx.drawImage(layer, s.tx, s.ty);
      }
      ctx.restore();
    }
  }, [layer, config, cameraX, cameraY, sceneW, sceneH, visible]);

  if (!second || !background || !visible) {
    return null;
  }
  // Behind the main background, the console shows it only through the main
  // background's backdrop pixels: drawn half transparent here.
  return (
    <Canvas
      ref={canvasRef}
      width={sceneW}
      height={sceneH}
      style={second.behind ? { opacity: 0.5 } : undefined}
    />
  );
};
