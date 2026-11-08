"""JARVIS 대시보드 데이터: Paseo 에이전트 기록과 JARVIS state/ 파일을 lanekeeper 대시보드가 읽는 형태로 바꾼다.

읽기만 한다. 출처
- Paseo: ~/.paseo/agents/<폴더>/<에이전트 ID>.json (제목, 부모 ID, 상태, Claude 세션 ID)
- JARVIS: registry.yaml, state/tasks/*.md, state/decisions/*.md, state/occupancy.json, state/releases.json
- 대화 기록: ~/.claude/projects/<cwd>/<세션 ID>.jsonl (배정·보고 같은 머리말 메시지만 뽑음)

역할 매핑(lanekeeper 화면 기준)
- JARVIS(제목이 JARVIS 인 에이전트) -> group resident, lane 'HUB' (오피스의 허브 자리, 흐름도 가운데)
- Coordinator(registry 의 coordinator 이거나 부모가 JARVIS) -> group resident, lane 프로젝트 키
- 이전 PM 세션(라벨 jarvis.role=pm 이나 제목 <키>-pm·<키>-coord 인데 registry 의 현재 PM 이 아님) -> role pm_prev,
  group task, lane 프로젝트 키, task '이전 PM'. 세션 교체 뒤에도 그 PM 과 Worker 가 프로젝트 방에 남는다(docs/02).
- JARVIS 는 registry.yaml `jarvis:`(대표 JARVIS)가 살아 있으면 그것.
- Worker(부모가 Coordinator) -> group task, lane 프로젝트 키, task 작업 ID(제목의 W-…)
"""
import glob
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

PASEO = os.path.abspath(os.path.expanduser(os.environ.get('PASEO_HOME') or '~/.paseo'))
JARVIS_NAME = os.environ.get('JARVIS_NAME') or 'JARVIS'
BOSS = os.environ.get('JARVIS_BOSS') or '사장'   # 사장실 명패: JARVIS 에게 채팅하는 사람(이름 대신 직책)
TAGS = ('배정', '보고', '질문', '진행', '승인요청', '점유', '해제', '완료')
TAG_RE = re.compile(r'^\[(' + '|'.join(TAGS) + r')\]', re.M)
TASK_RE = re.compile(r'W-\d{8}-\d+')
TASK_LABEL = 'jarvis.task'   # Worker 의 작업 번호 라벨. 제목은 '<영역>-<한국어 작업 이름>'
TASK_LABEL_RE = re.compile(r'W-\d{8}-\d+(?:-[A-Za-z0-9]+)?')   # 이름표: 한 작업을 나눈 Worker(-B, -C)까지
WORKER_DONE_S = 30 * 60   # 끝난 Worker 를 '끝남' 구역에 두는 시간. 넘으면 목록에서 뺀다


def iso_ms(s):
    """ISO 시각 -> ms. 'Z' 와 +09:00 을 읽고, 시간대가 없으면 이 PC 시각으로 본다."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return int(d.timestamp() * 1000)


# ---------- registry.yaml (프로젝트: name, root, coordinator) ----------

def registry(jarvis):
    """아주 작은 YAML 읽기: projects: 아래 2칸 들여쓴 키, 4칸 들여쓴 필드만."""
    out, cur = {}, None
    try:
        with open(os.path.join(jarvis, 'registry.yaml'), encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                m = re.match(r'^  ([A-Za-z0-9_.-]+):\s*$', line)
                if m:
                    cur = out.setdefault(m.group(1), {})
                    continue
                m = re.match(r'^    ([A-Za-z_]+):\s*(.*?)\s*$', line)
                if m and cur is not None:
                    v = m.group(2).split(' #')[0].strip().strip('"').strip("'")
                    cur[m.group(1)] = None if v in ('', 'null', '~') else v
    except OSError:
        pass
    return out


def registry_jarvis(jarvis):
    """registry.yaml 맨 위 `jarvis: <대표 JARVIS 에이전트 ID>`."""
    try:
        with open(os.path.join(jarvis, 'registry.yaml'), encoding='utf-8') as f:
            for line in f:
                m = re.match(r'^jarvis:\s*(\S+)', line)
                if m:
                    return m.group(1).strip('"').strip("'")
    except OSError:
        pass
    return None


def boss_name(jarvis):
    """사장실 명패: registry.yaml 맨 위 `boss: <이름>`, 없으면 JARVIS_BOSS 환경변수, 없으면 '사장'."""
    try:
        with open(os.path.join(jarvis, 'registry.yaml'), encoding='utf-8') as f:
            for line in f:
                m = re.match(r'^boss:\s*(.+?)\s*$', line)
                if m:
                    return m.group(1).split(' #')[0].strip().strip('"').strip("'") or BOSS
    except OSError:
        pass
    return BOSS


def norm(p):
    return (p or '').replace('\\', '/').rstrip('/').casefold()


# ---------- Paseo 에이전트 ----------

def paseo_records(root=PASEO, archived=False):
    """Paseo 에이전트 기록. archived=True 면 archive 된 것도(관계 계산용, 화면에는 안 보임)."""
    recs = []
    for p in glob.glob(os.path.join(root, 'agents', '*', '*.json')):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get('internal') or (d.get('archivedAt') and not archived):
            continue
        recs.append(d)
    return recs


def roles(recs, reg, home=None, rep=None):
    """에이전트 ID -> (역할, 프로젝트 키). 역할: jarvis | coordinator | pm_prev | worker | other.
    JARVIS 는 rep(registry 대표)가 살아 있으면 그것, 아니면 제목이 JARVIS 인 것, 없으면 JARVIS 폴더(home)에서
    부모 없이 뜬 것 중 최근 것."""
    by_id = {r['id']: r for r in recs}
    parent = {r['id']: (r.get('labels') or {}).get('paseo.parent-agent-id') for r in recs}
    live = [r for r in recs if r.get('lastStatus') != 'closed' and not r.get('archivedAt')]
    jarvis = [r for r in live if (r.get('title') or '') == JARVIS_NAME]
    if not jarvis and home:
        jarvis = [r for r in live if not parent.get(r['id']) and norm(r.get('cwd')) == norm(home)]
    jarvis.sort(key=lambda r: r.get('lastActivityAt') or '', reverse=True)
    jid = jarvis[0]['id'] if jarvis else None
    if rep and any(r['id'] == rep for r in live):
        jid = rep
    coord_of = {v.get('coordinator'): k for k, v in reg.items() if v.get('coordinator')}
    roots = {}
    for k, v in reg.items():
        if v.get('root'):
            roots.setdefault(norm(v['root']), []).append(k)
    root_of = {rt: ks[0] for rt, ks in roots.items() if len(ks) == 1}   # 같은 폴더를 여러 프로젝트가 쓰면 폴더로는 정하지 않는다

    def title_proj(r):   # 제목 '<프로젝트>-coord' / '<프로젝트>-w-<W-ID>' 의 프로젝트 키
        t = (r.get('title') or '').strip()
        for k in sorted(reg, key=len, reverse=True):
            if t in (k + '-coord', k + '-pm') or t.startswith(k + '-w-') or t.startswith(k + '-coord')                     or t.startswith(k + '-pm-'):
                return k
        return None

    out = {}
    for r in recs:
        i = r['id']
        proj = title_proj(r) or root_of.get(norm(r.get('cwd')))
        if i == jid:
            out[i] = ('jarvis', '')
        elif i in coord_of:
            out[i] = ('coordinator', coord_of[i])
        elif jid and parent.get(i) == jid:
            out[i] = ('coordinator', proj or '')
        elif is_prev_pm(r, reg, parent.get(i), title_proj):
            lb = r.get('labels') or {}
            out[i] = ('pm_prev', lb.get('jarvis.project') if lb.get('jarvis.project') in reg else title_proj(r))
        else:
            out[i] = None
    for r in recs:   # 부모가 Coordinator 면 Worker
        i = r['id']
        if out[i] is None:
            p = out.get(parent.get(i))
            out[i] = ('worker', p[1] or title_proj(r) or root_of.get(norm(r.get('cwd'))) or '')                 if p and p[0] in ('coordinator', 'pm_prev') else ('other', '')
    return out, jid, by_id


def is_prev_pm(r, reg, parent, title_proj):
    """registry 의 현재 PM 이 아닌 PM 세션: 라벨 jarvis.role=pm, 또는 부모 없는 <키>-pm·<키>-coord."""
    lb = r.get('labels') or {}
    if lb.get('jarvis.role') == 'pm' and lb.get('jarvis.project') in reg:
        return True
    t = (r.get('title') or '').strip()
    return not parent and any(t in (k + '-pm', k + '-coord') for k in reg)


def row_of(r, role, proj, now_ms):
    """lanekeeper 의 `claude agents` 줄 모양으로."""
    st = r.get('lastStatus') or ''
    reason = r.get('attentionReason') or ''
    attention = bool(r.get('requiresAttention')) and reason != 'finished'
    rt = r.get('runtimeInfo') or (r.get('persistence') or {})
    title = r.get('title') or r['id'][:8]
    row = {
        'name': title, 'paseoId': r['id'], 'sessionId': rt.get('sessionId') or r['id'],
        'cwd': r.get('cwd') or '', 'kind': 'paseo', 'engine': 'codex' if r.get('provider') == 'codex' else 'claude',
        'startedAt': iso_ms(r.get('createdAt')), 'role': role, 'project': proj,
        'model': (r.get('config') or {}).get('model') or rt.get('model') or '',
    }
    if st == 'running':
        row.update(status='busy', state='working')
    elif attention:
        row.update(status='idle', state='blocked', needs=reason or '확인 필요')
    elif role in ('worker', 'pm_prev') or st == 'closed':
        row.update(status='idle', state='done')
    else:
        row.update(status='idle', state='idle')
    if role == 'jarvis':
        row.update(group='resident', lane='HUB', task='')
    elif role == 'coordinator':
        row.update(group='resident', lane=proj or '프로젝트', task='')
    elif role == 'pm_prev':
        row.update(group='task', lane=proj, task='이전 PM', name=title + ' (이전)')
    elif role == 'worker':
        m = TASK_LABEL_RE.search(title)   # 옛 제목 '<프로젝트>-w-<W-ID>'
        task = (r.get('labels') or {}).get(TASK_LABEL) or (m.group(0) if m else title)
        row.update(group='task', lane=proj, task=task)
    else:
        row.update(group='other', lane='', task='')
    return row


def agent_rows(jarvis, root=PASEO, now_ms=None):
    """(rows, roster, hub 이름). 오래전에 끝난 Worker 는 뺀다."""
    now_ms = now_ms or int(time.time() * 1000)
    recs = paseo_records(root, archived=True)   # 관계(부모·이전 PM)는 archive 된 것까지 보고 계산, 표시만 거른다
    reg = registry(jarvis)
    rl, jid, by_id = roles(recs, reg, jarvis, registry_jarvis(jarvis))
    rows, names = [], {}
    for r in recs:
        role, proj = rl[r['id']]
        if role == 'other' or r.get('archivedAt'):
            continue
        row = row_of(r, role, proj, now_ms)
        last = iso_ms(r.get('lastActivityAt') or r.get('updatedAt'))
        if role == 'worker' and row['state'] == 'done' and last and now_ms - last > WORKER_DONE_S * 1000:
            continue
        rows.append(row)
        names[row['name']] = {'group': row['group'], 'lane': row['lane'], 'task': row['task'], 'order': len(names)}
    hub = (by_id[jid].get('title') or JARVIS_NAME) if jid else None
    return rows, names, hub


# ---------- 메시지(배정·보고 등) ----------

def tagged_prompts(path):
    """대화 기록에서 사용자 차례로 들어온 머리말 메시지만: [(ms, tag, text)]."""
    out = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '"user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('type') != 'user' or d.get('isMeta'):
                    continue
                c = (d.get('message') or {}).get('content')
                text = c if isinstance(c, str) else ' '.join(
                    x.get('text', '') for x in c or [] if isinstance(x, dict) and x.get('type') == 'text')
                m = TAG_RE.search(text or '')
                if m:
                    out.append((iso_ms(d.get('timestamp')) or 0, m.group(1), text[m.start():].strip()))
    except OSError:
        pass
    return out


KIND = {'배정': 'assign', '보고': 'report', '완료': 'report', '질문': 'notice', '승인요청': 'notice',
        '진행': 'other', '점유': 'other', '해제': 'other'}
NOTICE_MARKS = ('<agent-response>', '[Message received]', 'Server: srv_')   # Paseo 가 넣는 알림: 사람 채팅이 아님


def conversation(path):
    """대화 기록에서 머리말 없는 사람 메시지와 차례를 끝낸 답: [(ms, 'chat'|'answer'|'first', text)].
    'first' 는 첫 사용자 메시지(머리말이 있든 없든)로, Worker 가 받은 첫 지시를 찾는 데 쓴다."""
    out, first = [], True
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('isMeta') or d.get('isSidechain'):
                    continue
                msg = d.get('message') or {}
                c = msg.get('content')
                if d.get('type') == 'user':
                    if isinstance(c, list) and any(isinstance(x, dict) and x.get('type') == 'tool_result' for x in c):
                        continue
                    text = c if isinstance(c, str) else ' '.join(
                        x.get('text', '') for x in c or [] if isinstance(x, dict) and x.get('type') == 'text')
                    text = (text or '').strip()
                    if not text:
                        continue
                    t = iso_ms(d.get('timestamp')) or 0
                    if first:
                        out.append((t, 'first', text))
                        first = False
                    if not TAG_RE.search(text) and not any(m in text for m in NOTICE_MARKS) and not text.startswith('<'):
                        out.append((t, 'chat', text))
                elif d.get('type') == 'assistant' and msg.get('stop_reason') == 'end_turn':
                    text = ' '.join(x.get('text', '') for x in c or [] if isinstance(x, dict) and x.get('type') == 'text').strip()
                    if text:
                        out.append((iso_ms(d.get('timestamp')) or 0, 'answer', text))
    except OSError:
        pass
    return out


def messages(rows, transcript_path, hours=24, now=None, max_messages=300, boss=None):
    """받은 쪽 대화 기록의 머리말 메시지로 흐름을 만든다. 보낸 쪽: 배정은 부모, 그 밖은 작업 ID 의 Coordinator 나 자식."""
    BOSS = boss or globals()['BOSS']
    now = time.time() if now is None else now
    since = int((now - hours * 3600) * 1000) if hours > 0 else 0
    by_paseo = {r['paseoId']: r for r in rows}
    parent_of, kids = {}, {}
    for r in rows:
        p = None
        if r['role'] == 'coordinator':
            p = next((x['name'] for x in rows if x['role'] == 'jarvis'), None)
        elif r['role'] == 'worker':
            p = next((x['name'] for x in rows if x['role'] == 'coordinator' and x['project'] == r['project']), None)
        parent_of[r['name']] = p
        if p:
            kids.setdefault(p, []).append(r)
    msgs = []

    def add(t, frm, to, kind, text):
        first = text.splitlines()[0] if text else ''
        msgs.append({'t': t, 'from': frm, 'to': to, 'kind': kind, 'sum': first[:200], 'text': text[:4000],
                     'id': f'{frm}>{to}:{t}:{kind}'})

    for r in rows:
        tp = transcript_path(r['sessionId'], r['cwd'])
        if not tp:
            continue
        if r['role'] in ('jarvis', 'worker'):
            for t, kind, text in conversation(tp):
                if t < since:
                    continue
                if r['role'] == 'jarvis' and kind in ('chat', 'answer'):   # 사장 <-> JARVIS
                    add(t, BOSS, r['name'], 'chat', text) if kind == 'chat' else add(t, r['name'], BOSS, 'answer', text)
                elif r['role'] == 'worker' and kind == 'first' and parent_of.get(r['name']):   # Coordinator 의 첫 지시
                    add(t, parent_of[r['name']], r['name'], 'assign', text)
        for t, tag, text in tagged_prompts(tp):
            if t < since:
                continue
            if tag == '배정':
                frm = parent_of.get(r['name'])
            else:   # 아래에서 올라온 것: 작업 ID 가 맞는 자식, 없으면 첫 자식
                m = TASK_RE.search(text)
                ks = kids.get(r['name']) or []
                hit = next((k for k in ks if m and (k['task'] == m.group(0) or k['role'] == 'coordinator')), None)
                frm = (hit or (ks[0] if ks else None) or {}).get('name')
            if not frm:
                continue
            first = text.splitlines()[0] if text else ''
            msgs.append({'t': t, 'from': frm, 'to': r['name'], 'kind': KIND.get(tag, 'other'),
                         'sum': first[:200], 'text': text[:4000], 'id': f"{r['sessionId']}:{t}:{tag}"})
    msgs.sort(key=lambda m: -m['t'])
    # 사장 <-> JARVIS 는 오피스 장면에만 쓰고 흐름(그래프·목록)에서는 뺀다
    boss = [m for m in msgs if BOSS in (m['from'], m['to'])]
    msgs = [m for m in msgs if BOSS not in (m['from'], m['to'])]
    edges, count = {}, {}
    for m in msgs:
        e = edges.setdefault((m['from'], m['to']), {'from': m['from'], 'to': m['to'], 'count': 0, 'last': 0, 'kinds': {}})
        e['count'] += 1
        e['kinds'][m['kind']] = e['kinds'].get(m['kind'], 0) + 1   # 흐름 카드 선 이름표: 배정·보고·질문 건수
        e['last'] = max(e['last'], m['t'])
        for k, n in (('sent', m['from']), ('got', m['to'])):
            count.setdefault(n, {'sent': 0, 'got': 0})[k] += 1
    for r in rows:   # 메시지가 없어도 구조는 보이게: 부모-자식 선은 최소 1
        p = parent_of.get(r['name'])
        if p and (p, r['name']) not in edges:
            edges[(p, r['name'])] = {'from': p, 'to': r['name'], 'count': 0, 'last': 0, 'kinds': {}}
        for n in (p, r['name']):
            if n:
                count.setdefault(n, {'sent': 0, 'got': 0})
    first = {r['name']: r for r in rows}
    nodes = []
    for n, c in count.items():
        a = first.get(n) or {}
        nodes.append(dict(c, name=n, group=a.get('group', 'task'), lane=a.get('lane', ''), task=a.get('task', ''),
                          listed=bool(a), parent=parent_of.get(n) or '', role=a.get('role', ''),
                          **{k: a.get(k) for k in ('state', 'status', 'kind', 'engine', 'sessionId')}))
    hub = next((r['name'] for r in rows if r['role'] == 'jarvis'), None)
    return {'hours': hours, 'hub': hub, 'boss': BOSS, 'nodes': nodes, 'edges': sorted(edges.values(), key=lambda e: -e['count']),
            'total': len(msgs), 'messages': msgs[:max_messages], 'bossMessages': boss[:50]}


def office(jarvis, rows):
    """오피스 장면에 필요한 것: 사장 이름, 프로젝트 방 목록(registry 순서 + 줄에만 있는 레인), 결정 대기 수."""
    reg = registry(jarvis)
    projects = [{'key': k, 'name': v.get('name') or k} for k, v in reg.items()]
    seen = {p['key'] for p in projects}
    for r in rows:
        if r.get('role') in ('coordinator', 'worker') and r.get('lane') and r['lane'] not in seen:
            seen.add(r['lane'])
            projects.append({'key': r['lane'], 'name': r['lane']})
    pending = sum(1 for d in decisions(jarvis) if d['status'] not in ('waiting', 'decided', 'dropped', 'stale'))
    pending += sum(1 for r in rows if r.get('state') == 'blocked')
    return {'boss': boss_name(jarvis), 'projects': projects, 'pending': pending}


# ---------- state/ 파일 ----------

def frontmatter(path):
    """(필드, 본문). 'key: value  # 주석', 목록은 JSON 꼴 [..]."""
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return {}, ''
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        mm = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
        if not mm:
            continue
        v = mm.group(2)
        if not v.startswith(('[', '"')):
            v = v.split(' #')[0]
        v = v.strip()
        if v.startswith('['):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        elif v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        meta[mm.group(1)] = v
    return meta, m.group(2)


def section_text(body, title):
    m = re.search(r'^##\s*' + re.escape(title) + r'\s*\n(.*?)(?=^##\s|\Z)', body, re.M | re.S)
    return m.group(1).strip() if m else ''


def decisions(jarvis):
    """lanekeeper decisions.json 의 items 모양으로. 기준으로 정한 것은 결정 칸에 기준 번호를 붙인다."""
    items = []
    task_st = {}
    for p in glob.glob(os.path.join(jarvis, 'state', 'tasks', '*.md')):
        m, _ = frontmatter(p)
        if m.get('id'):
            task_st[m['id']] = m.get('status') or ''
    for p in sorted(glob.glob(os.path.join(jarvis, 'state', 'decisions', '*.md'))):
        meta, body = frontmatter(p)
        q = section_text(body, '질문')
        why = section_text(body, '결정과 이유')
        title = (q.splitlines() or [''])[0] or meta.get('id', os.path.basename(p))
        dec = meta.get('decision') or ''
        if meta.get('decided_by') == 'criteria' and meta.get('criterion'):
            dec = f"기준 {meta['criterion']}: {dec}".strip()
        elif meta.get('decided_by') == 'user':
            dec = f"사용자: {dec}".strip()
        refs = [x for x in (meta.get('task'), meta.get('project')) if x]
        st = meta.get('status') or 'open'
        if st == 'open' and task_st.get(meta.get('task')) in TASK_CLOSED:
            st = 'stale'   # 작업은 끝났는데 결정은 열린 채: 지금 결정 필요에서 빼고 따로 보인다
        if st == 'dropped' and not dec:
            dec = '필요 없어짐'
        items.append({'id': meta.get('id', ''), 'status': st, 'title': title,
                      'detail': '\n\n'.join(x for x in (q, why) if x), 'options': meta.get('options') or [],
                      'source': meta.get('project') or '', 'task': meta.get('task') or '',
                      'raised': meta.get('raised_at') or '', 'until': meta.get('until') or '',
                      'decision': dec, 'decided_at': meta.get('decided_at') or '', 'refs': refs,
                      'recommended': meta.get('recommended') or ''})
    return items


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def occupancy(jarvis):
    """lanekeeper dev-occupancy.json 모양으로: who, kind, what, since, until, note."""
    d = load_json(os.path.join(jarvis, 'state', 'occupancy.json'), None)
    if d is None:
        return None
    items = []
    for i in d.get('items') or []:
        if isinstance(i, dict):
            items.append({'who': i.get('agent') or i.get('project') or '', 'kind': 'run',
                          'what': ' · '.join(x for x in (i.get('resource'), i.get('task')) if x),
                          'since': i.get('since') or '', 'until': i.get('until') or '', 'note': i.get('note') or ''})
    return {'updated_at': d.get('updated_at') or '', 'items': items}


TASK_CLOSED = ('done', 'cancelled')
STATUS_KO = {'assigned': '배정됨', 'working': '진행 중', 'question': '질문 중', 'done': '끝남', 'cancelled': '취소'}
VERIFIED_KO = {'confirmed': '확인함', 'reported': '보고만', 'none': '-'}


def status_markdown(jarvis):
    """현황 탭: 작업판, 운영 반영 대기, 공유 자원 점유를 마크다운 표로."""
    reg = registry(jarvis)
    tasks = []
    for p in glob.glob(os.path.join(jarvis, 'state', 'tasks', '*.md')):
        meta, body = frontmatter(p)
        meta['last'] = (section_text(body, '보고').splitlines() or [''])[0]
        tasks.append(meta)
    tasks.sort(key=lambda m: m.get('updated_at') or m.get('assigned_at') or '', reverse=True)
    lines = ['# 현황', '']
    projects = list(reg) + sorted({t.get('project') for t in tasks if t.get('project') and t.get('project') not in reg})
    for k in projects:
        v = reg.get(k, {})
        lines += [f"## {v.get('name') or k}", '', f"Coordinator: `{v.get('coordinator') or '없음'}` · 폴더 `{v.get('root') or '-'}`", '']
        ts = [t for t in tasks if t.get('project') == k]
        if not ts:
            lines += ['진행 기록 없음.', '']
            continue
        lines += ['| 작업 | 제목 | 상태 | 확인 | 운영 반영 | 마지막 보고 | 갱신 |', '|---|---|---|---|---|---|---|']
        for t in ts:
            rel = {'dev_only': '대기', 'released': '끝남'}.get(t.get('release'), '-')
            lines.append('| `{}` | {} | {} | {} | {} | {} | {} |'.format(
                t.get('id', ''), t.get('title', ''), STATUS_KO.get(t.get('status'), t.get('status', '')),
                VERIFIED_KO.get(t.get('verified'), t.get('verified') or '-'), rel,
                (t.get('last') or '').replace('|', '/')[:120], (t.get('updated_at') or '')[5:16].replace('T', ' ')))
        lines.append('')
    rel = [i for i in (load_json(os.path.join(jarvis, 'state', 'releases.json'), {}).get('items') or [])
           if isinstance(i, dict) and i.get('status') != 'released']
    lines += ['## 운영 반영 대기', '']
    lines += [f"- {i.get('what', '')} · {i.get('project', '')} `{i.get('task', '')}` · {i.get('env', 'dev')} 에만, {i.get('since', '')[:16]} 부터"
              for i in rel] or ['없음.']
    occ = (occupancy(jarvis) or {}).get('items') or []
    lines += ['', '## 공유 자원 점유', '']
    lines += [f"- `{i['what']}` · {i['who']} · {i['since'][:16]} ~ {i['until'][:16]} {i['note']}" for i in occ] or ['없음.']
    return '\n'.join(lines) + '\n'


# ---------- Notion 할일 DB (할 일 탭) ----------
# 토큰: 환경변수 NOTION_TOKEN, 없으면 ~/.jarvis/notion_token 파일(저장소 밖). Notion 내부 통합을 할일 DB 에 연결해야 읽힌다.
NOTION_DS = os.environ.get('JARVIS_NOTION_DS') or '2ed3cf1a-c9b3-81aa-a0d6-000b40fab530'   # 할일 data source
NOTION_KSM = '3fb3cf1ac9b380818136ed223027e043'   # 프로젝트 KSM 페이지(NIPA)
NOTION_TOKEN_FILE = os.path.expanduser('~/.jarvis/notion_token')
# 할 일 탭에 보일 프로젝트(Notion 프로젝트 페이지 id, 쉼표로 여럿). 기본은 KSM 만, 빈 값이면 전부
NOTION_ONLY = {x.strip().replace('-', '') for x in os.environ.get('JARVIS_NOTION_PROJECTS', NOTION_KSM).split(',') if x.strip()}
NOTION_VERSION = '2025-09-03'
NOTION_TIMEOUT = 45   # 초. Notion 이 가끔 느려 20초로는 시간 초과가 났다(2026-11-08)
KST = timezone(timedelta(hours=9))
_notion = {'at': 0, 'data': None, 'rows': [], 'names': {}}
_notion_lock = threading.Lock()


def notion_token():
    t = os.environ.get('NOTION_TOKEN', '').strip()
    if t:
        return t
    try:
        with open(NOTION_TOKEN_FILE, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def notion_open(req, opener=None):
    """Notion 요청 하나 -> JSON. 시간 초과·연결 오류면 한 번 더 시도한다. HTTP 오류(4xx·5xx)는 그대로 올린다."""
    op = opener or urllib.request.urlopen
    for attempt in (1, 2):
        try:
            with op(req, timeout=NOTION_TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, socket.timeout, urllib.error.URLError):
            if attempt == 2:
                raise


def uuid_dashed(x):
    x = x.replace('-', '')
    return f'{x[:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:]}' if len(x) == 32 else x


def week_range(today):
    """월요일 시작 이번 주 (월, 일) 날짜 문자열."""
    d = date.fromisoformat(today)
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def notion_row(p):
    """Notion 페이지 하나 -> 할 일 한 줄(이름, 완료, 날짜, 유형, KSM 여부, 링크)."""
    props = p.get('properties') or {}

    def prop(name, kind):
        v = props.get(name) or {}
        return v.get(kind) if v.get('type') == kind else None

    title = ''.join(t.get('plain_text', '') for t in (prop('이름', 'title') or []))
    d = prop('날짜', 'date') or {}
    rel = [str(r.get('id', '')).replace('-', '') for r in (prop('프로젝트', 'relation') or [])]
    return {'id': p.get('id', ''), 'url': p.get('url', ''), 'title': title or '(제목 없음)',
            'done': bool(prop('완료', 'checkbox')), 'date': (d.get('start') or '')[:10], 'end': (d.get('end') or '')[:10],
            'types': [o.get('name', '') for o in (prop('유형', 'multi_select') or [])],
            'ksm': NOTION_KSM in rel, 'project': bool(rel), 'projects': rel, 'edited': p.get('last_edited_time', '')}


def only_projects(rows):
    """NOTION_ONLY 에 든 프로젝트의 할 일만. NOTION_ONLY 가 비면 그대로."""
    return [r for r in rows if set(r['projects']) & NOTION_ONLY] if NOTION_ONLY else rows


def group_todos(rows, today):
    """할 일 줄 -> 오늘 · 이번 주 · 다음 주 · 이번 주 전(프로젝트 있음 / 없음) · 날짜 없는 일(프로젝트 있음). 주는 월요일 시작.
    기간(시작~끝)인 할 일은 오늘이 그 안에 들면 오늘, 아니면 시작 날짜로 나눈다."""
    mon, sun = week_range(today)
    nmon, nsun = week_range((date.fromisoformat(sun) + timedelta(days=1)).isoformat())
    pmon = (date.fromisoformat(mon) - timedelta(days=7)).isoformat()
    out = {'today': [], 'week': [], 'next': [], 'overdue': [], 'overdue_other': [], 'undated': [], 'done': []}
    for r in rows:
        dt = r['date']
        if dt and (dt == today or dt <= today <= (r.get('end') or '')):
            out['today'].append(r)   # 오늘은 완료도 보인다
        elif r['done']:
            if dt and pmon <= dt <= nsun:
                out['done'].append(r)   # 완료: 지난주 월요일 ~ 다음 주 일요일
        elif not dt:
            if r['project']:
                out['undated'].append(r)   # 날짜 없고 프로젝트도 없는 옛 메모는 뺀다
        elif mon <= dt <= sun:
            out['week'].append(r)   # Notion '이번주 할일' 보기처럼 이번 주 지난 날도 여기
        elif nmon <= dt <= nsun:
            out['next'].append(r)
        elif dt < mon:
            out['overdue' if r['project'] else 'overdue_other'].append(r)
    out['today'].sort(key=lambda r: (r['done'], r['title']))
    for k in ('week', 'next'):
        out[k].sort(key=lambda r: (r['date'], r['title']))
    out['done'].sort(key=lambda r: (r['date'], r['title']), reverse=True)
    for k in ('overdue', 'overdue_other'):
        out[k].sort(key=lambda r: (r['date'], r['title']), reverse=True)   # 최근에 밀린 것이 위
    out['undated'].sort(key=lambda r: r['edited'], reverse=True)
    return out


def notion_query(token, today, opener=None):
    """완료 안 된 행과 지난주 월요일부터의 행(완료 포함)을 모두 읽는다. 100행씩, 최대 10쪽."""
    mon, _ = week_range(today)
    pmon = (date.fromisoformat(mon) - timedelta(days=7)).isoformat()
    when = {'or': [{'property': '완료', 'checkbox': {'equals': False}},
                   {'property': '날짜', 'date': {'on_or_after': pmon}}]}
    if NOTION_ONLY:   # 보일 프로젝트만 Notion 에서 바로 거른다(읽는 양을 줄여 시간 초과를 막음)
        when = {'and': [when, {'or': [{'property': '프로젝트', 'relation': {'contains': uuid_dashed(x)}} for x in sorted(NOTION_ONLY)]}]}
    body = {'page_size': 100, 'filter': when}
    rows = []
    for _ in range(10):
        req = urllib.request.Request(f'https://api.notion.com/v1/data_sources/{NOTION_DS}/query',
                                     data=json.dumps(body).encode(), method='POST', headers={
                                         'Authorization': 'Bearer ' + token, 'Notion-Version': NOTION_VERSION,
                                         'Content-Type': 'application/json'})
        d = notion_open(req, opener)
        rows += [notion_row(p) for p in d.get('results') or [] if p.get('object') == 'page']
        if not d.get('has_more'):
            break
        body['start_cursor'] = d.get('next_cursor')
    return rows


def project_names(token, rows, opener=None):
    """할 일에 붙은 프로젝트 페이지 id -> 이름. 한 번 읽은 이름은 기억한다. 못 읽으면 id 앞 8자."""
    names = _notion['names']
    for pid in sorted({p for r in rows for p in r['projects']}):
        if pid in names:
            continue
        req = urllib.request.Request(f'https://api.notion.com/v1/pages/{pid}', headers={
            'Authorization': 'Bearer ' + token, 'Notion-Version': NOTION_VERSION})
        try:
            props = notion_open(req, opener).get('properties') or {}
            names[pid] = ''.join(t.get('plain_text', '') for v in props.values() if v.get('type') == 'title'
                                 for t in v.get('title') or []) or pid[:8]
        except (OSError, ValueError):
            names[pid] = pid[:8]
    return {pid: names[pid] for pid in {p for r in rows for p in r['projects']}}


def notion_todos(now=None, opener=None, refresh=False):
    """할 일 탭 데이터. 처음 한 번 읽고, 그 뒤로는 refresh(화면의 새로고침 버튼)일 때만 다시 읽는다.
    실패하면 마지막으로 읽은 것을 오류와 함께 돌려준다."""
    now = time.time() if now is None else now
    with _notion_lock:
        if _notion['data'] is not None and not refresh:
            return _notion['data']
        today = datetime.fromtimestamp(now, KST).date().isoformat()
        token = notion_token()
        if not token:
            data = {'configured': False, 'today': today, 'error': '', 'at': 0, 'sections': None}
        else:
            prev = _notion['data'] or {}
            try:
                rows = only_projects(notion_query(token, today, opener))
                _notion['rows'] = rows
                data = {'configured': True, 'today': today, 'error': '', 'at': int(now * 1000),
                        'sections': group_todos(rows, today), 'projects': project_names(token, rows, opener)}
            except urllib.error.HTTPError as e:
                why = {401: '토큰이 맞지 않음', 403: '권한 없음', 404: '할일 DB 를 못 찾음(통합을 DB 에 연결했는지 확인)'}.get(e.code, '')
                data = dict(prev, configured=True, today=today, error=f'Notion {e.code} {why}'.strip())
            except (OSError, ValueError) as e:
                data = dict(prev, configured=True, today=today, error='Notion 읽기 실패: ' + str(e)[:120])
            data.setdefault('sections', None)
            data.setdefault('at', 0)
        _notion.update(at=now, data=data)
        return data


def notion_counts(data):
    """탭 옆 숫자: 오늘 남은 일(초록), 이번 주 남은 일(회색)."""
    s = (data or {}).get('sections') or {}
    return {'in_progress': sum(not r['done'] for r in s.get('today', [])), 'pending': len(s.get('week', []))}


PAGE_ID_RE = re.compile(r'^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$')
DATE_RE = re.compile(r'^\d{4}-\d\d-\d\d$')


def notion_update(page_id, done=None, day=None, opener=None, now=None):
    """할 일 한 줄 고치기: 완료 체크(done) 또는 날짜(day, YYYY-MM-DD). Notion 에 쓰고 캐시도 같이 고친다.
    돌려주는 값: (상태 코드, 할 일 탭 데이터 또는 {'error': …})."""
    if not isinstance(page_id, str) or not PAGE_ID_RE.match(page_id):
        return 400, {'error': 'id 가 맞지 않음'}
    props = {}
    if done is not None:
        if not isinstance(done, bool):
            return 400, {'error': 'done 은 true/false'}
        props['완료'] = {'checkbox': done}
    if day is not None:
        if not isinstance(day, str) or not DATE_RE.match(day):
            return 400, {'error': 'date 는 YYYY-MM-DD'}
        props['날짜'] = {'date': {'start': day}}
    if not props:
        return 400, {'error': '바꿀 것이 없음'}
    token = notion_token()
    if not token:
        return 400, {'error': 'Notion 토큰 없음'}
    req = urllib.request.Request(f'https://api.notion.com/v1/pages/{page_id}', method='PATCH',
                                 data=json.dumps({'properties': props}).encode(), headers={
                                     'Authorization': 'Bearer ' + token, 'Notion-Version': NOTION_VERSION,
                                     'Content-Type': 'application/json'})
    try:
        row = notion_row(notion_open(req, opener))
    except urllib.error.HTTPError as e:
        why = {401: '토큰이 맞지 않음', 403: '쓰기 권한 없음(통합의 콘텐츠 업데이트 기능을 켜야 함)',
               404: '페이지를 못 찾음'}.get(e.code, '')
        return 502, {'error': f'Notion {e.code} {why}'.strip()}
    except (OSError, ValueError) as e:
        return 502, {'error': 'Notion 쓰기 실패: ' + str(e)[:120]}
    now = time.time() if now is None else now
    with _notion_lock:
        rows = only_projects([x for x in _notion['rows'] if x['id'].replace('-', '') != page_id.replace('-', '')] + [row])
        _notion['rows'] = rows
        data = _notion['data'] or {'configured': True, 'error': '', 'at': 0}
        today = datetime.fromtimestamp(now, KST).date().isoformat()
        data = dict(data, today=today, sections=group_todos(rows, today))
        _notion['data'] = data
    return 200, data


# ---------- Notion 루틴 (할 일 탭 맨 위) ----------
# 루틴 항목: 이름·시간대·요일·사용·순서. 루틴 기록: 날짜별 한 줄(이름·날짜·완료·시간대·항목). 그날 처음 읽을 때 오늘 요일에 든
# 항목 중 기록이 없는 것을 만든다(통합에 콘텐츠 입력 기능이 있어야 함). 완료 체크는 notion_patch 로 쓴다.
ROUTINE_ITEMS_DS = os.environ.get('JARVIS_ROUTINE_ITEMS_DS') or 'e1640ede-1674-4fd4-b6cb-ccf7ed4f50f5'
ROUTINE_LOG_DS = os.environ.get('JARVIS_ROUTINE_LOG_DS') or '22fe9031-5b32-4363-989a-b7e233a842ad'
ROUTINE_ITEMS_URL = 'https://app.notion.com/p/8858ddf3ade34c58a433712859a05fdd'
WEEKDAYS = '월화수목금토일'
SLOT_ORDER = {'아침': 0, '낮': 1, '저녁': 2}
ROUTINE_DAYS = 7   # 최근 며칠의 했는지 점
_routine = {'data': None, 'today': ''}
_routine_lock = threading.Lock()


def notion_call(token, method, path, body=None, opener=None):
    req = urllib.request.Request('https://api.notion.com/v1/' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers={
                                     'Authorization': 'Bearer ' + token, 'Notion-Version': NOTION_VERSION,
                                     'Content-Type': 'application/json'})
    return notion_open(req, opener)


def query_all(token, ds, body, opener=None, pages=10):
    rows, body = [], dict(body, page_size=100)
    for _ in range(pages):
        d = notion_call(token, 'POST', f'data_sources/{ds}/query', body, opener)
        rows += [p for p in d.get('results') or [] if p.get('object') == 'page']
        if not d.get('has_more'):
            break
        body['start_cursor'] = d.get('next_cursor')
    return rows


def _prop(p, name, kind):
    v = (p.get('properties') or {}).get(name) or {}
    return v.get(kind) if v.get('type') == kind else None


def routine_item(p):
    return {'id': p.get('id', '').replace('-', ''), 'name': ''.join(t.get('plain_text', '') for t in _prop(p, '이름', 'title') or []) or '(이름 없음)',
            'slot': (_prop(p, '시간대', 'select') or {}).get('name', ''), 'days': [o.get('name', '') for o in _prop(p, '요일', 'multi_select') or []],
            'order': _prop(p, '순서', 'number') or 0, 'note': ''.join(t.get('plain_text', '') for t in _prop(p, '설명', 'rich_text') or []),
            'weekly': int(_prop(p, '주 횟수', 'number') or 0)}   # 0 = 요일마다, N = 이번 주(월~일) N번


def routine_log(p):
    rel = [str(r.get('id', '')).replace('-', '') for r in _prop(p, '항목', 'relation') or []]
    return {'id': p.get('id', ''), 'url': p.get('url', ''), 'item': rel[0] if rel else '',
            'name': ''.join(t.get('plain_text', '') for t in _prop(p, '이름', 'title') or []) or '(이름 없음)',
            'date': ((_prop(p, '날짜', 'date') or {}).get('start') or '')[:10], 'done': bool(_prop(p, '완료', 'checkbox')),
            'slot': (_prop(p, '시간대', 'select') or {}).get('name', '')}


def routine_view(items, logs, today):
    """오늘 줄(시간대·순서대로)과 항목별 최근 ROUTINE_DAYS 일의 했는지(done / miss / none, 오늘 안 한 것은 todo).
    주 N회 항목은 이번 주(월~일) 한 횟수를 weekly 로 붙이고, 안 한 지난날은 miss 대신 rest. 오늘 n/m 에는 요일마다 하는 것만 센다."""
    order = {i['id']: (SLOT_ORDER.get(i['slot'], 9), i['order'], i['name']) for i in items}
    target = {i['id']: i.get('weekly') or 0 for i in items}
    rows = sorted([dict(r) for r in logs if r['date'] == today],
                  key=lambda r: order.get(r['item'], (SLOT_ORDER.get(r['slot'], 9), 99, r['name'])))
    monday = (date.fromisoformat(today) - timedelta(days=date.fromisoformat(today).weekday())).isoformat()
    for r in rows:
        if target.get(r['item']):
            r['weekly'] = {'target': target[r['item']],
                           'count': sum(1 for x in logs if x['item'] == r['item'] and x['done'] and monday <= x['date'] <= today)}
    d0 = date.fromisoformat(today)
    days = [(d0 - timedelta(days=k)).isoformat() for k in range(ROUTINE_DAYS - 1, -1, -1)]
    by = {(r['item'], r['date']): r for r in logs}
    hist = {}
    for r in rows:
        idle = 'rest' if r.get('weekly') else 'miss'
        hist[r['id']] = [('done' if by[(r['item'], d)]['done'] else 'todo' if d == today else idle) if (r['item'], d) in by else 'none'
                         for d in days]
    daily = [r for r in rows if not r.get('weekly')]
    return {'rows': rows, 'days': days, 'hist': hist, 'done': sum(r['done'] for r in daily), 'total': len(daily)}


def notion_routines(now=None, opener=None, refresh=False):
    """오늘 루틴. 처음 한 번과 refresh, 날짜가 바뀌었을 때만 Notion 을 읽는다."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, KST).date().isoformat()
    with _routine_lock:
        if _routine['data'] is not None and not refresh and _routine['today'] == today:
            return _routine['data']
        token = notion_token()
        weekday = WEEKDAYS[date.fromisoformat(today).weekday()]
        base = {'today': today, 'weekday': weekday, 'itemsUrl': ROUTINE_ITEMS_URL, 'error': ''}
        if not token:
            return dict(base, configured=False)
        try:
            items = [routine_item(p) for p in query_all(token, ROUTINE_ITEMS_DS, {'filter': {'property': '사용', 'checkbox': {'equals': True}}}, opener)]
            since = (date.fromisoformat(today) - timedelta(days=ROUTINE_DAYS - 1)).isoformat()
            logs = [routine_log(p) for p in query_all(token, ROUTINE_LOG_DS, {'filter': {'property': '날짜', 'date': {'on_or_after': since}}}, opener)]
            have = {r['item'] for r in logs if r['date'] == today}
            for it in items:   # 오늘 요일에 든 항목인데 오늘 기록이 없으면 만든다
                if weekday not in it['days'] or it['id'] in have:
                    continue
                props = {'이름': {'title': [{'text': {'content': it['name']}}]}, '날짜': {'date': {'start': today}},
                         '완료': {'checkbox': False}, '항목': {'relation': [{'id': it['id']}]}}
                if it['slot']:
                    props['시간대'] = {'select': {'name': it['slot']}}
                logs.append(routine_log(notion_call(token, 'POST', 'pages', {'parent': {'type': 'data_source_id', 'data_source_id': ROUTINE_LOG_DS},
                                                                            'properties': props}, opener)))
                have.add(it['id'])
            data = dict(base, configured=True, at=int(now * 1000), **routine_view(items, logs, today))
            _routine['logs'], _routine['items'] = logs, items
        except urllib.error.HTTPError as e:
            why = {401: '토큰이 맞지 않음', 403: '권한 없음(통합에 콘텐츠 입력 기능을 켜야 오늘 루틴 줄을 만듦)', 404: '루틴 DB 를 못 찾음(통합 연결 확인)'}.get(e.code, '')
            data = dict(_routine['data'] or {}, **base, configured=True, error=f'Notion {e.code} {why}'.strip())
        except (OSError, ValueError) as e:
            data = dict(_routine['data'] or {}, **base, configured=True, error='Notion 읽기 실패: ' + str(e)[:120])
        _routine.update(data=data, today=today)
        return data


def routine_check(page_id, done, opener=None, now=None):
    """루틴 기록 한 줄의 완료를 바꾼다. (상태 코드, 루틴 데이터 또는 {'error': …})."""
    if not isinstance(page_id, str) or not PAGE_ID_RE.match(page_id) or not isinstance(done, bool):
        return 400, {'error': 'id 와 done(true/false) 필요'}
    token = notion_token()
    if not token:
        return 400, {'error': 'Notion 토큰 없음'}
    try:
        row = routine_log(notion_call(token, 'PATCH', f'pages/{page_id}', {'properties': {'완료': {'checkbox': done}}}, opener))
    except urllib.error.HTTPError as e:
        return 502, {'error': f'Notion {e.code} ' + {403: '쓰기 권한 없음', 404: '페이지를 못 찾음'}.get(e.code, '')}
    except (OSError, ValueError) as e:
        return 502, {'error': 'Notion 쓰기 실패: ' + str(e)[:120]}
    now = time.time() if now is None else now
    with _routine_lock:
        logs = [x for x in _routine.get('logs') or [] if x['id'].replace('-', '') != page_id.replace('-', '')] + [row]
        _routine['logs'] = logs
        if _routine['data'] is not None:
            today = _routine['today'] or datetime.fromtimestamp(now, KST).date().isoformat()
            _routine['data'] = dict(_routine['data'], **routine_view(_routine.get('items') or [], logs, today))
        return 200, _routine['data']
