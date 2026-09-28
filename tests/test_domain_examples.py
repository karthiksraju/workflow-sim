"""Public-runner checks: fixed business content and named broken controls."""
import pytest
from workflow_sim import run
from workflow_sim.examples.catalog import EXAMPLES, DURATION


@pytest.mark.parametrize('name', EXAMPLES)
def test_domain_example_has_real_content_and_a_detected_bug(name):
    good = run(f'workflow_sim.examples.{name}:build', duration=DURATION)
    bad = run(f'workflow_sim.examples.{name}:build', inputs={'broken': True}, duration=DURATION)
    assert good['outcome'] == 'PASS', good
    assert bad['outcome'] == 'ASSERTION_FAILED', bad
    checks = good['evidence']['checks']
    assert len(checks) >= 2 and all(c['actual'] == c['expected'] for c in checks)
    assert any(c['actual'] != c['expected'] for c in bad['evidence']['checks'])
    # The negative control fails business state, not an unrelated harness problem.
    assert not bad['evidence']['unsupported'] and not bad['evidence']['violations']


def test_fulfillment_success_preserves_other_reservation():
    result = run('workflow_sim.examples.fulfillment:build', inputs={'decline': False}, duration=12)
    assert result['outcome'] == 'PASS', result
    checks = {c['name']: c['actual'] for c in result['evidence']['checks']}
    assert checks['ship only paid orders'] == [{'order': 'order-42', 'quantity': 2}]
    assert checks['other reservation preserved'] == {'older-order': 2}
