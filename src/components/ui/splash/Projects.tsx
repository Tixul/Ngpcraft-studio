import React from "react";
import l10n from "shared/lib/lang/l10n";
import styled from "styled-components";
import projectIcon from "ui/icons/gbsproj.png";
import { CloseIcon } from "ui/icons/Icons";

interface SplashProjectProps {
  project: {
    name: string;
    dir: string;
  };
  onClick: () => void;
  onRemove: () => void;
}

const SplashProjectRemoveButton = styled.div`
  position: absolute;
  top: 5px;
  right: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.2s ease-in-out;
  transition-delay: 0.2s;
  background: ${(props) => props.theme.colors.input.background};
  border: 0;
  border-radius: 4px;
  width: 25px;
  height: 25px;

  svg {
    fill: ${(props) => props.theme.colors.text};
    width: 10px;
    height: 10px;
    max-width: 10px;
    max-height: 10px;
  }

  &:hover {
    cursor: pointer;
    svg {
      fill: ${(props) => props.theme.colors.highlight};
    }
  }
`;

const SplashProjectWrapper = styled.button`
  position: relative;
  display: flex;
  text-align: left;
  background: ${(props) => props.theme.colors.input.background};
  color: ${(props) => props.theme.colors.text};
  border: 0;
  border-bottom: 1px solid ${(props) => props.theme.colors.input.border};
  border-radius: 0px;
  padding: 15px 30px;
  width: 100%;

  img {
    width: 42px;
    margin-right: 10px;
  }

  ${SplashProjectRemoveButton} {
    opacity: 0;
  }

  &:hover {
    background: ${(props) => props.theme.colors.input.hoverBackground};
    ${SplashProjectRemoveButton} {
      opacity: 1;
    }
  }

  &:active {
    background: ${(props) => props.theme.colors.input.activeBackground};
  }

  &:focus {
    background: transparent;
    box-shadow: inset 0 0 0px 2px ${(props) => props.theme.colors.highlight};
  }

  &:last-child {
    margin-bottom: 0;
  }
`;

const SplashProjectDetails = styled.span`
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
`;

const SplashProjectName = styled.span`
  display: block;
  font-size: 16px;
  font-weight: bold;
  margin-bottom: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`;

const SplashProjectPath = styled.span`
  display: block;
  font-size: 11px;
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`;

export const SplashProjectClearButton = styled.div`
  display: flex;
  justify-content: center;
  padding: 30px;
`;

export const SplashProject = ({
  project,
  onClick,
  onRemove,
}: SplashProjectProps) => (
  <SplashProjectWrapper onClick={onClick}>
    <img src={projectIcon} alt="" draggable={false} />
    <SplashProjectDetails>
      <SplashProjectName>{project.name}</SplashProjectName>
      <SplashProjectPath>{project.dir}</SplashProjectPath>
    </SplashProjectDetails>
    <SplashProjectRemoveButton
      title={l10n("SPLASH_REMOVE_FROM_RECENT")}
      onClick={
        onRemove
          ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              onRemove();
            }
          : undefined
      }
    >
      <CloseIcon />
    </SplashProjectRemoveButton>
  </SplashProjectWrapper>
);
