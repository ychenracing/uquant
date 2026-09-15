# Wire capital repair to the production input path

Parent producer: `4c0ecc17bf91e7714306e58f61266f4d94e8f3d6`.

The initial five replays were economically unchanged because the new repair counter never advanced. Production intentionally calls risk assessment with `reference_context=None`; passing that context globally would change existing breadth semantics. Instead, the repair-only completeness check uses the supplied risk panel's already-started reference series when no optional context is supplied. Current observations and finite features remain mandatory for those references, both market indices and actual holdings. An explicitly supplied incomplete context still blocks repair.

Three failing regression cases reproduce the dormant production path. After the fix, 119 affected tests pass, including next-open funding with both optional-context modes, gaps, duplicate scans, missing/current nonfinite references, capital cap retention, chronic damage and Sentinel integration. Ruff and mypy pass for both affected risk modules.

This correction changes neither the proposed five-session recovery rule nor any economic target. It retains peaks, historical losses, the capital ladder and reduced exposure caps. Reuse the verified canonical native controls and A/E/H2 baselines. Run both complete native removal paths and A/E/H2 under this new committed producer, with separate raw output paths. The user's one percentage point drawdown margin remains effective. No full acceptance or alpha improvement is established by engineering tests.
