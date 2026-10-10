/*
 * NgpCraft: a background in "NGPC native" colours, as the console shows it:
 * the PNG itself, each channel rounded to 4 bits (V3 rule: >> 4, shown as
 * c * 17). Replaces GB Studio's palette colourisation for that background.
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
}

export const NgpcNativeImage = ({
  src,
  width,
  height,
}: NgpcNativeImageProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    const image = new Image();
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
  }, [src, width, height]);

  return <Canvas ref={canvasRef} width={width} height={height} />;
};
