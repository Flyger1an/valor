import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[2]/'infra/trading/dashboard_mirror.py'
spec=importlib.util.spec_from_file_location('dashboard_mirror',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DashboardMirrorTests(unittest.TestCase):
    def setUp(self):
        self.epoch=time.time()-100
        self.bundle={'runtime':{'mode':'demo','policy_hash':'policy'},
            'experiment':{'mode':'virtual_only','policy_hash':'policy','market_source':'alpaca',
                'identity_hash':'a'*64,'epoch':self.epoch,'evaluation_end':self.epoch+90*86400,
                'timestamp':self.epoch+90,'frames':18,'marks_fresh':False}}

    def test_stale_quotes_stay_stale_and_provider_clock_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'connection.json';target=root/'mirror'
            config.write_text(json.dumps({'host':'example.invalid','known_hosts_path':'known','policy_hash':'policy'}))
            with patch.object(module.subprocess,'run') as run:
                run.return_value.returncode=0;run.return_value.stdout=json.dumps(self.bundle)
                result=module.mirror(config,root/'existing-key',target)
            self.assertEqual(json.loads((target/'experiment.json').read_text()),self.bundle['experiment'])
            self.assertGreater(result['received_at'],result['experiment_timestamp'])
            self.assertEqual((target/'experiment.json').stat().st_mode & 0o777,0o600)
            self.assertIn('StrictHostKeyChecking=yes',run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['input'],module.REMOTE_READ)

    def test_source_policy_and_identity_changes_are_rejected(self):
        anchor=module.validate(self.bundle,'policy')
        for key,value in [('market_source','test_fixture'),('mode','live'),('identity_hash','b'*64),('policy_hash','other')]:
            bad=copy.deepcopy(self.bundle);bad['experiment'][key]=value
            with self.assertRaises(ValueError): module.validate(bad,'policy',anchor)

    def test_future_or_changed_study_clocks_are_rejected(self):
        for key,value in [('timestamp',time.time()+60),('evaluation_end',self.epoch+91*86400),('epoch',float('nan'))]:
            bad=copy.deepcopy(self.bundle);bad['experiment'][key]=value
            with self.assertRaises(ValueError): module.validate(bad,'policy')

    def test_transport_failure_keeps_the_last_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'connection.json';target=root/'mirror';target.mkdir()
            config.write_text(json.dumps({'host':'example.invalid','known_hosts_path':'known','policy_hash':'policy'}))
            original=json.dumps(self.bundle['experiment']);(target/'experiment.json').write_text(original)
            with patch.object(module.subprocess,'run') as run:
                run.return_value.returncode=255
                with self.assertRaises(RuntimeError): module.mirror(config,root/'existing-key',target)
            self.assertEqual((target/'experiment.json').read_text(),original)


if __name__=='__main__': unittest.main()
