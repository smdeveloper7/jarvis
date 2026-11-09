#!/usr/bin/env python3
"""PreToolUse·PostToolUse 훅: Paseo 에이전트 생성·닫기를 역할별로 막고 기록한다 (docs/02 3·4·7절).

matcher: mcp__paseo__create_agent | mcp__paseo__archive_agent | mcp__paseo__kill_agent

막는 것 (PreToolUse)
- JARVIS 창은 여러 개일 수 있고 모두 같은 규칙을 따른다(registry jarvis: 는 기본 수신자일 뿐).
- JARVIS 가 PM 생성: 라벨 jarvis.role=pm·jarvis.project 필수. 활성 PM 이 열려 있는데 교체 lock 이 없으면,
  또는 lock 의 후보가 이미 열려 있으면 막는다(중복 PM).
- JARVIS 의 archive·kill: 기본 수신자 JARVIS, registry 의 활성 PM, 열린 Worker 가 있는 에이전트는 막는다.
  PM 은 kill 대신 archive.
- PM: 후보(아직 registry 전) 이거나 교체 lock 중이면 Worker 생성을 막는다. Worker 에는 라벨 jarvis.task 필수.
  자기 Worker 가 아닌 에이전트는 닫지 못한다.
기록 (PostToolUse)
- PM 생성 성공: `paseo agent detach` 로 JARVIS 의 자식에서 떼어 낸다(JARVIS 를 닫아도 PM 이 남게).
  lock 이 있으면 candidate_id 와 detach 결과를 적는다.
- archive·kill 성공: sessions.jsonl 에 closed_by jarvis|pm.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time  # noqa: E402

from jarvis_state import (FileLock, Unknown, append_jsonl, current_role, deny, is_open, jarvis_dir,  # noqa: E402
                          labels, lock_path, now_iso, open_children_checked, paseo_record, paseo_records, read_input,
                          read_json, state, stdio_utf8, write_json)
from datetime import datetime, timezone  # noqa: E402

RESERVE_S = 180   # PM 생성 예약 유효 시간. PostToolUse 가 후보를 적으면 풀린다

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def pm_target(ti, reg):
    """create_agent 입력이 PM 생성이면 프로젝트 키. 라벨이 없어도 제목이 registry pm_title·<키>-pm·<키>-coord 면 PM."""
    lb = ti.get('labels') or {}
    if lb.get('jarvis.role') == 'pm':
        return lb.get('jarvis.project') or '?'
    title = (ti.get('title') or '').strip()
    for k, p in reg['projects'].items():
        if title in (k + '-pm', k + '-coord') or (p.get('pm_title') and title == p['pm_title']):
            return k
    return None


ID_ONLY = re.compile(r'W-\d{8}-\d+|^(worker|w|agent|task)[-_ ]?\d*$|^[0-9a-f-]{6,}$', re.I)


def worker_title_problem(title, proj):
    """Worker 제목은 맡긴 일이 보이게 '<영역>-<작업 이름>'. 작업 번호·순번만 있는 이름은 막는다."""
    t = (title or '').strip()
    if not t:
        return 'Worker 제목이 비어 있다'
    if ID_ONLY.search(t) or t.startswith(proj + '-w-'):
        return 'Worker 제목에 작업 번호·순번 대신 하는 일을 쓴다(작업 번호는 라벨 jarvis.task 에)'
    if '-' not in t or not re.search(r'[^\W\d_]', t.split('-', 1)[1]):
        return "Worker 제목은 '<영역>-<작업 이름>' 꼴로 짓는다(예: wf3-쿼리확인, 평가-검토화면)"
    return None


def check_jarvis_create(ti, reg, jdir):
    proj = pm_target(ti, reg)
    if proj is None:
        return None
    lb = ti.get('labels') or {}
    if lb.get('jarvis.role') != 'pm' or lb.get('jarvis.project') != proj:
        return 'PM 은 labels {"jarvis.role":"pm","jarvis.project":"%s"} 를 붙여 만든다(CLAUDE.md "PM 띄우기").' % proj
    if proj not in reg['projects']:
        return 'registry.yaml 에 없는 프로젝트 %s. 먼저 등록한다.' % proj
    lp = lock_path(jdir, proj)
    active = reg['projects'][proj].get('coordinator')
    if not os.path.exists(lp):
        if active and is_open(paseo_record(active)):
            return ('%s 의 활성 PM(%s)이 열려 있다. 중복 PM 을 만들지 않는다. 교체라면 '
                    'scripts/jarvisctl.py lock acquire %s 부터.' % (proj, active[:8], proj))
        return 'PM 을 만들기 전에 scripts/jarvisctl.py lock acquire %s --reason new|crash 로 lock 을 잡는다.' % proj
    with FileLock(lp, timeout=5):           # 같은 lock 에 create 두 개가 동시에 오면 하나만 예약
        lock = read_json(lp)
        if lock is None:
            return 'lock 을 읽지 못했다. 다시 시도.'
        if lock.get('stage') == 'switched':
            return '이미 registry 를 바꾼 lock 이다. jarvisctl switch 를 다시 실행해 마무리한다.'
        cand = lock.get('candidate_id')
        if cand and is_open(paseo_record(cand)):
            return '%s 교체 lock 에 이미 후보 PM(%s)이 있다. 그 세션을 검증하거나 archive 한 뒤 다시.' % (proj, cand[:8])
        owner = lock.get('owner')
        me = os.environ.get('PASEO_AGENT_ID', '').strip()
        if owner and me and owner != me and is_open(paseo_record(owner)):
            return ('%s 교체는 다른 JARVIS 창(%s)이 진행 중이다. 그 창에서 마치거나 jarvisctl lock stage %s … '
                    '--takeover 로 넘겨받은 뒤 다시.' % (proj, owner[:8], proj))
        rs = lock.get('reserving')
        if rs and time.time() - rs < RESERVE_S:
            return '%s 후보 PM 을 만드는 중이다(%d초 전 예약). 결과를 기다린다.' % (proj, time.time() - rs)
        if rs:   # 예약이 만료됨: 그 사이 실제로 만들어진 PM 이 있으면 회수 먼저(응답·PostToolUse 유실 대비)
            made = made_since(proj, rs)
            if made:
                return ('예약 뒤 만들어진 %s PM %s 가 있다. 그것을 쓰려면 jarvisctl lock candidate %s %s, 아니면 '
                        'archive 한 뒤 다시.' % (proj, made[0][:8], proj, made[0]))
        lock.update(reserving=time.time(), updated_at=now_iso())
        write_json(lp, lock)
    return None


def made_since(proj, since_epoch):
    """since_epoch 이후 만들어진, 닫히지 않은 그 프로젝트 PM 라벨 에이전트 ID 들."""
    out = []
    for r in paseo_records():
        lb = labels(r)
        if lb.get('jarvis.role') != 'pm' or lb.get('jarvis.project') != proj or not is_open(r):
            continue
        try:
            t = datetime.fromisoformat((r.get('createdAt') or '').replace('Z', '+00:00')).timestamp()
        except ValueError:
            continue
        if t >= since_epoch - 5:
            out.append(r['id'])
    return out


def check_close(tool, target, role, me, reg):
    if not target:
        return None
    if role in ('jarvis', 'jarvis_other'):
        if target == reg.get('jarvis'):
            return ('기본 수신자 JARVIS(registry jarvis:)다. 먼저 다른 창에서 jarvisctl claim-jarvis 로 넘긴 뒤 닫는다'
                    '(옛 창이 닫히면 claim 이 허용된다).')
        for k, p in reg['projects'].items():
            if p.get('coordinator') == target:
                return ('%s 의 활성 PM 이다. 교체 절차(jarvisctl switch)로 registry 를 바꾼 뒤 닫는다.' % k)
        rec = paseo_record(target)
        if tool.endswith('kill_agent') and labels(rec).get('jarvis.role') == 'pm':
            return 'PM 은 kill 하지 않고 archive 한다(기록 보존).'
        try:
            kids = open_children_checked(target)
        except Unknown as e:
            return '%s. 다시 시도한다.' % e
        if kids:
            return '열린 Worker %d개(%s)가 있다. Worker 가 모두 닫힌 뒤 닫는다.' % (
                len(kids), ', '.join(k['id'][:8] for k in kids))
        return None
    if role == 'pm':
        parent = labels(paseo_record(target)).get('paseo.parent-agent-id')
        if parent == me:
            return None
        # 장애 복구: 같은 프로젝트의 이전 PM(registry 의 활성 PM 이 아님)이 남긴 Worker 는 새 PM 이 정리할 수 있다
        plb = labels(paseo_record(parent)) if parent else {}
        proj = labels(paseo_record(me)).get('jarvis.project')
        active = [p.get('coordinator') for p in reg['projects'].values()]
        if parent and plb.get('jarvis.role') == 'pm' and plb.get('jarvis.project') == proj and parent not in active:
            return None
        return 'PM 은 자기가 만든 Worker 와 같은 프로젝트 이전 PM 의 Worker 만 닫는다.'
    return None


def pre(inp, jdir):
    agent_id, role, proj, reg, _ = current_role(inp, jdir)
    tool = inp.get('tool_name') or ''
    ti = inp.get('tool_input') or {}
    why = None
    if tool.endswith('create_agent'):
        if role in ('jarvis', 'jarvis_other'):
            why = check_jarvis_create(ti, reg, jdir)
        elif role == 'pm':
            if reg['projects'].get(proj, {}).get('coordinator') != agent_id:
                why = '아직 registry 에 연결되지 않은 후보 PM 이다. [준비] 로 답하고 [배정] 을 기다린다.'
            elif os.path.exists(lock_path(jdir, proj)):
                why = '%s 는 PM 교체 중(rotating.lock)이다. 새 Worker 를 띄우지 않는다.' % proj
            elif pm_target(ti, reg) is not None:
                why = 'PM 은 PM 을 만들지 않는다.'
            elif not (ti.get('labels') or {}).get('jarvis.task'):
                why = 'Worker 에는 labels {"jarvis.task":"<W-ID>"} 를 붙인다(rules/coordinator.md).'
            else:
                why = worker_title_problem(ti.get('title'), proj)
    elif tool.endswith('archive_agent') or tool.endswith('kill_agent'):
        why = check_close(tool, ti.get('agentId'), role, agent_id, reg)
    if why:
        deny(why)
    return 0


def created_id(resp):
    if isinstance(resp, dict):
        for k in ('agentId', 'id'):
            if isinstance(resp.get(k), str) and UUID.fullmatch(resp[k]):
                return resp[k]
    m = re.search(r'"agentId"\s*:\s*"(%s)"' % UUID.pattern, str(resp))
    return m.group(1) if m else None


def detach(agent_id):
    """Paseo 가 JARVIS 의 subagent 로 만든 PM 을 독립시킨다. 성공하면 True."""
    cli = os.environ.get('PASEO_CLI') or 'paseo'
    cmd = [cli, 'agent', 'detach', agent_id]
    if os.name == 'nt' and cli.lower().endswith(('.cmd', '.bat')):
        cmd = ['cmd', '/c'] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def post(inp, jdir):
    agent_id, role, proj, reg, _ = current_role(inp, jdir)
    if role not in ('jarvis', 'jarvis_other', 'pm'):
        return 0
    if role == 'jarvis_other':
        role = 'jarvis'
    tool = inp.get('tool_name') or ''
    ti = inp.get('tool_input') or {}
    resp = inp.get('tool_response')
    if tool.endswith('create_agent') and role == 'jarvis':
        target = pm_target(ti, reg)
        new = created_id(resp)
        if target and new:
            ok = detach(new) if os.environ.get('JARVIS_HOOK_NO_DETACH') != '1' else None
            lp = lock_path(jdir, target)
            if os.path.exists(lp):
                with FileLock(lp, timeout=5):
                    lock = read_json(lp)
                    cur = (lock or {}).get('candidate_id')
                    if lock is not None and (not cur or cur == new or not is_open(paseo_record(cur))):
                        lock.update(candidate_id=new, stage='verifying', detached=ok, reserving=None,
                                    updated_at=now_iso())
                        write_json(lp, lock)
                    elif lock is not None:      # 늦게 온 다른 생성 결과: 현재 후보를 덮지 않고 남겨 둔다
                        lock.setdefault('extra_created', []).append(new)
                        write_json(lp, lock)
    elif tool.endswith('archive_agent') or tool.endswith('kill_agent'):
        if 'error' in str(resp).lower()[:200]:
            return 0
        target = ti.get('agentId')
        rec = paseo_record(target)
        append_jsonl(state(jdir, 'sessions.jsonl'), {
            'timestamp': now_iso(), 'event': 'closed',
            'project_id': labels(rec).get('jarvis.project') or proj, 'agent_id': target,
            'closed_by': role, 'how': 'kill' if tool.endswith('kill_agent') else 'archive',
            'source': 'hook:agent_guard'})
    return 0


EVENT = {'name': ''}


def main():
    stdio_utf8()
    inp = read_input()
    EVENT['name'] = inp.get('hook_event_name') or ''
    jdir = jarvis_dir()
    if not jdir:
        return 0
    if EVENT['name'] == 'PostToolUse':
        return post(inp, jdir)
    return pre(inp, jdir)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        # 관리 대상 세션(Paseo + JARVIS 설정)의 PreToolUse 에서는 막는다: 가드가 고장 난 채 통과시키지 않는다
        if os.environ.get('PASEO_AGENT_ID') and jarvis_dir() and EVENT['name'] == 'PreToolUse':
            try:
                deny('agent_guard 훅 오류로 막았다: %s. JARVIS 에게 알린다.' % type(e).__name__)
            except Exception:  # noqa: BLE001
                pass
        sys.exit(0)
