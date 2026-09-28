"""Independent models and stock asyncio challenge simulator execution semantics.

The asyncio reference uses the same workload and delays, with real event loops and
threads. The expected content is computed independently here. Neither execution's
trace order is treated as an oracle for unconstrained concurrent operations.
"""
import asyncio
import importlib.util
from pathlib import Path

from hypothesis import given, settings, strategies as st
from workflow_sim import run

ADAPTERS = Path(__file__).parent / 'adapters'
spec = importlib.util.spec_from_file_location('native_generated_program', ADAPTERS / 'generated_program.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

work = st.fixed_dictionaries({'delay_ms': st.integers(0, 4), 'failures': st.integers(0, 2),
                             'value': st.integers(-100, 100), 'pool': st.booleans()})


@settings(max_examples=35, deadline=None, derandomize=True)
@given(st.lists(work, min_size=1, max_size=5))
def test_generated_program_matches_stock_asyncio_and_independent_content(specs):
    expected = {'values': [s['value'] * 2 for s in specs],
                'attempts': [s['failures'] + 1 for s in specs], 'effects': []}
    real = asyncio.run(native.execute(specs))
    simulated = run('generated_program:build', inputs={'specs': specs, 'expected': expected},
                    duration=1, project_dir=ADAPTERS)
    assert real == expected
    assert simulated['outcome'] == 'PASS', simulated
    assert simulated['evidence']['checks'][0]['actual'] == real
    assert not simulated['evidence']['report']['in_flight']


child = st.fixed_dictionaries({'delay': st.integers(0, 8), 'error': st.booleans(), 'cancel': st.booleans()})


@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.lists(child, min_size=1, max_size=5), st.integers(1, 6))
def test_generated_children_cannot_hide_pending_work_or_errors(children, horizon):
    active = [(i, s) for i, s in enumerate(children) if not s['cancel']]
    expected = [i for i, s in active if s['delay'] <= horizon]
    pending = any(s['delay'] > horizon for _, s in active)
    errors = [i for i, s in active if s['delay'] <= horizon and s['error']]
    outcome = 'INCOMPLETE' if pending else 'HARNESS_ERROR' if errors else 'PASS'
    result = run('generated_program:lifecycle', inputs={'children': children, 'expected': expected},
                 duration=horizon, project_dir=ADAPTERS)
    assert result['outcome'] == outcome, result
    assert result['evidence']['checks'][0]['actual'] == expected
    if pending:
        assert result['evidence']['report']['in_flight']
    if errors:
        failures = str(result['evidence']['report']['callback_failures'])
        assert all(f'child-error-{index}' in failures for index in errors)
