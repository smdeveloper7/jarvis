#!/usr/bin/env python3
"""JARVIS 수명주기·상태 명령 (docs/01·02). JARVIS 세션이 Bash 로 부른다. 판단은 JARVIS, 상태 전환은 이 코드가
원자적으로 한다. JARVIS 대화창은 여러 개일 수 있으므로, 겹치면 안 되는 일(번호 발급, registry, PM 교체)은 모두 여기서
잠금 안에서 한다.

  new-task <프로젝트> --title T [--request R]   W-YYYYMMDD-NN 작업 파일을 잠금 안에서 발급(JARVIS 자기 일은 프로젝트 jarvis)
  task <W-ID> [--status working|question|done|cancelled] [--note …] [--report …] [--verified …] [--release …]
       [--worklog --result … [--check …] [--next …] [--reason …] [--decision D-…]]
                                     작업 상태·경과를 잠금 안에서 바꾼다. done·cancelled 면 작업일지 한 건(docs/04 4.3)
  new-decision <프로젝트> --question Q [--task W-…] [--option …] [--recommended ①] [--force]
                                    D-YYYYMMDD-NN 결정 파일 발급(같은 작업의 열린 결정이 있으면 그것을 돌려줌)
  register <프로젝트> --root <폴더> [--name 이름] [--pm-title 제목] [--linear-project 이름]   프로젝트 등록
  set <프로젝트> <name|root|pm_title|linear_project> <값>   registry 필드 바꾸기
  decide <D-ID> [--status decided|waiting|dropped] --decision … [--by user|criteria] [--criterion n]
                                    결정 확정(open·waiting 일 때만, 성공한 창만 PM 에게 답함)
  decision-note <D-ID> "<한 줄>"     결정 파일에 설명 덧붙이기(결정 파일은 Edit·Write 금지)
  occupy <자원> --project P --task W [--agent PM] [--until 시각] [--note …]   공유 자원 점유(겹치면 대기)
  vacate <자원> [--task W]           점유 해제

  usage <에이전트ID>                 컨텍스트 토큰 (Paseo 기록 → Stop Snapshot → transcript)
  pm-check <프로젝트>                 [배정] 전 PM 깨우는 법: send | resume(닫힘→이어 띄움) | resume-rotate | rotate | new | wait
  can-rotate <프로젝트>               교체 조건 점검(JSON)
  lock acquire <프로젝트> [--reason context|crash|manual|new]
  lock show <프로젝트>
  lock stage <프로젝트> <handoff|provisioning|verifying>
  lock candidate <프로젝트> <후보 ID>  훅이 후보를 못 적었을 때 손으로
  lock release <프로젝트> [--result failed:<단계>]
  switch <프로젝트> <새 PM ID>        검증 후 registry coordinator 를 CAS 로 바꾸고 lock 해제(다시 실행해도 안전)
  handoff <프로젝트|manager> <파일>   인계 노트를 latest.md 로 (이전 것은 이력으로). 노트의 W·D 중 끝난 것과
                                     옛 노트에만 있던 열린 W·D 를 알려 준다(여러 창 합치기, docs/04 6.2)
  hook-check <에이전트ID>             SessionStart 훅이 그 세션에 넣은 표식(nonce)
  transfer-jarvis --to <ID|next>      기본 수신자(registry jarvis:)가 넘길 대상 지정
  claim-jarvis <새 JARVIS ID>         기본 수신자 전환(CAS)

상태를 바꾸는 명령은 JARVIS 폴더에서 뜬 세션(어느 JARVIS 창이든)과 Paseo 밖 사용자 셸만 쓴다. PM·Worker 는 못 쓴다.
registry `jarvis:` 는 쓰기 권한이 아니라 기본 수신자(아침 정리 보고, 보낸 창이 닫힌 보고를 받는 창)다.
JARVIS 폴더는 이 파일 위치(scripts/..), 다른 경로는 JARVIS_DIR 환경변수.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'hooks'))
from jarvis_state import (FileLock, Unknown, append_jsonl, clean, closed_state, context_tokens,  # noqa: E402
                          labels, now_iso, open_children_checked, open_tasks, paseo_lookup, read_json, registry,
                          write_json)
from pathutil import inside, norm  # noqa: E402

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


def require_jarvis():
    """JARVIS 창(역할 jarvis·jarvis_other)이나 Paseo 밖 사용자 셸만 허용. PM·Worker·검토·schedule 세션은 거절."""
    me = caller()
    if not me:
        return
    from jarvis_state import role_of
    stt, rec = paseo_lookup(me)
    if stt != 'ok':
        raise Refuse('호출한 세션(%s)의 Paseo 기록을 읽지 못함(%s)' % (me[:8], stt))
    role, _ = role_of(me, registry(JDIR), JDIR, rec.get('cwd') or '', rec)
    if role not in ('jarvis', 'jarvis_other'):
        raise Refuse('JARVIS 창만 쓴다(이 세션 %s 의 역할: %s)' % (me[:8], role))


def own_lock(lock, takeover=False):
    """교체 lock 의 소유 창만 진행한다. 소유 창이 닫혔거나 --takeover 면 소유를 넘겨받는다(기록 남김)."""
    me, owner = caller(), lock.get('owner')
    if not me or not owner or me == owner:
        return lock
    ost = closed_state(owner)
    if takeover or ost in ('archived', 'closed', 'missing'):
        event({'event': 'lock_takeover', 'project_id': lock.get('project'), 'old_agent_id': owner, 'new_agent_id': me,
               'reason': 'takeover' if takeover else 'owner_' + ost, 'source': 'jarvisctl'})
        lock['owner'] = me
        return lock
    raise Refuse('다른 JARVIS 창(%s)이 이 교체를 진행 중. 그 창에서 마치거나 --takeover 로 넘겨받는다' % owner[:8])


require_rep = require_jarvis   # 옛 이름


KST = timezone(timedelta(hours=9))


def next_id(folder, prefix):
    """<prefix>-YYYYMMDD-NN 의 다음 번호(KST 오늘). 호출하는 쪽이 잠금을 잡고 있어야 한다."""
    day = datetime.now(KST).strftime('%Y%m%d')
    os.makedirs(folder, exist_ok=True)
    nums = [int(m.group(1)) for f in os.listdir(folder)
            for m in [re.match(r'^%s-%s-(\d+)\.md$' % (prefix, day), f)] if m]
    return '%s-%s-%02d' % (prefix, day, max(nums, default=0) + 1)


def create_exclusive(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)


SELF_PROJECT = 'jarvis'   # JARVIS 자기 일(문서·보드·규칙). registry 에 없고 PM 도 없다(docs/04 3.1)


def cmd_new_task(a):
    require_jarvis()
    reg = registry(JDIR)
    if a.project not in reg['projects'] and a.project != SELF_PROJECT:
        raise Refuse('registry 에 없는 프로젝트 %s (JARVIS 자기 일은 %s)' % (a.project, SELF_PROJECT))
    folder = st('tasks')
    with FileLock(os.path.join(folder, '_alloc')):
        tid = next_id(folder, 'W')
        now = now_iso()
        body = ('---\nid: %s\nproject: %s\ntitle: %s\nstatus: assigned\njarvis: %s\ncoordinator: %s\nworkers: []\n'
                'linear:\nfeature:\nassigned_at: %s\nupdated_at: %s\ndone_at:\nverified: none\nrelease: none\n---\n'
                '## 요청\n%s\n\n## 보고\n\n## 경과\n- %s 배정\n'
                % (tid, a.project, a.title.replace('\n', ' '), caller() or 'user',
                   reg['projects'].get(a.project, {}).get('coordinator') or '', now, now, a.request or a.title,
                   now[11:16]))
        create_exclusive(os.path.join(folder, tid + '.md'), body)
    return out({'ok': True, 'id': tid, 'path': os.path.join(folder, tid + '.md')})


TASK_NEXT = {'assigned': {'working', 'question', 'done', 'cancelled'},
             'working': {'working', 'question', 'done', 'cancelled'},
             'question': {'working', 'question', 'done', 'cancelled'},
             'done': set(), 'cancelled': set()}
TASK_LABEL = {'working': '진행', 'question': '질문', 'done': '보고', 'cancelled': '취소'}
WORKLOG = os.environ.get('JARVIS_WORKLOG') or 'C:/Users/SMDEVELOPER/projects/worklog/scripts/worklog.ps1'
NO_WORKLOG = {'ask'}   # 단순 질의응답은 W 를 만들어도 작업일지를 남기지 않는다


def replace_section(text, title, body):
    """'## <title>' 절의 본문을 바꾼다(다음 '## ' 앞까지)."""
    m = re.search(r'(^## %s\n)(.*?)(?=^## |\Z)' % re.escape(title), text, re.S | re.M)
    if not m:
        return text.rstrip('\n') + '\n\n## %s\n%s\n' % (title, body.strip())
    return text[:m.start(2)] + body.strip() + '\n\n' + text[m.end(2):]


def cmd_task(a):
    """작업 파일의 status·updated_at·done_at·경과(·보고)를 잠금 안에서 바꾼다. done·cancelled 가 되면 작업일지로 이어진다."""
    require_jarvis()
    from jarvis_state import front
    path = st('tasks', a.id + '.md')
    if not os.path.exists(path):
        raise Refuse('작업 파일 없음: %s' % a.id)
    with FileLock(path):
        m = front(path)
        cur = m.get('status') or 'assigned'
        if a.status and a.status != cur and a.status not in TASK_NEXT.get(cur, set()):
            raise Refuse('%s 는 이미 %s 다. %s 로 바꿀 수 없다' % (a.id, cur, a.status))
        with open(path, encoding='utf-8') as f:
            text = f.read()
        now = now_iso()
        kw = {'updated_at': now}
        if a.status:
            kw['status'] = a.status
            if a.status in ('done', 'cancelled') and not m.get('done_at'):
                kw['done_at'] = now
        if a.verified:
            kw['verified'] = a.verified
        if a.release:
            kw['release'] = a.release
        text = set_front(text, **kw)
        if a.report:
            text = replace_section(text, '보고', a.report)
        note = a.note or (TASK_LABEL.get(a.status, '') if a.status and a.status != cur else '')
        if note:
            text = text.rstrip('\n') + '\n- %s %s\n' % (now[11:16], note.replace('\n', ' '))
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(path + '.tmp', path)
    res = {'ok': True, 'id': a.id, 'status': a.status or cur}
    final = (a.status or cur) in ('done', 'cancelled')
    proj = m.get('project') or ''
    if final and proj not in NO_WORKLOG:
        entry = {'agent': a.agent, 'project': 'JARVIS' if proj == SELF_PROJECT else proj,
                 'status': '완료' if (a.status or cur) == 'done' else '보류', 'title': m.get('title') or a.id,
                 'result': a.result or '', 'check': a.check or '', 'next': a.next or '', 'reason': a.reason or '',
                 'task': a.id, 'decision': a.decision or ''}
        if a.worklog:
            if not a.result:
                raise Refuse('--worklog 에는 --result 가 필요하다(상태는 이미 바뀜. --result 를 더해 다시 실행)')
            res['worklog'] = write_worklog(entry)
        elif a.status and a.status != cur:
            res['worklog_todo'] = ('작업일지 한 건을 남긴다: jarvisctl task %s --worklog --result "…" --check "…" '
                                   '--next "…" [--reason "…"] [--decision D-…]' % a.id)
    return out(res)


def write_worklog(entry):
    """공통 worklog.ps1 을 UTF-8 JSON 파일로 부른다(한글 인자가 깨지지 않게). 같은 내용이면 스크립트가 건너뛴다."""
    import subprocess
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.json')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in entry.items() if v}, f, ensure_ascii=False)
    try:
        cmd = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', WORKLOG, '-JsonFile', tmp]
        if os.environ.get('JARVIS_WORKLOG_RAWDIR'):   # 시험용: 실제 raw 대신 다른 폴더에
            cmd += ['-RawDir', os.environ['JARVIS_WORKLOG_RAWDIR']]
        r = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=60)
    finally:
        os.remove(tmp)
    if r.returncode != 0:
        raise Refuse('작업일지 기록 실패: %s' % (r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def cmd_new_decision(a):
    require_jarvis()
    folder = st('decisions')
    with FileLock(os.path.join(folder, '_alloc')):
        key = a.key or qkey(a.question)
        if not a.force:
            from jarvis_state import front
            same = [front(os.path.join(folder, f)) for f in sorted(os.listdir(folder)) if f.startswith('D-')]
            same = [m for m in same if m.get('project') == a.project and m.get('task') == (a.task or '')
                    and m.get('key') == key and m.get('status') in ('open', 'waiting')]
            if same:
                return out({'ok': True, 'existing': True, 'ids': [m.get('id') for m in same],
                            'why': '같은 작업·같은 질문의 열린 결정이 이미 있다(다른 창이 만들었을 수 있음). 그것을 쓴다'})
        did = next_id(folder, 'D')
        opts = ', '.join('"%s"' % o.replace('"', "'") for o in (a.option or []))
        body = ('---\nid: %s\ntask: %s\nproject: %s\nstatus: open\nkey: %s\njarvis: %s\ndecided_by:\ncriterion:\n'
                'options: [%s]\nrecommended: "%s"\nraised_at: %s\nuntil:\ndecided_at:\ndecision:\n---\n'
                '## 질문\n%s\n\n## 결정과 이유\n'
                % (did, a.task or '', a.project, key, caller() or 'user', opts, a.recommended or '', now_iso(),
                   a.question))
        create_exclusive(os.path.join(folder, did + '.md'), body)
    return out({'ok': True, 'id': did, 'path': os.path.join(folder, did + '.md')})


def qkey(q):
    import hashlib
    return hashlib.sha1(re.sub(r'\s+', ' ', q.strip()).encode('utf-8')).hexdigest()[:10]


def set_front(text, **kw):
    """마크다운 앞부분의 key: value 를 바꾼다(없는 키는 --- 앞에 더함)."""
    head, sep, body = text[4:].partition('\n---\n')
    lines = head.split('\n')
    for k, v in kw.items():
        v = '' if v is None else str(v).replace('\n', ' ')
        for i, line in enumerate(lines):
            if re.match(r'^%s:' % re.escape(k), line):
                lines[i] = '%s: %s' % (k, v) if v else '%s:' % k
                break
        else:
            lines.append('%s: %s' % (k, v))
    return '---\n' + '\n'.join(lines) + sep + body


def cmd_decide(a):
    """결정 확정은 open·waiting → decided·waiting·dropped CAS. 성공한 창만 PM 에게 답을 보낸다."""
    require_jarvis()
    from jarvis_state import front
    path = st('decisions', a.id + '.md')
    if not os.path.exists(path):
        raise Refuse('결정 파일 없음 %s' % a.id)
    with FileLock(path):
        cur = front(path)
        if cur.get('status') not in ('open', 'waiting'):
            return out({'ok': False, 'why': '이미 %s(%s, %s)' % (cur.get('status'), cur.get('decision'),
                                                              cur.get('decided_at')),
                        'decision': cur.get('decision'), 'decided_by': cur.get('decided_by')}, 1)
        if a.status == 'waiting':
            kw = {'status': 'waiting', 'until': a.until or '사용자가 다시 말할 때'}
        else:
            kw = {'status': a.status, 'decided_by': a.by, 'criterion': a.criterion, 'decided_at': now_iso(),
                  'decision': a.decision, 'decided_in': caller() or 'user'}
        with open(path, encoding='utf-8') as f:
            text = f.read()
        text = set_front(text, **kw)
        if a.reason:
            text = text.rstrip('\n') + '\n- %s %s\n' % (now_iso()[:16], a.reason)
        tmp = path + '.tmp%d' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    return out({'ok': True, 'id': a.id, 'status': a.status, 'next': '이 창이 PM 에게 답을 보낸다'})


# ---------- 공유 자원 점유 ----------

def read_occupancy(path):
    """없으면 빈 목록. 있는데 못 읽으면 거절(기존 점유를 덮어쓰지 않게)."""
    if not os.path.exists(path):
        return {'items': []}
    d = read_json(path)
    if not isinstance(d, dict) or not isinstance(d.get('items', []), list):
        raise Refuse('occupancy.json 을 읽지 못함. 고칠 때까지 허가·해제를 하지 않는다: %s' % path)
    d.setdefault('items', [])
    return d


def reclaimable(it):
    """점유한 PM 이 archive·삭제됐고, 그 PM 이 띄운 Worker 도 모두 닫혔을 때만 회수. 확인 못 하면 유지."""
    aid = it.get('agent')
    if not aid or closed_state(aid) not in ('archived', 'missing'):
        return False
    try:
        return not open_children_checked(aid)
    except Unknown:
        return False


def cmd_decision_note(a):
    """결정 파일에 설명 한 줄 더하기(잠금 안). 상태·결정 값은 decide 로만."""
    require_jarvis()
    path = st('decisions', a.id + '.md')
    if not os.path.exists(path):
        raise Refuse('결정 파일 없음 %s' % a.id)
    with FileLock(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
        text = text.rstrip('\n') + '\n- %s %s\n' % (now_iso()[:16], a.text.replace('\n', ' '))
        tmp = path + '.tmp%d' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    return out({'ok': True, 'id': a.id})


def cmd_occupy(a):
    """같은 자원을 다른 작업이 쓰고 있으면 거절(대기). 비었으면 등록. 성공한 창만 [점유허가] 를 보낸다."""
    require_jarvis()
    path = st('occupancy.json')
    with FileLock(path):
        d = read_occupancy(path)
        items, dropped = [], []
        for it in d.get('items', []):
            (dropped if reclaimable(it) else items).append(it)
        held = [it for it in items if it.get('resource') == a.resource and it.get('task') != a.task]
        if held:
            d['items'] = items
            d['updated_at'] = now_iso()
            write_json(path, d)
            return out({'ok': False, 'wait': held[0], 'reclaimed': dropped,
                        'next': '[점유대기] %s 가 %s 까지' % (held[0].get('task'), held[0].get('until'))}, 1)
        items = [it for it in items if not (it.get('resource') == a.resource and it.get('task') == a.task)]
        items.append({'resource': a.resource, 'project': a.project, 'task': a.task, 'agent': a.agent,
                      'since': now_iso(), 'until': a.until, 'note': a.note, 'granted_by': caller() or 'user'})
        write_json(path, {'updated_at': now_iso(), 'items': items})
    return out({'ok': True, 'reclaimed': dropped, 'next': '[점유허가] 를 보낸다'})


def cmd_vacate(a):
    require_jarvis()
    path = st('occupancy.json')
    with FileLock(path):
        d = read_occupancy(path)
        gone = [it for it in d.get('items', []) if it.get('resource') == a.resource and (not a.task or it.get('task') == a.task)]
        d['items'] = [it for it in d.get('items', []) if it not in gone]
        d['updated_at'] = now_iso()
        write_json(path, d)
    return out({'ok': True, 'removed': gone})


# ---------- registry 필드 ----------

FIELDS = ('name', 'root', 'pm_title', 'linear_project')


def set_field(text, proj, field, value):
    lines = text.split('\n')
    cur, start, end = None, None, None
    for i, line in enumerate(lines):
        m = re.match(r'^  ([A-Za-z0-9_.-]+):[ \t]*$', line)
        if m:
            if cur == proj:
                end = i
                break
            cur = m.group(1)
            if cur == proj:
                start = i
        elif cur == proj and line and not line.startswith('    ') and not line.startswith('#'):
            end = i
            break
    if start is None:
        raise Refuse('registry 에 없는 프로젝트 %s' % proj)
    end = end if end is not None else len(lines)
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    val = '"%s"' % value.replace('"', "'") if field in ('name', 'root', 'pm_title') else value
    for i in range(start + 1, end):
        if re.match(r'^    %s:' % field, lines[i]):
            lines[i] = '    %s: %s' % (field, val)
            return '\n'.join(lines)
    lines.insert(end, '    %s: %s' % (field, val))
    return '\n'.join(lines)


def cmd_set(a):
    require_jarvis()
    if a.field not in FIELDS:
        raise Refuse('바꿀 수 있는 필드: %s (coordinator 는 switch, jarvis 는 claim-jarvis)' % ', '.join(FIELDS))
    rewrite_registry(lambda t: set_field(t, a.project, a.field, a.value))
    return out({'ok': True, 'project': a.project, a.field: a.value})


def cmd_register(a):
    require_jarvis()
    if not re.match(r'^[A-Za-z0-9_.-]+$', a.project):
        raise Refuse('프로젝트 키는 영문·숫자·_.- 만')

    def fn(text):
        if re.search(r'^  %s:[ \t]*$' % re.escape(a.project), text, flags=re.M):
            raise Refuse('이미 등록된 프로젝트 %s' % a.project)
        if not re.search(r'^projects:[ \t]*$', text, flags=re.M):
            text = text.rstrip('\n') + '\nprojects:\n'
        block = '  %s:\n    name: "%s"\n    root: "%s"\n    coordinator:\n' % (
            a.project, (a.name or a.project).replace('"', "'"), a.root.replace('\\', '/'))
        block += '    pm_title: "%s"\n' % (a.pm_title or a.project + '-pm').replace('"', "'")
        if a.linear_project:
            block += '    linear_project: %s\n' % a.linear_project
        return text.rstrip('\n') + '\n' + block

    rewrite_registry(fn)
    return out({'ok': True, 'project': a.project, 'next': 'PM 띄우기(lock acquire %s --reason new)' % a.project})


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


def cmd_pm_check(a):
    """[배정] 전에 PM 을 어떻게 깨울지 정한다(docs/02 3.1). 닫힌(archive 아님) PM 은 새로 띄우지 않고 이어 띄운다.
    Paseo 는 닫힌 에이전트에 send_agent_prompt 를 보내면 저장된 세션으로 다시 연다(맥락 유지, SessionStart:resume).
    archive 는 사용자·교체 절차가 일부러 닫은 것이라 이어 띄우지 않는다."""
    p = registry(JDIR)['projects'].get(a.project)
    if not p:
        return out({'ok': False, 'why': 'registry 에 없는 프로젝트'}, 1)
    aid = p.get('coordinator')
    lock = read_json(lock_file(a.project))
    state = closed_state(aid) if aid else 'none'
    t, src, _ = usage(aid) if aid and state in ('open', 'closed') else (None, None, None)
    over = t is not None and t > THRESHOLD
    if lock:
        action, why = 'wait', 'PM 교체 중(rotating.lock). 교체가 끝난 뒤 새 PM 에 보낸다'
    elif state == 'open':
        action, why = ('send', '열려 있다. 그대로 [배정]') if not over else \
            ('rotate', '열려 있지만 컨텍스트가 기준을 넘었다. 지금 일을 보내기 전에 PM 세션 교체')
    elif state == 'closed':
        action, why = ('resume', '프로세스만 닫혔다. 새로 띄우지 말고 send_agent_prompt 로 [배정] 을 보내 이어 띄운다'
                       + ('' if t is not None else '(컨텍스트 크기 모름: 이어 띄운 뒤 jarvisctl usage 로 확인)')) \
            if not over else ('resume-rotate', '닫혔고 컨텍스트가 기준을 넘었다. [인계요청] 을 보내 이어 띄우고 PM 세션 교체 절차로')
    else:   # archived, missing, error, none
        action, why = 'new', 'PM 이 %s. "PM 띄우기"(mode crash, 인계 노트 state/handoff/%s/latest.md)' % (state, a.project)
    return out({'ok': True, 'project': a.project, 'agent_id': aid, 'pm_state': state, 'context_tokens': t,
                'source': src, 'threshold': THRESHOLD, 'action': action, 'why': why,
                'open_tasks': [x.get('id') for x in open_tasks(JDIR, a.project)]})


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


def update_lock(proj, held=False, takeover=False, **kw):
    """lock 갱신(소유 창만). held=True 면 이미 FileLock 안에서 부른 것."""
    path = lock_file(proj)
    if not held:
        with FileLock(path):
            return update_lock(proj, held=True, takeover=takeover, **kw)
    lock = read_json(path)
    if lock is None:
        raise Refuse('lock 없음')
    own_lock(lock, takeover)
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
        return out({'ok': True, 'lock': update_lock(a.project, takeover=a.takeover, stage=a.value)})
    if a.op == 'candidate':
        if not a.value:
            raise Refuse('후보 ID 필요')
        return out({'ok': True, 'lock': update_lock(a.project, takeover=a.takeover, candidate_id=a.value,
                                                    stage='verifying', reserving=None),
                    'problems': check_candidate(a.project, a.value)})
    if a.op == 'release':
        with FileLock(path):
            lock = read_json(path)
            if lock is None:
                raise Refuse('lock 없음')
            own_lock(lock, a.takeover)
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

def rewrite_registry(fn, after=None):
    """registry 잠금 안에서 읽기 → fn → 쓰기 → after(). transfer.json 같은 짝 파일도 같은 잠금 안에서 다룬다."""
    with FileLock(REG):
        with open(REG, encoding='utf-8') as f:
            text = f.read()
        new = fn(text)
        if new != text:
            tmp = REG + '.tmp%d' % os.getpid()
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(new)
            os.replace(tmp, REG)
        if after:
            after()


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
        own_lock(lock, a.takeover)
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
        lock = update_lock(a.project, held=True, takeover=True, stage='switched', owner=lock.get('owner'))
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
    check = handoff_check(latest, a.file)
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
    return out(dict({'ok': True, 'latest': latest}, **check))


REF_RE = re.compile(r'\b([WD]-\d{8}-\d+)\b')
CLOSED = ('done', 'cancelled', 'decided', 'dropped')
OPEN = ('assigned', 'working', 'question', 'open', 'waiting')


def ref_status(ref):
    from jarvis_state import front
    path = st('tasks' if ref.startswith('W-') else 'decisions', ref + '.md')
    return front(path).get('status') if os.path.exists(path) else 'missing'


def handoff_check(old_path, new_path):
    """새 노트에 적힌 W·D 중 이미 끝난 것(빼야 할 항목), 옛 노트에만 있고 아직 열린 W·D(옮겨야 할 항목).
    여러 JARVIS 창이 같은 latest.md 를 쓰므로 덮어쓸 때 다른 창의 열린 일을 잃지 않게 한다(docs/04 6.2)."""
    def refs(path):
        try:
            with open(path, encoding='utf-8') as f:
                return set(REF_RE.findall(f.read()))
        except OSError:
            return set()
    new, old = refs(new_path), refs(old_path)
    res = {}
    stale = sorted(r for r in new if ref_status(r) in CLOSED)
    dropped = sorted(r for r in old - new if ref_status(r) in OPEN)
    if stale:
        res['stale'] = stale
        res['stale_why'] = '새 노트에 적혔지만 이미 끝난 작업·결정이다. 다음 행동에 필요한 게 아니면 뺀다'
    if dropped:
        res['dropped_open'] = dropped
        res['dropped_why'] = '옛 노트에는 있었는데 새 노트에서 빠진 열린 작업·결정이다. 다른 창의 일이면 옮겨 적는다'
    return res


def cmd_hook_check(a):
    h = read_json(st('sessions', a.agent + '.hook.json'))
    return out({'agent_id': a.agent, 'hook': h, 'tag': ('jarvis-hooks v1#' + h['nonce']) if h else None},
               0 if h else 1)


# ---------- 대표 JARVIS ----------

def cmd_transfer(a):
    require_jarvis()
    import uuid
    holder = {}

    def put():
        body = {'from': registry(JDIR).get('jarvis'), 'to': a.to, 'at': now_iso(), 'by': caller() or 'user',
                'token': uuid.uuid4().hex[:10]}
        write_json(st('handoff', 'manager', 'transfer.json'), body)
        holder['body'] = body

    rewrite_registry(lambda t: t, after=put)
    return out({'ok': True, 'transfer': holder['body']})


def cmd_claim(a):
    require_jarvis()
    me = caller()
    from jarvis_state import role_of
    stt, rec = paseo_lookup(a.new)
    if stt != 'ok' or rec.get('archivedAt') or rec.get('lastStatus') == 'closed':
        raise Refuse('올릴 세션 %s 이 열려 있지 않거나 기록을 읽지 못함(%s)' % (a.new[:8], stt))
    trole, _ = role_of(a.new, registry(JDIR), JDIR, rec.get('cwd') or '', rec)
    if trole not in ('jarvis', 'jarvis_other'):
        raise Refuse('JARVIS 창만 기본 수신자가 된다(%s 의 역할: %s)' % (a.new[:8], trole))
    if me and me != a.new:
        raise Refuse('자기 자신(%s)만 기본 수신자로 올릴 수 있음' % me[:8])
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
        holder['token'] = tr.get('token') if holder['why'] == 'transfer' else None
        return set_jarvis(text, old, a.new)

    def consume():
        tok = holder.get('token')
        if tok and (read_json(st('handoff', 'manager', 'transfer.json')) or {}).get('token') == tok:
            os.remove(st('handoff', 'manager', 'transfer.json'))

    rewrite_registry(fn, after=consume)
    if 'why' not in holder:
        return out({'ok': True, 'why': '이미 기본 수신자'})
    ev = event({'event': 'jarvis_claim', 'old_agent_id': holder['old'], 'new_agent_id': a.new,
                'reason': holder['why'], 'result': 'switched', 'source': 'jarvisctl'})
    return out({'ok': True, 'event': ev})


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='jarvisctl')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('usage'); p.add_argument('agent'); p.set_defaults(fn=cmd_usage)
    p = sub.add_parser('pm-check'); p.add_argument('project'); p.set_defaults(fn=cmd_pm_check)
    p = sub.add_parser('can-rotate'); p.add_argument('project'); p.set_defaults(fn=cmd_can_rotate)
    p = sub.add_parser('lock'); p.add_argument('op', choices=['acquire', 'show', 'stage', 'candidate', 'release'])
    p.add_argument('project'); p.add_argument('value', nargs='?')
    p.add_argument('--reason', default='context', choices=['context', 'crash', 'manual', 'new'])
    p.add_argument('--result'); p.add_argument('--takeover', action='store_true'); p.set_defaults(fn=cmd_lock)
    p = sub.add_parser('switch'); p.add_argument('project'); p.add_argument('new')
    p.add_argument('--takeover', action='store_true'); p.set_defaults(fn=cmd_switch)
    p = sub.add_parser('handoff'); p.add_argument('role'); p.add_argument('file'); p.set_defaults(fn=cmd_handoff)
    p = sub.add_parser('hook-check'); p.add_argument('agent'); p.set_defaults(fn=cmd_hook_check)
    p = sub.add_parser('transfer-jarvis'); p.add_argument('--to', required=True); p.set_defaults(fn=cmd_transfer)
    p = sub.add_parser('claim-jarvis'); p.add_argument('new'); p.set_defaults(fn=cmd_claim)
    p = sub.add_parser('task'); p.add_argument('id')
    p.add_argument('--status', choices=['working', 'question', 'done', 'cancelled'])
    p.add_argument('--note'); p.add_argument('--report')
    p.add_argument('--verified', choices=['confirmed', 'reported', 'none'])
    p.add_argument('--release', choices=['none', 'dev_only', 'released'])
    p.add_argument('--worklog', action='store_true'); p.add_argument('--agent', default='Claude')
    p.add_argument('--result'); p.add_argument('--check'); p.add_argument('--next'); p.add_argument('--reason')
    p.add_argument('--decision'); p.set_defaults(fn=cmd_task)
    p = sub.add_parser('new-task'); p.add_argument('project'); p.add_argument('--title', required=True)
    p.add_argument('--request'); p.set_defaults(fn=cmd_new_task)
    p = sub.add_parser('new-decision'); p.add_argument('project'); p.add_argument('--question', required=True)
    p.add_argument('--task'); p.add_argument('--option', action='append'); p.add_argument('--recommended')
    p.add_argument('--key'); p.add_argument('--force', action='store_true'); p.set_defaults(fn=cmd_new_decision)
    p = sub.add_parser('decide'); p.add_argument('id')
    p.add_argument('--status', default='decided', choices=['decided', 'waiting', 'dropped'])
    p.add_argument('--decision'); p.add_argument('--by', default='user', choices=['user', 'criteria'])
    p.add_argument('--criterion'); p.add_argument('--until'); p.add_argument('--reason'); p.set_defaults(fn=cmd_decide)
    p = sub.add_parser('decision-note'); p.add_argument('id'); p.add_argument('text')
    p.set_defaults(fn=cmd_decision_note)
    p = sub.add_parser('occupy'); p.add_argument('resource'); p.add_argument('--project', required=True)
    p.add_argument('--task', required=True); p.add_argument('--agent'); p.add_argument('--until')
    p.add_argument('--note'); p.set_defaults(fn=cmd_occupy)
    p = sub.add_parser('vacate'); p.add_argument('resource'); p.add_argument('--task'); p.set_defaults(fn=cmd_vacate)
    p = sub.add_parser('set'); p.add_argument('project'); p.add_argument('field'); p.add_argument('value')
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser('register'); p.add_argument('project'); p.add_argument('--root', required=True)
    p.add_argument('--name'); p.add_argument('--pm-title'); p.add_argument('--linear-project')
    p.set_defaults(fn=cmd_register)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (Refuse, TimeoutError) as e:
        return out({'ok': False, 'why': str(e)}, 1)


if __name__ == '__main__':
    sys.exit(main())
