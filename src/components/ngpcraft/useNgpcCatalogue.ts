/*
 * NgpCraft: the V3 event catalogue in the renderer, loaded once per window.
 * undefined while loading, or when the V3 is not found (nothing is filtered).
 */
import { useEffect, useState } from "react";
import API from "renderer/lib/api";
import type { NgpcCatalogue } from "shared/lib/ngpcraft/catalogue";

let cataloguePromise: Promise<NgpcCatalogue | undefined> | undefined;

const loadOnce = () => {
  if (!cataloguePromise) {
    cataloguePromise = API.ngpcraft.getCatalogue().catch(() => undefined);
  }
  return cataloguePromise;
};

export const useNgpcCatalogue = (): NgpcCatalogue | undefined => {
  const [catalogue, setCatalogue] = useState<NgpcCatalogue | undefined>();
  useEffect(() => {
    let mounted = true;
    loadOnce().then((value) => {
      if (mounted) {
        setCatalogue(value);
      }
    });
    return () => {
      mounted = false;
    };
  }, []);
  return catalogue;
};
