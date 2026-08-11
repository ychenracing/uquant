# uquant stage-A checkpoint recovery

This branch stores a self-contained recovery checkpoint for local commit `2c3f7f0db7360724f6aed941cae745d97670411e` (`Harden generalization validation gates`).

The Git bundle contains the complete history and every tracked file at that commit, including the restored strategic-cohort candidate, the stage-A Bandit/coverage closure, frozen market data, `DATA_MANIFEST.json`, `SHA256SUMS`, source, tests, documentation, lockfiles, and frozen benchmark inputs. The bundle is split into 15 independently base64-encoded files.

## Restore

After checking out this backup branch:

```bash
checkpoint_dir=.checkpoint
parts_dir=/tmp/uquant-checkpoint-2c3f7f0-parts
bundle_file=/tmp/uquant-checkpoint-2c3f7f0.bundle
part_checksums="$(pwd)/$checkpoint_dir/PART_SHA256SUMS"
mkdir -p "$parts_dir"
for encoded in "$checkpoint_dir"/bundle/*.b64; do
  decoded="$parts_dir/$(basename "${encoded%.b64}")"
  base64 --decode "$encoded" > "$decoded"
done
(cd "$parts_dir" && sha256sum --check < "$part_checksums")
for part in "$parts_dir"/uquant-checkpoint-2c3f7f0.bundle.part-*; do
  dd if="$part" status=none
done > "$bundle_file"
echo "00798cd2849006d4dea1e121d4ce87fa193433ab33a533b58826dc0d14dfd395  $bundle_file" | sha256sum --check
git bundle verify "$bundle_file"
git clone --branch codex/resume-cohort-hysteresis-20260811 "$bundle_file" uquant-restored
git -C uquant-restored rev-parse HEAD
git -C uquant-restored status --porcelain=v1
```

The HEAD command must print:

```text
2c3f7f0db7360724f6aed941cae745d97670411e
```

The status command must print nothing.

## Resume boundary

Stage A is complete: all six low-severity Bandit findings were resolved with fixed Git subprocess invocation and exact deterministic-sampling suppression, Bandit reports zero findings, engineering coverage is 85.05%, and the relevant tests plus Ruff, strict Mypy, bytecode compilation, build, frozen-data, and diff checks pass.

The D continuous release blocker remains unchanged at the accepted candidate: `35.0573x` wealth, `27.78%` maximum drawdown, and 85 orders. Only the `60.59x` wealth floor fails. Do not push this checkpoint to `main` as a validated production release.

One causally justified stage-B experiment pre-confirmed the generic leader owner during the final capital-budget repair. It correctly entered on 2025-02-25 but then reproduced the already rejected `11.8184x` path, with 29.80% drawdown and 110 orders. It was fully rolled back and is not present in the bundle. Do not repeat that change or reset the capital-budget drawdown under another name.

The next and only admissible entry is a point-in-time state-contract analysis of the 2025-02-25 to 2025-04-07 owner/weight transition. It must explain how to avoid the immediate `0.00 -> 0.80` generic-leader gross jump and subsequent budget re-escalation without relaxing risk, exit, impact, drawdown, universe, or frozen economic gates. No further D long replay is justified until such a state Bug is proven.

No credentials, account snapshots, broker state, virtual environment, caches, or failed strategy experiment are included.
