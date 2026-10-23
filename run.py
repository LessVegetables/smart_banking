"""Single-origin local runner. Environment variables override values from .env."""
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
os.chdir(ROOT)
env=ROOT/'.env'
if env.exists():
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        name,value=line.split('=',1)
        os.environ.setdefault(name.strip(),value.strip().strip('"').strip("'"))

if __name__=='__main__':
    import socket
    import uvicorn
    host=os.environ.get('HOST','127.0.0.1')
    port=int(os.environ.get('PORT','8000'))
    if 'PORT' not in os.environ:
        for candidate in range(8000,8021):
            try:
                with socket.socket() as probe:probe.bind((host,candidate))
                port=candidate;break
            except OSError:continue
        else:raise RuntimeError('No free local port in 8000-8020. Set PORT explicitly.')
    print(f'Web: http://127.0.0.1:{port}/   Mini app: http://127.0.0.1:{port}/mini/',flush=True)
    uvicorn.run('apps.api.main:app',host=host,port=port)
