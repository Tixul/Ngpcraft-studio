"""Freeze the original integer ATAN2 and projectile VM sources (GBVM bd6f41c)."""
import argparse, hashlib, json, re
from pathlib import Path
from tools.reference import REFERENCE, GBVM_COMMIT, LABEL
from tools.extract_gbvm_model import write_json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'ngpcraft/runtime/projectile_vm.json'


def extract(root=REFERENCE):
    base='appData/engine/gbvm/'
    paths=[base+'src/core/math_atan2.c',base+'src/core/vm_projectiles.c',base+'src/core/vm_actor.c',
           base+'include/math.h',base+'LICENSE']
    raw={p:(root/p).read_bytes() for p in paths}
    source=raw[paths[0]].decode('utf-8').replace('\r\n','\n')
    table=re.search(r'const uint8_t atan2_table\[20\]\[18\] = \{.*?\n\};',source,re.S)
    fn=re.search(r'uint8_t atan2\(int16_t y, int16_t x\) BANKED \{.*?\n\}',source,re.S)
    clamp=re.search(r'^#define CLAMP\(a, min, max\).+$',raw[paths[3]].decode('utf-8'),re.M)
    if not table or not fn or not clamp:raise ValueError('Original ATAN2 source shape changed')
    rows=re.findall(r'\{([^{}]+)\}',table[0])
    if len(rows)!=20 or any(len(re.findall(r'\d+',r))!=18 for r in rows):raise ValueError('Original ATAN2 table shape changed')
    text=clamp[0].rstrip().replace('CLAMP','NGC_PROJECTILE_CLAMP')+'\n'+table[0]+'\n'+fn[0]+'\n#undef NGC_PROJECTILE_CLAMP\n'
    text=text.replace('const uint8_t','static const unsigned char').replace('uint8_t atan2(', 'static unsigned char ngc_atan2(')
    text=text.replace('int16_t','short').replace(' BANKED','').replace('CLAMP(x','NGC_PROJECTILE_CLAMP(x').replace('CLAMP(y','NGC_PROJECTILE_CLAMP(y')
    return dict(reference=LABEL,gbvm_commit=GBVM_COMMIT,
                sources={p:hashlib.sha256(data).hexdigest() for p,data in raw.items()},
                license=raw[paths[-1]].decode('utf-8'),atan2_source=text,
                adaptations=['C89 type names; static selected function named ngc_atan2; unchanged CLAMP/table/quadrants'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('write','check'));args=p.parse_args()
    fresh=extract()
    if args.command=='write':write_json(OUT,fresh);print('projectile VM reference written');return 0
    same=json.loads(OUT.read_text(encoding='utf-8'))==fresh
    print('projectile VM reference: OK' if same else 'projectile VM reference changed')
    return 0 if same else 1


if __name__=='__main__':raise SystemExit(main())
