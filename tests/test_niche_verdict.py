"""Tests for niche_verdict.py — GO / TEST / SKIP from observed evidence only."""
import unittest

from niche_verdict import verdict


def listing(title, shop_sales=50, favorites=3, price=19.99, currency="USD",
            tags=None, listing_type="physical", image_url="https://example.com/1.jpg",
            reviews=12, rating=4.8):
    return {
        "listing_id": 1, "title": title,
        "tags": tags if tags is not None else ["wedding", "invitation", "minimalist"],
        "price": price, "currency": currency, "favorites": favorites,
        "listing_url": "https://www.etsy.com/listing/1",
        "shop_id": 7, "shop_name": "Shop",
        "shop_total_sales": shop_sales, "shop_review_count": reviews,
        "shop_rating": rating, "shop_data_available": True,
        "listing_type": listing_type, "image_url": image_url,
        "is_personalizable": False, "category_path": "Paper Goods > Invitations",
    }


def sample(n, **kw):
    rows = []
    for i in range(n):
        row = listing(f"Minimalist wedding invitation card {i}", **kw)
        row["listing_id"] = 100 + i
        row["shop_id"] = 200 + i
        rows.append(row)
    return rows


class VerdictTests(unittest.TestCase):
    def test_go_small_competition_weak_shops(self):
        # Few exact matches: titles mostly do NOT contain the full phrase.
        rows = sample(100)
        for r in rows[:85]:
            r["title"] = "Rustic paper card set"
        out = verdict("minimalist wedding invitation", rows,
                      reported_matching_listings=8000)
        self.assertEqual(out["verdict"], "GO")
        self.assertIn("demand", out["demand_note"].lower())
        self.assertEqual(out["demand_status"], "unverified")
        self.assertTrue(out["reasons"])
        self.assertTrue(out["ranking_levers"])
        levers = [lv["lever"] for lv in out["ranking_levers"]]
        self.assertIn("Fill all 13 tags", levers)
        self.assertIn("Listing type", levers)

    def test_skip_huge_competition_strong_shops(self):
        rows = sample(100, shop_sales=25000, favorites=300, reviews=9000)
        out = verdict("wedding invitation", rows,
                      reported_matching_listings=500_000)
        self.assertEqual(out["verdict"], "SKIP")

    def test_middle_is_test(self):
        rows = sample(100, shop_sales=800, favorites=40)
        out = verdict("wedding invitation", rows,
                      reported_matching_listings=60_000)
        self.assertEqual(out["verdict"], "TEST")

    def test_empty_sample_cannot_judge(self):
        out = verdict("wedding invitation", [], reported_matching_listings=1000)
        self.assertEqual(out["verdict"], "TEST")
        self.assertEqual(out["confidence"], "low")

    def test_tiny_sample_is_test(self):
        out = verdict("wedding invitation", sample(4),
                      reported_matching_listings=1000)
        self.assertEqual(out["verdict"], "TEST")
        self.assertEqual(out["confidence"], "low")

    def test_no_demand_invented(self):
        out = verdict("wedding invitation", sample(100),
                      reported_matching_listings=8000)
        evidence = out["evidence"]
        self.assertEqual(evidence["sample_size"], 100)
        self.assertNotIn("etsy_monthly_searches", evidence)
        self.assertIn("is not verified", out["demand_note"])

    def test_digital_dominant_type_reported(self):
        rows = sample(50, listing_type="digital")
        out = verdict("wedding invitation", rows,
                      reported_matching_listings=9000)
        self.assertEqual(out["evidence"]["dominant_listing_type"], "digital")


if __name__ == "__main__":
    unittest.main()
