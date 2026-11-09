#!/usr/bin/env python3
"""SessionStart 훅: 역할에 맞는 맥락을 넣는다 (docs/02 4절, docs/01 7.3절).

- PM: 규칙(rules/coordinator.md), 프로젝트·JARVIS ID, 인계 노트, 열린 작업, 열린 결정 경로.
  startup·resume·compact·clear 모두. 새로 만들 때와 교체할 때 같은 초기화가 되게 한다.
- JARVIS 창(여러 개일 수 있음): 기본 수신자 여부, PM 현황, 닫힌 PM 대조 결과, manager 인계 노트 위치.
그 밖의 세션에서는 아무것도 하지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uuid  # noqa: E402

from jarvis_state import (closed_state, current_role, emit_context, jarvis_dir, now_iso, open_decisions,  # noqa: E402
                          open_tasks, read_input, read_json, state, stdio_utf8, write_json)
from pm_reconcile import reconcile  # noqa: E402

MAX_HANDOFF_LINES = 80
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
