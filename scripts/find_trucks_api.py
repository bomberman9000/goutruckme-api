"""Ищем trucks/autopark API через собственные машины аккаунта"""
import json, httpx, re

with open('ati_state.json') as f:
    raw = json.load(f)
cookies = {c['name']: c['value'] for c in raw}
ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

with httpx.Client(cookies=cookies, timeout=15) as client:
    # Собственный автопарк (у профиля autopark_main=2)
    candidates = [
        ('GET', 'https://ati.su/webapi/autopark/v1/trucks?skip=0&take=5', None),
        ('GET', 'https://ati.su/webapi/autopark/v1/autopark?skip=0&take=5', None),
        ('GET', 'https://d.ati.su/webapi/autopark/v1/trucks?skip=0&take=5', None),
        ('GET', 'https://d.ati.su/webapi/autopark/v1/autopark?skip=0&take=5', None),
        ('GET', 'https://ati.su/webapi/trucks/v1/my?skip=0&take=5', None),
        ('POST', 'https://trucks.ati.su/webapi/v1.0/trucks/my', {'paging': {'skip': 0, 'take': 5}}),
        ('GET', 'https://trucks.ati.su/webapi/v1.0/autopark/my?skip=0&take=5', None),
    ]

    for method, url, body in candidates:
        h = {'User-Agent': ua, 'Accept': 'application/json', 'Referer': 'https://ati.su/'}
        try:
            if method == 'POST':
                r = client.post(url, json=body, headers={**h, 'Content-Type': 'application/json', 'Origin': url.split('/webapi')[0]})
            else:
                r = client.get(url, headers=h)
            domain_path = url.replace('https://', '').split('?')[0]
            print(f'{r.status_code} {method} {domain_path}')
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, list) and d:
                    print(f'  list[{len(d)}] keys: {list(d[0].keys())[:8]}')
                    print(f'  ✅ FIRST: {json.dumps(d[0], ensure_ascii=False)[:300]}')
                elif isinstance(d, dict):
                    print(f'  dict keys: {list(d.keys())[:10]}')
        except Exception as e:
            print(f'ERR {url}: {e}')

    print()
    print('=== Brute force ati.su/webapi paths ===')
    paths = [
        '/webapi/autopark/v1/trucks',
        '/webapi/autopark/v2/trucks',
        '/webapi/autotransport/v1/trucks',
        '/webapi/transport/v1/trucks',
        '/webapi/trucks/v1/trucks',
        '/webapi/vehicles/v1/vehicles',
    ]
    for path in paths:
        for base in ['https://ati.su', 'https://d.ati.su', 'https://trucks.ati.su']:
            url = f'{base}{path}?skip=0&take=3'
            r = client.get(url, headers={'User-Agent': ua, 'Accept': 'application/json', 'Referer': base + '/'})
            if r.status_code == 200:
                print(f'✅ {r.status_code} {url}')
                print(f'   {r.text[:300]}')
            elif r.status_code != 404:
                print(f'{r.status_code} {url}')
