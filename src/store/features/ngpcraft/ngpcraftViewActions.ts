/*
 * NgpCraft: view actions shared by the project and ngpcraft slices, in a
 * module of their own (projectActions and ngpcraftState import each other
 * otherwise). "ngpcraftView/" actions do not mark the project modified.
 */
import { createAction } from "@reduxjs/toolkit";

// After a save that wrote ngpcraft.json without the settings of deleted
// resources (projectActions.saveProject): the same entries leave the state.
export const dropOrphanResources = createAction<string[]>(
  "ngpcraftView/dropOrphanResources",
);
