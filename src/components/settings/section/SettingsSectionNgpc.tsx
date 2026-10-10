/*
 * NgpCraft: project settings of the NGPC build, stored in ngpcraft.json
 * (V3 docs/FORMAT_PROJET.md §3). A setting left at its default is removed
 * from the file, so the V3 applies its own default.
 */
import React, { useCallback, useMemo } from "react";
import { SingleValue } from "react-select";
import l10n from "shared/lib/lang/l10n";
import { CardAnchor, CardHeading } from "ui/cards/Card";
import { SearchableCard } from "ui/cards/SearchableCard";
import { SearchableSettingRow } from "ui/form/SearchableSettingRow";
import { SettingRowInput, SettingRowLabel } from "ui/form/SettingRow";
import { FormInfo } from "ui/form/FormInfo";
import { NumberField } from "ui/form/NumberField";
import { Select } from "ui/form/Select";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { actions as ngpcraftActions } from "store/features/ngpcraft/ngpcraftState";
import {
  getNgpcProjectSettings,
  NGPC_ACTOR_CAPACITY_MAX,
  NGPC_ACTOR_CAPACITY_MIN,
  NGPC_PROJECTILE_CAPACITY_MAX,
  NGPC_PROJECTILE_CAPACITY_MIN,
} from "shared/lib/ngpcraft/projectExtension";

interface SettingsSectionNgpcProps {
  searchTerm: string;
}

interface ValueOption<T> {
  value: T;
  label: string;
}

export const SettingsSectionNgpc = ({
  searchTerm,
}: SettingsSectionNgpcProps) => {
  const dispatch = useAppDispatch();
  const extension = useAppSelector((state) => state.ngpcraft.extension);
  const settings = getNgpcProjectSettings(extension);

  const optionLongPress = settings.option_long_press ?? "select";
  const actorCapacity = settings.actor_capacity ?? NGPC_ACTOR_CAPACITY_MIN;
  const projectileCapacity =
    settings.projectile_capacity ?? NGPC_PROJECTILE_CAPACITY_MIN;
  const frameRate = settings.frame_rate ?? 60;
  const contextPolicy = settings.vm_context_policy ?? "auto";
  const defaultColor = settings.default_color ?? "gbs";

  const colorOptions: ValueOption<"gbs" | "native">[] = useMemo(
    () => [
      { value: "gbs", label: l10n("FIELD_NGPC_DEFAULT_COLOR_GBS") },
      { value: "native", label: l10n("FIELD_NGPC_DEFAULT_COLOR_NATIVE") },
    ],
    [],
  );

  const onChangeDefaultColor = useCallback(
    (option: SingleValue<ValueOption<"gbs" | "native">>) => {
      if (!option) {
        return;
      }
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "default_color",
          value: option.value === "native" ? "native" : undefined,
        }),
      );
    },
    [dispatch],
  );

  const longPressOptions: ValueOption<"select" | "start">[] = useMemo(
    () => [
      { value: "select", label: l10n("FIELD_NGPC_OPTION_LONG_PRESS_SELECT") },
      { value: "start", label: l10n("FIELD_NGPC_OPTION_LONG_PRESS_START") },
    ],
    [],
  );
  const frameRateOptions: ValueOption<60 | 30>[] = useMemo(
    () => [
      { value: 60, label: l10n("FIELD_NGPC_FRAME_RATE_60") },
      { value: 30, label: l10n("FIELD_NGPC_FRAME_RATE_30") },
    ],
    [],
  );

  const contextOptions: ValueOption<"auto" | "gbvm-16">[] = useMemo(
    () => [
      { value: "auto", label: l10n("FIELD_NGPC_VM_CONTEXTS_AUTO") },
      { value: "gbvm-16", label: l10n("FIELD_NGPC_VM_CONTEXTS_GBVM") },
    ],
    [],
  );

  const onChangeContexts = useCallback(
    (option: SingleValue<ValueOption<"auto" | "gbvm-16">>) => {
      if (!option) {
        return;
      }
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "vm_context_policy",
          value: option.value === "auto" ? undefined : option.value,
        }),
      );
    },
    [dispatch],
  );

  const onChangeLongPress = useCallback(
    (option: SingleValue<ValueOption<"select" | "start">>) => {
      if (!option) {
        return;
      }
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "option_long_press",
          value: option.value === "select" ? undefined : option.value,
        }),
      );
    },
    [dispatch],
  );

  const onChangeFrameRate = useCallback(
    (option: SingleValue<ValueOption<60 | 30>>) => {
      if (!option) {
        return;
      }
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "frame_rate",
          value: option.value === 60 ? undefined : option.value,
        }),
      );
    },
    [dispatch],
  );

  const onChangeActorCapacity = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = Math.round(Number(e.currentTarget.value));
      if (Number.isNaN(value)) {
        return;
      }
      const capacity = Math.min(
        NGPC_ACTOR_CAPACITY_MAX,
        Math.max(NGPC_ACTOR_CAPACITY_MIN, value),
      );
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "actor_capacity",
          value: capacity === NGPC_ACTOR_CAPACITY_MIN ? undefined : capacity,
        }),
      );
    },
    [dispatch],
  );

  const onChangeProjectileCapacity = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = Math.round(Number(e.currentTarget.value));
      if (Number.isNaN(value)) {
        return;
      }
      const capacity = Math.min(
        NGPC_PROJECTILE_CAPACITY_MAX,
        Math.max(NGPC_PROJECTILE_CAPACITY_MIN, value),
      );
      dispatch(
        ngpcraftActions.setProjectSetting({
          key: "projectile_capacity",
          value:
            capacity === NGPC_PROJECTILE_CAPACITY_MIN ? undefined : capacity,
        }),
      );
    },
    [dispatch],
  );

  return (
    <SearchableCard
      searchTerm={searchTerm}
      searchMatches={[
        l10n("SETTINGS_NGPC"),
        l10n("FIELD_NGPC_OPTION_LONG_PRESS"),
        l10n("FIELD_NGPC_ACTOR_CAPACITY"),
        l10n("FIELD_NGPC_PROJECTILE_CAPACITY"),
        l10n("FIELD_NGPC_DEFAULT_COLOR"),
        l10n("FIELD_NGPC_FRAME_RATE"),
        l10n("FIELD_NGPC_VM_CONTEXTS"),
      ]}
    >
      <CardAnchor id="settingsNgpc" />
      <CardHeading>{l10n("SETTINGS_NGPC")}</CardHeading>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_OPTION_LONG_PRESS")]}
      >
        <SettingRowLabel>
          {l10n("FIELD_NGPC_OPTION_LONG_PRESS")}
        </SettingRowLabel>
        <SettingRowInput>
          <Select
            value={longPressOptions.find((o) => o.value === optionLongPress)}
            options={longPressOptions}
            onChange={onChangeLongPress}
          />
          <FormInfo>{l10n("FIELD_NGPC_OPTION_LONG_PRESS_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_DEFAULT_COLOR")]}
      >
        <SettingRowLabel>{l10n("FIELD_NGPC_DEFAULT_COLOR")}</SettingRowLabel>
        <SettingRowInput>
          <Select
            value={colorOptions.find((o) => o.value === defaultColor)}
            options={colorOptions}
            onChange={onChangeDefaultColor}
          />
          <FormInfo>{l10n("FIELD_NGPC_DEFAULT_COLOR_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_ACTOR_CAPACITY")]}
      >
        <SettingRowLabel>{l10n("FIELD_NGPC_ACTOR_CAPACITY")}</SettingRowLabel>
        <SettingRowInput>
          <NumberField
            name="ngpcActorCapacity"
            value={actorCapacity}
            min={NGPC_ACTOR_CAPACITY_MIN}
            max={NGPC_ACTOR_CAPACITY_MAX}
            onChange={onChangeActorCapacity}
          />
          <FormInfo>{l10n("FIELD_NGPC_ACTOR_CAPACITY_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_PROJECTILE_CAPACITY")]}
      >
        <SettingRowLabel>
          {l10n("FIELD_NGPC_PROJECTILE_CAPACITY")}
        </SettingRowLabel>
        <SettingRowInput>
          <NumberField
            name="ngpcProjectileCapacity"
            value={projectileCapacity}
            min={NGPC_PROJECTILE_CAPACITY_MIN}
            max={NGPC_PROJECTILE_CAPACITY_MAX}
            onChange={onChangeProjectileCapacity}
          />
          <FormInfo>{l10n("FIELD_NGPC_PROJECTILE_CAPACITY_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_FRAME_RATE")]}
      >
        <SettingRowLabel>{l10n("FIELD_NGPC_FRAME_RATE")}</SettingRowLabel>
        <SettingRowInput>
          <Select
            value={frameRateOptions.find((o) => o.value === frameRate)}
            options={frameRateOptions}
            onChange={onChangeFrameRate}
          />
          <FormInfo>{l10n("FIELD_NGPC_FRAME_RATE_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <SearchableSettingRow
        searchTerm={searchTerm}
        searchMatches={[l10n("FIELD_NGPC_VM_CONTEXTS")]}
      >
        <SettingRowLabel>{l10n("FIELD_NGPC_VM_CONTEXTS")}</SettingRowLabel>
        <SettingRowInput>
          <Select
            value={contextOptions.find((o) => o.value === contextPolicy)}
            options={contextOptions}
            onChange={onChangeContexts}
          />
          <FormInfo>{l10n("FIELD_NGPC_VM_CONTEXTS_INFO")}</FormInfo>
        </SettingRowInput>
      </SearchableSettingRow>

      <FormInfo>{l10n("FIELD_NGPC_SETTINGS_FILE_INFO")}</FormInfo>
    </SearchableCard>
  );
};
