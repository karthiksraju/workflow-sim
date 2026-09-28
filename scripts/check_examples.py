"""Run the installed domain gallery and retain fixed/broken evidence."""
import argparse
import json
from pathlib import Path
from workflow_sim import run
from workflow_sim.examples.catalog import EXAMPLES, DURATION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name in EXAMPLES:
        for broken in (False, True):
            result = run(f'workflow_sim.examples.{name}:build', inputs={'broken': broken}, duration=DURATION)
            wanted = 'ASSERTION_FAILED' if broken else 'PASS'
            path = args.output / f'{name}-{"broken" if broken else "fixed"}.json'
            path.write_text(json.dumps(result, indent=2) + '\n')
            assert result['outcome'] == wanted, (name, wanted, result)
            checks = result['evidence']['checks']
            failed = [c['name'] for c in checks if c['actual'] != c['expected']]
            assert bool(failed) == broken and len(checks) >= 2
            assert not result['evidence']['unsupported'] and not result['evidence']['violations']
            records.append({'example': name, 'broken': broken, 'outcome': result['outcome'],
                            'failed_checks': failed, 'evidence_sha256': result['evidence_sha256'],
                            'file': path.name})
            print(f'{name}: {result["outcome"]} ({"broken" if broken else "fixed"})')
    (args.output / 'summary.json').write_text(json.dumps({'cases': records}, indent=2) + '\n')


if __name__ == '__main__':
    main()
