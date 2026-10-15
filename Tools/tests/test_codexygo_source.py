from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '.github/scripts'))
import codexygo_source as codex
import card_text_autofill as source


def record(rush=False):
    return {'codexygoId': 12, 'yugipediaSlug': 'Verified_Card', 'rush': rush,
            'translations': {'en': {'name': 'Verified Card', 'description': 'Draw.'},
                             'fr': {'name': 'Carte vérifiée', 'description': 'Piochez.'}}}


class CodexTests(unittest.TestCase):
    def fetch(self, data, title='Verified Card', rush=False, params=None):
        requests = []
        def request(url):
            requests.append(url)
            return json.dumps(data)
        result = codex.fetch_french(title, params or {}, rush, request, lambda _: None)
        return result, requests

    def test_documented_query_and_actual_response_envelope(self):
        card, urls = self.fetch({'result': {'data': [record()]}})
        self.assertEqual(card.name, 'Carte vérifiée')
        self.assertEqual(parse_qs(urlparse(urls[0]).query)['format'], ['master'])
        self.assertEqual(parse_qs(urlparse(urls[0]).query)['languages'], ['en,fr'])
        self.assertEqual(urlparse(urls[0]).path, '/api/card/v1/search/')
        self.assertEqual(parse_qs(urlparse(card.source).query)['codexygo_ids'], ['12'])

    def test_name_similarity_format_mismatch_and_ambiguity_are_rejected(self):
        other = record(); other['yugipediaSlug'] = 'Unrelated_Card'
        self.assertIsNone(self.fetch({'data': [other]})[0])
        self.assertIsNone(self.fetch({'data': [record(True)]})[0])
        other = record(); other['codexygoId'] = 13
        self.assertIsNone(self.fetch({'data': [record(), other]})[0])

    def test_different_english_name_is_discovered_but_exact_slug_is_required(self):
        for title, alias, search in (('Mad Rare Amy', 'Mad Rhea Amy', 'Amy'),
                                     ('Praime Stock', 'Prime Stock', 'Stock')):
            card = record(True)
            card['yugipediaSlug'] = title.replace(' ', '_')
            card['translations']['en']['name'] = alias
            card['translations']['fr']['fullText'] = 'Piochez.'
            unrelated = deepcopy(card)
            unrelated['codexygoId'] = 13
            unrelated['yugipediaSlug'] = 'Different_Card'
            queries = []
            def request(url):
                query = parse_qs(urlparse(url).query)['search'][0]
                queries.append(query)
                return json.dumps({'data': [unrelated, card] if query == search else []})
            result = codex.fetch_french(title, {}, True, request, lambda _: None)
            self.assertIsNotNone(result, title)
            self.assertEqual(parse_qs(urlparse(result.source).query)['codexygo_ids'], ['12'])
            self.assertEqual(queries[0], title)
            self.assertIn(search, queries)

    def test_broader_search_still_rejects_ambiguity_on_later_pages(self):
        first, second = record(), record()
        second['codexygoId'] = 13
        def request(url):
            query = parse_qs(urlparse(url).query)
            if query['search'] == ['Verified Card']:
                return json.dumps({'data': []})
            page = int(query['page'][0])
            return json.dumps({'data': [first if page == 1 else second], 'pagination': {'totalPages': 2}})
        self.assertIsNone(codex.fetch_french('Verified Card', {}, False, request, lambda _: None))

    def test_overbroad_search_does_not_accept_a_partial_page_match(self):
        queries = []
        def request(url):
            query = parse_qs(urlparse(url).query)['search'][0]
            queries.append(query)
            if query == 'Verified Card':
                return json.dumps({'data': []})
            return json.dumps({'data': [record()], 'pagination': {'totalPages': 11}})
        self.assertIsNone(codex.fetch_french('Verified Card', {}, False, request, lambda _: None))
        self.assertLessEqual(len(queries), 5)

    def test_pagination_does_not_hide_second_identity(self):
        first, second = record(), record(); second['codexygoId'] = 13
        def request(url):
            page = int(parse_qs(urlparse(url).query)['page'][0])
            return json.dumps({'data': [first if page == 1 else second], 'pagination': {'totalPages': 2}})
        self.assertIsNone(codex.fetch_french('Verified Card', {}, False, request, lambda _: None))

    def test_incomplete_pendulum_text_is_rejected(self):
        card = record()
        card['translations']['en']['pendulumEffect'] = 'Destroy.'
        self.assertIsNone(codex.format_french(card, 'Verified Card', {'pendulum_effect': 'Destroy.'}, False))
        card['translations']['fr']['pendulumEffect'] = 'Détruisez.'
        result = codex.format_french(card, 'Verified Card', {'pendulum_effect': 'Destroy.', 'pendulum_scale': '4'}, False)
        self.assertIn('Détruisez.', result.desc)
        self.assertIn('Piochez.', result.desc)
        self.assertIn('4', result.desc)

    def test_rush_materials_requirements_choices_and_maximum_are_preserved(self):
        card = record(True)
        card['rushEffectType'], card['atkMax'] = 'choice', 4000
        card['translations'] = {'en': {'rushOtherEffects': 'A + B', 'rushCondition': 'Pay.', 'rushChoiceEffects': ['Draw.', 'Destroy.']},
                                'fr': {'name': 'Carte', 'rushOtherEffects': 'A + B', 'rushCondition': 'Payez.',
                                       'rushChoiceEffects': ['Piochez.', 'Détruisez.'],
                                       'fullText': 'A + B\n[CONDITION] Payez.\n[EFFET MULTI-CHOIX] ●Piochez. ●Détruisez.'}}
        params = {'materials': 'A + B', 'requirement': 'Pay.'}
        result = codex.format_french(card, 'Verified Card', params, True)
        self.assertEqual(result.desc, 'ATK Maximum 4000\nA + B\n[CONDITION]\nPayez.\n[EFFET MULTI-CHOIX]\n●Piochez.\n●Détruisez.')
        for field in ('rushOtherEffects','rushCondition','rushChoiceEffects'):
            incomplete = deepcopy(card); incomplete['translations']['fr'].pop(field)
            self.assertIsNone(codex.format_french(incomplete, 'Verified Card', params, True))
        card['translations']['fr']['rushChoiceEffects'].pop()
        self.assertIsNone(codex.format_french(card, 'Verified Card', params, True))

    def test_rush_title_suffix_matches_api_slug_but_still_requires_rush_flag(self):
        card = record(True)
        card['translations']['fr']['fullText'] = 'Piochez.'
        result, urls = self.fetch({'data': [card]}, title='Verified Card (Rush Duel)', rush=True)
        self.assertIsNotNone(result)
        self.assertEqual(parse_qs(urlparse(urls[0]).query)['format'], ['rush'])

    def test_source_failure_keeps_existing_text_unmodified(self):
        with patch.object(source,'request_url',side_effect=OSError('offline')):
            self.assertIsNone(codex.fetch_french('Card', {}, False, source.request_url, lambda _: None))
        self.assertIsNone(self.fetch({'error': 'unavailable'})[0])

    def test_fallback_runs_only_after_printed_code_verification(self):
        wiki = {'query': {'pages': [{'title': 'Verified Card', 'revisions': [{'content': '{{CardTable2\n| text = Draw.\n| jp_sets = TEST-JP001; Set; Rare\n}}'}]}]}}
        def request(url):
            return json.dumps({'result': {'data': [record()]}} if url.startswith(codex.API) else wiki)
        with patch.object(source,'request_url',side_effect=request) as req:
            cards = source.fetch_yugipedia_cards(['Verified Card'], lambda _: None, lang='fr', expected_codes={'Verified Card': 'TEST-JP001'})
            self.assertEqual(cards['Verified Card'].name, 'Carte vérifiée')
            self.assertEqual(req.call_count, 2)
        with patch.object(source,'request_url',side_effect=request) as req:
            self.assertFalse(source.fetch_yugipedia_cards(['Verified Card'], lambda _: None, lang='fr', expected_codes={'Verified Card': 'TEST-JP002'}))
            self.assertEqual(req.call_count, 1)


    def test_malformed_api_records_fail_closed(self):
        for payload in ([],None,{'result':None},{'data':[None,{},'bad']}):
            self.assertIsNone(self.fetch(payload)[0])
        for bad in (None,13,{},['name']):
            card=record(); card['translations']['fr']['name']=bad
            self.assertIsNone(self.fetch({'data':[card]})[0])
        card=record(True); card['translations']['fr']['rushChoiceEffects']='not an array'
        self.assertIsNone(self.fetch({'data':[card]},rush=True)[0])
