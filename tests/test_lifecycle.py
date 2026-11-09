"""수명주기 훅과 jarvisctl (docs/02): 임시 JARVIS 폴더·Paseo 기록으로 훅 스크립트를 실제로 돌려 본다."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
HOOKS = os.path.join(ROOT, 'hooks')
sys.path.insert(0, HOOKS)
from jarvis_only_write import MSG_NEW, MSG_REG, shared_block  # noqa: E402
from jarvis_state import registry, role_of  # noqa: E402

JV, PM, PM2, W1, W2, OTHER = ('aaaaaaaa-0000-0000-0000-00000000000%d' % i for i in range(1, 7))
OLDPM = 'bbbbbbbb-0000-0000-0000-000000000001'
TMP = tempfile.mkdtemp(prefix='jarvis-test-')
J = os.path.join(TMP, 'JARVIS')
P = os.path.join(TMP, 'paseo')
PROJ = os.path.join(TMP, 'proj').replace('\\', '/')


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def rec(aid, cwd, status='idle', labels=None, archived=None):
    w(os.path.join(P, 'agents', 'x', aid + '.json'), json.dumps(
        {'id': aid, 'cwd': cwd, 'lastStatus': status, 'labels': labels or {}, 'archivedAt': archived}))


def setup():
    w(os.path.join(J, 'registry.yaml'), 'boss: 나\njarvis: %s\nprojects:\n  nipa:\n    name: "NIPA"\n'
      '    root: "%s"\n    coordinator: %s\n    linear_project: KSM\n' % (JV, PROJ, PM))
    w(os.path.join(J, 'rules', 'coordinator.md'), '# Coordinator 규칙\n규칙본문\n')
    w(os.path.join(J, 'state', 'tasks', 'W-20261109-01.md'),
      '---\nid: W-20261109-01\nproject: nipa\ntitle: 열린일\nstatus: working\n---\n')
    w(os.path.join(J, 'state', 'tasks', 'W-20261109-02.md'),
      '---\nid: W-20261109-02\nproject: nipa\ntitle: 끝난일\nstatus: done\n---\n')
    w(os.path.join(J, 'state', 'decisions', 'D-20261109-01.md'),
      '---\nid: D-20261109-01\nproject: nipa\nstatus: open\n---\n')
    w(os.path.join(J, 'state', 'handoff', 'nipa', 'latest.md'), '## 진행 중\n- 인계내용\n')
    rec(JV, J)
    rec(OTHER, J)
    rec(PM, PROJ, labels={'jarvis.role': 'pm', 'jarvis.project': 'nipa'})
    rec(PM2, PROJ, labels={'jarvis.role': 'pm', 'jarvis.project': 'nipa'})   # 후보
    rec(W1, PROJ, status='closed', labels={'paseo.parent-agent-id': PM, 'jarvis.task': 'W-20261109-01'})
    rec(OLDPM, PROJ, status='closed', labels={'jarvis.role': 'pm', 'jarvis.project': 'nipa'})


def run(script, inp, agent, cwd=None):
    env = dict(os.environ, CLAUDE_PLUGIN_OPTION_JARVIS_DIR=J, PASEO_HOME=P, PASEO_AGENT_ID=agent or '',
               PYTHONIOENCODING='utf-8', JARVIS_HOOK_NO_DETACH='1')
    inp = dict({'cwd': cwd or PROJ}, **inp)
    r = subprocess.run([sys.executable, os.path.join(HOOKS, script)], input=json.dumps(inp), env=env,
                       capture_output=True, text=True, encoding='utf-8')
    return r.returncode, r.stdout, r.stderr


def ctl(*args, agent=JV):
    env = dict(os.environ, JARVIS_DIR=J, PASEO_HOME=P, PASEO_AGENT_ID=agent, PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'jarvisctl.py')] + list(args), env=env,
                       capture_output=True, text=True, encoding='utf-8')
    return r.returncode, json.loads(r.stdout or 'null')


def denied(out):
    return '"permissionDecision": "deny"' in out


def events():
    try:
        with open(os.path.join(J, 'state', 'sessions.jsonl'), encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def create(title, labels=None):
    return {'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__paseo__create_agent',
            'tool_input': {'title': title, 'labels': labels or {}, 'provider': 'claude/x', 'initialPrompt': 'x'}}


def close(tool, target):
    return {'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__paseo__' + tool, 'tool_input': {'agentId': target}}


PMLB = {'jarvis.role': 'pm', 'jarvis.project': 'nipa'}
NL = chr(10)

try:
    setup()
    os.environ['PASEO_HOME'] = P
    reg = registry(J)
    assert reg['jarvis'] == JV and reg['projects']['nipa']['linear_project'] == 'KSM'

    # ---- 역할 ----
    from jarvis_state import paseo_record
    assert role_of(JV, reg, J, J)[0] == 'jarvis'
    assert role_of(OTHER, reg, J, J, paseo_record(OTHER))[0] == 'jarvis_other'
    assert role_of(PM, reg, J, PROJ) == ('pm', 'nipa')
    assert role_of(PM2, reg, J, PROJ, paseo_record(PM2)) == ('pm', 'nipa')      # 라벨로 후보 PM
    assert role_of(W1, reg, J, PROJ, paseo_record(W1)) == ('worker', 'nipa')
    assert role_of('', reg, J, PROJ)[0] == 'none'

    # ---- SessionStart ----
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart', 'source': 'compact'}, PM)
    ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
    for s in ('nipa', '규칙본문', '인계내용', 'W-20261109-01', 'D-20261109-01', 'jarvis-hooks v1#', '활성 PM'):
        assert s in ctx, s
    code, hk = ctl('hook-check', PM)
    assert code == 0 and hk['tag'] in ctx                                                  # 세션마다 다른 표식
    assert 'W-20261109-02' not in ctx
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart', 'source': 'startup'}, PM2)
    assert '연결 전' in out
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart'}, OTHER, cwd=J)
    assert 'JARVIS 시작 점검' in out and '기본 수신자' in out          # 다른 JARVIS 창도 같은 맥락
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart'}, W1)
    assert out.strip() == ''
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart'}, '')
    assert out.strip() == ''

    # ---- create_agent 가드 ----
    assert denied(run('agent_guard.py', create('nipa-pm'), JV, cwd=J)[1])                  # 라벨 없음
    assert denied(run('agent_guard.py', create('nipa-pm', PMLB), JV, cwd=J)[1])            # 활성 PM 열림, lock 없음
    assert denied(run('agent_guard.py', create('x', PMLB), OTHER, cwd=J)[1])               # 다른 창도 중복 PM 은 막힘
    assert not denied(run('agent_guard.py', create('wf3-일'), JV, cwd=J)[1])               # PM 아닌 생성은 통과
    code, lk = ctl('lock', 'acquire', 'nipa')
    assert code == 0 and lk['lock']['old_agent_id'] == PM
    assert ctl('lock', 'acquire', 'nipa')[0] == 1                                          # 배타적
    assert ctl('lock', 'acquire', 'nipa', agent=PM)[0] == 1                                 # PM 은 jarvisctl 못 씀
    assert not denied(run('agent_guard.py', create('nipa-pm', PMLB), JV, cwd=J)[1])        # lock 중 후보 생성 허용
    assert denied(run('agent_guard.py', create('nipa-pm', PMLB), JV, cwd=J)[1])            # 예약 중 두 번째 생성
    post = dict(create('nipa-pm', PMLB), hook_event_name='PostToolUse',
                tool_response={'agentId': PM2, 'status': 'running'})
    run('agent_guard.py', post, JV, cwd=J)
    assert json.load(open(os.path.join(J, 'state', 'handoff', 'nipa', 'rotating.lock'), encoding='utf-8'))[
        'candidate_id'] == PM2
    assert denied(run('agent_guard.py', create('nipa-pm', PMLB), JV, cwd=J)[1])            # 후보 중복
    # PM 의 Worker 생성
    assert denied(run('agent_guard.py', create('wf3-일', {'jarvis.task': 'W-1'}), PM2)[1])  # 후보 PM
    assert denied(run('agent_guard.py', create('wf3-일', {'jarvis.task': 'W-1'}), PM)[1])   # lock 중
    # ---- 전환 ----
    code, res = ctl('switch', 'nipa', PM)
    assert code == 1                                                                       # 후보와 다름
    code, res = ctl('switch', 'nipa', PM2)
    assert code == 1 and '진행 중 작업' in res['why'], res                                  # 작업이 working
    w(os.path.join(J, 'state', 'tasks', 'W-20261109-01.md'),
      '---\nid: W-20261109-01\nproject: nipa\ntitle: 열린일\nstatus: done\n---\n')
    rec(PM2, PROJ, labels=dict(PMLB, **{'paseo.parent-agent-id': JV}))
    code, res = ctl('switch', 'nipa', PM2)
    assert code == 1 and 'detach' in res['why'], res                                       # 아직 JARVIS 의 자식
    rec(PM2, PROJ, labels=PMLB)
    assert ctl('switch', 'nipa', PM2, agent=PM)[0] == 1                                    # PM 은 못 씀
    code, res = ctl('switch', 'nipa', PM2)
    assert code == 0 and res['ok'], res
    assert registry(J)['projects']['nipa']['coordinator'] == PM2
    assert not os.path.exists(os.path.join(J, 'state', 'handoff', 'nipa', 'rotating.lock'))
    assert events()[-1]['event'] == 'rotation' and events()[-1]['new_agent_id'] == PM2
    code, res = ctl('switch', 'nipa', PM2)
    assert code == 1                                                                       # lock 없음
    # 새 프로젝트: 빈 값·null 인 coordinator 줄에 switch 하면 'coordinator: <ID>' 꼴로 들어간다
    NP = 'cccccccc-0000-0000-0000-000000000001'
    NQ = 'cccccccc-0000-0000-0000-000000000002'
    with open(os.path.join(J, 'registry.yaml'), 'a', encoding='utf-8') as f:
        f.write('  newp:' + NL + '    name: "새"' + NL + '    root: "%s"' % PROJ + NL + '    coordinator:' + NL)
        f.write('  newq:' + NL + '    root: "%s"' % PROJ + NL + '    coordinator: null   # 아직' + NL)
    rec(NP, PROJ, labels={'jarvis.role': 'pm', 'jarvis.project': 'newp'})
    rec(NQ, PROJ, labels={'jarvis.role': 'pm', 'jarvis.project': 'newq'})
    for proj, aid in (('newp', NP), ('newq', NQ)):
        assert ctl('lock', 'acquire', proj, '--reason', 'new')[0] == 0
        assert ctl('lock', 'candidate', proj, aid)[0] == 0
        code, res = ctl('switch', proj, aid)
        assert code == 0, res
        assert ctl('switch', proj, aid)[0] == 1                                            # lock 없음
    text = open(os.path.join(J, 'registry.yaml'), encoding='utf-8').read()
    assert '    coordinator: %s\n' % NP in text and '    coordinator: %s # 아직' % NQ in text, text
    reg2 = registry(J)
    assert reg2['projects']['nipa']['coordinator'] == PM2 and reg2['jarvis'] == JV
    assert denied(run('agent_guard.py', create('wf3-일'), PM2)[1])                         # jarvis.task 없음
    assert not denied(run('agent_guard.py', create('wf3-일', {'jarvis.task': 'W-1'}), PM2)[1])
    # ---- archive·kill 가드 ----
    assert denied(run('agent_guard.py', close('archive_agent', PM2), JV, cwd=J)[1])        # 활성 PM
    assert denied(run('agent_guard.py', close('archive_agent', JV), JV, cwd=J)[1])         # 대표 자신
    assert denied(run('agent_guard.py', close('kill_agent', PM), JV, cwd=J)[1])            # PM kill
    rec(W2, PROJ, status='idle', labels={'paseo.parent-agent-id': PM, 'jarvis.task': 'W-2'})
    assert denied(run('agent_guard.py', close('archive_agent', PM), JV, cwd=J)[1])         # 열린 Worker
    rec(W2, PROJ, status='closed', labels={'paseo.parent-agent-id': PM, 'jarvis.task': 'W-2'})
    assert not denied(run('agent_guard.py', close('archive_agent', PM), JV, cwd=J)[1])     # 옛 PM archive 허용
    assert not denied(run('agent_guard.py', close('archive_agent', W2), PM2)[1])           # 이전 PM 의 Worker 정리
    W3 = 'dddddddd-0000-0000-0000-000000000001'
    rec(W3, J, status='idle', labels={'paseo.parent-agent-id': OTHER})
    assert denied(run('agent_guard.py', close('archive_agent', W3), PM2)[1])               # 남의 에이전트
    run('agent_guard.py', dict(close('archive_agent', PM), hook_event_name='PostToolUse',
                               tool_response={'archivedAt': 'x'}), JV, cwd=J)
    assert events()[-1]['closed_by'] == 'jarvis' and events()[-1]['agent_id'] == PM

    # ---- Stop 사용량 ----
    tr = os.path.join(TMP, 't.jsonl')
    w(tr, json.dumps({'type': 'assistant', 'message': {'usage': {
        'input_tokens': 2, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 10, 'output_tokens': 5}}})
      + '\n' + json.dumps({'type': 'user', 'message': {}}) + '\n')
    run('pm_usage.py', {'hook_event_name': 'Stop', 'transcript_path': tr, 'session_id': 's'}, PM2)
    snap = json.load(open(os.path.join(J, 'state', 'sessions', PM2 + '.json'), encoding='utf-8'))
    assert snap['context_tokens'] == 1017 and snap['project_id'] == 'nipa'
    assert ctl('usage', PM2)[1]['context_tokens'] == 1017
    run('pm_usage.py', {'hook_event_name': 'Stop', 'transcript_path': tr}, W1)
    assert not os.path.exists(os.path.join(J, 'state', 'sessions', W1 + '.json'))

    # ---- SessionEnd·PreCompact ----
    n = len(events())
    run('session_event.py', {'hook_event_name': 'PreCompact', 'trigger': 'auto', 'transcript_path': tr}, PM2)
    run('session_event.py', {'hook_event_name': 'SessionEnd', 'reason': 'other'}, PM2)
    assert [e['event'] for e in events()[n:]] == ['pre_compact', 'session_end']

    # ---- 대조: 사용자가 PM 을 archive ----
    rec(PM2, PROJ, labels=PMLB, archived='2026-11-09T01:00:00Z')
    c, out, _ = run('pm_reconcile.py', {'hook_event_name': 'UserPromptSubmit'}, JV, cwd=J)
    assert 'nipa PM' in out and '열린 작업 0건' in out
    assert events()[-1]['source'] == 'reconcile' and events()[-1]['closed_by'] == 'unknown'
    c, out, _ = run('pm_reconcile.py', {'hook_event_name': 'UserPromptSubmit'}, JV, cwd=J)
    assert out.strip() == ''                                                               # 한 번만
    c, out, _ = run('pm_reconcile.py', {'hook_event_name': 'UserPromptSubmit'}, OTHER, cwd=J)
    assert out.strip() == ''                                                               # 이미 다른 창이 알림

    # ---- 대표 JARVIS 전환 ----
    assert ctl('claim-jarvis', OTHER, agent=OTHER)[0] == 1                                 # 대표 살아 있음
    assert ctl('claim-jarvis', OTHER, agent=JV)[0] == 1                                    # 남을 대표로 올리지 못함
    assert ctl('transfer-jarvis', '--to', 'next', agent=PM)[0] == 1                        # PM 은 못 씀
    assert ctl('transfer-jarvis', '--to', 'next', agent=JV)[0] == 0
    code, res = ctl('claim-jarvis', OTHER, agent=OTHER)
    assert code == 0 and registry(J)['jarvis'] == OTHER, res

    # ---- 쓰기 권한 ----
    f = {'tool_input': {'file_path': os.path.join(J, 'state', 'x.md')}, 'cwd': J}
    assert shared_block(f, J, JV) is None                                                  # 공유 파일은 어느 창이든
    r = {'tool_input': {'file_path': os.path.join(J, 'registry.yaml')}, 'cwd': J}
    assert shared_block(r, J, OTHER) == MSG_REG and shared_block(r, J, '') is None          # Paseo 밖 사용자는 허용
    nt = {'tool_input': {'file_path': os.path.join(J, 'state', 'tasks', 'W-20990101-01.md')}, 'cwd': J}
    assert shared_block(nt, J, JV) == MSG_NEW                                               # 새 작업 파일 직접 생성 금지
    ex = {'tool_input': {'file_path': os.path.join(J, 'state', 'tasks', 'W-20261109-01.md')}, 'cwd': J}
    assert shared_block(ex, J, JV) is None                                                  # 있는 작업 파일 고치기는 허용
    assert shared_block({'tool_input': {'file_path': os.path.join(TMP, 'a')}, 'cwd': J}, J, JV) is None

    # ---- 인계 노트 이력 ----
    note = os.path.join(TMP, 'note.md')
    w(note, '새 노트\n')
    assert ctl('handoff', 'nipa', note, agent=OTHER)[0] == 0
    hd = os.path.join(J, 'state', 'handoff', 'nipa')
    assert open(os.path.join(hd, 'latest.md'), encoding='utf-8').read() == '새 노트\n'
    assert len([x for x in os.listdir(hd) if x[0].isdigit()]) == 1
    assert ctl('handoff', 'nipa', note, agent=OTHER)[0] == 0
    assert len([x for x in os.listdir(hd) if x[0].isdigit()]) == 2                         # 같은 초도 덮지 않음

    # ---- 중단 뒤 재개: registry 는 바뀌었는데 기록·lock 정리 전에 끊긴 경우 ----
    import time as _t
    from datetime import datetime as _dt, timezone as _tz
    PM3 = 'eeeeeeee-0000-0000-0000-000000000001'
    rec(PM3, PROJ, labels=PMLB)
    assert ctl('lock', 'acquire', 'nipa', '--reason', 'manual', agent=OTHER)[0] == 0
    assert ctl('lock', 'candidate', 'nipa', PM3, agent=OTHER)[0] == 0
    txt = open(os.path.join(J, 'registry.yaml'), encoding='utf-8').read()
    w(os.path.join(J, 'registry.yaml'), txt.replace('coordinator: ' + PM2, 'coordinator: ' + PM3))   # 전환만 된 상태
    code, res = ctl('lock', 'release', 'nipa', agent=OTHER)
    assert code == 1 and 'switch' in res['why'], res                                       # 반쯤 끝난 전환은 release 거절
    code, res = ctl('switch', 'nipa', PM3, agent=OTHER)
    assert code == 0 and res['ok'] and not os.path.exists(os.path.join(hd, 'rotating.lock')), res   # 이어서 마무리

    # ---- 만료된 생성 예약: 그 사이 만들어진 PM 이 있으면 새로 만들지 않음 ----
    PM4 = 'eeeeeeee-0000-0000-0000-000000000002'
    assert ctl('lock', 'acquire', 'nipa', '--reason', 'manual', agent=OTHER)[0] == 0
    lp = os.path.join(hd, 'rotating.lock')
    lk = json.load(open(lp, encoding='utf-8'))
    lk['reserving'] = _t.time() - 400
    w(lp, json.dumps(lk))
    w(os.path.join(P, 'agents', 'x', PM4 + '.json'), json.dumps(
        {'id': PM4, 'cwd': PROJ, 'lastStatus': 'idle', 'labels': PMLB,
         'createdAt': _dt.now(_tz.utc).isoformat().replace('+00:00', 'Z')}))
    c, out, _ = run('agent_guard.py', create('nipa-pm', PMLB), OTHER, cwd=J)
    assert denied(out) and 'lock candidate' in out, out
    assert ctl('lock', 'release', 'nipa', agent=OTHER)[0] == 0

    # ---- 죽은 프로세스가 남긴 잠금은 치우고, 남의 잠금은 지우지 않음 ----
    from jarvis_state import FileLock
    stale = os.path.join(TMP, 'z')
    w(stale + '.lock', '999999 deadbeef')
    with FileLock(stale, timeout=2):
        pass
    assert not os.path.exists(stale + '.lock')
    w(stale + '.lock', '%d other-token' % os.getpid())
    try:
        with FileLock(stale, timeout=0.3):
            raise AssertionError('살아 있는 잠금을 잡으면 안 됨')
    except TimeoutError:
        pass
    assert os.path.exists(stale + '.lock')

    # ---- Linear retry: 항목별 예약, 성공·충돌·실패 반영 (네트워크 없이) ----
    os.environ['JARVIS_DIR'] = J
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import importlib
    import linear as lin
    importlib.reload(lin)
    rows = [{'id': 'a', 'op': 'comment', 'args': {'cmd': 'comment', 'issue': 'PRO-1', 'body': 'x', 'key': None,
                                                  'dry_run': False}, 'done': False},
            {'id': 'b', 'op': 'state', 'args': {'cmd': 'state', 'issue': 'PRO-2', 'state': 'Done', 'expect_from': 'Todo',
                                                'dry_run': False}, 'done': False},
            {'id': 'c', 'op': 'comment', 'args': {'cmd': 'comment', 'issue': 'PRO-3', 'body': 'y', 'key': None,
                                                  'dry_run': False}, 'done': False}]
    lin.write_pending(rows)

    def fake_comment(ns):
        if ns.issue == 'PRO-3':
            raise lin.LinearError('연결 실패')
        return {'ok': True}

    def fake_state(ns):
        raise lin.Conflict('상태가 바뀜')
    lin.WRITES.update(comment=fake_comment, state=fake_state)
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        lin.cmd_retry(None)
    got = {r['id']: r for r in lin.read_pending()}
    assert got['a']['done'] and got['b']['done'] and 'conflict' in got['b']['result'], got
    assert not got['c']['done'] and got['c']['error'] and not got['c'].get('claim'), got
    # ---- 번호 발급·결정 중복·프로젝트 등록 (여러 창이 동시에 써도 겹치지 않게) ----
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(4) as ex_:
        ids = list(ex_.map(lambda i: ctl('new-task', 'nipa', '--title', '시험%d' % i, agent=[JV, OTHER][i % 2])[1]['id'],
                           range(6)))
    assert len(set(ids)) == 6, ids                                                         # 동시에 6개, 번호 안 겹침
    t1 = open(os.path.join(J, 'state', 'tasks', ids[0] + '.md'), encoding='utf-8').read()
    assert 'status: assigned' in t1 and 'project: nipa' in t1 and 'jarvis: ' in t1
    assert ctl('new-task', 'nipa', '--title', 'x', agent=PM)[0] == 1                       # PM 은 못 만듦
    code, d1 = ctl('new-decision', 'nipa', '--question', 'q?', '--task', ids[0], '--option', '① a', '--option', '② b')
    assert code == 0 and d1['id'].startswith('D-')
    code, d2 = ctl('new-decision', 'nipa', '--question', 'q? ', '--task', ids[0], agent=OTHER)
    assert code == 0 and d2.get('existing') and d2['ids'] == [d1['id']], d2                # 같은 질문은 재사용
    code, d3 = ctl('new-decision', 'nipa', '--question', '다른 질문', '--task', ids[0], agent=OTHER)
    assert code == 0 and not d3.get('existing') and d3['id'] != d1['id'], d3              # 다른 질문은 새 결정
    # 결정 확정은 한 창만 성공(CAS)
    code, r1 = ctl('decide', d1['id'], '--decision', '①', '--reason', '사용자가 ① 선택')
    assert code == 0, r1
    code, r2 = ctl('decide', d1['id'], '--decision', '②', agent=OTHER)
    assert code == 1 and r2['decision'] == '①', r2                                        # 늦은 창은 거절, 기존 답 확인
    dtext = open(os.path.join(J, 'state', 'decisions', d1['id'] + '.md'), encoding='utf-8').read()
    assert 'status: decided' in dtext and 'decision: ①' in dtext and '사용자가 ① 선택' in dtext, dtext
    # 공유 자원 점유: 확인과 등록이 한 잠금 안
    assert ctl('occupy', 'langflow-dev:WF4', '--project', 'nipa', '--task', 'W-A', '--agent', PM3)[0] == 0
    code, oc = ctl('occupy', 'langflow-dev:WF4', '--project', 'ask', '--task', 'W-B', agent=OTHER)
    assert code == 1 and oc['wait']['task'] == 'W-A', oc                                  # 다른 창도 같은 결과
    rec(PM3, PROJ, labels=PMLB, archived='2026-11-09T02:00:00Z')                          # 점유한 PM 이 사라지면 회수
    code, oc = ctl('occupy', 'langflow-dev:WF4', '--project', 'ask', '--task', 'W-B', agent=OTHER)
    assert code == 0 and oc['reclaimed'][0]['task'] == 'W-A', oc
    assert ctl('vacate', 'langflow-dev:WF4', '--task', 'W-B')[0] == 0
    rec(PM3, PROJ, labels=PMLB)
    # 점유한 PM 이 사라져도 그 Worker 가 열려 있으면 회수하지 않음
    HOLD, HW = '12345678-0000-0000-0000-000000000001', '12345678-0000-0000-0000-000000000002'
    rec(HOLD, PROJ, labels=PMLB)
    assert ctl('occupy', 'db:dev', '--project', 'nipa', '--task', 'W-H', '--agent', HOLD)[0] == 0
    rec(HOLD, PROJ, labels=PMLB, archived='2026-11-09T03:00:00Z')
    rec(HW, PROJ, status='running', labels={'paseo.parent-agent-id': HOLD, 'jarvis.task': 'W-H'})
    code, oc = ctl('occupy', 'db:dev', '--project', 'ask', '--task', 'W-I', agent=OTHER)
    assert code == 1 and oc['wait']['task'] == 'W-H', oc
    rec(HW, PROJ, status='closed', labels={'paseo.parent-agent-id': HOLD, 'jarvis.task': 'W-H'})
    code, oc = ctl('occupy', 'db:dev', '--project', 'ask', '--task', 'W-I', agent=OTHER)
    assert code == 0 and oc['reclaimed'][0]['task'] == 'W-H', oc
    # 점유 파일이 깨지면 허가하지 않음
    occ = os.path.join(J, 'state', 'occupancy.json')
    good = open(occ, encoding='utf-8').read()
    w(occ, '{깨진')
    assert ctl('occupy', 'x', '--project', 'nipa', '--task', 'W-Z')[0] == 1
    w(occ, good)
    # 결정 파일은 직접 못 고침, 설명은 decision-note, 프로젝트가 다르면 같은 질문도 새 결정
    dpath = {'tool_input': {'file_path': os.path.join(J, 'state', 'decisions', d1['id'] + '.md')}, 'cwd': J}
    assert shared_block(dpath, J, JV) is not None
    assert ctl('decision-note', d1['id'], '참고 한 줄')[0] == 0
    assert '참고 한 줄' in open(os.path.join(J, 'state', 'decisions', d1['id'] + '.md'), encoding='utf-8').read()
    code, dq = ctl('new-decision', 'ask', '--question', '같은 질문')
    code, dr = ctl('new-decision', 'nipa', '--question', '같은 질문')
    assert not dr.get('existing') and dr['id'] != dq['id'], dr
    # PM 은 기본 수신자가 될 수 없음
    assert ctl('claim-jarvis', PM3, agent=PM3)[0] == 1
    oc_path = {'tool_input': {'file_path': os.path.join(J, 'state', 'occupancy.json')}, 'cwd': J}
    assert shared_block(oc_path, J, JV) is not None                                        # 직접 수정 금지
    # 교체 lock 은 잡은 창만 진행, 넘겨받기는 기록
    assert ctl('lock', 'acquire', 'nipa', '--reason', 'manual', agent=JV)[0] == 0
    assert ctl('lock', 'stage', 'nipa', 'provisioning', agent=OTHER)[0] == 1
    assert denied(run('agent_guard.py', create('nipa-pm', PMLB), OTHER, cwd=J)[1])         # 남의 교체에 PM 생성 금지
    assert ctl('lock', 'stage', 'nipa', 'provisioning', '--takeover', agent=OTHER)[0] == 0
    assert events()[-1]['event'] == 'lock_takeover'
    assert ctl('lock', 'release', 'nipa', agent=OTHER)[0] == 0
    # 검토·schedule 세션은 JARVIS 폴더에 있어도 jarvisctl 못 씀
    RV = 'ffffffff-0000-0000-0000-000000000001'
    rec(RV, J, labels={'jarvis.role': 'reviewer'})
    assert ctl('new-task', 'nipa', '--title', 'x', agent=RV)[0] == 1
    # 프로젝트 등록·필드: pm_title 기본값, set 으로 변경, 그 제목도 PM 생성으로 판정
    assert ctl('register', 'docs', '--root', 'C:/x/docs', '--linear-project', 'DOCS')[0] == 0
    rg = registry(J)['projects']['docs']
    assert rg['linear_project'] == 'DOCS' and rg['pm_title'] == 'docs-pm', rg
    assert ctl('register', 'docs', '--root', 'C:/x/docs')[0] == 1                          # 중복 등록 거절
    assert ctl('set', 'docs', 'pm_title', '문서 PM')[0] == 0
    assert ctl('set', 'docs', 'coordinator', 'x')[0] == 1                                  # coordinator 는 switch 로만
    assert registry(J)['projects']['docs']['pm_title'] == '문서 PM'
    assert registry(J)['projects']['nipa']['coordinator'] == PM3                           # 다른 항목은 그대로
    assert denied(run('agent_guard.py', create('문서 PM'), JV, cwd=J)[1])                  # 라벨 없는 PM 제목도 잡음
    # Worker 이름은 하는 일 위주
    for bad in ('nipa-w-W-20261109-01', 'worker-1', 'W-20261109-01', '쿼리확인'):
        assert denied(run('agent_guard.py', create(bad, {'jarvis.task': 'W-1'}), PM3)[1]), bad
    assert not denied(run('agent_guard.py', create('wf3-쿼리확인', {'jarvis.task': 'W-1'}), PM3)[1])
    # 기본 수신자 넘김: claim 이 넘김 파일을 소비
    assert ctl('transfer-jarvis', '--to', 'next', agent=OTHER)[0] == 0
    assert ctl('claim-jarvis', JV, agent=JV)[0] == 0 and registry(J)['jarvis'] == JV
    assert not os.path.exists(os.path.join(J, 'state', 'handoff', 'manager', 'transfer.json'))

    # ---- Obsidian 현황 노트: JARVIS 창이 답을 마칠 때 state 가 바뀌었으면만 다시 만듦 ----
    import time as _t2
    os.environ['JARVIS_WORKLOG'] = os.path.join(TMP, 'worklog')
    note = os.path.join(TMP, 'worklog', 'wiki', 'jarvis', '현황.md')
    run('obsidian_sync.py', {'hook_event_name': 'Stop'}, PM3)                              # PM 에서는 안 돎
    assert not os.path.exists(note)
    run('obsidian_sync.py', {'hook_event_name': 'Stop'}, JV, cwd=J)
    assert os.path.exists(note) and '자동 생성' in open(note, encoding='utf-8').read()
    dec = open(os.path.join(TMP, 'worklog', 'wiki', 'jarvis', '결정.md'), encoding='utf-8').read()
    assert '열린 결정' in dec
    m1 = os.path.getmtime(note)
    _t2.sleep(1.1)
    run('obsidian_sync.py', {'hook_event_name': 'Stop'}, JV, cwd=J)
    assert os.path.getmtime(note) == m1                                                     # 바뀐 것 없으면 안 씀
    code, nt2 = ctl('new-task', 'nipa', '--title', '노트 갱신 시험')
    run('obsidian_sync.py', {'hook_event_name': 'Stop'}, JV, cwd=J)
    assert os.path.getmtime(note) > m1 and '노트 갱신 시험' in open(note, encoding='utf-8').read()
    print('ok')
finally:
    shutil.rmtree(TMP, ignore_errors=True)
