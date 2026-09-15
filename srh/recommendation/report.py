# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Human-readable decision evidence; units are explicit."""


def render_markdown(report):
    lines = ['# SRH Profile Recommender', '', f"**Verdict: {report['verdict']}**", '',
             f"Workload: `{report['workload_id']}`", '',
             f"Recommended profile: `{report['recommended_profile_id'] or 'none'}`", '',
             '| Profile | Prefill budget | Quality min | TTFA p95 (ms) | E2E p95 (ms) | Answer tok/s min | Eligible |',
             '|---|---:|---:|---:|---:|---:|---|']
    for row in report['candidates']:
        checks = row['checks']
        lines.append('| ' + ' | '.join(str(v) for v in (
            row['profile_id'], row['runtime'].get('scheduler', {}).get('max_num_batched_tokens', '?'),
            checks['quality_minimum_score']['actual'], checks['ttfa_p95_ms']['actual'],
            checks['end_to_end_p95_ms']['actual'], checks['answer_tokens_per_second_min']['actual'],
            row['eligible'])) + ' |')
    lines.extend(['', '## Decision evidence', '', report['policy'], ''])
    for row in report['candidates']:
        lines.extend([f"### {row['profile_id']}", '', f"Source: `{row['source_evidence']}`", '',
                      f"Evidence SHA256: `{row['evidence_sha256']}`", ''])
        for name in row['blocking_objectives']:
            check = row['checks'][name]
            lines.append(f"- {name}: observed {check['actual']}, target {check['target']}")
        lines.append('')
    if report['rejected_evidence']:
        lines.extend(['## Rejected evidence', ''])
        lines.extend(f"- {r['source_evidence']}: {r['reason']}" for r in report['rejected_evidence'])
    if report['incompatible_fields']:
        lines.extend(['', 'Incompatible cohort fields: ' + ', '.join(report['incompatible_fields'])])
    lines.extend(['', '## Next action', '', report['recommended_next_action'], '', '## Scope and limitations', ''])
    lines.extend('- ' + note for note in report['limitations'])
    return '\n'.join(lines) + '\n'
