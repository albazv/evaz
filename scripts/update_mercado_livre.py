#!/usr/bin/env python3
"""Update Mercado Livre product cards with matching images from Logitech's public catalog."""

from __future__ import annotations

import argparse
import ast
import html
import json
import os
import re
import ssl
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

import certifi


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "index.html"
IMAGE_DIR = ROOT / "assets" / "images" / "mercado-livre"
MANIFEST_PATH = IMAGE_DIR / "manifest.json"
REQUEST_TIMEOUT = 12
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
USER_AGENT = "ComelvaCatalogUpdater/1.0 (public listing image metadata)"
LOGITECH_ORIGIN = "https://www.logitechstore.com.br"
LOGITECH_HOSTS = {"www.logitechstore.com.br", "logitechstore.com.br"}
MAX_CATALOG_PAGES = 300
REQUEST_DELAY = 0.8
ALLOWED_HOST_SUFFIXES = (".mercadolivre.com.br", ".mercadolibre.com", ".mlstatic.com")
ALLOWED_HOSTS = {"mercadolivre.com.br", "mercadolibre.com", "mlstatic.com"}
GENERIC_MODEL_WORDS = {
    "BRANCO", "BRANCA", "BLACK", "CINZA", "CLARO", "COM", "DE", "EUA", "FIO",
    "GAMER", "GRAFITE", "INGLES", "LIGHTSPEED", "LOGITECH", "MOUSE", "PARA",
    "PRETO", "ROSA", "SEM", "TECLADO", "WIRELESS", "US",
}
MODEL_WORDS = {
    "ASTRO", "LIFT", "MX", "PEBBLE", "PRO", "RGB", "SUPERLIGHT", "SUPERSTRIKE", "TKL",
}
COLOR_VARIANTS = {
    "branco": {"branco", "branca", "white"},
    "preto": {"preto", "preta", "black"},
    "grafite": {"grafite", "graphite"},
    "cinza": {"cinza", "gray", "grey"},
    "claro": {"claro", "clara", "light"},
    "magenta": {"magenta"},
    "rosa": {"rosa", "pink"},
    "chiclete": {"chiclete", "gum", "chewing"},
}
IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


class ImageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Product:
    name: str
    product_type: str
    url: str
    image: str | None
    listing_id: str | None
    error: str | None = None


@dataclass(frozen=True)
class StoreProduct:
    name: str
    product_url: str
    image_url: str


class StorePageParser(HTMLParser):
    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.categories: list[str] = []
        self.page_links: list[str] = []
        self.products: list[StoreProduct] = []
        self._stack: list[str] = []
        self._in_navigation = 0
        self._card: dict[str, Any] | None = None
        self._anchor_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        classes = set((values.get("class") or "").lower().split())
        if tag == "nav" and "navigation" in classes:
            self._in_navigation += 1
        if self._in_navigation and tag == "a" and values.get("href"):
            url = urljoin(self.page_url, values["href"])
            if _allowed_store_url(url) and not _is_disallowed_store_path(url):
                self.categories.append(url)

        if tag == "a":
            href = values.get("href")
            self._anchor_href = urljoin(self.page_url, href) if href else None
        if self._card is None and (
            (tag == "li" and "product-item" in classes)
            or (tag == "div" and "product-item-info" in classes)
        ):
            self._card = {"tag": tag, "depth": len(self._stack) + 1, "text": [], "images": [], "links": []}
        elif self._card is not None and tag == "a" and self._anchor_href:
            if "product-item-link" in classes or "product-item-photo" in classes or "product-photo" in classes:
                self._card["links"].append(self._anchor_href)

        if self._card is not None and tag == "img":
            image_url = self._image_src(values)
            if image_url:
                self._card["images"].append(image_url)

        for key in ("href", "data-href"):
            href = values.get(key)
            if href:
                absolute = urljoin(self.page_url, href)
                parsed = urlparse(absolute)
                query = dict(parse_qsl(parsed.query))
                if (
                    _allowed_store_url(absolute)
                    and not _is_disallowed_store_path(absolute)
                    and parsed.path.rstrip("/") == urlparse(self.page_url).path.rstrip("/")
                    and query.get("p", "").isdigit()
                ):
                    self.page_links.append(absolute)

        if tag not in self.VOID_ELEMENTS:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._in_navigation:
            self._in_navigation -= 1
        if self._card is not None and tag == self._card["tag"] and len(self._stack) == self._card["depth"]:
            links = list(dict.fromkeys(self._card["links"]))
            images = list(dict.fromkeys(self._card["images"]))
            if links and images:
                title = " ".join(self._card["text"])
                for link in links:
                    for image_url in images:
                        self.products.append(StoreProduct(title, link, image_url))
            self._card = None
        for position in range(len(self._stack) - 1, -1, -1):
            if self._stack[position] == tag:
                del self._stack[position:]
                break
        if tag == "a":
            self._anchor_href = None

    def handle_data(self, data: str) -> None:
        if self._card is not None and data.strip():
            self._card["text"].append(data.strip())

    def _image_src(self, values: dict[str, str]) -> str | None:
        candidates = [
            values.get("data-original"),
            values.get("data-src"),
            values.get("src"),
            values.get("srcset", "").split(",")[0].strip().split(" ")[0],
        ]
        for candidate in candidates:
            if not candidate:
                continue
            absolute = urljoin(self.page_url, candidate)
            lowered = absolute.lower()
            if (
                _allowed_store_url(absolute)
                and "/media/catalog/product/" in urlparse(absolute).path
                and not any(token in lowered for token in ("placeholder", "no-image", "no_image"))
            ):
                return absolute
        return None


class ListingMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.canonical_urls: list[str] = []
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content")
            if key and content and key not in self.metadata:
                self.metadata[key] = content
        elif tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            href = values.get("href")
            if href:
                self.canonical_urls.append(href)
        elif tag == "script" and "ld+json" in (values.get("type") or "").lower():
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []


def _array_end(source: str, start: int) -> int:
    quote: str | None = None
    escaped = False
    for position in range(start + 1, len(source)):
        char = source[position]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "]":
            return position
    raise ValueError("Lista de produtos Mercado Livre sem fechamento.")


def _properties(object_source: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    pattern = re.compile(
        r"([A-Za-z_$][\w$]*)\s*:\s*"
        r"('[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")"
    )
    for match in pattern.finditer(object_source):
        try:
            value = ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            properties[match.group(1)] = value
    return properties


def _valid_listing_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host in ALLOWED_HOSTS
        or any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES)
    ):
        return None
    match = re.search(r"(?:^|/)MLB-?(\d+)(?:[-_/]|$)", parsed.path, re.IGNORECASE)
    return f"MLB-{match.group(1)}" if match else None


def parse_products(source: str) -> list[Product]:
    match = re.search(r"\bconst\s+mercadoLivreProducts\s*=\s*\[", source)
    if not match:
        raise ValueError("Lista mercadoLivreProducts não encontrada em index.html.")
    start = source.find("[", match.start())
    end = _array_end(source, start)
    products: list[Product] = []
    for object_match in re.finditer(r"\{([^{}]*)\}", source[start + 1:end]):
        values = _properties(object_match.group(1))
        if "name" not in values and "url" not in values:
            continue
        missing = [field for field in ("name", "type", "url") if not values.get(field)]
        name = values.get("name", "").strip()
        url = values.get("url", "").strip()
        listing_id = _valid_listing_id(url) if url else None
        error = None
        if missing:
            error = "Dados incompletos no cadastro: " + ", ".join(missing)
        elif listing_id is None:
            error = "URL não é um anúncio Mercado Livre HTTPS válido com código MLB."
        products.append(
            Product(
                name=name,
                product_type=values.get("type", ""),
                url=url,
                image=values.get("image"),
                listing_id=listing_id,
                error=error,
            )
        )
    if not products:
        raise ValueError("Nenhum produto encontrado em mercadoLivreProducts.")
    return products


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug[:64].rstrip("-") or "produto")


def filename_for_product(name: str, listing_id: str, extension: str) -> str:
    if not re.fullmatch(r"MLB-\d+", listing_id):
        raise ValueError("Código de anúncio inválido para nome de imagem.")
    if not re.fullmatch(r"[a-z0-9]{2,5}", extension):
        raise ValueError("Formato de imagem inválido.")
    return f"{_slug(name)}-{listing_id}-01.{extension}"


def image_format(data: bytes, content_type: str = "") -> str:
    for signature, extension in IMAGE_SIGNATURES:
        if data.startswith(signature):
            if extension == "png" and (len(data) < 45 or b"IEND\xaeB`\x82" not in data[-12:]):
                raise ImageValidationError("Arquivo PNG truncado ou sem bloco final.")
            if extension == "jpg" and (len(data) < 16 or not data.endswith(b"\xff\xd9")):
                raise ImageValidationError("Arquivo JPEG truncado ou sem marcador final.")
            if extension == "gif" and (len(data) < 14 or not data.endswith(b";")):
                raise ImageValidationError("Arquivo GIF truncado ou sem marcador final.")
            return extension
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        declared_size = int.from_bytes(data[4:8], "little")
        if declared_size + 8 > len(data) or len(data) < 30:
            raise ImageValidationError("Arquivo WebP truncado.")
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis") and len(data) >= 32:
            return "avif"
    raise ImageValidationError(
        f"Resposta não contém uma imagem suportada (Content-Type: {content_type or 'desconhecido'})."
    )


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host in ALLOWED_HOSTS
        or any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES)
    )


def _allowed_store_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in LOGITECH_HOSTS


def _is_disallowed_store_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.startswith(
        (
            "/catalogsearch/",
            "/catalog/category/view/",
            "/catalog/product/view/",
            "/catalog/product_compare/",
            "/customer/",
            "/checkout/",
            "/wishlist/",
            "/compare/",
        )
    )


def extract_store_categories(markup: str, page_url: str) -> list[str]:
    parser = StorePageParser(page_url)
    parser.feed(markup)
    canonical = [
        urlunparse(urlparse(url)._replace(path=urlparse(url).path.rstrip("/") or "/", query="", fragment=""))
        for url in parser.categories
    ]
    return list(dict.fromkeys(canonical))


def _relevant_store_categories(categories: list[str], products: list[Product]) -> list[str]:
    product_types = {product.product_type.lower() for product in products}
    broad_roots = {"/gaming", "/todas-as-categorias", "/dia-a-dia", "/audio"}
    direct_sections = {
        "mouse": {"mouse", "mouses"},
        "keyboard": {"teclado", "teclados", "combo", "combos"},
        "headphones": {"headset", "headsets", "astro"},
    }
    relevant: list[str] = []
    for category in categories:
        path = urlparse(category).path.lower().rstrip("/") or "/"
        segments = set(path.strip("/").split("/"))
        if (
            path in broad_roots
            or path.startswith("/lancamentos/")
            or any(segments & direct_sections.get(product_type, set()) for product_type in product_types)
        ):
            relevant.append(category)

    def category_rank(url: str) -> tuple[int, str]:
        segments = set(urlparse(url).path.lower().strip("/").split("/"))
        is_specific = any(segments & direct_sections.get(product_type, set()) for product_type in product_types)
        return (0 if is_specific else 1, url)

    return sorted(dict.fromkeys(relevant), key=category_rank)


def extract_store_page_links(markup: str, page_url: str) -> list[str]:
    parser = StorePageParser(page_url)
    parser.feed(markup)
    canonical: list[str] = []
    for url in parser.page_links:
        parsed = urlparse(url)
        page_number = dict(parse_qsl(parsed.query)).get("p", "")
        if page_number.isdigit() and int(page_number) > 1:
            canonical.append(
                urlunparse(parsed._replace(query=urlencode({"p": page_number}), fragment=""))
            )
    return list(dict.fromkeys(canonical))


def parse_store_products(markup: str, page_url: str) -> list[StoreProduct]:
    parser = StorePageParser(page_url)
    parser.feed(markup)
    return list(dict.fromkeys(parser.products))


def find_store_product(products: list[StoreProduct], product: Product) -> StoreProduct | None:
    matches = [
        candidate
        for candidate in products
        if _has_product_identity(
            product.name,
            " ".join((candidate.name, urlparse(candidate.product_url).path, urlparse(candidate.image_url).path)),
        )
    ]
    by_product_url: dict[str, list[StoreProduct]] = {}
    for candidate in matches:
        by_product_url.setdefault(candidate.product_url, []).append(candidate)
    if len(by_product_url) != 1:
        return None
    same_product = next(iter(by_product_url.values()))
    image_names = {
        urlparse(candidate.image_url).path.rsplit("/", 1)[-1].lower()
        for candidate in same_product
    }
    if len(image_names) != 1:
        return None
    return same_product[0]


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        if not _allowed_store_url(target):
            raise URLError("Redirecionamento para domínio não autorizado.")
        return super().redirect_request(request, response, code, message, headers, target)


def _fetch(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    if not _allowed_store_url(url):
        raise ValueError("URL HTTPS fora do domínio público da Logitech Store.")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    opener = build_opener(
        SafeRedirectHandler(),
        HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())),
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Resposta excede o limite de {max_bytes} bytes.")
        return data, response.headers.get("Content-Type", ""), response.geturl()


@lru_cache(maxsize=16)
def _robots_policy(origin: str) -> RobotFileParser | None:
    robots_url = f"{origin}/robots.txt"
    for attempt in range(2):
        try:
            content, content_type, final_url = _fetch(robots_url, MAX_PAGE_BYTES)
            if "html" in content_type.lower() or not _allowed_store_url(final_url):
                return None
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(content.decode("utf-8", errors="replace").splitlines())
            return parser
        except HTTPError:
            return None
        except (URLError, TimeoutError, OSError, ValueError):
            if attempt == 0:
                time.sleep(REQUEST_DELAY)
    return None


def _check_robots(url: str) -> None:
    if not _allowed_store_url(url):
        raise ValueError("URL fora do domínio público da Logitech Store.")
    if _is_disallowed_store_path(url):
        raise ValueError("Caminho de busca ou área restrita não será acessado.")
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    policy = _robots_policy(origin)
    if policy is None:
        raise ValueError("robots.txt da Logitech Store indisponível; produto pendente por segurança.")
    if not policy.can_fetch(USER_AGENT, url):
        raise ValueError("robots.txt da Logitech Store não permite acesso automatizado a esta página.")


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", html.unescape(value))
    return re.sub(r"[^a-z0-9]+", " ", decomposed.encode("ascii", "ignore").decode("ascii").lower()).strip()


def _identity_tokens(product_name: str) -> list[str]:
    tokens = re.findall(r"[A-Z0-9]+", product_name.upper())
    model = [
        token for token in tokens
        if any(char.isdigit() for char in token) or token in MODEL_WORDS
    ]
    if model:
        return list(dict.fromkeys(model))
    meaningful = [
        token for token in tokens
        if len(token) >= 3 and token not in GENERIC_MODEL_WORDS and token != "G"
    ]
    return [max(meaningful, key=len)] if meaningful else []


def _has_product_identity(product_name: str, candidate_name: str) -> bool:
    page_name = _normalize(candidate_name)
    page_words = set(page_name.split())
    if "logitech" not in page_words:
        return False
    tokens = _identity_tokens(product_name)
    if not tokens or not all(_normalize(token) in page_words for token in tokens):
        return False
    product_words = set(_normalize(product_name).split())
    expected_variants = {
        variant for variant in COLOR_VARIANTS if variant in product_words
    }
    found_variants = {
        variant
        for variant, synonyms in COLOR_VARIANTS.items()
        if synonyms & page_words
    }
    for variant in expected_variants:
        if not (COLOR_VARIANTS[variant] & page_words):
            return False
    if found_variants - expected_variants:
        return False
    return True


def _json_product_nodes(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if any(str(item).lower() == "product" for item in types):
            found.append(value)
        for nested in value.values():
            found.extend(_json_product_nodes(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_json_product_nodes(nested))
    return found


def _image_candidates(page: bytes, base_url: str, expected_id: str, product_name: str) -> list[str]:
    parser = ListingMetadataParser()
    parser.feed(page.decode("utf-8", errors="replace"))
    metadata = parser.metadata
    identity_urls = [base_url, *parser.canonical_urls]
    if metadata.get("og:url"):
        identity_urls.append(metadata["og:url"])
    if not any(_valid_listing_id(url) == expected_id for url in identity_urls):
        raise ValueError("Não foi possível confirmar o código MLB na página do anúncio.")

    candidates: list[str] = []
    for block in parser.json_ld:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for product in _json_product_nodes(data):
            product_title = product.get("name")
            if not isinstance(product_title, str) or not _has_product_identity(product_name, product_title):
                continue
            images = product.get("image", [])
            if isinstance(images, str):
                images = [images]
            if isinstance(images, list):
                candidates.extend(item for item in images if isinstance(item, str))

    og_title = metadata.get("og:title") or metadata.get("twitter:title")
    if og_title and _has_product_identity(product_name, og_title):
        og_image = metadata.get("og:image") or metadata.get("twitter:image")
        if og_image:
            candidates.append(og_image)

    safe_candidates: list[str] = []
    for candidate in candidates:
        absolute = urljoin(base_url, candidate)
        if _allowed_url(absolute) and absolute not in safe_candidates:
            safe_candidates.append(absolute)
    if not safe_candidates:
        raise ValueError("Anúncio confirmado, mas sem imagem de produto verificável.")
    return safe_candidates


def _challenge_error(page: bytes) -> str | None:
    text = _normalize(page[:200_000].decode("utf-8", errors="ignore"))
    indicators = (
        "captcha", "verify you are human", "access denied", "nao sou um robo",
        "complete the security check", "robot check",
    )
    if any(indicator in text for indicator in indicators):
        return "A página apresentou bloqueio ou verificação de acesso; anúncio pendente."
    return None


def _convert_if_available(data: bytes, extension: str) -> tuple[bytes, str]:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, extension
    try:
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            image.load()
            normalized = ImageOps.exif_transpose(image)
            output = BytesIO()
            has_alpha = "A" in normalized.getbands() or "transparency" in normalized.info
            normalized.convert("RGBA" if has_alpha else "RGB").save(
                output, format="WEBP", quality=88, method=6
            )
        return output.getvalue(), "webp"
    except Exception as error:
        raise ImageValidationError(f"Falha ao converter imagem: {error}") from error


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _write_json_atomic(path: Path, data: Any) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _write_atomic(path, serialized.encode("utf-8"))


def _load_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        content = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Manifesto existente inválido: {error}") from error
    if not isinstance(content, list):
        raise ValueError("Manifesto existente deve conter uma lista de produtos.")
    result: dict[str, dict[str, Any]] = {}
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("anuncio_url"), str):
            result[item["anuncio_url"]] = item
    return result


def _manifest_image_path(relative: str) -> Path | None:
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    resolved = (ROOT / candidate).resolve()
    try:
        resolved.relative_to(IMAGE_DIR.resolve())
    except ValueError:
        return None
    return resolved


def _is_completed(item: dict[str, Any] | None) -> bool:
    if not item or item.get("status") != "ok" or not isinstance(item.get("imagens"), list):
        return False
    return bool(item["imagens"]) and all(
        isinstance(relative, str)
        and (path := _manifest_image_path(relative)) is not None
        and path.is_file()
        for relative in item["imagens"]
    )


def _load_store_catalog(products: list[Product]) -> tuple[list[StoreProduct], list[str]]:
    root_url = f"{LOGITECH_ORIGIN}/"
    _check_robots(root_url)
    homepage, content_type, _ = _fetch(root_url, MAX_PAGE_BYTES)
    if "html" not in content_type.lower():
        raise ValueError("A página inicial da Logitech Store não retornou HTML.")
    homepage_text = homepage.decode("utf-8", errors="replace")
    all_categories = extract_store_categories(homepage_text, root_url)
    if not all_categories:
        raise ValueError("Não foi possível identificar categorias públicas na navegação da Logitech Store.")
    categories = _relevant_store_categories(all_categories, products)
    if not categories:
        raise ValueError("A navegação não possui categorias relacionadas aos tipos dos produtos.")
    print(f"Categorias consultadas: {len(categories)} de {len(all_categories)} públicas.")

    queue = list(categories)
    visited: set[str] = {root_url}
    catalog: list[StoreProduct] = []
    errors: list[str] = []
    requests_made = 1
    while queue and requests_made < MAX_CATALOG_PAGES:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            _check_robots(page_url)
            if requests_made:
                time.sleep(REQUEST_DELAY)
            page, content_type, final_url = _fetch(page_url, MAX_PAGE_BYTES)
            requests_made += 1
            if "html" not in content_type.lower():
                raise ValueError("A categoria não retornou HTML.")
            markup = page.decode("utf-8", errors="replace")
            catalog.extend(parse_store_products(markup, final_url))
            queue.extend(extract_store_page_links(markup, final_url))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            requests_made += 1
            errors.append(f"{urlparse(page_url).path}: {str(error)[:120]}")

    if queue:
        errors.append(f"Limite de {MAX_CATALOG_PAGES} páginas atingido antes de concluir o catálogo.")
    return list(dict.fromkeys(catalog)), errors


def _process_product(
    product: Product,
    catalog: list[StoreProduct],
    catalog_errors: list[str],
) -> dict[str, Any]:
    base = {
        "produto": product.name,
        "anuncio_url": product.url,
        "imagens": [],
        "status": "pendente",
    }
    if product.error or not product.listing_id:
        return {**base, "erro": product.error or "Código MLB inválido."}
    try:
        match = find_store_product(catalog, product)
        if match is None:
            reason = "Nenhum produto com modelo e variante exatos foi confirmado no catálogo oficial."
            if catalog_errors:
                reason += " Catálogo incompleto: " + "; ".join(catalog_errors[:3])
            return {**base, "erro": reason}
        _check_robots(match.product_url)
        _check_robots(match.image_url)
        data, content_type, _ = _fetch(match.image_url, MAX_IMAGE_BYTES)
        extension = image_format(data, content_type)
        converted, final_extension = _convert_if_available(data, extension)
        filename = filename_for_product(product.name, product.listing_id, final_extension)
        target = IMAGE_DIR / filename
        if target.exists():
            if target.read_bytes() != converted:
                raise ValueError("Arquivo-alvo já existe com conteúdo diferente; não foi sobrescrito.")
        else:
            _write_new(target, converted)
        relative_path = target.relative_to(ROOT).as_posix()
        return {
            **base,
            "imagens": [relative_path],
            "status": "ok",
            "produto_loja": match.name,
            "produto_url": match.product_url,
            "imagem_origem": match.image_url,
        }
    except HTTPError as error:
        if error.code in (401, 403, 407, 429):
            reason = f"A Logitech Store recusou acesso automatizado (HTTP {error.code}); sem tentativa de contornar."
        else:
            reason = f"Erro HTTP {error.code} ao acessar produto/imagem da Logitech Store."
        return {**base, "erro": reason}
    except (URLError, TimeoutError, OSError, ValueError) as error:
        return {**base, "erro": str(error)[:240]}


def _update_product_images(source: str, image_by_url: dict[str, str]) -> str:
    match = re.search(r"\bconst\s+mercadoLivreProducts\s*=\s*\[", source)
    if not match:
        raise ValueError("Lista mercadoLivreProducts não encontrada em index.html.")
    start = source.find("[", match.start())
    end = _array_end(source, start)
    array_source = source[start + 1:end]
    object_pattern = re.compile(r"\{[^{}]*\}")
    updates: list[tuple[int, int, str]] = []
    for object_match in object_pattern.finditer(array_source):
        block = object_match.group()
        values = _properties(block[1:-1])
        image_path = image_by_url.get(values.get("url", ""))
        if not image_path:
            continue
        quoted_image = json.dumps(image_path, ensure_ascii=False)
        image_property = re.compile(
            r"(\bimage\s*:\s*)('[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")"
        )
        if image_property.search(block):
            updated_block = image_property.sub(lambda found: found.group(1) + quoted_image, block, count=1)
        else:
            updated_block = block[:-1].rstrip() + f", image: {quoted_image} }}"
        updates.append((object_match.start(), object_match.end(), updated_block))
    for object_start, object_end, block in reversed(updates):
        array_source = array_source[:object_start] + block + array_source[object_end:]
    return source[:start + 1] + array_source + source[end:]


def _validate(source: str, products: list[Product], manifest: list[dict[str, Any]], old_urls: list[str]) -> list[str]:
    errors: list[str] = []
    updated_products = parse_products(source)
    if [item.url for item in updated_products] != old_urls:
        errors.append("Os links originais dos anúncios mudaram.")
    if len(updated_products) != len(products):
        errors.append("A quantidade de produtos mudou.")
    for item in updated_products:
        if item.image and not item.image.startswith(("https://", "http://")):
            if not (ROOT / item.image).is_file():
                errors.append(f"Imagem referenciada ausente: {item.image}")
    for record in manifest:
        if record.get("status") == "ok":
            for key in ("produto_url", "imagem_origem"):
                value = record.get(key)
                if not isinstance(value, str) or not _allowed_store_url(value):
                    errors.append(f"Origem oficial inválida no manifesto: {key}.")
            official_identity = " ".join(
                str(record.get(key, ""))
                for key in ("produto_loja", "produto_url", "imagem_origem")
            )
            if not _has_product_identity(str(record.get("produto", "")), official_identity):
                errors.append(f"Produto oficial não corresponde ao anúncio: {record.get('produto', '')}")
        for relative in record.get("imagens", []):
            path = _manifest_image_path(relative) if isinstance(relative, str) else None
            if path is None:
                errors.append(f"Caminho de imagem inválido no manifesto: {relative}")
                continue
            if not path.is_file():
                errors.append(f"Imagem do manifesto ausente: {relative}")
                continue
            try:
                image_format(path.read_bytes(), "application/octet-stream")
            except (OSError, ImageValidationError) as error:
                errors.append(f"Imagem inválida {relative}: {error}")
    for match in re.finditer(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", source, re.IGNORECASE | re.DOTALL):
        src = html.unescape(match.group(2))
        if src.startswith(("https://", "http://", "data:")):
            continue
        if not (ROOT / src).is_file():
            errors.append(f"src local ausente no HTML: {src}")
    return errors


def _dry_run(products: list[Product], manifest: dict[str, dict[str, Any]], force: bool) -> None:
    print(f"DRY RUN — {len(products)} anúncios identificados em index.html; nenhum arquivo será alterado.")
    for product in products:
        previous = manifest.get(product.url)
        if product.error:
            print(f"PENDENTE | {product.name} | {product.error}")
        else:
            if product.image is None:
                image_state = "sem imagem (o card usa ícone genérico)"
            elif not product.image.strip():
                image_state = "src vazio"
            elif any(token in Path(product.image).name.lower() for token in ("placeholder", "no-image", "sem-imagem")):
                image_state = f"placeholder: {product.image}"
            elif product.image.startswith(("https://", "http://")):
                image_state = "imagem remota, ainda não validada localmente"
            else:
                local_image = ROOT / product.image
                if not local_image.is_file():
                    image_state = f"imagem quebrada: {product.image}"
                else:
                    try:
                        image_format(local_image.read_bytes(), "application/octet-stream")
                        image_state = f"imagem local existente: {product.image}"
                    except ImageValidationError:
                        image_state = f"arquivo local inválido/placeholder: {product.image}"
            print(f"IMAGEM ATUAL | {product.name} | {image_state}")
            if _is_completed(previous) and not force:
                print(f"PULAR | {product.name} | imagem já processada: {', '.join(previous['imagens'])}")
            else:
                filename = filename_for_product(product.name, product.listing_id, "webp")
                print(f"PROCESSAR | {product.name} | {product.url}")
                print(f"  arquivo previsto: {IMAGE_DIR.relative_to(ROOT) / filename} (ou extensão original validada)")
    print(f"Manifesto de saída: {MANIFEST_PATH.relative_to(ROOT)}")


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--dry-run", action="store_true", help="lista os anúncios sem baixar nem alterar arquivos")
    argument_parser.add_argument("--force", action="store_true", help="tenta novamente produtos já concluídos")
    args = argument_parser.parse_args()

    try:
        original_source = INDEX_PATH.read_text(encoding="utf-8")
        products = parse_products(original_source)
        previous_manifest = _load_manifest()
    except (OSError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        _dry_run(products, previous_manifest, args.force)
        print("Fonte prevista: categorias públicas da Logitech Store; /catalogsearch/ não será acessado.")
        return 0

    old_urls = [product.url for product in products]
    records_by_url: dict[str, dict[str, Any]] = {}
    image_by_url: dict[str, str] = {}
    candidates_to_process = [
        product for product in products
        if not (_is_completed(previous_manifest.get(product.url)) and not args.force)
    ]
    catalog: list[StoreProduct] = []
    catalog_errors: list[str] = []
    if candidates_to_process:
        try:
            catalog, catalog_errors = _load_store_catalog(products)
            print(f"Catálogo público consultado: {len(catalog)} associações produto/imagem.")
            for error in catalog_errors:
                print(f"AVISO | catálogo parcial | {error}")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            catalog_errors = [str(error)[:240]]
            print(f"AVISO | catálogo indisponível | {catalog_errors[0]}")
    for product in products:
        previous = previous_manifest.get(product.url)
        if _is_completed(previous) and not args.force:
            record = previous
        else:
            record = _process_product(product, catalog, catalog_errors)
            if record["status"] != "ok" and _is_completed(previous):
                record = previous
                print(f"AVISO | {product.name} | atualização falhou; imagem anteriormente validada foi mantida.")
            elif record["status"] == "ok":
                print(f"ATUALIZADO | {product.name} | {', '.join(record['imagens'])}")
            else:
                print(f"PENDENTE | {product.name} | {record.get('erro', 'falha sem detalhes')}")
        records_by_url[product.url] = record
        images = record.get("imagens", [])
        if images:
            image_by_url[product.url] = images[0]

    new_source = _update_product_images(original_source, image_by_url)
    ordered_records = [records_by_url[product.url] for product in products]
    errors = _validate(new_source, products, ordered_records, old_urls)
    if errors:
        for error in errors:
            print(f"VALIDAÇÃO FALHOU | {error}", file=sys.stderr)
        return 1

    if new_source != original_source:
        INDEX_PATH.write_text(new_source, encoding="utf-8")
    _write_json_atomic(MANIFEST_PATH, ordered_records)

    successful = sum(record.get("status") == "ok" for record in ordered_records)
    pending = len(ordered_records) - successful
    print(f"Concluído: {successful} produtos atualizados/validados; {pending} pendentes.")
    print(f"Manifesto: {MANIFEST_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
