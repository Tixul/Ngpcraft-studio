/*
 * NgpCraft: a background in "NGPC native" colours, as the console shows it:
 * the PNG itself, each channel rounded to 4 bits (V3 rule: >> 4, shown as
 * c * 17). Replaces GB Studio's palette colourisation for that background.
 * Transparent pixels (alpha < 128, V3 native_pixels) show the backdrop.
 */
import React, { useEffect, useRef } from "react";
import styled from "styled-components";

const Canvas = styled.canvas`
  image-rendering: pixelated;
`;

interface NgpcNativeImageProps {
  src: string;
  width: number;
  height: number;
  // "rrggbb" from the V3 palette plan (chosen ngpc_backdrop or automatic);
  // absent: transparent pixels stay transparent.
  backdrop?: string;
}

export const NgpcNativeImage = ({
  src,
  width,
  height,
  backdrop,
}: NgpcNativeImageProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    const image = new Image();
    const fill =
      backdrop && /^[0-9a-fA-F]{6}$/.test(backdrop)
        ? [0, 2, 4].map((k) => parseInt(backdrop.slice(k, k + 2), 16))
        : undefined;
    image.onload = () => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (cancelled || !canvas || !ctx) {
        return;
      }
      ctx.clearRect(0, 0, width, height);
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, width, height);
      const pixels = data.data;
      for (let i = 0; i < pixels.length; i += 4) {
        if (fill && pixels[i + 3] < 128) {
          pixels[i] = fill[0];
          pixels[i + 1] = fill[1];
          pixels[i + 2] = fill[2];
          pixels[i + 3] = 255;
          continue;
        }
        pixels[i] = (pixels[i] >> 4) * 17;
        pixels[i + 1] = (pixels[i + 1] >> 4) * 17;
        pixels[i + 2] = (pixels[i + 2] >> 4) * 17;
      }
      ctx.putImageData(data, 0, 0);
    };
    image.src = src;
    return () => {
      cancelled = true;
    };
  }, [src, width, height, backdrop]);

  return <Canvas ref={canvasRef} width={width} height={height} />;
};
