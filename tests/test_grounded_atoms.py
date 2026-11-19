import unittest

from decision_memory.grounded_atoms import atoms, present


class DurationEvidenceTests(unittest.TestCase):
    def test_shift_log_does_not_introduce_a_next_day_requirement(self):
        self.assertEqual(atoms('班次日志以确认事件为准。'), [])
        self.assertEqual(atoms('依次日志归档。'), [])
        self.assertEqual(atoms('次日归档班次日志。'), [
            {'text': '次日', 'keys': ['relative-calendar-day:next']},
        ])

    def test_spelled_english_units_support_the_same_chinese_duration(self):
        for claim, source in [('21 天', 'Available for 21 calendar days after closure.'),
                              ('120 分钟', 'Entries remain visible for 2 hours.'),
                              ('14 天', 'The temporary hold is 2 weeks.')]:
            with self.subTest(claim=claim, source=source):
                self.assertTrue(present(atoms(claim)[0], source))

    def test_a_bare_number_or_business_day_cannot_supply_a_calendar_duration(self):
        duration, = atoms('21 天')
        for source in ('21 business days', '21 hours', '21 completed packets', '21 business days or later'):
            with self.subTest(source=source):
                self.assertFalse(present(duration, source))

    def test_written_durations_match_compound_units_and_equivalent_hours(self):
        pairs = [('七天', 'This is a seven-calendar-day minimum.'),
                 ('二十四小时', 'The notice has a one-day validity period.'),
                 ('72 小时', 'Apply the three-calendar-day cutoff.'),
                 ('四天', 'A ninety-six-hour window starts at approval.')]
        for claim, source in pairs:
            with self.subTest(claim=claim, source=source):
                found = atoms(claim)
                self.assertEqual(len(found), 1)
                self.assertTrue(present(found[0], source))

    def test_written_duration_does_not_accept_business_days_or_a_number_suffix(self):
        found = atoms('七天')
        self.assertEqual(len(found), 1)
        for source in ('seven business days', 'seven approvals', 'one hundred and seven days', '一百零七天'):
            with self.subTest(source=source):
                self.assertFalse(present(found[0], source))

    def test_workday_durations_share_a_count_without_becoming_elapsed_time(self):
        for claim in ('三个工作日', '3 个工作日', 'three-business-day', '3 working days'):
            with self.subTest(claim=claim):
                found = atoms(claim)
                self.assertEqual(len(found), 1)
                self.assertIn('business-days:3', found[0]['keys'])
                self.assertTrue(present(found[0], 'Allow three workdays on the office calendar.'))
                for source in ('3 days', '72 hours', '3 applications', 'four working days'):
                    self.assertFalse(present(found[0], source))
        self.assertFalse(present(atoms('72 小时')[0], 'two hundred and three business days'))

    def test_workday_binding_rejects_a_fraction_decimal_suffix_and_negative_count(self):
        duration, = atoms('三个工作日')
        for source in ('1 / 3 个工作日', '二点三个工作日', '1 point three working days', '−3 working days'):
            with self.subTest(source=source):
                self.assertFalse(present(duration, source))

    def test_explicit_elapsed_hours_and_natural_days_bind_without_borrowing_workdays(self):
        for claim in ('72 小时', '3个自然日', '七十二个连续小时'):
            with self.subTest(claim=claim):
                found = atoms(claim)
                self.assertEqual(len(found), 1)
                self.assertTrue(present(found[0], 'Close exactly 72 elapsed hours before the launch.'))
                self.assertFalse(present(found[0], 'Three business days on the local calendar.'))

    def test_next_workday_is_a_relative_anchor_not_a_one_day_duration(self):
        found = atoms('从下一个工作日开门时起算')
        self.assertEqual(len(found), 1)
        self.assertIn('relative-business-day:next', found[0]['keys'])
        self.assertTrue(present(found[0], 'Start at opening on the next business day.'))
        self.assertFalse(present(found[0], 'Allow one business day.'))
        self.assertTrue(present(atoms('尚剩下一个工作日')[0], 'Allow one business day.'))

    def test_business_hours_use_their_own_unit_and_support_chinese_counts(self):
        found = atoms('批准后两个工作小时内')
        self.assertEqual(len(found), 1)
        self.assertTrue(present(found[0], 'within two business hours of approval'))
        self.assertFalse(present(found[0], 'within two elapsed hours of approval'))
        self.assertFalse(present(found[0], 'within two business days of approval'))

    def test_relative_calendar_day_does_not_create_an_elapsed_duration(self):
        anchor, = atoms('下一个自然日开门')
        self.assertEqual(anchor['keys'], ['relative-calendar-day:next'])
        self.assertTrue(present(anchor, 'at the next calendar day'))
        duration, = atoms('一个自然日')
        self.assertFalse(present(duration, '下一个自然日开门'))

    def test_unquantified_business_day_does_not_supply_duration(self):
        duration, = atoms('一个工作日内')
        self.assertFalse(present(duration, 'Start at opening on a business day.'))
        self.assertTrue(present(duration, 'Complete the form within a business day.'))

    def test_half_and_mixed_working_durations_have_exact_typed_values(self):
        for claim, quote, key in [('半个工作日', 'Allow half a workday.', 'business-days:0.5'),
                                  ('一个半工作小时', 'Allow one and a half business hours.', 'business-hours:1.5')]:
            with self.subTest(claim=claim):
                duration, = atoms(claim)
                self.assertIn(key, duration['keys'])
                self.assertTrue(present(duration, quote))
        duration, = atoms('一个工作小时')
        self.assertFalse(present(duration, 'Allow half a business hour.'))

    def test_common_relative_day_spellings_keep_the_direction_and_day_type(self):
        for claim in ('次一工作日开门', '次工作日开门', '下一工作日开门'):
            with self.subTest(claim=claim):
                anchor, = atoms(claim)
                self.assertEqual(anchor['keys'], ['relative-business-day:next'])
                self.assertTrue(present(anchor, 'Open the next workday.'))
                self.assertFalse(present(anchor, 'Open the previous workday.'))
        anchor, = atoms('同一工作日内处理')
        self.assertTrue(present(anchor, 'Process on the same business day.'))
        self.assertFalse(present(anchor, 'Process within one business day.'))

    def test_fraction_suffix_is_not_a_whole_workday_count(self):
        duration, = atoms('三个工作日')
        for quote in ('Allow 1 ⁄ 3 working days.', '保留三分之三个工作日'):
            with self.subTest(quote=quote):
                self.assertFalse(present(duration, quote))

    def test_changing_to_another_date_does_not_imply_a_day_of_delay(self):
        date, = atoms('可调整到另一天')
        self.assertEqual(date['keys'], ['relative-calendar-day:another'])
        self.assertTrue(present(date, 'Move the booking to another day.'))
        self.assertFalse(present(date, 'Wait one day before confirming.'))
        duration, = atoms('等待一天')
        self.assertFalse(present(duration, '可调整到另一天'))

    def test_relative_date_supports_local_day_and_preserves_direction(self):
        previous, = atoms('上一本地自然日')
        self.assertTrue(present(previous, 'Use the previous local calendar day.'))
        self.assertFalse(present(previous, 'Use the next local calendar day.'))
        following, = atoms('次日归档')
        self.assertTrue(present(following, 'Archive the following day.'))

    def test_advance_notice_and_each_occurrence_do_not_become_relative_dates(self):
        duration, = atoms('需要至少提前一天提交预约')
        self.assertTrue(present(duration, 'Submit the booking at least one day in advance.'))
        self.assertEqual(atoms('每次日终汇总必须保留'), [])

    def test_actual_rescheduling_language_binds_the_new_date(self):
        anchor, = atoms('改期到另一天')
        self.assertTrue(present(anchor, 'Reschedule the booking for another day.'))
        self.assertTrue(present(anchor, 'Choose another day for the booking.'))
        self.assertFalse(present(anchor, 'Keep the existing booking for another day.'))

    def test_singular_article_in_english_duration_supports_one_unit(self):
        hour, = atoms('约一个小时之前')
        self.assertTrue(present(hour, 'The technicians start about an hour ahead of setup.'))
        self.assertFalse(present(hour, 'The technicians start half an hour ahead of setup.'))
        minute, = atoms('一分钟内')
        self.assertTrue(present(minute, 'Finish in a minute.'))
        self.assertFalse(present(minute, 'Finish within a working hour.'))
