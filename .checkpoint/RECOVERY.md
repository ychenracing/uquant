# uquant checkpoint recovery

This branch stores an exact, self-contained recovery checkpoint for local commit `7d7063d3f3973e438106309e5f0ac46412821610`.

The Git bundle contains the complete local history and every tracked file at that commit, including frozen market data, `DATA_MANIFEST.json`, `SHA256SUMS`, source, tests, documentation, lockfiles, and frozen benchmark inputs. The bundle is split into 15 independently base64-encoded files so it can be stored safely through the repository contents API.

## Restore

After checking out this backup branch:

```bash
checkpoint_dir=.checkpoint
bundle_file=/tmp/uquant-checkpoint-7d7063d.bundle
for part in "$checkpoint_dir"/bundle/*.b64; do
  base64 --decode "$part"
done > "$bundle_file"

echo "3f786dd4780a191a226d294c4d90b75244521d979009f9b0ebeb0031dc920ecc  $bundle_file" | sha256sum --check
git bundle verify "$bundle_file"
git clone "$bundle_file" uquant-restored
git -C uquant-restored rev-parse HEAD
```

The final command must print:

```text
7d7063d3f3973e438106309e5f0ac46412821610
```

## Resume boundary

The checkpoint keeps the validated strategic-cohort hysteresis repair. D continuous performance improved from 8.1528x to 35.0573x, with 27.78% maximum drawdown and 85 orders. Drawdown and order gates passed, but the frozen 60.59x wealth gate did not. Do not push this candidate to `main` as a validated production release.

The next low-cost engineering closure is: resolve six low-severity Bandit findings precisely, raise coverage from 84.88% to at least 85%, and rerun the complete test/static gate. The only remaining strategy blocker is the D continuous wealth gate; any further strategy change requires a new long replay.

No credentials, account snapshots, broker state, virtual environment, caches, or the rejected capital-cohort reset experiment are included.
