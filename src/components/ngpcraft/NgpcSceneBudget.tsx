/*
 * NgpCraft: budget of a scene at the last build (tiles, background palettes,
 * actors, V3 warnings), in the scene sidebar. The V3 computes it while
 * building, so nothing is shown before the first build of the session.
 */
import React from "react";
import styled from "styled-components";
import l10n from "shared/lib/lang/l10n";
import { useAppSelector } from "store/hooks";
import {
  NGPC_BACKGROUND_PALETTES,
  NGPC_TILE_SLOTS,
} from "shared/lib/ngpcraft/sceneBudget";

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  font-size: 11px;
`;

const Title = styled.div`
  font-weight: bold;
`;

const Line = styled.div`
  display: flex;
  justify-content: space-between;
  gap: 8px;
`;

const Bar = styled.div`
  height: 4px;
  background: ${(props) => props.theme.colors.input.border};
`;

const BarFill = styled.div<{ $ratio: number }>`
  height: 100%;
  width: ${(props) => Math.min(100, props.$ratio * 100)}%;
  background: ${(props) =>
    props.$ratio > 1
      ? "#e57373"
      : props.$ratio > 0.9
        ? "#ffb74d"
        : props.theme.colors.highlight};
`;

const Muted = styled.div`
  opacity: 0.7;
`;

const Warning = styled.div`
  color: #ffb74d;
`;

// The 144 px warning is already shown by the editor (background field).
const isBandWarning = (warning: string) => warning.includes("(ECARTS 1)");

interface NgpcSceneBudgetProps {
  sceneId: string;
}

export const NgpcSceneBudget = ({ sceneId }: NgpcSceneBudgetProps) => {
  const budget = useAppSelector(
    (state) => state.ngpcraft.sceneBudgets[sceneId],
  );
  const anyBudget = useAppSelector(
    (state) => Object.keys(state.ngpcraft.sceneBudgets).length > 0,
  );
  if (!budget) {
    return (
      <Wrapper>
        <Title>{l10n("FIELD_NGPC_BUDGET")}</Title>
        <Muted>
          {anyBudget
            ? l10n("FIELD_NGPC_BUDGET_NOT_BUILT")
            : l10n("FIELD_NGPC_BUDGET_BUILD_FIRST")}
        </Muted>
      </Wrapper>
    );
  }
  const warnings = budget.warnings.filter((w) => !isBandWarning(w));
  return (
    <Wrapper>
      <Title>{l10n("FIELD_NGPC_BUDGET")}</Title>
      <Line>
        <span>{l10n("FIELD_NGPC_BUDGET_TILES")}</span>
        <span>
          {budget.usedTiles}/{NGPC_TILE_SLOTS}
        </span>
      </Line>
      <Bar>
        <BarFill $ratio={budget.usedTiles / NGPC_TILE_SLOTS} />
      </Bar>
      <Muted>
        {l10n("FIELD_NGPC_BUDGET_TILES_DETAIL", {
          background: budget.backgroundTiles,
          sprites: budget.spriteTiles,
        })}
      </Muted>
      <Line>
        <span>{l10n("FIELD_NGPC_BUDGET_PALETTES")}</span>
        <span>
          {budget.backgroundPalettes}/{NGPC_BACKGROUND_PALETTES}
        </span>
      </Line>
      <Line>
        <span>{l10n("FIELD_NGPC_BUDGET_ACTORS")}</span>
        <span>{budget.actors}</span>
      </Line>
      {budget.projectiles && (
        <Line>
          <span>{l10n("FIELD_NGPC_BUDGET_PROJECTILES")}</span>
          <span>
            {l10n("FIELD_NGPC_BUDGET_PROJECTILES_DETAIL", {
              capacity: budget.projectiles.capacity,
              bytes: budget.projectiles.ramBytes,
            })}
          </span>
        </Line>
      )}
      {warnings.map((warning) => (
        <Warning key={warning}>{warning}</Warning>
      ))}
      <Muted>{l10n("FIELD_NGPC_BUDGET_LAST_BUILD")}</Muted>
    </Wrapper>
  );
};
