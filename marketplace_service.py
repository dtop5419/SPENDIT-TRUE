"""Best-effort marketplace search with a persistent SQLite cache.

The adapters read public search pages only. Marketplaces may change their markup,
require consent/login, or block automated requests; failures are isolated per provider.
"""
from __future__ import annotations

import concurrent.futures
import difflib
import html
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse, urlencode
from urllib.request import Request, urlopen

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "marketplace.db")
CACHE_TTL_SECONDS = int(os.environ.get("SPENDIT_MARKETPLACE_CACHE_TTL", "21600"))  # 6 hours
REQUEST_TIMEOUT = float(os.environ.get("SPENDIT_MARKETPLACE_TIMEOUT", "5"))
MAX_RESULTS = int(os.environ.get("SPENDIT_MARKETPLACE_MAX_RESULTS", "100"))
USER_AGENT = os.environ.get(
    "SPENDIT_MARKETPLACE_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

PROVIDERS = {
    "Wildberries": {
        "host": "www.wildberries.ru",
        "search_url": "https://www.wildberries.ru/catalog/0/search.aspx?search={query}",
    },
    "Ozon": {
        "host": "www.ozon.ru",
        "search_url": "https://www.ozon.ru/search/?text={query}",
    },
    "Яндекс Маркет": {
        "host": "market.yandex.ru",
        "search_url": "https://market.yandex.ru/search?text={query}",
    },
    # Дополнительные магазины: используются только публичные страницы поиска.
    # Сайты могут ограничивать автоматические запросы или менять разметку.
    "DNS": {
        "host": "www.dns-shop.ru",
        "search_url": "https://www.dns-shop.ru/search/?q={query}",
    },
    "Ситилинк": {
        "host": "www.citilink.ru",
        "search_url": "https://www.citilink.ru/search/?text={query}",
    },
    "М.Видео": {
        "host": "www.mvideo.ru",
        "search_url": "https://www.mvideo.ru/search?query={query}",
    },
    "Авито": {
        "host": "www.avito.ru",
        "search_url": "https://www.avito.ru/rossiya?q={query}",
    },
}


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query_key TEXT NOT NULL,
            provider TEXT NOT NULL,
            updated_at REAL NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            PRIMARY KEY(query_key, provider)
        );
        CREATE TABLE IF NOT EXISTS marketplace_offer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_key TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL,
            currency TEXT NOT NULL DEFAULT 'RUB',
            url TEXT NOT NULL,
            image TEXT,
            updated_at REAL NOT NULL,
            UNIQUE(provider, external_id)
        );
        CREATE INDEX IF NOT EXISTS ix_marketplace_offer_query
            ON marketplace_offer(query_key, updated_at);
        CREATE TABLE IF NOT EXISTS search_catalog (
            phrase TEXT PRIMARY KEY,
            category TEXT NOT NULL
        );
    """)
    conn.executemany("INSERT OR IGNORE INTO search_catalog(phrase, category) VALUES(?, ?)", SEARCH_CATALOG)
    conn.commit()
    return conn


SEARCH_CATALOG = [
    (phrase, category)
    for category, phrases in {
        "Электроника": "смартфон телефон планшет электронная книга ноутбук компьютер моноблок монитор клавиатура мышь веб камера микрофон наушники гарнитура колонка портативная колонка саундбар телевизор проектор приставка игровая консоль роутер модем внешний диск ssd флешка карта памяти зарядка кабель powerbank умные часы фитнес браслет фотоаппарат объектив экшн камера видеорегистратор принтер сканер графический планшет видеокарта процессор оперативная память материнская плата блок питания корпус компьютера охлаждение геймпад джойстик",
        "Дом и кухня": "пылесос робот пылесос отпариватель утюг стиральная машина сушильная машина холодильник морозильник микроволновка мультиварка аэрогриль блендер миксер кофемашина кофемолка чайник тостер духовка варочная панель посудомоечная машина фильтр воды увлажнитель воздуха очиститель воздуха обогреватель вентилятор кондиционер весы кухонные весы напольные сковорода кастрюля ножи посуда контейнер для еды термос постельное белье подушка одеяло матрас полотенце шторы светильник лампа настольная лампа мебель кресло офисное кресло стол письменный шкаф стеллаж зеркало",
        "Одежда и обувь": "футболка рубашка худи свитер куртка пальто пуховик джинсы брюки шорты платье юбка спортивный костюм пижама носки нижнее белье кроссовки кеды ботинки сапоги сандалии тапочки рюкзак сумка кошелек ремень шапка перчатки шарф очки солнцезащитные часы наручные",
        "Красота и здоровье": "фен выпрямитель волос плойка машинка для стрижки триммер электробритва зубная щетка ирригатор массажер тонометр термометр увлажнитель кожи косметика парфюм крем шампунь уход за кожей маникюрный набор эпилятор весы диагностические",
        "Спорт и отдых": "велосипед самокат электросамокат ролики скейтборд палатка спальник туристический рюкзак фонарь термос коврик йога гантели гиря штанга эспандер тренажер беговая дорожка ракетка теннис мяч футбольный мяч баскетбольный мяч коньки лыжи сноуборд удочка спиннинг чемодан дорожная сумка гамак",
        "Детские товары": "коляска автокресло детская кроватка стульчик для кормления игрушка конструктор кукла машинка игрушечная настольная игра детский велосипед детский самокат школьный рюкзак пенал детская одежда подгузники",
        "Авто и инструменты": "автомобильный компрессор домкрат видеорегистратор автомобильный пылесос зарядное устройство аккумулятор шины автомобильные коврики органайзер инструменты шуруповерт дрель перфоратор болгарка лобзик набор инструментов отвертка рулетка лестница краска",
        "Книги и канцелярия": "книга учебник блокнот ежедневник ручка карандаш маркеры принтерная бумага органайзер настольный календарь электронная книга",
        "Товары для животных": "корм для кошек корм для собак лоток когтеточка переноска лежанка игрушка для кошек ошейник поводок миска фонтан для животных",
    }.items()
    for phrase in phrases.split() if len(phrase) > 2 and phrase not in {"для", "или", "что", "как", "мне", "до", "руб", "цена"}
]

STOP_WORDS = {"для", "или", "что", "как", "мне", "нужен", "нужна", "нужно", "купить", "найди", "подбери", "пожалуйста", "до", "руб", "рублей", "цена", "дешево", "лучший", "лучшая", "лучшие", "новый", "новая", "новые", "товар", "товары", "маркете", "маркетплейсе"}


def _normalise_query(query):
    value = query.casefold().replace("ё", "е")
    # Normalize common everyday variants before typo correction and relevance checks.
    synonyms = {
        r"\bмобил(?:ьник|а|у|ом|е)?\b": "телефон",
        r"\bсмартфоны\b": "смартфон",
        r"\bайфон(?:ы|а|у|ом|е)?\b": "iphone телефон",
        r"\biphone\b": "iphone телефон",
        r"\bноуты\b": "ноутбук",
        r"\bноут(?:а|ы|буки|бука|буке|буком)?\b": "ноутбук",
    }
    for pattern, replacement in synonyms.items():
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"(\d+(?:[.,]\d+)?)\s*к\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), value)
    value = re.sub(r"(\d+(?:[.,]\d+)?)\s*тыс(?:яч|\.)?\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), value)
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    words = value.split()
    vocabulary = [phrase for phrase, _ in SEARCH_CATALOG]
    corrected = []
    for word in words:
        if len(word) >= 4 and word not in vocabulary:
            match = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.79)
            corrected.append(match[0] if match else word)
        else:
            corrected.append(word)
    return " ".join(corrected)


def _tokens(value):
    return {w for w in re.findall(r"[a-zа-я0-9]+", (value or "").casefold().replace("ё", "е")) if len(w) > 1 and w not in STOP_WORDS and not w.isdigit()}


def _relevance(query, title):
    wanted, actual = _tokens(query), _tokens(title)
    if not wanted or not actual:
        return 0.0
    direct = len(wanted & actual) / len(wanted)
    fuzzy = 0.0
    for token in wanted - actual:
        if any(difflib.SequenceMatcher(None, token, candidate).ratio() >= 0.82 for candidate in actual):
            fuzzy += 1
    return min(1.0, (len(wanted & actual) + fuzzy * 0.75) / len(wanted))


def _budget(query):
    query = re.sub(r"(\d+(?:[.,]\d+)?)\s*к\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), query.casefold())
    query = re.sub(r"(\d+(?:[.,]\d+)?)\s*тыс(?:яч|\.)?\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), query)
    match = re.search(r"(?:до|не дороже|максимум|бюджет\s*[:=]?)\s*(\d[\d\s.,]*)\s*(?:₽|руб(?:лей|ля)?|р\b)?", query)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    try:
        value = float(digits)
        return value if 0 < value < 100000000 else None
    except ValueError:
        return None


class _StructuredDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.parts = []
        self.scripts = []
        self.meta = {}
        self._script_type = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "script":
            self._script_type = attrs.get("type", "").lower()
            self.in_jsonld = self._script_type == "application/ld+json" or "json" in self._script_type or attrs.get("id") == "__NEXT_DATA__"
            self.parts = []
        elif tag.lower() == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            val = attrs.get("content")
            if key and val:
                self.meta[key.lower()] = val

    def handle_data(self, data):
        if self.in_jsonld:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_jsonld:
            body = "".join(self.parts).strip()
            if body:
                self.scripts.append(body)
            self.in_jsonld = False
            self.parts = []


def _valid_url(value, base_url, allowed_host):
    if not isinstance(value, str) or not value.strip():
        return None
    value = html.unescape(value.strip())
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.hostname.lower() != allowed_host and not parsed.hostname.lower().endswith("." + allowed_host):
        return None
    return absolute.split("#", 1)[0]


def _valid_image(value, base_url):
    if not isinstance(value, str) or not value.strip():
        return None
    absolute = urljoin(base_url, html.unescape(value.strip()))
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return absolute


def _price(value):
    if isinstance(value, list):
        for item in value[:3]:
            parsed = _price(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        value = value.get("price") or value.get("amount") or value.get("value")
    if isinstance(value, (int, float)):
        return float(value) if 0 < float(value) < 100000000 else None
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\u00a0", " ").replace("\u202f", " ")
    match = re.search(r"(?<!\d)(\d{1,3}(?:[\s.,]\d{3})+|\d{2,8})(?:[.,]\d{1,2})?", cleaned)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace("\u00a0", "")
    raw = raw.replace(",", "").replace(".", "") if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw) else raw.replace(",", ".")
    try:
        result = float(raw)
        return result if 0 < result < 100000000 else None
    except ValueError:
        return None


def _walk_products(value, out, base_url, host, depth=0):
    if depth > 18 or len(out) >= 40:
        return
    if isinstance(value, dict):
        typ = value.get("@type", "")
        types = typ if isinstance(typ, list) else [typ]
        name = value.get("name") or value.get("title") or value.get("productName")
        offers = value.get("offers") or value.get("offer") or value.get("priceInfo") or {}
        price = _price(offers)
        if price is None:
            price = _price(value.get("price") or value.get("priceValue") or value.get("price_amount"))
        raw_url = value.get("url") or value.get("productUrl") or value.get("canonicalUrl") or value.get("link")
        image = value.get("image") or value.get("imageUrl") or value.get("picture") or value.get("thumbnail")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        url = _valid_url(raw_url, base_url, host)
        if isinstance(name, str) and len(name.strip()) >= 4 and url and price is not None:
            name = re.sub(r"\s+", " ", html.unescape(name)).strip()[:250]
            if not any(x["url"] == url for x in out):
                out.append({"name": name, "price": price, "url": url, "image": _valid_image(image, base_url) if image else None})
        for key, child in value.items():
            if key in ("offers", "offer", "priceInfo", "price", "image", "thumbnail"):
                continue
            _walk_products(child, out, base_url, host, depth + 1)
    elif isinstance(value, list):
        for item in value[:500]:
            _walk_products(item, out, base_url, host, depth + 1)


def _extract_products(body, base_url, host):
    parser = _StructuredDataParser()
    try:
        parser.feed(body)
    except Exception:
        pass
    found = []
    for chunk in parser.scripts:
        if len(chunk) > 8_000_000:
            continue
        try:
            data = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        _walk_products(data, found, base_url, host)
        if len(found) >= 20:
            break
    if not found:
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title")
        raw_price = parser.meta.get("product:price:amount") or parser.meta.get("og:price:amount")
        raw_url = parser.meta.get("og:url")
        image = parser.meta.get("og:image") or parser.meta.get("twitter:image")
        url = _valid_url(raw_url, base_url, host)
        price = _price(raw_price)
        if title and url and price:
            found.append({"name": html.unescape(title).strip()[:250], "price": price, "url": url, "image": _valid_image(image, base_url) if image else None})
    return found[:12]


def _fetch_wildberries(query):
    """Fetch WB search results with API and public-search-page fallbacks.

    WB occasionally changes API versions or blocks a request without browser-like
    headers. Try a small set of public endpoints, then parse the normal search page.
    """
    endpoints = [
        "https://search.wb.ru/exactmatch/ru/common/v18/search?" + urlencode({
            "ab_testing": "false", "appType": "1", "curr": "rub", "dest": "-1257786",
            "query": query, "resultset": "catalog", "sort": "popular", "spp": "30",
            "suppressSpellcheck": "false", "page": "1"
        }),
        "https://search.wb.ru/exactmatch/ru/common/v9/search?" + urlencode({
            "appType": "1", "curr": "rub", "dest": "-1257786", "query": query,
            "resultset": "catalog", "sort": "popular", "page": "1"
        }),
    ]
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    last_error = None
    for endpoint in endpoints:
        try:
            request = Request(endpoint, headers=headers)
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                data = json.loads(response.read(5_000_000).decode("utf-8", errors="replace"))
            products = data.get("data", {}).get("products", [])
            if not products and isinstance(data.get("products"), list):
                products = data["products"]
            found = _wildberries_products(products)
            if found:
                return found
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:140]}"

    # Fallback: use the public search page, which sometimes contains product JSON
    # even when the JSON endpoint is unavailable.
    try:
        page_url = "https://www.wildberries.ru/catalog/0/search.aspx?search=" + quote_plus(query)
        request = Request(page_url, headers={**headers, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            final_url = response.geturl()
            body = response.read(5_000_000).decode("utf-8", errors="replace")
        found = _extract_products(body, final_url, "www.wildberries.ru")
        if found:
            return found
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {str(exc)[:140]}"
    if last_error:
        raise RuntimeError(last_error)
    return []


def _wildberries_products(products):
    found = []
    for item in products[:40]:
        name = str(item.get("name") or item.get("brand") or "").strip()
        price_data = item.get("salePriceU") or item.get("priceU")
        price = float(price_data) / 100 if isinstance(price_data, (int, float)) else None
        product_id = item.get("id") or item.get("nmId")
        if not name or not product_id or not price or price <= 0:
            continue
        image = None
        try:
            vol = int(product_id) // 100000
            part = int(product_id) // 1000
            basket = _wb_basket(vol)
            image = f"https://{basket}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp"
        except (TypeError, ValueError):
            pass
        found.append({"name": name[:250], "price": price, "url": f"https://www.wildberries.ru/catalog/{product_id}/detail.aspx", "image": image})
    return found


def _wb_basket(vol):
    if vol <= 143:
        return "basket-01"
    if vol <= 287:
        return "basket-02"
    if vol <= 431:
        return "basket-03"
    if vol <= 719:
        return "basket-04"
    if vol <= 1007:
        return "basket-05"
    if vol <= 1061:
        return "basket-06"
    if vol <= 1115:
        return "basket-07"
    if vol <= 1169:
        return "basket-08"
    if vol <= 1313:
        return "basket-09"
    if vol <= 1601:
        return "basket-10"
    if vol <= 1655:
        return "basket-11"
    if vol <= 1919:
        return "basket-12"
    if vol <= 2045:
        return "basket-13"
    if vol <= 2195:
        return "basket-14"
    return "basket-15"



def _fetch_search_engine(query, provider, config):
    """Fallback: parse indexed results for a provider when its page is JS-only.

    Search snippets are used only when they contain an explicit price; links remain
    restricted to the requested provider's domain.
    """
    from urllib.parse import quote_plus
    search_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(f'site:{config["host"]} {query}')
    req = Request(search_url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        body = response.read(3_000_000).decode("utf-8", errors="replace")
    parser = _SearchResultParser()
    parser.feed(body)
    found = []
    for title, href, snippet in parser.results:
        url = _valid_url(href, "https://html.duckduckgo.com/", config["host"])
        if not url or not title or _relevance(query, title) < 0.25:
            continue
        price = _price(snippet)
        if price is None:
            continue
        found.append({"name": title[:250], "price": price, "url": url, "image": None})
        if len(found) >= 8:
            break
    return found


class _SearchResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results=[]; self.current=None; self.capture=None; self.buf=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == "a" and "result__a" in a.get("class", ""):
            self.current={"title":"", "url":a.get("href", ""), "snippet":""}; self.capture="title"; self.buf=[]
        elif tag in ("a", "div", "td") and self.current and ("result__snippet" in a.get("class", "")):
            self.capture="snippet"; self.buf=[]
    def handle_data(self, data):
        if self.current and self.capture: self.buf.append(data)
    def handle_endtag(self, tag):
        if self.current and self.capture and ((self.capture=="title" and tag=="a") or (self.capture=="snippet" and tag in ("a","div","td"))):
            self.current[self.capture]=" ".join("".join(self.buf).split()); self.capture=None; self.buf=[]
            if self.current.get("title") and self.current.get("snippet"):
                self.results.append((self.current["title"], self.current["url"], self.current["snippet"])); self.current=None

def _fetch_provider(provider, query):
    config = PROVIDERS[provider]
    search_url = config["search_url"].format(query=quote_plus(query))
    if provider == "Wildberries":
        try:
            offers = _fetch_wildberries(query)
            return provider, offers, None if offers else "page returned no products"
        except Exception as exc:
            return provider, [], f"{type(exc).__name__}: {str(exc)[:180]}"
    request = Request(search_url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7", "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            final_url = response.geturl()
            final_host = (urlparse(final_url).hostname or "").lower()
            if final_host != config["host"] and not final_host.endswith("." + config["host"]):
                return provider, [], "redirected outside marketplace"
            body = response.read(5_000_000).decode("utf-8", errors="replace")
        offers = _extract_products(body, final_url, config["host"])
        if not offers:
            try:
                offers = _fetch_search_engine(query, provider, config)
            except Exception:
                offers = []
        return provider, offers, None if offers else "direct page and indexed fallback returned no priced products"
    except Exception as exc:
        try:
            offers = _fetch_search_engine(query, provider, config)
            return provider, offers, None if offers else f"{type(exc).__name__}: {str(exc)[:140]}"
        except Exception:
            return provider, [], f"{type(exc).__name__}: {str(exc)[:180]}"


def _shopping_query(query):
    q = query.strip().lower()
    if len(q) < 2:
        return False
    nonshopping = (
        "привет", "здравствуй", "спасибо", "что такое", "объясни", "расскажи", "как работает",
        "погода", "который час", "какое сегодня число", "vpn", "инфляц", "api", "flask",
    )
    if any(q.startswith(prefix) for prefix in nonshopping):
        return False
    return True


def search_marketplaces(query, force_refresh=False):
    original = re.sub(r"\s+", " ", str(query or "")).strip()[:180]
    if not _shopping_query(original):
        return {"offers": [], "searched": False, "updated_at": None, "providers": {}, "search_links": {}, "corrected_query": original}
    query = _normalise_query(original)
    search_links = {name: config["search_url"].format(query=quote_plus(query)) for name, config in PROVIDERS.items()}
    key = query.casefold()
    now = time.time()
    budget = _budget(original)
    conn = _connect()
    try:
        cached_rows = conn.execute("SELECT * FROM marketplace_offer WHERE query_key=? ORDER BY price ASC LIMIT ?", (key, MAX_RESULTS)).fetchall()
        cache_rows = conn.execute("SELECT * FROM search_cache WHERE query_key=?", (key,)).fetchall()
        # A failed/empty provider response must not be cached for six hours:
        # marketplace anti-bot pages and API hiccups are often temporary.
        fresh = (len(cache_rows) == len(PROVIDERS)
                 and all(now - r["updated_at"] < CACHE_TTL_SECONDS
                         and not r["error"] and int(r["result_count"] or 0) > 0
                         for r in cache_rows))
        if fresh and not force_refresh:
            offers = [_offer_dict(r) for r in cached_rows]
            offers = _filter_offers(query, offers, budget)
            local = _search_saved(conn, query, budget, key)
            offers = _merge_offers(local, offers)[:MAX_RESULTS]
            return {"offers": offers, "searched": True, "updated_at": max((r["updated_at"] for r in cache_rows), default=None),
                    "providers": {r["provider"]: {"count": r["result_count"], "error": r["error"]} for r in cache_rows}, "cached": True,
                    "search_links": search_links, "corrected_query": query if query != original else None}
    finally:
        conn.close()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(PROVIDERS))) as pool:
        futures = [pool.submit(_fetch_provider, provider, query) for provider in PROVIDERS]
        done, pending = concurrent.futures.wait(futures, timeout=REQUEST_TIMEOUT + 2)
        for future in done:
            try:
                results.append(future.result())
            except Exception:
                continue
        for future in pending:
            future.cancel()
    conn = _connect()
    try:
        providers_status = {}
        for provider in PROVIDERS:
            item = next((x for x in results if x[0] == provider), (provider, [], "provider timeout"))
            _, offers, error = item
            offers = _filter_offers(query, offers, budget)
            providers_status[provider] = {"count": len(offers), "error": error}
            if offers:
                conn.execute("DELETE FROM marketplace_offer WHERE query_key=? AND provider=?", (key, provider))
                for offer in offers:
                    conn.execute("""INSERT INTO marketplace_offer
                        (query_key,provider,external_id,name,price,currency,url,image,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(provider,external_id) DO UPDATE SET
                        query_key=excluded.query_key,name=excluded.name,price=excluded.price,
                        currency=excluded.currency,url=excluded.url,image=excluded.image,updated_at=excluded.updated_at
                    """, (key, provider, offer["url"], offer["name"], offer["price"], "RUB", offer["url"], offer.get("image"), now))
            conn.execute("""INSERT INTO search_cache(query_key,provider,updated_at,result_count,error)
                VALUES(?,?,?,?,?) ON CONFLICT(query_key,provider) DO UPDATE SET
                updated_at=excluded.updated_at,result_count=excluded.result_count,error=excluded.error""",
                (key, provider, now, len(offers), error))
        conn.commit()
        rows = conn.execute("SELECT * FROM marketplace_offer WHERE query_key=? ORDER BY price ASC LIMIT ?", (key, MAX_RESULTS)).fetchall()
        live_offers = _filter_offers(query, [_offer_dict(r) for r in rows], budget)
        saved = _search_saved(conn, query, budget, key)
        offers = _merge_offers(saved, live_offers)[:MAX_RESULTS]
        return {"offers": offers, "searched": True, "updated_at": now, "providers": providers_status,
                "cached": False, "search_links": search_links, "corrected_query": query if query != original else None}
    finally:
        conn.close()


def _filter_offers(query, offers, budget=None):
    result = []
    for offer in offers:
        name = offer.get("name", "")
        score = _relevance(query, name)
        query_tokens = _tokens(query)
        if query_tokens and score < 0.28:
            continue
        price = offer.get("price")
        if budget is not None and price is not None and price > budget:
            continue
        offer["relevance"] = score
        result.append(offer)
    result.sort(key=lambda x: (-(x.get("relevance") or 0), x.get("price") or float("inf")))
    return result


def _search_saved(conn, query, budget, current_key):
    rows = conn.execute("SELECT * FROM marketplace_offer WHERE query_key<>? ORDER BY updated_at DESC LIMIT 1500", (current_key,)).fetchall()
    candidates = []
    for row in rows:
        offer = _offer_dict(row)
        score = _relevance(query, offer["name"])
        if score < 0.5:
            continue
        if budget is not None and offer.get("price") is not None and offer["price"] > budget:
            continue
        offer["relevance"] = score
        candidates.append(offer)
    candidates.sort(key=lambda x: (-(x.get("relevance") or 0), x.get("price") or float("inf")))
    return candidates[:MAX_RESULTS]


def _merge_offers(*groups):
    unique = {}
    for group in groups:
        for offer in group:
            url = offer.get("url")
            if not url:
                continue
            if url not in unique or (offer.get("relevance", 0) > unique[url].get("relevance", 0)):
                unique[url] = offer
    return sorted(unique.values(), key=lambda x: (-(x.get("relevance") or 0), x.get("price") or float("inf")))


def seed_catalog(limit=100):
    conn = _connect()
    try:
        all_phrases = [row[0] for row in conn.execute("SELECT phrase FROM search_catalog ORDER BY category, phrase").fetchall()]
        count = max(1, min(int(limit), len(all_phrases)))
        step = max(1, len(all_phrases) // count)
        phrases = all_phrases[::step][:count]
    finally:
        conn.close()
    total = 0
    for phrase in phrases:
        result = search_marketplaces(phrase, force_refresh=True)
        total += len(result.get("offers", []))
        time.sleep(0.35)
    return {"queries": len(phrases), "offers": total}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.seed:
        print(json.dumps(seed_catalog(args.limit), ensure_ascii=False))


def _offer_dict(row):
    return {"name": row["name"], "price": row["price"], "url": row["url"], "image": row["image"],
            "provider": row["provider"], "updated_at": row["updated_at"], "source": "marketplace"}
