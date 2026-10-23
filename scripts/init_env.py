"""Create local-only demo secrets once; never print their values."""
from pathlib import Path
import secrets

root=Path(__file__).resolve().parents[1]
target=root/'.env'
if not target.exists():
    text=(root/'.env.example').read_text(encoding='utf-8')
    text=text.replace('replace-with-a-long-random-password',secrets.token_urlsafe(24))
    text=text.replace('APP_ORIGIN=http://127.0.0.1:5173','APP_ORIGIN=http://127.0.0.1:8000')
    text+='\nPOSTGRES_PASSWORD='+secrets.token_hex(24)+'\n'
    target.write_text(text,encoding='utf-8')
    print('Local .env created. Staff password is in STAFF_PASSWORD. Existing files are never overwritten.')
