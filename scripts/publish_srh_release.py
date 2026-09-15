#!/usr/bin/env python3
"""Publish the tested SRH alpha; credentials remain in process memory only."""
import argparse
import getpass
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

REPO = 'sesa-research-hub/srh-profile-recommender'
BRANCH = 'feature/srh-workload-intelligence'
TAG = 'srh-profile-recommender-v0.1.0-alpha.1'
ROOT = Path(__file__).resolve().parents[1]

class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, data=None):
        req = urllib.request.Request(
            'https://api.github.com/repos/' + REPO + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == 'GET':
                return None
            raise RuntimeError(f'GitHub HTTP {exc.code}: operation stopped. Check repository permissions, reviews and required checks; then rerun.') from None


def publish(api, sha, tree, pr_body, release_body):
    head = api.request('GET', '/commits/' + BRANCH)
    if not head or head['sha'] != sha:
        raise RuntimeError('Remote branch differs from local HEAD. Push the tested branch first.')
    pulls = api.request('GET', '/pulls?state=all&head=sesa-research-hub:' + BRANCH + '&base=main&per_page=100') or []
    matching = [p for p in pulls if p['head']['sha'] == sha and p['head']['repo']['full_name'] == REPO]
    pr = next((p for p in matching if p.get('merged_at')), None)
    if pr is None:
        comparison = api.request('GET', '/compare/main...' + sha)
        if not comparison or comparison['status'] not in ('ahead', 'identical'):
            raise RuntimeError('main has diverged. Reconcile and test locally before publishing.')
        pr = next((p for p in matching if p['state'] == 'open'), None)
        if pr is None:
            pr = api.request('POST', '/pulls', {'title': 'SRH Profile Recommender: first evidence-driven alpha', 'head': BRANCH, 'base': 'main', 'body': pr_body})
        print('Pull request:', pr['html_url'])
        merged = api.request('PUT', f"/pulls/{pr['number']}/merge", {'sha': sha, 'merge_method': 'merge'})
        if not merged.get('merged'):
            raise RuntimeError('PR not merged. Complete required reviews/checks and rerun.')
        merge_sha = merged['sha']
    else:
        merge_sha = pr['merge_commit_sha']
    commit = api.request('GET', '/git/commits/' + merge_sha)
    if not commit or commit['tree']['sha'] != tree:
        raise RuntimeError('Merged tree differs from tested source. No tag or release created.')
    ref = api.request('GET', '/git/ref/tags/' + TAG)
    if ref:
        obj = ref['object']
        if obj['type'] != 'commit' or obj['sha'] != merge_sha:
            raise RuntimeError('Existing tag conflicts with verified merge; it will not be overwritten.')
    else:
        api.request('POST', '/git/refs', {'ref': 'refs/tags/' + TAG, 'sha': merge_sha})
    release = api.request('GET', '/releases/tags/' + TAG)
    if release:
        if release.get('draft') or not release.get('prerelease'):
            raise RuntimeError('Existing release has unexpected status; inspect it manually.')
    else:
        release = api.request('POST', '/releases', {'tag_name': TAG, 'target_commitish': merge_sha, 'name': 'SRH Profile Recommender 0.1.0-alpha.1', 'body': release_body, 'draft': False, 'prerelease': True, 'make_latest': 'false'})
    print('Prerelease:', release['html_url'])


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Authenticate interactively and create PR, merge, tag and prerelease')
    args = parser.parse_args()
    if git('branch', '--show-current') != BRANCH or git('status', '--porcelain'):
        raise RuntimeError('Use the expected branch with a clean working tree.')
    if git('remote', 'get-url', 'origin') != 'https://github.com/' + REPO + '.git':
        raise RuntimeError('Unexpected origin URL.')
    subprocess.run(['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT, check=True)
    sha, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    print('Verified local commit:', sha)
    if not args.publish:
        print('Preflight complete. Use --publish to authenticate and publish.')
        return
    token = getpass.getpass('GitHub personal access token (hidden, never saved): ')
    if not token:
        raise RuntimeError('Empty token.')
    publish(GitHub(token), sha, tree,
            (ROOT / 'docs/releases/PULL_REQUEST.md').read_text(),
            (ROOT / 'docs/releases/0.1.0-alpha.1.md').read_text())

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        raise SystemExit(str(exc))
