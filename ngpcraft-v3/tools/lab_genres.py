"""Native M5 genre regression cases; counter fixtures, not unchanged gbs2 games."""
import argparse, hashlib, json
from pathlib import Path
from tools.genre_oracle import run, UP, DOWN, LEFT, RIGHT, A
from tools.demo_gbs_scene import write_json

MOVES = [(10,40,UP|LEFT),(50,80,DOWN|RIGHT),(90,120,UP|RIGHT),
         (130,160,DOWN|LEFT),(170,200,LEFT),(210,240,RIGHT),(250,280,UP),(290,320,DOWN)]
CASES = {}
for direction, start in (("right",(0,9)),("left",(245,9)),("up",(12,24)),("down",(12,0))):
    for mode in ("FREE", "LOCK_PERPENDICULAR"):
        CASES[f"shmup-{direction}-{mode.lower()}"] = dict(kind="shmup", frames=340, schedule=MOVES,
            direction=direction, start=start, scenery="sample_town" if direction in ("up","down") else None,
            keep_actors=False, fields={"SHOOTER_MOVEMENT_TYPE":"MOVEMENT_TYPE_"+mode})
CASES["shmup-end"] = dict(kind="shmup", frames=300, schedule=[], keep_actors=False,
                          fields={"shooter_scroll_speed":255})
CASES["shmup-trigger-contact"] = dict(kind="shmup",frames=140,schedule=[],keep_actors=False,
    triggers=[(2,9,2,2,"1","2"),(6,9,2,2,"3",None)])
CASES["shmup-trigger-screen"] = dict(kind="shmup",frames=140,schedule=[],keep_actors=False,
    fields={"SHOOTER_TRIGGER_ACTIVATION":"ON_SCREEN_ENTER"},
    triggers=[(20,0,1,18,"1","2"),(25,0,1,18,"3",None)])
CASES["shmup-wall"] = dict(kind="shmup",frames=100,schedule=[(2,95,DOWN)],keep_actors=False,
    walls=[(x,12,15) for x in range(14)],player_hits=True,
    fields={"SHOOTER_WALL_COLLISION_GROUP":"COLLISION_GROUP_2"})
CASES["shmup-actor"] = dict(kind="shmup",frames=80,schedule=[],keep_actors=False,player_hits=True,
    fields={"shooter_scroll_speed":0},actors=[(0,72,"1","1")])
CASES["shmup-unscripted-actor"] = dict(kind="shmup",frames=80,schedule=[],keep_actors=False,
    fields={"shooter_scroll_speed":0},actors=[(0,72,"1",None)])
CASES["pnc-clamp"] = dict(kind="pnc",frames=600,keep_actors=False,
    schedule=[(1,200,LEFT|UP),(210,500,RIGHT|DOWN),(510,580,LEFT|RIGHT|UP|DOWN)])
CASES["pnc-trigger"] = dict(kind="pnc",frames=90,schedule=[(8,9,A),(20,35,A),(50,51,A)],
    keep_actors=False,triggers=[(9,6,2,2,"1",None)])
CASES["pnc-leave-only"] = dict(kind="pnc",frames=90,schedule=[(8,9,A),(20,21,A)],
    keep_actors=False,triggers=[(9,6,2,2,None,"2")])
CASES["pnc-actor-priority"] = dict(kind="pnc",frames=90,schedule=[(8,9,A),(20,35,A),(50,51,A)],
    keep_actors=False,triggers=[(9,6,2,2,"1",None)],actors=[(72,48,"","2")])
CASES["pnc-combined-actor"] = dict(kind="pnc",frames=90,schedule=[(8,9,A),(20,21,A)],
    keep_actors=False,actors=[(72,48,"1","2")])
CASES["pnc-diagonal"] = dict(kind="pnc",frames=340,schedule=MOVES,keep_actors=False)
CASES["shmup-projectiles"] = dict(kind="shmup",frames=140,keep_actors=False,shots=True,
    schedule=[(10,11,A),(25,26,A),(40,41,A),(55,56,A)])

def unsigned_sine(export):
    path=export/'src/scene_actors.c'
    source=path.read_text(encoding='utf-8')
    old='(short)((short)SIN(a) * (short)(speed))'
    assert source.count(old)==1
    source=source.replace(old,'(SIN(a) * (unsigned short)(speed))')
    path.write_text(source,encoding='utf-8')
    data=json.loads((export/'export.json').read_text(encoding='utf-8'))
    data['sha256']['src/scene_actors.c']=hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(export/'export.json',data)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    for name in ('toolchain','emulator','bios','clang'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--case',choices=tuple(CASES)+('witness',),action='append')
    a=p.parse_args()
    if a.out.exists():p.error('output must be new')
    a.out.mkdir(parents=True)
    results={}
    for name in a.case or tuple(CASES)+('witness',):
        case=dict(CASES['pnc-diagonal' if name=='witness' else name])
        r=run(a.out/name,toolchain=a.toolchain,emulator=a.emulator,bios=a.bios,
              clang=a.clang,mutate=unsigned_sine if name=='witness' else None,**case)
        results[name]=r
        print(name, json.dumps({k:r[k] for k in ('ticks','mismatches','script_starts','seen','passed')}),flush=True)
        write_json(a.out/'summary.json',results)
    return 0 if all(not r['passed'] if name=='witness' else r['passed'] for name,r in results.items()) else 1

if __name__=='__main__':raise SystemExit(main())
