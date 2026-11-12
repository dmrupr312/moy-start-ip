"""Тесты: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import checklist, okved, rules  # noqa: E402
from agent.core import Agent, extract_profile  # noqa: E402


class TestRules(unittest.TestCase):
    def test_self_contrib_2026(self):
        self.assertEqual(rules.self_contrib(300_000, 2026), 57_390)
        self.assertAlmostEqual(rules.self_contrib(1_300_000, 2026), 57_390 + 10_000)
        # потолок 1%-взноса
        self.assertAlmostEqual(rules.self_contrib(10**10, 2026), 57_390 + 321_818)

    def test_self_contrib_2027(self):
        self.assertEqual(rules.self_contrib(0, 2027), 61_154)

    def test_ndfl_progressive(self):
        self.assertAlmostEqual(rules.ndfl_progressive(2_400_000), 312_000)
        self.assertAlmostEqual(rules.ndfl_progressive(5_000_000), 312_000 + 390_000)

    def test_npd(self):
        r = rules.calc_npd(rules.Profile(income=1_000_000, b2c_share=1.0))
        self.assertTrue(r.available)
        self.assertAlmostEqual(r.total, 40_000 - 10_000)

    def test_npd_blocked(self):
        r = rules.calc_npd(rules.Profile(income=3_000_000))
        self.assertFalse(r.available)
        r = rules.calc_npd(rules.Profile(income=1_000_000, employees=1))
        self.assertFalse(r.available)

    def test_usn6_reduced_by_contrib(self):
        r = rules.calc_usn6(rules.Profile(income=1_000_000, year=2026))
        contrib = 57_390 + 7_000
        # взносы (64 390) больше налога (60 000) — налог уменьшается до нуля
        self.assertAlmostEqual(r.tax, 0)
        self.assertAlmostEqual(r.total, contrib)
        r = rules.calc_usn6(rules.Profile(income=3_000_000, year=2026))
        contrib = 57_390 + 27_000
        self.assertAlmostEqual(r.tax, 180_000 - contrib)
        self.assertAlmostEqual(r.total, 180_000)

    def test_usn_vat_over_20m(self):
        r = rules.calc_usn6(rules.Profile(income=30_000_000, year=2026))
        self.assertGreater(r.vat, 0)
        self.assertAlmostEqual(r.vat, 30_000_000 * 0.05 / 1.05)

    def test_psn_limit_2027(self):
        r = rules.calc_psn(rules.Profile(income=18_000_000, patent_cost=100_000, year=2027))
        self.assertFalse(r.available)
        r = rules.calc_psn(rules.Profile(income=18_000_000, patent_cost=100_000, year=2026))
        self.assertTrue(r.available)

    def test_compare_best_npd(self):
        res = rules.compare_regimes({"income": 1_200_000, "year": 2027})
        self.assertEqual(res["best"]["code"], "npd")

    def test_compare_ausn_region_off(self):
        res = rules.compare_regimes({"income": 5_000_000, "ausn_region": False})
        self.assertNotIn("ausn8", res["ranking"])


class TestOkved(unittest.TestCase):
    def test_search(self):
        self.assertEqual(okved.search_okved("маникюр на дому")[0]["code"], "96.02")
        self.assertEqual(okved.search_okved("разработка сайтов")[0]["code"], "62.01")
        self.assertEqual(okved.search_okved("репетитор по математике")[0]["code"], "85.41")
        self.assertEqual(okved.search_okved("грузоперевозки на газели")[0]["code"], "49.41")

    def test_format(self):
        self.assertTrue(okved.validate_okved_format("62.01"))
        self.assertTrue(okved.validate_okved_format("85.41.9"))
        self.assertFalse(okved.validate_okved_format("62"))


class TestChecklist(unittest.TestCase):
    def test_inn(self):
        self.assertTrue(checklist.inn_valid("500100732259"))
        self.assertFalse(checklist.inn_valid("500100732258"))

    def test_precheck_errors(self):
        res = checklist.precheck({"fio": "Иванов", "okved": ["62"], "passport": "12"})
        self.assertEqual(res["refusal_risk"], "высокий")
        self.assertGreaterEqual(len(res["errors"]), 3)

    def test_precheck_ok(self):
        res = checklist.precheck({"fio": "Иванова Анна Сергеевна", "okved": ["96.02"],
                                  "passport": "4510 123456", "email": "a@b.ru",
                                  "birth_date": "1995-05-01"})
        self.assertEqual(res["refusal_risk"], "низкий")


class TestDialog(unittest.TestCase):
    def test_extract(self):
        p = {}
        extract_profile("доход 150 тысяч в месяц, без сотрудников, клиенты физлица", p)
        self.assertEqual(p["income"], 1_800_000)
        self.assertEqual(p["employees"], 0)
        self.assertEqual(p["b2c_share"], 1.0)
        p = {}
        extract_profile("оборот 30 млн в год, закупка 18 млн, 3 сотрудника", p)
        self.assertEqual(p["income"], 30_000_000)
        self.assertEqual(p["expenses"], 18_000_000)
        self.assertEqual(p["employees"], 3)

    def test_offline_dialog(self):
        a = Agent(llm=None)
        r = a.handle(None, "Делаю маникюр на дому")
        self.assertIn("96.02", r["reply"])
        r = a.handle(r["session_id"], "около 120 тысяч в месяц, без сотрудников")
        self.assertIsNotNone(r["calc"])
        self.assertEqual(r["calc"]["best"]["code"], "npd")

    def test_llm_tool_protocol(self):
        class FakeLLM:
            name = "fake"
            def __init__(self):
                self.n = 0
            def chat(self, messages, temperature=0.2):
                self.n += 1
                if self.n == 1:
                    return '{"tool": "search_okved", "args": {"query": "разработка сайтов"}}'
                if self.n == 2:
                    return '```json\n{"tool": "compare_regimes", "args": {"income": 3600000}}\n```'
                return "Готово: смотрите расчёт."
        a = Agent(llm=FakeLLM())
        r = a.handle(None, "делаю сайты, 300 тысяч в месяц")
        self.assertEqual([t["tool"] for t in r["trace"]], ["search_okved", "compare_regimes"])
        self.assertEqual(r["reply"], "Готово: смотрите расчёт.")
        self.assertEqual(r["okved"][0]["code"], "62.01")


if __name__ == "__main__":
    unittest.main()


class TestAnalytics(unittest.TestCase):
    def test_full_and_switch(self):
        from agent import analytics
        an = analytics.full({"income": 1_440_000, "b2c_share": 1.0})
        self.assertEqual(an["kpis"]["best_code"], "npd")
        self.assertAlmostEqual(an["kpis"]["saving_vs_usn6"], 86_400 - 47_600)
        # после лимита 2,4 млн НПД уходит, лучшим становится УСН 6%
        self.assertEqual(an["sensitivity"]["switches"][0]["to"], "usn6")
        self.assertTrue(2_400_000 <= an["sensitivity"]["switches"][0]["income"] <= 2_600_000)

    def test_calendar_sums(self):
        from agent import analytics, rules
        calc = rules.compare_regimes({"income": 3_000_000, "year": 2026})
        cal = analytics.calendar(calc, "usn6")
        usn = next(r for r in calc["results"] if r["code"] == "usn6")
        self.assertAlmostEqual(sum(i["amount"] for i in cal["items"]), usn["total"], delta=5)

    def test_service(self):
        from agent import analytics
        s = analytics.service()
        self.assertEqual(len(s["personas"]), 5)
        for p in s["personas"]:
            self.assertLessEqual(p["best"], p["osno"])
