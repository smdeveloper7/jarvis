"""hooks/jarvis_only_write.py: JARVIS 폴더 파일은 JARVIS 세션만 고친다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks'))
from jarvis_only_write import blocked  # noqa: E402

J = 'C:/Users/me/JARVIS'


def case(file_path, cwd, project='', jarvis=J):
    return blocked({'tool_input': {'file_path': file_path}, 'cwd': cwd}, jarvis, project, nt=True)


# Coordinator·Worker(프로젝트 폴더)가 JARVIS 파일을 고치면 막는다
assert case('C:/Users/me/JARVIS/registry.yaml', 'C:/Users/me/projects/nipa')
assert case('C:\\Users\\me\\JARVIS\\state\\tasks\\W-1.md', 'C:/Users/me/projects/nipa')
assert case('/c/Users/me/JARVIS/criteria.md', 'C:/Users/me/projects/nipa')          # Git Bash 꼴
assert case('c:/users/me/jarvis/rules/worker.md', 'C:/Users/me/projects/nipa')      # 대소문자
assert case('../../JARVIS/criteria.md', 'C:/Users/me/projects/nipa')                # 상대 경로
# JARVIS 자신은 허용
assert not case('C:/Users/me/JARVIS/registry.yaml', 'C:/Users/me/JARVIS')
assert not case('C:/Users/me/JARVIS/state/x.md', 'C:/Users/me/JARVIS/state')
assert not case('C:/Users/me/JARVIS/registry.yaml', 'C:/tmp', project='C:/Users/me/JARVIS')
# JARVIS 밖 파일, 이름만 비슷한 폴더, 설정 없음은 허용
assert not case('C:/Users/me/projects/nipa/a.py', 'C:/Users/me/projects/nipa')
assert not case('C:/Users/me/JARVIS2/a.md', 'C:/Users/me/projects/nipa')
assert not case('C:/Users/me/JARVIS/registry.yaml', 'C:/Users/me/projects/nipa', jarvis='')
print('ok')
