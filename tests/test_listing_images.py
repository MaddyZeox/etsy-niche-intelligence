"""Tests for v1.6 listing images + listing-type fields (mocked Etsy API)."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load_collector():
    spec = importlib.util.spec_from_file_location(
        "etsy_collector_images_test", ROOT / "etsy_collector_keyword.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_listing(i, with_images=True):
    row = {
        "listing_id": 1000 + i,
        "title": f"Minimalist wedding invitation {i}",
        "tags": ["wedding", "invitation", "minimalist"],
        "taxonomy_id": 1,
        "price": {"amount": 1999, "divisor": 100, "currency_code": "USD"},
        "num_favorers": 10 + i,
        "url": f"https://www.etsy.com/listing/{1000 + i}",
        "shop_id": 500 + i,
        "listing_type": "download" if i % 2 else "physical",
        "materials": ["paper", "cardstock"],
        "who_made": "i_did",
        "when_made": "made_to_order",
        "is_personalizable": True,
        "is_customizable": False,
        "quantity": 50,
        "created_timestamp": 1750000000,
    }
    if with_images:
        row["images"] = [
            {"rank": 2, "url_570xN": f"https://img.etsy.com/2_{i}.jpg",
             "url_fullxfull": f"https://img.etsy.com/2full_{i}.jpg"},
            {"rank": 1, "url_570xN": f"https://img.etsy.com/1_{i}.jpg",
             "url_fullxfull": f"https://img.etsy.com/1full_{i}.jpg"},
        ]
    return row


class ImageHelpersTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()

    def test_primary_image_picks_rank_1(self):
        url, large, count, source = self.mod._primary_image(fake_listing(1))
        self.assertEqual(url, "https://img.etsy.com/1_1.jpg")
        self.assertEqual(large, "https://img.etsy.com/1full_1.jpg")
        self.assertEqual(count, 2)
        self.assertEqual(source, "includes")

    def test_primary_image_missing(self):
        self.assertEqual(self.mod._primary_image(fake_listing(1, with_images=False)),
                         (None, None, 0, "missing"))

    def test_listing_type_normalized(self):
        self.assertEqual(self.mod._listing_type_value(fake_listing(1)), "digital")
        self.assertEqual(self.mod._listing_type_value(fake_listing(2)), "physical")
        self.assertIsNone(self.mod._listing_type_value({}))
        self.assertIsNone(self.mod._listing_type_value({"listing_type": "weird"}))

    def test_listing_record_fields(self):
        rec = self.mod._listing_record(fake_listing(1))
        self.assertEqual(rec["image_url"], "https://img.etsy.com/1_1.jpg")
        self.assertEqual(rec["listing_type"], "digital")
        self.assertEqual(rec["materials"], ["paper", "cardstock"])
        self.assertTrue(rec["is_personalizable"])
        self.assertEqual(rec["quantity"], 50)
        self.assertFalse(rec["shop_data_available"])

    def test_fetch_listing_image_uses_images_endpoint(self):
        calls = []

        def fake_fetch(path, headers):
            calls.append(path)
            return {"results": [
                {"rank": 1, "url_570xN": "https://img.etsy.com/x.jpg",
                 "url_fullxfull": "https://img.etsy.com/xfull.jpg"},
            ]}

        with patch.object(self.mod, "fetch_json", side_effect=fake_fetch):
            out = self.mod.fetch_listing_image(123, {})
        self.assertEqual(out["image_url"], "https://img.etsy.com/x.jpg")
        self.assertEqual(out["image_source"], "images_endpoint")
        self.assertIn("/listings/123/images", calls[0])

    def test_fetch_listing_image_none_when_empty(self):
        with patch.object(self.mod, "fetch_json", return_value={"results": []}):
            self.assertIsNone(self.mod.fetch_listing_image(123, {}))

    def test_fetch_listing_image_none_on_error(self):
        with patch.object(self.mod, "fetch_json", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.mod.fetch_listing_image(123, {}))


class CollectorImagesEndToEndTests(unittest.TestCase):
    """Mocked full collector run: parallel pages + includes=Images."""

    def test_main_writes_images_and_types(self):
        mod = load_collector()
        pages = {0: [fake_listing(0), fake_listing(1)],
                 1: [fake_listing(2), fake_listing(3)]}
        seen_paths = []

        def fake_fetch(path, headers):
            seen_paths.append(path)
            if path.startswith("/listings/active"):
                offset = 0
                if "offset=50" in path:
                    offset = 1
                return {"count": 4, "results": pages[offset]}
            if path.startswith("/seller-taxonomy/nodes"):
                return {"results": [{"id": 1, "name": "Paper Goods", "children": []}]}
            raise AssertionError(f"unexpected path {path}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with patch.object(mod, "fetch_json", side_effect=fake_fetch), \
                 patch("keyring.get_password", return_value="fake"), \
                 patch.object(mod, "DIR", tmp), \
                 patch.object(mod, "DB_FILE", tmp / "cache.sqlite3"), \
                 patch.object(mod, "OUTPUT_DIR", tmp / "research_results"):
                mod.main("test item", pages=2, buyer_country="US",
                         run_id="a" * 32, mode="quick")
            out_files = list((tmp / "research_results").glob("*.json"))
            self.assertEqual(len(out_files), 1)
            data = json.loads(out_files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["sample_size"], 4)
            first = data["listings"][0]
            self.assertEqual(first["image_url"], "https://img.etsy.com/1_0.jpg")
            self.assertEqual(first["image_source"], "includes")
            self.assertEqual(first["listing_type"], "physical")
            self.assertEqual(data["listings"][1]["listing_type"], "digital")
            self.assertEqual(first["materials"], ["paper", "cardstock"])
            # both pages requested in parallel (offsets 0 and 50 present)
            active = [p for p in seen_paths if p.startswith("/listings/active")]
            self.assertTrue(any("offset=0" in p for p in active))
            self.assertTrue(any("offset=50" in p for p in active))
            # includes=Images was requested
            self.assertTrue(any("includes=Images" in p for p in active))

    def test_includes_400_falls_back_to_plain(self):
        mod = load_collector()

        def fake_fetch(path, headers):
            if "includes=Images" in path:
                raise urllib.error.HTTPError(path, 400, "Bad Request", {}, None)
            if path.startswith("/listings/active"):
                return {"count": 1, "results": [fake_listing(0, with_images=False)]}
            if path.startswith("/seller-taxonomy/nodes"):
                return {"results": [{"id": 1, "name": "Paper Goods", "children": []}]}
            raise AssertionError(f"unexpected path {path}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with patch.object(mod, "fetch_json", side_effect=fake_fetch), \
                 patch("keyring.get_password", return_value="fake"), \
                 patch.object(mod, "DIR", tmp), \
                 patch.object(mod, "DB_FILE", tmp / "cache.sqlite3"), \
                 patch.object(mod, "OUTPUT_DIR", tmp / "research_results"):
                mod.main("test item", pages=1, buyer_country="US",
                         run_id="b" * 32, mode="quick")
            out_files = list((tmp / "research_results").glob("*.json"))
            data = json.loads(out_files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["sample_size"], 1)
            self.assertEqual(data["listings"][0]["image_source"], "missing")
            self.assertIsNone(data["listings"][0]["image_url"])


if __name__ == "__main__":
    unittest.main()
