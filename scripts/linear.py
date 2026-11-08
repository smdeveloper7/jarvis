#!/usr/bin/env python3
"""Linear 연동 (docs/03 9절). 대표 JARVIS 만 부른다. PM·Worker 는 Linear 를 쓰지 않는다.

키: 환경변수 LINEAR_API_KEY (없으면 Windows 사용자 환경변수에서 읽음). 키는 출력·기록하지 않는다.

  python scripts/linear.py projects
  python scripts/linear.py issues <프로젝트키|Linear Project 이름> [--state "In Progress"]
  python scripts/linear.py get PRO-12
  python scripts/linear.py create <프로젝트키> --title T --key <멱등 키> [--description D] [--parent PRO-11]
                                [--label Feature] [--state Todo] [--dry-run]
  python scripts/linear.py state PRO-12 "In Review" [--dry-run]
  python scripts/linear.py comment PRO-12 "W-20261110-01 done: …" [--key W-20261110-01:done] [--dry-run]
  python scripts/linear.py pending | retry

- 프로젝트키(nipa)는 registry.yaml 의 linear_project(KSM)로 바꾼다. 이름이 같다고 짝짓지 않는다.
- create 의 --key(W-ID 나 기능 ID)는 설명 끝에 표식으로 넣고, 만들기 전에 같은 표식을 찾아 있으면 그 이슈를 돌려준다(중복 방지).
- comment 의 --key 도 같은 표식으로 이미 단 코멘트를 다시 달지 않는다.
- state 는 바꾸기 전 상태를 기억해, retry 때 그 사이 상태가 바뀌었으면 되돌리지 않고 conflict 로 남긴다.
- 쓰기가 실패하면 state/linear_pending.jsonl 에 남긴다(항목 ID 있음). retry 는 잠금 안에서 항목을 예약하고 처리한다.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'hooks'))
import time  # noqa: E402
import uuid  # noqa: E402

from jarvis_state import FileLock, append_jsonl, now_iso, registry  # noqa: E402

JDIR = os.path.abspath(os.environ.get('JARVIS_DIR') or os.path.join(HERE, '..'))
API = 'https://api.linear.app/graphql'
PENDING = os.path.join(JDIR, 'state', 'linear_pending.jsonl')


class LinearError(Exception):
    pass


class Conflict(LinearError):
    """다시 해도 안 되는 실패(대상 상태가 이미 바뀜). retry 가 done 으로 닫는다."""


def api_key():
    k = os.environ.get('LINEAR_API_KEY', '').strip()
    if not k and os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as h:
                k = winreg.QueryValueEx(h, 'LINEAR_API_KEY')[0].strip()
        except OSError:
            k = ''
    if not k:
        raise LinearError('LINEAR_API_KEY 가 없다')
    return k


def gql(query, variables=None):
    req = urllib.request.Request(API, data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': api_key()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise LinearError('HTTP %s %s' % (e.code, e.read()[:300].decode('utf-8', 'ignore')))
    except urllib.error.URLError as e:
        raise LinearError('연결 실패: %s' % e.reason)
    except (TimeoutError, OSError, ValueError) as e:
        raise LinearError('응답 실패: %s %s' % (type(e).__name__, e))
    if d.get('errors'):
        raise LinearError('; '.join(x.get('message', '') for x in d['errors']))
    return d['data']


def marker(key):
    return '<!-- jarvis:key=%s -->' % key


def project_name(name_or_key):
    p = registry(JDIR)['projects'].get(name_or_key)
    if p is not None:
        if not p.get('linear_project'):
            raise LinearError('registry 의 %s 에 linear_project 가 없다' % name_or_key)
        return p['linear_project']
    return name_or_key


def project(name):
    d = gql('query($n:String!){projects(filter:{name:{eq:$n}}){nodes{id name teams{nodes{id key}}}}}', {'n': name})
    nodes = d['projects']['nodes']
    if len(nodes) != 1:
        raise LinearError('Linear Project "%s" 를 하나로 찾지 못함(%d개)' % (name, len(nodes)))
    return nodes[0]


def team_meta(team_id):
    d = gql('query($t:String!){team(id:$t){id key states{nodes{id name}} labels{nodes{id name}}}}', {'t': team_id})
    return d['team']


def org_labels():
    d = gql('{issueLabels(first:100){nodes{id name team{id}}}}')
    return d['issueLabels']['nodes']


def issue(ident):
    d = gql('query($i:String!){issue(id:$i){id identifier title url state{name} parent{identifier}'
            ' project{name} labels{nodes{name}} children{nodes{identifier title state{name}}}}}', {'i': ident})
    return d['issue']


def find_by_key(project_id, key):
    d = gql('query($p:ID!,$m:String!){issues(filter:{project:{id:{eq:$p}},description:{contains:$m}},first:5)'
            '{nodes{identifier title url state{name}}}}', {'p': project_id, 'm': marker(key)})
    return d['issues']['nodes']


def pending(op, args, err):
    row = {'id': uuid.uuid4().hex[:10], 'at': now_iso(), 'op': op, 'args': args, 'error': str(err)[:300], 'done': False}
    if not append_jsonl(PENDING, row):
        print('경고: linear_pending 기록 실패. 이 작업을 직접 다시 해야 한다: %s' % json.dumps(row, ensure_ascii=False),
              file=sys.stderr)


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return code


# ---------- 명령 ----------

def cmd_projects(a):
    d = gql('{projects(first:50){nodes{name state teams{nodes{key}}}}}')
    reg = registry(JDIR)['projects']
    back = {v.get('linear_project'): k for k, v in reg.items() if v.get('linear_project')}
    return out([{'name': p['name'], 'state': p['state'], 'teams': [t['key'] for t in p['teams']['nodes']],
                 'jarvis_project': back.get(p['name'])} for p in d['projects']['nodes']])


def cmd_issues(a):
    p = project(project_name(a.project))
    flt = {'project': {'id': {'eq': p['id']}}}
    if a.state:
        flt['state'] = {'name': {'eq': a.state}}
    d = gql('query($f:IssueFilter){issues(filter:$f,first:100){nodes{identifier title state{name}'
            ' parent{identifier} labels{nodes{name}}}}}', {'f': flt})
    return out([{'id': i['identifier'], 'title': i['title'], 'state': i['state']['name'],
                 'parent': (i.get('parent') or {}).get('identifier'),
                 'labels': [x['name'] for x in i['labels']['nodes']]} for i in d['issues']['nodes']])


def cmd_get(a):
    return out(issue(a.issue))


def do_create(a):
    p = project(project_name(a.project))
    found = find_by_key(p['id'], a.key)
    if found:
        return {'ok': True, 'existing': True, 'issue': found[0]}
    team = p['teams']['nodes'][0]
    meta = team_meta(team['id'])
    inp = {'teamId': team['id'], 'projectId': p['id'], 'title': a.title,
           'description': ((a.description or '').rstrip() + '\n\n' + marker(a.key)).lstrip()}
    if a.state:
        sid = [s['id'] for s in meta['states']['nodes'] if s['name'] == a.state]
        if not sid:
            raise LinearError('상태 "%s" 없음' % a.state)
        inp['stateId'] = sid[0]
    if a.label:
        lids = [x['id'] for x in org_labels() + meta['labels']['nodes'] if x['name'] in a.label]
        if len(set(lids)) < len(set(a.label)):
            raise LinearError('라벨 일부 없음: %s' % a.label)
        inp['labelIds'] = sorted(set(lids))
    if a.parent:
        inp['parentId'] = issue(a.parent)['id']
    if a.dry_run:
        return {'ok': True, 'dry_run': True, 'input': inp}
    d = gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{identifier title url}}}', {'i': inp})
    if not d['issueCreate']['success']:
        raise LinearError('issueCreate success=false')
    return {'ok': True, 'existing': False, 'issue': d['issueCreate']['issue']}


def do_state(a):
    it = issue(a.issue)
    cur = it['state']['name']
    if getattr(a, 'expect_from', None) and cur != a.expect_from and cur != a.state:
        raise Conflict('상태가 그 사이 %s 로 바뀜(기대 %s). 되돌리지 않음' % (cur, a.expect_from))
    if cur == a.state:
        return {'ok': True, 'issue': it['identifier'], 'from': cur, 'to': a.state, 'unchanged': True}
    a.expect_from = cur
    d = gql('query($i:String!){issue(id:$i){team{states{nodes{id name}}}}}', {'i': a.issue})
    sid = [s['id'] for s in d['issue']['team']['states']['nodes'] if s['name'] == a.state]
    if not sid:
        raise LinearError('상태 "%s" 없음' % a.state)
    if a.dry_run:
        return {'ok': True, 'dry_run': True, 'issue': it['identifier'], 'from': it['state']['name'], 'to': a.state}
    r = gql('mutation($i:String!,$s:String!){issueUpdate(id:$i,input:{stateId:$s}){success}}',
            {'i': it['id'], 's': sid[0]})
    if not r['issueUpdate']['success']:
        raise LinearError('issueUpdate success=false')
    return {'ok': True, 'issue': it['identifier'], 'from': cur, 'to': a.state}


def do_comment(a):
    it = issue(a.issue)
    body = a.body
    if getattr(a, 'key', None):
        body = body.rstrip() + '\n\n' + marker(a.key)
        d = gql('query($i:String!){issue(id:$i){comments(first:100){nodes{body}}}}', {'i': a.issue})
        if any(marker(a.key) in (c.get('body') or '') for c in d['issue']['comments']['nodes']):
            return {'ok': True, 'existing': True, 'issue': it['identifier']}
    if a.dry_run:
        return {'ok': True, 'dry_run': True, 'issue': it['identifier'], 'body': body}
    r = gql('mutation($i:String!,$b:String!){commentCreate(input:{issueId:$i,body:$b}){success}}',
            {'i': it['id'], 'b': body})
    if not r['commentCreate']['success']:
        raise LinearError('commentCreate success=false')
    return {'ok': True, 'issue': it['identifier']}


WRITES = {'create': do_create, 'state': do_state, 'comment': do_comment}


def run_write(a):
    try:
        if a.cmd == 'create':      # 같은 --key 로 동시에 만들지 않게 직렬화
            with FileLock(os.path.join(JDIR, 'state', 'linear_create')):
                return out(do_create(a))
        return out(WRITES[a.cmd](a))
    except (LinearError, TimeoutError) as e:
        keep = not getattr(a, 'dry_run', False) and not isinstance(e, Conflict)
        if keep:
            pending(a.cmd, {k: v for k, v in vars(a).items() if k not in ('fn',)}, e)
        return out({'ok': False, 'error': str(e), 'pending': keep}, 1)


def read_pending():
    rows = []
    try:
        with open(PENDING, encoding='utf-8') as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except OSError:
        pass
    return rows


def write_pending(rows):
    tmp = PENDING + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, PENDING)


def cmd_pending(a):
    return out([r for r in read_pending() if not r.get('done')])


CLAIM_S = 300


def claim_one():
    """잠금 안에서 처리할 항목 하나를 토큰으로 예약해 돌려준다. 없으면 None."""
    with FileLock(PENDING):
        rows = read_pending()
        for r in rows:
            if r.get('done') or (r.get('claimed_at') and time.time() - r['claimed_at'] < CLAIM_S):
                continue
            r.setdefault('id', uuid.uuid4().hex[:10])
            r['claimed_at'], r['claim'] = time.time(), uuid.uuid4().hex[:10]
            write_pending(rows)
            return dict(r)
    return None


def finish(row, **upd):
    """예약 토큰이 그대로일 때만 결과를 반영한다(다른 retry 가 가져갔으면 손대지 않음)."""
    with FileLock(PENDING):
        rows = read_pending()
        for r in rows:
            if r.get('id') == row['id'] and r.get('claim') == row['claim']:
                r.update(upd, claimed_at=None, claim=None)
                write_pending(rows)
                return True
    return False


def cmd_retry(a):
    """항목을 하나씩 예약 → 실행 → 토큰 확인 후 반영. 같은 항목을 두 retry 가 동시에 하지 않는다."""
    res, seen = [], set()
    while True:
        r = claim_one()
        if r is None or r['id'] in seen:
            if r is not None:
                finish(r)
            break
        seen.add(r['id'])
        ns = argparse.Namespace(**r['args'])
        try:
            if r['op'] == 'create':
                with FileLock(os.path.join(JDIR, 'state', 'linear_create')):
                    res.append(do_create(ns))
            else:
                res.append(WRITES[r['op']](ns))
            finish(r, done=True, done_at=now_iso())
        except Conflict as e:
            res.append({'ok': False, 'op': r['op'], 'conflict': str(e)})
            finish(r, done=True, done_at=now_iso(), result='conflict: ' + str(e)[:200])
        except (LinearError, TimeoutError) as e:
            res.append({'ok': False, 'op': r['op'], 'error': str(e)})
            finish(r, error=str(e)[:300])
    return out(res)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='linear')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('projects').set_defaults(fn=cmd_projects)
    p = sub.add_parser('issues'); p.add_argument('project'); p.add_argument('--state'); p.set_defaults(fn=cmd_issues)
    p = sub.add_parser('get'); p.add_argument('issue'); p.set_defaults(fn=cmd_get)
    p = sub.add_parser('create'); p.add_argument('project'); p.add_argument('--title', required=True)
    p.add_argument('--key', required=True); p.add_argument('--description'); p.add_argument('--parent')
    p.add_argument('--label', action='append'); p.add_argument('--state'); p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=run_write)
    p = sub.add_parser('state'); p.add_argument('issue'); p.add_argument('state'); p.add_argument('--expect-from')
    p.add_argument('--dry-run', action='store_true'); p.set_defaults(fn=run_write)
    p = sub.add_parser('comment'); p.add_argument('issue'); p.add_argument('body'); p.add_argument('--key')
    p.add_argument('--dry-run', action='store_true'); p.set_defaults(fn=run_write)
    sub.add_parser('pending').set_defaults(fn=cmd_pending)
    sub.add_parser('retry').set_defaults(fn=cmd_retry)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except LinearError as e:
        return out({'ok': False, 'error': str(e)}, 1)


if __name__ == '__main__':
    sys.exit(main())
