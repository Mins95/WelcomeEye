"""Entry presentation languages, complete native catalogues and safe messages."""
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

from load_integration import load, ROOT
from test_connect3_config import flow

localization = load('localization')


class LanguageTests(unittest.TestCase):
    def test_language_validation_fallback_and_secret_safe_opening_errors(self):
        for code in ('fr','en','es'):
            self.assertEqual(localization.language_for({'language':code},'fr'),code)
            hub=SimpleNamespace(entry=SimpleNamespace(data={'language':code,'opening_code':'PRIVATE_CODE'}))
            error=SimpleNamespace(reason='device_rejected',physical_request_uncertain=False)
            text=localization.opening_error(hub,error)
            self.assertNotIn('PRIVATE_CODE',text)
            self.assertNotIn('SimpleNamespace',text)
        self.assertEqual(localization.language_for({},'es-ES'),'es')
        self.assertEqual(localization.language_for({'language':'unknown'},'en'),'en')

    def test_complete_native_translations_and_placeholder_parity(self):
        def leaves(value,prefix=''):
            result={}
            for key,item in value.items():
                name=prefix+'.'+key
                if isinstance(item,dict): result.update(leaves(item,name))
                else: result[name]=item
            return result
        original=leaves(json.loads((ROOT/'strings.json').read_text(encoding='utf-8')))
        for language in ('fr','en','es'):
            translated=leaves(json.loads((ROOT/f'translations/{language}.json').read_text(encoding='utf-8')))
            self.assertEqual(set(original),set(translated),language)
            for key,source in original.items():
                value=translated[key]
                self.assertTrue(isinstance(value,str) and value, key)
                self.assertEqual(set(re.findall(r'\{[^{}]+\}',source)),
                    set(re.findall(r'\{[^{}]+\}',value)),key)
                self.assertNotIn('\ufffd',value,key)

    def test_native_language_options_are_bounded(self):
        cls=flow()
        self.assertEqual(set(cls._language_schema({})),{'language'})
        for invalid in ('de',None,True,[],{}):
            updates={}
            with self.assertRaises(ValueError):
                cls._read_opening_choices({'language':invalid},updates,{})
            self.assertEqual(updates,{})


class LanguageFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_models_change_language_without_replacing_identity_or_secrets(self):
        for family,variant in (('legacy_owsp','connect_v1'),('legacy_owsp','connect2_r001'),
                               ('r002_experimental','connect2_r002'),('connect3_qv_experimental','connect3')):
            for language in ('en','es'):
                instance=flow()
                entry=SimpleNamespace(entry_id='entry',unique_id='unique',data={
                    'device_variant':variant,'protocol_family':family,'host':'192.0.2.1',
                    'password':'PRIVATE','auth_code':'PRIVATE_AUTH',
                    'certificate_sha256':'a'*64,'opening_code':'PRIVATE_OPENING'})
                instance._get_reconfigure_entry=lambda:entry
                before=dict(entry.data)
                form=await instance.async_step_reconfigure()
                self.assertIn('language',form['data_schema'])
                result=await instance.async_step_reconfigure({'language':language})
                self.assertEqual(result['type'],'abort')
                self.assertEqual(entry.data['language'],language)
                for key in ('uid','auth_code','opening_code','password','certificate_sha256'):
                    if key in before:self.assertEqual(entry.data[key],before[key])


if __name__=='__main__':
    unittest.main()
