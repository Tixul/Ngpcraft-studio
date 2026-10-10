"""Save/load between SHMUP and POINTNCLICK with native music still attached."""
import argparse, json, os
from pathlib import Path
from tools.lab_music_save import project_case
from tools.demo_gbs_scene import write_json
from tools.probe_music_events import observe
from ngpcraft.pipeline import export_project
from ngpcraft.toolchain import build_rom

def run(root,toolchain,emulator,bios,driver):
    if root.exists():raise ValueError('output must be new')
    root.mkdir(parents=True)
    project,_=project_case(root)
    for path in (project/'project/scenes').rglob('scene.gbsres'):
        data=json.loads(path.read_text(encoding='utf-8'))
        data['type']='SHMUP' if path.parent.name=='sample_town' else 'POINTNCLICK'
        write_json(path,data)
    m=export_project(project,root/'export');build=build_rom(root/'export',Path(toolchain))
    proof=observe(root/'export',emulator,bios,driver,root/'proof',frames=300)
    variables=proof['samples'][-1]['variables']
    result={'passed':proof['correct'] and variables['VAR_VARIABLE_20']==1 and variables['VAR_VARIABLE_21']==7,
            'variables':variables,'features':m['features'],'restores':[a for a in proof['actions'] if a[1]=='restore'],
            'save':build['save'],'rom_bytes':len((root/'export/rom/game.ngc').read_bytes())}
    write_json(root/'comparison.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    for name in ('toolchain','emulator','bios','driver'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--clang',default=os.environ.get('NGC_TEST_CLANG'))
    a=p.parse_args()
    if not a.clang:p.error('--clang or NGC_TEST_CLANG is required')
    os.environ['NGC_TEST_CLANG']=str(a.clang)
    r=run(a.out,a.toolchain,a.emulator,a.bios,a.driver)
    print(json.dumps({k:r[k] for k in ('passed','variables','restores','rom_bytes')}))
    return 0 if r['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
