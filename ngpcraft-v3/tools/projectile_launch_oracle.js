// Execute released ScriptBuilder arrow bodies. Low-level emission is a test
// adapter; this is a lowering oracle, not an execution of GB Studio's compiler.
const fs = require('fs'), crypto = require('crypto');
const source = fs.readFileSync(process.argv[2], 'utf8');
if(process.argv[3]==='definitions') {
 const input=JSON.parse(fs.readFileSync(0,'utf8'));
 const start=source.indexOf('export const compileSceneProjectiles = (');
 const end=source.indexOf('\nexport const compileSceneProjectilesHeader',start);
 if(start<0 || end<0) throw Error('Missing original definition compiler');
 let arrow=source.slice(start,end).replace('export const compileSceneProjectiles = ','').trim().replace(/;$/,'');
 arrow=arrow.replace(/: PrecompiledScene|: number|: PrecompiledSprite\[\]/g,'');
 const boundsStart=source.indexOf('const compileBounds = ('), boundsArrow=source.indexOf('=> {',boundsStart);
 const boundsEnd=source.indexOf('\n};',boundsArrow);
 if(boundsStart<0 || boundsEnd<0) throw Error('Missing original bounds compiler');
 const boundsBody=source.slice(boundsArrow+3,boundsEnd+2);
 const enginePxToSubPx=x=>'PX_TO_SUBPX('+x+')';
 const compileBounds=Function('enginePxToSubPx','return ({boundsX,boundsY,boundsWidth,boundsHeight}) => '+boundsBody).call(null,enginePxToSubPx);
 const groups={'':0,player:1,'1':2,'2':4,'3':8};
 const compile=Function('toStructArrayDataFile','PROJECTILE_TYPE','sceneProjectilesSymbol','sceneName',
  'filterNull','toFarPtr','pxToSubpx','toASMCollisionGroup','toASMCollisionMask','compileBounds','flatten','return ('+arrow+');')(
   (type,symbol,comment,rows)=>rows,'projectile_def_t',s=>s+'_projectiles',s=>s.name,
   rows=>rows.filter(r=>r!==null),s=>s,x=>Math.floor(x*32),s=>groups[s],
   mask=>mask.reduce((a,g)=>a|groups[g],0),compileBounds,a=>a.flat());
 const rows=compile({symbol:'scene',name:'oracle',projectiles:input.projectiles},0,input.sprites);
 for(const row of rows) {
   for(const key of Object.keys(row.bounds)) {
     const m=/^PX_TO_SUBPX\((-?\d+)\)( - 1)?$/.exec(row.bounds[key]);
     if(!m) throw Error('Unexpected bounds expression');
     row.bounds[key]=Number(m[1])*32-(m[2]?1:0);
   }
 }
 process.stdout.write(JSON.stringify({source_sha256:crypto.createHash('sha256').update(source).digest('hex'),results:rows}));
 process.exit(0);
}
const methods = ['_rpnProjectilePosArgs', 'launchProjectileInDirection',
 'launchProjectileInAngle', 'launchProjectileInAngleVariable',
 'launchProjectileInSourceActorDirection', 'launchProjectileInActorDirection',
 'launchProjectileTowardsActor'];
function extract(name) {
 const start = source.indexOf('  ' + name + ' = (');
 if(start < 0) throw Error('Missing original method ' + name);
 const arrow = source.indexOf('=> {', start), body = arrow + 3;
 let depth=1, end=body+1;
 while(depth && end<source.length) { const c=source[end++]; if(c==='{') depth++; if(c==='}') depth--; }
 if(depth) throw Error('Unclosed original method');
 const params=source.slice(source.indexOf('(',start),arrow).replace(/: (string|number)/g,'');
 return params+'=> '+source.slice(body,end);
}
function context() {
 const c = { output:[], stack:0, locals:new Map() };
 c._offset = (v, off=0) => {
   if(typeof v !== 'string') return String(v);
   if(!v.startsWith('.LOCAL') && !v.startsWith('.SCRIPT_ARG')) return v;
   if(c.stack===0 && off===0) return v;
   return '^/('+v+(off?' + '+off:'')+(c.stack?' - '+c.stack:'')+')/';
 };
 const pad = (name,args,indent,width) => ' '.repeat(indent)+name.padEnd(width,' ')+(args.length?args.join(', '):'');
 c._addCmd=(name,...args)=>c.output.push(pad(name,args.map(v=>c._offset(v)),8,24).trimEnd());
 c._declareLocal=(name,size,temp=false)=> {
   const symbol='.LOCAL_'+(temp?'TMP'+c.locals.size+'_':'')+name.toUpperCase();
   c.locals.set(symbol,size); return symbol;
 };
 c._localRef=(ref,off)=>c._offset(ref,off);
 c._addComment=text=>c.output.push('        ; '+text);
 c._addNL=()=>c.output.push('');
 c._actorGetPosition=ref=>c._addCmd('VM_ACTOR_GET_POS',ref);
 c._actorGetAngle=(ref,dest)=>c._addCmd('VM_ACTOR_GET_ANGLE',ref,dest);
 c._projectileLaunch=(index,ref)=>c._addCmd('VM_PROJECTILE_LAUNCH',index,ref);
 c._stackPop=n=>{ c._addCmd('VM_POP',n); c.stack-=n; };
 c.setActorId=(ref,id)=> {
   if(typeof id === 'object') c._addCmd('VM_SET',ref,id.symbol);
   else if(typeof id === 'string' && id.startsWith('.')) c._addCmd('VM_SET',ref,id);
   else c._addCmd('VM_SET_CONST',ref,typeof id === 'number'?id:0);
 };
 c._rpn=()=> {
   const r={lines:[],size:0};
   r.emit=(cmd,v)=>r.lines.push(pad(cmd,v===undefined?[]:[c._offset(v)],12,12).trimEnd());
   r.ref=v=>{r.emit('.R_REF',v);r.size++;return r;};
   r.int16=v=>{r.emit('.R_INT16',v);r.size++;return r;};
   r.refVariable=v=>{r.emit(typeof v==='object'?'.R_REF_IND':'.R_REF',typeof v==='object'?v.symbol:'VAR_VARIABLE_'+v);r.size++;return r;};
   r.operator=v=>{r.emit('.R_OPERATOR',v);r.size--;return r;};
   r.stop=()=>{r.emit('.R_STOP');c._addCmd('VM_RPN');c.output.push(...r.lines);c.stack+=r.size;};
   return r;
 };
 for(const name of methods) c[name]=Function('pxToSubpx','tileToSubpx','dirToAngle','return ('+extract(name)+');').call(c,
   x=>Math.floor(x*32),x=>Math.floor(x*256),d=>({up:0,right:64,down:128,left:192})[d]);
 return c;
}
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const result=input.map(row=>{const c=context(); c.stack=row.stack||0;c[row.method](...row.args);return {output:c.output,stack:c.stack};});
process.stdout.write(JSON.stringify({source_sha256:crypto.createHash('sha256').update(source).digest('hex'),results:result}));
