/*
 * NgpCraft: "NGPC native" colours of a sprite sheet (phase 2,
 * docs/ETUDE_16_PALETTES.md; V3 docs/M7_NATIVE_COLOR.md), in the sprite
 * editor. The choice is ngpcraft.json resources.<sheet id>.ngpc_color; in
 * native mode the V3 plan of the sheet's tiles is shown (palettes, tiles in
 * error). Offered only when the installed V3 has `palette-plan`.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import styled from "styled-components";
import { SingleValue } from "react-select";
import l10n from "shared/lib/lang/l10n";
import API from "renderer/lib/api";
import { FormField, FormRow } from "ui/form/layout/FormLayout";
import { FormInfo } from "ui/form/FormInfo";
import { Select } from "ui/form/Select";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { actions as ngpcraftActions } from "store/features/ngpcraft/ngpcraftState";
import { getNgpcResourceSetting } from "shared/lib/ngpcraft/projectExtension";
import {
  NGPC_COLOR_KEY,
  NGPC_COLOR_NATIVE,
  NgpcSpritePalettePlan,
} from "shared/lib/ngpcraft/palettePlan";
import { useNgpcNativeColorAvailable } from "components/ngpcraft/NgpcNativePalettes";

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
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

interface ColorOption {
  value: "gbs" | "native";
  label: string;
}

interface NgpcSpriteColorsProps {
  spriteId: string;
  // Asset version: the plan is read again when the PNG changes.
  spriteVersion?: number;
}

export const NgpcSpriteColors = ({
  spriteId,
  spriteVersion,
}: NgpcSpriteColorsProps) => {
  const dispatch = useAppDispatch();
  const available = useNgpcNativeColorAvailable();
  const native = useAppSelector(
    (state) =>
      getNgpcResourceSetting(
        state.ngpcraft.extension,
        spriteId,
        NGPC_COLOR_KEY,
      ) === NGPC_COLOR_NATIVE,
  );
  const [plan, setPlan] = useState<NgpcSpritePalettePlan>();
  const [error, setError] = useState("");

  const options: ColorOption[] = useMemo(
    () => [
      { value: "gbs", label: l10n("FIELD_NGPC_SPRITE_COLORS_GBS") },
      { value: "native", label: l10n("FIELD_NGPC_SPRITE_COLORS_NATIVE") },
    ],
    [],
  );

  useEffect(() => {
    if (!native) {
      setPlan(undefined);
      setError("");
      return;
    }
    let current = true;
    API.ngpcraft.spritePalettePlan(spriteId).then((result) => {
      if (!current) {
        return;
      }
      if ("plan" in result) {
        setPlan(result.plan);
        setError("");
      } else {
        setPlan(undefined);
        setError(result.error);
      }
    });
    return () => {
      current = false;
    };
  }, [native, spriteId, spriteVersion]);

  const onChange = useCallback(
    (option: SingleValue<ColorOption>) => {
      if (!option) {
        return;
      }
      dispatch(
        ngpcraftActions.setResourceSetting({
          resourceId: spriteId,
          key: NGPC_COLOR_KEY,
          value: option.value === "native" ? NGPC_COLOR_NATIVE : undefined,
        }),
      );
    },
    [dispatch, spriteId],
  );

  if (!available && !native) {
    return null;
  }

  const tileErrors = plan?.errors.filter((e) => e.tile !== undefined) ?? [];
  const limitError = plan?.errors.find((e) => e.reason === "palette_limit");

  return (
    <FormRow>
      <FormField
        name="ngpcSpriteColors"
        label={l10n("FIELD_NGPC_SPRITE_COLORS")}
      >
        <Wrapper>
          <Select
            value={options.find((o) => o.value === (native ? "native" : "gbs"))}
            options={options}
            onChange={onChange}
          />
          {native && (
            <>
              <FormInfo>{l10n("FIELD_NGPC_SPRITE_COLORS_INFO")}</FormInfo>
              {error && <ErrorText>{error}</ErrorText>}
              {plan && (
                <>
                  <Row>
                    {l10n("FIELD_NGPC_SPRITE_PALETTES", {
                      count: plan.palettes.length,
                      limit: plan.paletteLimit,
                    })}
                  </Row>
                  <Row>
                    {plan.palettes.map((palette, index) => (
                      <Palette
                        key={index}
                        title={palette.map((c) => `#${c}`).join(" ")}
                      >
                        {palette.map((color, c) => (
                          <Swatch key={c} $color={color} />
                        ))}
                      </Palette>
                    ))}
                  </Row>
                  {tileErrors.length > 0 && (
                    <ErrorText
                      title={(tileErrors[0].colors ?? [])
                        .map((c) => `#${c}`)
                        .join(" ")}
                    >
                      {l10n("FIELD_NGPC_SPRITE_TILE_ERROR", {
                        count: tileErrors.length,
                        tile: tileErrors[0].tile ?? 0,
                      })}
                    </ErrorText>
                  )}
                  {limitError && (
                    <ErrorText>
                      {l10n("FIELD_NGPC_SPRITE_LIMIT_ERROR", {
                        palettes: limitError.palettes ?? 0,
                        limit: plan.paletteLimit,
                      })}
                    </ErrorText>
                  )}
                </>
              )}
            </>
          )}
        </Wrapper>
      </FormField>
    </FormRow>
  );
};
