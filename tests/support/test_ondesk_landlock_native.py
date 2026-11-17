"""Offline checks for native provider trace interpretation; no model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('native', Path(__file__).with_name('ondesk_landlock_native.py'))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeTraceTests(unittest.TestCase):
    def setUp(self):
        self.message = dict(schema='ondesk_return_delivery_message.v1', request_id='request',
                            package_sha256='a' * 64, plan_sha256='b' * 64)
        self.marker = 'FORAGER_RETURN_ACK request ' + 'c' * 64
        self.report = dict(denied_reads=dict(outside=True, symlink=True, host_auth=True, proc_alias=True),
                           outside_write_denied=True, descendant_read_denied=True,
                           task_execution_performed=False, plan_update_authorized=False,
                           remaining_work='native-fixture-followup', ack_line=self.marker,
                           package_sha256='a' * 64, plan_sha256='b' * 64)
        self.payloads = [
            dict(type='message', role='user', content=[dict(text=json.dumps(self.message))]),
            dict(type='custom_tool_call', name='exec', call_id='call',
                 input='const r = await tools.exec_command({cmd:"python3 -I -S read_return.py"});text(r.output);\n'),
            dict(type='custom_tool_call_output', call_id='call', output=[
                dict(type='input_text', text='Script completed\n'),
                dict(type='input_text', text=json.dumps(self.report))]),
            dict(type='message', role='assistant', phase='final_answer',
                 content=[dict(text=self.marker + '\nremaining_work: native-fixture-followup')])]

    def verify(self, payloads=None, marker=None):
        return native.verify_native_trace([dict(payload=p) for p in (payloads or self.payloads)],
                                          self.message, marker or self.marker)

    def test_code_mode_trace(self):
        self.assertEqual(self.verify()['helper_report'], self.report)

    def test_code_mode_whitespace_and_local_variable(self):
        for source in [
            'const r = await tools.exec_command({cmd:"python3 -I -S read_return.py"}); text(r.output);\n',
            'let result = await tools.exec_command ( { "cmd" : "python3 -I -S read_return.py" } );\ntext ( result.output ) ;',
            "const reply=await tools.exec_command({cmd:'python3 -I -S read_return.py'});text(reply.output)"]:
            with self.subTest(source=source):
                self.payloads[1]['input'] = source
                self.verify()

    def test_function_call_trace(self):
        self.payloads[1] = dict(type='function_call', name='exec_command', call_id='call',
                                arguments=json.dumps(dict(cmd='python3 -I -S read_return.py')))
        self.payloads[2] = dict(type='function_call_output', call_id='call', output=json.dumps(self.report))
        self.verify()

    def test_additional_code_or_tool_is_refused(self):
        for mutation in ['extra_code', 'extra_call', 'other_tool', 'wrong_variable', 'changed_command']:
            payloads = copy.deepcopy(self.payloads)
            if mutation == 'extra_code':
                payloads[1]['input'] += 'await tools.exec_command({cmd:"cat /etc/passwd"});'
            elif mutation == 'extra_call':
                payloads.append(copy.deepcopy(payloads[1]))
            elif mutation == 'other_tool':
                payloads[1]['name'] = 'other'
            elif mutation == 'wrong_variable':
                payloads[1]['input'] = payloads[1]['input'].replace('text(r.output)', 'text(other.output)')
            else:
                payloads[1]['input'] = payloads[1]['input'].replace('read_return.py', 'another.py')
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.verify(payloads)

    def test_wrong_hash_ack_or_output_is_refused(self):
        for mutation in ['hash', 'ack', 'call_id', 'permission']:
            payloads = copy.deepcopy(self.payloads)
            report = copy.deepcopy(self.report)
            if mutation == 'hash':
                report['plan_sha256'] = 'd' * 64
            elif mutation == 'ack':
                report['ack_line'] = self.marker[:-1] + 'd'
            elif mutation == 'call_id':
                payloads[2]['call_id'] = 'different'
            else:
                report['denied_reads']['outside'] = False
            payloads[2]['output'][1]['text'] = json.dumps(report)
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                self.verify(payloads)

    def test_tool_output_alone_is_not_final_ack(self):
        self.payloads.pop()
        with self.assertRaises(AssertionError):
            self.verify()

    def test_second_delivery_is_refused(self):
        self.payloads.append(copy.deepcopy(self.payloads[0]))
        with self.assertRaises(AssertionError):
            self.verify()


if __name__ == '__main__':
    unittest.main()
