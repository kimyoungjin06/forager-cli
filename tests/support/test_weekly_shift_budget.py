"""Offline admission parity; no provider, Telegram, or real profile calls."""
import copy
import subprocess
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import offdesk_usage_guard as guard
import offdesk_remote_operator_telegram as operator


class WeeklyScope(unittest.TestCase):
    def input(self):
        now=1000
        sample=guard.normalize('gpt','fixture-account',{'week':(20,10000)},now)
        state=guard.initialize({'gpt':sample},9000,now,10,300)
        return {'state':state,'contract':{k:copy.deepcopy(state[k]) for k in ('schema','deadline','baselines','limit_percentage_points')},'samples':{'gpt':copy.deepcopy(sample)},'now':now,'max_age':300}

    def test_pure_evaluation_reuses_latching_guard_and_accounts_for_other_sessions(self):
        v=self.input();v['samples']['gpt']['weekly_meters']['week']['used_percent']=30
        result=guard.evaluate_bound(v)
        self.assertFalse(result['verdict']['allowed'])
        self.assertTrue(result['state']['held'])
        self.assertTrue(result['verdict']['account_usage_includes_other_sessions'])
        self.assertTrue(result['verdict']['inflight_call_can_overshoot'])

    def test_malformed_empty_and_changed_contracts_do_not_silently_disable_caps(self):
        for mutate in (lambda v:v['state'].update(baselines={}),
                       lambda v:v['state'].update(held='false'),
                       lambda v:v['state'].update(high_water={}),
                       lambda v:v['state'].update(limit_percentage_points=11),
                       lambda v:v.update(max_age=301)):
            v=self.input();mutate(v)
            with self.assertRaises((ValueError,KeyError,TypeError)):guard.evaluate_bound(v)

    def test_subprocess_evaluation_does_not_reuse_a_stale_caller_clock(self):
        value = self.input()
        process = subprocess.run([sys.executable, '-I', '-S', str(Path(guard.__file__)), 'evaluate-stdin'],
                                 input=json.dumps(value), text=True, capture_output=True, check=True)
        result = json.loads(process.stdout)
        self.assertFalse(result['verdict']['allowed'])
        self.assertIn('deadline_reached', result['verdict']['reasons'])
        self.assertTrue(result['state']['held'])

    def test_telegram_confirmation_rejects_same_bound_weekly_hold_before_any_arm(self):
        status={'active_shift_id':None,'shifts':[{'shift_id':'fixture','manifest_sha256':'a'*64,'weekly_usage':{'runtime_weekly_limit':True,'allowed':False}}]}
        with patch.object(operator,'bounded_shift_status',return_value=status), patch.object(operator,'run_shift_forager_json') as cli:
            with self.assertRaisesRegex(operator.RemoteOperatorTelegramError,'weekly allowance is held'):
                operator.arm_confirmed_shift('unused','private','fixture','a'*64)
            cli.assert_not_called()

    def test_telegram_changed_manifest_is_not_an_approval_for_the_new_budget(self):
        status={'active_shift_id':None,'shifts':[{'shift_id':'fixture','manifest_sha256':'b'*64,'weekly_usage':{'runtime_weekly_limit':True,'allowed':True}}]}
        with patch.object(operator,'bounded_shift_status',return_value=status), patch.object(operator,'run_shift_forager_json') as cli:
            with self.assertRaisesRegex(operator.RemoteOperatorTelegramError,'no longer matches'):
                operator.arm_confirmed_shift('unused','private','fixture','a'*64)
            cli.assert_not_called()


if __name__=='__main__':unittest.main()
