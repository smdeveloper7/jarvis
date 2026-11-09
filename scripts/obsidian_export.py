#!/usr/bin/env python3
"""JARVIS State → Obsidian 읽기 전용 노트 (docs/01 5절).

실행 상태의 원본은 JARVIS/state 와 registry.yaml 이다. Obsidian 에서 보려고 그 내용을 마크다운 노트로 만들어
보관함(worklog/wiki/jarvis/)에 둔다. 한 방향(State → Obsidian)이고, 노트를 고쳐도 상태는 바뀌지 않는다.

  python scripts/obsidian_export.py            # 현황.md, 결정.md 갱신
  python scripts/obsidian_export.py --dry-run  # 쓰지 않고 내용만 출력

만드는 노트
- 현황.md: 프로젝트별 작업판, 운영 반영 대기, 공유 자원 점유 (대시보드 "현황" 탭과 같은 내용)
- 결정.md: 지금 정할 것(open)과 미뤄 둔 것(waiting)

보관함 위치는 환경변수 JARVIS_WORKLOG, 없으면 C:/Users/SMDEVELOPER/projects/worklog.
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JDIR = os.path.abspath(os.environ.get('JARVIS_DIR') or os.path.join(HERE, '..'))
CODE = os.path.abspath(os.path.join(HERE, '..'))     # 코드는 이 저장소에서, 데이터는 JDIR 에서
sys.path.insert(0, os.path.join(CODE, 'dashboard'))
sys.path.insert(0, os.path.join(CODE, 'hooks'))
import jarvis_data as jd  # noqa: E402
from jarvis_state import FileLock, front, now_iso  # noqa: E402

WORKLOG = os.environ.get('JARVIS_WORKLOG') or 'C:/Users/SMDEVELOPER/projects/worklog'
STATUS_KO = {'open': '지금 결정', 'waiting': '미룸'}


def header(title, src):
    return ('---\nsource: %s\ngenerated: %s\n---\n'
            '> 자동 생성 노트. 여기서 고치지 않는다(다음 갱신 때 덮어씀). 원본: `%s`. '
            '갱신: `python scripts/obsidian_export.py`\n\n' % (src, now_iso(), src))


def status_note():
    body = jd.status_markdown(JDIR)
    return header('현황', 'JARVIS/state, JARVIS/registry.yaml') + body


def decisions_note():
    rows = []
    for p in sorted(glob.glob(os.path.join(JDIR, 'state', 'decisions', 'D-*.md'))):
        m = front(p)
        if m.get('status') not in ('open', 'waiting'):
            continue
        q = (jd.section_text(jd.frontmatter(p)[1], '질문').strip().split('\n') or [''])[0]
        rows.append((m, q))
    lines = ['# 열린 결정', '']
    if not rows:
        lines.append('없음.')
    else:
        lines += ['| 결정 | 프로젝트 | 작업 | 상태 | 질문 | 추천 | 미룬 기한 |', '|---|---|---|---|---|---|---|']
        for m, q in rows:
            lines.append('| `{}` | {} | {} | {} | {} | {} | {} |'.format(
                m.get('id', ''), m.get('project', ''), m.get('task') or '-', STATUS_KO.get(m.get('status'), ''),
                (q or '').replace('|', '/')[:120], (m.get('recommended') or '').strip('"') or '-',
                m.get('until') or '-'))
    lines += ['', '결정은 JARVIS 가 `jarvisctl decide` 로 확정한다. 이 노트에서 고치지 않는다.']
    return header('결정', 'JARVIS/state/decisions') + '\n'.join(lines) + '\n'


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with FileLock(os.path.join(JDIR, 'state', 'obsidian_export')):   # 잠금 파일은 보관함(OneDrive) 밖에
        tmp = path + '.tmp%d' % os.getpid()
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='obsidian_export')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    out_dir = os.path.join(WORKLOG, 'wiki', 'jarvis')
    notes = {'현황.md': status_note(), '결정.md': decisions_note()}
    for name, text in notes.items():
        if a.dry_run:
            print('=== %s ===\n%s' % (name, text))
        else:
            write(os.path.join(out_dir, name), text)
    if not a.dry_run:
        print('ok %s' % ', '.join(os.path.join(out_dir, n) for n in notes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
