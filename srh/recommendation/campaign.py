# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Measure isolated prefill variants, preserving and restoring the source container."""
import argparse
import fcntl
import signal
import copy
from contextlib import ExitStack
import http.client
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=120)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def docker(method, path, body=None):
    conn = DockerConnection()
    conn.request(method, path, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status >= 400:
        raise RuntimeError(f'Docker {method} {path}: {response.status} {data.decode()}')
    return json.loads(data) if data else None


def ready(container, url, timeout=1200):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = docker('GET', f'/containers/{container}/json')['State']
        if not state['Running']:
            raise RuntimeError(f'{container} exited: {state["ExitCode"]}, OOM={state["OOMKilled"]}')
        try:
            with urllib.request.urlopen(url + '/models', timeout=5) as response:
                if json.load(response).get('data'):
                    print(f'{container}: ready', flush=True)
                    return
        except (OSError, ValueError):
            pass
        time.sleep(5)
    raise TimeoutError(f'{container}: readiness exceeded {timeout}s')


def _main(resources):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('experiment')
    parser.add_argument('pack', type=Path)
    parser.add_argument('--container', default='qwen38-flash')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Authorize temporary stop and automatic restoration of the source runtime')
    args = parser.parse_args()
    source = docker('GET', f'/containers/{args.container}/json')
    if not source['State']['Running']:
        parser.error('source container must be running')
    if source['HostConfig']['NetworkMode'] in ('host', 'none'):
        parser.error('requires a bridge-networked source container')
    bindings = source['HostConfig'].get('PortBindings', {}).get('8000/tcp', [])
    if len(bindings) != 1 or not bindings[0].get('HostPort'):
        parser.error('requires exactly one published port for container port 8000')
    source_url = f"http://localhost:{int(bindings[0]['HostPort'])}/v1"
    lock = resources.enter_context(open(f'/tmp/srh-campaign-{source["Id"]}.lock', 'a'))
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('another campaign already owns this source container')
    command = source['Config'].get('Cmd', [])
    if '--max-num-batched-tokens' not in command:
        parser.error('source must explicitly declare --max-num-batched-tokens 8192')
    budget_index = command.index('--max-num-batched-tokens') + 1
    if budget_index >= len(command) or command[budget_index] != '8192':
        parser.error('this prefill campaign requires an 8192-token baseline')
    variants = [4096, 16384]
    print('Campaign: baseline + prefill budgets', variants, flush=True)
    if not args.execute:
        print('Use --execute to measure. Source will be temporarily stopped and restored.')
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    source_file = args.output_dir / 'source-container.private.json'
    source_file.touch(mode=0o600)
    source_file.write_text(json.dumps(source, indent=2))
    journal = {'source_container_id': source['Id'], 'variants': variants, 'evidence': [], 'restored': False}
    journal_path = args.output_dir / 'campaign.json'
    root = Path(__file__).resolve().parents[2]
    results = root / 'srh/experiments/results'

    def save():
        journal_path.write_text(json.dumps(journal, indent=2) + '\n')

    def measure(name, url, label):
        before = set(results.glob('paired-*.json'))
        log = args.output_dir / f'{label}.log'
        print(f'Measuring {label}', flush=True)
        with log.open('w') as stream:
            subprocess.run([sys.executable, '-u', '-m', 'srh.experiments.paired_modes',
                            str(args.plan), args.experiment, str(args.pack), '--container', name,
                            '--base-url', url, '--repetitions', '5'], cwd=root, stdout=stream,
                           stderr=subprocess.STDOUT, check=True, timeout=1800)
        created = set(results.glob('paired-*.json')) - before
        if len(created) != 1:
            raise RuntimeError('Expected exactly one new evidence file')
        evidence = created.pop()
        target = args.output_dir / f'{label}.json'
        target.write_bytes(evidence.read_bytes())
        journal['evidence'].append(str(target.resolve()))
        save()
        print(f'Completed {label}: {target}', flush=True)

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f'campaign interrupted by signal {signum}')

    previous_handler = signal.signal(signal.SIGTERM, interrupt)
    resources.callback(signal.signal, signal.SIGTERM, previous_handler)
    active = None
    stopped = False
    try:
        ready(source['Id'], source_url)
        measure(args.container, source_url, 'baseline-8192')
        # Preserve original container and its writable layer; never recreate it.
        stopped = True
        docker('POST', f'/containers/{source["Id"]}/stop?t=60')
        for budget in variants:
            config = copy.deepcopy(source['Config'])
            config['Image'] = source['Image']
            config['Hostname'] = ''
            config['Labels'] = {'srh.campaign': args.output_dir.name}
            index = config['Cmd'].index('--max-num-batched-tokens')
            config['Cmd'][index + 1] = str(budget)
            host = copy.deepcopy(source['HostConfig'])
            host['RestartPolicy'] = {'Name': 'no'}
            host['PortBindings'] = {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18301'}]}
            host['AutoRemove'] = False
            config['HostConfig'] = host
            name = f'srh-prefill-{budget}-{int(time.time())}'
            active = docker('POST', f'/containers/create?name={name}', config)['Id']
            journal['active_variant_id'] = active
            save()
            docker('POST', f'/containers/{active}/start')
            print(f'Started {name}; loading model', flush=True)
            ready(active, 'http://localhost:18301/v1')
            measure(name, 'http://localhost:18301/v1', f'prefill-{budget}')
            docker('POST', f'/containers/{active}/stop?t=60')
            # Retain stopped variants for audit; no volumes or containers deleted.
            active = None
    except BaseException as exc:
        journal['error'] = str(exc)
        save()
        raise
    finally:
        if active:
            try:
                docker('POST', f'/containers/{active}/stop?t=60')
            except Exception as exc:
                journal['variant_stop_error'] = str(exc)
        if stopped:
            print('Restoring original container', flush=True)
            docker('POST', f'/containers/{source["Id"]}/start')
            ready(source['Id'], source_url)
        journal['restored'] = True
        journal.pop('active_variant_id', None)
        save()
        print('Original runtime restored', flush=True)


def main():
    with ExitStack() as resources:
        _main(resources)


if __name__ == '__main__':
    main()
