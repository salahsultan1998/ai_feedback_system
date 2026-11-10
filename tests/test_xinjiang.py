"""Xinjiang AI candidates must survive the complete existing report workflow."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re
import unittest
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from app import create_app
from feedback.config import load_config
from feedback.feishu_sheets import detail_rows
from feedback.importer import read_feedback
from feedback.processing import batch_instructions, fingerprint, validate_result
from feedback.storage import atomic_csv, read_csv
from feedback.workflow import run_report


def ai_model(prompt, payload, config, validate, should_stop=None):
    if 'feedbacks' in payload:
        return validate({'results': [dict(feedback_id=item['feedback_id'],
            translation_zh='玩家反映排行页面无法打开，并建议增加重试按钮。',
            label='玩法相关', theme='社交与好友', subtheme='排行', is_valid=True,
            exclusion_reason='', is_suggestion=item['feedback_id'] == '004',
            is_bug=item['feedback_id'] in {'004', '005'}) for item in payload['feedbacks']]})
    if payload.get('task') == 'bugs':
        key = 'candidate_ids' if payload['stage'] == '合并' else 'feedback_ids'
        field = 'candidate_id' if key == 'candidate_ids' else 'feedback_id'
        return validate({'bugs': [{'text': '玩家反映排行页面无法打开，待核查。',
                                  key: [item[field] for item in payload['items']]}]})
    if 'label' in payload:
        return validate({'text': '玩家反映排行无法打开。',
                         'feedback_ids': [payload['items'][0]['feedback_id']]})
    return validate({'suggestions': [{'text': '在排行页面增加重试按钮。',
                                      'feedback_ids': [payload['items'][0]['feedback_id']]}]})


class XinjiangTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config('xinjiang')

    def source(self):
        return pd.DataFrame([{'反馈ID': f'00{i}', 'UID': '0009', '反馈内容': 'شەرەپ تاختىسى ئېچىلمايدۇ',
            '反馈日期': day, '充值金额': '196.98', '设备信息': 'device_name:test'} for i, day in
            enumerate(['2026-10-01', '2026-10-12', '2026-10-15', '2026-10-16',
                       '2026-10-19', '2026-10-22', '2026-10-23', '2026-10-31'])])

    def test_full_report_retains_ai_flags_exports_amount_and_reuses_results(self):
        source = self.root / 'source.xlsx'
        self.source().to_excel(source, index=False)
        raw = read_feedback(source, self.config)
        self.assertTrue(raw['反馈标签'].eq('').all())
        self.assertEqual(raw.iloc[0]['用户ID'], '0009')
        atomic_csv(self.root / 'rows.csv', raw)
        with patch('feedback.ai_client.request_json', side_effect=ai_model) as model:
            result = run_report(self.root, self.config, '2026-10-19', '2026-10-22', False, 'first', lambda **kw: None)
            count = model.call_count
            self.assertEqual(result['suggestions'], 1)
            self.assertEqual(result['suggestion_feedback_count'], 1)
            self.assertEqual((result['total'], result['previous_total']), (2, 2))
            run_report(self.root, self.config, '2026-10-19', '2026-10-22', False, 'retry', lambda **kw: None)
            self.assertEqual(model.call_count, count)
        saved = read_csv(self.root / 'processed.csv').set_index('反馈ID')
        self.assertEqual(saved.loc['004', '是否建议'], '是')
        self.assertEqual(saved.loc['005', '是否建议'], '否')
        self.assertEqual(saved.loc['005', '是否疑似BUG'], '是')
        folder = self.root / 'reports/first'
        review = json.loads((folder / 'review.json').read_text())
        self.assertEqual(review['suggestions'][0]['feedback_ids'], ['004'])
        text = (folder / 'report.md').read_text()
        self.assertIn('重试按钮', text)
        self.assertIn('004、005', text)
        online_rows = detail_rows(folder)
        self.assertIn('充值金额', online_rows[0])
        self.assertEqual(len(online_rows), 5)
        book = load_workbook(folder / 'feedback_details.xlsx')
        self.addCleanup(book.close)
        headers = [c.value for c in book.worksheets[0][4]]
        self.assertIn('充值金额', headers)
        self.assertNotIn('VIP等级', headers)
        self.assertEqual(book.worksheets[0].cell(5, headers.index('充值金额')+1).value, '196.98')

    def test_ai_flags_are_strict_and_invalid_feedback_cannot_be_candidates(self):
        answer = dict(translation_zh='原文无法理解', label='', theme='', subtheme='',
                      is_valid=False, exclusion_reason='内容无法理解', is_suggestion=True, is_bug=True)
        checked = validate_result(answer, self.config)
        self.assertFalse(checked['is_suggestion'])
        self.assertFalse(checked['is_bug'])
        for field in ('is_suggestion', 'is_bug'):
            for value in ('true', None, 1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_result({**answer, field: value}, self.config)
        row = {'反馈ID':'001', '用户自己填写的内容':'测试', '反馈标签':''}
        source_config = {**self.config, 'candidate_source': 'source_label'}
        self.assertNotEqual(fingerprint(row, self.config), fingerprint(row, source_config))
        self.assertIn('is_bug', batch_instructions(self.config))
        baloot = load_config('baloot')
        answer.update(is_valid=True, exclusion_reason='', label='其他', theme='', subtheme='')
        self.assertFalse(validate_result(answer, baloot)['is_suggestion'])
        self.assertTrue(validate_result(answer, baloot, '建议')['is_suggestion'])
        self.assertNotIn('is_bug', validate_result(answer, baloot))

    def test_game_page_upload_and_feishu_disabled(self):
        app = create_app({'TESTING': True, 'STORAGE_ROOT': self.root / 'web'})
        client = app.test_client()
        page = client.get('/games/xinjiang')
        self.assertEqual(page.status_code, 200)
        token = re.search(r'data-workspace="([^"]+)"', page.get_data(as_text=True)).group(1)
        with client.session_transaction() as session:
            csrf = session['csrf']
        result = client.post('/api/games/xinjiang/uploads',
            headers={'X-Workspace': token, 'X-CSRF-Token': csrf},
            data={'file': (BytesIO(self.source().to_csv(index=False).encode()), 'xinjiang.csv')})
        self.assertEqual(result.status_code, 201, result.get_json())
        self.assertEqual(result.get_json()['valid'], 8)
        self.assertFalse(self.config['feishu']['enabled'])
