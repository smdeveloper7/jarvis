"""SessionStart 맥락의 기억 부분 (docs/04 7절): 임시 JARVIS·worklog 폴더로 PM·JARVIS 맥락을 만들어 본다."""
import os
import shutil
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix='jarvis-ctx-')
WL = os.path.join(TMP, 'worklog')
J = os.path.join(TMP, 'JARVIS')
os.environ['JARVIS_WORKLOG_DIR'] = WL
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks'))
import session_context as sc  # noqa: E402


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


REG = {'jarvis': 'JV', 'projects': {'nipa': {'name': 'NIPA', 'root': 'C:/p/nipa', 'coordinator': 'PM'}}}
try:
    # 위키가 없어도 맥락은 만들어진다
    assert '프로젝트 위키 개요' not in '\n'.join(sc.wiki_project_lines('nipa'))
    assert sc.wiki_lag() == (None, None)

    w(os.path.join(WL, 'wiki', 'nipa', '개요.md'), '---\ntype: 개요\n---\n## 목표\n' + '줄\n' * 40)
    w(os.path.join(WL, 'wiki', 'nipa', 'decisions', '2026-11-01-a.md'), '---\ntype: 결정\n---\n# 결정 A\n')
    time.sleep(0.05)
    w(os.path.join(WL, 'wiki', 'nipa', 'decisions', '2026-11-09-b.md'), '# 결정 B\n')
    w(os.path.join(WL, 'wiki', 'nipa', '해결법', '배치-0건.md'), '# 배치 0건\n')
    lines = sc.wiki_project_lines('nipa')
    text = '\n'.join(lines)
    assert '## 목표' in text and 'type: 개요' not in text, text            # 머리 칸은 빼고 본문만
    assert '나머지는' in text and len([ln for ln in lines if ln == '줄']) == 24, text   # 개요 25줄 상한
    assert text.index('결정 B') < text.index('결정 A'), text                 # 최근 고친 순
    assert '[해결법] 배치 0건  [[wiki/nipa/해결법/배치-0건]]' in text, text

    # 위키 밀림: 최근 일지 날짜 - log.md 마지막 정리 날짜
    w(os.path.join(WL, 'raw', '2026-11-09.md'), '# x\n')
    w(os.path.join(WL, 'wiki', 'log.md'), '# 기록\n- 2026-11-08 07:00 | Claude | … | …\n')
    assert sc.wiki_lag() == (1, '2026-11-08')
    w(os.path.join(WL, 'wiki', 'log.md'), '# 기록\n- 2026-11-01 07:00 | Claude | … | …\n')
    assert sc.wiki_lag() == (8, '2026-11-01')

    # JARVIS 맥락: 인계 노트 내용, 열린 W(jarvis 포함), 결정 제목, 밀림 경고
    w(os.path.join(J, 'state', 'handoff', 'manager', 'latest.md'), '## 사용자와 진행 중인 대화\n- 답 기다림\n')
    w(os.path.join(J, 'state', 'tasks', 'W-20261109-01.md'),
      '---\nid: W-20261109-01\nproject: jarvis\ntitle: 자기 일\nstatus: working\n---\n')
    w(os.path.join(J, 'state', 'tasks', 'W-20261109-02.md'),
      '---\nid: W-20261109-02\nproject: nipa\ntitle: 끝남\nstatus: done\n---\n')
    w(os.path.join(J, 'criteria.md'), '# 판단 기준\n\n1. 읽기 전용 조회는 허용 (D-…)\n')
    ho = os.path.join(J, 'state', 'handoff', 'manager', 'latest.md')
    text = '\n'.join(sc.jarvis_memory_lines(J, REG, ho))
    assert '답 기다림' in text and 'W-20261109-01 [working] jarvis' in text and '끝남' not in text, text
    assert '1. 읽기 전용 조회는 허용' in text and 'nipa · 결정 B' in text and '8일 밀림' in text, text
    w(os.path.join(WL, 'wiki', 'log.md'), '# 기록\n- 2026-11-09 07:00 | Claude | … | …\n')
    assert '밀림' not in '\n'.join(sc.jarvis_memory_lines(J, REG, ho))          # 밀리지 않으면 경고 없음
    print('ok')
finally:
    shutil.rmtree(TMP, ignore_errors=True)
