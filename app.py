import json
import os
import secrets
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for, session
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from authlib.integrations.flask_client import OAuth
except ImportError:  # Google OAuth becomes available after `pip install -r requirements.txt`.
    OAuth = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{os.path.join(INSTANCE_DIR, 'spendit.db')}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_BINDS={"real": f"sqlite:///{os.path.join(INSTANCE_DIR, 'spendit_real.db')}"},
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Войдите в аккаунт, чтобы продолжить."


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    quiz_done = db.Column(db.Boolean, default=False)

    interests = db.Column(db.Text, default="")
    budget_min = db.Column(db.Integer, default=0)
    budget_max = db.Column(db.Integer, default=0)
    priority = db.Column(db.String(40), default="balanced")
    shopping_goal = db.Column(db.String(80), default="discover")
    brands = db.Column(db.Text, default="")

    income = db.Column(db.Integer, default=0)
    role = db.Column(db.String(20), default="user")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    purchases = db.relationship("Purchase", backref="user", lazy=True, cascade="all, delete-orphan")
    complaints = db.relationship("Complaint", backref="user", lazy=True, cascade="all, delete-orphan")
    cart_items = db.relationship("CartItem", backref="user", lazy=True, cascade="all, delete-orphan")
    favorites = db.relationship("Favorite", backref="user", lazy=True, cascade="all, delete-orphan")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    original_price = db.Column(db.Integer, nullable=True)
    desc = db.Column(db.Text)
    details = db.Column(db.Text)
    image = db.Column(db.String(500), default="https://via.placeholder.com/600x400?text=SpendIt")
    rating = db.Column(db.Float, default=4.7)
    featured = db.Column(db.Boolean, default=False)
    tags = db.Column(db.Text, default="")


class RealProduct(db.Model):
    __bind_key__ = "real"
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    original_price = db.Column(db.Integer, nullable=True)
    desc = db.Column(db.Text)
    details = db.Column(db.Text)
    image = db.Column(db.String(500), default="https://via.placeholder.com/600x400?text=SpendIt")
    rating = db.Column(db.Float, default=4.7)
    featured = db.Column(db.Boolean, default=False)
    tags = db.Column(db.Text, default="")


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    product_name = db.Column(db.String(200))
    price = db.Column(db.Integer)
    quantity = db.Column(db.Integer, default=1)
    date = db.Column(db.String(50))


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)


class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_favorite_user_product"),)


class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer)
    product_name = db.Column(db.String(200))
    text = db.Column(db.Text)
    date = db.Column(db.String(50))
    reply = db.Column(db.Text, nullable=True)
    replied_by = db.Column(db.String(80), nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.post("/api/assistant")
def assistant():
    from assistant_engine import answer as assistant_answer
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    products = catalog_products()
    result = assistant_answer(message, products, current_user if current_user.is_authenticated else None)
    for item in result.get("products", []):
        item["url"] = url_for("product", product_id=item["id"])
        item["source"] = "Spendit"

    try:
        from marketplace_service import search_marketplaces
        live = search_marketplaces(message)
    except Exception:
        live = {"offers": [], "searched": False, "updated_at": None, "providers": {}}

    external = []
    for offer in live.get("offers", []):
        external.append({
            "name": offer["name"], "price": offer["price"], "url": offer["url"],
            "image": offer.get("image"), "why": "Предложение с маркетплейса",
            "source": "Интернет-магазины и маркетплейсы",
        })
    if external:
        existing = result.get("products", [])
        seen = {str(item.get("name", "")).casefold() for item in existing}
        for item in external:
            if str(item.get("name", "")).casefold() not in seen:
                existing.append(item)
                seen.add(str(item.get("name", "")).casefold())
        result["products"] = existing[:100]
        result["kind"] = "products"
        result["answer"] = "Подходящие предложения из интернет-магазинов и маркетплейсов. Цены и наличие могут измениться — проверьте их на странице продавца."
    result["marketplaces"] = {
        "searched": bool(live.get("searched")),
        "updated_at": live.get("updated_at"),
        "providers": live.get("providers", {}),
        "cached": live.get("cached", False),
        "search_links": live.get("search_links", {}),
        "corrected_query": live.get("corrected_query"),
    }
    return jsonify(result)


def urlquote(value):
    from urllib.parse import quote_plus
    return quote_plus(value)


def current_mode():
    return "fun" if request and request.cookies.get("spendit_mode", "real") == "fun" else "real"

app.jinja_env.globals["current_mode"] = current_mode

def catalog_model():
    return Product if current_mode() == "fun" else RealProduct

def catalog_products():
    return catalog_model().query.all()

def catalog_get(product_id):
    return db.session.get(catalog_model(), product_id)

def catalog_query():
    return catalog_model().query



def add_column_if_missing(table, column, ddl):
    rows = db.session.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    names = {row[1] for row in rows}
    if column not in names:
        db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


with app.app_context():
    db.create_all()
    for column, ddl in {
        "email": "VARCHAR(255)",
        "google_id": "VARCHAR(255)",
        "avatar_url": "VARCHAR(500)",
        "budget_min": "INTEGER DEFAULT 0",
        "budget_max": "INTEGER DEFAULT 0",
        "priority": "VARCHAR(40) DEFAULT 'balanced'",
        "shopping_goal": "VARCHAR(80) DEFAULT 'discover'",
        "brands": "TEXT DEFAULT ''",
        "created_at": "DATETIME",
    }.items():
        add_column_if_missing("user", column, ddl)
    for column, ddl in {
        "rating": "FLOAT DEFAULT 4.7",
        "featured": "BOOLEAN DEFAULT 0",
        "tags": "TEXT DEFAULT ''",
    }.items():
        add_column_if_missing("product", column, ddl)
    add_column_if_missing("purchase", "quantity", "INTEGER DEFAULT 1")
    db.session.commit()

    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            username="admin",
            password=generate_password_hash("admin123"),
            role="admin",
            quiz_done=True,
        )
        db.session.add(admin)
        db.session.commit()

    if Product.query.count() == 0:
        products = [
            Product(name="Беспроводные наушники", category="Электроника", price=3500, original_price=5000, desc="Отличный звук, активное шумоподавление.", details="Глубокий бас, до 30 часов работы, Bluetooth 5.2.", featured=True, tags="музыка,гаджеты,подарок"),
            Product(name="Набор Lego Technic", category="Хобби", price=8900, original_price=10990, desc="Собери суперкар или экскаватор — 1500+ деталей.", details="Детализированная модель с подвижными элементами.", tags="творчество,подарок"),
            Product(name="Умная колонка", category="Электроника", price=7500, desc="Голосовой помощник и центр умного дома.", details="Музыка, таймеры, сценарии умного дома.", featured=True, tags="дом,гаджеты,умный дом"),
            Product(name="Абонемент в спортзал", category="Спорт", price=2500, desc="Месяц тренировок без ограничений.", details="Тренажёры, бассейн, групповые занятия.", tags="спорт,здоровье"),
            Product(name="Кожаный кошелёк", category="Аксессуары", price=4200, original_price=5500, desc="Натуральная кожа и RFID-защита.", details="Отделения для карт и купюр.", tags="стиль,подарок"),
            Product(name="Билет на концерт", category="Развлечения", price=5500, desc="Живой концерт популярной группы.", details="Место в партере.", tags="музыка,досуг"),
            Product(name="Курс по Python", category="Образование", price=19900, original_price=24900, desc="Продвинутое программирование через проекты.", details="60 часов обучения и практика.", featured=True, tags="python,карьера,обучение"),
            Product(name="Электросамокат", category="Транспорт", price=28000, desc="До 25 км/ч и запас хода 30 км.", details="Мотор 350 Вт, дисковые тормоза.", tags="мобильность,спорт"),
            Product(name="Фотосессия в студии", category="Услуги", price=6000, desc="2 часа съёмки + 30 обработанных фото.", details="Профессиональный фотограф.", tags="контент,подарок"),
            Product(name="Робот-пылесос", category="Дом", price=18000, original_price=22000, desc="Сухая и влажная уборка с картой помещения.", details="Лидар-навигация, управление со смартфона.", featured=True, tags="дом,технологии"),
            Product(name="Сертификат в SPA", category="Услуги", price=8000, desc="Массаж, обёртывание и хамам.", details="Подарочный сертификат на 3 часа.", tags="релакс,подарок"),
            Product(name="Настольная игра", category="Развлечения", price=2900, original_price=3500, desc="Стратегия для компании друзей.", details="От 3 игроков.", tags="друзья,досуг"),
            Product(name="Кроссовки брендовые", category="Одежда", price=12000, desc="Комфортная колодка и амортизация.", details="Повседневная модель для города.", tags="спорт,стиль"),
            Product(name="Годовая подписка на книги", category="Образование", price=4500, desc="Электронная библиотека без ограничений.", details="Доступ к большому каталогу книг.", tags="чтение,обучение"),
            Product(name="Скейтборд", category="Спорт", price=5600, original_price=6900, desc="Классический круизер из канадского клёна.", details="Алюминиевая подвеска.", tags="спорт,улица"),
            Product(name="Мощный блендер", category="Дом", price=7000, desc="Смузи, супы-пюре и лёд.", details="1200 Вт, чаша 2 л.", tags="кухня,дом"),
            Product(name="Наручные часы", category="Аксессуары", price=15000, original_price=18900, desc="Японский механизм и водозащита.", details="Сапфировое стекло, кожаный ремешок.", tags="стиль,подарок"),
            Product(name="Прыжок с парашютом", category="Развлечения", price=11000, desc="Тандем-прыжок с инструктором.", details="Высота до 4000 метров.", tags="приключения,эмоции"),
            Product(name="Чайник с терморегуляцией", category="Дом", price=4200, original_price=4900, desc="Поддержание температуры, 1.7 л.", details="Автоотключение и несколько режимов.", tags="кухня,дом"),
            Product(name="Путешествие на выходные", category="Путешествия", price=25000, desc="Тур на выходные с отелем и трансфером.", details="Экскурсии и питание включены.", tags="путешествия,отдых"),
        ]
        db.session.add_all(products)
        db.session.commit()

    if RealProduct.query.count() == 0:
        real_products = [
            RealProduct(id=1001, name="Apple iPhone 15 128 ГБ", category="Смартфоны", price=69990, original_price=74990, desc="Современный смартфон для повседневных задач, фото и видео.", details="6.1-inch OLED, USB-C, 128 ГБ. Реалистичный демонстрационный товар.", featured=True, tags="смартфон,apple,телефон,фото"),
            RealProduct(id=1002, name="Samsung Galaxy S24 128 ГБ", category="Смартфоны", price=64990, desc="Компактный флагман для работы, общения и развлечений.", details="AMOLED-дисплей, 128 ГБ, качественная камера.", featured=True, tags="смартфон,samsung,android,камера"),
            RealProduct(id=1003, name="Sony WH-1000XM5", category="Наушники", price=29990, original_price=34990, desc="Полноразмерные беспроводные наушники с активным шумоподавлением.", details="Bluetooth, ANC, длительная автономная работа.", featured=True, tags="музыка,sony,наушники,anc"),
            RealProduct(id=1004, name="Logitech MX Master 3S", category="Периферия", price=8990, desc="Эргономичная мышь для работы и творчества.", details="Точная настройка, тихие кнопки, многокнопочное управление.", tags="мышь,logitech,работа,офис"),
            RealProduct(id=1005, name="Keychron K8 Pro", category="Периферия", price=12990, desc="Механическая клавиатура для работы и игр.", details="Hot-swap, подсветка, компактный формат.", tags="клавиатура,keychron,игры,работа"),
            RealProduct(id=1006, name="ASUS TUF Gaming A15", category="Ноутбуки", price=99990, desc="Игровой ноутбук для современных игр и тяжёлых задач.", details="15.6-inch дисплей, производительная графика, быстрый SSD.", featured=True, tags="ноутбук,gaming,asus,игры"),
            RealProduct(id=1007, name="MacBook Air 13", category="Ноутбуки", price=119990, desc="Тихий и лёгкий ноутбук для работы, учёбы и творчества.", details="13-inch дисплей, энергоэффективная платформа, большой ресурс батареи.", tags="ноутбук,apple,работа,учёба"),
            RealProduct(id=1008, name="Samsung 990 PRO 2 ТБ", category="Комплектующие", price=21990, desc="Быстрый NVMe SSD для компьютера и игровых сборок.", details="PCIe 4.0, 2 ТБ, высокий уровень производительности.", tags="ssd,samsung,пк,игры"),
            RealProduct(id=1009, name="Kingston Fury Beast 32 ГБ", category="Комплектующие", price=9990, desc="Комплект оперативной памяти для современной системы.", details="32 ГБ комплектом, подходит для производительных ПК.", tags="ram,память,пк"),
            RealProduct(id=1010, name="AMD Ryzen 7 7800X3D", category="Комплектующие", price=46990, desc="Процессор высокого класса для игровой системы.", details="8 ядер, 16 потоков, ориентирован на игровые сборки.", featured=True, tags="amd,cpu,процессор,gaming"),
            RealProduct(id=1011, name="NVIDIA GeForce RTX 4070 SUPER", category="Комплектующие", price=74990, desc="Мощная видеокарта для игр и графических задач.", details="Производительный GPU для современных игровых сборок.", tags="nvidia,gpu,видеокарта,gaming"),
            RealProduct(id=1012, name="PlayStation 5 Slim", category="Игровые консоли", price=54990, desc="Домашняя консоль для современных игр.", details="Версия Slim, быстрый SSD и библиотека игр.", tags="sony,ps5,игры,консоль"),
            RealProduct(id=1013, name="Xbox Series X", category="Игровые консоли", price=59990, desc="Производительная консоль для игр в высоком качестве.", details="4K-гейминг, быстрый SSD, Game Pass.", tags="xbox,microsoft,игры,консоль"),
            RealProduct(id=1014, name="Apple Watch Series 9", category="Смарт-часы", price=37990, desc="Умные часы для уведомлений, спорта и повседневного использования.", details="Яркий дисплей, мониторинг активности, интеграция со смартфоном.", tags="apple,часы,спорт,гаджеты"),
            RealProduct(id=1015, name="Kindle Paperwhite", category="Гаджеты", price=15990, desc="Электронная книга с экраном, комфортным для долгого чтения.", details="Подсветка, компактный корпус, длительная автономность.", tags="книги,чтение,kindle"),
            RealProduct(id=1016, name="Dyson V8", category="Для дома", price=29990, desc="Беспроводной пылесос для быстрой уборки дома.", details="Компактный формат, несколько насадок, автономная работа.", tags="дом,dyson,уборка"),
            RealProduct(id=1017, name="Philips Airfryer XL", category="Для дома", price=16990, desc="Аэрогриль для быстрых домашних блюд с минимальным количеством масла.", details="Большая корзина и автоматические программы.", tags="кухня,дом,еда"),
            RealProduct(id=1018, name="JBL Charge 5", category="Аудио", price=12990, original_price=14990, desc="Портативная Bluetooth-колонка для дома и поездок.", details="Защита от воды, насыщенный звук, аккумулятор.", tags="jbl,музыка,колонка,путешествия"),
            RealProduct(id=1019, name="GoPro HERO12 Black", category="Камеры", price=44990, desc="Экшн-камера для поездок, спорта и динамичной съёмки.", details="Компактный корпус, стабилизация и качественная видеозапись.", tags="камера,gopro,спорт,видео"),
            RealProduct(id=1020, name="IKEA MARKUS", category="Мебель", price=19990, desc="Эргономичное рабочее кресло для домашнего офиса.", details="Высокая спинка, регулируемые элементы, нейтральный дизайн.", tags="кресло,офис,работа,дом"),
        ]
        db.session.add_all(real_products)
        db.session.commit()



oauth = OAuth(app) if OAuth else None
if oauth and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def google_enabled():
    return bool(oauth and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def safe_next_url(value):
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.netloc:
        return None
    return value if value.startswith("/") else None


@app.route("/auth/google")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if not google_enabled():
        flash("Google-вход пока не настроен. Добавьте GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET.")
        return redirect(url_for("login"))
    next_url = safe_next_url(request.args.get("next"))
    if next_url:
        session["spendit_google_next"] = next_url
    redirect_uri = url_for("google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def google_callback():
    if not google_enabled():
        flash("Google-вход не настроен.")
        return redirect(url_for("login"))
    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.google.userinfo()
    except Exception:
        flash("Не удалось завершить вход через Google. Попробуйте ещё раз.")
        return redirect(url_for("login"))

    google_id = str(userinfo.get("sub"))
    email = (userinfo.get("email") or "").strip().lower() or None
    name = (userinfo.get("name") or "user").strip()[:70]
    avatar = userinfo.get("picture")

    user = User.query.filter_by(google_id=google_id).first()
    if not user and email:
        user = User.query.filter_by(email=email).first()

    if not user:
        base_username = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_") or "user"
        username = base_username
        suffix = 1
        while User.query.filter_by(username=username).first():
            suffix += 1
            username = f"{base_username}_{suffix}"
        user = User(
            username=username,
            password=generate_password_hash(secrets.token_urlsafe(32)),
            email=email,
            google_id=google_id,
            avatar_url=avatar,
            quiz_done=False,
        )
        db.session.add(user)
    else:
        user.google_id = google_id
        user.email = email or user.email
        user.avatar_url = avatar or user.avatar_url
    db.session.commit()
    login_user(user)
    next_url = session.pop("spendit_google_next", None)
    if not user.quiz_done and user.role not in ("admin", "subadmin"):
        return redirect(url_for("quiz"))
    return redirect(safe_next_url(next_url) or url_for("index"))


@app.context_processor
def inject_globals():
    model = catalog_model()
    categories = sorted({p.category for p in model.query.all()})
    cart_count = 0
    favorite_ids = set()
    notifications = 0
    if current_user.is_authenticated:
        catalog_ids = {p.id for p in model.query.with_entities(model.id).all()}
        cart_count = sum(item.quantity for item in CartItem.query.filter_by(user_id=current_user.id).all() if item.product_id in catalog_ids)
        favorite_ids = {item.product_id for item in Favorite.query.filter_by(user_id=current_user.id).all() if item.product_id in catalog_ids}
        notifications = Complaint.query.filter_by(user_id=current_user.id).filter(Complaint.reply.isnot(None)).count()
    return {
        "categories": categories,
        "cart_count": cart_count,
        "favorite_ids": favorite_ids,
        "notifications": notifications,
        "google_enabled": google_enabled(),
        "current_mode_name": current_mode(),
    }


def json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return [item.strip() for item in str(value).split(",") if item.strip()]


app.jinja_env.globals["json_list"] = json_list

def preference_score(product, user):
    interests = set(json_list(user.interests))
    brands = set(x.strip().lower() for x in (user.brands or "").split(",") if x.strip())
    product_tags = set(x.strip().lower() for x in (product.tags or "").split(",") if x.strip())
    score = 0.0
    if product.category in interests:
        score += 45
    if product.category.lower() in brands or product.name.lower() in brands:
        score += 10
    if product_tags & brands:
        score += 8

    min_b, max_b = user.budget_min or 0, user.budget_max or 0
    if max_b > 0:
        center = (min_b + max_b) / 2 if min_b else max_b * 0.65
        distance = abs(product.price - center)
        score += max(0, 35 - (distance / max(center, 1)) * 35)
        if product.price <= max_b:
            score += 12
        else:
            score -= min(25, (product.price - max_b) / max(max_b, 1) * 25)

    priority_bonus = {
        "price": max(0, 22 - product.price / 3000),
        "performance": 8 if product.featured else 3,
        "quality": product.rating * 4,
        "balanced": 10,
    }.get(user.priority or "balanced", 10)
    score += priority_bonus
    score += product.rating * 2
    return score


def personalized_products(products, user):
    return sorted(products, key=lambda p: preference_score(p, user), reverse=True)


def ensure_onboarded(user):
    if user.role in ("admin", "subadmin"):
        return True
    if not user.quiz_done:
        return False
    return True


@app.before_request
def gate_new_users():
    endpoint = request.endpoint or ""
    public = {"login", "register", "logout", "google_login", "google_callback", "static"}
    if current_user.is_authenticated and not ensure_onboarded(current_user) and endpoint not in public and endpoint != "quiz":
        return redirect(url_for("quiz"))
    return None


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        if len(username) < 3:
            flash("Логин должен содержать минимум 3 символа.")
        elif len(password) < 6:
            flash("Пароль должен содержать минимум 6 символов.")
        elif password != confirm:
            flash("Пароли не совпадают.")
        elif User.query.filter_by(username=username).first():
            flash("Пользователь с таким логином уже существует.")
        elif email and User.query.filter_by(email=email).first():
            flash("Этот email уже используется.")
        else:
            user = User(
                username=username,
                password=generate_password_hash(password),
                email=email,
                quiz_done=False,
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("quiz"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(username=username).first()
        if not user:
            flash("Пользователь не найден.")
        elif not user.password or not check_password_hash(user.password, password):
            flash("Неверный логин или пароль.")
        else:
            login_user(user)
            return redirect(safe_next_url(request.args.get("next")) or url_for("index"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.")
    return redirect(url_for("login"))


@app.route("/quiz", methods=["GET", "POST"])
@login_required
def quiz():
    if current_user.quiz_done and request.method == "GET":
        return redirect(url_for("index"))
    cats = sorted({p.category for p in catalog_products()})
    if request.method == "POST":
        interests = [x.strip() for x in request.form.getlist("interests") if x.strip()]
        budget_max = request.form.get("budget_max", type=int) or 0
        budget_min = request.form.get("budget_min", type=int) or 0
        priority = request.form.get("priority", "balanced")
        shopping_goal = request.form.get("shopping_goal", "discover")
        brands = request.form.get("brands", "").strip()
        if not interests:
            flash("Выберите хотя бы одну категорию, чтобы рекомендации смогли стартовать.")
            return render_template("quiz.html", categories=cats, form=request.form)
        if budget_max <= 0:
            flash("Укажите верхнюю границу бюджета.")
            return render_template("quiz.html", categories=cats, form=request.form)
        if budget_min < 0 or budget_min > budget_max:
            flash("Минимальный бюджет не может быть больше максимального.")
            return render_template("quiz.html", categories=cats, form=request.form)

        current_user.interests = json.dumps(interests, ensure_ascii=False)
        current_user.budget_min = budget_min
        current_user.budget_max = budget_max
        current_user.priority = priority
        current_user.shopping_goal = shopping_goal
        current_user.brands = brands
        current_user.quiz_done = True
        db.session.commit()
        flash("Профиль рекомендаций готов — лента теперь подстроена под вас.")
        return redirect(url_for("index"))

    return render_template("quiz.html", categories=cats, form=request.form)


@app.route("/preferences", methods=["GET", "POST"])
@login_required
def preferences():
    if request.method == "POST":
        interests = [x.strip() for x in request.form.getlist("interests") if x.strip()]
        current_user.interests = json.dumps(interests, ensure_ascii=False)
        current_user.budget_min = request.form.get("budget_min", type=int) or 0
        current_user.budget_max = request.form.get("budget_max", type=int) or 0
        current_user.priority = request.form.get("priority", "balanced")
        current_user.shopping_goal = request.form.get("shopping_goal", "discover")
        current_user.brands = request.form.get("brands", "").strip()
        current_user.quiz_done = True
        db.session.commit()
        flash("Предпочтения обновлены.")
        return redirect(url_for("profile"))
    return render_template("preferences.html")


@app.post("/mode/<mode>")
def set_mode(mode):
    if mode not in ("real", "fun"):
        abort(404)
    response = redirect(request.referrer or url_for("index"))
    response.set_cookie("spendit_mode", mode, max_age=60*60*24*365, samesite="Lax")
    flash("Включён обычный каталог." if mode == "real" else "Включён режим шутки. Тут товары могут быть… подозрительными 😄")
    return response


@app.route("/")
def index():
    query = request.args.get("q", "").strip()
    budget = request.args.get("budget", type=int) or 0
    selected_categories = request.args.getlist("category")
    sort_option = request.args.get("sort", "recommended")
    products = catalog_products()
    if query:
        needle = query.lower()
        products = [p for p in products if needle in p.name.lower() or needle in (p.desc or "").lower() or needle in (p.tags or "").lower()]
    if budget:
        products = [p for p in products if p.price <= budget]
    if selected_categories:
        products = [p for p in products if p.category in selected_categories]

    personalized = current_user.is_authenticated and sort_option == "recommended"
    if personalized:
        products = personalized_products(products, current_user)
    elif sort_option == "price_asc":
        products.sort(key=lambda p: p.price)
    elif sort_option == "price_desc":
        products.sort(key=lambda p: p.price, reverse=True)
    elif sort_option == "rating":
        products.sort(key=lambda p: p.rating, reverse=True)
    elif sort_option == "new":
        products.sort(key=lambda p: p.id, reverse=True)
    else:
        products.sort(key=lambda p: p.name.lower())

    featured_products = catalog_query().filter_by(featured=True).all()
    featured = sorted(featured_products, key=lambda p: (p.rating, p.id), reverse=True)[:4]
    return render_template(
        "index.html",
        offers=products,
        featured=featured,
        query=query,
        budget=budget or "",
        selected_categories=selected_categories,
        sort_option=sort_option,
        personalized=personalized,
    )


@app.route("/product/<int:product_id>")
def product(product_id):
    offer = catalog_get(product_id)
    if not offer:
        flash("Товар не найден.")
        return redirect(url_for("index"))
    related_pool = [p for p in catalog_query().filter(catalog_model().id != offer.id, catalog_model().category == offer.category).all()]
    related = (personalized_products(related_pool, current_user) if current_user.is_authenticated else sorted(related_pool, key=lambda p: (p.rating, p.id), reverse=True))[:4]
    return render_template("product.html", offer=offer, related=related)


@app.post("/cart/add/<int:product_id>")
def cart_add(product_id):
    if not current_user.is_authenticated:
        return jsonify(success=False, requires_auth=True, login_url=url_for("login", next=request.referrer or url_for("index")), message="Войдите, чтобы добавить товар в корзину."), 401
    product = catalog_get(product_id)
    if not product:
        return jsonify(success=False, message="Товар не найден"), 404
    item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    quantity = max(1, request.form.get("quantity", type=int) or 1)
    if item:
        item.quantity += quantity
    else:
        db.session.add(CartItem(user_id=current_user.id, product_id=product_id, quantity=quantity))
    db.session.commit()
    count = sum(i.quantity for i in CartItem.query.filter_by(user_id=current_user.id).all())
    return jsonify(success=True, message=f"{product.name} добавлен в корзину", cart_count=count)


@app.post("/cart/update/<int:item_id>")
@login_required
def cart_update(item_id):
    item = CartItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    quantity = request.form.get("quantity", type=int) or 1
    if quantity <= 0:
        db.session.delete(item)
    else:
        item.quantity = min(quantity, 99)
    db.session.commit()
    return redirect(url_for("cart"))


@app.post("/cart/remove/<int:item_id>")
@login_required
def cart_remove(item_id):
    item = CartItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("cart"))


@app.post("/cart/clear")
@login_required
def cart_clear():
    CartItem.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash("Корзина очищена.")
    return redirect(url_for("cart"))


@app.route("/cart")
@login_required
def cart():
    items = CartItem.query.filter_by(user_id=current_user.id).order_by(CartItem.id.desc()).all()
    rows = []
    total = 0
    for item in items:
        product = catalog_get(item.product_id)
        if not product:
            continue
        line_total = product.price * item.quantity
        total += line_total
        rows.append({"item": item, "product": product, "line_total": line_total})
    return render_template("cart.html", rows=rows, total=total)


@app.post("/favorite/toggle/<int:product_id>")
def favorite_toggle(product_id):
    if not current_user.is_authenticated:
        return jsonify(success=False, requires_auth=True, login_url=url_for("login", next=request.referrer or url_for("index")), message="Войдите, чтобы сохранять избранное."), 401
    product = catalog_get(product_id)
    if not product:
        return jsonify(success=False, message="Товар не найден"), 404
    item = Favorite.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if item:
        db.session.delete(item)
        state = False
    else:
        db.session.add(Favorite(user_id=current_user.id, product_id=product_id))
        state = True
    db.session.commit()
    return jsonify(success=True, favorite=state)


@app.route("/favorites")
@login_required
def favorites():
    ids = [f.product_id for f in Favorite.query.filter_by(user_id=current_user.id).all()]
    products = catalog_query().filter(catalog_model().id.in_(ids)).all() if ids else []
    return render_template("favorites.html", offers=products)


@app.post("/checkout")
@login_required
def checkout():
    items = CartItem.query.filter_by(user_id=current_user.id).all()
    if not items:
        flash("Корзина пока пустая.")
        return redirect(url_for("cart"))
    created = 0
    for item in items:
        product = catalog_get(item.product_id)
        if not product:
            continue
        db.session.add(Purchase(
            user_id=current_user.id,
            product_id=product.id,
            product_name=product.name,
            price=product.price,
            quantity=item.quantity,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        created += 1
    CartItem.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash(f"Заказ собран. Позиций: {created}. Оплата не подключена — это демонстрационный заказ.")
    return redirect(url_for("my_purchases"))


@app.route("/profile")
@login_required
def profile():
    order_count = Purchase.query.filter_by(user_id=current_user.id).count()
    favorite_count = Favorite.query.filter_by(user_id=current_user.id).count()
    return render_template("profile.html", order_count=order_count, favorite_count=favorite_count)


@app.route("/product/<int:product_id>/complain", methods=["POST"])
@login_required
def complain(product_id):
    product = catalog_get(product_id)
    if not product:
        flash("Товар не найден.")
        return redirect(url_for("index"))
    text_value = request.form.get("complaint_text", "").strip()
    if not text_value:
        flash("Введите текст обращения.")
        return redirect(url_for("product", product_id=product_id))
    db.session.add(Complaint(
        user_id=current_user.id,
        product_id=product_id,
        product_name=product.name,
        text=text_value,
        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    db.session.commit()
    flash("Обращение отправлено в поддержку.")
    return redirect(url_for("product", product_id=product_id))


@app.route("/my/purchases")
@login_required
def my_purchases():
    purchases = Purchase.query.filter_by(user_id=current_user.id).order_by(Purchase.id.desc()).all()
    return render_template("my_purchases.html", purchases=purchases)


@app.route("/my/complaints")
@login_required
def my_complaints():
    complaints = Complaint.query.filter_by(user_id=current_user.id).order_by(Complaint.id.desc()).all()
    return render_template("my_complaints.html", complaints=complaints)


def admin_required(level="staff"):
    if not current_user.is_authenticated or current_user.role not in ("admin", "subadmin"):
        flash("Доступ запрещён.")
        return redirect(url_for("index"))
    if level == "admin" and current_user.role != "admin":
        flash("Недостаточно прав.")
        return redirect(url_for("admin_panel"))
    return None


@app.route("/admin")
@login_required
def admin_panel():
    denied = admin_required()
    if denied:
        return denied
    stats = {
        "users": User.query.count(),
        "products": catalog_query().count(),
        "orders": Purchase.query.count(),
        "complaints": Complaint.query.count(),
        "cart_items": CartItem.query.count(),
    }
    return render_template("admin/index.html", stats=stats)


@app.route("/admin/users")
@login_required
def admin_users():
    denied = admin_required("admin")
    if denied:
        return denied
    return render_template("admin/users.html", users=User.query.order_by(User.id.desc()).all())


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_user(user_id):
    denied = admin_required("admin")
    if denied:
        return denied
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "user")
        duplicate = User.query.filter(User.username == username, User.id != user.id).first()
        if duplicate:
            flash("Такой логин уже занят.")
        else:
            user.username = username or user.username
            user.role = role if role in ("user", "subadmin", "admin") else "user"
            db.session.commit()
            flash("Профиль пользователя обновлён.")
            return redirect(url_for("admin_users"))
    return render_template("admin/edit_user.html", user=user)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    denied = admin_required("admin")
    if denied:
        return denied
    if user_id == current_user.id:
        flash("Нельзя удалить самого себя.")
        return redirect(url_for("admin_users"))
    user = db.session.get(User, user_id)
    if user:
        username = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f"Пользователь {username} удалён.")
    return redirect(url_for("admin_users"))


@app.route("/admin/products")
@login_required
def admin_products():
    denied = admin_required()
    if denied:
        return denied
    return render_template("admin/products.html", products=catalog_query().order_by(catalog_model().id.desc()).all())


@app.route("/admin/products/add", methods=["GET", "POST"])
@login_required
def admin_add_product():
    denied = admin_required()
    if denied:
        return denied
    if request.method == "POST":
        product = catalog_model()(
            name=request.form.get("name", "").strip(),
            category=request.form.get("category", "Другое").strip(),
            price=request.form.get("price", type=int) or 0,
            original_price=request.form.get("original_price", type=int),
            desc=request.form.get("desc", "").strip(),
            details=request.form.get("details", "").strip(),
            image=request.form.get("image", "").strip() or "https://via.placeholder.com/600x400?text=SpendIt",
            tags=request.form.get("tags", "").strip(),
            rating=request.form.get("rating", type=float) or 4.7,
            featured=bool(request.form.get("featured")),
        )
        db.session.add(product)
        db.session.commit()
        flash("Товар добавлен в каталог.")
        return redirect(url_for("admin_products"))
    return render_template("admin/add_product.html", categories=sorted({p.category for p in catalog_products()}))


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_product(product_id):
    denied = admin_required()
    if denied:
        return denied
    product = catalog_get(product_id)
    if not product:
        abort(404)
    if request.method == "POST":
        product.name = request.form.get("name", product.name).strip()
        product.category = request.form.get("category", product.category).strip()
        product.price = request.form.get("price", type=int) or product.price
        product.original_price = request.form.get("original_price", type=int)
        product.desc = request.form.get("desc", "").strip()
        product.details = request.form.get("details", "").strip()
        product.image = request.form.get("image", "").strip() or product.image
        product.tags = request.form.get("tags", "").strip()
        product.rating = request.form.get("rating", type=float) or product.rating
        product.featured = bool(request.form.get("featured"))
        db.session.commit()
        flash("Товар обновлён.")
        return redirect(url_for("admin_products"))
    return render_template("admin/edit_product.html", product=product, categories=sorted({p.category for p in catalog_products()}))


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@login_required
def admin_delete_product(product_id):
    denied = admin_required()
    if denied:
        return denied
    product = catalog_get(product_id)
    if product:
        name = product.name
        db.session.delete(product)
        db.session.commit()
        flash(f"Товар «{name}» удалён.")
    return redirect(url_for("admin_products"))


@app.route("/admin/complaints")
@login_required
def admin_complaints():
    denied = admin_required()
    if denied:
        return denied
    complaints = Complaint.query.order_by(Complaint.id.desc()).all()
    return render_template("admin/complaints.html", complaints=complaints)


@app.post("/admin/complaints/<int:complaint_id>/reply")
@login_required
def admin_reply_complaint(complaint_id):
    denied = admin_required()
    if denied:
        return denied
    complaint = db.session.get(Complaint, complaint_id)
    if not complaint:
        abort(404)
    reply_text = request.form.get("reply", "").strip()
    if not reply_text:
        flash("Введите ответ.")
    else:
        complaint.reply = reply_text
        complaint.replied_by = current_user.username
        db.session.commit()
        flash("Ответ отправлен пользователю.")
    return redirect(url_for("admin_complaints"))


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_error):
    db.session.rollback()
    return render_template("500.html"), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "1") == "1")
