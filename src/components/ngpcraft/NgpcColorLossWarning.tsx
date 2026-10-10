/*
 * NgpCraft: under the background palettes of a scene, when the background is
 * in a GB Studio colour mode and its PNG has colours those modes merge (GB
 * Studio reads 4 shades from the green channel: red and black become the same
 * shade), say so and offer the "NGPC native" mode, which keeps the PNG
 * colours and computes the palettes itself. A choice, nothing is changed
 * until the button is pressed.
 */
import React, { useEffect, useState } from "react";
import styled from "styled-components";
import l10n from "shared/lib/lang/l10n";
import { Button } from "ui/buttons/Button";
import { ColorLoss, findColorLosses } from "shared/lib/ngpcraft/colorLoss";

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  font-size: 11px;
  border-radius: 4px;
  border: 1px solid #e5a73a;
  background: rgba(229, 167, 58, 0.12);
`;

const Swatch = styled.span<{ $color: string }>`
  display: inline-block;
  width: 10px;
  height: 10px;
  margin: 0 2px;
  vertical-align: middle;
  background: #${(props) => props.$color};
  border: 1px solid ${(props) => props.theme.colors.input.border};
`;

interface NgpcColorLossWarningProps {
  src: string;
  onUseNative: () => void;
}

export const NgpcColorLossWarning = ({
  src,
  onUseNative,
}: NgpcColorLossWarningProps) => {
  const [losses, setLosses] = useState<ColorLoss[]>([]);

  useEffect(() => {
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (cancelled || !ctx) {
        return;
      }
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, image.width, image.height).data;
      setLosses(findColorLosses(data));
    };
    image.src = src;
    return () => {
      cancelled = true;
    };
  }, [src]);

  if (losses.length === 0) {
    return null;
  }
  const first = losses[0];
  return (
    <Wrapper>
      <div>
        {l10n("FIELD_NGPC_COLOR_LOSS")} <Swatch $color={first.a} />#{first.a}{" "}
        = <Swatch $color={first.b} />#{first.b}
        {losses.length > 1
          ? ` (+${losses.length - 1})`
          : ""}
      </div>
      <div>
        <Button size="small" onClick={onUseNative}>
          {l10n("FIELD_NGPC_COLOR_LOSS_USE_NATIVE")}
        </Button>
      </div>
    </Wrapper>
  );
};
