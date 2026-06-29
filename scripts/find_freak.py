from pathlib import Path
p=Path('data/podcasts/raw')
if not p.exists():
    print('no raw dir')
    raise SystemExit
files=list(p.rglob('*.mp3'))
fk=[f for f in files if 'freakonomics' in str(f).lower()]
if not fk:
    print('no freakonomics files')
else:
    fk_sorted=sorted(fk,key=lambda f: f.stat().st_size)
    for f in fk_sorted[:10]:
        print(f, round(f.stat().st_size/(1024*1024),2))
