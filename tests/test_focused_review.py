import json
import unittest

from decision_memory.focused_review import compact_review_hints, field_issues


def field(text, *quotes):
    return {'text': text, 'evidence': [{'source_id': f'D{index:05}', 'quote': quote}
                                       for index, quote in enumerate(quotes, 1)]}


class FocusedReviewTests(unittest.TestCase):
    def test_notification_is_not_evidence_of_confirmation(self):
        claim = field('责任范围已与项目主管确认过。', '范围我跟项目主管说过了。人员还未分配。')
        [issue] = field_issues(claim)
        self.assertEqual(issue['check'], 'confirmation')
        [witness] = issue['evidence']
        original = claim['evidence'][0]['quote']
        self.assertEqual(original[witness['start']:witness['end']], '范围我跟项目主管说过了。')
        self.assertEqual(field_issues(field(claim['text'], original, '项目主管明确确认该范围。')), [])

    def test_an_unrelated_only_does_not_support_access_exclusivity(self):
        claim = '仅维护员和当班主管可访问归档目录。'
        positive = 'Keep only the latest approved build. The maintainer and shift lead can access the archive.'
        [issue] = field_issues(field(claim, positive))
        self.assertEqual(issue['check'], 'exclusive_access')
        [witness] = issue['evidence']
        self.assertNotIn('only', positive[witness['start']:witness['end']])
        exclusive = 'Only the maintainer and the shift lead can access the archive.'
        self.assertEqual(field_issues(field(claim, exclusive)), [])
        unrelated = 'Only the owner may edit the draft. The maintainer and shift lead can access the archive.'
        self.assertEqual([row['check'] for row in field_issues(field(claim, unrelated))], ['exclusive_access'])

    def test_negative_approval_does_not_suppress_a_confirmation_cue(self):
        claim = 'The team confirmed the change with the supervisor.'
        source = 'The team notified the supervisor. The change was not approved.'
        self.assertEqual([row['check'] for row in field_issues(field(claim, source))], ['confirmation'])

    def test_one_sufficient_condition_does_not_establish_necessity(self):
        only_when = '仅当工单退回分部重新派发时才开启新批次。'
        source = '原批次内只发一条消息。工单退回分部后重新派发，须开启新批次。'
        self.assertEqual([row['check'] for row in field_issues(field(only_when, source))], ['only_if'])
        self.assertEqual(field_issues(field('仅当复核完成时才结项。', '截图删除后会释放空间。')), [])

    def test_offered_availability_does_not_establish_a_fixed_time(self):
        claim = '现场复核安排在交接前十分钟。'
        proposal = 'Which checks should I book? Ten minutes before handover we can get the crew to look.'
        self.assertEqual([row['check'] for row in field_issues(field(claim, proposal))], ['tentative_slot'])
        self.assertEqual(field_issues(field(claim, proposal, 'The approved test slot is ten minutes before handover.')), [])

    def test_compaction_keeps_missing_literals_and_local_risks(self):
        draft = {'items': [{'reason': field('责任范围已与项目主管确认过。', '范围我跟项目主管说过了。'),
                            'conclusion': field('目标'), 'boundary': field('未说明')}], 'open_questions': []}
        baseline = {'fields': [{'item_index': 1, 'field': 'reason', 'missing_symbols': [],
                               'missing_atoms': [{'text': '30', 'candidate_source_ids': ['A00001']}],
                               'candidate_source_ids': ['D00010', 'D00012', 'A00005'],
                               'later_related_source_ids': [], 'absent_from_entire_input': [],
                               'unstated_requires_check': False}],
                    'unreferenced_artifact_candidates': [f'A{i:05}' for i in range(30)],
                    'unreferenced_salient_total': 30, 'semantic_certification': False}
        condensed = compact_review_hints(draft, baseline)
        self.assertEqual(condensed['fields'][0]['missing_atoms'], baseline['fields'][0]['missing_atoms'])
        self.assertEqual(condensed['field_issues'][0]['field'], 'reason')
        self.assertEqual(condensed['field_issues'][0]['check'], 'confirmation')
        self.assertLess(len(json.dumps(condensed)), len(json.dumps(baseline)))

    def test_keeps_candidates_beyond_ordinary_cap_when_they_resolve_literals(self):
        signal = {'fields': [{'item_index': 1, 'field': 'boundary', 'candidate_source_ids': ['A0', 'A1', 'A2'],
                              'absent_from_entire_input': ['MISSING_API'],
                              'missing_atoms': [{'text': '4 天', 'candidate_source_ids': ['D1', 'D2', 'D3']}]}],
                  'unreferenced_artifact_candidates': []}
        result = compact_review_hints({'items': [], 'open_questions': []}, signal)
        self.assertEqual(result['fields'][0]['absent_from_entire_input'], ['MISSING_API'])
        self.assertEqual(result['fields'][0]['missing_atoms'][0]['candidate_source_ids'], ['D1', 'D2', 'D3'])

    def test_a_direct_contradiction_keeps_a_slot_even_when_weaker_signals_fill_the_cap(self):
        notice = field('The team confirmed the change.', 'The team notified the supervisor.')
        timing = field('Submit the form three days before the session starts.',
                       'Submit the form 72 hours before the doors open.')
        blank = field('未说明')
        draft = {'items': [{'conclusion': blank, 'reason': notice, 'boundary': blank}] * 24
                          + [{'conclusion': timing, 'reason': blank, 'boundary': blank}]}
        hints = {'fields': [], 'unreferenced_artifact_candidates': []}
        result = compact_review_hints(draft, hints)
        self.assertEqual((result['field_issues'][0]['check'], len(result['field_issues'])), ('deadline_anchor', 24))


if __name__ == '__main__':
    unittest.main()
