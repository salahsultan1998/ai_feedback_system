import copy
import json
import os
import time
from io import BytesIO
from unittest.mock import patch

from app import create_app
from feedback import bug_review
from feedback.processing import fingerprint
from feedback.storage import atomic_csv
from feedback.workflow import run_report
from tests.helpers import BaseTest, fake_request, workspace_client


def bug_model(prompt, payload, config, validate, should_stop=None):
    if payload.get('task') == 'bugs':
        if payload['stage'] == '提取':
            ids = list(dict.fromkeys(item['feedback_id'] for item in payload['items']))
            return validate({'bugs': [{'text': '玩家反映对局中闪退。', 'feedback_ids': ids}]})
        return validate({'bugs': [{'text': '玩家反映对局中闪退。', 'candidate_ids': [item['candidate_id'] for item in payload['items']]}]})
    return fake_request(prompt, payload, config, validate, should_stop)


class BugWorkflowTests(BaseTest):
    def test_bug_analysis_does_not_change_existing_translation_or_module_statistics(self):
        self.raw.loc[self.raw['反馈ID'].isin(['004', '005']), '反馈标签'] = 'BUG'
        atomic_csv(self.root / 'rows.csv', self.raw)
        before_config = copy.deepcopy(self.config)
        before_config.pop('bugs_prompt', None)
        with patch('feedback.ai_client.request_json', side_effect=bug_model) as model:
            before = run_report(self.root, before_config, '2026-10-19', '2026-10-22', False, 'before', lambda **kw: None)
            after = run_report(self.root, self.config, '2026-10-19', '2026-10-22', False, 'after', lambda **kw: None)
            self.assertEqual(sum('feedbacks' in call.args[1] for call in model.call_args_list), 1)
            calls = model.call_count
            run_report(self.root, self.config, '2026-10-19', '2026-10-22', False, 'again', lambda **kw: None)
            self.assertEqual(model.call_count, calls, 'Retry should reuse BUG analysis and classification caches')
        old_md = (self.root / 'reports/before/report.md').read_text()
        new_md = (self.root / 'reports/after/report.md').read_text()
        self.assertEqual((before['total'], before['previous_total']), (after['total'], after['previous_total']))
        self.assertEqual(old_md.split('## 反馈统计')[1], new_md.split('## 反馈统计')[1])
        self.assertEqual(old_md.split('## 有价值的玩家建议')[0], new_md.split('## 有价值的玩家建议')[0])
        self.assertIn(bug_review.HEADING, new_md)
        self.assertIn('1 个疑似问题，涉及 2 条反馈', new_md)
        self.assertIn('004、005', new_md)
        self.assertNotIn('002', new_md.split(bug_review.HEADING)[1].split('## 反馈统计')[0])
        snapshot = json.loads((self.root / 'reports/after/review.json').read_text())
        self.assertEqual(set(snapshot['sources']), {'004', '005'})
        self.assertEqual(fingerprint(self.raw.iloc[4], before_config), fingerprint(self.raw.iloc[4], self.config))


class BugReviewWebTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.raw.loc[self.raw['反馈ID'].isin(['004', '006']), '反馈标签'] = 'BUG'
        self.raw.loc[self.raw['反馈ID'].eq('005'), '反馈标签'] = '建议'
        self.raw.loc[self.raw['反馈ID'].eq('006'), '反馈时间'] = '2026-10-20'
        self.app = create_app({'TESTING': True, 'STORAGE_ROOT': self.root / 'web'})
        self.client = workspace_client(self.app)
        self.client.get('/')
        with self.client.session_transaction() as session:
            self.headers = {'X-CSRF-Token': session['csrf']}
        response = self.client.post('/api/games/baloot/uploads', headers=self.headers,
            data={'file': (BytesIO(self.raw.to_csv(index=False).encode()), 'bugs.csv')})
        self.base = '/api/games/baloot/uploads/' + response.get_json()['run']
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only'}), patch('feedback.ai_client.request_json', side_effect=bug_model):
            response = self.client.post(self.base + '/reports', headers=self.headers,
                json={'start': '2026-10-19', 'end': '2026-10-22'})
            self.job = self.base + '/jobs/' + response.get_json()['id']
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = self.client.get(self.job).get_json()
                if status['status'] not in {'running', 'stopping'}:
                    break
                time.sleep(.01)
            self.assertEqual(status['status'], 'complete', status)

    def payload(self):
        detail = self.client.get(self.job + '/bugs').get_json()
        return {'version': detail['version'], 'bugs': [
            {key: item[key] for key in ('id', 'text', 'included', 'feedback_ids')}
            for item in detail['bugs']]}

    def test_editing_bug_evidence_updates_report_counts_download_push_and_preserves_stats(self):
        before = self.client.get(self.job + '/report').get_json()
        self.assertTrue(before['bug_review']['editable'])
        before_stats = before['html'].split('<table>')[1]
        payload = self.payload()
        self.assertEqual(payload['bugs'][0]['feedback_ids'], ['004', '006'])
        payload['bugs'][0]['text'] = '玩家反映对局时游戏意外退出。'
        payload['bugs'][0]['feedback_ids'] = ['004']
        with patch('feedback.ai_client.request_json') as model:
            response = self.client.post(self.job + '/bugs', json=payload, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            saved = response.get_json()
            self.assertIn('1 个疑似问题，涉及 1 条反馈', saved['html'])
            self.assertIn('相关反馈：1 条；反馈 ID：004', saved['html'])
            self.assertEqual(saved['html'].split('<table>')[1], before_stats)
            self.assertTrue(saved['bug_review']['modified'])
            # Suggestion edits preserve the reviewed BUG section and its source subset.
            detail = self.client.get(self.job + '/review').get_json()
            suggestions = [{'id': item['id'], 'text': '修正后的产品建议'} for item in detail['suggestions']]
            response = self.client.post(self.job + '/review', headers=self.headers,
                json={'version': detail['version'], 'suggestions': suggestions})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertIn('相关反馈：1 条；反馈 ID：004', response.get_json()['html'])
            md = self.client.get(self.job + '/download/markdown')
            self.assertIn('玩家反映对局时游戏意外退出。', md.data.decode())
            md.close()
            version = response.get_json()['bug_review']['version']
            with patch('feedback.feishu.credentials', return_value=('https://example', '')), patch(
                    'feedback.feishu_sheets.sync', return_value=None), patch(
                    'feedback.feishu.send_payload', return_value={'code': 0}) as sender:
                result = self.client.post(self.job + '/push/feishu', headers=self.headers,
                    json={'report_version': version})
                self.assertEqual(result.status_code, 200, result.get_json())
                self.assertIn('相关反馈：1 条；反馈 ID：004', json.dumps(sender.call_args.args[1], ensure_ascii=False))
            model.assert_not_called()
        self.assertFalse(self.client.get(self.job + '/bugs').get_json()['editable'])

    def test_exclude_all_remains_recoverable_and_api_preserves_locking_and_version_checks(self):
        payload = self.payload()
        self.assertEqual(self.client.post(self.job + '/bugs', json=payload).status_code, 403)
        with self.app.extensions['feishu_lock']:
            self.assertEqual(self.client.post(self.job + '/bugs', json=payload, headers=self.headers).status_code, 409)
        payload['bugs'][0]['included'] = False
        response = self.client.post(self.job + '/bugs', json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn(bug_review.EMPTY_TEXT, response.get_json()['html'])
        self.assertTrue(response.get_json()['bug_review']['editable'])
        excluded_version = response.get_json()['bug_review']['version']
        self.assertEqual(self.client.post(self.job + '/bugs', json=payload, headers=self.headers).status_code, 409)
        restore = self.payload()
        restore['bugs'][0]['included'] = True
        response = self.client.post(self.job + '/bugs', json=restore, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn('1 个疑似问题，涉及 2 条反馈', response.get_json()['html'])
        with patch('web.api.feishu.push') as sender:
            response = self.client.post(self.job + '/push/feishu', headers=self.headers, json={'report_version': excluded_version})
            self.assertEqual(response.status_code, 409)
            sender.assert_not_called()
