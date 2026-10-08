"""Strict publication admission; rejected provider observations remain immutable."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import Quote, decimal as D, encode
from .engine import write_snapshot
from .history import retain
from .ipc import read_object

MAX_BATCH_BYTES = 100_000
MAX_REJECTION_BYTES = 250_000
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def violations(batch, previous, symbols):
    """No clock tolerance, retiming, quote omission, or stale-price refresh."""
    errors = []
    if previous and any(batch.get(k) != previous.get(k) for k in ('source', 'venue', 'policy_hash')):
        errors.append({'reason': 'quote_provenance_changed'})
    receipt = float(D(batch['timestamp']))
    if receipt <= 0:
        raise ValueError('invalid quote receipt clock')
    if previous and receipt < float(D(previous['timestamp'])):
        errors.append({'reason': 'rewound_receipt_clock', 'received_at': receipt,
                       'prior_received_at': previous['timestamp']})
    watermarks = previous.get('quote_watermarks', previous.get('quotes', {}))
    for symbol, raw in batch['quotes'].items():
        if symbol not in symbols:
            errors.append({'symbol': symbol, 'reason': 'unexpected_quote_instrument'})
            continue
        try:
            if raw.get('instrument', symbol) != symbol:
                raise ValueError('quote instrument mismatch')
            q = Quote(symbol, raw['bid'], raw['ask'], raw['timestamp'])
            prior = watermarks.get(symbol)
            if prior:
                Quote(symbol, prior['bid'], prior['ask'], prior['timestamp'])
            reason = ('future_quote' if q.timestamp > receipt else
                      'rewound_quote' if prior and q.timestamp < prior['timestamp'] else
                      'same_timestamp_quote_revision' if prior and q.timestamp == prior['timestamp']
                      and any(D(raw[k]) != D(prior[k]) for k in ('bid', 'ask')) else None)
            if reason:
                errors.append({'symbol': symbol, 'reason': reason, 'provider_at': q.timestamp,
                               'prior_provider_at': prior['timestamp'] if prior else None,
                               'received_at': receipt})
        except (ValueError, KeyError, TypeError, ArithmeticError):
            errors.append({'symbol': symbol, 'reason': 'invalid_quote'})
    return errors


def publish(target, batch, symbols):
    """The last accepted file is also the durable per-asset watermark across restarts."""
    target = Path(target)
    encoded = encode(batch)
    if len(encoded.encode()) > MAX_BATCH_BYTES:
        raise ValueError('quote batch exceeds size limit')
    batch = json.loads(encoded)
    previous = read_object(target / 'quotes.json', {}, limit=MAX_BATCH_BYTES)
    errors = violations(batch, previous, symbols)
    if errors:
        record = {'kind': 'rejected_quote_batch_v1', 'batch': batch, 'previous': previous, 'violations': errors}
        archive = target / 'quote-rejections'
        size = len(encode({'identity': record}).encode())
        if size > MAX_REJECTION_BYTES or sum(p.stat().st_size for p in archive.glob('*.json')) + size > MAX_ARCHIVE_BYTES:
            raise ValueError('quote rejection archive capacity exhausted; publication remains blocked')
        evidence_hash, _ = retain(archive, record, {})
        raise ValueError('quote_batch_rejected:'+evidence_hash)
    # Keep watermarks for absent assets; their later reappearance must not bypass the guard.
    batch['quote_watermarks'] = {**previous.get('quote_watermarks', previous.get('quotes', {})), **batch['quotes']}
    if len(encode(batch).encode()) > MAX_BATCH_BYTES:
        raise ValueError('quote watermark exceeds size limit')
    write_snapshot(target / 'quotes.json', batch)
