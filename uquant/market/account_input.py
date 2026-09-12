"""Hash-bound historical raw-share inputs for the native account path."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ..account.corporate_actions import apply_corporate_actions, validate_action
from ..contracts.strict_json import canonical_json_sha256, strict_json_loads
from ..models.account import AccountState
from ..models.corporate_action import CorporateAction, DividendTaxDebit
from .price_series import linked_prices


class HistoricalAccountInput:
    """One reviewed source bundle; unavailable coverage cannot become zero events."""

    def __init__(self, root: Path) -> None:
        self.root = root
        raw = (root / 'ACCOUNT_INPUT.json').read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        document = strict_json_loads(raw)
        if not isinstance(document, dict) or set(document) != {
            'schema', 'start', 'end', 'sessions', 'files', 'sources',
            'coverage', 'actions', 'tax_debits',
        } or document['schema'] != 'historical-raw-shares':
            raise ValueError('historical account input schema differs')
        self.document: dict[str, Any] = document
        self.start, self.end = document['start'], document['end']
        if not date.fromisoformat(self.start) <= date.fromisoformat(self.end) < date(2026, 8, 6):
            raise ValueError('historical account input has an invalid/protected interval')
        sessions = document['sessions']
        if not isinstance(sessions, list) or not sessions or sessions != sorted(set(sessions)):
            raise ValueError('historical account sessions must be unique and increasing')
        if any(not self.start <= date.fromisoformat(day).isoformat() <= self.end for day in sessions):
            raise ValueError('historical account session outside coverage')
        self._validate_sources()
        self.actions = tuple(CorporateAction(**item) for item in document['actions'])
        self.tax_debits = tuple(DividendTaxDebit(**item) for item in document['tax_debits'])
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError('duplicate corporate action identity')
        for action in self.actions:
            validate_action(action)
            self._require_source(action.source_url, action.source_sha256)
            if action.ex_date > self.end:
                raise ValueError('corporate action lies beyond reviewed historical interval')
        for debit in self.tax_debits:
            self._require_source(debit.source_url, debit.source_sha256)

    def _validate_sources(self) -> None:
        root = self.root
        document = self.document
        sources = document['sources']
        if not isinstance(sources, dict) or not sources:
            raise ValueError('historical account input requires original sources')
        for source_id, source in sources.items():
            if not isinstance(source, dict) or set(source) != {'path', 'url', 'sha256'}:
                raise ValueError(f'original source fields differ: {source_id}')
            path = root / source['path']
            if not path.resolve().is_relative_to(root.resolve()) or not source['url'].startswith('https://'):
                raise ValueError(f'original source path/URL differs: {source_id}')
            if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
                raise ValueError(f'original source bytes differ: {source_id}')

    def apply(self, account: AccountState, *, date: str, phase: str) -> None:
        """Apply this verified source bundle at one native account boundary."""
        apply_corporate_actions(account, self.actions, date=date, phase=phase, tax_debits=self.tax_debits)

    def _require_source(self, url: str, digest: str) -> None:
        if not any(row['url'] == url and row['sha256'] == digest
                   for row in self.document['sources'].values()):
            raise ValueError('account event lacks matching original source bytes')

    def visible_identity(self, symbols: tuple[str, ...], as_of: str) -> str:
        """Bind available economic terms without hashing undisclosed future events."""
        actions = [asdict(action) for action in self.actions
                   if action.symbol in symbols and action.disclosed_date <= as_of]
        action_ids = {action['action_id'] for action in actions}
        debits = [asdict(debit) for debit in self.tax_debits
                  if debit.action_id in action_ids and debit.date <= as_of]
        return canonical_json_sha256({'schema': self.document['schema'],
                                      'actions': sorted(actions, key=lambda row: row['action_id']),
                                      'tax_debits': sorted(debits, key=lambda row: row['debit_id'])})

    def _validate_calendar_coverage(self, symbol: str, frame: pd.DataFrame) -> None:
        coverage = self.document['coverage'].get(symbol)
        if not isinstance(coverage, dict) or set(coverage) != {
            'reviewed_from', 'reviewed_through', 'action_sources', 'absent_sessions',
        }:
            raise ValueError(f'{symbol}: missing reviewed action/calendar coverage')
        if coverage['reviewed_from'] > self.start or coverage['reviewed_through'] < self.end:
            raise ValueError(f'{symbol}: incomplete reviewed coverage {self.start}..{self.end}')
        if not coverage['action_sources'] or any(
            source not in self.document['sources'] for source in coverage['action_sources']
        ):
            raise ValueError(f'{symbol}: company-action coverage lacks original sources')
        absent = coverage['absent_sessions']
        if not isinstance(absent, dict):
            raise ValueError(f'{symbol}: absent-session review must be explicit')
        for day, event in absent.items():
            if day not in self.document['sessions'] or not isinstance(event, dict) or set(event) != {'reason', 'source'}:
                raise ValueError(f'{symbol} {day}: invalid absence evidence')
            if event['reason'] not in {'not_listed', 'suspended'} or event['source'] not in self.document['sources']:
                raise ValueError(f'{symbol} {day}: unexplained absent session')
        dates = [str(day.date()) for day in frame.index]
        expected_dates = [day for day in self.document['sessions'] if day not in absent]
        if dates != expected_dates:
            missing = sorted(set(expected_dates) - set(dates))
            unexpected = sorted(set(dates) - set(expected_dates))
            raise ValueError(f'{symbol}: calendar differs; missing={missing[:5]}, unexpected={unexpected[:5]}')
        if frame[['volume', 'amount']].isna().any().any():
            raise ValueError(f'{symbol}: missing volume/amount cannot become zero')

    def augment(self, symbol: str, frame: pd.DataFrame, path: Path) -> pd.DataFrame:
        """Validate exact calendar gaps, then attach only causal signal prices."""
        expected = self.document['files'].get(symbol)
        if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'{symbol}: raw price file identity differs')
        self._validate_calendar_coverage(symbol, frame)
        if symbol in {'sh000300', 'sh000682'}:
            return frame
        if 'reported_change' not in frame:
            raise ValueError(f'{symbol}: raw exchange reference change is missing')
        events = [{'ex_date': a.ex_date, 'disclosed': a.disclosed_date,
                   'cash_adjustment_per_share': a.cash_adjustment_per_share,
                   'share_change_ratio': a.reference_share_ratio}
                  for a in self.actions if a.symbol == symbol]
        rows = [{str(key): value for key, value in row.items()}
                for row in frame.reset_index().to_dict('records')]
        for row in rows:
            row['date'] = str(row['date'].date())
        result = pd.DataFrame(linked_prices(rows, events, as_of=self.end)).set_index('date')
        result.index = pd.DatetimeIndex(result.index, name='date')
        references = frame['close'] - frame['reported_change']
        if (references <= 0).any() or references.isna().any():
            raise ValueError(f'{symbol}: raw previous reference prices are invalid')
        if ((result['reference_close'].iloc[1:] - references.iloc[1:]).abs() > .011).any():
            raise ValueError(f'{symbol}: unreviewed exchange reference discontinuity')
        result['reference_close'] = references
        # Quote volume is expressed in actual shares, independent of any
        # differentiated reference ratio used by the exchange price formula.
        share_scale = 1.0
        for day in result.index:
            for action in self.actions:
                if action.symbol == symbol and action.ex_date == str(day.date()):
                    share_scale *= 1 + action.share_ratio
            result.loc[day, 'share_scale'] = share_scale
        return result
