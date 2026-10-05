import os
import configparser
from contextlib import closing
import json
import re
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from usage import collect, render_display


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, 'nora.db')
        self.ledger = os.path.join(self.tmp.name, 'ledger.db')
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript('''
                CREATE TABLE sessions(id TEXT, source TEXT, chat_type TEXT,
                    ended_at REAL, last_activity_at REAL, started_at REAL);
                CREATE TABLE session_model_usage(session_id TEXT, model TEXT, task TEXT,
                    billing_provider TEXT, billing_base_url TEXT, billing_mode TEXT,
                    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                    estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
                    first_seen REAL, last_seen REAL);
            ''')
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()

    def row(self, sid='live', model='gpt-6-sol', task='', ins=100, outs=20,
            cache=50, estimated=.3, actual=0, status='estimated', seen=None):
        seen = seen or self.now
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (sid, model, task, 'openrouter', None, None, ins, outs, cache,
                 estimated, actual, status, seen, seen))

    def session(self, sid='live', seen=None, ended=None, source='telegram'):
        seen = seen or self.now
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
                (sid, source, 'dm', ended, seen, seen-1800))

    def run_collect(self, now=None):
        return collect({'Nora': self.source}, self.ledger, now or self.now)

    def test_active_session_counts_cost_once_and_initial_windows_partial(self):
        self.session()
        self.row()
        self.row(model='other-model', ins=30, outs=5, cache=8, estimated=.2)
        self.row(model='aux-model', task='title_generation', ins=11, outs=2, estimated=.01)
        result = self.run_collect()
        row = result['session']['Nora']
        self.assertEqual((row['input_tokens'], row['output_tokens'], row['cache_read_tokens']), (130, 25, 58))
        self.assertAlmostEqual(row['cost_usd'], .5)
        self.assertEqual(row['model'], 'gpt-6-sol')
        self.assertEqual(row['status'], 'active')
        self.assertIsNone(result['today']['cost_usd'])
        self.assertTrue(result['today']['partial'])
        self.assertIsNone(result['last_24h']['Nora']['cost_usd'])
    def test_deltas_count_gross_today_and_conversation_only_in_last24(self):
        self.session()
        self.row(ins=100, outs=20, estimated=.30)
        self.row(model='helper', task='title_generation', ins=10, outs=1, estimated=.01)
        self.run_collect()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE session_model_usage SET input_tokens=120, output_tokens=25, cache_read_tokens=60, estimated_cost_usd=.42, last_seen=? WHERE task=''", (self.now+90,))
            db.execute("UPDATE session_model_usage SET input_tokens=13, output_tokens=2, estimated_cost_usd=.02, last_seen=? WHERE task='title_generation'", (self.now+90,))
        result = self.run_collect(self.now+90)
        self.assertEqual((result['today']['input_tokens'], result['today']['output_tokens']), (23, 6))
        self.assertAlmostEqual(result['today']['cost_usd'], .13)
        self.assertEqual((result['last_24h']['Nora']['input_tokens'], result['last_24h']['Nora']['output_tokens'], result['last_24h']['Nora']['cache_read_tokens']), (20, 5, 10))
        self.assertAlmostEqual(result['last_24h']['Nora']['cost_usd'], .12)
        self.assertTrue(result['last_24h']['Nora']['partial'])
        self.assertAlmostEqual(self.run_collect(self.now+90)['today']['cost_usd'], .13)
    def test_subscription_included_is_zero_cost_and_model_filters_apply(self):
        self.session()
        self.row(model='gpt-6-sol', status='included', estimated=.99, actual=0)
        self.row(model='tencent/hy3', ins=60, outs=6, estimated=.6)
        self.row(model='z-ai/glm-5.2', ins=40, outs=4, estimated=.4)
        self.run_collect()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE session_model_usage SET input_tokens=input_tokens+10, estimated_cost_usd=estimated_cost_usd+.1, last_seen=?", (self.now+90,))
        result = self.run_collect(self.now+90)
        self.assertEqual(result['session']['Nora']['input_tokens'], 110)
        self.assertEqual(result['session']['Nora']['cost_usd'], 0)
        self.assertEqual(result['last_24h']['Nora']['input_tokens'], 10)
        self.assertEqual(result['last_24h']['Nora']['cost_usd'], 0)
        self.assertAlmostEqual(result['today']['cost_usd'], .2)

    def test_late_actual_cost_correction_is_accounted_for(self):
        self.session()
        self.row(estimated=.3, actual=0, status='estimated')
        self.run_collect()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE session_model_usage SET actual_cost_usd=.25, cost_status='actual', last_seen=?", (self.now+90,))
        result = self.run_collect(self.now+90)
        self.assertAlmostEqual(result['session']['Nora']['cost_usd'], .25)
        self.assertAlmostEqual(result['today']['cost_usd'], -.05)
    def test_cli_emits_json_without_private_session_metadata(self):
        self.session()
        self.row()
        completed = subprocess.run([sys.executable, 'usage.py', '--nora-db', self.source,
                                    '--ledger', self.ledger], cwd=os.path.dirname(__file__),
                                   capture_output=True, text=True, check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result['session']['Nora']['input_tokens'], 100)
        self.assertEqual(result['missing_agents'], ['ITGuy'])
        self.assertNotIn('session_id', completed.stdout)
        self.assertNotIn('chat_id', completed.stdout)
        self.assertNotIn(self.source, completed.stdout)
    def test_render_display_has_fixed_ten_plain_text_fields(self):
        self.session()
        self.row()
        result = self.run_collect()
        lines = render_display(result)
        self.assertEqual(len(lines), 10)
        self.assertIn('Nora', lines[1])
        self.assertIn('gpt-6-sol', lines[1])
        self.assertIn('in 100', lines[2])
        self.assertIn('$0.3000', lines[2])
        self.assertIn('ITGuy unavailable', lines[3])
        self.assertIn('warming', lines[0].lower())
        self.assertFalse(any('live' in line for line in lines))
    def test_render_display_shows_populated_24h_window(self):
        self.session()
        self.row()
        self.run_collect()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE session_model_usage SET input_tokens=105, last_seen=?", (self.now+90,))
        result = self.run_collect(self.now+90)
        lines = render_display(result)
        self.assertIn('Nora', lines[5])
        self.assertIn('in 5', lines[6])
        self.assertEqual(len(lines), 10)
    def test_cli_display_mode_emits_only_fixed_strings(self):
        self.session()
        self.row()
        completed = subprocess.run([sys.executable, 'usage.py', '--nora-db', self.source,
                                    '--ledger', self.ledger, '--display'],
                                   cwd=os.path.dirname(__file__), capture_output=True,
                                   text=True, check=True)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload), ['display'])
        self.assertEqual(len(payload['display']), 10)
        self.assertNotIn('session_id', completed.stdout)
    def test_widget_json_is_regex_parseable_even_for_odd_model_names(self):
        self.session()
        self.row(model='provider/model"\\broken')
        response = json.dumps({'display': render_display(self.run_collect())},
                              separators=(',', ':'), ensure_ascii=False)
        pattern = r'^\{"display":\[' + ','.join([r'"([^"\\]*)"'] * 10) + r'\]\}$'
        match = re.fullmatch(pattern, response)
        self.assertIsNotNone(match)
        self.assertEqual(len(match.groups()), 10)
    def test_native_skin_polls_every_90s_and_keeps_values_on_failure(self):
        skin = configparser.RawConfigParser(interpolation=None)
        skin.read(os.path.join(os.path.dirname(__file__), 'HUM.ini'), encoding='utf-8')
        self.assertEqual(skin.getint('Rainmeter', 'Update'), 90000)
        self.assertEqual(skin.getint('Rainmeter', 'AlwaysOnTop'), -2)
        parent = skin['MeasureUsage']
        self.assertEqual(parent['UpdateRate'], '1')
        self.assertIn('STALE', parent['OnConnectErrorAction'])
        self.assertIn('STALE', parent['OnRegExpErrorAction'])
        self.assertEqual(skin['MeterClose']['LeftMouseUpAction'], '[!DeactivateConfig]')
        self.assertEqual(len([s for s in skin.sections() if s.startswith('MeasureField')]), 10)
        self.session()
        self.row()
        response = json.dumps({'display': render_display(self.run_collect())},
                              separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(len(re.fullmatch(parent['RegExp'], response).groups()), 10)
        for meter in ('MeterTodayValue', 'MeterNoraSession', 'MeterITGuySession',
                      'MeterNora24h', 'MeterITGuy24h'):
            self.assertNotIn('MeasureName', skin[meter])
    def test_new_model_row_after_baseline_counts_full_usage(self):
        self.session()
        self.row()
        self.run_collect()
        self.row(model='new-model', ins=40, outs=6, cache=12,
                 estimated=.08, seen=self.now+90)
        result = self.run_collect(self.now+90)
        self.assertEqual(result['today']['input_tokens'], 40)
        self.assertEqual(result['last_24h']['Nora']['input_tokens'], 40)
        self.assertAlmostEqual(result['today']['cost_usd'], .08)
        self.assertEqual(result['session']['Nora']['input_tokens'], 140)
    def test_native_skin_uses_only_windows_fonts(self):
        skin = configparser.RawConfigParser(interpolation=None)
        skin.read(os.path.join(os.path.dirname(__file__), 'HUM.ini'), encoding='utf-8')
        faces = {section.get('FontFace') for section in skin.values() if section.get('FontFace')}
        self.assertEqual(faces, {'Tahoma', 'Consolas'})


if __name__ == '__main__':
    unittest.main()
