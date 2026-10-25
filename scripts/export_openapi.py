"""Write the API's OpenAPI schema to docs/api/openapi.json.

    python scripts/export_openapi.py

tests/test_deploy.py fails when the committed file no longer matches the app: run this after
changing a route or a request/response model.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'api' / 'openapi.json'


def schema() -> str:
    from apps.api.main import app
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n'


if __name__ == '__main__':
    # Importing the app opens its database: keep the export away from local data.
    os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='alfa-openapi-'))
    sys.path.insert(0, str(ROOT))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(schema(), encoding='utf-8', newline='\n')
    print(f'{OUT.relative_to(ROOT)} written')
