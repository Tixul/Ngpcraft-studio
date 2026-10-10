/* eslint-disable camelcase */ // Keys of the V3 file format.
/*
 * NgpCraft: settings left in ngpcraft.json by a deleted or recreated scene or
 * asset are not sent to the V3 (it refuses unknown ids) and are reported.
 * Case met by Wilfried: a second background set on the template's scene,
 * the scene replaced by new ones.
 */
import {
  projectResourceIds,
  withoutOrphanResources,
} from "shared/lib/ngpcraft/projectExtension";
import ngpcraftReducer, {
  initialState,
} from "store/features/ngpcraft/ngpcraftState";
import { dropOrphanResources } from "store/features/ngpcraft/ngpcraftViewActions";
import documentReducer, {
  initialState as documentInitialState,
} from "store/features/document/documentState";

test("entries of missing resources are removed and listed", () => {
  const extension = {
    schema: "ngpcraft-project/1",
    ngpc: { default_color: "native" },
    resources: {
      gone: { second_background: { background_id: "bg" } },
      kept: { ngpc_color: "gbs" },
    },
  };
  const { extension: sent, removed } = withoutOrphanResources(
    extension,
    new Set(["kept", "bg"]),
  );
  expect(removed).toEqual(["gone"]);
  expect(sent.resources).toEqual({ kept: { ngpc_color: "gbs" } });
  expect(sent.ngpc).toEqual({ default_color: "native" });
  expect(extension.resources).toHaveProperty("gone"); // input untouched
});

test("nothing to remove keeps the same object", () => {
  const extension = { schema: "ngpcraft-project/1", resources: { a: {} } };
  const result = withoutOrphanResources(extension, new Set(["a"]));
  expect(result.removed).toEqual([]);
  expect(result.extension).toBe(extension);
});

test("project ids include scene actors and triggers", () => {
  const ids = projectResourceIds({
    scenes: [{ id: "s", actors: [{ id: "a" }], triggers: [{ id: "t" }] }],
    backgrounds: [{ id: "b" }],
  } as unknown as Parameters<typeof projectResourceIds>[0]);
  expect([...ids].sort()).toEqual(["a", "b", "s", "t"]);
});

test("after a save, orphans leave the state without marking the project modified", () => {
  const state = ngpcraftReducer(
    {
      ...initialState,
      extension: {
        schema: "ngpcraft-project/1",
        resources: { gone: { ngpc_color: "native" }, kept: {} },
      },
    },
    dropOrphanResources(["gone"]),
  );
  expect(state.extension?.resources).toEqual({ kept: {} });
  const document = documentReducer(
    documentInitialState,
    dropOrphanResources(["gone"]),
  );
  expect(document.modified).toBe(false);
});
