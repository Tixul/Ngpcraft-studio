/*
 * NgpCraft: "NGPC native" colours of a background, in the scene sidebar
 * (docs/ETUDE_16_PALETTES.md). Shows what the V3 computed from the PNG:
 * backdrop, palettes (3 colours each, 16 at most), tiles in error.
 */
import React, { useEffect } from "react";
import styled from "styled-components";
import l10n from "shared/lib/lang/l10n";
import { useAppDispatch, useAppSelector } from "store/hooks";
import {
  loadNativeColorSupport,
  loadPalettePlan,
} from "store/features/ngpcraft/ngpcraftState";
import {
  isNgpcNativeResource,
  NgpcProjectExtension,
} from "shared/lib/ngpcraft/projectExtension";

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
`;

const Row = styled.div`
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
`;

const Swatch = styled.span<{ $color: string }>`
  display: inline-block;
  width: 10px;
  height: 14px;
  background: #${(props) => props.$color};
  border: 1px solid ${(props) => props.theme.colors.input.border};
`;

const Palette = styled.span`
  display: inline-flex;
  margin-right: 4px;
`;

const ErrorText = styled.div`
  color: #e57373;
`;

let supportRequested = false;

// Ask the main process once whether the V3 offers "NGPC native" colours.
export const useNgpcNativeColorAvailable = (): boolean => {
  const dispatch = useAppDispatch();
  const available = useAppSelector(
    (state) => state.ngpcraft.nativeColorAvailable,
  );
  useEffect(() => {
    if (!supportRequested) {
      supportRequested = true;
      dispatch(loadNativeColorSupport());
    }
  }, [dispatch]);
  return available;
};

// Own ngpc_color, else the project default (V3 native_color.is_native).
export const isNgpcNativeBackground = (
  extension: NgpcProjectExtension | null,
  backgroundId: string | undefined,
) => isNgpcNativeResource(extension, backgroundId);

interface NgpcNativePalettesProps {
  backgroundId: string;
  // Asset version: the plan is read again when the PNG changes.
  backgroundVersion?: number;
}

export const NgpcNativePalettes = ({
  backgroundId,
  backgroundVersion,
}: NgpcNativePalettesProps) => {
  const dispatch = useAppDispatch();
  const state = useAppSelector((s) => s.ngpcraft.palettePlans[backgroundId]);

  useEffect(() => {
    dispatch(loadPalettePlan(backgroundId));
  }, [dispatch, backgroundId, backgroundVersion]);

  if (!state || (state.loading && !state.plan)) {
    return <Wrapper>{l10n("FIELD_NGPC_PALETTES_LOADING")}</Wrapper>;
  }
  if (state.error || !state.plan) {
    return (
      <Wrapper>
        <ErrorText>{state.error}</ErrorText>
      </Wrapper>
    );
  }
  const { plan } = state;
  const first = plan.errors[0];
  return (
    <Wrapper>
      <Row title={`#${plan.backdrop}`}>
        {l10n("FIELD_NGPC_BACKDROP")}
        <Swatch $color={plan.backdrop} />
      </Row>
      <Row>
        {l10n("FIELD_NGPC_PALETTES_COUNT", {
          count: plan.palettes.length,
          limit: plan.paletteLimit,
        })}
      </Row>
      <Row>
        {plan.palettes.map((palette, index) => (
          <Palette key={index} title={palette.map((c) => `#${c}`).join(" ")}>
            {palette.map((color, c) => (
              <Swatch key={c} $color={color} />
            ))}
          </Palette>
        ))}
      </Row>
      {first && (
        <ErrorText>
          {l10n("FIELD_NGPC_PALETTES_ERRORS", {
            count: plan.errors.length,
            x: first.x,
            y: first.y,
          })}
        </ErrorText>
      )}
    </Wrapper>
  );
};

const ErrorTile = styled.div`
  position: absolute;
  width: 8px;
  height: 8px;
  box-sizing: border-box;
  border: 1px solid #ff1744;
  background: rgba(255, 23, 68, 0.35);
`;

interface NgpcPaletteErrorsOverlayProps {
  backgroundId: string;
}

// Red squares on the tiles the V3 cannot colour (more than 3 colours plus
// the backdrop, or beyond 16 palettes). Scene pixel coordinates.
export const NgpcPaletteErrorsOverlay = ({
  backgroundId,
}: NgpcPaletteErrorsOverlayProps) => {
  const native = useAppSelector((state) =>
    isNgpcNativeBackground(state.ngpcraft.extension, backgroundId),
  );
  const errors = useAppSelector(
    (state) => state.ngpcraft.palettePlans[backgroundId]?.plan?.errors,
  );
  if (!native || !errors || errors.length === 0) {
    return null;
  }
  return (
    <>
      {errors.map((error) => (
        <ErrorTile
          key={`${error.x}_${error.y}`}
          style={{ left: error.x * 8, top: error.y * 8 }}
          title={error.colors.map((c) => `#${c}`).join(" ")}
        />
      ))}
    </>
  );
};
