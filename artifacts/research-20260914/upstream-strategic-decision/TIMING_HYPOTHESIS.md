# Prospective certificate persistence hypothesis

Candidate based on preserved handoff 6d7f8263; not accepted pending replay.
H8's 34 sealed files and H9's seven raw traces verified against their manifests.
Recovered H8 tree: d8e8a159be0b3ef91db632fd8e9380a11d4ff371.

## Corrections established from current source

The handoff's “existing three-session minimum” is incorrect: the frozen
strategic_cohort_confirm_days is 2. E July 22 has candidate route streak 3,
but the preceding selected certificate was not READY. The streak counts
candidate route eligibility, not persistence of the selected whole cohort.
The evidence hash includes the session date and cannot remain equal across
different sessions. No configuration or qualification threshold is changed.

Use existing account candidate_tenure to retain the first READY session for
the same candidate/signature/quorum. Same-day rescans cannot mature it;
interrupted qualification or changed identity restarts observation. Weak
nondecisive reversal deployment remains deferred on that first session,
then proceeds through all original checks when the same READY identity
persists and qualification_streak exceeds strategic_cohort_confirm_days.
Cross-entry arbitration retains exact current symbol/signature/evidence hash.
Existing grant/epoch early returns and all post-commitment duties are retained.

The behavior test first failed on H9's continued deployment block on the next
distinct session. No production change preceded that failing test.
Screen in the authorized order: native-initialized E acute, E H2, native
remove308/remove502, A/D/E bull, then remaining protections. Results from
different candidates must not be combined. This is a falsifiable timing
hypothesis, not a repair claim.

Runtime setup: locked dev dependencies installed with uv sync --frozen
--extra dev --no-install-project after the editable build dependency fetch
timed out. Tests import the checkout. Runtime Python is 3.12.14, numpy 2.5.1,
pandas 3.0.5; record exact runtime for replays and do not relabel old evidence.
