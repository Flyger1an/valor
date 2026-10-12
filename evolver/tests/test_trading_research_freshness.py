"""Research warmup must not relabel an old evaluation as a current one."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_trading_runtime import NOW, policy
from evolver.trading.engine import write_snapshot
from evolver.trading.history import digest
from evolver.trading.learning import evaluate
from evolver.trading.runtime import research_tick
from evolver.trading.strategies import CATALOG


class ResearchFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy(approved_strategies=tuple(s.version for s in CATALOG))
        self.incumbent, self.challenger = CATALOG[0].version, CATALOG[1].version
        self.bars = [dict(timestamp=NOW-300*(80-i), open='100', close='100',
                          low='99', high='101', volume='1') for i in range(80)]
        self.saved = {'status': 'collecting_forward_evidence', 'cutoff': NOW-86400,
                      'challenger': self.challenger, 'incumbent': self.incumbent,
                      'end': NOW-86700, 'candidate': {'trades': 0}, 'baseline': {'trades': 0},
                      'evidence_hash': 'previous-evaluation', 'variants_tested': 9,
                      'history_errors': {'BTC-USD': 'history_recovery_warmup'},
                      'history_binding': {'end': NOW-86700, 'hashes': {'BTC-USD': 'old'}}}

    def test_warmup_discards_stale_results_but_preserves_frozen_selection(self):
        with patch('evolver.trading.learning.backtest') as backtest:
            result = evaluate(self.policy, {'BTC-USD': self.bars}, self.saved, self.incumbent)
        backtest.assert_not_called()
        self.assertEqual(result['status'], 'collecting_data')
        self.assertEqual(result['cutoff'], self.saved['cutoff'])
        self.assertEqual(result['challenger'], self.challenger)
        self.assertEqual(result['end'], self.bars[-1]['timestamp'])
        self.assertEqual(result['bars'], 80)
        self.assertEqual(result['required_bars'], 600)
        self.assertFalse(result['forward_evaluation_performed'])
        for key in ('candidate', 'baseline', 'evidence_hash', 'history_errors', 'history_binding', 'variants_tested'):
            self.assertNotIn(key, result)
        self.assertEqual(self.saved['candidate'], {'trades': 0})

    def test_empty_warmup_does_not_claim_an_evaluation_end(self):
        result = evaluate(self.policy, {}, self.saved, self.incumbent)
        self.assertIsNone(result['end'])
        self.assertEqual(result['bars'], 0)
        self.assertEqual(result['cutoff'], self.saved['cutoff'])
        self.assertNotIn('candidate', result)

    def test_research_binds_available_history_without_inheriting_old_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = {name: Path(temp)/name for name in ('market', 'research', 'outbox')}
            saved = {**self.saved, 'policy_hash': self.policy.fingerprint, 'history_context': {}}
            write_snapshot(paths['research']/'assessment.json', saved)
            write_snapshot(paths['market']/'history.json', {
                'source': 'coinbase_public', 'venue': 'coinbase', 'timestamp': NOW,
                'policy_hash': self.policy.fingerprint, 'histories': {'BTC-USD': self.bars}})
            research_tick(self.policy, paths, NOW)
            result = json.loads((paths['research']/'assessment.json').read_text())
        self.assertEqual(result['evaluated_at'], NOW)
        self.assertEqual(result['history_binding']['end'], self.bars[-1]['timestamp'])
        self.assertEqual(result['history_binding']['hashes']['BTC-USD'], digest(self.bars))
        self.assertEqual(result['cutoff'], saved['cutoff'])
        self.assertNotIn('candidate', result)
        self.assertNotIn('history_errors', result)

    def test_recovered_sample_keeps_original_forward_cutoff_and_challenger(self):
        warmup = evaluate(self.policy, {'BTC-USD': self.bars}, self.saved, self.incumbent)
        complete = [dict(self.bars[0], timestamp=NOW-300*(650-i)) for i in range(650)]
        with patch('evolver.trading.learning.backtest', return_value=[]) as backtest:
            result = evaluate(self.policy, {'BTC-USD': complete}, warmup, self.incumbent)
        self.assertEqual(result['status'], 'collecting_forward_evidence')
        self.assertEqual(result['challenger'], self.challenger)
        self.assertEqual(result['cutoff'], self.saved['cutoff'])
        self.assertEqual(result['end'], complete[-1]['timestamp'])
        self.assertEqual(backtest.call_count, 2)
        self.assertTrue(all(call.kwargs == {'start': self.saved['cutoff']} for call in backtest.call_args_list))


if __name__ == '__main__':
    unittest.main()
