from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import rush_prerelease as rush
import rush_translator as translator
import rush_source_text as sources
from card_text_autofill import CardText


class LocaleSourceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.repo=self.root/'repo'; self.local_root=self.root/'local'
        self.published,self.local,self.baseline,self.reports=translator.paths(self.repo,self.local_root,'FR')
        self.data={120309057:(120309057,4,0,0,33,1000,1000,4,1,1,0)}
        self.raw={120309057:(120309057,'原名','原文',*(['']*16))}
        self.base=self.repo/'FR/Rush/Base Files/rush-pre-release.cdb'
        rush.write_changed(self.base,rush.cdb_bytes(self.data,self.raw))
        rush.write_changed(self.published,self.base.read_bytes())
        rush.write_changed(self.repo/'Shared/Rush/sets.md',b'(RD/CA01-JP): `120309XXX`')

    def test_refresh_fetches_french_text_without_upstream_checkout_and_keeps_baseline(self):
        fetched={'RD/CA01-JP057':CardText('Nom français','[CONDITION] Aucune\n[EFFET] Piochez.','https://example.test')}
        with patch.object(sources,'fetch_yugipedia_cards',return_value=fetched) as fetch:
            translator.refresh_locale(self.repo,self.local_root,'FR',log=lambda _:None)
        fetch.assert_called_once()
        data,texts=rush.read_cdb(self.local)
        self.assertEqual(data,self.data)
        self.assertEqual(texts[120309057][1],'Nom français')
        self.assertIn('[EFFET]\t\nPiochez.',texts[120309057][2])
        self.assertEqual(self.baseline.read_bytes(),self.published.read_bytes())
        self.assertIn('120309057',rush.read_json(self.reports/'source_text.json')['matches'])

    def test_manual_edit_survives_second_fetch_and_upstream_retirement(self):
        translator.refresh_locale(self.repo,self.local_root,'FR',fetch_text=False,log=lambda _:None)
        edited=dict(self.raw); edited[120309057]=(120309057,'Nom humain','Texte humain',*(['']*16))
        rush.write_changed(self.local,rush.cdb_bytes(self.data,edited))
        with patch.object(sources,'fetch_yugipedia_cards',return_value={}) as fetch:
            translator.refresh_locale(self.repo,self.local_root,'FR',log=lambda _:None)
        # No card is requested from the network once every editable field is translated.
        self.assertEqual(fetch.call_args.args[0],[])
        self.assertEqual(rush.read_cdb(self.local)[1],edited)
        rush.write_changed(self.published,rush.cdb_bytes({},{}))
        translator.refresh_locale(self.repo,self.local_root,'FR',fetch_text=False,log=lambda _:None)
        self.assertEqual(rush.read_cdb(self.local)[0],{})

    def test_offline_option_and_failed_source_leave_database_usable(self):
        with patch.object(sources,'fetch_rows',side_effect=AssertionError('must be offline')):
            translator.refresh_locale(self.repo,self.local_root,'FR',fetch_text=False,log=lambda _:None)
        with patch.object(sources,'fetch_yugipedia_cards',return_value={}):
            translator.refresh_locale(self.repo,self.local_root,'FR',log=lambda _:None)
        self.assertEqual(rush.read_cdb(self.local)[1],self.raw)
        self.assertTrue(rush.read_json(self.reports/'source_text.json')['unresolved'])


    def test_editor_save_during_web_lookup_wins_and_manual_format_is_preserved(self):
        translator.refresh_locale(self.repo,self.local_root,'FR',fetch_text=False,log=lambda _:None)
        def fetch(*args,**kwargs):
            data,rows=rush.read_cdb(self.local)
            value=list(rows[120309057]); value[2]='[EFFET]  Texte humain sur une ligne.'
            rows[120309057]=tuple(value)
            rush.write_changed(self.local,rush.cdb_bytes(data,rows))
            return {'RD/CA01-JP057':CardText('Nom web','[EFFET] Texte web.','https://example.test')}
        with patch.object(sources,'fetch_yugipedia_cards',side_effect=fetch):
            translator.fill_source_text(self.repo,self.local,self.reports,'FR',log=lambda _:None)
        self.assertEqual(rush.read_cdb(self.local)[1][120309057][1:3],('Nom web','[EFFET]  Texte humain sur une ligne.'))
        self.assertEqual(rush.read_json(self.reports/'source_text.json')['matches']['120309057']['fields'],['name'])
