import React, { useLayoutEffect, useRef, useState, FC } from "react";
import styled from "styled-components";
import type { TemplatePlugin } from "lib/templates/templateManager";
import {
  Option,
  Select,
  SelectMenu,
  selectMenuStyleProps,
} from "ui/form/Select";
import { RelativePortal } from "ui/layout/RelativePortal";
import pluginPreview from "assets/templatePreview/plugin.png";

interface Template {
  id: string;
  name: string;
  preview: string;
  videoPreview: boolean;
  description: string;
}

interface SplashTemplateSelectProps {
  templates: Template[];
  templatePlugins: TemplatePlugin[];
  name: string;
  value: string;
  disabled?: boolean;
  onChange: (newValue: string) => void;
}

const SplashTemplateSelectWrapper = styled.div`
  width: 100%;
`;

// NgpCraft: the tiles wrap instead of being cut when the row is too narrow.
const SplashTemplateSelectOptions = styled.div`
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  row-gap: 10px;
  width: 100%;
  margin-bottom: 5px;

  & > * {
    margin-right: 10px;
  }
`;

const SplashTemplateButtonWrapper = styled.div`
  position: relative;
`;

const SplashTemplateButton = styled.input.attrs({
  type: "radio",
})`
  width: 80px;
  height: 80px;
  margin: 0;
  padding: 0;
  border-radius: ${(props) => props.theme.borderRadius}px;
  -webkit-appearance: none;
  &:focus {
    box-shadow: 0 0 0px 4px ${(props) => props.theme.colors.highlight};
  }
  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
`;

const SplashTemplateLabel = styled.label`
  position: absolute;
  top: 0;
  left: 0;
  width: 80px;
  height: 80px;
  background-color: #fff;
  border: 2px solid ${(props) => props.theme.colors.input.background};
  border-radius: ${(props) => props.theme.borderRadius}px;
  -webkit-appearance: none;
  box-sizing: border-box;

  img,
  video {
    width: 100%;
    height: 100%;
  }

  ${SplashTemplateButton}:checked + & {
    border: 2px solid ${(props) => props.theme.colors.highlight};
    box-shadow: 0 0 0px 2px ${(props) => props.theme.colors.highlight};
  }

  ${SplashTemplateButton}:disabled + & {
    cursor: not-allowed;
    opacity: 0.5;
  }
`;

const SplashTemplateName = styled.div`
  font-size: 11px;
  font-weight: bold;
  margin-bottom: 5px;
`;

const SplashTemplateDescription = styled.div`
  font-size: 11px;
`;

interface SplashTemplateVideoProps {
  src: string;
  playing: boolean;
}

const SplashTemplateVideo: FC<SplashTemplateVideoProps> = ({
  src,
  playing,
}) => {
  const ref = useRef<HTMLVideoElement>(null);

  useLayoutEffect(() => {
    if (ref.current) {
      if (playing) {
        ref.current?.play();
      } else {
        ref.current?.pause();
      }
    }
  }, [playing, ref]);

  return <video ref={ref} src={src} muted loop />;
};

export const SplashTemplateSelect = ({
  templates,
  templatePlugins,
  name,
  value,
  disabled,
  onChange,
}: SplashTemplateSelectProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedPluginId, setSelectedPluginId] = useState(
    templatePlugins[0]?.id ?? "",
  );
  const selectedPlugin =
    templatePlugins.find((template) => template.id === selectedPluginId) ||
    templatePlugins[0];
  const selectedTemplate =
    templates.find((template) => template.id === value) ?? selectedPlugin;

  const templatePluginOptions: Option[] = templatePlugins.map((v) => ({
    value: v.id,
    label: v.name,
  }));

  const selectedTemplatePluginOption: Option =
    templatePluginOptions.find((t) => t.value === selectedPluginId) ??
    templatePluginOptions[0];

  return (
    <SplashTemplateSelectWrapper>
      <SplashTemplateSelectOptions>
        {templates.map((template) => (
          <SplashTemplateButtonWrapper key={template.id}>
            <SplashTemplateButton
              id={`${name}_${template.id}`}
              name={name}
              value={template.id}
              checked={template.id === value}
              onChange={() => onChange(template.id)}
              disabled={disabled}
            />
            <SplashTemplateLabel
              htmlFor={`${name}_${template.id}`}
              title={template.name}
            >
              {template.videoPreview ? (
                <SplashTemplateVideo
                  src={template.preview}
                  playing={template.id === value}
                />
              ) : (
                <img
                  src={template.preview}
                  alt={template.name}
                  draggable={false}
                />
              )}
            </SplashTemplateLabel>
          </SplashTemplateButtonWrapper>
        ))}
        {selectedPlugin && (
          <SplashTemplateButtonWrapper key={selectedPlugin.id}>
            <SplashTemplateButton
              id={`${name}_${selectedPlugin.id}`}
              name={name}
              value={selectedPlugin.id}
              checked={selectedPlugin.id === value}
              onChange={() => onChange(selectedPlugin.id)}
              onClick={() => setIsOpen(true)}
              disabled={disabled}
            />
            <SplashTemplateLabel
              htmlFor={`${name}_${selectedPlugin.id}`}
              title={selectedPlugin.name}
            >
              <img
                src={selectedPlugin.preview}
                alt={selectedPlugin.name}
                onError={(e) =>
                  ((e.target as HTMLImageElement).src = pluginPreview)
                }
              />
            </SplashTemplateLabel>
            {isOpen && (
              <RelativePortal pin="top-right" offsetX={78}>
                <SelectMenu>
                  <Select
                    name={name}
                    options={templatePluginOptions}
                    value={selectedTemplatePluginOption}
                    onChange={(option) => {
                      if (option) {
                        setSelectedPluginId(option.value);
                        onChange(option.value);
                        setIsOpen(false);
                      }
                    }}
                    onBlur={() => {
                      setIsOpen(false);
                    }}
                    {...selectMenuStyleProps}
                  />
                </SelectMenu>
              </RelativePortal>
            )}
          </SplashTemplateButtonWrapper>
        )}
      </SplashTemplateSelectOptions>
      {selectedTemplate && (
        <>
          <SplashTemplateName>{selectedTemplate.name}</SplashTemplateName>
          <SplashTemplateDescription>
            {selectedTemplate.description}
          </SplashTemplateDescription>
        </>
      )}
    </SplashTemplateSelectWrapper>
  );
};
