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
from jarvis_only_write import not_representative  # noqa: E402
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
    assert '보조 세션' in out
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart'}, W1)
    assert out.strip() == ''
    c, out, _ = run('session_context.py', {'hook_event_name': 'SessionStart'}, '')
    assert out.strip() == ''

    # ---- create_agent 가드 ----
    assert denied(run('agent_guard.py', create('nipa-pm'), JV, cwd=J)[1])                  # 라벨 없음
    assert denied(run('agent_guard.py', create('nipa-pm', PMLB), JV, cwd=J)[1])            # 활성 PM 열림, lock 없음
    assert denied(run('agent_guard.py', create('x', PMLB), OTHER, cwd=J)[1])               # 보조 JARVIS
    assert not denied(run('agent_guard.py', create('wf3-일'), JV, cwd=J)[1])               # PM 아닌 생성은 통과
    code, lk = ctl('lock', 'acquire', 'nipa')
    assert code == 0 and lk['lock']['old_agent_id'] == PM
    assert ctl('lock', 'acquire', 'nipa')[0] == 1                                          # 배타적
    assert ctl('lock', 'acquire', 'nipa', agent=OTHER)[0] == 1                              # 대표만 lock
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
    assert ctl('switch', 'nipa', PM2, agent=OTHER)[0] == 1                                 # 대표만
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
    assert out.strip() == ''                                                               # 대표만

    # ---- 대표 JARVIS 전환 ----
    assert ctl('claim-jarvis', OTHER, agent=OTHER)[0] == 1                                 # 대표 살아 있음
    assert ctl('claim-jarvis', OTHER, agent=JV)[0] == 1                                    # 남을 대표로 올리지 못함
    assert ctl('transfer-jarvis', '--to', 'next', agent=OTHER)[0] == 1                     # 대표 아님
    assert ctl('transfer-jarvis', '--to', 'next', agent=JV)[0] == 0
    code, res = ctl('claim-jarvis', OTHER, agent=OTHER)
    assert code == 0 and registry(J)['jarvis'] == OTHER, res

    # ---- 쓰기 권한 ----
    f = {'tool_input': {'file_path': os.path.join(J, 'state', 'x.md')}, 'cwd': J}
    assert not_representative(f, J, JV, OTHER)
    assert not not_representative(f, J, OTHER, OTHER)
    assert not not_representative(f, J, '', OTHER)                                         # Paseo 밖 세션
    assert not not_representative(f, J, JV, OTHER, schedule=True)
    assert not not_representative({'tool_input': {'file_path': os.path.join(TMP, 'a')}, 'cwd': J}, J, JV, OTHER)

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
    print('ok')
finally:
    shutil.rmtree(TMP, ignore_errors=True)
