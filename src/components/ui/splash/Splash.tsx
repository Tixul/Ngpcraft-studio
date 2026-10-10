import React, { useState } from "react";
import styled from "styled-components";
import { Button } from "ui/buttons/Button";
import {
  StyledSplashTab,
  StyledSplashTabLink,
  StyledSplashWindow,
} from "ui/splash/style";

declare const VERSION: string;
declare const COMMITHASH: string;

interface SplashWindowProps {
  focus: boolean;
  children: React.ReactNode;
}

export const SplashWindow = ({ focus, children }: SplashWindowProps) => {
  return <StyledSplashWindow $focus={focus} children={children} />;
};

export const SplashSidebar = styled.div`
  display: flex;
  flex-direction: column;
  background: ${(props) => props.theme.colors.sidebar.background};
  width: 200px;
  height: 100%;
  flex-shrink: 0;
  -webkit-app-region: drag;
  box-sizing: border-box;
  box-shadow: 0px 0px 20px rgba(0, 0, 0, 0.05) inset;
`;

export const SplashContent = styled.div`
  display: flex;
  flex-direction: column;
  background: ${(props) => props.theme.colors.background};
  color: ${(props) => props.theme.colors.text};
  padding: 20px;
  flex-grow: 1;
  -webkit-app-region: drag;
  input,
  select,
  button {
    -webkit-app-region: no-drag;
  }
`;

export const SplashForm = styled.form`
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
`;

export const SplashLogo = styled.div`
  position: relative;
  margin: 35px 20px 5px;
  transition: transform 0.2s ease-in-out;

  img {
    width: 100%;
  }

  &:hover {
    transform: scale(1.05);
  }
`;

export const SplashEasterEggButton = styled.button`
  position: absolute;
  left: 18px;
  top: 52px;
  width: 20px;
  height: 20px;
  border-radius: 20px;
  background-color: transparent;
  border: 0;
  -webkit-app-region: no-drag;
  cursor: pointer;

  &:hover {
    background: radial-gradient(
      circle,
      rgba(251, 63, 139, 0.2) 0%,
      rgba(252, 70, 107, 0) 100%
    );
  }

  &:active {
    background: radial-gradient(
      circle,
      rgba(251, 63, 139, 0.6) 0%,
      rgba(252, 70, 107, 0) 100%
    );
  }
`;

export const SplashAppTitleWrapper = styled.div`
  color: ${(props) => props.theme.colors.secondaryText};
  font-size: 11px;
  text-align: center;
  margin-bottom: 20px;
  div {
    user-select: text;
  }
`;

export const SplashAppTitle = ({ appName }: { appName?: string }) => {
  const [showCommit, setShowCommit] = useState(false);
  const displayCommit = () => setShowCommit(true);
  return (
    <SplashAppTitleWrapper onClick={displayCommit}>
      {showCommit ? (
        <div>
          {VERSION} ({COMMITHASH})
        </div>
      ) : (
        `${appName ?? "NgpCraft Studio"} ${VERSION}`
      )}
    </SplashAppTitleWrapper>
  );
};

interface SplashTabProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean;
}

export const SplashTab = ({ selected, ...props }: SplashTabProps) => (
  <StyledSplashTab $selected={selected} {...props} />
);

export const SplashTabLink = (
  props: React.AnchorHTMLAttributes<HTMLAnchorElement>,
) => <StyledSplashTabLink {...props} />;

export const SplashOpenButton = styled(Button).attrs(() => ({
  variant: "transparent",
}))`
  color: ${(props) => props.theme.colors.text};
  font-size: 13px;
  justify-content: flex-start;
  padding: 5px;
  margin: 15px;
  -webkit-app-region: no-drag;
`;

export const SplashCreateButton = styled.div`
  padding: 0px 10px;
`;

export const SplashScroll = styled.div`
  width: 100%;
  height: 100%;
  overflow: auto;
  box-sizing: border-box;
  background: ${(props) => props.theme.colors.background};
  color: ${(props) => props.theme.colors.text};
  position: relative;

  h2 {
    margin-top: 0;
  }
`;

export const SplashInfoMessage = styled.div`
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  justify-content: center;
  align-items: center;
  font-size: 13px;
  box-sizing: border-box;
  padding: 30px;
  text-align: center;
`;

export const SplashLoading = styled.form`
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  align-items: center;
  justify-content: center;
`;
