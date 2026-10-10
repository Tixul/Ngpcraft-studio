"""Migrate a legacy comparison project with the published GB Studio sources.

A monolithic project (GB Studio 1.x to 3.x) goes through migrateLegacyProject;
a resource project saved by an older 4.x goes through the published loader
(loadProjectResources, its TypeBox Cast) like GB Studio 4.3.2 opening it. Both
then run migrateProjectResources.

Usage: python -m tools.migrate_gbstudio game.gbsproj DEST --dependencies NODE_MODULES
The original is never written. Assets and migration evidence accompany DEST.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from tools.reference import REFERENCE
from ngpcraft.project import load_project, _atomic_write
from ngpcraft.gbvm.events import HANDLERS

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def migrate(source, destination, dependencies, reference=REFERENCE):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    dependencies=Path(dependencies).resolve();reference=Path(reference).resolve()
    if not source.is_file() or source.suffix.lower() != '.gbsproj':
        raise ValueError('source must be a legacy .gbsproj file')
    original=json.loads(source.read_text(encoding='utf-8-sig'))
    resources=bool(original.get('_resourceType'))
    if destination == source.parent or destination.is_relative_to(source.parent):
        raise ValueError('destination must be outside the source project')
    if destination.exists():
        raise ValueError('destination must be new; no comparison project is overwritten')
    if (source.parent/'plugins').exists() and any((source.parent/'plugins').rglob('*')):
        raise ValueError('plugin migration needs its own event definitions; this core migration tool does not execute plugins')
    node=shutil.which('node')
    if not node:raise ValueError('Node is required for official GB Studio migration')
    inputs=[source]+sorted(p for top in ('assets','project') for p in (source.parent/top).rglob('*') if p.is_file())
    before={p.relative_to(source.parent).as_posix():digest(p) for p in inputs}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ngc-migration-',dir=destination.parent) as temp:
        scratch=Path(temp);candidate=scratch/'project';candidate.mkdir()
        # A resource project's asset .gbsres are rebuilt by the published export buffer.
        shutil.copytree(source.parent/'assets',candidate/'assets',
                        ignore=shutil.ignore_patterns('*.gbsres') if resources else None)
        raw=scratch/'official.json'
        proc=subprocess.run([node,str(Path(__file__).with_suffix('.cjs')),str(reference),
                             str(dependencies),str(source),str(candidate),str(raw)],
                            capture_output=True,text=True,encoding='utf8',timeout=120)
        if proc.returncode:raise ValueError('official migration failed:\n'+proc.stderr[-6000:])
        result=json.loads(raw.read_text(encoding='utf8'))
        for entry in result['buffer']:
            target=(candidate/entry['path']).resolve()
            if not target.is_relative_to(candidate):raise ValueError('resource path escapes migrated project')
            if target.exists():raise ValueError('official migration generated a duplicate resource path: '+entry['path'])
            _atomic_write(target,entry['data'])
        model=load_project(candidate)
        commands={};warnings=[]
        def visit(events,where):
            for i,event in enumerate(events):
                if (event.get('args') or {}).get('__comment'):continue
                command=event['command'];loc=f'{where}[{i}]'
                commands.setdefault(command,[]).append(loc)
                args=event.get('args') or {}
                if command == 'EVENT_LAUNCH_PROJECTILE' and args.get('directionType') == 'anglevar':
                    warnings.append({'path':loc,'kind':'angle-variable-needs-review',
                        'reason':'Modern angles use 0..255; EVENT_ACTOR_GET_DIRECTION stores 0..3. Compare legacy intent before editing.'})
                for key,child in (event.get('children') or {}).items():
                    if key == 'script' and command == 'EVENT_CALL_CUSTOM_EVENT':continue
                    visit(child,loc+'.children.'+key)
        for path,resource in model.files.items():
            for key,value in resource.items():
                if (key == 'script' or key.endswith('Script')) and isinstance(value,list):visit(value,path+':'+key)
        if any(digest(p)!=before[p.relative_to(source.parent).as_posix()] for p in inputs):
            raise ValueError('source changed while migration ran; retry against a stable project')
        report={'schema':'ngpcraft-gbstudio-migration/1','source':str(source),
                'source_version':original.get('_version'),'source_release':original.get('_release'),
                'target_metadata':result['metadata'],'reference':str(reference),
                'reference_sources_sha256':result['sourceHashes'],'input_sha256':before,
                'asset_hydration':result['hydration'],'resources':len(result['buffer']),
                'active_commands':commands,'unimplemented_commands':sorted(set(commands)-set(HANDLERS)),
                'warnings':warnings,'source_unchanged':True,
                'source_kind':'resources' if resources else 'monolithic',
                'scope':'Core official migration plus explicit classic sprite detection and compiler bounds; no audio conversion or gameplay repair.'}
        _atomic_write(candidate/'migration-report.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        _atomic_write(candidate/'official-migration-resources.json',json.dumps(result['migrationOnlyBuffer'],ensure_ascii=False,indent=2)+'\n')
        for attempt in range(5):
            try:
                os.replace(candidate,destination);break
            except PermissionError:
                # Windows: an indexer or antivirus may still hold a fresh file.
                if attempt==4:raise
                import time;time.sleep(0.5*(attempt+1))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    parser.add_argument('--dependencies',type=Path,required=True,help='node_modules with the pinned migration dependencies')
    parser.add_argument('--reference',type=Path,default=REFERENCE)
    args=parser.parse_args()
    report=migrate(args.source,args.destination,args.dependencies,args.reference)
    print(json.dumps({'output':str(args.destination),'resources':report['resources'],
                      'unimplemented_commands':report['unimplemented_commands'],
                      'warnings':len(report['warnings'])},indent=2))

if __name__=='__main__':main()
