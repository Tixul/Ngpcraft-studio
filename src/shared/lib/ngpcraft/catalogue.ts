/*
 * NgpCraft: which GB Studio events the V3 compiler delivers.
 * Read from the V3 parity catalogue (ngpcraft/parity/gbstudio.json), which
 * lists the 151 GB Studio 4.3.2 events. An event missing from it comes from a
 * plugin: the V3 compiles it through GB Studio's own plugin loader, and refuses
 * it at build, located, when it calls a ScriptBuilder method the V3 does not
 * have (ngpcraft-v3/docs/M7_PLUGINS.md).
 */

export type NgpcEventStatus = "identique" | "adapte" | "refuse";

export interface NgpcCatalogueEvent {
  status: NgpcEventStatus;
  // Gap reference in the V3 ECARTS document, e.g. "§12", and its title.
  ecart?: string;
  ecartTitle?: string;
  // Free text from the catalogue: partial delivery, reason of a refusal.
  detail?: string;
}

export interface NgpcCatalogue {
  events: Record<string, NgpcCatalogueEvent>;
}

export type NgpcEventDelivery = "delivered" | "adapted" | "refused" | "plugin";

// Events of NgpCraft Studio itself (src/lib/events/eventNgpc*.js), not in GB
// Studio: the V3 compiles them natively (ngpcraft-v3/ngpcraft/parity NGPC_EVENTS).
export const NGPC_EVENTS: ReadonlySet<string> = new Set([
  "EVENT_NGPC_BACKGROUND_VARIANT",
  // ngpcraft-v3/docs/M8_NGPC_SYSTEM.md
  "EVENT_NGPC_CLOCK_GET",
  "EVENT_NGPC_IF_LANGUAGE",
  "EVENT_NGPC_BATTERY_GET",
  "EVENT_NGPC_SHUTDOWN",
]);

export const ngpcEventDelivery = (
  catalogue: NgpcCatalogue,
  eventId: string,
): NgpcEventDelivery => {
  if (NGPC_EVENTS.has(eventId)) {
    return "delivered";
  }
  const entry = catalogue.events[eventId];
  if (!entry) {
    return "plugin";
  }
  if (entry.status === "refuse") {
    return "refused";
  }
  return entry.status === "adapte" ? "adapted" : "delivered";
};

export const isNgpcEventBuildable = (
  catalogue: NgpcCatalogue,
  eventId: string,
) => {
  const delivery = ngpcEventDelivery(catalogue, eventId);
  return delivery !== "refused";
};

// One line explaining the NGPC status, for tooltips and badges.
export const ngpcEventNote = (
  catalogue: NgpcCatalogue,
  eventId: string,
): string => {
  if (NGPC_EVENTS.has(eventId)) {
    return "NGPC event (NgpCraft Studio only)";
  }
  const entry = catalogue.events[eventId];
  if (!entry) {
    return "NGPC: plugin event, compiled by the NgpCraft V3 when it only uses ScriptBuilder methods the V3 has (checked at build)";
  }
  const gap = entry.ecart
    ? ` (${entry.ecart}${entry.ecartTitle ? ` ${entry.ecartTitle}` : ""})`
    : "";
  if (entry.status === "refuse") {
    return `NGPC: refused${gap}${entry.detail ? `: ${entry.detail}` : ""}`;
  }
  if (entry.status === "adapte") {
    return `NGPC: adapted${gap}${entry.detail ? `: ${entry.detail}` : ""}`;
  }
  return entry.detail ? `NGPC: ${entry.detail}` : "";
};
