"""Reproducible allowlisted source bundle; excludes credentials and runtime data."""
import hashlib
from pathlib import Path
import stat
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT.parent/'alfa_hack-vps.zip'
DIRECTORIES=('apps','config','scripts','docs','tests','deploy','dist')
FILES=('Dockerfile','compose.yaml','compose.vps.yaml','deploy.sh','.dockerignore','.gitignore','.gitattributes','.env.example',
       'package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html','requirements.txt',
       'requirements-lock.txt','run.py','setup.ps1','start.ps1','README.md','VPS.md','ARCHITECTURE.md')
EXCLUDED={'__pycache__','.pytest_cache','.venv','node_modules','.npm-cache','data','.git'}
paths=[ROOT/f for f in FILES]
for directory in DIRECTORIES:
    paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and not EXCLUDED.intersection(p.relative_to(ROOT).parts) and p.suffix not in ('.pyc','.pyo','.tsbuildinfo') and not p.name.startswith('.env'))
paths=sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())
assert all(p.is_file() for p in paths),'A required source file is missing'
manifest=[]
with zipfile.ZipFile(OUTPUT,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path in paths:
        rel=path.relative_to(ROOT).as_posix()
        assert not EXCLUDED.intersection(path.relative_to(ROOT).parts)
        assert not path.name.startswith('.env') or rel=='.env.example'
        content=path.read_bytes()
        # Linux scripts and configuration should not inherit Windows CRLF endings.
        if path.suffix in ('.sh','.py','.md','.yaml','.json','.txt','.tsx','.ts','.css','.html') or path.name in ('Dockerfile','Caddyfile','.dockerignore','.gitignore','.env.example'):
            content=content.replace(b'\r\n',b'\n')
        manifest.append(f'{hashlib.sha256(content).hexdigest()}  {rel}')
        entry=zipfile.ZipInfo('alfa_hack/'+rel)
        entry.create_system=3;entry.compress_type=zipfile.ZIP_DEFLATED
        entry.external_attr=(stat.S_IFREG | (0o755 if path.suffix=='.sh' else 0o644))<<16
        archive.writestr(entry,content)
    archive.writestr('alfa_hack/MANIFEST.sha256','\n'.join(manifest)+'\n')
with zipfile.ZipFile(OUTPUT) as archive:
    assert archive.testzip() is None
    for line in archive.read('alfa_hack/MANIFEST.sha256').decode().splitlines():
        expected,rel=line.split('  ',1)
        assert hashlib.sha256(archive.read('alfa_hack/'+rel)).hexdigest()==expected
    # Ensure accidentally hardcoded local credentials cannot reach a release.
    payloads=[archive.read(name) for name in archive.namelist()]
    sensitive={'STAFF_PASSWORD','POSTGRES_PASSWORD','LLM_API_KEY','TELEGRAM_BOT_TOKEN','AWS_SECRET_ACCESS_KEY','AWS_ACCESS_KEY_ID'}
    for env_file in (ROOT/'.env',ROOT/'.env.vps'):
        if not env_file.exists():continue
        for line in env_file.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):continue
            key,value=line.split('=',1);value=value.strip().strip('"').strip("'")
            if key.strip() in sensitive and len(value)>=8 and not value.startswith(('replace-','<')):
                assert not any(value.encode() in data for data in payloads),f'Credential detected in bundle: {key.strip()}'
    count=len(archive.namelist())
checksum=hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
OUTPUT.with_suffix('.zip.sha256').write_text(checksum+'  '+OUTPUT.name+'\n',encoding='ascii')
print(f'Archive: {OUTPUT}\nFiles: {count}\nBytes: {OUTPUT.stat().st_size}\nSHA256: {checksum}\nManifest verified; no credentials, databases or installed dependencies included.')
