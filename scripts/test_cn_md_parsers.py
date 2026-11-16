# -*- coding: utf-8 -*-
"""cn_md_parsers 单测：对语料全部 10+10 期逐期跑并断言关键字段。

运行：cd /home/ubuntu/claude-workspace/gvrm-cn-board && python3 -m unittest discover -v
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cn_md_parsers import parse_news, parse_review  # noqa: E402

REVIEW_CSV = "/data/fmdata/store/data/soochow/soochow_daily_review.csv"
NEWS_CSV = "/data/fmdata/store/data/soochow/soochow_multi_news.csv"

REVIEW_FIELDS = [
    "review_date", "core_judgement", "limit_up", "limit_down",
    "money_effect", "max_boards", "total_amount_yi", "amount_delta_yi",
    "index_moves", "ladder", "lhb_inst_buys", "lhb_inst_sells",
    "regulatory", "watch_next",
]
NEWS_FIELDS = [
    "news_date", "catalysts", "asset_panorama", "watchlist",
    "exec_summary_paras",
]


def _load(csv, date_col="date"):
    df = pd.read_csv(csv)
    return {row[date_col]: row["raw"] for _, row in df.iterrows()}


REVIEWS = _load(REVIEW_CSV)
NEWS = _load(NEWS_CSV)


def _hit(d, f):
    return d is not None and d.get(f) not in (None, [], {})


class TestParseReviewAllPeriods(unittest.TestCase):
    """全部 10 期复盘逐期校验 + 命中率汇总表。"""

    @classmethod
    def setUpClass(cls):
        cls.parsed = {d: parse_review(md) for d, md in REVIEWS.items()}

    def test_10_periods_present(self):
        self.assertEqual(len(REVIEWS), 10, "语料应为 10 期复盘")

    def test_anchor_fields_every_period(self):
        for d, p in self.parsed.items():
            self.assertIsNotNone(p, "%s 解析整体失败" % d)
            for f in ("review_date", "limit_up", "limit_down", "core_judgement"):
                self.assertTrue(_hit(p, f), "%s 字段 %s 为空" % (d, f))

    def test_numeric_fields_sane_every_period(self):
        for d, p in self.parsed.items():
            self.assertIsInstance(p["limit_up"], int)
            self.assertGreaterEqual(p["limit_up"], 0)
            self.assertGreaterEqual(p["limit_down"], 0)
            self.assertGreater(p["total_amount_yi"], 5000)
            self.assertLess(p["total_amount_yi"], 50000)
            self.assertIsInstance(p["index_moves"], dict)
            self.assertEqual(set(p["index_moves"]),
                             {"上证指数", "深证成指", "创业板指"})
            for x in p["ladder"]:
                self.assertLessEqual(x["boards"], p["max_boards"],
                                     "%s 梯队 %s 超过当日最高板" % (d, x["stock"]))

    def test_hit_rate_summary_table(self):
        print("\n===== 复盘逐期字段命中率 =====")
        header = "%-12s" % "date" + " ".join(f[:7] for f in REVIEW_FIELDS) + "  coverage"
        print(header)
        for d in sorted(REVIEWS, reverse=True):
            p = self.parsed[d]
            marks = "".join("." if _hit(p, f) else "X" for f in REVIEW_FIELDS)
            print("%-12s %s  %s" % (d, marks, p["parse_coverage"]))
        for f in REVIEW_FIELDS:
            n = sum(1 for p in self.parsed.values() if _hit(p, f))
            print("字段 %-16s 命中 %d/10" % (f, n))


class TestReview20261106(unittest.TestCase):
    """指定值断言：2026-11-06 期。"""

    @classmethod
    def setUpClass(cls):
        cls.p = parse_review(REVIEWS["2026-11-06"])

    def test_scalars(self):
        p = self.p
        self.assertEqual(p["review_date"], "2026-11-06")
        self.assertEqual(p["limit_up"], 32)
        self.assertEqual(p["limit_down"], 15)
        self.assertEqual(p["max_boards"], 5)
        self.assertAlmostEqual(p["money_effect"], -0.06, places=6)
        self.assertAlmostEqual(p["total_amount_yi"], 16600.0, places=6)
        self.assertAlmostEqual(p["amount_delta_yi"], -4600.0, places=6)

    def test_core_judgement(self):
        self.assertIn("核心判断", "核心判断")  # sanity
        self.assertTrue(self.p["core_judgement"].startswith("今日短线情绪"))
        self.assertGreater(len(self.p["core_judgement"]), 50)

    def test_index_moves(self):
        mv = self.p["index_moves"]
        self.assertAlmostEqual(mv["上证指数"], -1.10, places=6)
        self.assertAlmostEqual(mv["深证成指"], -2.57, places=6)
        self.assertAlmostEqual(mv["创业板指"], -3.96, places=6)

    def test_ladder(self):
        lad = self.p["ladder"]
        hit = [x for x in lad if x["stock"] == "闽发铝业" and x["boards"] == 5]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["theme"], "光伏与新能源")
        self.assertIn("光伏边框", hit[0]["note"])
        others = {(x["stock"], x["boards"]) for x in lad}
        self.assertIn(("浙文影业", 5), others)
        self.assertIn(("永臻股份", 4), others)

    def test_lhb(self):
        buys = self.p["lhb_inst_buys"]
        hit = [x for x in buys if x["stock"] == "东山精密"]
        self.assertEqual(len(hit), 1)
        self.assertAlmostEqual(hit[0]["amount_yi"], 8.81, places=6)
        self.assertLessEqual(len(buys), 8)

    def test_regulatory(self):
        regs = self.p["regulatory"]
        jiuyang = [x for x in regs if x["stock"] == "九阳股份"]
        self.assertEqual(jiuyang[0]["type"], "临触发")
        self.assertIn("181.22%", jiuyang[0]["detail"])
        types = {x["type"] for x in regs}
        self.assertTrue(types <= {"严重异动", "临触发", "多次异动", "重点监控"})

    def test_watch_next(self):
        wn = self.p["watch_next"]
        self.assertEqual(len(wn), 5)
        self.assertTrue(wn[0].startswith("高度端的5进6"))
        for w in wn:
            self.assertLessEqual(len(w), 80)


class TestParseNewsAllPeriods(unittest.TestCase):
    """全部 10 期新闻逐期校验 + 命中率汇总表。"""

    @classmethod
    def setUpClass(cls):
        cls.parsed = {d: parse_news(md) for d, md in NEWS.items()}

    def test_10_periods_present(self):
        self.assertEqual(len(NEWS), 10, "语料应为 10 期新闻")

    def test_all_fields_hit_every_period(self):
        for d, p in self.parsed.items():
            self.assertIsNotNone(p, "%s 解析整体失败" % d)
            for f in NEWS_FIELDS:
                self.assertTrue(_hit(p, f), "%s 字段 %s 为空" % (d, f))
            self.assertEqual(len(p["catalysts"]), 5, "%s 催化不足5条" % d)
            self.assertEqual(p["news_date"], d)

    def test_hit_rate_summary_table(self):
        print("\n===== 新闻逐期字段命中率 =====")
        for d in sorted(NEWS, reverse=True):
            p = self.parsed[d]
            marks = "".join("." if _hit(p, f) else "X" for f in NEWS_FIELDS)
            print("%-12s %s  %s  cat=%d pano=%d wl=%d" % (
                d, marks, p["parse_coverage"], len(p["catalysts"]),
                len(p["asset_panorama"]), len(p["watchlist"])))


class TestNews20261109(unittest.TestCase):
    """指定值断言：2026-11-09 期。"""

    @classmethod
    def setUpClass(cls):
        cls.p = parse_news(NEWS["2026-11-09"])

    def test_date_and_summary(self):
        self.assertEqual(self.p["news_date"], "2026-11-09")
        self.assertEqual(len(self.p["exec_summary_paras"]), 5)

    def test_catalysts(self):
        cat = self.p["catalysts"]
        self.assertEqual(len(cat), 5)
        self.assertIn("美伊", cat[0]["title"])
        self.assertEqual(cat[0]["tag"], "强催化")
        self.assertTrue(cat[0]["body"].startswith("卡塔尔官员"))

    def test_asset_panorama(self):
        pano = self.p["asset_panorama"]
        self.assertEqual(len(pano), 9)
        gold = [x for x in pano if x["dimension"] == "黄金"]
        self.assertEqual(len(gold), 1)
        self.assertIn("偏多", gold[0]["signal"])
        self.assertIn("偏多明显", gold[0]["sentiment"])
        dims = [x["dimension"] for x in pano]
        self.assertNotIn("资产维度", dims)  # 表头不混入

    def test_watchlist(self):
        wl = self.p["watchlist"]
        self.assertEqual(len(wl), 5)
        for w in wl:
            self.assertLessEqual(len(w), 100)


class TestRobustness(unittest.TestCase):
    """边界：空文/缺锚点/交叉文档一律 None，不抛异常。"""

    def test_empty_and_missing_anchor(self):
        self.assertIsNone(parse_review(None))
        self.assertIsNone(parse_review(""))
        self.assertIsNone(parse_review("一段与研究报无关的普通文字，没有任何锚点"))
        self.assertIsNone(parse_news(None))
        self.assertIsNone(parse_news(""))
        self.assertIsNone(parse_news("# 别的文档\n正文但没有执行摘要"))

    def test_cross_document(self):
        self.assertIsNone(parse_review(NEWS["2026-11-09"]))
        self.assertIsNone(parse_news(REVIEWS["2026-11-06"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
