// Execute the complete released ScriptBuilder under the local TypeScript loader.
// No rewritten compiler bodies or replacement emission helpers.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const crypto = require('node:crypto');
const [reference, dependencies] = process.argv.slice(2);
const src = path.join(reference, 'src');
const depsRequire = Module.createRequire(path.join(dependencies, '__oracle.cjs'));
const ts = depsRequire('typescript');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function(specifier, parent, ...rest) {
  // Released handlerCommon.ts createMockRequire legacy alias; real module.
  if (specifier === '../helpers/l10n') return path.join(src,'shared/lib/lang/l10n.ts');
  if (specifier.startsWith('.') && parent) {
    const base = path.resolve(path.dirname(parent.filename), specifier);
    for (const candidate of [base+'.ts', base+'.js', base+'.json', path.join(base,'index.ts')]) {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
  }
  if (/^(shared|lib|consts|store|components|renderer|lang)(\/|$)/.test(specifier)) {
    const base = path.join(src, specifier);
    for (const candidate of [base+'.ts', base+'.js', base+'.json', base, path.join(base,'index.ts')]) {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
  }
  if (!specifier.startsWith('.') && !path.isAbsolute(specifier) && !Module.isBuiltin(specifier)) {
    try { return depsRequire.resolve(specifier); } catch (_) {}
  }
  return originalResolve.call(this, specifier, parent, ...rest);
};
Module._extensions['.ts'] = function(module, filename) {
  const output = ts.transpileModule(fs.readFileSync(filename,'utf8'), {fileName: filename, compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
    esModuleInterop: true, resolveJsonModule: true,
  }}).outputText;
  module._compile(output, filename);
};
const filename = path.join(src, 'lib/compiler/scriptBuilder/scriptBuilder.ts');
const Builder = require(filename).default;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
if (!Array.isArray(input) && input.expressions) {
  const tokenizer = require(path.join(src, 'shared/lib/rpn/tokenizer.ts')).default;
  const shuntingYard = require(path.join(src, 'shared/lib/rpn/shuntingYard.ts')).default;
  const helpers = require(path.join(src, 'shared/lib/scriptValue/helpers.ts'));
  const results = input.expressions.map(expression => {
    try {
      const tokens = tokenizer(expression);
      return {tokens, rpn: shuntingYard(tokens), value: helpers.expressionToScriptValue(expression)};
    } catch (error) { return {error: error.message}; }
  });
  process.stdout.write(JSON.stringify({results}));
  return;
}
const results = input.map(row => {
  const output = [];
  const options = {...row.options};
  if (options.argLookup && Array.isArray(options.argLookup.variable)) {
    options.argLookup = {variable: new Map(options.argLookup.variable), actor: new Map(options.argLookup.actor || [])};
  }
  const eventFiles = row.eventFiles || {};
  const compileEvents = (builder, events=[]) => {
    for (const event of events) {
      const handler = require(path.join(src, 'lib/events', eventFiles[event.command]));
      // compileEntityEvents passes {...helpers, event}: event.symbol (editableSymbol).
      const helpers = new Proxy(builder, {get: (target, key) => key === 'event' ? event : target[key]});
      handler.compile({...event.args, ...event.children}, helpers);
    }
  };
  // subScripts: child scripts in a context of their own (threads, timers...)
  // go through the released compileEntityEvents with the listed handlers.
  const handlers = {};
  if (row.subScripts) {
    for (const [command, file] of Object.entries(eventFiles)) handlers[command] = require(path.join(src, 'lib/events', file));
    Object.assign(options, {additionalScripts: {}, additionalScriptsCache: {}, symbols: {}, maxDepth: 5,
                            context: 'entity', disabledSceneTypeIds: [], ...options});
  }
  const builder = new Builder(output, {scene: {actors: [{id: 'a'}, {id: 'b'}]}, scriptEventHandlers: handlers, compileEvents, warnings: () => {}, ...options});
  if (row.events) compileEvents(builder, row.events);
  for (const [method, args] of (Array.isArray(row) ? row : row.methods || [])) builder[method](...args);
  builder._packLocals();
  // scriptString: the whole script text (data tables follow the code).
  if (row.scriptString) return {output, stack: builder.stackPtr, script: builder.toScriptString(row.scriptString, false)};
  return row.subScripts ? {output, stack: builder.stackPtr, additionalScripts: builder.options.additionalScripts}
                        : {output, stack: builder.stackPtr};
});
process.stdout.write(JSON.stringify({source_sha256: crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex'), results}));
