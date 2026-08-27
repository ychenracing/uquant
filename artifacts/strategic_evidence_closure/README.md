# Strategic Evidence Closure Artifacts

This directory contains compact, sealed evidence for Tasks 3-6. Large deterministic route
and reachability shards remain external; `evidence_manifest.json` binds their logical paths,
byte sizes, SHA-256 identities, and row counts as the **sealed expected identity**. The separate
availability and verification fields describe assembly-time readback only; a later validator
reports live readback for whichever recovered shards are supplied. Absence is explicit and is
never treated as verification. `compact_summary.json` separates experiment completion from the
literal capability result. `analysis.md` answers the four research questions without converting
synthetic diagnostics into historical-return claims.

Validate tracked evidence and any available external shards with:

```bash
python -m scripts.run_strategic_evidence_closure validate
```
