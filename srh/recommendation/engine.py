# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Conservative comparison of observed runtime profiles for one experiment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from srh.profiles.capture import canonical_sha256
from srh.recommendation.evidence import replay, finite
from srh.workloads.contract import validate_contract
from srh.workloads.planner import build_plan
from srh.experiments.paired_modes import slo_assessment


def require(condition, message):
    if not condition:
        raise ValueError(message)


def candidate(evidence, contract):
    require(evidence['schema'] == 'srh.paired-response-experiment.v1', 'unsupported schema')
    require(evidence['workload']['id'] == contract['id'], 'workload mismatch')
    profile = evidence['runtime_profile']
    identity = profile['execution_identity']
    digest = canonical_sha256(identity)
    require(profile['profile_sha256'] == digest and profile['profile_id'] == 'srh-' + digest[:12], 'profile hash mismatch')
    require(profile['provenance']['effective_process_observed'] is True, 'runtime not observed')
    require(profile['provenance']['source'] == 'live-runtime', 'runtime not live')
    require(evidence['runtime_verification']['stable'] is True, 'runtime changed during measurement')
    require(evidence['runtime_verification']['end_profile_sha256'] == digest, 'end profile mismatch')
    hardware = identity['hardware']
    require(hardware['architecture'] and hardware['gpu']['name'] and hardware['gpu']['driver'], 'incomplete hardware observation')
    runtime = identity['effective_runtime']
    require(isinstance(runtime['process_environment'], dict), 'missing observed process environment')
    require(runtime['model']['snapshot'] and identity['container_image']['image_id'], 'missing immutable runtime identity')
    require(evidence['runtime']['model'] in runtime['model']['served_names'], 'served model mismatch')
    protocol = evidence['protocol']
    require(protocol['type'] == 'paired-interleaved', 'unsupported measurement protocol')
    require(protocol['repetitions'] >= 5, 'at least five repetitions required')
    require(protocol['reasoning_policy'] == contract['request_profile']['reasoning'], 'reasoning mismatch')
    require(evidence['comparison']['response_contract'] == contract['response_contract'], 'response contract mismatch')
    require(evidence['comparison']['quality_requirements'] == contract['quality'], 'quality policy mismatch')
    require(len(evidence['comparison']['pack_sha256']) == 64, 'missing scenario content digest')
    require(evidence['comparison']['executor_version'], 'missing executor version')
    require(type(evidence['comparison']['seed']) is int, 'missing seed')
    mode = contract['response_contract']['default_mode']
    require(contract['tools']['enabled'] is False and not contract['quality'].get('tool_call_correctness_required'),
            'tool evaluation is not supported by this evaluator')
    require(contract['request_profile']['streaming'] is True, 'only streaming measurements are supported')
    require(evidence['experiment'] in build_plan(contract)['experiments'], 'experiment does not match workload plan')
    expected_mode = {'disabled': 'no-think', 'required': 'think'}.get(contract['request_profile']['reasoning'])
    require(expected_mode and protocol['resolved_model_mode'] == expected_mode, 'resolved reasoning mode mismatch')
    summaries = {response_mode: replay(evidence, response_mode) for response_mode in ('probe', 'assistant')}
    summary = summaries[mode]
    checks = slo_assessment(summary, contract['service_objectives'], contract['quality'])
    required = {'ttfa_p95_ms', 'end_to_end_p95_ms', 'answer_tokens_per_second_min', 'error_rate_max', 'quality_minimum_score'}
    require(required <= checks['checks'].keys(), 'incomplete quality/SLO contract')
    for item in checks['checks'].values():
        if item['actual'] is None and summary['request_count'] == 0:
            continue  # A complete all-failed campaign is valid negative evidence.
        require(type(item['actual']) in (int, float) and math.isfinite(item['actual']) and item['actual'] >= 0, 'missing or invalid metric')
    mandatory_rate = summary['required_quality_pass_rate']
    checks['checks']['required_quality_checks'] = {'actual': mandatory_rate, 'target': 1.0, 'pass': mandatory_rate == 1.0}
    checks['pass'] = checks['pass'] and mandatory_rate == 1.0
    signature = {
        'experiment': evidence['experiment'],
        'scenario': evidence['scenario'],
        'comparison': evidence['comparison'],
        'protocol': {k: v for k, v in protocol.items() if k != 'run_ids'},
        'hardware': identity['hardware'],
        'model': runtime['model'],
        'engine': runtime['engine'],
        'image_id': identity['container_image']['image_id'],
    }
    return signature, {
        'profile_id': profile['profile_id'], 'profile_sha256': digest,
        'eligible': checks['pass'], 'checks': checks['checks'],
        'blocking_objectives': [k for k, v in checks['checks'].items() if not v['pass']],
        'runtime': runtime,
        'observed_summary': summary,
        'diagnostic_summaries': summaries,
    }


def recommend(evidences, contract):
    errors, _ = validate_contract(contract)
    require(not errors, 'invalid workload contract: ' + '; '.join(errors))
    for name, value in contract['service_objectives'].items():
        finite(value, name)
    finite(contract['quality']['minimum_score'], 'minimum_score')
    rows, rejected, signatures = [], [], []
    for source, evidence in evidences:
        try:
            signature, row = candidate(evidence, contract)
            require(row['profile_sha256'] not in [r['profile_sha256'] for r in rows], 'duplicate profile: select one campaign explicitly')
            row['source_evidence'] = source
            row['evidence_sha256'] = canonical_sha256(evidence)
            rows.append(row)
            signatures.append(signature)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            rejected.append({'source_evidence': source, 'reason': str(exc)})
    comparable = bool(signatures) and all(s == signatures[0] for s in signatures)
    mismatch_fields = sorted({key for signature in signatures[1:]
                              for key in signatures[0] if signature[key] != signatures[0][key]}) if signatures else []
    sufficient = comparable and len(rows) >= 3 and not rejected
    eligible = [r for r in rows if r['eligible']]
    # Explicit lexicographic policy: quality/SLO gates first, then latency.
    ranked = sorted(eligible, key=lambda r: (
        r['checks']['ttfa_p95_ms']['actual'],
        r['checks']['end_to_end_p95_ms']['actual'],
        -r['checks']['answer_tokens_per_second_min']['actual'],
        r['profile_sha256'],
    ))
    verdict = 'INSUFFICIENT_EVIDENCE' if not sufficient else ('RECOMMENDED' if ranked else 'NO_PROFILE_MEETS_SLO')
    return {
        'schema': 'srh.profile-recommendation.v1',
        'engine_version': '0.2.0',
        'hash_format': 'SHA256 of UTF-8 JSON, sorted keys, compact separators, ensure_ascii=False',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'workload_id': contract['id'], 'contract_sha256': canonical_sha256(contract),
        'verdict': verdict,
        'recommended_profile_id': ranked[0]['profile_id'] if sufficient and ranked else None,
        'policy': 'quality and all SLO gates; TTFA p95 ascending, E2E p95 ascending, answer throughput descending; hash tie-break',
        'incompatible_fields': mismatch_fields,
        'comparable': comparable, 'minimum_distinct_profiles': 3,
        'candidates': rows, 'rejected_evidence': rejected,
        'scope': signatures[0]['experiment'] if comparable else None,
        'ranking': [r['profile_id'] for r in ranked] if sufficient else [],
        'prefix_control_experiment': next((e for e in build_plan(contract)['experiments']
                                           if comparable and e['cache_state'] == 'shared-prefix'
                                           and e['concurrency'] == signatures[0]['experiment']['concurrency']
                                           and e['request'] == signatures[0]['experiment']['request']), None)
                                     if verdict == 'NO_PROFILE_MEETS_SLO' else None,
        'recommended_next_action': (
            'Validate selected profile over the remaining workload plan and repeat across runs'
            if verdict == 'RECOMMENDED' else
            'Collect at least three valid profiles with the same experiment, scenario and protocol'
            if verdict == 'INSUFFICIENT_EVIDENCE' else
            'Run a separate shared-prefix control cohort to test whether context reuse resolves TTFA'
            if all(r['blocking_objectives'] == ['ttfa_p95_ms'] for r in rows) else
            'Address the reported quality/SLO blockers before deployment'
        ),
        'limitations': ['Applies only to the measured experiment, not the whole workload envelope.',
                        'Five repetitions are exploratory evidence, not statistical confidence in tail latency.',
                        'Hashes detect inconsistency, not malicious evidence fabrication.',
                        'Semantic quality is scoped to the supplied structured enterprise-orders scenario.',
                        'Endpoint-to-container routing and concurrent external load require operator verification.',
                        'Sequential exploratory runs do not isolate thermal or filesystem-cache effects.',
                        'Model snapshot paths are recorded; weight files are not independently content-attested.'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('contract', type=Path)
    parser.add_argument('evidence', type=Path, nargs='+')
    parser.add_argument('--markdown', type=Path, help='Also write a readable decision report')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    evidence = [(str(p.resolve()), json.loads(p.read_text())) for p in args.evidence]
    report = recommend(evidence, contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    if args.markdown:
        from srh.recommendation.report import render_markdown
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report))
    print(report['verdict'])
    print('Recommended:', report['recommended_profile_id'])
    print('Decision evidence:', args.output)


if __name__ == '__main__':
    main()
