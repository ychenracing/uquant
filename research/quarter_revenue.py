"""Standalone-quarter fields from fixed original reports, with no return inputs."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

from research.revenue_reports import number


def original_prior(row: dict[str, Any]) -> float | None:
    """Use only explicit amounts independently checked in the same original."""
    if row.get('prior_revenue_reported') is not None:
        return row['prior_revenue_reported']
    values = {item['prior'] for item in row.get('independent_income_arithmetic', [])
              if math.isclose(item['current'], row['cumulative_revenue_reported'], abs_tol=.01)
              and item['prior'] > 0}
    return values.pop() if len(values) == 1 else None


def quarter_panel(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep earliest original versions; never overwrite them with restatements."""
    originals = {}
    for row in sorted(reports, key=lambda r: (r['disclosed_date'] or '9999', r['source_url'])):
        if row['status'] == 'verified_numeric_original':
            originals.setdefault((row['symbol'], row['period']), row)
    output = []
    for (symbol, period), row in sorted(originals.items()):
        kind, year = period[-2:], period[:4]
        cells = (row.get('revenue_rows') or [[]])[0]
        growth, method, sources = None, 'unavailable', [row]
        if kind == 'Q1':
            growth, method = row['cumulative_revenue_yoy_percent'], 'original_Q1'
        elif kind == 'Q3' and len(cells) == 5:
            growth, method = number(cells[2]), 'original_Q3'
        elif kind == 'Q3' and len(cells) == 9:
            growth, method = number(cells[4]), 'original_Q3_adjusted_comparison_as_disclosed'
        else:
            previous = originals.get((symbol, year + {'H1': 'Q1', 'FY': 'Q3'}.get(kind, 'missing')))
            if previous is not None and row.get('revenue_unit') == previous.get('revenue_unit') == 'CNY':
                current_prior = original_prior(row)
                previous_prior = original_prior(previous)
                if current_prior is not None and previous_prior is not None:
                    numerator = row['cumulative_revenue_reported'] - previous['cumulative_revenue_reported']
                    denominator = current_prior - previous_prior
                    if numerator >= 0 and denominator > 0:
                        growth = (numerator / denominator - 1) * 100
                        method, sources = 'original_cumulative_difference', [row, previous]
        output.append({
            'symbol': symbol, 'period': year + {'H1': 'Q2', 'FY': 'Q4'}.get(kind, kind),
            'period_index': row['period_index'],
            'disclosed_date': max(r['disclosed_date'] for r in sources),
            'quarter_yoy_percent': growth, 'method': method,
            'source_sha256': [r['sha256'] for r in sources],
            'source_urls': [r['source_url'] for r in sources],
            'units': 'percent',
        })
    return output


def available_quarters(panel: list[dict[str, Any]], date: str) -> dict[str, dict[str, Any]]:
    """Return latest disclosed period; missing latest values remain unavailable."""
    grouped = defaultdict(list)
    for row in panel:
        if row['disclosed_date'] < date:
            grouped[row['symbol']].append(row)
    return {symbol: max(rows, key=lambda r: r['period_index']) for symbol, rows in grouped.items()}
