/*
 * NgpCraft: Preferences > NgpCraft. Where the ROM build tools are: the
 * Toshiba toolchain, Python, Wine (Linux / macOS) and, for development, the
 * V3 folder. An empty field uses the default, shown as placeholder.
 */
import React, { useCallback, useEffect, useState } from "react";
import styled from "styled-components";
import API from "renderer/lib/api";
import l10n from "shared/lib/lang/l10n";
import { FormRow } from "ui/form/layout/FormLayout";
import { TextField } from "ui/form/TextField";
import { Button } from "ui/buttons/Button";
import { DotsIcon } from "ui/icons/Icons";
import { NGPCRAFT_SETTINGS } from "shared/lib/ngpcraft/settingsKeys";

type ToolKey = "toolchainPath" | "pythonPath" | "winePath" | "v3Path";

const Heading = styled.div`
  font-weight: bold;
  margin-bottom: 8px;
`;

const Report = styled.pre<{ $ok: boolean }>`
  margin: 0 0 10px;
  padding: 8px;
  white-space: pre-wrap;
  font-size: 11px;
  border-radius: 4px;
  color: ${(props) => (props.$ok ? "#81c784" : "#e57373")};
  background: ${(props) => props.theme.colors.input.background};
`;

export const NgpcToolsPreferences = () => {
  const [values, setValues] = useState<Record<ToolKey, string>>({
    toolchainPath: "",
    pythonPath: "",
    winePath: "",
    v3Path: "",
  });
  const [defaults, setDefaults] = useState<Record<ToolKey, string>>({
    toolchainPath: "",
    pythonPath: "",
    winePath: "",
    v3Path: "",
  });
  const [isWindows, setIsWindows] = useState(true);
  const [report, setReport] = useState<{ ok: boolean; lines: string[] }>();

  const refreshDefaults = useCallback(async () => {
    const tools = await API.ngpcraft.toolDefaults();
    setIsWindows(tools.isWindows);
    setDefaults({
      toolchainPath: tools.toolchainPath,
      pythonPath: tools.pythonPath,
      winePath: tools.winePath,
      v3Path: tools.v3Path,
    });
  }, []);

  useEffect(() => {
    const load = async () => {
      const read = (key: ToolKey) =>
        API.settings.getString(NGPCRAFT_SETTINGS[key], "");
      setValues({
        toolchainPath: await read("toolchainPath"),
        pythonPath: await read("pythonPath"),
        winePath: await read("winePath"),
        v3Path: await read("v3Path"),
      });
      await refreshDefaults();
    };
    load();
  }, [refreshDefaults]);

  const save = useCallback(
    async (key: ToolKey, value: string) => {
      setValues((current) => ({ ...current, [key]: value }));
      if (value.trim()) {
        await API.settings.set(NGPCRAFT_SETTINGS[key], value.trim());
      } else {
        await API.settings.delete(NGPCRAFT_SETTINGS[key]);
      }
      setReport(undefined);
      await refreshDefaults();
    },
    [refreshDefaults],
  );

  const browse = useCallback(
    async (key: ToolKey, kind: "folder" | "file") => {
      const path =
        kind === "folder"
          ? await API.dialog.chooseDirectory()
          : await API.dialog.chooseFile();
      if (path) {
        await save(key, path);
      }
    },
    [save],
  );

  const onCheck = useCallback(async () => {
    setReport(await API.ngpcraft.checkTools());
  }, []);

  const field = (
    key: ToolKey,
    label: string,
    info: string,
    kind: "folder" | "file",
  ) => (
    <FormRow>
      <TextField
        name={NGPCRAFT_SETTINGS[key]}
        label={label}
        value={values[key]}
        placeholder={defaults[key]}
        onChange={(e) => save(key, e.currentTarget.value)}
        additionalRight={
          <Button onClick={() => browse(key, kind)} type="button">
            <DotsIcon />
          </Button>
        }
        info={info}
      />
    </FormRow>
  );

  return (
    <>
      <Heading>{l10n("FIELD_NGPC_TOOLS")}</Heading>
      {field(
        "toolchainPath",
        l10n("FIELD_NGPC_TOOLCHAIN_PATH"),
        l10n("FIELD_NGPC_TOOLCHAIN_PATH_INFO"),
        "folder",
      )}
      {field(
        "pythonPath",
        l10n("FIELD_NGPC_PYTHON_PATH"),
        l10n("FIELD_NGPC_PYTHON_PATH_INFO"),
        "file",
      )}
      {!isWindows &&
        field(
          "winePath",
          l10n("FIELD_NGPC_WINE_PATH"),
          l10n("FIELD_NGPC_WINE_PATH_INFO"),
          "file",
        )}
      {field(
        "v3Path",
        l10n("FIELD_NGPC_V3_PATH"),
        l10n("FIELD_NGPC_V3_PATH_INFO"),
        "folder",
      )}
      <FormRow>
        <Button onClick={onCheck}>{l10n("FIELD_NGPC_CHECK_TOOLS")}</Button>
      </FormRow>
      {report && <Report $ok={report.ok}>{report.lines.join("\n")}</Report>}
    </>
  );
};
