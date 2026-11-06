"""dashboard/jarvis_data.py: Paseo 기록 -> 역할·줄, state/ 파일 -> 결정·현황."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dashboard'))
import jarvis_data as jd  # noqa: E402

tmp = tempfile.mkdtemp()
try:
    J = os.path.join(tmp, 'JARVIS')
    P = os.path.join(tmp, 'paseo')
    os.makedirs(os.path.join(J, 'state', 'decisions'))
    os.makedirs(os.path.join(J, 'state', 'tasks'))
    nipa = os.path.join(tmp, 'nipa')
    with open(os.path.join(J, 'registry.yaml'), 'w', encoding='utf-8') as f:
        f.write('projects:\n  nipa:\n    name: "NIPA"\n    root: "%s"\n    coordinator: null\n' % nipa.replace('\\', '/'))

    def agent(i, title, cwd, parent=None, status='idle', archived=None, attention=None):
        d = {'id': i, 'title': title, 'cwd': cwd, 'lastStatus': status, 'createdAt': '2026-11-06T10:00:00.000Z',
             'lastActivityAt': '2099-01-01T00:00:00.000Z', 'labels': {'paseo.parent-agent-id': parent} if parent else {},
             'runtimeInfo': {'sessionId': 's-' + i}, 'archivedAt': archived,
             'requiresAttention': bool(attention), 'attentionReason': attention}
        os.makedirs(os.path.join(P, 'agents', 'x'), exist_ok=True)
        with open(os.path.join(P, 'agents', 'x', i + '.json'), 'w', encoding='utf-8') as f:
            json.dump(d, f)

    agent('j', 'JARVIS', J)
    agent('c', 'nipa-coord', nipa, parent='j', status='running')
    agent('w', 'nipa-w-W-20261106-02', nipa, parent='c', attention='permission')
    agent('old', 'JARVIS', J, archived='2026-11-06T09:00:00Z')   # 보관된 것은 뺀다
    agent('x', 'someone-else', tmp)                                 # 팀 밖은 뺀다

    rows, names, hub = jd.agent_rows(J, root=P)
    by = {r['name']: r for r in rows}
    assert hub == 'JARVIS' and set(by) == {'JARVIS', 'nipa-coord', 'nipa-w-W-20261106-02'}, by.keys()
    assert by['JARVIS']['lane'] == 'HUB' and by['JARVIS']['group'] == 'resident'
    assert by['nipa-coord']['lane'] == 'nipa' and by['nipa-coord']['state'] == 'working'
    w = by['nipa-w-W-20261106-02']
    assert w['group'] == 'task' and w['task'] == 'W-20261106-02' and w['lane'] == 'nipa'
    assert w['state'] == 'blocked' and w['needs'] == 'permission'

    with open(os.path.join(J, 'state', 'decisions', 'D-20261106-01.md'), 'w', encoding='utf-8') as f:
        f.write('---\nid: D-20261106-01\ntask: W-20261106-02\nproject: nipa\nstatus: decided   # 주석\n'
                'decided_by: criteria\ncriterion: "#4"\noptions: ["① 유지", "② 제거"]\nraised_at: 2026-11-06T19:20:00+09:00\n'
                'decision: ①\n---\n## 질문\n고장확률 문구를 어떻게 할까요?\n\n## 결정과 이유\n기준 #4\n')
    d = jd.decisions(J)[0]
    assert d['status'] == 'decided' and d['title'] == '고장확률 문구를 어떻게 할까요?', d
    assert d['options'] == ['① 유지', '② 제거'] and d['decision'] == '기준 #4: ①', d['decision']

    with open(os.path.join(J, 'state', 'tasks', 'W-20261106-02.md'), 'w', encoding='utf-8') as f:
        f.write('---\nid: W-20261106-02\nproject: nipa\ntitle: 고장확률 수정\nstatus: question\nverified: none\nrelease: dev_only\n'
                'updated_at: 2026-11-06T19:22:00+09:00\n---\n## 보고\n원인 확인 | 결정 대기\n')
    md = jd.status_markdown(J)
    assert '| `W-20261106-02` | 고장확률 수정 | 질문 중 | - | 대기 | 원인 확인 / 결정 대기 |' in md, md
    assert jd.iso_ms('2026-11-06T10:00:00Z') == jd.iso_ms('2026-11-06T19:00:00+09:00')
    print('ok')
finally:
    shutil.rmtree(tmp, ignore_errors=True)
