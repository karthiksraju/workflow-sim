"""Installed example inventory, shared by documentation and validation tooling."""
EXAMPLES = {
    'celery_retry': 'Celery retry preserves the serialized order and older deliveries',
    'billing': 'Lost receipt acknowledgement and duplicate payment webhook',
    'fulfillment': 'Payment decline compensates only its own stock reservation',
    'ingestion': 'Import restart after writes but before checkpoint',
    'documents': 'Chunk retry and complete-content assembly before publication',
    'monitoring': 'Recovery invalidates a stale delayed alert',
    'meetings': 'Rescheduling fences a late recording result',
}
DURATION = 12
