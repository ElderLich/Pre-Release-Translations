from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from string import Formatter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prerelease_gui as gui
import prerelease_i18n as i18n
import prerelease_preferences as prefs
import AutoPR_local as wrapper


class PreferenceTests(unittest.TestCase):
    def test_renamed_data_checkout_repairs_only_the_missing_official_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Path(directory).resolve()/'Pre-Release-Translations'
            current=repo.parent/'MDPro4_Data/Data/locales'
            previous=repo.parent/'MDPro3_Data/Data/locales'
            self.assertEqual(prefs.translations_root(repo),str(current))
            self.assertEqual(prefs.translations_root(repo,str(previous)),str(previous))
            current.mkdir(parents=True)
            self.assertEqual(prefs.translations_root(repo,str(previous)),str(current))
            self.assertEqual(prefs.translations_root(repo,str(current)),str(current))
            previous.mkdir(parents=True)
            self.assertEqual(prefs.translations_root(repo,str(previous)),str(previous))
            custom=repo.parent/'translator-fork/MDPro3_Data/Data/locales'
            self.assertEqual(prefs.translations_root(repo,str(custom)),str(custom))
            self.assertEqual(prefs.translations_root(repo,'custom/locales'),'custom/locales')

    def test_catalogs_preserve_placeholders_and_cover_the_same_controls(self):
        keys=set(i18n.CATALOGS['FR'])
        fields=lambda s:{field for _,field,_,_ in Formatter().parse(s) if field is not None}
        for lang,catalog in i18n.CATALOGS.items():
            self.assertEqual(set(catalog),keys,lang)
            for key,value in catalog.items():
                self.assertEqual(fields(key),fields(value),(lang,key))

    def test_editor_detection_and_arguments_preserve_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='editor space ') as directory:
            root=Path(directory); exe=root/'DataEditorX.exe'; exe.touch()
            db=root/'Rush cards.cdb'; db.touch()
            self.assertEqual(prefs.find_editor(str(exe)),exe)
            self.assertIsNone(prefs.find_editor(str(root/'missing.exe')))
            with patch.object(prefs.subprocess,'Popen') as start:
                prefs.launch_editor(exe,db)
                self.assertEqual(start.call_args.args[0],[str(exe.resolve()),str(db.resolve())])
                self.assertNotIn('shell',start.call_args.kwargs)
            self.assertEqual(prefs.database_path(root,'FR',True),root/'FR/Rush/raw2/rush-pre-release.cdb')
            self.assertEqual(prefs.database_path(root,'IT',False),root/'IT/raw2/test-release.cdb')

    def test_hidden_worker_children_keep_console_commands_unchanged(self):
        import local_workspace
        with patch.dict(local_workspace.os.environ, {"TRANSSUPERPRE_GUI":"1"}):
            self.assertEqual(local_workspace.child_creationflags(),getattr(local_workspace.subprocess,"CREATE_NO_WINDOW",0))
        with patch.dict(local_workspace.os.environ, {"TRANSSUPERPRE_GUI":"0"}):
            self.assertEqual(local_workspace.child_creationflags(),0)

    def test_compact_activity_uses_actual_counts_not_invented_progress(self):
        from prerelease_activity import activity_result
        key,values=activity_result('[SourceText] Updated CDB rows=9; translator edits preserved')
        self.assertEqual(i18n.translate(key,'FR',**values),'9 textes de cartes mis à jour.')
        self.assertIsNone(activity_result('Downloading package...'))
        key,values=activity_result('FR Rush ready: 91 cards; 2 added, 1 retired.')
        self.assertEqual(values,{'cards':'91','added':'2','removed':'1'})

    def test_windowless_relaunch_is_gui_only_and_never_recurses(self):
        with patch.object(wrapper.sys,'executable','C:/Python/python.exe'), patch.object(wrapper.subprocess,'Popen') as start, patch.object(wrapper.Path,'is_file',return_value=True):
            for arg in ('refresh','--menu','rush-refresh','rush-upload'):
                with patch.object(wrapper.sys,'argv',['AutoPR.py',arg]):
                    self.assertFalse(wrapper.relaunch_windowless())
            start.assert_not_called()
            if wrapper.os.name=='nt':
                with patch.object(wrapper.sys,'argv',['AutoPR.py']):
                    self.assertTrue(wrapper.relaunch_windowless())
                    self.assertTrue(start.call_args.args[0][0].endswith('pythonw.exe'))
                with patch.object(wrapper.sys,'argv',['AutoPR.py']),patch.object(wrapper.sys,'executable','C:/Python/pythonw.exe'):
                    self.assertFalse(wrapper.relaunch_windowless())


class LocalizedWindowTests(unittest.TestCase):
    def window(self,lang='FR',config=None,scale=1):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)
        if config:
            (path/'.transsuperpre_local.json').write_text(json.dumps(config),encoding='utf-8')
        root=gui.tk.Tk(); root.withdraw()
        root.tk.call('tk','scaling',scale*96/72)
        window=gui.PrereleaseWindow(root,path,path,lang)
        self.addCleanup(window.close)
        return root,window

    def test_renamed_translation_checkout_reaches_gui_and_saved_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory).resolve()
            repo=parent/'Pre-Release-Translations'; repo.mkdir()
            local=parent/'local'; local.mkdir()
            current=parent/'MDPro4_Data/Data/locales'; current.mkdir(parents=True)
            config=local/'.transsuperpre_local.json'
            config.write_text(json.dumps({'rush_translations_root':str(parent/'MDPro3_Data/Data/locales')}),encoding='utf-8')
            root=gui.tk.Tk(); root.withdraw()
            window=gui.PrereleaseWindow(root,repo,local,'EN')
            try:
                self.assertEqual(window.translations.get(),str(current))
                window.save_paths()
                self.assertEqual(json.loads(config.read_text())['rush_translations_root'],str(current))
            finally:
                window.close()

    def test_default_follows_locale_and_override_persists_without_changing_card_language(self):
        root,window=self.window()
        self.assertEqual(window.display_language,'FR')
        self.assertEqual(window.standard_fetch_button.cget('text'),'Actualiser depuis MyCard')
        window.language.set('IT')
        self.assertEqual(window.standard_fetch_button.cget('text'),'Aggiorna da MyCard')
        window.ui_language='EN'; window.apply_language(); window.save_paths()
        self.assertEqual(window.language.get(),'IT')
        self.assertEqual(window.standard_fetch_button.cget('text'),'Fetch updates from MyCard')
        self.assertEqual(json.loads(window.config_path.read_text())['interface_language'],'EN')
        window.rush_language.set('KR'); window.notebook.select(1); root.update()
        self.assertEqual(window.display_language,'EN')

    def test_hidden_log_keeps_animation_and_readable_status_then_stops_timer(self):
        root,window=self.window(config={'show_log':False})
        with patch.object(gui.threading,'Thread'):
            window.start(['test-command'],'Standard')
        self.assertIsNotNone(window.progress.timer)
        self.assertFalse(window.log_visible.get())
        window.events.put(('log','[SourceText] CodexYGO: matched card\n'))
        window.events.put(('done',0))
        root.after_cancel(window.poll_id); window.poll()
        self.assertIsNone(window.progress.timer)
        self.assertEqual(window.status_kind.get(),'TERMINÉ')
        self.assertIn('CodexYGO',window.log.get('1.0','end'))
        self.assertFalse(window.busy)

    def test_french_and_italian_buttons_fit_and_all_tabs_are_translated(self):
        for lang in ('FR','IT'):
            for scale in (1,1.25,1.5):
                root,window=self.window(lang,scale=scale)
                for width,height in ((window.px(1120),window.px(940)),root.minsize()):
                    root.geometry(f'{width}x{height}'); root.deiconify(); root.update()
                    for index in (0,1,2):
                        window.notebook.select(index); root.update()
                        for control,_ in window.controls:
                            if control.winfo_ismapped() and isinstance(control,gui.ttk.Button):
                                self.assertGreaterEqual(control.winfo_width()+2,control.winfo_reqwidth(),(lang,scale,control.cget('text')))
                self.assertEqual(window.nav_buttons[2].cget('text'),i18n.translate('All locales',lang))
