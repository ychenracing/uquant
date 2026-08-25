"""Compatibility facade for the canonical observational execution journal."""

from uquant.observation.execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    execution_journal_checkpoint,
    read_execution_journal,
    record_to_dict,
    render_execution_journal,
)
from uquant.observation.execution_journal.models import (
    BROKER_ORDER_ID_PATTERN as _BROKER_ORDER_ID,
)
from uquant.observation.execution_journal.models import PLAN_ID_PATTERN as _PLAN_ID
from uquant.observation.execution_journal.models import SHA256_PATTERN as _SHA256
from uquant.observation.execution_journal.models import SYMBOL_PATTERN as _SYMBOL
from uquant.observation.execution_journal.models import V1_FIELDS as _V1_FIELDS
from uquant.observation.execution_journal.models import V2_FIELDS as _V2_FIELDS
from uquant.observation.execution_journal.models import ZERO_HASH as _ZERO_HASH

__all__ = (  # noqa: RUF022 - frozen public-name order
    "JournalCheckpoint",
    "JournalRecord",
    "JournalStatus",
    "_BROKER_ORDER_ID",
    "_PLAN_ID",
    "_SHA256",
    "_SYMBOL",
    "_V1_FIELDS",
    "_V2_FIELDS",
    "_ZERO_HASH",
    "append_filled",
    "append_planned",
    "append_skipped",
    "execution_journal_checkpoint",
    "read_execution_journal",
    "record_to_dict",
    "render_execution_journal",
)

for _value in (
    JournalCheckpoint,
    JournalRecord,
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    execution_journal_checkpoint,
    read_execution_journal,
    record_to_dict,
    render_execution_journal,
):
    _value.__module__ = __name__
