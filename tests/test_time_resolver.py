"""
Tests for time_resolver.resolve_relative_time
固定 now = 2026-02-20 (Friday) 以便断言确定性结果
"""

from datetime import datetime

import pytest

from astrbot_plugin_livingmemory.core.utils.time_resolver import resolve_relative_time

# 固定时间点：2026-02-20 Friday
NOW = datetime(2026, 2, 20, 14, 30, 0)


# 日（简体中文） 
class TestDaySimplifiedChinese:
    def test_today(self):
        assert resolve_relative_time("今天开会", NOW) == "今天(2026-02-20)开会"

    def test_today_alt(self):
        assert resolve_relative_time("今日有事", NOW) == "今日(2026-02-20)有事"

    def test_yesterday(self):
        assert resolve_relative_time("昨天下雨", NOW) == "昨天(2026-02-19)下雨"

    def test_yesterday_alt(self):
        assert resolve_relative_time("昨日很冷", NOW) == "昨日(2026-02-19)很冷"

    def test_tomorrow(self):
        assert resolve_relative_time("明天下午开会", NOW) == "明天(2026-02-21)下午开会"

    def test_tomorrow_alt(self):
        assert resolve_relative_time("明日出发", NOW) == "明日(2026-02-21)出发"

    def test_day_before_yesterday(self):
        assert resolve_relative_time("前天很忙", NOW) == "前天(2026-02-18)很忙"

    def test_day_before_yesterday_alt(self):
        assert resolve_relative_time("前日有事", NOW) == "前日(2026-02-18)有事"

    def test_day_after_tomorrow(self):
        assert resolve_relative_time("后天放假", NOW) == "后天(2026-02-22)放假"

    def test_day_after_tomorrow_alt(self):
        assert resolve_relative_time("后日出门", NOW) == "后日(2026-02-22)出门"

    def test_three_days_ago(self):
        assert resolve_relative_time("大前天发生了什么", NOW) == "大前天(2026-02-17)发生了什么"

    def test_three_days_later(self):
        assert resolve_relative_time("大后天见", NOW) == "大后天(2026-02-23)见"


# 日（繁体中文） 
class TestDayTraditionalChinese:
    def test_day_after_tomorrow_traditional(self):
        assert resolve_relative_time("後天見面", NOW) == "後天(2026-02-22)見面"

    def test_day_after_tomorrow_alt_traditional(self):
        assert resolve_relative_time("後日再說", NOW) == "後日(2026-02-22)再說"

    def test_three_days_later_traditional(self):
        assert resolve_relative_time("大後天出發", NOW) == "大後天(2026-02-23)出發"


# 日（英文） 
class TestDayEnglish:
    def test_today(self):
        assert resolve_relative_time("today is good", NOW) == "today(2026-02-20) is good"

    def test_yesterday(self):
        assert resolve_relative_time("yesterday was fun", NOW) == "yesterday(2026-02-19) was fun"

    def test_tomorrow(self):
        assert resolve_relative_time("see you tomorrow", NOW) == "see you tomorrow(2026-02-21)"

    def test_the_day_before_yesterday(self):
        result = resolve_relative_time("the day before yesterday was cold", NOW)
        assert result == "the day before yesterday(2026-02-18) was cold"

    def test_the_day_after_tomorrow(self):
        result = resolve_relative_time("the day after tomorrow we leave", NOW)
        assert result == "the day after tomorrow(2026-02-22) we leave"

    def test_case_insensitive(self):
        assert resolve_relative_time("Today is Friday", NOW) == "Today(2026-02-20) is Friday"
        assert resolve_relative_time("TOMORROW we go", NOW) == "TOMORROW(2026-02-21) we go"


#  周（中文） 
# 2026-02-20 是周五，week_start = 2026-02-16 (周一)
class TestWeekChinese:
    def test_last_week(self):
        assert resolve_relative_time("上周开会", NOW) == "上周(2026-02-09起)开会"

    def test_last_week_alt(self):
        assert resolve_relative_time("上星期很忙", NOW) == "上星期(2026-02-09起)很忙"

    def test_last_week_traditional(self):
        assert resolve_relative_time("上週有事", NOW) == "上週(2026-02-09起)有事"

    def test_last_week_libai(self):
        assert resolve_relative_time("上礼拜出差", NOW) == "上礼拜(2026-02-09起)出差"

    def test_last_week_libai_traditional(self):
        assert resolve_relative_time("上禮拜開會", NOW) == "上禮拜(2026-02-09起)開會"

    def test_next_week(self):
        assert resolve_relative_time("下周见", NOW) == "下周(2026-02-23起)见"

    def test_next_week_alt(self):
        assert resolve_relative_time("下星期出发", NOW) == "下星期(2026-02-23起)出发"

    def test_this_week(self):
        assert resolve_relative_time("这周有空", NOW) == "这周(2026-02-16起)有空"

    def test_this_week_traditional(self):
        assert resolve_relative_time("這週很忙", NOW) == "這週(2026-02-16起)很忙"

    def test_this_week_benzhou(self):
        assert resolve_relative_time("本周计划", NOW) == "本周(2026-02-16起)计划"

    def test_this_week_benzhou_traditional(self):
        assert resolve_relative_time("本週計劃", NOW) == "本週(2026-02-16起)計劃"


# 周（英文） 
class TestWeekEnglish:
    def test_last_week(self):
        assert resolve_relative_time("last week was busy", NOW) == "last week(2026-02-09起) was busy"

    def test_next_week(self):
        assert resolve_relative_time("next week we meet", NOW) == "next week(2026-02-23起) we meet"

    def test_this_week(self):
        assert resolve_relative_time("this week is fine", NOW) == "this week(2026-02-16起) is fine"


# 月（中文） 
class TestMonthChinese:
    def test_last_month(self):
        assert resolve_relative_time("上个月很忙", NOW) == "上个月(2026-01)很忙"

    def test_last_month_traditional(self):
        assert resolve_relative_time("上個月出差", NOW) == "上個月(2026-01)出差"

    def test_next_month(self):
        assert resolve_relative_time("下个月放假", NOW) == "下个月(2026-03)放假"

    def test_next_month_traditional(self):
        assert resolve_relative_time("下個月開學", NOW) == "下個月(2026-03)開學"

    def test_this_month(self):
        assert resolve_relative_time("这个月有事", NOW) == "这个月(2026-02)有事"

    def test_this_month_traditional(self):
        assert resolve_relative_time("這個月很忙", NOW) == "這個月(2026-02)很忙"

    def test_this_month_benyue(self):
        assert resolve_relative_time("本月目标", NOW) == "本月(2026-02)目标"


# 月（英文） 
class TestMonthEnglish:
    def test_last_month(self):
        assert resolve_relative_time("last month was great", NOW) == "last month(2026-01) was great"

    def test_next_month(self):
        assert resolve_relative_time("next month we travel", NOW) == "next month(2026-03) we travel"

    def test_this_month(self):
        assert resolve_relative_time("this month is busy", NOW) == "this month(2026-02) is busy"


# 年（中文） 
class TestYearChinese:
    def test_last_year(self):
        assert resolve_relative_time("去年发生了很多事", NOW) == "去年(2025)发生了很多事"

    def test_this_year(self):
        assert resolve_relative_time("今年目标", NOW) == "今年(2026)目标"

    def test_next_year(self):
        assert resolve_relative_time("明年计划", NOW) == "明年(2027)计划"

    def test_two_years_ago(self):
        assert resolve_relative_time("前年的事", NOW) == "前年(2024)的事"

    def test_two_years_later(self):
        assert resolve_relative_time("后年再说", NOW) == "后年(2028)再说"

    def test_two_years_later_traditional(self):
        assert resolve_relative_time("後年再說", NOW) == "後年(2028)再說"


# 年（英文） 
class TestYearEnglish:
    def test_last_year(self):
        assert resolve_relative_time("last year was tough", NOW) == "last year(2025) was tough"

    def test_this_year(self):
        assert resolve_relative_time("this year is better", NOW) == "this year(2026) is better"

    def test_next_year(self):
        assert resolve_relative_time("next year we move", NOW) == "next year(2027) we move"


# 边界与特殊情况 
class TestEdgeCases:
    def test_no_match(self):
        """无相对时间词的文本应原样返回"""
        text = "这是一段普通文本，没有时间词"
        assert resolve_relative_time(text, NOW) == text

    def test_empty_string(self):
        assert resolve_relative_time("", NOW) == ""

    def test_multiple_keywords_in_one_sentence(self):
        """同一句中出现多个时间词，都应被标注"""
        result = resolve_relative_time("昨天和今天都很忙，明天也是", NOW)
        assert "昨天(2026-02-19)" in result
        assert "今天(2026-02-20)" in result
        assert "明天(2026-02-21)" in result

    def test_mixed_chinese_english(self):
        """中英文混合"""
        result = resolve_relative_time("today很忙，明天有空", NOW)
        assert "today(2026-02-20)" in result
        assert "明天(2026-02-21)" in result

    def test_already_annotated_skipped(self):
        """已经标注过的不应重复标注"""
        text = "明天(2026-02-21)下午开会"
        assert resolve_relative_time(text, NOW) == text

    def test_long_phrase_priority_over_short(self):
        """'大前天' 不应被拆成 '前天' 匹配"""
        result = resolve_relative_time("大前天的事", NOW)
        assert "大前天(2026-02-17)" in result
        # 不应出现单独的 "前天" 标注
        assert "前天(" not in result.replace("大前天(", "")

    def test_long_english_phrase_priority(self):
        """'the day before yesterday' 不应被拆成 'yesterday' 匹配"""
        result = resolve_relative_time("the day before yesterday was nice", NOW)
        assert "the day before yesterday(2026-02-18)" in result
        # yesterday 不应被单独匹配
        count = result.count("(2026-")
        assert count == 1

    def test_english_extra_spaces(self):
        """英文多词组中间有多个空格也应匹配"""
        result = resolve_relative_time("last  week was fun", NOW)
        assert "(2026-02-09起)" in result

    def test_month_boundary_december(self):
        """12月的下个月应该是次年1月"""
        dec_now = datetime(2026, 12, 15)
        result = resolve_relative_time("下个月放假", dec_now)
        assert "下个月(2027-01)" in result

    def test_month_boundary_january(self):
        """1月的上个月应该是前一年12月"""
        jan_now = datetime(2026, 1, 10)
        result = resolve_relative_time("上个月很冷", jan_now)
        assert "上个月(2025-12)" in result

    def test_week_start_monday(self):
        """确认 week_start 是周一"""
        # 2026-02-20 是周五，week_start 应该是 2026-02-16 (周一)
        result = resolve_relative_time("本周计划", NOW)
        assert "本周(2026-02-16起)" in result

    def test_different_now_value(self):
        """使用不同的 now 值验证计算正确"""
        other_now = datetime(2025, 7, 1, 10, 0, 0)
        assert resolve_relative_time("明天见", other_now) == "明天(2025-07-02)见"
        assert resolve_relative_time("去年的事", other_now) == "去年(2024)的事"

    def test_multiple_same_keyword(self):
        """同一个关键词出现多次"""
        result = resolve_relative_time("今天开会，今天也要写代码", NOW)
        assert result.count("今天(2026-02-20)") == 2

    def test_keyword_at_start_and_end(self):
        """关键词在句首和句尾"""
        assert resolve_relative_time("今天", NOW) == "今天(2026-02-20)"
        assert resolve_relative_time("去吧明天", NOW) == "去吧明天(2026-02-21)"