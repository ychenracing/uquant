# Phase 5 gross-cap rejection archive

Phase 5 commit `9a82143a3079bdd846c995962a246a66c834c1d5` is rejected. Production remains
`FREEZE_ONLY`; `LIMITED_GROSS_CAP` is not a production or CLI mode and old configuration values
raise an explicit error.

The first pre-registered paired cell, `a/h1_2024`, retained only **91.8484162698%** of base
wealth. Max drawdown improved by **0.0000 percentage points**, Acute return worsened by
**1.0979160035 percentage points**, account orders increased by **4**, and gross turnover
increased by **1.2085078385**. The decision is **REJECTED**.

The compact machine-readable record is
`artifacts/sentinel/phase6/phase5_gross_cap_rejection.json`. It binds the source branch, commit,
baseline commit, locked-candidate SHA-256 and paired-evidence SHA-256. This archive intentionally
contains neither the large Phase 5 equity curves nor any Phase 5 production implementation.
