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
    # 활동 기준: 실행 중 working, 권한 대기 waiting, 닫힘·오래 활동 없음 off
    assert by['nipa-coord']['activity'] == 'working' and by['nipa-w-W-20261106-02']['activity'] == 'waiting'
    old = jd.row_of({'id': 'z', 'lastStatus': 'idle', 'lastActivityAt': '2020-01-01T00:00:00Z'}, 'coordinator', 'nipa',
                    jd.iso_ms('2026-11-09T00:00:00Z'))
    closed = jd.row_of({'id': 'y', 'lastStatus': 'closed', 'lastActivityAt': '2026-11-09T00:00:00Z'}, 'coordinator',
                       'nipa', jd.iso_ms('2026-11-09T00:10:00Z'))
    fresh = jd.row_of({'id': 'v', 'lastStatus': 'idle', 'lastActivityAt': '2026-11-09T00:00:00Z'}, 'coordinator',
                      'nipa', jd.iso_ms('2026-11-09T00:10:00Z'))
    assert (old['activity'], closed['activity'], fresh['activity']) == ('off', 'off', 'resting')
    # 방: 세션이 일하거나 진행 중 작업·정할 결정·교체 중·최근 활동이 있으면 active, PM 상태와 이유를 붙인다
    of = {p['key']: p for p in jd.office(J, rows)['projects']}
    assert of['nipa']['active'] and '세션 작업·대기 중' in of['nipa']['why'], of
    quiet = [dict(r, activity='off') for r in rows]
    assert not {p['key']: p for p in jd.office(J, quiet)['projects']}['nipa']['active']
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
    # 작업이 끝났는데 열린 결정은 stale, dropped 는 '필요 없어짐'
    for n, st in (('02', 'open'), ('03', 'dropped')):
        with open(os.path.join(J, 'state', 'decisions', 'D-20261106-%s.md' % n), 'w', encoding='utf-8') as f:
            f.write('---\nid: D-20261106-%s\ntask: W-20261106-02\nstatus: %s\n---\n## 질문\nq\n' % (n, st))
    st = {d['id']: d for d in jd.decisions(J)}
    assert st['D-20261106-02']['status'] == 'open', st   # 작업이 아직 question
    with open(os.path.join(J, 'state', 'tasks', 'W-20261106-02.md'), encoding='utf-8') as f:
        txt = f.read()
    with open(os.path.join(J, 'state', 'tasks', 'W-20261106-02.md'), 'w', encoding='utf-8') as f:
        f.write(txt.replace('status: question', 'status: done'))
    st = {d['id']: d for d in jd.decisions(J)}
    assert st['D-20261106-02']['status'] == 'stale' and st['D-20261106-03']['decision'] == '필요 없어짐', st
    with open(os.path.join(J, 'state', 'tasks', 'W-20261106-02.md'), 'w', encoding='utf-8') as f:
        f.write(txt)
    md = jd.status_markdown(J)
    assert '| `W-20261106-02` | 고장확률 수정 | 질문 중 | - | 대기 | 원인 확인 / 결정 대기 |' in md, md
    assert jd.iso_ms('2026-11-06T10:00:00Z') == jd.iso_ms('2026-11-06T19:00:00+09:00')

    # 같은 폴더를 쓰는 두 프로젝트: 소속은 제목(<키>-coord, <키>-w-…)과 부모로 정한다
    reg = {'wf3-wf4': {'root': 'C:/p/nipa'}, 'nl2sql': {'root': 'C:/p/nipa'}, 'docs': {'root': 'C:/p/docs'}}
    par = lambda i: {'paseo.parent-agent-id': i}
    recs = [{'id': 'J', 'title': 'JARVIS', 'cwd': 'C:/J'},
            {'id': 'A', 'title': 'wf3-wf4-coord', 'cwd': 'C:/p/nipa', 'labels': par('J')},
            {'id': 'B', 'title': 'nl2sql-coord', 'cwd': 'C:/p/nipa', 'labels': par('J')},
            {'id': 'C', 'title': '문서', 'cwd': 'C:/p/docs', 'labels': par('J')},
            {'id': 'D', 'title': 'nl2sql-w-W-20261106-1', 'cwd': 'C:/p/nipa', 'labels': par('B')},
            {'id': 'E', 'title': 'x', 'cwd': 'C:/p/nipa/wt', 'labels': par('A')}]
    out, _, _ = jd.roles(recs, reg)
    assert out['A'] == ('coordinator', 'wf3-wf4') and out['B'] == ('coordinator', 'nl2sql') and out['C'] == ('coordinator', 'docs'), out
    assert out['D'] == ('worker', 'nl2sql') and out['E'] == ('worker', 'wf3-wf4'), out

    # 새 Worker 제목 '<영역>-<한국어 작업 이름>' + 라벨 jarvis.task, 제목이 JARVIS 가 아닌 JARVIS(폴더로 찾기)
    recs = [{'id': 'J', 'title': 'C:/x/경로', 'cwd': 'C:/J'},
            {'id': 'A', 'title': 'nipa-coord', 'cwd': 'C:/p/nipa', 'labels': par('J')},
            {'id': 'W', 'title': 'wf3-쿼리확인', 'cwd': 'C:/p/nipa', 'lastStatus': 'running',
             'labels': {'paseo.parent-agent-id': 'A', 'jarvis.task': 'W-20261108-01'}}]
    out, jid, _ = jd.roles(recs, {'nipa': {'root': 'C:/p/nipa'}}, home='C:/J')
    assert jid == 'J' and out['A'] == ('coordinator', 'nipa') and out['W'] == ('worker', 'nipa'), out
    row = jd.row_of(recs[2], 'worker', 'nipa', 0)
    assert row['name'] == 'wf3-쿼리확인' and row['task'] == 'W-20261108-01', row

    # PM 세션 교체 뒤: registry 의 현재 PM 만 coordinator, 옛 PM 은 pm_prev, 그 Worker 는 그대로 프로젝트 Worker
    # 대표 JARVIS 는 registry jarvis: 가 살아 있으면 그것(제목이 JARVIS 인 세션이 여럿이어도)
    recs = [{'id': 'J1', 'title': 'JARVIS', 'cwd': 'C:/J', 'lastActivityAt': '2026-11-09T02:00:00Z'},
            {'id': 'J2', 'title': 'JARVIS', 'cwd': 'C:/J', 'lastActivityAt': '2026-11-09T01:00:00Z'},
            {'id': 'NEW', 'title': 'nipa-pm', 'cwd': 'C:/p/nipa', 'labels': {'jarvis.role': 'pm', 'jarvis.project': 'nipa'}},
            {'id': 'OLD', 'title': 'nipa-coord', 'cwd': 'C:/p/nipa', 'labels': {}},
            {'id': 'W', 'title': 'wf3-일', 'cwd': 'C:/p/nipa', 'labels': {'paseo.parent-agent-id': 'OLD'}}]
    reg = {'nipa': {'root': 'C:/p/nipa', 'coordinator': 'NEW'}}
    out, jid, _ = jd.roles(recs, reg, home='C:/J', rep='J2')
    assert jid == 'J2' and out['NEW'] == ('coordinator', 'nipa') and out['OLD'] == ('pm_prev', 'nipa'), out
    assert out['W'] == ('worker', 'nipa') and out['J1'] == ('other', ''), out
    row = jd.row_of(recs[3], 'pm_prev', 'nipa', 0)
    assert row['group'] == 'task' and row['lane'] == 'nipa' and row['name'] == 'nipa-coord (이전)', row
    print('ok')
finally:
    shutil.rmtree(tmp, ignore_errors=True)
