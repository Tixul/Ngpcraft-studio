// The GB Studio 4.3.2 code that the plugin bridge runs (plugin_bridge.cjs,
// docs/M7_PLUGINS.md), from one of two places:
//   - fromSources(src, modules): a released GB Studio source tree, TypeScript
//     transpiled at load time (development, tests);
//   - fromBundle(file): the same modules gathered by esbuild into one file,
//     ScriptBuilder signatures read beforehand (packaged editor: no sources,
//     no TypeScript; built by src/lib/forge/hooks/bundle-plugin-bridge.js).
// Both give {ScriptBuilder, LATEST_PROJECT_VERSION,
// loadScriptEventHandlerFromUntrustedString, signatures}, English set.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

// What the bridge takes from GB Studio, in the order the editor loads it:
// [export name, module, export]; LANGUAGE marks when English is set (after the
// ScriptBuilder, before the rest).
const LANGUAGE = 'lang/en.json';
const STEPS = [
  ['ScriptBuilder', 'lib/compiler/scriptBuilder/scriptBuilder', 'default'],
  ['setL10NData', 'shared/lib/lang/l10n', 'setL10NData'],
  LANGUAGE,
  ['LATEST_PROJECT_VERSION', 'lib/project/migration/migrateProjectResources', 'LATEST_PROJECT_VERSION'],
  ['loadScriptEventHandlerFromUntrustedString', 'lib/scriptEventsHandlers/untrustedHandler',
   'loadScriptEventHandlerFromUntrustedString'],
];
// package.json "imports" of GB Studio (webpack alias in the editor).
const ALIASES = {'#my-quickjs-variant': '@jitl/quickjs-singlefile-cjs-release-sync'};
const SCRIPT_BUILDER_FILES = ['scriptBuilderBase.ts', 'scriptBuilder.ts'];

// ScriptBuilder method -> {required, total} positional parameters, read from the
// released TypeScript (total null: rest parameter).
const signaturesOf = (ts, src) => {
  const signatures = {};
  for (const file of SCRIPT_BUILDER_FILES) {
    const full = path.join(src, 'lib/compiler/scriptBuilder', file);
    const sf = ts.createSourceFile(full, fs.readFileSync(full, 'utf8'), ts.ScriptTarget.ES2020, true);
    const visit = (node) => {
      if (ts.isPropertyDeclaration(node) && node.initializer &&
          (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
        const params = node.initializer.parameters;
        signatures[node.name.getText(sf)] = {
          required: params.filter((q) => !q.questionToken && !q.initializer && !q.dotDotDotToken).length,
          total: params.some((q) => q.dotDotDotToken) ? null : params.length};
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return signatures;
};

// esbuild entries ({file name: source}): the same modules, statically imported in
// the same order (an import is evaluated where it stands); language.ts sets English.
const bundleEntry = () => {
  const lines = [];
  for (const step of STEPS) {
    if (step === LANGUAGE) {
      lines.push('import "./language";');
    } else {
      const [name, module, exported] = step;
      lines.push(`export {${exported} as ${name}} from ${JSON.stringify(module)};`);
    }
  }
  return {
    'entry.ts': lines.join('\n') + '\n',
    'language.ts': 'import {setL10NData} from "shared/lib/lang/l10n";\n' +
      `import language from ${JSON.stringify(LANGUAGE)};\nsetL10NData(language);\n`,
  };
};

const pick = (loaded, signatures) => {
  const gb = {signatures};
  for (const step of STEPS) {
    if (step !== LANGUAGE && step[0] !== 'setL10NData') gb[step[0]] = loaded[step[0]];
  }
  return gb;
};

const fromSources = (src, modules) => {
  const depsRequire = Module.createRequire(path.join(modules, '__ngpcraft_bridge.cjs'));
  const ts = depsRequire('typescript');
  const srcRoot = path.resolve(src);
  const originalResolve = Module._resolveFilename;
  Module._resolveFilename = function(specifier, parent, ...rest) {
    if (Object.prototype.hasOwnProperty.call(ALIASES, specifier)) return depsRequire.resolve(ALIASES[specifier]);
    if (specifier === '../helpers/l10n') return path.join(src, 'shared/lib/lang/l10n.ts');
    if (specifier.startsWith('.') && parent) {
      const base = path.resolve(path.dirname(parent.filename), specifier);
      for (const c of [base + '.ts', base + '.js', base + '.json', path.join(base, 'index.ts')]) {
        if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
      }
    }
    if (/^(shared|lib|consts|store|components|renderer|lang)(\/|$)/.test(specifier)) {
      const base = path.join(src, specifier);
      for (const c of [base + '.ts', base + '.js', base + '.json', base, path.join(base, 'index.ts')]) {
        if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
      }
    }
    if (!specifier.startsWith('.') && !path.isAbsolute(specifier) && !Module.isBuiltin(specifier)) {
      try { return depsRequire.resolve(specifier); } catch (_) {}
    }
    return originalResolve.call(this, specifier, parent, ...rest);
  };
  const compileTs = (module, filename) => {
    const out = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {fileName: filename, compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true, resolveJsonModule: true,
    }}).outputText;
    module._compile(out, filename);
  };
  const originalJs = Module._extensions['.js'];
  Module._extensions['.js'] = function(module, filename) {
    // GB Studio's own .js sources are ES modules bundled by webpack.
    if (!path.resolve(filename).startsWith(srcRoot)) return originalJs(module, filename);
    return compileTs(module, filename);
  };
  Module._extensions['.ts'] = compileTs;
  const loaded = {};
  for (const step of STEPS) {
    if (step === LANGUAGE) {
      loaded.setL10NData(require(path.join(src, LANGUAGE)));
    } else {
      const [name, module, exported] = step;
      loaded[name] = require(path.join(src, module + '.ts'))[exported];
    }
  }
  return pick(loaded, signaturesOf(ts, src));
};

const fromBundle = (file) => {
  const loaded = require(path.resolve(file));
  if (!loaded.signatures) throw new Error(`${file}: not a plugin bridge bundle (no signatures)`);
  return pick(loaded, loaded.signatures);         // English set by the bundle itself
};

module.exports = {ALIASES, signaturesOf, bundleEntry, fromSources, fromBundle};
