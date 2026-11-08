"""JARVIS 훅 공통: 역할 판정, registry·Paseo 기록 읽기, state 쓰기.

모든 훅은 모든 세션(JARVIS, PM, Worker, 사용자 세션)에서 불린다. 여기 함수로 역할을 정하고,
역할이 맞지 않으면 훅은 아무것도 하지 않고 끝난다. 실패하면 조용히 넘어간다(세션을 막지 않는다).

역할 판정 (docs/02 2절)
- Paseo 가 띄운 세션은 환경변수 PASEO_AGENT_ID 가 있다.
- registry.yaml `jarvis:` 와 같으면 대표 JARVIS, JARVIS 폴더에서 뜬 다른 Paseo 세션은 JARVIS 보조(읽기만).
- Paseo 기록의 라벨 jarvis.role=pm, jarvis.project=<키> 이거나 registry coordinator 와 같으면 PM.
- 라벨 paseo.parent-agent-id 가 PM 이면 Worker.
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from pathutil import inside, norm

KST = timezone(timedelta(hours=9))


def now_iso():
    return datetime.now(KST).isoformat(timespec='seconds')


def stdio_utf8():
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass


def read_input():
    try:
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def jarvis_dir():
    return os.environ.get('CLAUDE_PLUGIN_OPTION_JARVIS_DIR', '').strip()


def paseo_home():
    return os.path.expanduser(os.environ.get('PASEO_HOME') or '~/.paseo')


# ---------- registry.yaml ----------

def registry(jdir):
    """{'jarvis': id, 'projects': {키: {name, root, coordinator, linear_project}}}. 작은 YAML 만 읽는다."""
    out = {'jarvis': None, 'projects': {}}
    cur = None
    try:
        with open(os.path.join(jdir, 'registry.yaml'), encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                m = re.match(r'^jarvis:\s*(.*?)\s*$', line)
                if m:
                    out['jarvis'] = clean(m.group(1))
                    continue
                m = re.match(r'^  ([A-Za-z0-9_.-]+):\s*$', line)
                if m:
                    cur = out['projects'].setdefault(m.group(1), {})
                    continue
                m = re.match(r'^    ([A-Za-z_]+):\s*(.*?)\s*$', line)
                if m and cur is not None:
                    cur[m.group(1)] = clean(m.group(2))
    except OSError:
        pass
    return out


def clean(v):
    v = v.split(' #')[0].strip().strip('"').strip("'")
    return None if v in ('', 'null', '~') else v


# ---------- Paseo 기록 ----------

def paseo_lookup(agent_id, home=None, tries=3):
    """('ok', 기록) | ('missing', None) | ('error', None). 읽기·파싱 실패는 잠깐 뒤 다시 읽는다.
    'missing' 은 파일이 없다는 뜻(Paseo 에서 삭제), 'error' 는 확인 못 함 — 둘을 섞지 않는다."""
    if not agent_id:
        return 'missing', None
    paths = glob.glob(os.path.join(home or paseo_home(), 'agents', '*', agent_id + '.json'))
    if not paths:
        return 'missing', None
    for i in range(tries):
        try:
            with open(paths[0], encoding='utf-8') as f:
                return 'ok', json.load(f)
        except (OSError, ValueError):
            time.sleep(0.1 * (i + 1))
    return 'error', None


def paseo_record(agent_id, home=None):
    return paseo_lookup(agent_id, home)[1]


def closed_state(agent_id, home=None):
    """'open' | 'archived' | 'closed' | 'missing' | 'error'."""
    st, rec = paseo_lookup(agent_id, home)
    if st != 'ok':
        return st
    if rec.get('archivedAt'):
        return 'archived'
    return 'closed' if rec.get('lastStatus') == 'closed' else 'open'


def paseo_records(home=None, strict=False):
    """모든 Paseo 에이전트 기록. strict=True 면 (기록 목록, 읽지 못한 파일 수)를 돌려준다.
    보호 판단(열린 Worker 확인)은 strict 로 불러, 읽지 못한 기록이 있으면 보류한다."""
    out, bad = [], 0
    for p in glob.glob(os.path.join(home or paseo_home(), 'agents', '*', '*.json')):
        for i in range(3):
            try:
                with open(p, encoding='utf-8') as f:
                    out.append(json.load(f))
                break
            except FileNotFoundError:
                break
            except (OSError, ValueError):
                if i == 2:
                    bad += 1
                time.sleep(0.05)
    return (out, bad) if strict else out


class Unknown(Exception):
    """확인하지 못해서 안전하게 보류해야 하는 경우."""


def open_children_checked(agent_id, home=None):
    """열린 자식 목록. 읽지 못한 기록이 있으면 Unknown(열린 Worker 가 없다고 단정하지 않는다)."""
    recs, bad = paseo_records(home, strict=True)
    if bad:
        raise Unknown('Paseo 기록 %d개를 읽지 못해 열린 Worker 여부를 확인할 수 없음' % bad)
    return open_children(agent_id, recs)


def labels(rec):
    return (rec or {}).get('labels') or {}


def is_open(rec):
    """archive 되지 않았고 닫히지 않은 에이전트."""
    return bool(rec) and not rec.get('archivedAt') and rec.get('lastStatus') != 'closed'


def open_children(agent_id, recs):
    return [r for r in recs if labels(r).get('paseo.parent-agent-id') == agent_id and is_open(r)]


# ---------- 역할 ----------

def role_of(agent_id, reg, jdir, cwd='', rec=None):
    """(역할, 프로젝트 키). 역할: jarvis | jarvis_other | pm | worker | none."""
    if not agent_id:
        return 'none', None
    if reg.get('jarvis') and agent_id == reg['jarvis']:
        return 'jarvis', None
    for k, p in reg['projects'].items():
        if p.get('coordinator') == agent_id:
            return 'pm', k
    lb = labels(rec)
    if lb.get('jarvis.role') == 'pm' and lb.get('jarvis.project') in reg['projects']:
        return 'pm', lb['jarvis.project']
    parent = lb.get('paseo.parent-agent-id')
    if parent:
        r, k = role_of(parent, reg, jdir, rec=paseo_record(parent))
        if r == 'pm':
            return 'worker', k
    if jdir and cwd and inside(norm(cwd), norm(jdir)) and not lb.get('paseo.schedule-id') \
            and lb.get('jarvis.role') != 'reviewer':
        return 'jarvis_other', None
    return 'none', None


def current_role(inp, jdir, reg=None):
    agent_id = os.environ.get('PASEO_AGENT_ID', '').strip()
    reg = reg or registry(jdir)
    rec = paseo_record(agent_id) if agent_id else None
    cwd = inp.get('cwd') or os.environ.get('CLAUDE_PROJECT_DIR', '') or os.getcwd()
    role, proj = role_of(agent_id, reg, jdir, cwd, rec)
    return agent_id, role, proj, reg, rec


# ---------- state 쓰기 ----------

def state(jdir, *parts):
    return os.path.join(jdir, 'state', *parts)


def lock_path(jdir, proj):
    return state(jdir, 'handoff', proj, 'rotating.lock')


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp%d' % os.getpid()
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def pid_alive(pid):
    """그 프로세스가 아직 살아 있나. 확인 못 하면 살아 있다고 본다(잠금을 함부로 치우지 않게)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if os.name == 'nt':
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.windll.kernel32.GetLastError() == 5            # 접근 거부 = 살아 있음
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return not ok or code.value == 259                                # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


class FileLock:
    """<path>.lock 을 O_EXCL 로 만드는 프로세스 간 잠금. 파일에 '<pid> <토큰>' 을 적는다.
    잡은 프로세스가 죽었을 때만(또는 stale 초를 넘기고 pid 를 알 수 없을 때) 치운다. 풀 때는 자기 토큰일 때만 지운다.
    with FileLock(path): ...  (못 잡으면 TimeoutError)"""

    def __init__(self, path, timeout=10.0, stale=600.0):
        self.lk, self.timeout, self.stale, self.token = path + '.lock', timeout, stale, None

    def _owner(self):
        try:
            with open(self.lk, encoding='utf-8') as f:
                parts = f.read().split()
            return parts[0] if parts else None, parts[1] if len(parts) > 1 else None
        except OSError:
            return None, None

    def __enter__(self):
        import uuid
        os.makedirs(os.path.dirname(self.lk) or '.', exist_ok=True)
        end = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                self.token = uuid.uuid4().hex
                os.write(fd, ('%d %s' % (os.getpid(), self.token)).encode())
                os.close(fd)
                return self
            except FileExistsError:
                pid, _ = self._owner()
                try:
                    age = time.time() - os.path.getmtime(self.lk)
                except OSError:
                    continue
                if (pid and not pid_alive(pid)) or (not pid and age > self.stale):
                    try:
                        os.remove(self.lk)
                    except OSError:
                        pass
                    continue
                if time.time() > end:
                    raise TimeoutError('잠금을 못 잡음: ' + self.lk)
                time.sleep(0.05)

    def __exit__(self, *a):
        if self._owner()[1] == self.token:
            try:
                os.remove(self.lk)
            except OSError:
                pass


def append_jsonl(path, obj):
    """한 줄 추가(event_id 를 붙임). 여러 훅이 동시에 써도 줄이 섞이지 않게 잠근다. 성공하면 True."""
    if isinstance(obj, dict) and 'event_id' not in obj:
        import uuid
        obj = dict({'event_id': uuid.uuid4().hex[:12]}, **obj)
    try:
        with FileLock(path):
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(obj, ensure_ascii=False) + '\n')
        return True
    except (OSError, TimeoutError):
        return False


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------- 작업·결정 ----------

def front(path):
    """마크다운 앞부분 --- 사이의 key: value."""
    meta = {}
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().split('\n')
    except OSError:
        return meta
    if not lines or lines[0].strip() != '---':
        return meta
    for line in lines[1:]:
        if line.strip() == '---':
            break
        m = re.match(r'^([A-Za-z_]+):\s*(.*?)\s*$', line)
        if m:
            meta[m.group(1)] = m.group(2).split(' #')[0].strip()
    return meta


def open_tasks(jdir, proj):
    out = []
    for p in sorted(glob.glob(state(jdir, 'tasks', 'W-*.md'))):
        m = front(p)
        if m.get('project') == proj and m.get('status') not in ('done', 'cancelled'):
            out.append(m)
    return out


def open_decisions(jdir, proj):
    out = []
    for p in sorted(glob.glob(state(jdir, 'decisions', 'D-*.md'))):
        m = front(p)
        if m.get('project') == proj and m.get('status') in ('open', 'waiting'):
            out.append((p, m))
    return out


# ---------- transcript 사용량 ----------

def context_tokens(transcript_path, tail=1 << 20):
    """transcript 마지막 assistant usage 로 컨텍스트 크기. Paseo contextWindowUsedTokens 와 같은 값."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, 'rb') as f:
            f.seek(max(0, size - tail))
            data = f.read().decode('utf-8', 'ignore')
    except OSError:
        return None
    for line in reversed(data.splitlines()):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get('message') or {}).get('usage')
        if d.get('type') == 'assistant' and u:
            return sum(int(u.get(k) or 0) for k in
                       ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'))
    return None


def emit_context(event, text):
    """SessionStart·UserPromptSubmit 에서 컨텍스트에 글을 넣는다."""
    print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}},
                     ensure_ascii=False))


def deny(reason):
    """PreToolUse 에서 도구 실행을 막는다."""
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                             'permissionDecisionReason': reason}}, ensure_ascii=False))
