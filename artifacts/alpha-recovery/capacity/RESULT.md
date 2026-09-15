# Result: correctness only, no alpha recovery

Exact local producer 67ec6914824e554bb391b871f251489aedcbaa93 is preserved in source.bundle. Remote production-equivalent commit: 812b4ddc07e70f453cc128fa796ffabbc06cac35. Fingerprint: 9e249712f58fa74d49a4a84bc384a71960e2cf1078993b362cf9e4348309a3db.

77 focused tests passed. All three deterministic economic replays completed under Python 3.12.13 / numpy 2.5.1 / pandas 3.0.5 / uv 0.11.33. A wealth 11.87065858669219, E wealth 13.821022723557565, remove308 prefix equity 2219163.36163238. These exactly equal the canonical current baseline; A/E drawdown, orders and turnover also exactly equal baseline. Raw outputs retain actual producer identity and unchanged input/config metadata.

The full-position-count routing defect is real, but removing it did not generate an economically effective transfer in the measured windows. This is not an accepted alpha candidate. Do not tune its edge, confirmation, transfer size or position limits. No full acceptance or main merge is claimed.

Next: independent component ablation of the failed stock-confirmation candidate. Keep original stock qualification and remove only redundant ordinary deployment waiting, preserving exact strategic-certificate deferral and all capital/risk/settlement guards. This is not a repeat of the combined failed candidate.
