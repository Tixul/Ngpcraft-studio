/* eslint-disable @typescript-eslint/no-var-requires */
const webpack = require("webpack");
const ForkTsCheckerWebpackPlugin = require("fork-ts-checker-webpack-plugin");
const BundleAnalyzerPlugin =
  require("webpack-bundle-analyzer").BundleAnalyzerPlugin;
const { GitRevisionPlugin } = require("git-revision-webpack-plugin");
const pkg = require("../../../package.json");

const gitRevisionPlugin = new GitRevisionPlugin({
  commithashCommand: "rev-list --max-count=1 --no-merges --abbrev-commit HEAD",
});

// NgpCraft: the fork folder may not be a git repository; fall back to a fixed
// label instead of failing the build.
const gitOr = (read, fallback) => {
  try {
    return read();
  } catch (e) {
    return fallback;
  }
};

const docsUrl = "https://www.gbstudio.dev/docs/";

const plugins = [
  new webpack.DefinePlugin({
    GIT_VERSION: JSON.stringify(
      gitOr(() => gitRevisionPlugin.version(), pkg.version),
    ),
    COMMITHASH: JSON.stringify(
      gitOr(() => gitRevisionPlugin.commithash(), "ngpcraft"),
    ),
    VERSION: JSON.stringify(pkg.version),
    RELEASE_VERSION: JSON.stringify(pkg.version.replace(/-rc.*/, "")),
    DOCS_URL: JSON.stringify(docsUrl),
  }),
];

if (process.env.ANALYZE_BUNDLE) {
  plugins.push(new BundleAnalyzerPlugin());
}

if (!process.env.NO_TYPE_CHECKING) {
  plugins.push(
    new ForkTsCheckerWebpackPlugin({
      async: false,
      typescript: {
        memoryLimit: 4096,
      },
    }),
  );
}

module.exports = plugins;
