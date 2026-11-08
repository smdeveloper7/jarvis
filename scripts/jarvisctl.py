#!/usr/bin/env python3
"""JARVIS 수명주기 명령 (docs/02). 대표 JARVIS 가 Bash 로 부른다. 판단은 JARVIS, 상태 전환은 이 코드가 원자적으로 한다.

  usage <에이전트ID>                 컨텍스트 토큰 (Paseo 기록 → Stop Snapshot → transcript)
  can-rotate <프로젝트>               교체 조건 점검(JSON)
  lock acquire <프로젝트> [--reason context|crash|manual|new]
  lock show <프로젝트>
  lock stage <프로젝트> <handoff|provisioning|verifying>
  lock candidate <프로젝트> <후보 ID>  훅이 후보를 못 적었을 때 손으로
  lock release <프로젝트> [--result failed:<단계>]
  switch <프로젝트> <새 PM ID>        검증 후 registry coordinator 를 CAS 로 바꾸고 lock 해제(다시 실행해도 안전)
  handoff <프로젝트|manager> <파일>   인계 노트를 latest.md 로 (이전 것은 이력으로)
  hook-check <에이전트ID>             SessionStart 훅이 그 세션에 넣은 표식(nonce)
  transfer-jarvis --to <ID|next>      대표 JARVIS 가 넘길 대상 지정
  claim-jarvis <새 JARVIS ID>         대표 JARVIS 전환(CAS)

상태를 바꾸는 명령은 대표 JARVIS(registry jarvis:)만 쓴다. Paseo 밖(사용자 셸, PASEO_AGENT_ID 없음)에서는
사용자 직접 복구로 보고 허용한다. JARVIS 폴더는 이 파일 위치(scripts/..), 다른 경로는 JARVIS_DIR 환경변수.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'hooks'))
from jarvis_state import (FileLock, Unknown, append_jsonl, clean, closed_state, context_tokens,  # noqa: E402
                          labels, now_iso, open_children_checked, open_tasks, paseo_lookup, read_json, registry,
                          write_json)
from pathutil import norm  # noqa: E402

JDIR = os.path.abspath(os.environ.get('JARVIS_DIR') or os.path.join(HERE, '..'))
THRESHOLD = int(os.environ.get('JARVIS_ROTATE_TOKENS') or 200000)
SNAPSHOT_MAX_AGE_S = 6 * 3600
REG = os.path.join(JDIR, 'registry.yaml')


class Refuse(Exception):
    pass


def st(*p):
    return os.path.join(JDIR, 'state', *p)


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return code


def caller():
    return os.environ.get('PASEO_AGENT_ID', '').strip() or None


def require_rep():
    """대표 JARVIS 가 아니면 거절. Paseo 밖(사용자 셸)은 허용."""
    me, rep = caller(), registry(JDIR).get('jarvis')
    if me and rep and me != rep:
        raise Refuse('대표 JARVIS(%s)만 쓴다. 이 세션은 %s' % (rep[:8], me[:8]))


def event(row):
    if not append_jsonl(st('sessions.jsonl'), dict({'timestamp': now_iso()}, **row)):
        raise Refuse('sessions.jsonl 기록 실패(잠금). 다시 실행하면 이어서 한다')
    return row


# ---------- 사용량 ----------

def transcript_of(rec):
    rt = (rec or {}).get('persistence') or {}
    sid = rt.get('sessionId') or ((rec or {}).get('runtimeInfo') or {}).get('sessionId')
    cwd = (rec or {}).get('cwd') or ''
    if not sid or not cwd:
        return None
    p = os.path.join(os.path.expanduser('~/.claude/projects'), re.sub(r'[:\\/]', '-', cwd), sid + '.jsonl')
    return p if os.path.exists(p) else None


def usage(agent_id):
    """(토큰, 출처, 비용). Paseo 기록 → Stop Snapshot(6시간 안) → transcript."""
    _, rec = paseo_lookup(agent_id)
    lu = (rec or {}).get('lastUsage') or {}
    if lu.get('contextWindowUsedTokens'):
        return int(lu['contextWindowUsedTokens']), 'paseo', lu.get('totalCostUsd')
    snap = read_json(st('sessions', agent_id + '.json'))
    if snap and snap.get('context_tokens') is not None:
        try:
            age = (datetime.now().astimezone() - datetime.fromisoformat(snap['observed_at'])).total_seconds()
        except (KeyError, ValueError):
            age = None
        if age is not None and age <= SNAPSHOT_MAX_AGE_S:
            return int(snap['context_tokens']), 'snapshot', None
    tp = transcript_of(rec)
    if tp:
        t = context_tokens(tp, tail=4 << 20)
        if t is not None:
            return t, 'transcript', None
    return None, None, None


def cmd_usage(a):
    t, src, cost = usage(a.agent)
    return out({'agent_id': a.agent, 'context_tokens': t, 'source': src, 'total_cost_usd': cost})


# ---------- 교체 조건 ----------

def busy_tasks(proj):
    return [x.get('id') for x in open_tasks(JDIR, proj) if x.get('status') in ('assigned', 'working', 'question')]


def cmd_can_rotate(a):
    p = registry(JDIR)['projects'].get(a.project)
    if not p:
        return out({'ok': False, 'why': 'registry 에 없는 프로젝트'}, 1)
    aid = p.get('coordinator')
    stt, rec = paseo_lookup(aid)
    t, src, _ = usage(aid) if aid else (None, None, None)
    tasks = busy_tasks(a.project)
    try:
        kids, kids_known = (open_children_checked(aid) if aid else []), True
    except Unknown:
        kids, kids_known = [], False
    lock = read_json(lock_file(a.project))
    checks = {
        'context_over_threshold': t is not None and t > THRESHOLD,
        'pm_idle': stt == 'ok' and rec.get('lastStatus') == 'idle' and not rec.get('archivedAt'),
        'no_open_tasks': not tasks,
        'no_open_workers': kids_known and not kids,
        'no_lock': lock is None,
    }
    return out({'ok': all(checks.values()), 'project': a.project, 'agent_id': aid, 'pm_state': closed_state(aid),
                'context_tokens': t, 'source': src, 'threshold': THRESHOLD, 'checks': checks,
                'open_tasks': tasks, 'open_workers': [k['id'] for k in kids], 'lock': lock})


# ---------- lock ----------

def lock_file(proj):
    return st('handoff', proj, 'rotating.lock')


def update_lock(proj, held=False, **kw):
    """lock 갱신. held=True 면 이미 FileLock 안에서 부른 것."""
    path = lock_file(proj)
    if not held:
        with FileLock(path):
            return update_lock(proj, held=True, **kw)
    lock = read_json(path)
    if lock is None:
        raise Refuse('lock 없음')
    lock.update(kw, updated_at=now_iso())
    write_json(path, lock)
    return lock


def check_candidate(proj, cand):
    """후보가 이 프로젝트 PM 으로 쓸 수 있는지. 문제 목록을 돌려준다."""
    p = registry(JDIR)['projects'].get(proj) or {}
    stt, rec = paseo_lookup(cand)
    if stt != 'ok':
        return ['후보 기록을 읽지 못함(%s)' % stt]
    lb, bad = labels(rec), []
    if rec.get('archivedAt') or rec.get('lastStatus') == 'closed':
        bad.append('후보가 닫혀 있음')
    if lb.get('jarvis.role') != 'pm' or lb.get('jarvis.project') != proj:
        bad.append('라벨 jarvis.role=pm, jarvis.project=%s 아님' % proj)
    if p.get('root') and norm(rec.get('cwd') or '') != norm(p['root']):
        bad.append('작업 폴더가 registry root 와 다름(%s)' % rec.get('cwd'))
    if lb.get('paseo.parent-agent-id'):
        bad.append('아직 detach 안 됨(부모 %s). paseo agent detach %s' % (lb['paseo.parent-agent-id'][:8], cand))
    return bad


def cmd_lock(a):
    path = lock_file(a.project)
    if a.op == 'show':
        return out(read_json(path))
    require_rep()
    if a.op == 'acquire':
        reg = registry(JDIR)
        if a.project not in reg['projects']:
            raise Refuse('registry 에 없는 프로젝트')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = {'project': a.project, 'owner': caller(), 'old_agent_id': reg['projects'][a.project].get('coordinator'),
                'candidate_id': None, 'stage': 'handoff', 'reason': a.reason, 'started_at': now_iso(),
                'updated_at': now_iso()}
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return out({'ok': False, 'why': '이미 교체 중', 'lock': read_json(path)}, 1)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        return out({'ok': True, 'lock': body})
    if a.op == 'stage':
        if a.value not in ('handoff', 'provisioning', 'verifying'):
            raise Refuse('stage 값: handoff|provisioning|verifying')
        return out({'ok': True, 'lock': update_lock(a.project, stage=a.value)})
    if a.op == 'candidate':
        if not a.value:
            raise Refuse('후보 ID 필요')
        return out({'ok': True, 'lock': update_lock(a.project, candidate_id=a.value, stage='verifying',
                                                    reserving=None), 'problems': check_candidate(a.project, a.value)})
    if a.op == 'release':
        with FileLock(path):
            lock = read_json(path)
            if lock is None:
                raise Refuse('lock 없음')
            cur = registry(JDIR)['projects'].get(a.project, {}).get('coordinator')
            if lock.get('stage') == 'switched' or (lock.get('candidate_id') and cur == lock['candidate_id']):
                raise Refuse('registry 가 이미 후보(%s)를 가리킨다. switch %s %s 를 다시 실행해 마무리한다'
                             % (lock.get('candidate_id'), a.project, lock.get('candidate_id')))
            if a.result:
                event({'event': 'rotation', 'project_id': a.project, 'old_agent_id': lock.get('old_agent_id'),
                       'new_agent_id': lock.get('candidate_id'), 'reason': lock.get('reason'), 'result': a.result,
                       'source': 'jarvisctl'})
            os.remove(path)
        return out({'ok': True, 'released': lock})
    return 1


# ---------- registry 쓰기 (잠금 안에서 다시 읽고 비교) ----------

def rewrite_registry(fn):
    with FileLock(REG):
        with open(REG, encoding='utf-8') as f:
            text = f.read()
        new = fn(text)
        if new != text:
            tmp = REG + '.tmp%d' % os.getpid()
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(new)
            os.replace(tmp, REG)


def set_coordinator(text, proj, old, new):
    lines = text.split('\n')
    cur, hit = None, None
    for i, line in enumerate(lines):
        m = re.match(r'^  ([A-Za-z0-9_.-]+):[ \t]*$', line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r'^    coordinator:[ \t]*(.*)$', line)
        if m and cur == proj:
            hit = i
            raw = m.group(1)
            have = clean(raw)
            if have == new:
                return text                       # 이미 바뀜(멱등)
            if have != old:
                raise Refuse('registry 의 %s coordinator(%s)가 lock 의 old(%s)와 다름' % (proj, have, old))
            comment = raw[raw.index(' #'):] if ' #' in raw else ''
            lines[i] = '    coordinator: ' + new + comment
    if hit is None:
        raise Refuse('%s 의 coordinator 줄을 못 찾음' % proj)
    return '\n'.join(lines)


def set_jarvis(text, old, new):
    m = re.search(r'^jarvis:[ \t]*(.*)$', text, flags=re.M)
    if not m:
        return text.rstrip('\n') + '\njarvis: %s\n' % new
    have = clean(m.group(1))
    if have == new:
        return text
    if have != old:
        raise Refuse('registry jarvis 가 바뀜(%s). 다시 시도' % have)
    raw = m.group(1)
    comment = raw[raw.index(' #'):] if ' #' in raw else ''
    return text[:m.start()] + 'jarvis: ' + new + comment + text[m.end():]


# ---------- switch ----------

def cmd_switch(a):
    """검증 → registry 전환 → stage=switched → 기록 → lock 삭제. 전부 프로젝트 lock 파일의 잠금 안에서 한다.
    중간에 끊겨도 다시 실행하면 registry 실제 값을 보고 이어서 끝낸다."""
    require_rep()
    path = lock_file(a.project)
    with FileLock(path, timeout=30):
        lock = read_json(path)
        if not lock:
            raise Refuse('lock 없음. lock acquire 부터')
        if lock.get('candidate_id') != a.new:
            raise Refuse('lock 의 후보(%s)와 다름. 훅이 못 적었으면 lock candidate %s %s'
                         % (lock.get('candidate_id'), a.project, a.new))
        old = lock.get('old_agent_id')
        cur = registry(JDIR)['projects'].get(a.project, {}).get('coordinator')
        if lock.get('stage') != 'switched' and cur != a.new:
            bad = check_candidate(a.project, a.new)
            if lock.get('reason') not in ('crash', 'new'):
                tasks = busy_tasks(a.project)
                if tasks:
                    bad.append('진행 중 작업 %s' % tasks)
                if old:
                    ostt, orec = paseo_lookup(old)
                    if ostt != 'ok':
                        bad.append('옛 PM 기록을 읽지 못함(%s)' % ostt)
                    elif orec.get('lastStatus') == 'running':
                        bad.append('옛 PM 이 실행 중')
                    try:
                        kids = open_children_checked(old)
                    except Unknown as e:
                        kids = []
                        bad.append(str(e))
                    if kids:
                        bad.append('옛 PM 의 Worker 가 열려 있음 %s' % [k['id'][:8] for k in kids])
            if bad:
                raise Refuse('; '.join(bad))
            rewrite_registry(lambda t: set_coordinator(t, a.project, old, a.new))
        lock = update_lock(a.project, held=True, stage='switched')
        t, src, cost = usage(old) if old else (None, None, None)
        ev = event({'event': 'rotation', 'project_id': a.project, 'old_agent_id': old, 'new_agent_id': a.new,
                    'context_tokens_before': t, 'total_cost_usd_before': cost, 'reason': lock.get('reason'),
                    'result': 'switched', 'source': 'jarvisctl', 'lock_started_at': lock.get('started_at')})
        os.remove(path)
    return out({'ok': True, 'event': ev, 'next': '옛 PM 은 새 PM 이 [배정] 하나를 마친 뒤 사용자 승인 후 archive'})


# ---------- 인계 노트 · 훅 표식 ----------

def cmd_handoff(a):
    require_rep()
    d = st('handoff', a.role)
    os.makedirs(d, exist_ok=True)
    latest = os.path.join(d, 'latest.md')
    with FileLock(latest):
        if os.path.exists(latest):
            stamp = datetime.fromtimestamp(os.path.getmtime(latest)).strftime('%Y%m%d-%H%M%S')
            dst, n = os.path.join(d, stamp + '.md'), 1
            while os.path.exists(dst):
                n += 1
                dst = os.path.join(d, '%s-%d.md' % (stamp, n))
            shutil.move(latest, dst)
        shutil.copyfile(a.file, latest + '.tmp')
        os.replace(latest + '.tmp', latest)
    return out({'ok': True, 'latest': latest})


def cmd_hook_check(a):
    h = read_json(st('sessions', a.agent + '.hook.json'))
    return out({'agent_id': a.agent, 'hook': h, 'tag': ('jarvis-hooks v1#' + h['nonce']) if h else None},
               0 if h else 1)


# ---------- 대표 JARVIS ----------

def cmd_transfer(a):
    rep = registry(JDIR).get('jarvis')
    me = caller()
    if me and rep and me != rep:
        raise Refuse('대표 JARVIS 만 넘길 수 있음')
    body = {'from': rep, 'to': a.to, 'at': now_iso(), 'by': me or 'user'}
    write_json(st('handoff', 'manager', 'transfer.json'), body)
    return out({'ok': True, 'transfer': body})


def cmd_claim(a):
    me = caller()
    if me and me != a.new:
        raise Refuse('자기 자신(%s)만 대표로 올릴 수 있음' % me[:8])
    holder = {}

    def fn(text):
        m = re.search(r'^jarvis:[ \t]*(.*)$', text, flags=re.M)
        old = clean(m.group(1)) if m else None
        holder['old'] = old
        if old == a.new:
            return text
        tr = read_json(st('handoff', 'manager', 'transfer.json')) or {}
        ost = closed_state(old) if old else 'missing'
        if ost == 'error':
            raise Refuse('옛 대표 %s 의 상태를 읽지 못함. 다시 시도' % old[:8])
        ok = (not old) or ost in ('archived', 'closed', 'missing') or \
            (tr.get('from') == old and tr.get('to') in (a.new, 'next'))
        if not ok:
            raise Refuse('대표 %s 가 살아 있고 넘김(transfer-jarvis)이 없음. 대표에게 넘기게 하거나 사용자가 옛 대표를 '
                         'archive 한 뒤 다시' % old[:8])
        holder['why'] = 'transfer' if tr.get('from') == old and old else ('old_' + ost if old else 'first')
        return set_jarvis(text, old, a.new)

    rewrite_registry(fn)
    if 'why' not in holder:
        return out({'ok': True, 'why': '이미 대표'})
    try:
        os.remove(st('handoff', 'manager', 'transfer.json'))
    except OSError:
        pass
    ev = event({'event': 'jarvis_claim', 'old_agent_id': holder['old'], 'new_agent_id': a.new,
                'reason': holder['why'], 'result': 'switched', 'source': 'jarvisctl'})
    return out({'ok': True, 'event': ev})


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='jarvisctl')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('usage'); p.add_argument('agent'); p.set_defaults(fn=cmd_usage)
    p = sub.add_parser('can-rotate'); p.add_argument('project'); p.set_defaults(fn=cmd_can_rotate)
    p = sub.add_parser('lock'); p.add_argument('op', choices=['acquire', 'show', 'stage', 'candidate', 'release'])
    p.add_argument('project'); p.add_argument('value', nargs='?')
    p.add_argument('--reason', default='context', choices=['context', 'crash', 'manual', 'new'])
    p.add_argument('--result'); p.set_defaults(fn=cmd_lock)
    p = sub.add_parser('switch'); p.add_argument('project'); p.add_argument('new'); p.set_defaults(fn=cmd_switch)
    p = sub.add_parser('handoff'); p.add_argument('role'); p.add_argument('file'); p.set_defaults(fn=cmd_handoff)
    p = sub.add_parser('hook-check'); p.add_argument('agent'); p.set_defaults(fn=cmd_hook_check)
    p = sub.add_parser('transfer-jarvis'); p.add_argument('--to', required=True); p.set_defaults(fn=cmd_transfer)
    p = sub.add_parser('claim-jarvis'); p.add_argument('new'); p.set_defaults(fn=cmd_claim)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (Refuse, TimeoutError) as e:
        return out({'ok': False, 'why': str(e)}, 1)


if __name__ == '__main__':
    sys.exit(main())
