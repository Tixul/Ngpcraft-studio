"""Official GB Studio MOD import followed by explicit NGPC audio adaptation."""
import argparse,base64,hashlib,json,os,tempfile
from pathlib import Path
from tools.uge_oracle import run
from tools.reference import REFERENCE
from ngpcraft.uge_convert import export_conversion
from ngpcraft.project import _atomic_write

def atomic_bytes(path,data):
    fd,temp=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        os.replace(temp,path)
    except BaseException:
        if os.path.exists(temp):os.unlink(temp)
        raise

def convert_mod(source,destination,initial_apu,policy):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('destination must be new')
    if source.suffix.lower()!='.mod':raise ValueError('expected a MOD source')
    original=source.read_bytes();result=run([{'modpath':str(source)}])[0]
    if source.read_bytes()!=original:raise ValueError('source changed during official MOD import')
    binary=base64.b64decode(result['binary_base64'])
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ngc-mod-',dir=destination.parent) as tmp:
        root=Path(tmp);uge=root/'source.uge';uge.write_bytes(binary)
        report=export_conversion(uge,initial_apu,policy,root/'converted')
        out=root/'converted'
        for name,data in [('source.uge',binary),('initial-apu.json',Path(initial_apu).read_bytes()),('policy.json',Path(policy).read_bytes())]:
            atomic_bytes(out/name,data)
        evidence={'schema':'ngpcraft-mod-import/1','source_sha256':hashlib.sha256(original).hexdigest(),
                  'uge_sha256':hashlib.sha256(binary).hexdigest(),'source_name':source.name,
                  'official_import':'convertMODDataToUGESong(data, true) -> saveUGESong -> loadUGESong',
                  'reference':str(REFERENCE),'source_unchanged':True,
                  'reference_sources_sha256':{str(p.relative_to(REFERENCE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                      for base in (REFERENCE/'src/shared/lib/uge/mod2uge',) for p in sorted(base.rglob('*.ts'))}}
        _atomic_write(out/'mod-import.json',json.dumps(evidence,indent=2)+'\n')
        manifest=json.loads((out/'manifest.json').read_text())
        manifest['files_sha256']={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'}
        _atomic_write(out/'manifest.json',json.dumps(manifest,indent=2)+'\n')
        os.replace(out,destination)
    return {**report,'output':str(destination)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('destination')
    p.add_argument('--initial-apu',required=True);p.add_argument('--policy',required=True)
    a=p.parse_args();print(json.dumps(convert_mod(a.source,a.destination,a.initial_apu,a.policy),indent=2))

if __name__=='__main__':main()
