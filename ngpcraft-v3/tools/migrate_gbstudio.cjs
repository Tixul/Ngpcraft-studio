// Execute the published GB Studio migrations and resource encoder unchanged.
// TypeScript's transpileModule only removes types and converts module syntax.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const crypto = require('node:crypto');
const [reference, dependencies, input, projectRoot, result] = process.argv.slice(2);
const src = path.join(reference, 'src');
const depsRequire = Module.createRequire(path.join(dependencies, '__migration.cjs'));
const ts = depsRequire('typescript');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function(specifier, parent, ...rest) {
  if (specifier === '../helpers/l10n') return path.join(src,'shared/lib/lang/l10n.ts');
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
  const source = fs.readFileSync(filename,'utf8');
  const output = ts.transpileModule(source, {fileName: filename, compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
    esModuleInterop: true, resolveJsonModule: true,
  }}).outputText;
  module._compile(output, filename);
};
process.env.NODE_ENV = 'test';
// Match the loader's fieldsLookup for the trusted core event definitions.
const defs = {};
for (const filename of fs.readdirSync(path.join(src,'lib/events')).sort()) {
  if (!/^event.*\.js$/.test(filename)) continue;
  const handler = require(path.join(src,'lib/events',filename));
  const fieldsLookup = {};
  function visit(fields) { for (const field of fields || []) {
    if (field.key) fieldsLookup[field.key] = field;
    visit(field.fields);
  }}
  visit(handler.fields);
  defs[handler.id] = {...handler, fieldsLookup};
}
const {migrateLegacyProject} = require(path.join(src,'lib/project/migration/legacy/migrateLegacyProject.ts'));
const {migrateProjectResources} = require(path.join(src,'lib/project/migration/migrateProjectResources.ts'));
const {loadProjectResources} = require(path.join(src,'lib/project/loadProjectResources.ts'));
const {buildResourceExportBuffer, encodeResource} = require(path.join(src,'shared/lib/resources/save.ts'));
async function main() {
  const original = JSON.parse(fs.readFileSync(input,'utf8').replace(/^\uFEFF/, ''));
  // loadProjectData.ts: a metadata resource is loaded by loadProjectResources
  // from its own directory; a monolithic project by migrateLegacyProject.
  const unmigrated = original._resourceType
    ? await loadProjectResources(path.dirname(input), original)
    : migrateLegacyProject(original, projectRoot, defs);
  const migrated = await migrateProjectResources(unmigrated);
  const migrationOnlyBuffer = buildResourceExportBuffer(migrated);
  const hydration = [];
  const {detectClassic} = require(path.join(src,'renderer/lib/sprites/detect.ts'));
  const sizeOf = depsRequire('image-size');
  for (const sprite of migrated.sprites) {
    const file = path.join(projectRoot,'assets/sprites',sprite.filename);
    const size = sizeOf(file);
    sprite.width = size.width; sprite.height = size.height;
    const old = (original.spriteSheets || []).find(s=>s.id===sprite.id);
    if (old && !old.states && !old.animations) {
      if (size.height !== 16 || !size.width || size.width % 16) throw new Error('Cannot detect legacy sprite '+sprite.filename);
      const detected = detectClassic({...sprite, states:sprite.states.map(s=>s.id)});
      const tiles = Object.fromEntries(detected.metaspriteTiles.map(t=>[t.id,t]));
      const frames = Object.fromEntries(detected.metasprites.map(f=>[f.id,{...f,tiles:f.tiles.map(id=>tiles[id])}]));
      const animations = Object.fromEntries(detected.spriteAnimations.map(a=>[a.id,{...a,frames:a.frames.map(id=>frames[id])}]));
      sprite.states = [{...detected.state, animations:detected.state.animations.map(id=>animations[id])}];
      Object.assign(sprite,detected.changes);
      hydration.push({resource:sprite.id,source:'renderer/lib/sprites/detect.ts:detectClassic',frames:size.width/16});
    }
    // Explicitly materialize the published compiler's `boundsWidth || 16`
    // and `boundsHeight || 16` for the strict NGPC input; do not change loader.
    for (const key of ['boundsWidth','boundsHeight']) if (sprite[key]===0) {
      hydration.push({resource:sprite.id,field:key,before:0,after:16,source:'compileBounds fallback'});
      sprite[key]=16;
    }
  }
  for (const song of migrated.music) song.type = /\.mod$/i.test(song.filename) ? 'mod' : 'uge';
  // Older 4.x saves carried the font's character mapping in its resource. The
  // 4.3.2 FontResource has no such field: loadFontData reads it from the font's
  // .json beside the image, so the copy in the resource is dropped, recorded.
  for (const font of migrated.fonts) if ('mapping' in font) {
    hydration.push({resource:font.id,field:'mapping',before:'present',after:'absent',
                    source:'FontResource (4.3.2 types.ts); loadFontData reads assets/fonts/*.json'});
    delete font.mapping;
  }
  for (const [kind, assets] of [['emotes',migrated.emotes],['avatars',migrated.avatars],['fonts',migrated.fonts]]) {
    for (const asset of assets) {
      const size = sizeOf(path.join(projectRoot,'assets',kind,asset.filename));
      asset.width=size.width;asset.height=size.height;
    }
  }
  const buffer = buildResourceExportBuffer(migrated);
  buffer.push({path:'project.gbsproj', data:encodeResource('project',migrated.metadata)});
  const loaded = Object.keys(require.cache).filter(f=>f.startsWith(reference));
  const sourceHashes = Object.fromEntries(loaded.map(f=>[path.relative(reference,f).replace(/\\/g,'/'),crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex')]));
  const temporary = result+'.'+process.pid+'.tmp';
  try {
    fs.writeFileSync(temporary, JSON.stringify({metadata:migrated.metadata, buffer, migrationOnlyBuffer, hydration, sourceHashes},null,2), {flag:'wx'});
    fs.renameSync(temporary,result);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
