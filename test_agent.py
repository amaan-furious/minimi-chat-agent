import urllib.request, json

BASE = 'http://localhost:8000'

# Create session
req = urllib.request.Request(
    BASE+'/sessions', data=b'{}',
    headers={'Content-Type':'application/json'}, method='POST'
)
session = json.loads(urllib.request.urlopen(req).read())
sid = session['id']
print(f'Session: {sid}')

# Send a message that requires web search
payload = json.dumps({
    'session_id': sid,
    'message': 'What is the latest version of Python and when was it released?'
}).encode()
req = urllib.request.Request(
    BASE+'/chat', data=payload,
    headers={'Content-Type':'application/json'}, method='POST'
)
print('Streaming response:')
print('-'*60)

with urllib.request.urlopen(req, timeout=60) as resp:
    for raw in resp:
        line = raw.decode('utf-8').strip()
        if line.startswith('data:'):
            try:
                evt = json.loads(line[5:].strip())
                t = evt['type']
                d = evt['data']
                if t == 'tool_call':
                    q = d['args']['query']
                    print(f'[SEARCH] {q}')
                elif t == 'tool_result':
                    n = d['result_count']
                    s = d['source']
                    print(f'[GOT {n} results via {s}]')
                elif t == 'token':
                    print(d, end='', flush=True)
                elif t == 'done':
                    print()
                    print('-'*60)
                    print('DONE')
                elif t == 'error':
                    print(f'ERROR: {d}')
            except Exception as e:
                pass

print()
print('Test complete!')
