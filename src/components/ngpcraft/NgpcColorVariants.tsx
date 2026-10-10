/*
 * NgpCraft: colour variants of a background in "NGPC native" colours
 * (ngpcraft.json resources.<id>.ngpc_color_variants, V3 native_variants.py).
 * A variant is another background of the project with the same size, the
 * same drawing recoloured; the event "NGPC: Background Colour Variant" shows
 * variant k (0 = as drawn). The V3 checks at build that it only recolours.
 */
import React, { useCallback, useMemo } from "react";
import styled from "styled-components";
import { SingleValue } from "react-select";
import l10n from "shared/lib/lang/l10n";
import { useAppDispatch, useAppSelector } from "store/hooks";
import { actions as ngpcraftActions } from "store/features/ngpcraft/ngpcraftState";
import { backgroundSelectors } from "store/features/entities/entitiesSelectors";
import { getNgpcResourceSetting } from "shared/lib/ngpcraft/projectExtension";
import { assetPath } from "shared/lib/helpers/assets";
import { Input } from "ui/form/Input";
import { Select } from "ui/form/Select";
import { Button } from "ui/buttons/Button";

export const NGPC_COLOR_VARIANTS_KEY = "ngpc_color_variants";
// V3 native_variants.MAX_VARIANTS (+ the background as drawn = 16 colour sets).
export const NGPC_COLOR_VARIANTS_MAX = 15;

export interface NgpcColorVariant {
  name: string;
  image: string;
}

const Wrapper = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
`;

const Row = styled.div`
  display: flex;
  align-items: center;
  gap: 4px;
  > :nth-child(2) {
    flex: 1 1 40%;
  }
  > :nth-child(3) {
    flex: 1 1 60%;
  }
`;

const Index = styled.span`
  width: 14px;
  text-align: right;
  opacity: 0.7;
`;

const Muted = styled.div`
  opacity: 0.7;
`;

const isVariant = (value: unknown): value is NgpcColorVariant =>
  typeof value === "object" &&
  value !== null &&
  typeof (value as NgpcColorVariant).name === "string" &&
  typeof (value as NgpcColorVariant).image === "string";

interface ImageOption {
  value: string;
  label: string;
}

interface NgpcColorVariantsProps {
  backgroundId: string;
}

export const NgpcColorVariants = ({ backgroundId }: NgpcColorVariantsProps) => {
  const dispatch = useAppDispatch();
  const stored = useAppSelector((state) =>
    getNgpcResourceSetting(
      state.ngpcraft.extension,
      backgroundId,
      NGPC_COLOR_VARIANTS_KEY,
    ),
  );
  const background = useAppSelector((state) =>
    backgroundSelectors.selectById(state, backgroundId),
  );
  const backgrounds = useAppSelector(backgroundSelectors.selectAll);

  const variants: NgpcColorVariant[] = useMemo(
    () => (Array.isArray(stored) ? stored.filter(isVariant) : []),
    [stored],
  );

  // Same image size as the background; the background itself excluded.
  const options: ImageOption[] = useMemo(
    () =>
      background
        ? backgrounds
            .filter(
              (b) =>
                b.id !== background.id &&
                b.imageWidth === background.imageWidth &&
                b.imageHeight === background.imageHeight,
            )
            .map((b) => ({ value: assetPath("backgrounds", b), label: b.name }))
        : [],
    [background, backgrounds],
  );

  const save = useCallback(
    (next: NgpcColorVariant[]) => {
      dispatch(
        ngpcraftActions.setResourceSetting({
          resourceId: backgroundId,
          key: NGPC_COLOR_VARIANTS_KEY,
          value: next.length > 0 ? next : undefined,
        }),
      );
    },
    [dispatch, backgroundId],
  );

  const onAdd = useCallback(() => {
    if (options.length === 0 || variants.length >= NGPC_COLOR_VARIANTS_MAX) {
      return;
    }
    const used = new Set(variants.map((v) => v.name));
    let n = variants.length + 1;
    while (used.has(`${l10n("FIELD_NGPC_COLOR_VARIANT")} ${n}`)) {
      n++;
    }
    save([
      ...variants,
      {
        name: `${l10n("FIELD_NGPC_COLOR_VARIANT")} ${n}`,
        image: options[0].value,
      },
    ]);
  }, [options, variants, save]);

  if (!background) {
    return null;
  }

  return (
    <Wrapper>
      <div>{l10n("FIELD_NGPC_COLOR_VARIANTS")}</div>
      <Row>
        <Index>0</Index>
        <Muted>{l10n("FIELD_NGPC_COLOR_VARIANT_ORIGINAL")}</Muted>
      </Row>
      {variants.map((variant, index) => (
        <Row key={index}>
          <Index>{index + 1}</Index>
          <Input
            value={variant.name}
            onChange={(e) =>
              save(
                variants.map((v, i) =>
                  i === index ? { ...v, name: e.currentTarget.value } : v,
                ),
              )
            }
          />
          <Select
            value={
              options.find((o) => o.value === variant.image) ?? {
                value: variant.image,
                label: variant.image,
              }
            }
            options={options}
            onChange={(option: SingleValue<ImageOption>) => {
              if (option) {
                save(
                  variants.map((v, i) =>
                    i === index ? { ...v, image: option.value } : v,
                  ),
                );
              }
            }}
          />
          <Button
            size="small"
            variant="transparent"
            title={l10n("FIELD_NGPC_COLOR_VARIANT_REMOVE")}
            onClick={() => save(variants.filter((_, i) => i !== index))}
          >
            ✕
          </Button>
        </Row>
      ))}
      {options.length === 0 ? (
        <Muted>{l10n("FIELD_NGPC_COLOR_VARIANTS_NONE")}</Muted>
      ) : (
        variants.length < NGPC_COLOR_VARIANTS_MAX && (
          <Button size="small" onClick={onAdd}>
            {l10n("FIELD_NGPC_COLOR_VARIANT_ADD")}
          </Button>
        )
      )}
      <Muted>{l10n("FIELD_NGPC_COLOR_VARIANTS_INFO")}</Muted>
    </Wrapper>
  );
};
