"""HTTP smoke test against a running local server, using synthetic data only."""
import sys
import time
import re
import httpx

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'
with httpx.Client(base_url=base,timeout=10) as c:
    for route in ('/','/mini/','/staff','/api/health','/api/catalog','/docs'):
        r=c.get(route);assert r.status_code==200,(route,r.status_code)
        print(route,r.status_code)
    html=c.get('/').text
    for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html):
        r=c.get(asset);assert r.status_code==200;assert 'text/html' not in r.headers['content-type'];print(asset,r.status_code)
    assert c.post('/api/auth/demo',json={'name':'Демо-проверка'},headers={'Origin':base}).status_code==200
    s=c.post('/api/surveys').json();s=c.post(f'/api/surveys/{s["id"]}/persona/healthy').json()
    a=c.post('/api/applications',json={'survey_id':s['id'],'version':s['version'],'confirmed':True,'idempotency_key':'smoke-'+s['id']}).json()
    for _ in range(50):
        a=c.get('/api/applications/'+a['id']).json()
        if a['status']=='approved':break
        time.sleep(.1)
    assert a['status']=='approved',a['status']
    for action in ('accept','sign'):
        r=c.post(f'/api/applications/{a["id"]}/actions',json={'action':action,'revision':a['revision']});assert r.status_code==200,r.text;a=r.json()
    assert a['status']=='issued'
    print('Background worker + application + accept + simulated signing: OK')
