/* eslint-disable @typescript-eslint/no-var-requires */
const { FusesPlugin } = require("@electron-forge/plugin-fuses");
const { FuseV1Options, FuseVersion } = require("@electron/fuses");
const rendererConfig = require("./webpack.renderer.config.js");

const rendererPreloadConfig = {
  ...rendererConfig,
  plugins: [],
};

// NgpCraft: Linux packages (deb, rpm), named like the AppImage below.
const ngpcraftLinuxPackage = {
  name: "ngpcraft-studio",
  bin: "ngpcraft-studio",
  productName: "NgpCraft Studio",
  description: "Neo Geo Pocket Color game maker based on GB Studio",
  homepage: "https://github.com/Tixul/Ngpcraft-studio",
  icon: "src/assets/app/icon/app_icon.png",
  categories: ["Development", "IDE"],
};

module.exports = async () => {
  const { MakerAppImage } = await import("@reforged/maker-appimage");

  return {
    makers: [
      {
        name: "@electron-forge/maker-squirrel",
        config: {
          name: "ngpcraft_studio",
          exe: "ngpcraft-studio.exe",
          loadingGif: "src/assets/app/install.gif",
          setupIcon: "src/assets/app/icon/app_icon.ico",
        },
      },
      {
        name: "@electron-forge/maker-zip",
        platforms: ["darwin", "win32", "linux"],
      },
      new MakerAppImage({
        options: {
          name: "ngpcraft-studio",
          bin: "ngpcraft-studio",
          productName: "NgpCraft Studio",
          genericName: "Visual retro game maker",
          icon: {
            strict: true,
            default: "512x512",
            "16x16": "src/assets/app/icon/16x16.png",
            "32x32": "src/assets/app/icon/32x32.png",
            "48x48": "src/assets/app/icon/48x48.png",
            "64x64": "src/assets/app/icon/64x64.png",
            "128x128": "src/assets/app/icon/128x128.png",
            "256x256": "src/assets/app/icon/256x256.png",
            "512x512": "src/assets/app/icon/512x512.png",
            "1024x1024": "src/assets/app/icon/app_icon.png",
          },
          categories: ["Development", "IDE"],
        },
      }),
      // NgpCraft: deb / rpm default to package.json's name ("gb-studio", kept
      // for plugin compatibility) for the package and its binary; the binary
      // is packagerConfig.executableName, and the package must not take the
      // name of an installed GB Studio.
      {
        name: "@electron-forge/maker-deb",
        config: {
          options: {
            ...ngpcraftLinuxPackage,
            maintainer: "Tixul",
          },
        },
      },
      {
        name: "@electron-forge/maker-rpm",
        config: {
          options: ngpcraftLinuxPackage,
        },
      },
    ],
    packagerConfig: {
      name: "NgpCraft Studio",
      executableName: "ngpcraft-studio",
      packageManager: "yarn",
      icon: "src/assets/app/icon/app_icon",
      darwinDarkModeSupport: true,
      extendInfo: "src/assets/app/Info.plist",
      extraResource: [
        "src/assets/app/icon/gbsproj.icns",
        // NgpCraft: no Assets.car (GB Studio's macOS 26 icon catalogue):
        // macOS takes the NgpCraft app_icon.icns instead.
        // NgpCraft: V3 compiler, assembled by the prePackage hook.
        ".bundle/ngpcraft-v3",
      ],
      afterCopy: ["./src/lib/forge/hooks/after-copy"],
      asar: true,
      appBundleId: "io.github.tixul.ngpcraftstudio",
      // NgpCraft: sign only when a certificate is configured (CI secrets);
      // otherwise the macOS build is unsigned instead of failing.
      ...(process.env.APPLE_ID
        ? {
            osxSign: {
              "hardened-runtime": true,
              entitlements: "./entitlements.plist",
            },
          }
        : {}),
    },
    hooks: {
      prePackage: require("../../../src/lib/forge/hooks/bundle-v3"),
      postPackage: require("../../../src/lib/forge/hooks/notarize"),
    },
    plugins: [
      {
        name: "@electron-forge/plugin-auto-unpack-natives",
        config: {},
      },
      {
        name: "@electron-forge/plugin-webpack",
        config: {
          devContentSecurityPolicy:
            "default-src 'self' 'unsafe-inline' data:; script-src 'self' 'unsafe-eval' 'unsafe-inline' data:; worker-src 'self' blob:;",
          devServer: { liveReload: false },
          mainConfig: "./src/apps/gb-studio/webpack.main.config.js",
          renderer: {
            config: "./src/apps/gb-studio/webpack.renderer.config.js",
            nodeIntegration: false,
            entryPoints: [
              {
                html: "./src/apps/gb-studio/project/project.html",
                js: "./src/apps/gb-studio/project/ProjectRoot.tsx",
                preload: {
                  js: "./src/apps/gb-studio/project/preload.ts",
                  config: rendererPreloadConfig,
                },
                name: "main_window",
                additionalChunks: [
                  "vendor-react",
                  "vendor-scriptracker",
                  "vendor-hotloader",
                  "vendor-lodash",
                ],
              },
              {
                html: "./src/apps/gb-studio/splash/splash.html",
                js: "./src/apps/gb-studio/splash/SplashRoot.tsx",
                preload: {
                  js: "./src/apps/gb-studio/splash/preload.ts",
                  config: rendererPreloadConfig,
                },
                name: "splash_window",
                additionalChunks: [
                  "vendor-react",
                  "vendor-hotloader",
                  "vendor-lodash",
                ],
              },
              {
                html: "./src/apps/gb-studio/preferences/preferences.html",
                js: "./src/apps/gb-studio/preferences/PreferencesRoot.tsx",
                preload: {
                  js: "./src/apps/gb-studio/project/preload.ts",
                  config: rendererPreloadConfig,
                },
                name: "preferences_window",
                additionalChunks: [
                  "vendor-react",
                  "vendor-hotloader",
                  "vendor-lodash",
                ],
              },
              {
                html: "./src/apps/gb-studio/plugins/plugins.html",
                js: "./src/apps/gb-studio/plugins/PluginsRoot.tsx",
                preload: {
                  js: "./src/apps/gb-studio/plugins/preload.ts",
                  config: rendererPreloadConfig,
                },
                name: "plugins_window",
              },
              {
                html: "./src/apps/gb-studio/music/music.html",
                js: "./src/apps/gb-studio/music/MusicRoot.tsx",
                preload: {
                  js: "./src/apps/gb-studio/project/preload.ts",
                  config: rendererPreloadConfig,
                },
                name: "music_window",
                additionalChunks: [
                  "vendor-react",
                  "vendor-hotloader",
                  "vendor-lodash",
                ],
              },
              {
                name: "game_window",
                preload: {
                  js: "./src/apps/gb-studio/game/preload.ts",
                  config: rendererPreloadConfig,
                },
              },
            ],
          },
        },
      },
      // Fuses are used to enable/disable various Electron functionality
      // at package time, before code signing the application
      new FusesPlugin({
        version: FuseVersion.V1,
        [FuseV1Options.RunAsNode]: false,
        [FuseV1Options.EnableCookieEncryption]: true,
        [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
        [FuseV1Options.EnableNodeCliInspectArguments]: false,
        [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
        [FuseV1Options.OnlyLoadAppFromAsar]: true,
      }),
    ],
  };
};
