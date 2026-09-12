"""Offline declared-action diagnostics; never execute answers or change grading."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from . import actions

EXPECTED = dict(edited='preserve', scratch='reset', race='conditional_update',
                backup='sqlite_backup')


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate JSON key')
        obj[key] = value
    return obj


def _action(raw, allowed):
    if not isinstance(raw, str):
        return None, 'missing_text'
    text = raw.strip()
    basis = 'complete_json'
    if '```' in text:
        matches = list(re.finditer(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, re.S))
        if len(matches) != 1:
            return None, 'ambiguous_or_invalid_fence'
        match = matches[0]
        remainder = text[:match.start()] + text[match.end():]
        # A second structured candidate is ambiguous; prose is not interpreted.
        if any(char in remainder for char in '{}[]`'):
            return None, 'additional_structured_content'
        text = match[1]
        basis = 'single_fenced_json_declaration_only'
    try:
        obj = json.loads(text, object_pairs_hook=_pairs)
    except (ValueError, TypeError):
        return None, 'invalid_or_duplicate_json'
    if type(obj) is not dict or set(obj) != {'action'}:
        return None, 'invalid_action_shape'
    if type(obj['action']) is not str or obj['action'] not in allowed:
        return None, 'action_not_allowlisted'
    return obj['action'], basis


def diagnose(row, task, *, action_source_matches=True):
    """Compare a declaration to the known task contract, separate from execution."""
    for field in ('task_id', 'group'):
        if field not in row:
            raise ValueError(f'result row missing required field: {field}')
    known = next((c for c in actions.cases() if c['task_id'] == row['task_id']), None)
    same_contract = known is not None and all(task.get(k) == v for k, v in known.items())
    expected = EXPECTED.get(row['task_id']) if same_contract and action_source_matches else None
    declaration, basis = (None, 'transport_incomplete')
    if row.get('transport_status') == 'complete':
        declaration, basis = _action(row.get('raw_answer'), task.get('allowed_actions', []))
    execution = row.get('execution')
    return {
        'task_id': row['task_id'], 'group': row['group'],
        'transport_status': row.get('transport_status'), 'error_type': row.get('error_type'),
        'format_pass': row.get('format_pass'),
        'declared_action': declaration, 'declaration_basis': basis,
        'expected_action': expected,
        'expected_basis': 'fixed_action_postcondition_contract' if expected else 'unknown_contract',
        'declared_action_agreement': declaration == expected if declaration and expected else None,
        'executed': execution is not None,
        'executed_action': execution.get('action') if execution else None,
        'postconditions': execution.get('observations') if execution else None,
        'first_pass': execution.get('passed') is True if execution else False,
        'semantic_success': row.get('semantic_success'),
    }


def build_report(root):
    root = Path(root).resolve()
    inputs = {}

    def read(path):
        content = path.read_bytes()
        inputs[str(path.relative_to(root))] = hashlib.sha256(content).hexdigest()
        return content

    tasks = [json.loads(line) for line in read(root/'holdout_tasks.jsonl').decode().splitlines() if line.strip()]
    if len(tasks) != len({task['task_id'] for task in tasks}):
        raise ValueError('duplicate task_id in holdout_tasks.jsonl')
    tasks = {task['task_id']: task for task in tasks}
    manifest = json.loads(read(root/'manifest.json'))
    action_hash = hashlib.sha256(Path(actions.__file__).read_bytes()).hexdigest()
    source_matches = manifest.get('source_sha256', {}).get('evaluation/experience_v1/actions.py') == action_hash
    rows = []
    count = 0
    for path in sorted((root/'results').glob('*.json')):
        try:
            row = json.loads(read(path))
        except ValueError as exc:
            raise ValueError(f'{path.name}: malformed JSON result file: {exc}') from None
        count += 1
        if row.get('kind') != 'engineering_action':
            continue
        if row.get('task_id') not in tasks:
            raise ValueError(f'{path.name}: result references unknown task_id: {row.get("task_id")!r}')
        result = diagnose(row, tasks[row['task_id']], action_source_matches=source_matches)
        result.update(source=str(path.relative_to(root)), source_sha256=inputs[str(path.relative_to(root))])
        rows.append(result)
    return {
        'protocol': 'offline-declared-action-diagnostic-1', 'root': str(root),
        'source_result_count': count, 'rows': rows, 'input_sha256': inputs,
        'action_source_sha256': action_hash, 'action_source_matches_frozen': source_matches,
        'expected_action_contract': EXPECTED,
        'engineering_first_pass': {g: sum(r['first_pass'] for r in rows if r['group'] == g) for g in ('A', 'B', 'C')},
        'new_model_requests': 0, 'actions_executed_by_diagnostic': 0,
        'limitation': 'Declaration agreement is not executed success or a causal attribution. Original grading and gates are unchanged.',
    }


def build_review_report(root, review_path):
    """Validate an external review receipt, not its judgments or declared blindness."""
    root, review_path = Path(root).resolve(), Path(review_path).resolve()
    if review_path.is_relative_to(root):
        raise ValueError('review receipt must be outside frozen input directory')
    packet_raw = (root/'blind-review.json').read_bytes()
    review_raw = review_path.read_bytes()
    report = {'protocol':'offline-review-intake-1',
              'packet_sha256':hashlib.sha256(packet_raw).hexdigest(),
              'review_sha256':hashlib.sha256(review_raw).hexdigest(),
              'reviewer':None, 'structural_validation':'failed', 'errors':[],
              'quality_verdict':'unassessed', 'blindness':'explicitly_exposed_or_unknown',
              'new_model_requests':0}
    errors = report['errors']

    def invalid_constant(value):
        raise ValueError('non-finite JSON constant')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite JSON number')
        return number

    def parse(raw, label):
        try:
            return json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant,
                              parse_float=finite_float)
        except (ValueError, UnicodeError):
            errors.append(label+': invalid, duplicate-key or non-finite JSON')
            return None

    packet = parse(packet_raw, 'packet')
    review = parse(review_raw, 'review')
    if type(packet) is not list or not packet:
        errors.append('packet: expected nonempty list')
        return report
    items = {}
    for item in packet:
        if (type(item) is not dict or type(item.get('review_id')) is not str
                or not item['review_id'].strip() or item['review_id'] in items
                or item.get('kind') not in ('creative_copy', 'negative_transfer')
                or type(item.get('answer')) is not str
                or item.get('transport_status') not in ('complete', 'error')):
            errors.append('packet: invalid or duplicate item')
            return report
        items[item['review_id']] = item
    if type(review) is not dict:
        errors.append('review: expected object')
        return report
    if review.get('packet_sha256') != report['packet_sha256']:
        errors.append('review: packet hash mismatch')
    reviewer = review.get('reviewer')
    if type(reviewer) is not str or not reviewer.strip():
        errors.append('review: reviewer required')
    else:
        report['reviewer'] = reviewer
    if review.get('group_identity_exposed') is not False:
        errors.append('review: blindness missing or explicitly exposed')
    else:
        report['blindness'] = 'declared_unverified'
    rows = review.get('rows')
    if type(rows) is not list:
        errors.append('review: rows must be a list')
        return report
    seen = set()
    for number, row in enumerate(rows):
        label = f'row {number}'
        if type(row) is not dict or type(row.get('review_id')) is not str:
            errors.append(label+': invalid row or ID')
            continue
        identity = row['review_id']
        if identity not in items or identity in seen:
            errors.append(label+': unknown or duplicate ID')
            continue
        seen.add(identity)
        item = items[identity]
        if type(row.get('reason')) is not str or not row['reason'].strip():
            errors.append(label+': reason required')
        quotes = row.get('quotes')
        if (type(quotes) is not list or (item['answer'] and not quotes)
                or any(type(q) is not str or not q.strip() or q not in item['answer'] for q in quotes)):
            errors.append(label+': quotes must be exact nonempty answer substrings')
        incomplete = item['transport_status'] != 'complete' or not item['answer'].strip()
        if item['kind'] == 'creative_copy':
            fact, score = row.get('fact_accuracy'), row.get('intent_score')
            if fact not in ('pass', 'fail', 'unknown'):
                errors.append(label+': invalid fact_accuracy')
            if 'intent_score' not in row or not (score is None or type(score) is int and 1 <= score <= 5):
                errors.append(label+': intent_score must be integer 1..5 or null')
            if incomplete and (fact != 'unknown' or score is not None):
                errors.append(label+': incomplete output must remain unknown')
        else:
            for field in ('decision_pass', 'reason_pass'):
                value = row.get(field)
                if field not in row or not (value is None or type(value) is bool):
                    errors.append(label+': '+field+' must be boolean or null')
                if incomplete and value is not None:
                    errors.append(label+': incomplete output must remain unknown')
            historical = row.get('historical_source_accuracy')
            if historical not in ('pass', 'fail', 'unknown') or incomplete and historical != 'unknown':
                errors.append(label+': invalid historical_source_accuracy')
    if seen != set(items):
        errors.append('review: missing packet IDs')
    if not errors:
        report['structural_validation'] = 'passed'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root'); parser.add_argument('--output', required=True)
    parser.add_argument('--review', help='validate an external blind-review receipt without grading')
    args = parser.parse_args(argv)
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    if output.is_relative_to(root):
        raise ValueError('diagnostic output must be outside frozen input directory')
    report = build_review_report(root, args.review) if args.review else build_report(root)
    with output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    summary = {'report':str(output), 'new_model_requests':0}
    if args.review:
        summary['structural_validation'] = report['structural_validation']
    else:
        summary['engineering_first_pass'] = report['engineering_first_pass']
    print(json.dumps(summary))
    return 1 if args.review and report['structural_validation'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
