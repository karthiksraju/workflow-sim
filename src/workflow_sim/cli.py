"""Small local CLI; never sends results to a service."""
import argparse
import json
from pathlib import Path
from . import __version__, run
from .contracts import encode


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run a trusted workflow adapter in an isolated process')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('adapter', help='module:function')
    parser.add_argument('--inputs', type=Path, help='JSON object file')
    parser.add_argument('--duration', type=float, default=60)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-steps', type=int, default=100000)
    parser.add_argument('--wall-timeout', type=float, default=30)
    parser.add_argument('--project-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output:
            args.output.unlink(missing_ok=True)
        result = run(args.adapter, inputs=json.loads(args.inputs.read_text()) if args.inputs else {},
                     duration=args.duration, seed=args.seed, max_steps=args.max_steps,
                     wall_timeout=args.wall_timeout, project_dir=args.project_dir)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_name(args.output.name + '.tmp')
            temporary.write_bytes(encode(result) + b'\n')
            temporary.replace(args.output)
        print(json.dumps(result, indent=2))
        return {'PASS': 0, 'ASSERTION_FAILED': 1, 'INCOMPLETE': 2, 'UNSUPPORTED': 3}.get(result['outcome'], 4)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(4, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
