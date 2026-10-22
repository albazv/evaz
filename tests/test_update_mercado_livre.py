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

    def test_public_store_listing_image_is_matched_to_exact_model_and_variant(self):
        markup = """
        <ul>
          <li class="item product product-item">
            <div class="product-item-info">
              <a class="product photo product-item-photo" href="https://www.logitechstore.com.br/g305-preto/">
                <img src="https://www.logitechstore.com.br/media/catalog/product/g305_black.webp"
                     alt="Mouse sem fio Logitech G305 preto">
              </a>
              <a class="product-item-link" href="https://www.logitechstore.com.br/g305-preto/">
                Mouse sem fio Logitech G305 preto
              </a>
            </div>
          </li>
        </ul>
        """
        products = updater.parse_store_products(markup, "https://www.logitechstore.com.br/gaming")
        target = next(
            product
            for product in updater.parse_products((ROOT / "index.html").read_text(encoding="utf-8"))
            if "G305 preto" in product.name
        )

        match = updater.find_store_product(products, target)

        self.assertIsNotNone(match)
        self.assertEqual(match.image_url, "https://www.logitechstore.com.br/media/catalog/product/g305_black.webp")

        repeated_listing = updater.StoreProduct(
            "Mouse sem fio Logitech G305 preto promoção",
            "https://www.logitechstore.com.br/g305-preto/",
            "https://www.logitechstore.com.br/media/catalog/product/cache/other/g305_black.webp",
        )
        self.assertEqual(
            updater.find_store_product(products + [repeated_listing], target),
            match,
        )

        wrong_variant = markup.replace("g305-preto", "g305-branco").replace("G305 preto", "G305 branco")
        wrong_products = updater.parse_store_products(wrong_variant, "https://www.logitechstore.com.br/gaming")
        self.assertIsNone(updater.find_store_product(wrong_products, target))

    def test_store_category_discovery_uses_public_navigation_and_excludes_search(self):
        markup = """
        <nav class="navigation">
          <a href="https://www.logitechstore.com.br/gaming">Gaming</a>
          <a href="/todas-as-categorias/mouse/">Mouse</a>
          <a href="/catalogsearch/result/?q=mouse">Busca</a>
          <a href="https://other.example/catalog">External</a>
        </nav>
        """

        categories = updater.extract_store_categories(markup, "https://www.logitechstore.com.br/")

        self.assertEqual(
            categories,
            [
                "https://www.logitechstore.com.br/gaming",
                "https://www.logitechstore.com.br/todas-as-categorias/mouse",
            ],
        )
        self.assertTrue(updater._is_disallowed_store_path("https://www.logitechstore.com.br/catalogsearch/result/?q=mouse"))
        self.assertFalse(updater._is_disallowed_store_path("https://www.logitechstore.com.br/gaming?p=2"))

    def test_store_pagination_only_follows_public_page_number_links(self):
        markup = """
        <nav class="pages-items">
          <a href="https://www.logitechstore.com.br/gaming?product_list_order=price&amp;p=2">2</a>
          <a href="https://www.logitechstore.com.br/gaming?p=1">1</a>
          <a href="https://www.logitechstore.com.br/gaming?dir=asc">Ordenar</a>
          <a href="https://www.logitechstore.com.br/catalogsearch/result/?q=mouse&p=2">Busca</a>
        </nav>
        """

        self.assertEqual(
            updater.extract_store_page_links(markup, "https://www.logitechstore.com.br/gaming"),
            ["https://www.logitechstore.com.br/gaming?p=2"],
        )

    def test_catalog_scan_prioritizes_categories_for_product_types(self):
        products = updater.parse_products((ROOT / "index.html").read_text(encoding="utf-8"))
        categories = [
            "https://www.logitechstore.com.br/gaming",
            "https://www.logitechstore.com.br/gaming/mouses",
            "https://www.logitechstore.com.br/gaming/volantes/",
            "https://www.logitechstore.com.br/audio/headsets/",
            "https://www.logitechstore.com.br/exclusivos/",
        ]

        relevant = updater._relevant_store_categories(categories, products)

        self.assertIn("https://www.logitechstore.com.br/gaming/mouses", relevant)
        self.assertIn("https://www.logitechstore.com.br/audio/headsets/", relevant)
        self.assertNotIn("https://www.logitechstore.com.br/gaming/volantes/", relevant)
        self.assertNotIn("https://www.logitechstore.com.br/exclusivos/", relevant)

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
