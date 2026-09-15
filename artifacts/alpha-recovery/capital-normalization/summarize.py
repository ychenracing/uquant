"""Small audit receipts; raw replays stay local under the no-large-upload rule."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def summarize(root, control, passive):
    output = {}
    for name in ('a', 'e', 'e-h2', 'remove308', 'remove502'):
        path = root / (name + '.json.gz')
        if not path.exists():
            output[name] = {'status': 'NOT_AVAILABLE'}
            continue
        raw, baseline = read(path), read(control / (name + '.json.gz'))
        native = name.startswith('remove')
        if native:
            assert raw['status'] == baseline['status'] == 'COMPLETE'
            assert not raw['replay_error'] and not baseline['replay_error']
            for key in ('runtime', 'scenario', 'runner_sha256'):
                assert raw[key] == baseline[key], (name, key)
            for key in baseline['identities']:
                if key not in ('head', 'tree', 'production_source_sha256'):
                    assert raw['identities'][key] == baseline['identities'][key], (name, key)
            rows, control_rows = raw['rows'], baseline['rows']
            fills = [f['payload'] for f in raw['fills']]
            producer, runtime = raw['source'], raw['runtime']
            date_key = 'session'
        else:
            assert raw['completed'] and baseline['completed']
            for key in ('environment', 'config', 'interval', 'seed', 'symbols', 'adapter_sha256', 'runner_sha256'):
                assert raw[key] == baseline[key], (name, key)
            changed = sorted(k for k in raw['source_files'] | baseline['source_files']
                             if raw['source_files'].get(k) != baseline['source_files'].get(k))
            assert all(k.startswith('uquant/') for k in changed), changed
            rows, control_rows = raw['trace'], baseline['trace']
            fills = [f for row in rows for f in row['new_fills']]
            producer = {'head': raw['commit'], 'changed_source_files_vs_control': changed}
            runtime, date_key = raw['environment'], 'date'
        assert [r[date_key] for r in rows] == [r[date_key] for r in control_rows]
        assert len({r[date_key] for r in rows}) == len(rows)
        peak, drawdown = 2_000_000, 0.
        for row in rows:
            peak = max(peak, row['equity'])
            drawdown = max(drawdown, 1-row['equity']/peak)
        metrics = raw['metrics']
        assert math.isclose(metrics['final_wealth'], rows[-1]['equity']/2_000_000, rel_tol=1e-10)
        assert math.isclose(metrics['max_drawdown'], drawdown, abs_tol=1e-10)
        for fill in fills:
            assert fill['signal_date'] < fill['fill_date'] and fill['shares'] > 0
            if native:
                assert fill['symbol'] != raw['scenario']['removed_symbol']
        fees = sum(sum(float(f.get(k, 0)) for k in ('commission', 'stamp_duty', 'transfer_fee')) for f in fills)
        slippage = sum(float(f.get('slippage_cost', 0)) for f in fills)
        m = {k: metrics[k] for k in ('final_wealth','max_drawdown','account_orders','annual_turnover','gross_turnover','actual_strategic_epoch_count','distinct_owner_count') if k in metrics}
        m.update(fees=fees, slippage=slippage)
        item = dict(status='COMPLETE_MATCHED_DIAGNOSTIC', producer=producer, runtime=runtime,
                    sessions=len(rows), metrics=m, control_wealth=baseline['metrics']['final_wealth'],
                    wealth_ratio=metrics['final_wealth']/baseline['metrics']['final_wealth'],
                    local_path=str(path.resolve()), bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), raw_remote_saved=False)
        if native:
            b = passive['results'][raw['scenario']['removed_symbol']]
            item.update(passive_wealth=b['final_wealth'], passive_drawdown=b['max_drawdown'],
                        passive_wealth_ratio=metrics['final_wealth']/b['final_wealth'],
                        minimum_benchmark_wealth=.9*b['final_wealth'],
                        benchmark_gap=max(0,.9*b['final_wealth']-metrics['final_wealth']))
        output[name] = item
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--passive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), 'Preserve previous receipts'
    result = summarize(args.root, args.control, json.loads(args.passive.read_text()))
    payload = dict(status='DIAGNOSTIC_NOT_FINAL_ACCEPTANCE', results=result,
                   verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2)+'\n')
    print(json.dumps({name:r.get('metrics',r) for name,r in result.items()}))
