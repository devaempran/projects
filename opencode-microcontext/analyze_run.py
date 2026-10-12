import json, os, re, sys

d = os.path.expanduser('~/.local/share/opencode/log/llm-io')
L = os.path.join(d, sys.argv[1] if len(sys.argv) > 1 else '20260824T000944Z-22492.jsonl')
recs = [json.loads(l) for l in open(L, encoding='utf-8')]

pairs = []
pending = None
for r in recs:
    if r['type'] == 'request':
        pending = r
    elif pending is not None:
        pairs.append((pending, r)); pending = None


def out(a):
    o = a.get('output')
    if isinstance(o, str):
        try:
            o = json.loads(o)
        except Exception:
            return 'TEXT', ''
    if isinstance(o, dict):
        return o.get('name'), json.dumps(o.get('input'))
    return None, ''


print('file:', os.path.basename(L))
print('requests:', sum(1 for r in recs if r['type'] == 'request'), '| pairs:', len(pairs))
print()
for q, a in pairs:
    if q.get('source') != 'orchestrator':
        continue
    p = q.get('prompt') or ''
    m = re.search(r'Step (\d+) of (\d+)', p)
    name, args = out(a)
    flags = []
    if 'You cannot get more steps' in p:
        flags.append('CHECKPOINT/no-ext')
    if 'more steps would let you finish' in p:
        flags.append('CHECKPOINT/EXTENDABLE')
    if 'no new information' in p:
        flags.append('repeat-output')
    if 'already made this exact call' in p:
        flags.append('repeat-blocked')
    if 'returned nothing' in p:
        flags.append('empty')
    if 'budget was extended' in p:
        flags.append('EXTENDED')
    if 'LAST step' in p:
        flags.append('last-step')
    label = m.group(0) if m else '-'
    print(f"  {str(q.get('role')):8s} sub={str(q.get('subtaskId')):5s} {label:13s} -> {str(name):8s} "
          f"{args[:46]:48s} {' '.join(flags)}")

print()
print('=== reducer / verifier ===')
for q, a in pairs:
    if q.get('role') not in ('reducer', 'verifier'):
        continue
    name, args = out(a)
    print(f"--- {q['role']} input={q['estimatedInputTokens']}tok attempt={q.get('attempt')} ---")
    print(q['prompt'][:900])
    print('  -> ' + (args[:700] if args else str(name)))
    print()
