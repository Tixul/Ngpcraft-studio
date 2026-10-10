/*
 * NgpCraft: badge on a script event the V3 will not build (refused), shown
 * where the event is, with the reason as tooltip.
 */
import React from "react";
import styled from "styled-components";
import { useNgpcCatalogue } from "components/ngpcraft/useNgpcCatalogue";
import {
  isNgpcEventBuildable,
  ngpcEventNote,
} from "shared/lib/ngpcraft/catalogue";

const Badge = styled.span`
  flex-shrink: 0;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 3px;
  background: #c62828;
  color: #ffffff;
  font-size: 10px;
  font-weight: bold;
  line-height: 16px;
`;

interface NgpcEventBadgeProps {
  command: string;
}

export const NgpcEventBadge = ({ command }: NgpcEventBadgeProps) => {
  const catalogue = useNgpcCatalogue();
  if (!catalogue || isNgpcEventBuildable(catalogue, command)) {
    return null;
  }
  return <Badge title={ngpcEventNote(catalogue, command)}>NGPC ✕</Badge>;
};
