"""Set the completed-session liquidity observed by next-open execution tests."""

from __future__ import annotations

import pandas as pd


def set_prior_session_volume(frame: pd.DataFrame, open_date: pd.Timestamp, shares: float) -> None:
    index = frame.index.get_loc(open_date)
    assert isinstance(index, int) and index > 0
    previous = frame.index[index - 1]
    frame.loc[previous, "volume"] = shares
    if "amount" in frame:
        frame.loc[previous, "amount"] = shares * float(frame.loc[previous, "close"])
