/* eslint-disable camelcase */ // Keys of ngpcraft.json (V3 format).
/*
 * NgpCraft: the planes of a scene, in the scene sidebar.
 * - GB Studio mode (default): one background, as in GB Studio; the V3 places
 *   text, priority tiles and colour complements on the planes itself.
 * - NGPC planes (advanced): a second background on SCR1, drawn over the main
 *   one (SCR2), transparent where its GB colour index is 0, at its own rate
 *   of the camera ("fixed", "1", "1/2".."1/256" or "N%"), optionally looping
 *   per axis. Stored in ngpcraft.json resources.<scene id>.second_background
 *   (V3 docs/FORMAT_PROJET.md "Second décor NGPC"); lost on a return to
 *   GB Studio. The scene view shows it through a camera window.
 */
import React, { useCallback, useMemo } from "react";
import { SingleValue } from "react-select";
import l10n from "shared/lib/lang/l10n";
import { FormField, FormRow } from "ui/form/layout/FormLayout";
import { FormInfo } from "ui/form/FormInfo";
import { Select } from "ui/form/Select";
import { CheckboxField } from "ui/form/CheckboxField";
import { NumberField } from "ui/form/NumberField";
import { useAppDispatch, useAppSelector } from "store/hooks";
import {
  actions as ngpcraftActions,
  setPlaneVisibility,
} from "store/features/ngpcraft/ngpcraftState";
import { backgroundSelectors } from "store/features/entities/entitiesSelectors";
import { BackgroundSelectButton } from "components/forms/BackgroundSelectButton";
import type { ColorModeSetting } from "shared/lib/resources/types";
import {
  MAX_PERCENT,
  percentOf,
  RATE_SHIFT,
} from "shared/lib/ngpcraft/secondBackground";
import {
  NGPC_SECOND_BACKGROUND_KEY,
  SecondBackgroundSetting,
  useSecondBackground,
} from "./useSecondBackground";

// second_background.py SCROLL_RATES, in the V3's order, then "custom" (N%).
const CUSTOM = "custom";
type RateChoice = keyof typeof RATE_SHIFT | typeof CUSTOM;

interface Option<T> {
  value: T;
  label: string;
}

type PlaneMode = "gbstudio" | "ngpc";

interface NgpcSecondBackgroundProps {
  sceneId: string;
  mainBackgroundId: string;
  tilesetId: string;
  uiPaletteId: string;
  colorMode: ColorModeSetting;
}

interface RateFieldProps {
  name: string;
  label: string;
  value: string;
  options: Option<RateChoice>[];
  onChange: (rate: string) => void;
}

const RateField = ({
  name,
  label,
  value,
  options,
  onChange,
}: RateFieldProps) => {
  const percent = percentOf(value);
  const choice: RateChoice = percent !== undefined ? CUSTOM : value;
  return (
    <FormField name={name} label={label}>
      <Select
        value={options.find((o) => o.value === choice)}
        options={options}
        onChange={(o: SingleValue<Option<RateChoice>>) => {
          if (!o) {
            return;
          }
          onChange(o.value === CUSTOM ? "100%" : o.value);
        }}
      />
      {percent !== undefined && (
        <NumberField
          name={`${name}Percent`}
          value={percent}
          min={0}
          max={MAX_PERCENT}
          placeholder="0"
          onChange={(e) => {
            const n = Math.round(Number(e.currentTarget.value) || 0);
            onChange(`${Math.max(0, Math.min(MAX_PERCENT, n))}%`);
          }}
        />
      )}
    </FormField>
  );
};

export const NgpcSecondBackground = ({
  sceneId,
  mainBackgroundId,
  tilesetId,
  uiPaletteId,
  colorMode,
}: NgpcSecondBackgroundProps) => {
  const dispatch = useAppDispatch();
  const second = useSecondBackground(sceneId);
  const backgrounds = useAppSelector((state) =>
    backgroundSelectors.selectAll(state),
  );
  const planeVisibility = useAppSelector(
    (state) => state.ngpcraft.planeVisibility,
  );

  const modeOptions: Option<PlaneMode>[] = useMemo(
    () => [
      { value: "gbstudio", label: l10n("FIELD_NGPC_PLANES_GBSTUDIO") },
      { value: "ngpc", label: l10n("FIELD_NGPC_PLANES_ADVANCED") },
    ],
    [],
  );

  const rateOptions: Option<RateChoice>[] = useMemo(
    () => [
      ...Object.keys(RATE_SHIFT).map((rate) => ({
        value: rate,
        label:
          rate === "fixed"
            ? l10n("FIELD_NGPC_SECOND_BG_FIXED")
            : rate === "1"
              ? l10n("FIELD_NGPC_SECOND_BG_CAMERA")
              : rate,
      })),
      { value: CUSTOM, label: l10n("FIELD_NGPC_SECOND_BG_CUSTOM") },
    ],
    [],
  );

  const write = useCallback(
    (next: SecondBackgroundSetting | undefined) => {
      dispatch(
        ngpcraftActions.setResourceSetting({
          resourceId: sceneId,
          key: NGPC_SECOND_BACKGROUND_KEY,
          value: next,
        }),
      );
    },
    [dispatch, sceneId],
  );

  // Optional V3 fields are written only when true (no implicit defaults).
  const update = useCallback(
    (patch: Partial<SecondBackgroundSetting>) => {
      if (!second) {
        return;
      }
      const next: SecondBackgroundSetting = { ...second, ...patch };
      if (!next.loop_x) {
        delete next.loop_x;
      }
      if (!next.loop_y) {
        delete next.loop_y;
      }
      if (!next.behind) {
        delete next.behind;
      }
      write(next);
    },
    [second, write],
  );

  const onMode = useCallback(
    (o: SingleValue<Option<PlaneMode>>) => {
      if (!o) {
        return;
      }
      if (o.value === "gbstudio") {
        write(undefined);
        return;
      }
      if (second) {
        return;
      }
      const first =
        backgrounds.find((b) => b.id !== mainBackgroundId) ?? backgrounds[0];
      if (first) {
        write({ background_id: first.id, scroll_x: "1/2", scroll_y: "1/2" });
      }
    },
    [backgrounds, mainBackgroundId, second, write],
  );

  return (
    <>
      <FormRow>
        <FormField name="ngpcPlanes" label={l10n("FIELD_NGPC_PLANES")}>
          <Select
            value={modeOptions.find(
              (o) => o.value === (second ? "ngpc" : "gbstudio"),
            )}
            options={modeOptions}
            onChange={onMode}
          />
        </FormField>
      </FormRow>
      {second && (
        <>
          <FormRow>
            <FormField
              name="ngpcSecondBackgroundId"
              label={l10n("FIELD_NGPC_SECOND_BG_IMAGE")}
            >
              <BackgroundSelectButton
                name="ngpcSecondBackgroundId"
                value={second.background_id}
                tilesetId={tilesetId}
                uiPaletteId={uiPaletteId}
                colorMode={colorMode}
                is360={false}
                onChange={(id) => update({ background_id: id })}
              />
            </FormField>
          </FormRow>
          <FormRow>
            <RateField
              name="ngpcSecondScrollX"
              label={l10n("FIELD_NGPC_SECOND_BG_SCROLL_X")}
              value={second.scroll_x}
              options={rateOptions}
              onChange={(rate) => update({ scroll_x: rate })}
            />
            <RateField
              name="ngpcSecondScrollY"
              label={l10n("FIELD_NGPC_SECOND_BG_SCROLL_Y")}
              value={second.scroll_y}
              options={rateOptions}
              onChange={(rate) => update({ scroll_y: rate })}
            />
          </FormRow>
          <FormRow>
            <CheckboxField
              name="ngpcSecondLoopX"
              label={l10n("FIELD_NGPC_SECOND_BG_LOOP_X")}
              checked={!!second.loop_x}
              onChange={(e) => update({ loop_x: e.currentTarget.checked })}
            />
            <CheckboxField
              name="ngpcSecondLoopY"
              label={l10n("FIELD_NGPC_SECOND_BG_LOOP_Y")}
              checked={!!second.loop_y}
              onChange={(e) => update({ loop_y: e.currentTarget.checked })}
            />
          </FormRow>
          <FormRow>
            <CheckboxField
              name="ngpcSecondBehind"
              label={l10n("FIELD_NGPC_SECOND_BG_BEHIND")}
              title={l10n("FIELD_NGPC_SECOND_BG_BEHIND_INFO")}
              checked={!!second.behind}
              onChange={(e) => update({ behind: e.currentTarget.checked })}
            />
          </FormRow>
          <FormRow>
            <CheckboxField
              name="ngpcShowScr2"
              label={l10n("FIELD_NGPC_SHOW_SCR2")}
              checked={planeVisibility.scr2}
              onChange={(e) =>
                dispatch(
                  setPlaneVisibility({
                    plane: "scr2",
                    visible: e.currentTarget.checked,
                  }),
                )
              }
            />
            <CheckboxField
              name="ngpcShowScr1"
              label={l10n("FIELD_NGPC_SHOW_SCR1")}
              checked={planeVisibility.scr1}
              onChange={(e) =>
                dispatch(
                  setPlaneVisibility({
                    plane: "scr1",
                    visible: e.currentTarget.checked,
                  }),
                )
              }
            />
          </FormRow>
          <FormRow>
            <FormInfo>{l10n("FIELD_NGPC_SECOND_BG_INFO")}</FormInfo>
          </FormRow>
        </>
      )}
    </>
  );
};
