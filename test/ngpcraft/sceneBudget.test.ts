/*
 * NgpCraft: the per-scene budget of the last build is view data. It is kept
 * by scene id and does not mark the project modified.
 */
import ngpcraftReducer, {
  initialState,
  setSceneBudgets,
} from "store/features/ngpcraft/ngpcraftState";
import documentReducer, {
  initialState as documentInitialState,
} from "store/features/document/documentState";
import type { NgpcSceneBudget } from "shared/lib/ngpcraft/sceneBudget";

const budget = (id: string): NgpcSceneBudget => ({
  id,
  name: id,
  usedTiles: 100,
  backgroundTiles: 60,
  spriteTiles: 30,
  backgroundPalettes: 2,
  actors: 3,
  viewHeight: 152,
  warnings: [],
});

test("budgets are stored by scene id and replace the previous build", () => {
  const first = ngpcraftReducer(
    initialState,
    setSceneBudgets([budget("a"), budget("b")]),
  );
  expect(Object.keys(first.sceneBudgets)).toEqual(["a", "b"]);
  const second = ngpcraftReducer(first, setSceneBudgets([budget("b")]));
  expect(Object.keys(second.sceneBudgets)).toEqual(["b"]);
});

test("a build budget does not mark the project modified", () => {
  const state = documentReducer(
    documentInitialState,
    setSceneBudgets([budget("a")]),
  );
  expect(state.modified).toBe(false);
});
