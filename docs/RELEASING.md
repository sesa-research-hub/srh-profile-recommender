# Publishing an SRH prerelease

The publishing helper can create a public prerelease of this monorepo. See
THIRD_PARTY_NOTICES.md for archive boundaries.

For the first alpha, run from a clean, committed
`feature/srh-workload-intelligence` branch:

```bash
cd ~/ai/srh-profile-recommender
git -c credential.helper= push -u origin feature/srh-workload-intelligence
python3 scripts/publish_srh_release.py --publish
```

For the Capacity Planner alpha, run from a clean, committed
`feature/srh-capacity-planner` branch:

```bash
cd ~/ai/srh-profile-recommender
git -c credential.helper= push -u origin feature/srh-capacity-planner
python3 scripts/publish_srh_release.py --publish \
  --branch feature/srh-capacity-planner \
  --tag srh-profile-recommender-v0.2.0-alpha.1 \
  --pr-title "SRH Capacity Planner and measured deployment recommendation" \
  --release-name "SRH Profile Recommender 0.2.0-alpha.1" \
  --pr-body docs/releases/PULL_REQUEST-CAPACITY.md \
  --release-body docs/releases/0.2.0-alpha.1.md
```

For Git, enter your GitHub username and use the PAT as the password. The Python helper asks for the PAT again, hidden; the token identifies your account and is never saved. A fine-grained token must select this repository and allow Contents read/write and Pull requests read/write. Repository and organization policies still apply.

Without `--publish`, the helper only validates the clean local checkout and runs offline tests. Publishing checks the remote branch matches the tested commit, checks main has not diverged, creates or reuses the PR, and requests a normal merge pinned to that source SHA. GitHub may require reviews or checks: complete them through the PR and rerun. The script never overrides protections.

The merged Git tree must equal the tested tree before a lightweight tag and public prerelease are created. Existing conflicting tags are never moved. Reruns reuse the matching merged PR, tag and prerelease. A timeout can leave an operation completed remotely; rerun to reconcile. No custom binaries, model weights or private benchmark artifacts are uploaded.

After success, synchronize the local main branch:

```bash
git fetch origin --tags
git switch main
git merge --ff-only origin/main
```
