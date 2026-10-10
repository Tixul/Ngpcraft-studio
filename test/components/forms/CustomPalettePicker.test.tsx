/**
 * @jest-environment jsdom
 */

import React from "react";
import { UnknownAction, Store } from "@reduxjs/toolkit";
import entitiesReducer, {
  initialState as entitiesInitialState,
} from "store/features/entities/entitiesState";
import entitiesActions from "store/features/entities/entitiesActions";
import { initialState as settingsInitialState } from "store/features/settings/settingsState";
import { RootState } from "store/storeTypes";
import CustomPalettePicker from "components/forms/CustomPalettePicker";
import { render, waitFor, act, fireEvent } from "../../react-utils";
import { dummyPalette } from "../../dummydata";

type TestStore = Store<RootState, UnknownAction> & {
  setState: (state: RootState) => void;
};

const makeState = (selectedColor: string) =>
  ({
    project: {
      present: {
        entities: {
          ...entitiesInitialState,
          palettes: {
            entities: {
              palette1: {
                ...dummyPalette,
                id: "palette1",
                colors: [
                  selectedColor,
                  dummyPalette.colors[1],
                  dummyPalette.colors[2],
                  dummyPalette.colors[3],
                ],
              },
            },
            ids: ["palette1"],
          },
        },
        settings: {
          ...settingsInitialState,
          colorCorrection: "none",
        },
      },
    },
  }) as unknown as RootState;

const makeStore = (initialState: RootState): TestStore => {
  let state = initialState;
  const listeners = new Set<() => void>();

  return {
    getState: () => state,
    dispatch: (action: UnknownAction) => {
      if (action.type === entitiesActions.editPaletteColor.type) {
        state = {
          ...state,
          project: {
            ...state.project,
            present: {
              ...state.project.present,
              entities: entitiesReducer(
                state.project.present.entities,
                action as ReturnType<typeof entitiesActions.editPaletteColor>,
              ),
            },
          },
        };
        listeners.forEach((listener) => listener());
      }
      return action;
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    replaceReducer: () => undefined,
    [Symbol.observable]: () => ({
      subscribe: () => ({
        unsubscribe: () => undefined,
      }),
      [Symbol.observable]() {
        return this;
      },
    }),
    setState: (nextState: RootState) => {
      state = nextState;
      listeners.forEach((listener) => listener());
    },
  } as TestStore;
};

test("Should resync slider and hex state when palette color changes without palette id changing", async () => {
  // NgpCraft: NGPC channels are 4 bits (0-15); 111111 is channel value 1.
  const store = makeStore(makeState("111111"));

  render(<CustomPalettePicker paletteId="palette1" />, store, {});

  const colorHexInput = document.getElementById("colorHex") as HTMLInputElement;
  const colorRInput = document.getElementById("colorR") as HTMLInputElement;

  await waitFor(() => expect(colorHexInput.value).toBe("#111111"));
  expect(colorRInput.value).toBe("1");

  act(() => {
    store.setState(makeState("ffffff"));
  });

  await waitFor(() => expect(colorHexInput.value).toBe("#ffffff"));
  expect(colorRInput.value).toBe("15");
});

test("Should keep local saturation value after updating selected color through the store", async () => {
  const store = makeStore(makeState("ff0000"));

  render(<CustomPalettePicker paletteId="palette1" />, store, {});

  const colorSaturationInput = document.getElementById(
    "colorSaturation",
  ) as HTMLInputElement;

  await waitFor(() => expect(colorSaturationInput.value).toBe("100"));

  act(() => {
    fireEvent.change(colorSaturationInput, {
      currentTarget: { value: "50" },
      target: { value: "50" },
    });
  });

  await waitFor(() => expect(colorSaturationInput.value).toBe("50"));
});
