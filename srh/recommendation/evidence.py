# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Validate raw observations and replay the supported semantic evaluator."""
import copy
import hashlib
import math
from pathlib import Path

from srh.experiments.executor import summarize_batches
from srh.scenarios.run_pack import evaluate_answer


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, name, minimum=0):
    require(type(value) in (int, float) and math.isfinite(value) and value >= minimum,
            f'{name}: expected finite number >= {minimum}')
    return value


def replay(evidence, mode):
    """Never use stored aggregate quality, request counts or summary metrics."""
    comparison = evidence['comparison']
    truth = comparison['ground_truth']
    require(truth['schema'] == 'srh.scenario-ground-truth.v1', 'unsupported ground truth')
    evaluator_path = Path(__file__).resolve().parents[1] / 'scenarios/run_pack.py'
    expected_digest = hashlib.sha256(evaluator_path.read_bytes()).hexdigest()
    require(comparison['evaluator_sha256'] == expected_digest, 'evaluator version mismatch')
    repetitions = evidence['protocol']['repetitions']
    concurrency = evidence['experiment']['concurrency']
    require(type(repetitions) is int and repetitions >= 5, 'at least five repetitions required')
    require(type(concurrency) is int and concurrency >= 1, 'invalid concurrency')
    batches = copy.deepcopy(evidence['batches'][mode])
    require(len(batches) == repetitions, 'repetition count does not match observations')
    mandatory_passes = []
    for index, batch in enumerate(batches, 1):
        require(batch['repetition'] == index, 'missing or duplicated repetition')
        require(batch['concurrency'] == concurrency, 'batch concurrency mismatch')
        require(batch['cache_state'] == evidence['experiment']['cache_state'], 'batch cache mismatch')
        requests = batch['requests']
        require(len(requests) == concurrency, 'incomplete request batch')
        require(sorted(r['request_no'] for r in requests) == list(range(1, concurrency + 1)), 'duplicated request ids')
        elapsed = finite(batch['batch_elapsed_seconds'], 'batch elapsed', .000001)
        failed, tokens = 0, 0
        for request in requests:
            require(type(request['success']) is bool, 'invalid request success flag')
            if not request['success']:
                failed += 1
                continue
            perf = request['performance']
            for field in ('ttfa_seconds', 'elapsed_seconds', 'answer_tokens_per_second', 'completion_tokens'):
                finite(perf[field], field)
            require(perf['ttfa_seconds'] <= perf['elapsed_seconds'], 'TTFA exceeds E2E')
            request['quality'] = evaluate_answer(request['answer'], truth)
            # A truncated response does not meet the response contract, even if
            # it emitted the expected structured record before truncation.
            if perf.get('finish_reason') != 'stop':
                request['quality']['score'] = 0.0
                request['quality']['pass'] = False
            checks = request['quality']['checks']
            policy = comparison['quality_requirements']
            required = []
            if policy.get('grounding_required'):
                required.extend(['expected_order', 'expected_activity', 'no_forbidden_selection'])
            if policy.get('citation_required'):
                required.append('expected_evidence')
            if policy.get('structured_output_required') or comparison['response_contract'].get('structured_result_required'):
                required.append('structured_result')
            mandatory_passes.append(all(checks[k] for k in required) and perf.get('finish_reason') == 'stop')
            tokens += perf['completion_tokens']
        batch['aggregate'] = {'failed_requests': failed, 'completion_tokens_per_second': tokens / elapsed}
    summary = summarize_batches(batches)
    summary['required_quality_pass_rate'] = sum(mandatory_passes) / len(mandatory_passes) if mandatory_passes else 0.0
    return summary
