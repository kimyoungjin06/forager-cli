"""Read-only attention categories and identities shared by cards and lookup."""
import hashlib
import json


def attention_category(item):
    kind = item.get('kind')
    if kind == 'decision':
        return 'decision', '결정 대기'
    if kind == 'recovery':
        return 'review', '복구 검토 대기'
    if kind == 'task':
        status = str(item.get('status') or '').lower()
        reason = item.get('reason') or item.get('next_safe_action_kind')
        if status in {'failed', 'resume_pending', 'running', 'stuck'} or reason in {
            'recovery_required', 'resume_review_required',
        }:
            return 'execution', '실행 복구 확인'
        if status in {'completed', 'cancelled'}:
            return 'review', '결과 검토 대기'
    return 'unknown', '작업 확인 대기'


def task_attention_key(project, task_id, request_id):
    encoded = json.dumps([str(project or ''), str(task_id or ''), str(request_id or '')],
                         ensure_ascii=False, separators=(',', ':')).encode()
    return 'task:v2:' + hashlib.sha256(encoded).hexdigest()


def task_inspection_hint(project, task_id, request_id):
    # Exact lookup never executes these values as a shell or runtime command.
    parts = [str(project or ''), str(task_id or ''), str(request_id or '')]
    if all(parts) and all(not any(char.isspace() for char in part) for part in parts):
        return '/tasks ' + ' '.join(parts)
    return '/tasks'
