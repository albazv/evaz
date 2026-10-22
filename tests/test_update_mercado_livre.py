import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_mercado_livre.py"
SPEC = importlib.util.spec_from_file_location("update_mercado_livre", SCRIPT)
updater = None
if SPEC and SPEC.loader and SCRIPT.exists():
    updater = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = updater
    SPEC.loader.exec_module(updater)


class MercadoLivreUpdaterTests(unittest.TestCase):
    def test_dry_run_lists_products_without_creating_files(self):
        index_path = ROOT / "index.html"
        manifest_path = ROOT / "assets" / "images" / "mercado-livre" / "manifest.json"
        index_before = index_path.read_bytes()
        manifest_existed = manifest_path.exists()
        manifest_before = manifest_path.read_bytes() if manifest_existed else None
        image_files_before = set((manifest_path.parent).glob("*.webp"))

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("25 anúncios", result.stdout)
        self.assertEqual(index_path.read_bytes(), index_before)
        self.assertEqual(manifest_path.exists(), manifest_existed)
        if manifest_existed:
            self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertEqual(set(manifest_path.parent.glob("*.webp")), image_files_before)

    def test_product_parser_preserves_every_listing_url(self):
        if updater is None:
            self.skipTest("updater script has not been created")
        products = updater.parse_products((ROOT / "index.html").read_text(encoding="utf-8"))
        urls = [product.url for product in products]

        self.assertEqual(len(products), 25)
        self.assertEqual(len(set(urls)), 25)
        self.assertTrue(all("produto.mercadolivre.com.br/MLB-" in url for url in urls))
        self.assertTrue(all(product.name and product.product_type for product in products))

    def test_image_update_changes_only_the_matching_product(self):
        source = (ROOT / "index.html").read_text(encoding="utf-8")
        products = updater.parse_products(source)
        target = products[0]
        updated = updater._update_product_images(
            source,
            {target.url: "assets/images/mercado-livre/headset-MLB-7853276482-01.webp"},
        )
        updated_products = updater.parse_products(updated)

        self.assertEqual([product.url for product in updated_products], [product.url for product in products])
        self.assertEqual(updated_products[0].image, "assets/images/mercado-livre/headset-MLB-7853276482-01.webp")
        self.assertEqual([product.image for product in updated_products[1:]], [product.image for product in products[1:]])

    def test_product_image_requires_matching_listing_and_model_identity(self):
        page = b"""
        <html><head>
          <link rel="canonical" href="https://produto.mercadolivre.com.br/MLB-7847529792-m240">
          <meta property="og:title" content="Mouse sem fio Logitech M240 grafite">
          <meta property="og:image" content="https://http2.mlstatic.com/m240.webp">
        </head></html>
        """
        matches = updater._image_candidates(
            page,
            "https://produto.mercadolivre.com.br/MLB-7847529792-m240",
            "MLB-7847529792",
            "Mouse sem fio Logitech M240 grafite",
        )
        self.assertEqual(matches, ["https://http2.mlstatic.com/m240.webp"])

        page = page.replace(b"Logitech M240", b"Logitech M185")
        with self.assertRaisesRegex(ValueError, "sem imagem de produto verificável"):
            updater._image_candidates(
                page,
                "https://produto.mercadolivre.com.br/MLB-7847529792-m240",
                "MLB-7847529792",
                "Mouse sem fio Logitech M240 grafite",
            )

    def test_variant_color_must_match_before_using_listing_image(self):
        self.assertFalse(
            updater._has_product_identity(
                "Mouse gamer Logitech G305 branco",
                "Mouse sem fio Logitech G305 preto",
            )
        )
        self.assertTrue(
            updater._has_product_identity(
                "Mouse gamer Logitech G305 branco",
                "Mouse sem fio Logitech G305 white",
            )
        )

    def test_listing_id_in_query_string_does_not_confirm_a_page(self):
        page = b"""
        <html><head>
          <link rel="canonical" href="https://produto.mercadolivre.com.br/MLB-1111111111-item?ref=MLB-7847529792">
          <meta property="og:title" content="Mouse sem fio Logitech M240 grafite">
          <meta property="og:image" content="https://http2.mlstatic.com/m240.webp">
        </head></html>
        """
        with self.assertRaisesRegex(ValueError, "código MLB"):
            updater._image_candidates(
                page,
                "https://produto.mercadolivre.com.br/MLB-1111111111-item",
                "MLB-7847529792",
                "Mouse sem fio Logitech M240 grafite",
            )

    def test_non_image_responses_are_rejected(self):
        if updater is None:
            self.skipTest("updater script has not been created")
        with self.assertRaises(updater.ImageValidationError):
            updater.image_format(b"<!doctype html><title>access denied</title>", "text/html")
        with self.assertRaises(updater.ImageValidationError):
            updater.image_format(b"\x89PNG\r\n\x1a\ntruncated", "image/png")

    def test_listing_id_is_part_of_the_safe_filename(self):
        if updater is None:
            self.skipTest("updater script has not been created")
        first = updater.filename_for_product("Mouse Logitech G305 preto", "MLB-7847403870", "webp")
        second = updater.filename_for_product("Mouse Logitech G305 preto", "MLB-5426238523", "webp")

        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("-MLB-7847403870-01.webp"))
        self.assertNotIn(" ", first)

    def test_local_product_images_are_not_reused_between_listings(self):
        if updater is None:
            self.skipTest("updater script has not been created")
        products = updater.parse_products((ROOT / "index.html").read_text(encoding="utf-8"))
        images = [product.image for product in products if product.image]

        self.assertEqual(len(images), len(set(images)))


if __name__ == "__main__":
    unittest.main()
