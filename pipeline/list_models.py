#!/usr/bin/env python3
"""List the models an API key can use.   Usage:  python list_models.py arc   |   python list_models.py xai
Reads keys from .env like arc_study.py does. Never prints the key itself."""
import json, os, sys, urllib.error, urllib.request
import arc_study

PROVIDERS = {'arc': ('https://llm-api.arc.vt.edu/api/v1/models', 'ARC_API_KEY'),
             'xai': ('https://api.x.ai/v1/models', 'XAI_API_KEY')}

def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else 'arc').lower()
    if which not in PROVIDERS: sys.exit('Usage: python list_models.py arc|xai')
    url, env = PROVIDERS[which]
    arc_study.load_dotenv(); arc_study.use_system_certificates()
    key = os.environ.get(env, '')
    if not key: sys.exit(f'{env} is empty. Add a line  {env}=your-key  to .env and save it.')
    if key != key.strip() or key[0] in '"\'' or ' ' in key:
        print(f'Warning: {env} has spaces or quotes around it; remove them in .env.')
    print(f'{env}: {len(key)} characters, starts with "{key[:4]}"')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key}), timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f'{which} refused the request: HTTP {e.code}\n{e.read().decode(errors="replace")[:500]}')
    except urllib.error.URLError as e:
        sys.exit(f'Could not reach {which}: {e.reason}' + (' (is the VT VPN on?)' if which == 'arc' else ''))
    ids = sorted(m.get('id', '?') for m in data.get('data', []))
    print(f'{len(ids)} models:'); print('\n'.join('  ' + i for i in ids))

if __name__ == '__main__':
    main()
