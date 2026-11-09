#!/usr/bin/env python3
"""SessionStart 훅: 역할에 맞는 맥락을 넣는다 (docs/02 3절, docs/04 7절).

내용이 아니라 지도를 준다. 처음에는 무엇이 있는지만 짧게 넣고, 내용은 필요할 때 찾게 한다.
- PM: 규칙(rules/coordinator.md), 프로젝트·JARVIS ID, 인계 노트, 열린 작업, 열린 결정 경로,
  위키 개요(<프로젝트>/개요.md)와 결정·해결법 제목 목록. startup·resume·compact·clear 모두.
- JARVIS 창(여러 개일 수 있음): 기본 수신자 여부, PM 현황, 닫힌 PM 대조 결과, manager 인계 노트 내용,
  열린 W·D, 판단 기준, 위키 최근 결정 제목, 위키 정리가 밀렸을 때만 경고.
그 밖의 세션에서는 아무것도 하지 않는다. 위키를 못 읽어도 나머지는 그대로 넣는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glob  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from datetime import date  # noqa: E402

from jarvis_state import (closed_state, current_role, emit_context, jarvis_dir, now_iso, open_decisions,  # noqa: E402
                          open_tasks, read_input, read_json, state, stdio_utf8, write_json)
from pm_reconcile import reconcile  # noqa: E402

MAX_HANDOFF_LINES = 60          # PM 인계 노트 (docs/04 6.2 와 같은 상한)
MAX_MANAGER_HANDOFF_LINES = 30  # JARVIS 창은 다른 항목도 있어 더 짧게
WORKLOG = os.environ.get('JARVIS_WORKLOG_DIR') or 'C:/Users/SMDEVELOPER/projects/worklog'
WIKI_LAG_DAYS = 2               # 위키 정리가 이만큼 밀렸을 때만 경고
HOOK_TAG = 'jarvis-hooks v1'   # [준비] 에 '표식#nonce' 를 적게 하고 JARVIS 가 jarvisctl hook-check 로 맞춰 본다


def hook_tag(jdir, agent_id, source):
    """세션마다 새 nonce 를 state/sessions/<ID>.hook.json 에 남긴다. 규칙 파일에는 없는 값이라 훅이 돈 증거가 된다."""
    nonce = uuid.uuid4().hex[:6]
    write_json(state(jdir, 'sessions', agent_id + '.hook.json'), {'nonce': nonce, 'at': now_iso(), 'source': source})
    return '%s#%s' % (HOOK_TAG, nonce)


def read_text(path, limit=None):
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if limit and len(lines) > limit:
        lines = lines[:limit] + ['… (%d줄 생략, 파일 직접 확인)' % (len(lines) - limit)]
    return '\n'.join(lines)


def strip_front(text):
    if text and text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end > 0:
            return text[end + 5:].lstrip('\n')
    return text


def note_title(path):
    """노트의 첫 '# ' 제목, 없으면 파일 이름."""
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('# '):
                    return line[2:].strip()
    except OSError:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def wiki_notes(proj, kind, limit):
    """위키 <프로젝트>/<kind>/ 노트를 최근 고친 순으로 (제목, 보관함 기준 링크)."""
    d = os.path.join(WORKLOG, 'wiki', proj, kind)
    paths = sorted(glob.glob(os.path.join(d, '*.md')), key=os.path.getmtime, reverse=True)[:limit]
    return [(note_title(p), 'wiki/%s/%s/%s' % (proj, kind, os.path.splitext(os.path.basename(p))[0])) for p in paths]


def wiki_project_lines(proj):
    """PM 맥락: 그 프로젝트 위키 개요(25줄)와 결정·해결법 제목(최대 15개)."""
    out = []
    ov = os.path.join(WORKLOG, 'wiki', proj, '개요.md')
    text = read_text(ov)
    if text:
        lines = strip_front(text).splitlines()
        if len(lines) > 25:
            lines = lines[:25] + ['… (나머지는 %s)' % ov.replace('\\', '/')]
        out += ['', '## 프로젝트 위키 개요 (%s)' % ov.replace('\\', '/')] + lines
    titles = [('결정', t, l) for t, l in wiki_notes(proj, 'decisions', 10)] + \
             [('해결법', t, l) for t, l in wiki_notes(proj, '해결법', 5)]
    if titles:
        out += ['', '## 위키 노트 제목 (본문은 필요할 때 %s 에서 연다)' % WORKLOG]
        out += ['- [%s] %s  [[%s]]' % (k, t, l) for k, t, l in titles]
    return out


def wiki_lag():
    """위키 정리가 며칠 밀렸나: 가장 최근 일지 날짜 - log.md 의 마지막 정리 날짜. 모르면 (None, …)."""
    raws = sorted(glob.glob(os.path.join(WORKLOG, 'raw', '????-??-??.md')))
    log = read_text(os.path.join(WORKLOG, 'wiki', 'log.md'))
    if not raws or not log:
        return None, None
    dates = re.findall(r'^- (\d{4}-\d{2}-\d{2}) ', log, re.M)
    newest = os.path.basename(raws[-1])[:10]
    if not dates:
        return None, newest
    try:
        return (date.fromisoformat(newest) - date.fromisoformat(dates[-1])).days, dates[-1]
    except ValueError:
        return None, dates[-1]


def jarvis_memory_lines(jdir, reg, ho):
    """JARVIS 창 맥락의 기억 부분(docs/04 7절): 인계 노트 → 열린 W·D → 판단 기준 → 최근 결정 제목 → 위키 밀림."""
    out = []
    text = read_text(ho, MAX_MANAGER_HANDOFF_LINES)
    if text:
        out += ['', '## manager 인계 노트 (상태 파일과 다르면 상태 파일이 맞다)', text]
    projs = list(reg['projects']) + ['jarvis']
    tasks = [t for k in projs for t in open_tasks(jdir, k)]
    out += ['', '## 열린 작업 W (jarvisctl task 로 바꾼다)']
    out += ['- %s [%s] %s · %s' % (t.get('id'), t.get('status'), t.get('project'), t.get('title')) for t in tasks[:12]]
    if len(tasks) > 12:
        out.append('- … 외 %d건 (state/tasks)' % (len(tasks) - 12))
    if not tasks:
        out.append('- 없음')
    decs = [m for k in projs for _, m in open_decisions(jdir, k)]
    if decs:
        out += ['', '## 열린 결정 D']
        out += ['- %s [%s] %s' % (m.get('id'), m.get('status'), m.get('project')) for m in decs[:8]]
    crit = read_text(os.path.join(jdir, 'criteria.md'))
    items = [ln for ln in strip_front(crit or '').splitlines() if re.match(r'^\s*(\d+\.|-)\s', ln)]
    if items:
        out += ['', '## 판단 기준 (criteria.md)'] + items[:10]
    recent = []
    for d in glob.glob(os.path.join(WORKLOG, 'wiki', '*', 'decisions')):
        proj = os.path.basename(os.path.dirname(d))
        recent += [(os.path.getmtime(p), proj, note_title(p)) for p in glob.glob(os.path.join(d, '*.md'))]
    if recent:
        out += ['', '## 위키 최근 결정 (본문은 필요할 때 기억 찾기로)']
        out += ['- %s · %s' % (pj, t) for _, pj, t in sorted(recent, reverse=True)[:5]]
    lag, last = wiki_lag()
    if lag is not None and lag >= WIKI_LAG_DAYS:
        out += ['', '**위키 정리가 %d일 밀림(마지막 %s). 사용자에게 정리할지 묻는다(wiki-curator).**' % (lag, last)]
    return out


def pm_context(jdir, reg, proj, agent_id, source):
    p = reg['projects'].get(proj, {})
    active = p.get('coordinator') == agent_id
    tag = hook_tag(jdir, agent_id, source)
    out = ['# JARVIS PM 맥락 (%s, SessionStart:%s, 훅이 넣음)' % (tag, source),
           '- [준비] 로 답할 때 `맥락: %s` 를 그대로 적는다(JARVIS 가 이 맥락이 들어왔는지 확인한다).' % tag,
           '- 너는 프로젝트 `%s`(%s)의 PM(Coordinator)이다. 작업 폴더: %s'
           % (proj, p.get('name') or proj, p.get('root') or '?'),
           '- 보고·질문을 받는 JARVIS: %s' % (reg.get('jarvis') or '모름: list_agents 에서 제목 JARVIS'),
           '- registry 상태: %s' % ('활성 PM' if active else
                                    '아직 registry 에 연결 전(새로 띄워진 세션). [준비] 로 답하고 [배정] 을 기다려라'),
           '- Worker 규칙: %s' % os.path.join(jdir, 'rules', 'worker.md').replace('\\', '/')]
    if p.get('linear_project'):
        out.append('- Linear Project: %s (팀 PRO). Linear 쓰기는 JARVIS 가 한다' % p['linear_project'])
    rules = read_text(os.path.join(jdir, 'rules', 'coordinator.md'))
    if rules:
        out += ['', '## 규칙 (rules/coordinator.md)', rules]
    ho = state(jdir, 'handoff', proj, 'latest.md')
    text = read_text(ho, MAX_HANDOFF_LINES)
    out += ['', '## 인계 노트 (%s)' % ho.replace('\\', '/')]
    out.append(text if text else '없음 (새 프로젝트이거나 아직 교체한 적 없음)')
    tasks = open_tasks(jdir, proj)
    out += ['', '## 열린 작업 (state/tasks, 이것이 인계 노트보다 우선)']
    out += ['- %s [%s] %s' % (t.get('id'), t.get('status'), t.get('title')) for t in tasks] or ['- 없음']
    decs = open_decisions(jdir, proj)
    out += ['', '## 열린 결정']
    out += ['- %s (%s) %s' % (m.get('id'), m.get('status'), path.replace('\\', '/')) for path, m in decs] or ['- 없음']
    lock = read_json(state(jdir, 'handoff', proj, 'rotating.lock'))
    if lock:
        out += ['', '**지금 이 프로젝트는 PM 교체 중(rotating.lock, 사유 %s)이다. 새 Worker 를 띄우지 마라.**'
                % lock.get('reason')]
        old = lock.get('old_agent_id')
        if old and old != agent_id:
            out.append('- 이전 PM: %s. 그 PM 이 만든 Worker(list_agents 라벨 paseo.parent-agent-id=%s)의 마지막 '
                       '[완료] 를 먼저 확인하고, 아직 받지 못한 결과가 있으면 [준비] 의 확인 필요에 적는다.' % (old, old))
    try:
        out += wiki_project_lines(proj)
    except Exception:   # 위키를 못 읽어도 PM 맥락은 그대로
        pass
    return '\n'.join(out)


def jarvis_context(jdir, reg, agent_id=''):
    msgs = reconcile(jdir, reg)
    ho = state(jdir, 'handoff', 'manager', 'latest.md')
    rep = reg.get('jarvis')
    out = ['# JARVIS 시작 점검 (훅이 넣음)',
           '- JARVIS 창은 여러 개일 수 있다. 상태 파일은 공유하고, 작업·결정 번호는 jarvisctl new-task·new-decision, '
           'registry 는 jarvisctl 로만 바꾼다.',
           '- 기본 수신자(registry jarvis:, 아침 정리 보고·보낸 창이 닫힌 보고를 받음): %s'
           % ('이 창' if rep == agent_id else (rep or '없음')[:8] + (' (닫혀 있으면 jarvisctl claim-jarvis 로 이 창이 맡는다)'
                                                                      if rep else ' (jarvisctl claim-jarvis 로 맡는다)')),
           '- manager 인계 노트: %s' % (ho.replace('\\', '/') if os.path.exists(ho) else '없음')]
    for k, p in reg['projects'].items():
        aid = p.get('coordinator')
        out.append('- %s: PM %s(%s), 열린 작업 %d건' % (k, (aid or '없음')[:8], closed_state(aid) if aid else '-',
                                                       len(open_tasks(jdir, k))))
        if os.path.exists(state(jdir, 'handoff', k, 'rotating.lock')):
            out.append('  - 교체 중(rotating.lock). jarvisctl lock show %s 로 이어 할지 사용자에게 묻는다' % k)
    out += msgs
    try:
        out += jarvis_memory_lines(jdir, reg, ho)
    except Exception:   # 기억 부분이 실패해도 시작 점검은 그대로
        pass
    return '\n'.join(out)


def main():
    stdio_utf8()
    inp = read_input()
    jdir = jarvis_dir()
    if not jdir:
        return 0
    agent_id, role, proj, reg, _ = current_role(inp, jdir)
    source = inp.get('source') or 'startup'
    if role == 'pm':
        emit_context('SessionStart', pm_context(jdir, reg, proj, agent_id, source))
    elif role in ('jarvis', 'jarvis_other'):
        emit_context('SessionStart', jarvis_context(jdir, reg, agent_id))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
