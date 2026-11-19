import json
import unittest

from decision_memory.deadline_relation import deadline_anchor_conflicts, deadline_anchor_diagnostics


def field_with_citations(claim, *quotes):
    return {'text': claim, 'evidence': [{'source_id': f'citation-{index}', 'quote': quote}
                                        for index, quote in enumerate(quotes, 1)]}


class DeadlineRelationTests(unittest.TestCase):
    def test_conflict_links_the_exact_same_duration_clauses(self):
        claim = '🧰 Stock note. Submit the inspection form at least 72 hours before the session starts.'
        quote = ('Inventory is optional. Submit the inspection form at least three days '
                 'before the doors open; keep a duplicate.')
        record, = deadline_anchor_conflicts(field_with_citations(claim, 'Keep two copies.', quote))

        self.assertEqual(record['duration_key'], 'time-seconds:259200')
        self.assertEqual(record['direction'], 'before')
        self.assertEqual(record['claim']['anchor'], 'start')
        self.assertEqual(claim[record['claim']['start']:record['claim']['end']],
                         'at least 72 hours before the session starts')
        cited, = record['evidence']
        self.assertEqual(cited['source_id'], 'citation-2')
        self.assertEqual(cited['anchor'], 'opening')
        self.assertEqual(quote[cited['start']:cited['end']], 'at least three days before the doors open')
        self.assertEqual(json.loads(json.dumps(record)), record)

    def test_chinese_anchor_before_the_duration_preserves_the_workday_unit(self):
        claim = '清单须在演出开始前至少三个工作日提交。'
        quote = '只留一份。清单须在场馆开门前至少3个工作日提交。'
        record, = deadline_anchor_conflicts(field_with_citations(claim, quote))

        self.assertEqual(record['duration_key'], 'business-days:3')
        self.assertEqual(record['direction'], 'before')
        cited, = record['evidence']
        self.assertEqual(quote[cited['start']:cited['end']], '场馆开门前至少3个工作日')

    def test_release_and_taking_effect_are_distinct_reference_events(self):
        claim = 'Archive the notice five days after the rules take effect.'
        quote = 'Archive the notice 120 hours after the rules are published.'
        record, = deadline_anchor_conflicts(field_with_citations(claim, quote))

        self.assertEqual(record['direction'], 'after')
        self.assertEqual(record['claim']['anchor'], 'effective')
        self.assertEqual(record['evidence'][0]['anchor'], 'release')

    def test_cross_language_windows_without_action_overlap_are_advisory(self):
        claim = '盘点单在演出开始前两天提交。'
        quote = 'File the storage checklist forty-eight hours before the doors open.'
        result = deadline_anchor_diagnostics(field_with_citations(claim, quote))

        self.assertEqual(result['conflicts'], [])
        record, = result['candidates']
        self.assertEqual(record['duration_key'], 'time-seconds:172800')
        cited, = record['evidence']
        self.assertEqual(quote[cited['start']:cited['end']], 'forty-eight hours before the doors open')

    def test_revised_duration_can_be_located_alongside_a_different_earlier_duration(self):
        claim = '物资清单须在演出开始前二十四小时提交。'
        quote = ('Move the supply list cutoff from twelve hours before scheduled doors '
                 'to twenty-four hours before scheduled doors.')
        record, = deadline_anchor_diagnostics(field_with_citations(claim, quote))['candidates']

        cited, = record['evidence']
        self.assertEqual(quote[cited['start']:cited['end']], 'twenty-four hours before scheduled doors')

    def test_repeated_equal_durations_in_one_clause_are_ambiguous(self):
        claim = '物资清单须在演出开始前一天提交。'
        quote = 'Move the cutoff from one day before scheduled doors to 24 hours before scheduled doors.'
        self.assertEqual(deadline_anchor_diagnostics(field_with_citations(claim, quote)),
                         {'conflicts': [], 'candidates': []})

    def test_unrelated_qualified_or_compatible_phrases_stay_silent(self):
        pairs = [
            ('Submit the inspection form three days before the session starts.',
             'Check the emergency lights three days before the doors open.'),
            ('Submit the inspection form three days before the session starts.',
             'Submit the inspection form for three days. Check the lights before the doors open.'),
            ('Submit the inspection form three days before the session starts.',
             'Submit the inspection form at least three days before the deadline.'),
            ('Submit the inspection form three days before the session starts.',
             'Submit the inspection form three days after the doors open.'),
            ('Submit the inspection form three days before the session starts.',
             'Submit the inspection form three working days before the doors open.'),
            ('Submit the inspection form at least three days early.',
             'Submit the inspection form three days before the doors open.'),
            ('Submit the inspection form three days before the doors open.',
             'Submit the inspection form three days before the opening of the doors.'),
            ('Submit the inspection form three days before registration starts.',
             'Submit the inspection form three days before registration opens.'),
            ('须提前三个工作日提交清单。', '须在场馆开门前三个工作日提交清单。'),
            ('须在演出开始前三个工作日提交清单。', '更新告示后应保留三份，场馆开门后再张贴。'),
            ('须在演出开始后三天提交清单。', '提交清单，三天后场馆开门。'),
        ]
        for claim, quote in pairs:
            with self.subTest(claim=claim, quote=quote):
                self.assertEqual(deadline_anchor_diagnostics(field_with_citations(claim, quote)),
                                 {'conflicts': [], 'candidates': []})

    def test_one_citation_with_alternate_anchors_cannot_support_a_conflict(self):
        claim = 'Submit the inspection form three days before the session starts.'
        quote = ('Submit the inspection form three days before the doors open, '
                 'or before the session starts.')
        self.assertEqual(deadline_anchor_conflicts(field_with_citations(claim, quote)), [])

    def test_alternative_citations_suppress_conflicts_and_candidates(self):
        matching = 'Submit the inspection form three days before the session starts.'
        different = 'Submit the inspection form three days before the doors open.'
        unknown = 'Submit the inspection form three days before the inspection finishes.'
        paraphrased = 'Send the inspection form three days before the session begins.'
        claims = (matching, '检查表在会议开始前三天提交。')

        for claim in claims:
            for alternative in (matching, unknown, paraphrased):
                with self.subTest(claim=claim, alternative=alternative):
                    self.assertEqual(deadline_anchor_diagnostics(field_with_citations(claim, different, alternative)),
                                     {'conflicts': [], 'candidates': []})

    def test_repeated_citations_produce_a_bounded_number_of_spans(self):
        claim = 'Submit the inspection form three days before the session starts.'
        quote = 'Submit the inspection form 72 hours before the doors open.'
        record, = deadline_anchor_conflicts(field_with_citations(claim, *([quote] * 12)))

        self.assertGreater(len(record['evidence']), 0)
        self.assertLessEqual(len(record['evidence']), 4)
        self.assertEqual(record['evidence'][0]['source_id'], 'citation-1')

    def test_a_long_citation_with_the_same_duration_does_not_imply_a_conflict(self):
        claim = 'Submit the inspection form three days before the session starts.'
        contrast = 'Submit the inspection form 72 hours before the doors open.'
        long = 'For the grouped intake of regional inspection forms ' * 8 + claim
        self.assertEqual(deadline_anchor_conflicts(field_with_citations(claim, contrast, long)), [])
