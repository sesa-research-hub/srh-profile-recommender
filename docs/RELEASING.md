# Publishing the first alpha

The release is `srh-profile-recommender-v0.1.0-alpha.1`, a public prerelease of this monorepo. See THIRD_PARTY_NOTICES.md for archive boundaries.

Run from a clean, committed `feature/srh-workload-intelligence` branch:

```bash
cd ~/ai/srh-private-ai
git -c credential.helper= push -u origin feature/srh-workload-intelligence
python3 scripts/publish_srh_release.py --publish
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
