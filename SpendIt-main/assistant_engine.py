"""SpendIt Assistant: deterministic, explainable intent parsing and catalog reasoning."""
import ast
import datetime as dt
import operator
import re
from urllib.parse import quote_plus


INTENT_WORDS = {
    "greeting": ("привет", "здравствуй", "добрый день", "доброе утро", "добрый вечер", "хай", "hello"),
    "thanks": ("спасибо", "благодарю", "спс"),
    "identity": ("кто ты", "что ты за бот", "что ты такое", "как ты работаешь"),
    "capabilities": ("что ты умеешь", "что умеешь", "помоги", "как пользоваться"),
    "weather": ("погода", "температур", "прогноз", "на улице"),
    "time": ("который час", "сколько времени", "время сейчас", "текущее время"),
    "date": ("какое сегодня число", "какая сегодня дата", "сегодняшняя дата", "какой сегодня день"),
}

CATEGORY_SYNONYMS = {
    "Развлечения": ("отдых", "развлеч", "досуг", "весель", "развлекух", "развеяться", "эмоции", "приключения"),
    "Игровые консоли": ("консол", "playstation", "ps5", "xbox", "игровая приставка"),
    "Ноутбуки": ("ноут", "ноутбук", "лэптоп", "компьютер для учебы", "компьютер для работы"),
    "Комплектующие": ("комплектующ", "процессор", "видеокарт", "gpu", "cpu", "оператив", "озу", "ssd", "диск", "пк"),
    "Периферия": ("мышь", "мышка", "клавиатур", "перифер", "вебкам", "микрофон"),
    "Наушники": ("наушник", "гарнитур", "airpods", "наушники"),
    "Аудио": ("колонк", "музык", "звук", "портативная акустика"),
    "Смартфоны": ("телефон", "смартфон", "айфон", "iphone", "android", "самсунг"),
    "Смарт-часы": ("умные часы", "смарт часы", "watch", "часы для спорта"),
    "Гаджеты": ("гаджет", "электронная книга", "kindle", "технологи"),
    "Для дома": ("дом", "уборк", "пылесос", "чайник", "кухн", "airfryer", "бытов"),
    "Мебель": ("кресло", "стол", "мебел", "офисное кресло"),
    "Камеры": ("камер", "фото", "фотограф", "gopro", "видео"),
    "Образование": ("обуч", "курс", "учеб", "саморазвит", "книг", "читать", "python"),
    "Спорт": ("спорт", "трениров", "фитнес", "бег", "велосипед", "активн"),
    "Путешествия": ("путешеств", "поездк", "отдых за городом", "тур", "выходные"),
    "Подарки": ("подар", "на день рождения", "сюрприз"),
}

GOAL_SYNONYMS = {
    "gaming": ("игр", "гейм", "игров", "gaming", "fps", "steam"),
    "work": ("работ", "офис", "продуктив", "монтаж", "програм", "код", "фриланс"),
    "study": ("учеб", "учёб", "школ", "универ", "студент", "курс"),
    "relax": ("отдых", "досуг", "развлеч", "релакс", "расслаб", "вечер"),
    "gift": ("подар", "сюрприз", "день рождения", "юбилей"),
    "home": ("дом", "квартир", "кухн", "уборк"),
}

PRIORITY_WORDS = {
    "cheap": ("дешев", "эконом", "подешевле", "бюджетн", "выгод", "доступн"),
    "performance": ("мощн", "производител", "быстр", "топ", "максимум", "флагман"),
    "quality": ("качеств", "надёж", "надеж", "лучший", "проверен"),
    "balanced": ("оптималь", "сбаланс", "цена/качество", "разумн", "лучшее соотношение"),
}


def _contains(text, words):
    return any(w in text for w in words)


def normalize_text(text):
    value = text.lower().strip()
    value = re.sub(r"\bжо\b", "до", value)
    value = value.replace("ё", "е")
    value = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*к\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), value)
    value = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*тыс(?:яч|\.)?\b", lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), value)
    return value


def parse_budget(text):
    text = normalize_text(text)
    numbers = [int(n.replace(" ", "")) for n in re.findall(r"\d[\d ]{2,}", text)]
    if not numbers:
        numbers = [int(n) for n in re.findall(r"\b\d{3,6}\b", text)]
    if not numbers:
        return None
    if "до" in text or "не больше" in text or "максимум" in text or "в пределах" in text or "не дороже" in text:
        return (0, max(numbers))
    if "от" in text and len(numbers) == 1:
        return (numbers[0], None)
    if len(numbers) >= 2:
        return (min(numbers), max(numbers))
    return (0, numbers[0])


def parse_intent(text):
    lower = normalize_text(text)
    for intent, words in INTENT_WORDS.items():
        if _contains(lower, words):
            return intent
    return "catalog"


def infer_categories(text):
    found = []
    lower = normalize_text(text)
    for category, words in CATEGORY_SYNONYMS.items():
        score = sum(1 for word in words if word in lower)
        if score:
            found.append((category, score))
    return sorted(found, key=lambda x: x[1], reverse=True)


def infer_goal(text):
    lower = normalize_text(text)
    found = []
    for goal, words in GOAL_SYNONYMS.items():
        score = sum(1 for word in words if word in lower)
        if score:
            found.append((goal, score))
    return sorted(found, key=lambda x: x[1], reverse=True)[0][0] if found else None


def infer_priority(text):
    lower = normalize_text(text)
    for priority, words in PRIORITY_WORDS.items():
        if _contains(lower, words):
            return priority
    return "balanced"


def tokenise(text):
    stop = {"до", "для", "что", "есть", "мне", "хочу", "нужен", "нужна", "можно", "какой", "какую", "чтобы", "и", "или", "на", "в", "с", "по"}
    return [x for x in re.findall(r"[a-zа-яё0-9-]+", text.lower()) if len(x) > 2 and x not in stop]


def _safe_calc(expr):
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
    tree = ast.parse(expr, mode="eval")
    def calc(node):
        if isinstance(node, ast.Expression): return calc(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)): return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in operators: return operators[type(node.op)](calc(node.left), calc(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = calc(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        raise ValueError
    return calc(tree)


def _product_text(product):
    return " ".join([
        str(getattr(product, "name", "")),
        str(getattr(product, "category", "")),
        str(getattr(product, "desc", "")),
        str(getattr(product, "details", "")),
        str(getattr(product, "tags", "")),
    ]).lower()


def rank_products(products, query, user=None):
    lower = query.lower()
    tokens = tokenise(query)
    categories = dict(infer_categories(query))
    goal = infer_goal(query)
    priority = infer_priority(query)
    budget = parse_budget(lower)
    user_interests = set(x.strip().lower() for x in (getattr(user, "interests", "") or "").split(",") if x.strip())
    user_brands = set(x.strip().lower() for x in (getattr(user, "brands", "") or "").split(",") if x.strip())
    scored = []
    for product in products:
        hay = _product_text(product)
        score = 0.0
        reasons = []
        for token in tokens:
            if token in hay:
                score += 7
        for category, cat_score in categories.items():
            if str(product.category).lower() == category.lower():
                score += 28 + 5 * cat_score
                reasons.append(category)
        if budget:
            low, high = budget
            price = int(product.price)
            if high is not None:
                if price <= high:
                    score += 24
                    reasons.append("в бюджете")
                else:
                    over = price - high
                    score -= min(35, over / max(high, 1) * 50)
            if low and price >= low:
                score += 6
        if goal:
            goal_hits = {"gaming": ("игры", "gaming", "гейм", "консоль", "gpu", "процессор"),
                         "work": ("работ", "офис", "мышь", "клавиат", "ноутбук", "ssd"),
                         "study": ("учеб", "книг", "ноутбук", "курс", "образован"),
                         "relax": ("развлеч", "музык", "аудио", "игр", "консол", "путешеств"),
                         "gift": ("подар", "аксессуары", "развлеч", "гаджет"),
                         "home": ("дом", "кух", "уборк", "мебель")}.get(goal, ())
            if any(x in hay for x in goal_hits):
                score += 17
                reasons.append(goal)
        if priority == "cheap":
            score += max(0, 18 - product.price / 5000)
        elif priority == "performance":
            score += float(product.rating or 0) * 2 + (12 if getattr(product, "featured", False) else 0)
        elif priority == "quality":
            score += float(product.rating or 0) * 4
        else:
            score += float(product.rating or 0) * 2
            if getattr(product, "original_price", None) and product.original_price > product.price:
                score += 5
        if user_interests and any(i in hay for i in user_interests):
            score += 9
            reasons.append("ваши интересы")
        if user_brands and any(b in hay for b in user_brands):
            score += 6
            reasons.append("предпочтительный бренд")
        scored.append((score, product, reasons))
    if budget and budget[1] is not None:
        within = [row for row in scored if int(row[1].price) <= budget[1]]
        if within:
            scored = within
    if budget and budget[1] is not None:
        within = [row for row in scored if int(row[1].price) <= budget[1]]
        if within:
            scored = within
    scored.sort(key=lambda x: (x[0], float(getattr(x[1], "rating", 0) or 0)), reverse=True)
    return scored[:5], {"budget": budget, "categories": [x[0] for x in infer_categories(query)], "goal": goal, "priority": priority}


def explain_recommendation(meta, matches):
    parts = []
    budget = meta["budget"]
    if budget and budget[1] is not None:
        parts.append(f"учёл лимит до {budget[1]:,} ₽".replace(",", " "))
    if meta["categories"]:
        parts.append("связал запрос с " + ", ".join(meta["categories"][:2]).lower())
    if meta["goal"]:
        labels = {"gaming": "игровым сценарием", "work": "работой", "study": "учёбой", "relax": "отдыхом", "gift": "подарком", "home": "домом"}
        parts.append("подстроил под " + labels.get(meta["goal"], meta["goal"]))
    if meta["priority"] != "balanced":
        labels = {"cheap": "экономию", "performance": "производительность", "quality": "качество"}
        parts.append("приоритет " + labels.get(meta["priority"], meta["priority"]))
    return "; ".join(parts) if parts else "по смыслу запроса и характеристикам каталога"


def answer(message, products, user=None):
    text = message.strip()
    lower = normalize_text(text)
    if not text:
        return {"ok": False, "answer": "Напишите вопрос или задачу — я попробую понять, что именно вам нужно.", "kind": "chat"}

    url_match = re.search(r"https?://[^\s]+", text)
    if url_match:
        url = url_match.group(0).rstrip(".,!?)]")
        rest = (text.replace(url, "").strip(" ,.!?"))
        msg = "Это ссылка. Могу открыть её в новой вкладке."
        if rest:
            msg += f" А остальной запрос я тоже понял как: «{rest}»."
        return {"ok": True, "answer": msg, "link": url, "kind": "link"}

    intent = parse_intent(text)
    if intent == "greeting":
        return {"ok": True, "answer": "Привет! Я SpendIt Assistant. Можешь писать обычным языком: «хочу отдохнуть до 5000», «нужен ноут для учёбы», «что взять для игр» — я сам разберу запрос и посмотрю, что есть в текущем каталоге.", "kind": "chat"}
    if intent == "thanks":
        return {"ok": True, "answer": "Всегда пожалуйста 😎 Если нужно, можешь просто описать задачу своими словами — формулировка не обязана быть точной.", "kind": "chat"}
    if intent == "identity":
        return {"ok": True, "answer": "Я встроенный помощник SpendIt. У меня нет полноценной большой языковой модели, но я умею разбирать смысловые подсказки, бюджеты, категории, цели покупки, приоритеты и подбирать варианты из активной базы.", "kind": "chat"}
    if intent == "capabilities":
        return {"ok": True, "answer": "Я понимаю обычные фразы, бюджеты («до 5000», «3000–10000»), задачи («для игр», «для дома», «на подарок»), синонимы категорий и могу объяснить, почему предложил товар. Также умею считать, распознавать ссылки, отвечать на базовые вопросы о времени и погоде и давать навигационные подсказки.", "kind": "chat"}
    faq = {
        "что такое инфляция": "Инфляция — это устойчивый рост общего уровня цен. Проще: со временем на ту же сумму обычно можно купить меньше товаров и услуг.",
        "что такое vpn": "VPN создаёт защищённое соединение между вашим устройством и VPN-сервером. Он может скрывать ваш реальный IP от сайтов и защищать трафик на небезопасных сетях, но не делает пользователя полностью анонимным.",
        "что такое api": "API — это набор правил, по которым одна программа обращается к другой. Например, сайт может запросить у API каталог товаров и получить данные в JSON.",
        "что такое flask": "Flask — лёгкий Python-фреймворк для создания веб-приложений. SpendIt как раз построен на Flask.",
    }
    for question, reply in faq.items():
        if question in lower:
            return {"ok": True, "answer": reply, "kind": "chat"}
    percent = re.search(r"(\d+(?:[.,]\d+)?)\s*%.*?от\s*(\d+(?:[.,]\d+)?)", lower)
    if percent:
        result = float(percent.group(1).replace(",", ".")) * float(percent.group(2).replace(",", ".")) / 100
        return {"ok": True, "answer": f"{percent.group(1)}% от {percent.group(2)} = {result:g}", "kind": "calculator"}
    if intent == "time":
        now = dt.datetime.now()
        return {"ok": True, "answer": f"Сейчас {now:%H:%M} по времени сервера SpendIt.", "kind": "chat"}
    if intent == "date":
        now = dt.datetime.now()
        return {"ok": True, "answer": f"Сегодня {now:%d.%m.%Y}.", "kind": "chat"}
    if intent == "weather":
        city_match = re.search(r"(?:в|для)\s+([а-яёa-z\- ]{2,40})", lower)
        city = city_match.group(1).strip() if city_match else "ваш город"
        return {"ok": True, "answer": f"Для «{city}» лучше открыть актуальный прогноз: я не буду выдумывать температуру без живых погодных данных.", "link": "https://www.google.com/search?q=" + quote_plus(city + " погода"), "kind": "weather"}

    expr = text.replace(" ", "")
    if re.fullmatch(r"[0-9()+\-*/%.]+", expr) and any(ch.isdigit() for ch in expr):
        try:
            value = _safe_calc(expr)
            return {"ok": True, "answer": f"Ответ: {value:g}" if isinstance(value, float) else f"Ответ: {value}", "kind": "calculator"}
        except Exception:
            pass

    matches, meta = rank_products(products, text, user)
    strong = [x for x in matches if x[0] > 8]
    if strong:
        explanation = explain_recommendation(meta, strong)
        category_text = ", ".join(meta["categories"][:2]) if meta["categories"] else "нужный сценарий"
        title = f"Подобрал варианты под {category_text.lower()}" if meta["categories"] else "Подобрал варианты по смыслу запроса"
        product_payload = []
        for score, p, reasons in strong[:4]:
            why = ", ".join(reasons[:3]) or "похож по задаче"
            product_payload.append({"id": p.id, "name": p.name, "price": p.price, "url": None, "why": why})
        answer_text = f"{title}. Я не требую точного названия товара: {explanation}."
        return {"ok": True, "answer": answer_text, "products": product_payload, "kind": "products", "meta": meta}

    if "не знаю" in lower or "что посовет" in lower or "посоветуй" in lower:
        return {"ok": True, "answer": "Опиши ситуацию своими словами: что хочешь получить, для чего это и сколько примерно готов потратить. Я переведу это в параметры каталога.", "kind": "chat"}
    return {"ok": True, "answer": "Я не нашёл достаточно сильного совпадения. Попробуй описать не название товара, а задачу — например «хочу расслабиться вечером до 5000», «нужен ноут для программирования» или «собери мне недорогой игровой набор».", "kind": "chat"}
