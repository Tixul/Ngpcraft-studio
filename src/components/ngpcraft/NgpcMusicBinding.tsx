/*
 * NgpCraft: bar above a song editor showing its NGPC version. The GB Studio
 * song (.uge, .mod) is never played on NGPC: the V3 plays the native song
 * linked in ngpcraft.json (resources.<id>.native_music), and refuses a song
 * a script plays without one. A .uge or .mod song can be converted by the
 * V3 from here (lib/ngpcraft/convertUge; a .mod through GB Studio's MOD
 * import first): the result goes to native-audio/<song>/, with the V3 loss
 * report, and is linked to that song.
 */
import React, { useCallback, useState } from "react";
import styled from "styled-components";
import l10n from "shared/lib/lang/l10n";
import API from "renderer/lib/api";
import { Button } from "ui/buttons/Button";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { actions as ngpcraftActions } from "store/features/ngpcraft/ngpcraftState";
import {
  getNgpcResourceSetting,
  NgpcNativeMusic,
} from "shared/lib/ngpcraft/projectExtension";
import { musicSelectors } from "store/features/entities/entitiesSelectors";
import { assetPath } from "shared/lib/helpers/assets";
import buildGameActions from "store/features/buildGame/buildGameActions";

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

// Octaves added by the conversion, e.g. "channel 3: +1 octave".
const transposeText = (semitones: number[]): string =>
  semitones
    .map((value, index) =>
      value > 0
        ? l10n("FIELD_NGPC_MUSIC_TRANSPOSED", {
            channel: index + 1,
            octaves: value / 12,
          })
        : "",
    )
    .filter(Boolean)
    .join(", ");

const isNativeMusic = (value: unknown): value is NgpcNativeMusic =>
  typeof value === "object" &&
  value !== null &&
  "format" in value &&
  ((value as { format: unknown }).format === "ngps" ||
    (value as { format: unknown }).format === "held-score");

interface NgpcMusicBindingProps {
  songId: string;
}

export const NgpcMusicBinding = ({ songId }: NgpcMusicBindingProps) => {
  const dispatch = useAppDispatch();
  const extension = useAppSelector((state) => state.ngpcraft.extension);
  const binding = getNgpcResourceSetting(extension, songId, "native_music");
  const native = isNativeMusic(binding) ? binding : undefined;
  const song = useAppSelector((state) =>
    musicSelectors.selectById(state, songId),
  );
  const songModified = useAppSelector((state) => state.tracker.modified);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [converting, setConverting] = useState(false);

  const onConvert = useCallback(async () => {
    if (!song) {
      return;
    }
    setError("");
    setInfo("");
    if (songModified) {
      setError(l10n("FIELD_NGPC_MUSIC_SAVE_FIRST"));
      return;
    }
    setConverting(true);
    const result = await API.ngpcraft
      .convertUge(
        assetPath("music", song),
        song.type === "mod" ? !song.settings?.disableSpeedConversion : undefined,
      )
      .catch((e) => ({ error: String(e) }));
    setConverting(false);
    if ("error" in result) {
      setError(
        l10n("FIELD_NGPC_MUSIC_CONVERT_FAILED", { error: result.error }),
      );
      return;
    }
    dispatch(
      ngpcraftActions.setResourceSetting({
        resourceId: songId,
        key: "native_music",
        value: result.binding,
      }),
    );
    const transposed = transposeText(result.transposeSemitones);
    setInfo(
      l10n("FIELD_NGPC_MUSIC_CONVERTED", { folder: result.folder }) +
        (transposed ? ` ${transposed}.` : ""),
    );
  }, [dispatch, song, songId, songModified]);

  const onLink = useCallback(async () => {
    setError("");
    setInfo("");
    const result = await API.ngpcraft.chooseNativeSong();
    if ("error" in result) {
      setError(result.error);
    } else if ("binding" in result) {
      dispatch(
        ngpcraftActions.setResourceSetting({
          resourceId: songId,
          key: "native_music",
          value: result.binding,
        }),
      );
    }
  }, [dispatch, songId]);

  const onUnlink = useCallback(() => {
    setError("");
    setInfo("");
    dispatch(
      ngpcraftActions.setResourceSetting({
        resourceId: songId,
        key: "native_music",
        value: undefined,
      }),
    );
  }, [dispatch, songId]);

  // A ROM that only plays this song, in the Play window (real driver and PSG).
  const onListen = useCallback(() => {
    setError("");
    dispatch(
      buildGameActions.buildGame({
        buildType: "web",
        ngpcMusicPreviewId: songId,
      }),
    );
  }, [dispatch, songId]);

  const onEdit = useCallback(async () => {
    if (native?.format !== "ngps") {
      return;
    }
    setError((await API.ngpcraft.openSoundCreator(native.song)) ?? "");
  }, [native]);

  let status = l10n("FIELD_NGPC_MUSIC_UNLINKED");
  if (native?.format === "ngps") {
    status = l10n("FIELD_NGPC_MUSIC_LINKED_NGPS", {
      song: native.song,
      bank: native.bank,
    });
  } else if (native?.format === "held-score") {
    status = l10n("FIELD_NGPC_MUSIC_LINKED_HELD_SCORE", {
      score: native.score,
    });
  }

  return (
    <Bar>
      <Label>{l10n("FIELD_NGPC_MUSIC")}</Label>
      <Status $warning={!native || Boolean(error)} title={error || status}>
        {error || status}
      </Status>
      {(song?.type === "uge" || song?.type === "mod") && (
        <Button size="small" onClick={onConvert} disabled={converting}>
          {converting
            ? l10n("FIELD_NGPC_MUSIC_CONVERTING")
            : native?.format === "held-score"
              ? l10n("FIELD_NGPC_MUSIC_RECONVERT")
              : l10n("FIELD_NGPC_MUSIC_CONVERT")}
        </Button>
      )}
      {native && (
        <Button size="small" onClick={onListen}>
          {l10n("FIELD_NGPC_MUSIC_LISTEN")}
        </Button>
      )}
      {native?.format === "ngps" && (
        <Button size="small" onClick={onEdit}>
          {l10n("FIELD_NGPC_MUSIC_EDIT")}
        </Button>
      )}
      <Button size="small" onClick={onLink}>
        {native
          ? l10n("FIELD_NGPC_MUSIC_CHANGE")
          : l10n("FIELD_NGPC_MUSIC_LINK")}
      </Button>
      {native && (
        <Button size="small" onClick={onUnlink}>
          {l10n("FIELD_NGPC_MUSIC_UNLINK")}
        </Button>
      )}
      {info && <Info>{info}</Info>}
    </Bar>
  );
};
