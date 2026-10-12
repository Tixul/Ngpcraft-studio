/*
 * NgpCraft: bar above a sound effect showing its NGPC version. A GB Studio
 * sound file is never played on NGPC: the V3 plays the native timelines
 * bound in ngpcraft.json (ngpc.native_sfx["<sound id>:<effect>"]) and refuses
 * a sound a script plays without one. FX Hammer banks and VGM files are
 * converted by the V3 from here (lib/ngpcraft/convertSfx); WAV effects are
 * refused by the V3 with its reason.
 */
import React, { useCallback, useState } from "react";
import styled from "styled-components";
import l10n from "shared/lib/lang/l10n";
import API from "renderer/lib/api";
import { Button } from "ui/buttons/Button";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { actions as ngpcraftActions } from "store/features/ngpcraft/ngpcraftState";
import { getNgpcSoundEffects } from "shared/lib/ngpcraft/projectExtension";
import { assetPath } from "shared/lib/helpers/assets";
import type { Sound } from "shared/lib/resources/types";

const Bar = styled.div`
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid ${(props) => props.theme.colors.sidebar.border};
  background: ${(props) => props.theme.colors.sidebar.background};
  color: ${(props) => props.theme.colors.text};
  font-size: 11px;
`;

const Label = styled.span`
  font-weight: bold;
`;

const Status = styled.span<{ $warning?: boolean }>`
  flex-grow: 1;
  color: ${(props) => (props.$warning ? "#e57373" : "inherit")};
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const Info = styled.div`
  flex-basis: 100%;
  opacity: 0.8;
`;

interface NgpcSfxBindingProps {
  sound: Sound;
}

export const NgpcSfxBinding = ({ sound }: NgpcSfxBindingProps) => {
  const dispatch = useAppDispatch();
  const extension = useAppSelector((state) => state.ngpcraft.extension);
  const bound = getNgpcSoundEffects(extension, sound.id);
  // GB Studio plays effect 0 of anything but an FX Hammer bank.
  const total =
    sound.type === "fxhammer" ? Math.max(1, sound.numEffects ?? 1) : 1;
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [converting, setConverting] = useState(false);

  const onConvert = useCallback(async () => {
    setError("");
    setInfo("");
    setConverting(true);
    const result = await API.ngpcraft
      .convertSfx(assetPath("sounds", sound), total)
      .catch((e) => ({ error: String(e) }));
    setConverting(false);
    if ("error" in result) {
      setError(l10n("FIELD_NGPC_SFX_CONVERT_FAILED", { error: result.error }));
      return;
    }
    dispatch(
      ngpcraftActions.setSoundEffects({
        soundId: sound.id,
        timelines: result.timelines,
      }),
    );
    setInfo(
      l10n("FIELD_NGPC_SFX_CONVERTED", {
        losses: result.losses.length
          ? result.losses.join("; ")
          : l10n("FIELD_NGPC_SFX_NO_LOSS"),
      }),
    );
  }, [dispatch, sound, total]);

  const onUnlink = useCallback(() => {
    setError("");
    setInfo("");
    dispatch(
      ngpcraftActions.setSoundEffects({
        soundId: sound.id,
        timelines: undefined,
      }),
    );
  }, [dispatch, sound.id]);

  // FX Hammer banks and VGM files are converted by the build (autoConvertSounds).
  const automatic =
    bound.length < total && (sound.type === "fxhammer" || sound.type === "vgm");
  const status = bound.length
    ? l10n("FIELD_NGPC_SFX_LINKED", { count: bound.length, total })
    : l10n(automatic ? "FIELD_NGPC_SFX_AUTO" : "FIELD_NGPC_SFX_UNLINKED");

  return (
    <Bar>
      <Label>{l10n("FIELD_NGPC_SFX")}</Label>
      <Status
        $warning={(bound.length < total && !automatic) || Boolean(error)}
        title={error || status}
      >
        {error || status}
      </Status>
      <Button size="small" onClick={onConvert} disabled={converting}>
        {converting
          ? l10n("FIELD_NGPC_SFX_CONVERTING")
          : bound.length
            ? l10n("FIELD_NGPC_SFX_RECONVERT")
            : l10n("FIELD_NGPC_SFX_CONVERT")}
      </Button>
      {bound.length > 0 && (
        <Button size="small" onClick={onUnlink}>
          {l10n("FIELD_NGPC_SFX_UNLINK")}
        </Button>
      )}
      {info && <Info>{info}</Info>}
    </Bar>
  );
};
