#!/usr/bin/env python3
"""기억 찾기 (docs/04 7절): 판단 기준 → 위키 → 일지 순으로 키워드를 찾아 출처와 함께 짧게 돌려준다.

  python scripts/recall.py <키워드…> [--project nipa] [--since 2026-10-01] [--limit 8] [--raw-only|--wiki-only]

- 키워드는 모두 들어간 줄·노트를 먼저, 일부만 들어간 것은 뒤에 둔다(대소문자 무시).
- 결과는 단계 순(criteria → wiki 결정 → 해결법 → 개요 → 그 밖의 위키 → 일지 최근 순)으로, 노트마다 맞은 줄 두세 개.
- 원문(대화 transcript)은 찾지 않는다. 여기서 못 찾으면 `~/.claude/projects/<폴더>/` 를 직접 Grep 한다(30일 안만 남음).
읽기만 한다.
"""
import argparse
import glob
import os
import re
import sys

JDIR = os.path.abspath(os.environ.get('JARVIS_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
WORKLOG = os.environ.get('JARVIS_WORKLOG_DIR') or 'C:/Users/SMDEVELOPER/projects/worklog'
RANK = {'기준': 0, '결정': 1, '해결법': 2, '개요': 3, '위키': 4, '일지': 5}


def read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ''


def kind_of(path):
    p = path.replace('\\', '/')
    if p.endswith('criteria.md'):
        return '기준'
    if '/raw/' in p:
        return '일지'
    if '/decisions/' in p:
        return '결정'
    if '/해결법/' in p:
        return '해결법'
    if p.endswith('/개요.md'):
        return '개요'
    return '위키'


def link_of(path):
    p = path.replace('\\', '/')
    root = WORKLOG.replace('\\', '/').rstrip('/') + '/'
    if p.startswith(root):
        return '[[%s]]' % os.path.splitext(p[len(root):])[0]
    return p


def project_ok(path, text, project):
    if not project:
        return True
    p = path.replace('\\', '/')
    if '/wiki/' in p:
        return '/wiki/%s/' % project in p or '/wiki/공통/' in p or re.search(r'^project:\s*"?%s' % re.escape(project), text, re.M)
    return True   # 일지는 기록 단위로 거른다


def raw_blocks(path, text, project):
    """일지 파일을 '### ' 기록 단위로 나눈다. 프로젝트 칸으로 거른다."""
    day = os.path.basename(path)[:10]
    for block in re.split(r'(?m)^(?=### )', text):
        if not block.startswith('### '):
            continue
        head = block.split('\n', 1)[0]
        cols = [c.strip() for c in head[4:].split('|')]
        if project and len(cols) > 2 and cols[2].lower() not in (project.lower(), 'jarvis' if project == '공통' else ''):
            continue
        yield '%s %s' % (day, head[4:].strip()), block


def score(text, terms):
    low = text.lower()
    hit = [t for t in terms if t in low]
    return len(hit), sum(low.count(t) for t in hit)


def snippets(text, terms, n=3):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith('---') and any(t in s.lower() for t in terms):
            out.append(s[:180])
            if len(out) >= n:
                break
    return out


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='recall')
    ap.add_argument('terms', nargs='+')
    ap.add_argument('--project')
    ap.add_argument('--since', help='일지 날짜 하한 YYYY-MM-DD')
    ap.add_argument('--limit', type=int, default=8)
    ap.add_argument('--raw-only', action='store_true')
    ap.add_argument('--wiki-only', action='store_true')
    a = ap.parse_args(argv)
    terms = [t.lower() for t in a.terms]
    found = []   # (전부 맞음 여부, 단계, -최신, 횟수, 제목, 링크, 줄들)
    if not a.raw_only:
        files = [os.path.join(JDIR, 'criteria.md')] + glob.glob(os.path.join(WORKLOG, 'wiki', '**', '*.md'), recursive=True)
        for path in files:
            text = read(path)
            if not text or not project_ok(path, text, a.project):
                continue
            n, c = score(text, terms)
            if n:
                title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith('# ')),
                             os.path.splitext(os.path.basename(path))[0])
                found.append((n == len(terms), RANK[kind_of(path)], -os.path.getmtime(path), c, title,
                              link_of(path), snippets(text, terms)))
    if not a.wiki_only:
        for path in sorted(glob.glob(os.path.join(WORKLOG, 'raw', '????-??-??.md')), reverse=True):
            if a.since and os.path.basename(path)[:10] < a.since:
                continue
            for title, block in raw_blocks(path, read(path), a.project):
                n, c = score(block, terms)
                if n:
                    found.append((n == len(terms), RANK['일지'], -os.path.getmtime(path), c, title,
                                  link_of(path), snippets(block, terms, 2)))
    found.sort(key=lambda x: (not x[0], x[1], x[2], -x[3]))
    if not found:
        print('못 찾음: %s. 원문은 ~/.claude/projects/<폴더>/*.jsonl 을 직접 Grep(30일 안).' % ' '.join(a.terms))
        return 1
    kinds = {v: k for k, v in RANK.items()}
    for full, rank, _, _, title, link, lines in found[:a.limit]:
        print('[%s%s] %s  %s' % (kinds[rank], '' if full else ', 일부', title, link))
        for ln in lines:
            print('    ' + ln)
    if len(found) > a.limit:
        print('… 외 %d건 (--limit 로 더 보기)' % (len(found) - a.limit))
    return 0


if __name__ == '__main__':
    sys.exit(main())
