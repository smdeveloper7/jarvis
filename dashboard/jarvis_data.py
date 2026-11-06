"""JARVIS 대시보드 데이터: Paseo 에이전트 기록과 JARVIS state/ 파일을 lanekeeper 대시보드가 읽는 형태로 바꾼다.

읽기만 한다. 출처
- Paseo: ~/.paseo/agents/<폴더>/<에이전트 ID>.json (제목, 부모 ID, 상태, Claude 세션 ID)
- JARVIS: registry.yaml, state/tasks/*.md, state/decisions/*.md, state/occupancy.json, state/releases.json
- 대화 기록: ~/.claude/projects/<cwd>/<세션 ID>.jsonl (배정·보고 같은 머리말 메시지만 뽑음)

역할 매핑(lanekeeper 화면 기준)
- JARVIS(제목이 JARVIS 인 에이전트) -> group resident, lane 'HUB' (오피스의 허브 자리, 흐름도 가운데)
- Coordinator(registry 의 coordinator 이거나 부모가 JARVIS) -> group resident, lane 프로젝트 키
- Worker(부모가 Coordinator) -> group task, lane 프로젝트 키, task 작업 ID(제목의 W-…)
"""
import glob
import json
import os
import re
import time
from datetime import datetime

PASEO = os.path.abspath(os.path.expanduser(os.environ.get('PASEO_HOME') or '~/.paseo'))
JARVIS_NAME = os.environ.get('JARVIS_NAME') or 'JARVIS'
BOSS = os.environ.get('JARVIS_BOSS') or '사장'   # 사장실 명패: JARVIS 에게 채팅하는 사람(이름 대신 직책)
TAGS = ('배정', '보고', '질문', '진행', '승인요청', '점유', '해제', '완료')
TAG_RE = re.compile(r'^\[(' + '|'.join(TAGS) + r')\]', re.M)
TASK_RE = re.compile(r'W-\d{8}-\d+')
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


def norm(p):
    return (p or '').replace('\\', '/').rstrip('/').casefold()


# ---------- Paseo 에이전트 ----------

def paseo_records(root=PASEO):
    recs = []
    for p in glob.glob(os.path.join(root, 'agents', '*', '*.json')):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get('archivedAt') or d.get('internal'):
            continue
        recs.append(d)
    return recs


def roles(recs, reg):
    """에이전트 ID -> (역할, 프로젝트 키). 역할: jarvis | coordinator | worker | other."""
    by_id = {r['id']: r for r in recs}
    parent = {r['id']: (r.get('labels') or {}).get('paseo.parent-agent-id') for r in recs}
    jarvis = [r for r in recs if (r.get('title') or '') == JARVIS_NAME and r.get('lastStatus') != 'closed']
    jarvis.sort(key=lambda r: r.get('lastActivityAt') or '', reverse=True)
    jid = jarvis[0]['id'] if jarvis else None
    coord_of = {v.get('coordinator'): k for k, v in reg.items() if v.get('coordinator')}
    root_of = {norm(v.get('root')): k for k, v in reg.items() if v.get('root')}
    out = {}
    for r in recs:
        i = r['id']
        proj = root_of.get(norm(r.get('cwd')))
        if i == jid:
            out[i] = ('jarvis', '')
        elif i in coord_of:
            out[i] = ('coordinator', coord_of[i])
        elif jid and parent.get(i) == jid:
            out[i] = ('coordinator', proj or '')
        else:
            out[i] = None
    for r in recs:   # 부모가 Coordinator 면 Worker
        i = r['id']
        if out[i] is None:
            p = out.get(parent.get(i))
            out[i] = ('worker', p[1] or root_of.get(norm(r.get('cwd'))) or '') if p and p[0] == 'coordinator' else ('other', '')
    return out, jid, by_id


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
    elif role == 'worker' or st == 'closed':
        row.update(status='idle', state='done')
    else:
        row.update(status='idle', state='idle')
    if role == 'jarvis':
        row.update(group='resident', lane='HUB', task='')
    elif role == 'coordinator':
        row.update(group='resident', lane=proj or '프로젝트', task='')
    elif role == 'worker':
        m = TASK_RE.search(title)
        row.update(group='task', lane=proj, task=m.group(0) if m else title)
    else:
        row.update(group='other', lane='', task='')
    return row


def agent_rows(jarvis, root=PASEO, now_ms=None):
    """(rows, roster, hub 이름). 오래전에 끝난 Worker 는 뺀다."""
    now_ms = now_ms or int(time.time() * 1000)
    recs = paseo_records(root)
    reg = registry(jarvis)
    rl, jid, by_id = roles(recs, reg)
    rows, names = [], {}
    for r in recs:
        role, proj = rl[r['id']]
        if role == 'other':
            continue
        row = row_of(r, role, proj, now_ms)
        last = iso_ms(r.get('lastActivityAt') or r.get('updatedAt'))
        if role == 'worker' and row['state'] == 'done' and last and now_ms - last > WORKER_DONE_S * 1000:
            continue
        rows.append(row)
        names[row['name']] = {'group': row['group'], 'lane': row['lane'], 'task': row['task'], 'order': len(names)}
    hub = by_id[jid].get('title') if jid else None
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


def messages(rows, transcript_path, hours=24, now=None, max_messages=300):
    """받은 쪽 대화 기록의 머리말 메시지로 흐름을 만든다. 보낸 쪽: 배정은 부모, 그 밖은 작업 ID 의 Coordinator 나 자식."""
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
        e = edges.setdefault((m['from'], m['to']), {'from': m['from'], 'to': m['to'], 'count': 0, 'last': 0})
        e['count'] += 1
        e['last'] = max(e['last'], m['t'])
        for k, n in (('sent', m['from']), ('got', m['to'])):
            count.setdefault(n, {'sent': 0, 'got': 0})[k] += 1
    for r in rows:   # 메시지가 없어도 구조는 보이게: 부모-자식 선은 최소 1
        p = parent_of.get(r['name'])
        if p and (p, r['name']) not in edges:
            edges[(p, r['name'])] = {'from': p, 'to': r['name'], 'count': 0, 'last': 0}
        for n in (p, r['name']):
            if n:
                count.setdefault(n, {'sent': 0, 'got': 0})
    first = {r['name']: r for r in rows}
    nodes = []
    for n, c in count.items():
        a = first.get(n) or {}
        nodes.append(dict(c, name=n, group=a.get('group', 'task'), lane=a.get('lane', ''), task=a.get('task', ''),
                          listed=bool(a), **{k: a.get(k) for k in ('state', 'status', 'kind', 'engine', 'sessionId')}))
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
    pending = sum(1 for d in decisions(jarvis) if d['status'] not in ('waiting', 'decided'))
    pending += sum(1 for r in rows if r.get('state') == 'blocked')
    return {'boss': BOSS, 'projects': projects, 'pending': pending}


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
        items.append({'id': meta.get('id', ''), 'status': meta.get('status') or 'open', 'title': title,
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
