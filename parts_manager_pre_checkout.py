from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from xml.sax.saxutils import escape as xml_escape
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime, timedelta
import json, os, uuid, html, ssl, threading, logging, re, socket, webbrowser, time, sys, smtplib, sqlite3, urllib.parse

VERSION = "11.0.2"

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Parts Manager"
# Пользовательские данные не должны лежать рядом с EXE: папка установки может быть только для чтения.
_DEFAULT_DATA_ROOT = os.environ.get("PARTS_MANAGER_DATA_DIR")
if not _DEFAULT_DATA_ROOT:
    if os.name == "nt":
        _local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        _DEFAULT_DATA_ROOT = os.path.join(_local, "Parts Manager", "data")
    else:
        _DEFAULT_DATA_ROOT = os.path.join(os.path.expanduser("~/.parts_manager"), "data")
DATA_DIR = _DEFAULT_DATA_ROOT
os.makedirs(DATA_DIR, exist_ok=True)

BUILTIN = {
    "vin_url": "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues",
    "ai_url": "https://openrouter.ai/api/v1/chat/completions",
    "ai_key": os.environ.get("OPENROUTER_API_KEY", ""),
    "ai_model": "deepseek/deepseek-chat-v3.1:free",
    "rossko_search": "https://api.rossko.ru/service/v2.1/GetSearch",
    "rossko_checkout": "https://api.rossko.ru/service/v2.1/GetCheckoutDetails",
    "zappro_base": "https://portal.zap-pro.ru",
    "zappro_get_price": "https://portal.zap-pro.ru/api/getPrice",
    "trinity_base": "https://trinity-parts.ru",
    "berg_base": "https://api.berg.ru",
    "berg_version": "v1.0",
    "berg_key_default": os.environ.get("BERG_API_KEY", ""),
    "avd_url": "https://ws1.avdmotors.ru/AvdUserService.svc/secure",
    "avd_ns": "http://tempuri.org/",
}

MAX_RESULTS = 200
COLLAPSED_ROWS = 3

API_TYPES = {
    "berg": {
        "name": "REST/JSON",
        "hint": "1 поле — API-ключ",
        "fields": [
            ("key", "API-ключ", "Ключ из личного кабинета поставщика", "password", True),
        ],
        "test_article": "GDB1044",
    },
    "zappro": {
        "name": "REST/JSON (login/password)",
        "hint": "2 поля — логин и пароль",
        "fields": [
            ("login", "Логин", "Логин от личного кабинета", "text", True),
            ("password", "Пароль", "Пароль", "password", True),
        ],
        "test_article": "LC-1030",
    },
    "rossko": {
        "name": "SOAP (KEY1/KEY2)",
        "hint": "4 поля — два ключа и параметры доставки",
        "fields": [
            ("key1", "KEY1", "Первый ключ", "password", True),
            ("key2", "KEY2", "Второй ключ", "password", True),
            ("delivery_id", "delivery_id", "Способ доставки (получите кнопкой «Проверить доставку»)", "text", False),
            ("address_id", "address_id", "Адрес доставки (получите кнопкой «Проверить доставку»)", "text", False),
        ],
        "test_article": "lc-1400",
    },
    "avd": {
        "name": "SOAP WCF (login/password)",
        "hint": "3 поля — адрес сервиса, логин, пароль",
        "fields": [
            ("endpoint", "Адрес WEB-сервиса", "https://ws1.avdmotors.ru/AvdUserService.svc/secure", "text", True),
            ("login", "Логин", "Логин от личного кабинета AVD", "text", True),
            ("password", "Пароль", "Пароль от личного кабинета AVD", "password", True),
        ],
        "default_endpoint": "https://ws1.avdmotors.ru/AvdUserService.svc/secure",
        "test_article": "LC-1030",
    },
    "custom": {
        "name": "Свой URL (REST/JSON)",
        "hint": "4 поля — URL, ключ, тип авторизации, имя параметра",
        "fields": [
            ("url", "URL запроса", "Например: https://api.example.ru/search?q={article}", "text", True),
            ("api_key", "API-ключ", "Если нужен", "password", False),
            ("auth_mode", "Куда ключ", "select:url|header", "select", False),
            ("url_article_param", "Имя параметра артикула", "Обычно: article, number, q", "text", False),
        ],
        "test_article": "LC-1030",
    },
}

DBS = {k: os.path.join(DATA_DIR, v) for k, v in {"suppliers": "suppliers.json", "vehicles": "vehicles.json",
       "settings": "settings.json", "cart": "cart.json",
       "cart_backup": "cart_backup.json"}.items()}
DEFAULTS = {
    "suppliers": [],
    "vehicles": [],
    "settings": {"company_name": "", "city": "", "active_vehicle_id": "",
                 "last_smart_request": "", "last_smart_max_price": "",
                 "last_smart_max_days": "", "last_smart_article": "",
                 "smtp_host": "", "smtp_port": 587, "smtp_security": "starttls",
                 "smtp_user": "", "smtp_password": "", "smtp_from_email": "", "smtp_from_name": "Parts Manager"},
    "cart": [],
    "cart_backup": [],
}

_lock = threading.Lock()

def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    # Мягкая миграция старых JSON из корня приложения в data/.
    for k, p in DBS.items():
        legacy = os.path.join(HERE, os.path.basename(p))
        if not os.path.exists(p) and legacy != p and os.path.exists(legacy):
            try:
                os.replace(legacy, p)
            except OSError:
                pass
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS[k], f, ensure_ascii=False, indent=2)

def read(k):
    with _lock:
        try:
            with open(DBS[k], "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = DEFAULTS.get(k, [])
    if k == "settings" and isinstance(data, dict):
        m = dict(DEFAULTS["settings"])
        m.update({kk: vv for kk, vv in data.items() if vv is not None})
        return m
    return data

def write(k, v):
    with _lock:
        tmp = DBS[k] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DBS[k])


def smtp_security_mode(value):
    value = str(value or "starttls").lower().strip()
    return value if value in ("starttls", "ssl", "none") else "starttls"

def send_supplier_order(sup, items, settings):
    """Отправляет заказ одному поставщику через SMTP."""
    host = str(settings.get("smtp_host") or "").strip()
    user = str(settings.get("smtp_user") or "").strip()
    password = str(settings.get("smtp_password") or "")
    from_name = str(settings.get("smtp_from_name") or APP_NAME).strip() or APP_NAME
    from_email = str(settings.get("smtp_from_email") or user).strip()
    recipient = str(sup.get("order_email") or "").strip()
    if not recipient:
        raise ValueError("У поставщика не указан e-mail для заказов")
    if not host:
        raise ValueError("Не настроен SMTP-сервер в разделе «Настройки»")
    if not from_email:
        raise ValueError("Не указан e-mail отправителя в разделе «Настройки»")
    try:
        port = int(settings.get("smtp_port") or 587)
    except (TypeError, ValueError):
        port = 587
    security = smtp_security_mode(settings.get("smtp_security"))

    order_no = datetime.now().strftime("PM-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
    company = str(settings.get("company_name") or "").strip()
    city = str(settings.get("city") or "").strip()
    total = 0.0
    rows = []
    text_rows = []
    for item in items:
        qty = max(1, int(item.get("qty", 1) or 1))
        price = parse_price(item.get("price"))
        line_total = price * qty if price is not None else None
        if line_total is not None:
            total += line_total
        article = str(item.get("article") or "—")
        brand = str(item.get("brand") or "—")
        name = str(item.get("name") or "—")
        price_txt = f"{fmt_price(price)} ₽" if price is not None else "уточнить"
        line_txt = f"{fmt_price(line_total)} ₽" if line_total is not None else "уточнить"
        rows.append(f"<tr><td>{html.escape(article)}</td><td>{html.escape(brand)}</td><td>{html.escape(name)}</td><td>{qty}</td><td>{html.escape(price_txt)}</td><td>{html.escape(line_txt)}</td></tr>")
        text_rows.append(f"{article} | {brand} | {name} | {qty} шт. | {price_txt} | {line_txt}")
    total_txt = f"{fmt_price(total)} ₽"
    subject = f"Заказ {order_no} — {sup.get('name') or 'Поставщик'}"
    greeting = "Добрый день!"
    signature = company or from_name
    extra = f"<p><b>Компания:</b> {html.escape(company)}</p>" if company else ""
    if city:
        extra += f"<p><b>Город / регион:</b> {html.escape(city)}</p>"
    html_body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#172033">
<h2>Заказ {html.escape(order_no)}</h2><p>{greeting}</p>{extra}
<p>Просим подтвердить наличие и стоимость следующих позиций:</p>
<table cellpadding="7" cellspacing="0" border="1" style="border-collapse:collapse"><tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr>{''.join(rows)}</table>
<p><b>Итого по текущим ценам: {html.escape(total_txt)}</b></p>
<p>Пожалуйста, подтвердите наличие, актуальную цену и срок поставки.</p>
<p>Спасибо!<br>{html.escape(signature)}</p></body></html>"""
    text_body = (f"Заказ {order_no}\n\n{greeting}\n\n" +
                 (f"Компания: {company}\n" if company else "") +
                 (f"Город / регион: {city}\n" if city else "") +
                 "Просим подтвердить наличие и стоимость:\n\n" +
                 "Артикул | Бренд | Деталь | Кол-во | Цена | Сумма\n" +
                 "\n".join(text_rows) +
                 f"\n\nИтого по текущим ценам: {total_txt}\n" +
                 "Пожалуйста, подтвердите наличие, актуальную цену и срок поставки.\n\n" +
                 f"Спасибо!\n{signature}\n")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if user:
                server.login(user, password)
            server.send_message(msg)
    return order_no

def backup_cart():
    """Безопасно сохраняет резервную копию корзины; отсутствие backup не ломает приложение."""
    try:
        write("cart_backup", read("cart"))
    except Exception:
        logging.exception("cart backup failed")

def esc(v):
    return html.escape(str(v if v is not None else ""))

def parse_price(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(" ", "").replace("\u00a0", "").replace("₽", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None

def fmt_price(v):
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")

def normalize_article(s):
    return re.sub(r"[^a-zA-Z0-9]", "", str(s or "")).lower()

def normalize_brand(s):
    if not s:
        return ""
    t = str(s).strip().lower()
    replace_map = {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
        "в": "b", "к": "k", "м": "m", "н": "h", "т": "t",
    }
    out = [replace_map.get(ch, ch) for ch in t]
    return re.sub(r"[^a-z0-9]", "", "".join(out))

def delivery_days(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("сегодня", "today", "0"):
        return 0
    if s in ("завтра", "tomorrow", "1"):
        return 1
    if s in ("послезавтра", "2"):
        return 2
    m = re.match(r"^\s*(\d+)\s*[-–—]\s*(\d+)\s*", s)
    if m:
        return max(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^\s*(\d+)\s*", s)
    if m:
        return int(m.group(1))
    return None

def human_delivery(delivery_value, delivery_start_iso=""):
    days = delivery_days(delivery_value)
    if days is not None:
        if days == 0:
            return "сегодня"
        if days == 1:
            return "завтра"
        if days == 2:
            return "послезавтра"
        return f"{days} дн."
    if delivery_start_iso:
        try:
            dt = datetime.fromisoformat(delivery_start_iso[:19])
            return dt.strftime("%d.%m")
        except Exception:
            pass
    raw = str(delivery_value or "").strip()
    return raw if raw else "—"

# ---------- VIN ----------
def decode_vin(vin):
    url = f"{BUILTIN['vin_url']}/{quote(vin)}?format=json"
    try:
        req = Request(url, headers={"User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Сервис VIN недоступен: {e}")
    r = (data.get("Results") or [{}])[0]
    def g(k):
        v = r.get(k, "")
        return v.strip() if isinstance(v, str) else v
    result = {
        "vin": g("VIN") or vin,
        "make": g("Make"), "model": g("Model"), "year": g("ModelYear"),
        "body_class": g("BodyClass"), "engine_cylinders": g("EngineCylinders"),
        "engine_hp": g("EngineHP"), "displacement_l": g("DisplacementL"),
        "fuel_type": g("FuelTypePrimary"), "transmission": g("TransmissionStyle"),
        "drive_type": g("DriveType"), "plant_country": g("PlantCountry"),
        "manufacturer": g("Manufacturer"),
    }
    err_code = g("ErrorCode") or ""
    err_text = g("ErrorText") or ""
    soft = ("Check Digit", "has errors", "Unable to provide", "Invalid Characters", "not calculate")
    is_soft = any(m in err_text for m in soft)
    has_data = bool(result["make"] or result["model"] or result["year"])
    if err_code and err_code != "0":
        if has_data and is_soft:
            return result, "VIN распознан частично. Проверьте данные вручную."
        elif has_data:
            return result, f"Предупреждение: {err_text}"
        else:
            raise ValueError(f"VIN не распознан: {err_text or 'нет данных'}")
    if not has_data:
        raise ValueError("VIN не распознан — сервис не вернул данных.")
    return result, ""

# ---------- AI ----------
SMART_PROMPT = """Ты — ассистент автомагазина. Разбери запрос пользователя на запчасть.
Верни ТОЛЬКО JSON без пояснений:
{"article":"артикул или null","brand":"бренд или null","name":"название по-русски",
"keywords":["ключевые","слова"],"max_price":число или null,"max_days":число или null,"quantity":число}
Если данных нет — null. Не выдумывай артикулы."""

def build_smart_prompt(vehicle=None):
    prompt = SMART_PROMPT
    if vehicle:
        lines = []
        if vehicle.get("make"): lines.append(f"- Марка: {vehicle['make']}")
        if vehicle.get("model"): lines.append(f"- Модель: {vehicle['model']}")
        if vehicle.get("year"): lines.append(f"- Год: {vehicle['year']}")
        if vehicle.get("engine"): lines.append(f"- Двигатель: {vehicle['engine']}")
        if vehicle.get("body_class"): lines.append(f"- Кузов: {vehicle['body_class']}")
        if vehicle.get("drive_type"): lines.append(f"- Привод: {vehicle['drive_type']}")
        if vehicle.get("fuel_type"): lines.append(f"- Топливо: {vehicle['fuel_type']}")
        if vehicle.get("vin"): lines.append(f"- VIN: {vehicle['vin']}")
        if lines:
            prompt += "\n\nАвтомобиль пользователя:\n" + "\n".join(lines) + \
                      "\n\nУчитывай это при определении детали."
    return prompt

def ai_parse_request(user_text, vehicle=None):
    key = os.environ.get("OPENROUTER_API_KEY", BUILTIN["ai_key"]).strip()
    if not key or "ВСТАВЬ" in key:
        raise ValueError("Подбор временно недоступен.")
    sys_prompt = build_smart_prompt(vehicle)
    payload = json.dumps({
        "model": BUILTIN["ai_model"],
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_text}],
        "temperature": 0.1, "max_tokens": 500,
    }).encode("utf-8")
    req = Request(BUILTIN["ai_url"], data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "X-Title": "Parts Manager"})
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as ex:
        code = getattr(ex, "code", None)
        if code == 403:
            raise ValueError("Ключ подбора исчерпан или заблокирован.")
        if code == 401:
            raise ValueError("Неверный ключ подбора.")
        if code == 429:
            raise ValueError("Слишком много запросов. Подождите минуту.")
        raise ValueError(f"Сервис подбора недоступен: {ex}")
    choices = data.get("choices") or []
    if not choices or "message" not in choices[0]:
        err = data.get("error", "нет данных")
        raise ValueError(f"Неожиданный ответ подбора: {err}")
    content = choices[0]["message"].get("content", "")
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("Не удалось разобрать запрос. Попробуйте переформулировать.")
    p = json.loads(m.group(0))
    return {
        "article": p.get("article") or None,
        "brand": p.get("brand") or None,
        "name": p.get("name") or "",
        "keywords": p.get("keywords") or [],
        "max_price": parse_price(p.get("max_price")),
        "max_days": int(p["max_days"]) if p.get("max_days") is not None else None,
        "quantity": int(p.get("quantity") or 1),
    }

def ai_diagnose():
    key = os.environ.get("OPENROUTER_API_KEY", BUILTIN["ai_key"]).strip()
    payload = json.dumps({
        "model": BUILTIN["ai_model"],
        "messages": [{"role": "user", "content": "Ответь одним словом: работает"}],
        "max_tokens": 20,
    }).encode("utf-8")
    req = Request(BUILTIN["ai_url"], data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "X-Title": "Parts Manager"})
    try:
        with urlopen(req, timeout=45) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except Exception as ex:
        code = getattr(ex, "code", 0)
        body = ""
        if hasattr(ex, "read"):
            try:
                body = ex.read().decode("utf-8", "replace")
            except Exception:
                pass
        return code, f"{ex}\n\n{body}"

# ---------- Провайдеры ----------
def rossko_search(sup, article):
    cred = sup.get("credentials", {}) or {}
    key1 = cred.get("key1", "").strip()
    key2 = cred.get("key2", "").strip()
    delivery_id = cred.get("delivery_id", "").strip()
    address_id = cred.get("address_id", "").strip()
    if not (key1 and key2 and delivery_id and address_id and article):
        return [], [], "ключи или адрес не заполнены"
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="https://api.rossko.ru/">
  <soap:Body><ns:GetSearch><ns:KEY1>{xml_escape(key1)}</ns:KEY1><ns:KEY2>{xml_escape(key2)}</ns:KEY2>
  <ns:text>{xml_escape(article)}</ns:text><ns:delivery_id>{xml_escape(delivery_id)}</ns:delivery_id>
  <ns:address_id>{xml_escape(address_id)}</ns:address_id></ns:GetSearch></soap:Body></soap:Envelope>'''.encode("utf-8")
    ctx = ssl.create_default_context()
    try:
        req = Request(BUILTIN["rossko_search"], data=envelope, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{BUILTIN["rossko_search"]}"',
            "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=60, context=ctx) as resp:
            raw = resp.read()
    except Exception as ex:
        body = ""
        if hasattr(ex, "read"):
            try:
                body = ex.read().decode("utf-8", "replace")
            except Exception:
                pass
        return [], [], f"сеть ROSSKO: {ex} {body[:300]}"
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [], [], f"XML: {e}"
    texts = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el.text and el.text.strip():
            texts.setdefault(tag, []).append(el.text.strip())
    if (texts.get("success", ["false"])[0] or "").strip().lower() not in ("true", "1", "yes"):
        msg = texts.get("message", ["нет данных"])[0]
        return [], [], f"ROSSKO: {msg}"
    def parse_part(part_el):
        info = {"guid": "", "brand": "", "partnumber": "", "name": "", "stocks": []}
        for child in part_el:
            tag = child.tag.split("}")[-1]
            if tag in ("guid", "brand", "partnumber", "name"):
                info[tag] = (child.text or "").strip()
            elif tag == "stocks":
                for stock in child:
                    if stock.tag.split("}")[-1] != "stock":
                        continue
                    s = {}
                    for f in stock:
                        ft = f.tag.split("}")[-1]
                        if f.text:
                            s[ft] = f.text.strip()
                    info["stocks"].append(s)
        return info
    main, crosses = [], []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "PartsList":
            for part in el:
                if part.tag.split("}")[-1] != "Part":
                    continue
                info = parse_part(part)
                for s in info["stocks"]:
                    price = parse_price(s.get("price"))
                    if price is None:
                        continue
                    main.append({
                        "id": str(uuid.uuid4()), "supplier_id": sup["id"],
                        "supplier_name": sup.get("name", "ROSSKO"),
                        "brand": info["brand"], "partnumber": info["partnumber"],
                        "article": info["partnumber"], "name": info["name"],
                        "price": price, "count": int(s.get("count", 0) or 0),
                        "multiplicity": int(s.get("multiplicity", 1) or 1),
                        "delivery": s.get("delivery", ""),
                        "delivery_start": s.get("deliveryStart", ""),
                        "delivery_end": s.get("deliveryEnd", ""),
                        "warehouse": s.get("description", ""),
                        "warehouse_id": s.get("id", ""),
                        "extra": s.get("extra", "0") == "1",
                        "is_cross": False})
        elif tag == "crosses":
            for part in el:
                if part.tag.split("}")[-1] != "Part":
                    continue
                info = parse_part(part)
                for s in info["stocks"]:
                    price = parse_price(s.get("price"))
                    if price is None:
                        continue
                    crosses.append({
                        "id": str(uuid.uuid4()), "supplier_id": sup["id"],
                        "supplier_name": sup.get("name", "ROSSKO"),
                        "brand": info["brand"], "partnumber": info["partnumber"],
                        "article": info["partnumber"], "name": info["name"],
                        "price": price, "count": int(s.get("count", 0) or 0),
                        "multiplicity": int(s.get("multiplicity", 1) or 1),
                        "delivery": s.get("delivery", ""),
                        "delivery_start": s.get("deliveryStart", ""),
                        "delivery_end": s.get("deliveryEnd", ""),
                        "warehouse": s.get("description", ""),
                        "warehouse_id": s.get("id", ""),
                        "extra": s.get("extra", "0") == "1",
                        "is_cross": True})
    return main, crosses, ""

def berg_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    key = (cred.get("key", "") or cred.get("api_key", "")).strip()
    if not key:
        key = BUILTIN.get("berg_key_default", "").strip()
    if not key:
        return [], [], "не заполнен API-ключ"
    if not article:
        return [], [], "не указан артикул"
    base = (sup.get("endpoint") or BUILTIN["berg_base"]).rstrip("/")
    ver = BUILTIN["berg_version"]
    ctx = ssl.create_default_context()
    def do_request(art, br=""):
        params = {
            "key": key,
            "analogs": "1",
            "warehouse_types[]": ["1", "2", "3"],
            "items[0][resource_article]": art,
        }
        if br:
            params["items[0][brand_name]"] = br
        qs = urlencode(params, doseq=True)
        url = f"{base}/{ver}/ordering/get_stock.json?{qs}"
        try:
            req = Request(url, headers={
                "User-Agent": f"PartsManager/{VERSION}",
                "Accept": "application/json",
                "X-Berg-API-Key": key})
            with urlopen(req, timeout=45, context=ctx) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except HTTPError as ex:
            try:
                body = ex.read().decode("utf-8", "replace")
            except Exception:
                body = str(ex)
            if ex.code == 300:
                try:
                    return json.loads(body), ""
                except Exception:
                    pass
            if ex.code == 401:
                return None, "Ключ не подошёл. Проверьте, что он активен и скопирован целиком."
            return None, f"HTTP {ex.code}: {body[:400]}"
        except Exception as ex:
            return None, f"сеть: {ex}"
        try:
            return json.loads(raw), ""
        except json.JSONDecodeError as e:
            return None, f"JSON: {e} | {raw[:300]}"
    def parse_all(data, original_article):
        out = []
        if not isinstance(data, dict):
            return out
        if data.get("errors"):
            return out
        for res in (data.get("resources") or []):
            if not isinstance(res, dict):
                continue
            info = {
                "id": res.get("id"),
                "name": res.get("name") or "",
                "article": res.get("article") or original_article,
                "brand": (res.get("brand") or {}).get("name") or "",
            }
            offs = res.get("offers")
            if not offs:
                continue
            for off in offs:
                if not isinstance(off, dict):
                    continue
                price = parse_price(off.get("price"))
                if price is None:
                    continue
                term = off.get("average_period") or off.get("assured_period") or ""
                wh = off.get("warehouse") or {}
                out.append({
                    "id": str(uuid.uuid4()), "supplier_id": sup["id"],
                    "supplier_name": sup.get("name", "BERG"),
                    "brand": info["brand"],
                    "partnumber": info["article"], "article": info["article"],
                    "name": info["name"],
                    "price": price,
                    "count": int(off.get("quantity") or 0),
                    "multiplicity": int(off.get("multiplication_factor") or 1),
                    "delivery": str(term), "delivery_start": "", "delivery_end": "",
                    "warehouse": wh.get("name") or "BERG",
                    "warehouse_id": str(wh.get("id") or ""),
                    "extra": False, "is_cross": False,
                })
        return out
    data, err = do_request(article, brand)
    if err:
        return [], [], err
    all_offers = parse_all(data, article)
    if not all_offers and isinstance(data, dict):
        warnings_list = data.get("warnings") or []
        is_ambiguous = any((w or {}).get("code") == "WARN_ARTICLE_IS_AMBIGUOUS"
                           for w in warnings_list if isinstance(w, dict))
        resources = data.get("resources") or []
        if is_ambiguous and resources:
            seen_brands = []
            for res in resources:
                if not isinstance(res, dict):
                    continue
                b = (res.get("brand") or {}).get("name")
                if b and b not in seen_brands:
                    seen_brands.append(b)
            for b in seen_brands[:5]:
                data2, err2 = do_request(article, b)
                if err2:
                    continue
                all_offers += parse_all(data2, article)
    req_norm = normalize_article(article)
    main, crosses = [], []
    for o in all_offers:
        if normalize_article(o.get("article")) == req_norm:
            main.append(o)
        else:
            o["is_cross"] = True
            crosses.append(o)
    return main, crosses, ""

def zappro_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    login = cred.get("login", "").strip()
    password = cred.get("password", "").strip()
    if not login or not password:
        return [], [], "не заполнены логин и пароль"
    params = {"login": login, "password": password, "number": article}
    if brand:
        params["brand"] = brand
    url = BUILTIN["zappro_get_price"] + "?" + urlencode(params)
    ctx = ssl.create_default_context()
    try:
        req = Request(url, headers={"User-Agent": f"PartsManager/{VERSION}",
                                     "Accept": "application/json"})
        with urlopen(req, timeout=45, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            body = str(ex)
        return [], [], f"HTTP {ex.code}: {body[:500]}"
    except Exception as ex:
        return [], [], f"сеть: {ex}"
    if raw.strip().startswith("<"):
        return [], [], f"ответ XML (ожидался JSON): {raw[:300]}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [], f"JSON: {e} | {raw[:300]}"
    if isinstance(data, dict) and data.get("error"):
        return [], [], f"сервер: {data['error']}"
    items = data if isinstance(data, list) else data.get("goods") or data.get("items") or []
    if not isinstance(items, list):
        items = []
    all_offers = []
    for it in items:
        price = parse_price(it.get("price") or it.get("Price"))
        if price is None:
            continue
        count = int(it.get("count") or it.get("quantity") or 0)
        term = it.get("delivery") or it.get("deliveryDays") or it.get("term") or ""
        is_cross = bool(it.get("isAnalog") or it.get("analog") or it.get("is_cross"))
        all_offers.append({
            "id": str(uuid.uuid4()), "supplier_id": sup["id"],
            "supplier_name": sup.get("name", "ZAP PRO"),
            "brand": it.get("brand") or brand or "",
            "partnumber": it.get("number") or it.get("article") or article,
            "article": it.get("number") or it.get("article") or article,
            "name": it.get("name") or it.get("description") or "",
            "price": price, "count": count,
            "multiplicity": int(it.get("rate") or it.get("multiplicity") or 1),
            "delivery": str(term), "delivery_start": "", "delivery_end": "",
            "warehouse": it.get("warehouse") or it.get("store") or "ZAP PRO",
            "warehouse_id": it.get("warehouseId") or it.get("storeId") or "",
            "extra": False, "is_cross": is_cross})
    req_norm = normalize_article(article)
    main, crosses = [], []
    for o in all_offers:
        if o.get("is_cross") or normalize_article(o.get("article")) != req_norm:
            o["is_cross"] = True
            crosses.append(o)
        else:
            main.append(o)
    return main, crosses, ""

def avd_soap_request(method, params, endpoint=None):
    endpoint = (endpoint or BUILTIN["avd_url"]).strip()
    if not endpoint:
        endpoint = BUILTIN["avd_url"]
    ns = BUILTIN["avd_ns"]
    action = f"{ns}IAvdUserService/{method}"
    fields_xml = ""
    for key, val in params.items():
        if val is None:
            continue
        sval = str(val)
        if isinstance(val, bool):
            sval = "true" if val else "false"
        fields_xml += f"<ns:{key}>{xml_escape(sval)}</ns:{key}>"
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <{method} xmlns="{ns}">
      {fields_xml}
    </{method}>
  </s:Body>
</s:Envelope>'''.encode("utf-8")
    ctx = ssl.create_default_context()
    try:
        req = Request(endpoint, data=envelope, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{action}"',
            "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=60, context=ctx) as resp:
            return resp.read().decode("utf-8", "replace"), ""
    except HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            body = str(ex)
        fault_msg = ""
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("faultstring", "Message", "message") and el.text:
                    fault_msg = el.text.strip(); break
        except Exception:
            pass
        if ex.code == 404:
            return body, f"HTTP 404 — адрес сервиса не найден. Отправляли на: {endpoint}"
        return body, f"HTTP {ex.code}: {fault_msg or body[:400]}"
    except Exception as ex:
        return "", f"сеть: {ex}"

def avd_parse_result(xml_text):
    import xml.etree.ElementTree as ET
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    items = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("Item", "FastPrice", "PriceItem", "GetPriceResultFast", "Result"):
            items.append(el)
    if not items:
        for el in root.iter():
            children = list(el)
            if children and any((c.tag.split("}")[-1] == "Price") for c in children):
                items.append(el)
    def text_of(el, name):
        for child in el.iter():
            if child.tag.split("}")[-1] == name and child.text:
                return child.text.strip()
        return ""
    for it in items:
        price = parse_price(text_of(it, "Price"))
        if price is None:
            continue
        out.append({
            "ItemNumber": text_of(it, "ItemNumber") or text_of(it, "Number"),
            "ItemName": text_of(it, "ItemName") or text_of(it, "Name"),
            "CatalogName": text_of(it, "CatalogName") or text_of(it, "Catalog"),
            "Price": price,
            "Quantity": int(text_of(it, "Quantity") or 0),
            "Multiply": int(text_of(it, "Multiply") or 1),
            "SupplierName": text_of(it, "SupplierName") or text_of(it, "Supplier"),
            "SupplierPeriod": text_of(it, "SupplierPeriod") or text_of(it, "Period"),
            "SupplierRegion": text_of(it, "SupplierRegion"),
            "DealerStore": text_of(it, "DealerStore"),
            "Hash": text_of(it, "Hash"),
        })
    return out

def avd_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    endpoint = (cred.get("endpoint", "") or "").strip() or BUILTIN["avd_url"]
    login = cred.get("login", "").strip()
    password = cred.get("password", "").strip()
    if not login or not password:
        return [], [], "не заполнены логин и пароль"
    if not article:
        return [], [], "не указан артикул"
    ws_key = cred.get("wsKey", "").strip()
    base_params = {"login": login, "password": password, "number": article, "wsKey": ws_key}
    body, err = avd_soap_request("GetOriginalPrice", base_params, endpoint=endpoint)
    if err:
        return [], [], err
    original = avd_parse_result(body)
    crosses = []
    if original:
        catalog = original[0].get("CatalogName") or ""
        cross_params = {"login": login, "password": password, "number": article,
                        "catalog": catalog, "wsKey": ws_key}
        body2, err2 = avd_soap_request("GetFastCrossesPrice", cross_params, endpoint=endpoint)
        if not err2:
            crosses = avd_parse_result(body2)
    def to_offer(x, is_cross):
        return {
            "id": str(uuid.uuid4()), "supplier_id": sup["id"],
            "supplier_name": sup.get("name", "AVD"),
            "brand": x.get("SupplierName") or brand or "",
            "partnumber": x.get("ItemNumber") or article,
            "article": x.get("ItemNumber") or article,
            "name": x.get("ItemName") or "",
            "price": x.get("Price"),
            "count": x.get("Quantity") or 0,
            "multiplicity": x.get("Multiply") or 1,
            "delivery": str(x.get("SupplierPeriod") or ""),
            "delivery_start": "", "delivery_end": "",
            "warehouse": x.get("DealerStore") or x.get("SupplierRegion") or "AVD",
            "warehouse_id": x.get("Hash") or "",
            "extra": False, "is_cross": is_cross,
        }
    main = [to_offer(x, False) for x in original]
    cross_offers = [to_offer(x, True) for x in crosses]
    return main, cross_offers, ""

def custom_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    url_tpl = (cred.get("url", "") or "").strip()
    api_key = (cred.get("api_key", "") or "").strip()
    auth_mode = (cred.get("auth_mode", "url") or "url").strip()
    art_param = (cred.get("url_article_param", "") or "article").strip()
    if not url_tpl:
        return [], [], "не задан URL запроса"
    if not article:
        return [], [], "не указан артикул"
    if "{article}" in url_tpl:
        url = url_tpl.replace("{article}", quote(article))
    else:
        sep = "&" if "?" in url_tpl else "?"
        url = f"{url_tpl}{sep}{urlencode({art_param: article})}"
    headers = {"User-Agent": f"PartsManager/{VERSION}", "Accept": "application/json"}
    if api_key and auth_mode == "header":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    elif api_key and auth_mode == "url":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode({'key': api_key})}"
    ctx = ssl.create_default_context()
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=45, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")
        except Exception:
            body = str(ex)
        return [], [], f"HTTP {ex.code}: {body[:500]}"
    except Exception as ex:
        return [], [], f"сеть: {ex}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [], f"JSON: {e} | {raw[:300]}"
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("items", "goods", "results", "offers", "products", "parts", "data"):
            if isinstance(data.get(key), list):
                items = data[key]; break
    all_offers = []
    for it in items:
        if not isinstance(it, dict):
            continue
        price = None
        for pk in ("price", "Price", "cost", "amount"):
            price = parse_price(it.get(pk))
            if price is not None:
                break
        if price is None:
            continue
        art = it.get("article") or it.get("Article") or it.get("number") or it.get("sku") or article
        count = 0
        for ck in ("count", "quantity", "stock", "qty", "available"):
            try:
                count = int(it.get(ck) or 0); break
            except (ValueError, TypeError):
                continue
        term = it.get("delivery") or it.get("term") or it.get("days") or it.get("deliveryDays") or ""
        all_offers.append({
            "id": str(uuid.uuid4()), "supplier_id": sup["id"],
            "supplier_name": sup.get("name", "Поставщик"),
            "brand": it.get("brand") or it.get("Brand") or brand or "",
            "partnumber": art, "article": art,
            "name": it.get("name") or it.get("description") or it.get("title") or "",
            "price": price, "count": count,
            "multiplicity": int(it.get("multiplicity") or it.get("rate") or 1),
            "delivery": str(term), "delivery_start": "", "delivery_end": "",
            "warehouse": it.get("warehouse") or it.get("store") or sup.get("name", ""),
            "warehouse_id": str(it.get("warehouse_id") or ""),
            "extra": False, "is_cross": bool(it.get("is_cross") or it.get("analog"))})
    req_norm = normalize_article(article)
    main, crosses = [], []
    for o in all_offers:
        if o.get("is_cross") or normalize_article(o.get("article")) != req_norm:
            o["is_cross"] = True
            crosses.append(o)
        else:
            main.append(o)
    return main, crosses, ""

def collect_offers(article, filters, ai_hint=None, sort_mode="price_delivery"):
    suppliers = read("suppliers")
    if not suppliers:
        return [], [], []
    main_offers, all_crosses, errors = [], [], []
    def apply_filters(lst):
        out = []
        for o in lst:
            if filters.get("min_price") is not None and o["price"] < filters["min_price"]:
                continue
            if filters.get("max_price") is not None and o["price"] > filters["max_price"]:
                continue
            if filters.get("max_days") is not None:
                days = delivery_days(o.get("delivery"))
                if days is not None and days > filters["max_days"]:
                    continue
            if filters.get("in_stock_only") and o.get("count", 0) <= 0:
                continue
            out.append(o)
        return out
    brand = (ai_hint or {}).get("brand") or ""
    for s in suppliers:
        tpl = s.get("template", "custom")
        if tpl not in API_TYPES:
            tpl = "custom"
        try:
            if tpl == "rossko": m, c, err = rossko_search(s, article)
            elif tpl == "berg": m, c, err = berg_search(s, article, brand)
            elif tpl == "zappro": m, c, err = zappro_search(s, article, brand)
            elif tpl == "avd": m, c, err = avd_search(s, article, brand)
            else: m, c, err = custom_search(s, article, brand)
        except Exception as ex:
            m, c, err = [], [], f"ошибка: {ex}"
        # Коммерческий режим: демо/заглушки никогда не попадают в выдачу.
        m = [o for o in (m or []) if not o.get("demo") and not o.get("is_demo")]
        c = [o for o in (c or []) if not o.get("demo") and not o.get("is_demo")]
        if err:
            errors.append(f"{s.get('name', tpl)}: {err}")
        else:
            main_offers += m
            all_crosses += c
    main_offers = apply_filters(main_offers)
    all_crosses = apply_filters(all_crosses)
    return main_offers, all_crosses, errors

def offer_sort_key(o, mode="smart", requested_article="", requested_brand=""):
    price = parse_price(o.get("price"))
    price = price if price is not None else 10**12
    days = delivery_days(o.get("delivery"))
    days_key = days if days is not None else 9999
    stock = 0 if int(o.get("count", 0) or 0) > 0 else 1
    exact_article = 0 if normalize_article(o.get("article")) == normalize_article(requested_article) else 1
    exact_brand = 0 if requested_brand and normalize_brand(o.get("brand")) == normalize_brand(requested_brand) else 1
    if mode == "price": return (exact_article, price, days_key, stock)
    if mode == "delivery": return (exact_article, days_key, price, stock)
    if mode == "stock": return (exact_article, stock, days_key, price)
    return (exact_article, exact_brand, stock, days_key, price)

def sort_offers(offers, mode="smart", requested_article="", requested_brand=""):
    return sorted(list(offers or []), key=lambda o: offer_sort_key(o, mode, requested_article, requested_brand))

def group_by_article(offers):
    groups = {}
    for o in offers:
        key = normalize_article(o.get("article") or "")
        if key not in groups:
            groups[key] = {"article": o.get("article") or "", "brand": o.get("brand") or "",
                           "name": o.get("name") or "", "offers": []}
        groups[key]["offers"].append(o)
        if not groups[key]["name"] and o.get("name"): groups[key]["name"] = o["name"]
        if not groups[key]["brand"] and o.get("brand"): groups[key]["brand"] = o["brand"]
    result = list(groups.values())
    for g in result:
        g["offers"].sort(key=lambda x: (parse_price(x.get("price")) if parse_price(x.get("price")) is not None else 10**12, delivery_days(x.get("delivery")) if delivery_days(x.get("delivery")) is not None else 9999))
        g["min_price"] = g["offers"][0]["price"] if g["offers"] else 0
    result.sort(key=lambda g: g["min_price"])
    return result

def top_selection(offers):
    if not offers:
        return [], False
    by_price = sorted(offers, key=lambda o: o["price"])
    def dk(o):
        d = delivery_days(o.get("delivery"))
        return d if d is not None else 999
    by_delivery = sorted(offers, key=lambda o: (dk(o), o["price"]))
    picked, seen = [], set()
    if by_price:
        picked.append(by_price[0]); seen.add(by_price[0]["id"])
    for o in by_delivery:
        if len(picked) >= COLLAPSED_ROWS: break
        if o["id"] in seen: continue
        picked.append(o); seen.add(o["id"])
    for o in by_price:
        if len(picked) >= COLLAPSED_ROWS: break
        if o["id"] in seen: continue
        picked.append(o); seen.add(o["id"])
    return picked, len(offers) > len(picked)

def optimize_cart(strategy="standard", allow_analogs=True):
    """Оптимизация корзины.

    Правила:
    - Если старая позиция всё ещё в наличии и новая цена дороже — оставляем старую.
    - Если старая позиция пропала или её count=0 — берём лучший доступный
      вариант и помечаем is_forced=True, reason="...".
    """
    cart = read("cart")
    if not cart:
        return [], [], {}
    new_cart, report = [], []
    total_before = 0.0; total_after = 0.0; max_days_after = 0

    for item in cart:
        price_old = parse_price(item.get("price")) or 0.0
        qty = int(item.get("qty", 1) or 1)
        days_old = delivery_days(item.get("term"))
        total_before += price_old * qty
        article = item.get("article") or ""
        brand_old = item.get("brand") or ""
        supplier_old = item.get("supplier_name") or ""
        supplier_id_old = item.get("supplier_id") or ""
        new_item = dict(item)

        alt_main, alt_crosses, _ = collect_offers(
            article, {"min_price": None, "max_price": None, "max_days": None, "in_stock_only": False},
            sort_mode="price_delivery")
        candidates = list(alt_main)
        if allow_analogs:
            for c in alt_crosses:
                c["is_cross"] = True
                candidates.append(c)

        # Фильтр по бренду
        if not allow_analogs:
            norm_brand_old = normalize_brand(brand_old)
            if norm_brand_old:
                filtered = [c for c in candidates
                            if normalize_brand(c.get("brand")) == norm_brand_old]
                if filtered:
                    candidates = filtered
                else:
                    candidates = []

        # Ищем "старую" позицию (тот же поставщик, артикул, бренд) в свежих данных
        old_offer_found = None
        for c in candidates:
            if (c.get("supplier_id") == supplier_id_old
                and normalize_article(c.get("article") or "") == normalize_article(article)):
                old_offer_found = c
                break

        old_still_available = bool(old_offer_found and int(old_offer_found.get("count", 0)) > 0)

        # Правило: если старая позиция ещё в наличии — не заменяем на дорогую
        if old_still_available:
            # Проверим, что лучший кандидат дешевле старого
            valid = [c for c in candidates if int(c.get("count", 0) or 0) > 0]
            if not valid:
                valid = candidates
            if strategy == "price":
                valid.sort(key=lambda c: (c["price"], delivery_days(c.get("delivery")) or 999))
            elif strategy == "delivery":
                valid.sort(key=lambda c: (delivery_days(c.get("delivery")) or 999, c["price"]))
            else:
                valid.sort(key=lambda c: (c["price"], delivery_days(c.get("delivery")) or 999))
            best = valid[0] if valid else old_offer_found
            if best["price"] >= price_old - 0.01:
                # Оставляем старую позицию
                total_after += price_old * qty
                if days_old is not None: max_days_after = max(max_days_after, days_old)
                new_cart.append(new_item)
                report.append({
                    "article": article, "brand": brand_old,
                    "old_price": price_old, "old_supplier": supplier_old,
                    "old_days": days_old,
                    "new_price": price_old, "new_supplier": supplier_old,
                    "new_days": days_old, "changed": False, "is_cross": False,
                    "is_forced": False, "reason": "",
                })
                continue
            # Иначе берём лучший (дешевле старого)
            best_offer = best
            forced = False
            reason = ""
        else:
            # Старая позиция недоступна — ищем лучший доступный вариант
            valid = [c for c in candidates if int(c.get("count", 0) or 0) > 0]
            if strategy == "price":
                valid.sort(key=lambda c: (c["price"], delivery_days(c.get("delivery")) or 999))
            elif strategy == "delivery":
                valid.sort(key=lambda c: (delivery_days(c.get("delivery")) or 999, c["price"]))
            else:
                valid.sort(key=lambda c: (c["price"], delivery_days(c.get("delivery")) or 999))
            if not valid:
                # Ничего не нашли — оставляем как есть с пометкой
                total_after += price_old * qty
                if days_old is not None: max_days_after = max(max_days_after, days_old)
                new_cart.append(new_item)
                report.append({
                    "article": article, "brand": brand_old,
                    "old_price": price_old, "old_supplier": supplier_old,
                    "old_days": days_old,
                    "new_price": price_old, "new_supplier": supplier_old,
                    "new_days": days_old, "changed": False, "is_cross": False,
                    "is_forced": True,
                    "reason": "Позиция недоступна ни у одного поставщика",
                })
                continue
            best_offer = valid[0]
            forced = True
            if old_offer_found is None:
                reason = "Позиция пропала у поставщика, заменили на доступный вариант"
            else:
                reason = "Товар выкуплен, найдена замена"
            if best_offer["price"] > price_old:
                reason += f" (было {fmt_price(price_old)} ₽ → стало {fmt_price(best_offer['price'])} ₽)"
            else:
                reason += f" (было {fmt_price(price_old)} ₽ → стало {fmt_price(best_offer['price'])} ₽)"

        # Применяем замену
        new_price = best_offer["price"]
        nd = delivery_days(best_offer.get("delivery"))
        new_item["price"] = new_price
        new_item["supplier_id"] = best_offer.get("supplier_id", new_item.get("supplier_id", ""))
        new_item["supplier_name"] = best_offer.get("supplier_name", "")
        new_item["term"] = best_offer.get("delivery", "")
        new_item["brand"] = best_offer.get("brand") or brand_old
        total_after += new_price * qty
        if nd is not None: max_days_after = max(max_days_after, nd)
        new_cart.append(new_item)
        report.append({
            "article": article, "brand": brand_old,
            "old_price": price_old, "old_supplier": supplier_old,
            "old_days": days_old,
            "new_price": new_price, "new_supplier": best_offer.get("supplier_name", ""),
            "new_days": nd,
            "changed": (price_old != new_price) or (supplier_old != best_offer.get("supplier_name", "")),
            "is_cross": best_offer.get("is_cross", False),
            "is_forced": bool(forced),
            "reason": reason,
        })

    today = datetime.now()
    last_date = today + timedelta(days=max_days_after) if max_days_after else None
    summary = {"total_before": total_before, "total_after": total_after,
               "diff": total_before - total_after,
               "last_delivery_date": last_date.strftime("%d.%m.%Y") if last_date else "—",
               "max_days_after": max_days_after}
    return new_cart, report, summary

def cart_index():
    idx = {}
    for x in read("cart"):
        key = (normalize_article(x.get("article") or ""), x.get("supplier_id") or "")
        idx[key] = idx.get(key, 0) + int(x.get("qty", 1) or 1)
    return idx

def cart_index_by_article():
    idx = {}
    for x in read("cart"):
        key = normalize_article(x.get("article") or "")
        idx[key] = idx.get(key, 0) + int(x.get("qty", 1) or 1)
    return idx

def cart_item_id(article, supplier_id):
    for x in read("cart"):
        if normalize_article(x.get("article") or "") == normalize_article(article) and \
           (x.get("supplier_id") or "") == (supplier_id or ""):
            return x.get("id")
    return None

CSS = r"""
:root{--bg:#f3f6fa;--card:#fff;--text:#172033;--muted:#667085;--blue:#2563eb;--dark:#0f172a;--green:#16a34a;--red:#dc2626;--line:#e2e8f0;--orange:#d97706;--violet:#7c3aed}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Segoe UI,Arial,sans-serif}
header{background:var(--dark);color:#fff;padding:15px 28px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:5;box-shadow:0 2px 10px #0002}
.brand{font-size:20px;font-weight:800}.ver{font-weight:400;color:#94a3b8;font-size:13px;margin-left:6px}
nav{display:flex;gap:8px;flex-wrap:wrap}nav a{color:#cbd5e1;text-decoration:none;padding:8px 10px;border-radius:8px}nav a:hover{background:#ffffff12;color:#fff}
main{max-width:1240px;margin:25px auto;padding:0 18px 50px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:21px;margin-bottom:17px;box-shadow:0 5px 20px #0f172a0a}
h1{font-size:25px;margin:0 0 7px}h2{font-size:19px;margin:0 0 12px}.muted{color:var(--muted)}.title{display:flex;justify-content:space-between;gap:15px;align-items:center}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.field{display:flex;flex-direction:column;gap:6px}.field label{font-weight:700}.field small{color:var(--muted);line-height:1.35}
input,select,textarea{font:inherit;width:100%;border:1px solid #cbd5e1;border-radius:9px;padding:10px 11px;background:#fff}textarea{min-height:95px;resize:vertical}
button,.btn{font:inherit;font-weight:700;border:0;border-radius:9px;padding:10px 14px;background:var(--blue);color:#fff;text-decoration:none;display:inline-block;cursor:pointer}button:hover,.btn:hover{filter:brightness(.96)}
.secondary{background:#e2e8f0;color:#172033}.green{background:var(--green)}.red{background:var(--red)}.orange{background:var(--orange)}.violet{background:var(--violet)}
.toolbar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:15px}
.note,.ok,.warn,.err{padding:12px 14px;border-radius:10px;margin:10px 0}
.note{background:#eff6ff;color:#1d4ed8}.ok{background:#ecfdf5;color:#166534}
.warn{background:#fffbeb;color:#92400e}.err{background:#fef2f2;color:#991b1b}
table{width:100%;border-collapse:collapse}th,td{padding:11px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:#475569;font-size:12px}
.badge{display:inline-block;padding:4px 8px;border-radius:99px;background:#eef2ff;color:#3730a3;font-weight:700;font-size:12px}
.badge.green{background:#dcfce7;color:#166534}.badge.red{background:#fee2e2;color:#991b1b}
.badge.orange{background:#fed7aa;color:#9a3412}.badge.violet{background:#ede9fe;color:#5b21b6}
.price{font-weight:800;font-size:16px}.vin{font:700 18px Consolas,monospace;letter-spacing:1px}
.hero{display:grid;grid-template-columns:1.4fr .6fr;gap:17px}
.quick{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.quick .btn{text-align:center;padding:16px 8px}
.row-actions form{display:inline}
.xml-box{background:#0f172a;color:#a5f3fc;padding:15px;border-radius:10px;font:12px Consolas,monospace;white-space:pre-wrap;max-height:500px;overflow:auto;word-break:break-all}
.warehouse{font-size:12px;color:var(--muted);line-height:1.3;margin-top:2px}
.flash{position:fixed;top:70px;left:50%;transform:translateX(-50%);background:#16a34a;color:#fff;padding:12px 20px;border-radius:10px;box-shadow:0 6px 20px #0003;z-index:100;display:flex;gap:14px;align-items:center;font-weight:600}
.flash a{color:#fff;text-decoration:underline;font-weight:700}
.art-block{border:1px solid var(--line);border-radius:12px;margin-bottom:18px;overflow:hidden}
.art-head{background:#f8fafc;padding:12px 16px;border-bottom:1px solid var(--line)}
.art-head .art-line{font-size:16px;font-weight:800}
.art-head .art-line .art-brand{color:var(--violet)}
.art-head .art-name{color:var(--muted);font-size:13px;margin-top:2px}
.art-more{padding:10px 16px;background:#f8fafc;border-top:1px solid var(--line);font-size:13px}
.art-more a{color:var(--blue);text-decoration:none;font-weight:700}
.cross-tag{display:inline-block;background:#ede9fe;color:#5b21b6;font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;margin-left:6px}
.cart-btn{display:inline-block;padding:6px 10px;background:var(--blue);color:#fff;border-radius:8px;text-decoration:none;font-weight:700;border:0;cursor:pointer;font-size:15px;line-height:1}
.cart-btn:hover{filter:brightness(.95)}
.cart-in-row{background:#f0fdf4!important}
.cart-mark{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:8px;background:#dcfce7;color:#166534;font-size:12px;font-weight:800}
.cart-btn.in-cart{background:#dcfce7;color:#166534;border:1px solid #86efac;box-shadow:inset 0 0 0 1px #bbf7d0}
.qty-pm{display:inline-flex;align-items:center;gap:6px;font-weight:700}
.qty-pm a,.qty-pm button{width:26px;height:26px;line-height:1;padding:0;background:#e2e8f0;color:#172033;border-radius:6px;border:0;cursor:pointer;font-weight:800;font-size:14px;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}
.qty-pm .num{min-width:22px;text-align:center}
.opt-radio{display:flex;gap:12px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.opt-radio label{display:inline-flex;gap:6px;align-items:center;padding:10px 14px;background:#f8fafc;border:2px solid var(--line);border-radius:10px;cursor:pointer;font-weight:600}
.opt-radio input{width:auto}
.saved{color:var(--green);font-weight:800}
.loss{color:var(--red);font-weight:800}
.steps{display:flex;gap:8px;margin-bottom:16px;font-size:13px;color:var(--muted)}
.steps span{padding:6px 12px;border-radius:99px;background:#eef2ff}
.steps span.active{background:var(--blue);color:#fff;font-weight:700}
.steps span.done{background:#dcfce7;color:#166534}
.tile{display:block;background:#fff;border:2px solid var(--line);border-radius:14px;padding:18px;text-decoration:none;color:var(--text);transition:all .15s;margin-bottom:10px;cursor:pointer}
.tile:hover{border-color:var(--blue);box-shadow:0 4px 18px #2563eb22}
.tile.selected{border-color:var(--blue);background:#eff6ff}
.tile .nm{font-size:17px;font-weight:800}
.tile .hint{color:var(--muted);font-size:13px;margin-top:4px}
.forced-icon{display:inline-block;margin-left:6px;color:#d97706;font-weight:900;cursor:help;font-size:16px}
.forced-row{background:#fffbeb}
@media(max-width:850px){.grid,.hero{grid-template-columns:1fr}.quick{grid-template-columns:1fr}header{padding:12px 14px}nav{display:none}}
"""

def layout(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head>
<body><header><div class="brand">Parts Manager <span class="ver">v{VERSION}</span></div><nav>
<a href="/">Проценка</a><a href="/smart">🤖 Умный подбор</a><a href="/vehicle">🚗 VIN / Авто</a><a href="/suppliers">Поставщики</a><a href="/orders">📋 Заказы</a><a href="/finance">💰 Финансы</a><a href="/customers">👥 Клиенты</a><a href="/cart">🛒 Корзина{(" · " + str(sum(int(x.get("qty",1) or 1) for x in read("cart")))) if read("cart") else ""}</a><a href="/settings">⚙ Настройки</a></nav></header><main>{body}</main></body></html>"""

def steps_html(current):
    def cls(n):
        if n < current: return "done"
        if n == current: return "active"
        return ""
    return f"""<div class="steps">
<span class="{cls(1)}">1. Название</span>
<span class="{cls(2)}">2. Тип API</span>
<span class="{cls(3)}">3. Данные</span>
</div>"""

def postdata(h):
    n = int(h.headers.get("Content-Length", "0"))
    return parse_qs(h.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)

def supplier(sid):
    return next((x for x in read("suppliers") if x.get("id") == sid), None)

def vehicle(vid):
    return next((x for x in read("vehicles") if x.get("id") == vid), None)

def active_vehicle():
    s = read("settings")
    vid = s.get("active_vehicle_id") or ""
    v = vehicle(vid) if vid else None
    if v: return v
    vs = read("vehicles")
    return vs[-1] if vs else None

def render_cart_cell(o, cart_by_supplier, cart_by_article):
    key = (normalize_article(o.get("article") or ""), o.get("supplier_id") or "")
    art_key = normalize_article(o.get("article") or "")
    qty_in_cart = cart_by_supplier.get(key, 0)
    if qty_in_cart > 0:
        item_id = cart_item_id(o.get("article"), o.get("supplier_id"))
        return (f'<div class="qty-pm">'
                f'<form method="post" action="/cart_dec" style="display:inline">'
                f'<input type="hidden" name="id" value="{esc(item_id or "")}">'
                f'<button title="Убрать одну">−</button></form>'
                f'<span class="num">{qty_in_cart}</span>'
                f'<form method="post" action="/cart_inc" style="display:inline">'
                f'<input type="hidden" name="id" value="{esc(item_id or "")}">'
                f'<button title="Добавить ещё одну">+</button></form>'
                f'</div>')
    if art_key and art_key in cart_by_article:
        other_qty = cart_by_article[art_key]
        return f'<span class="cart-btn in-cart" title="Уже в корзине: {other_qty} шт.">✓ В корзине · {other_qty}</span>'
    return (f'<form method="post" action="/add_cart" style="display:inline">'
            f'<input type="hidden" name="supplier_id" value="{esc(o.get("supplier_id",""))}">'
            f'<input type="hidden" name="article" value="{esc(o.get("article",""))}">'
            f'<input type="hidden" name="brand" value="{esc(o.get("brand",""))}">'
            f'<input type="hidden" name="name" value="{esc(o.get("name",""))}">'
            f'<input type="hidden" name="price" value="{esc(o.get("price",0))}">'
            f'<input type="hidden" name="term" value="{esc(o.get("delivery",""))}">'
            f'<input type="hidden" name="qty" value="1">'
            f'<button class="cart-btn" title="Добавить в корзину">🛒</button>'
            f'</form>')

def render_offer_rows(offers, cart_by_supplier, cart_by_article):
    rows = ""
    for o in offers:
        term = human_delivery(o.get("delivery"), o.get("delivery_start", ""))
        wh_text = esc(o.get("warehouse", "") or "—")
        if len(wh_text) > 70:
            wh_text = wh_text[:70] + "…"
        count = o.get("count", 0)
        mult = o.get("multiplicity", 1)
        count_txt = f"{count} шт." + (f" ×{mult}" if mult > 1 else "")
        stock_badge = ("<span class='badge green'>в наличии</span>" if count > 0
                       else "<span class='badge red'>под заказ</span>")
        _cart_key = (normalize_article(o.get("article") or ""), o.get("supplier_id") or "")
        _in_cart = cart_by_supplier.get(_cart_key, 0) > 0
        rows += f"""<tr{' class="cart-in-row"' if _in_cart else ''}>
<td><b>{esc(o.get('supplier_name','—'))}</b>{'<div class="cart-mark">✓ В корзине</div>' if _in_cart else ''}</td>
<td>{esc(term)}</td>
<td>{wh_text}</td>
<td>{count_txt} {stock_badge}</td>
<td class="price">{fmt_price(o['price'])} ₽</td>
<td>{render_cart_cell(o, cart_by_supplier, cart_by_article)}</td>
</tr>"""
    return rows

_current_article = [""]
_current_filters = {}

def render_grouped_results(main_offers, crosses, cart_by_supplier, cart_by_article,
                           sort_mode="smart", cross_sort_mode="price", requested_article="", requested_brand=""):
    html_parts = []
    if main_offers:
        main_sorted = sort_offers(main_offers, sort_mode, requested_article, requested_brand)
        main_top, main_has_more = top_selection(main_sorted)
        main_rows = render_offer_rows(main_top, cart_by_supplier, cart_by_article)
        art = main_top[0].get("article", "") if main_top else ""
        brand = main_top[0].get("brand", "") if main_top else ""
        name = main_top[0].get("name", "") if main_top else ""
        more_html = ""
        if main_has_more:
            more_html = (f'<div class="art-more">Показать все предложения (ещё '
                         f'{len(main_sorted) - len(main_top)}) '
                         f'<a href="/expand?article={quote(art)}&group=main">развернуть</a></div>')
        html_parts.append(f"""<div class="art-block">
<div class="art-head">
  <div class="art-line">{esc(art)} <span class="art-brand">· {esc(brand)}</span></div>
  <div class="art-name">{esc(name)}</div>
  <div class="toolbar" style="margin-top:8px">
  <form method="get" action="/" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
  <input type="hidden" name="part" value="{esc(requested_article or art)}">
  <span class="muted">Сортировка:</span>
  <select name="sort" onchange="this.form.submit()" style="width:auto">
  <option value="smart" {'selected' if sort_mode=='smart' else ''}>Оптимальная</option>
  <option value="price" {'selected' if sort_mode=='price' else ''}>Сначала дешевле</option>
  <option value="delivery" {'selected' if sort_mode=='delivery' else ''}>Сначала быстрее</option>
  <option value="stock" {'selected' if sort_mode=='stock' else ''}>Сначала в наличии</option>
  </select></form></div>
</div>
<div class="art-body"><table>
<tr><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{main_rows}
</table></div>{more_html}</div>""")
    else:
        html_parts.append("<div class='card'><div class='muted'>По запрошенному артикулу предложений нет.</div></div>")
    if crosses:
        cross_groups = group_by_article(crosses)
        def gkey(g):
            d = delivery_days(g["offers"][0].get("delivery")) if g["offers"] else 999
            return (d if d is not None else 999, g["min_price"])
        if cross_sort_mode == "price":
            cross_groups.sort(key=lambda g: g["min_price"])
        elif cross_sort_mode == "delivery":
            cross_groups.sort(key=gkey)
        else:
            cross_groups.sort(key=lambda g: (g["min_price"], gkey(g)[0]))
        def sel(m):
            return "selected" if cross_sort_mode == m else ""
        sort_html = f"""<div class="toolbar" style="margin-bottom:12px">
<form method="get" action="/" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<input type="hidden" name="part" value="{esc(_current_article[0] if _current_article else '')}">
<input type="hidden" name="min_price" value="{esc(_current_filters.get('min_price', '') or '')}">
<input type="hidden" name="max_price" value="{esc(_current_filters.get('max_price', '') or '')}">
<input type="hidden" name="max_days" value="{esc(_current_filters.get('max_days', '') or '')}">
<input type="hidden" name="in_stock" value="{esc('1' if _current_filters.get('in_stock_only') else '')}">
<span class="muted">Сортировка аналогов:</span>
<select name="cross_sort" onchange="this.form.submit()" style="width:auto">
<option value="price" {sel("price")}>По цене</option>
<option value="delivery" {sel("delivery")}>По сроку</option>
<option value="standard" {sel("standard")}>Стандарт</option>
</select>
</form></div>"""
        blocks = [sort_html]
        for g in cross_groups:
            top, has_more = top_selection(g["offers"])
            rows = render_offer_rows(top, cart_by_supplier, cart_by_article)
            more = ""
            if has_more:
                more = (f'<div class="art-more">Показать все предложения (ещё '
                        f'{len(g["offers"]) - len(top)}) '
                        f'<a href="/expand?article={quote(g["article"])}&group=cross">развернуть</a></div>')
            blocks.append(f"""<div class="art-block">
<div class="art-head">
  <div class="art-line">{esc(g['article'])} <span class="art-brand">· {esc(g['brand'])}</span><span class="cross-tag">аналог</span></div>
  <div class="art-name">{esc(g['name'])}</div>
</div>
<div class="art-body"><table>
<tr><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{rows}
</table></div>{more}</div>""")
        html_parts.append("<div class='card'><h2>Аналоги</h2>" + "".join(blocks) + "</div>")
    return "".join(html_parts)

def render_test_result(sid, sup, tpl, article, main, crosses, err):
    if err:
        extra_hint = ""
        if tpl == "avd" and ("IP" in err or "ip" in err.lower() or "адрес" in err.lower() or "404" in err):
            extra_hint = ("<div class='warn'><b>Если ошибка про IP:</b> зайдите на avdmotors.ru → Личный кабинет "
                          "→ Настройки → IP-адрес → добавьте ваш внешний IP (узнать на 2ip.ru).<br><br>"
                          "<b>Если ошибка 404:</b> в карточке поставщика поменяйте «Адрес WEB-сервиса» на один из:<br>"
                          "<code>https://ws1.avdmotors.ru/AvdUserService.svc/secure</code><br>"
                          "<code>https://ws1.avdmotors.ru/AvdUserService.svc</code></div>")
        return f"""<div class="card"><h1>Проверка поставщика</h1>
<div class="err"><b>Подключение не прошло проверку.</b> {esc(err)}</div>
<div class="note"><b>Поставщик не сохранён.</b> Исправьте данные и повторите проверку.</div>
{extra_hint}
<div class="note">Проверьте данные доступа. Если всё верно — возможно, проблема на стороне поставщика.</div>
<div class="toolbar">
<a class="btn orange" href="/wizard3?name={quote(sup.get('name',''))}&tpl={quote(tpl)}">← Исправить данные</a>
<a class="btn secondary" href="/suppliers">К списку поставщиков</a>
</div></div>"""
    cart_by_supplier = cart_index(); cart_by_article = cart_index_by_article()
    html = render_grouped_results(main, crosses, cart_by_supplier, cart_by_article,
                                   cross_sort_mode="price")
    return f"""<div class="card"><div class="title"><div><h1>✓ Всё работает</h1>
<div class="muted">Поставщик: {esc(sup.get('name', tpl))} · Тестовый артикул: {esc(article)}</div>
<div class="ok">Подключение проверено и поставщик сохранён. В проценке будут учитываться только реальные ответы этого API.</div></div>
<a class="btn secondary" href="/supplier?id={esc(sid)}">Открыть карточку</a>
{("<form method=\"post\" action=\"/rossko_checkout_details\" style=\"display:inline\"><input type=\"hidden\" name=\"id\" value=\"" + esc(sid) + "\"><button class=\"orange\">🚚 Проверить доставку</button></form>" if tpl == "rossko" else "")}</div>
<div class="ok">Подключение успешно. Найдено основных: {len(main)}, аналогов: {len(crosses)}.</div></div>
{html}
<div class="toolbar"><a class="btn green" href="/suppliers">Готово — к списку поставщиков</a></div>"""


DB_PATH = os.path.join(DATA_DIR, "parts_manager.db")
ORDER_STATUSES = ["Новый", "Отправляется", "Отправлен", "Подтверждён поставщиком", "В обработке", "Отгружен", "Завершён", "Отменён", "Ошибка отправки"]
PAYMENT_STATUSES = ["Не оплачено", "Частично оплачено", "Оплачено", "Возврат"]

def db_conn():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    with db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS customers(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT DEFAULT '', phone TEXT DEFAULT '',
          email TEXT DEFAULT '', city TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(
          id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL, customer_id TEXT DEFAULT '', vehicle_id TEXT DEFAULT '',
          supplier_id TEXT DEFAULT '', supplier_name TEXT DEFAULT '', channel TEXT DEFAULT '', status TEXT NOT NULL,
          payment_status TEXT NOT NULL, external_no TEXT DEFAULT '', subtotal REAL DEFAULT 0, cost REAL DEFAULT 0,
          profit REAL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS order_items(
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL, article TEXT, brand TEXT, name TEXT, qty INTEGER DEFAULT 1,
          price REAL, cost REAL, term TEXT, supplier_article TEXT DEFAULT '', FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS order_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, status TEXT, message TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS payments(
          id TEXT PRIMARY KEY, order_id TEXT DEFAULT '', customer_id TEXT DEFAULT '', supplier_id TEXT DEFAULT '',
          kind TEXT NOT NULL, amount REAL NOT NULL, currency TEXT DEFAULT 'RUB', note TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS financial_transactions(
          id TEXT PRIMARY KEY, order_id TEXT DEFAULT '', customer_id TEXT DEFAULT '', supplier_id TEXT DEFAULT '',
          kind TEXT NOT NULL, category TEXT NOT NULL, amount REAL NOT NULL, direction TEXT NOT NULL,
          status TEXT DEFAULT 'Проведено', note TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_fin_created ON financial_transactions(created_at);
        """)

def money(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def new_order_number():
    return "PM-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()

def send_supplier_order_api(sup, items, settings):
    """Generic JSON order transport. Supplier-specific adapters can use this endpoint without changing the order core."""
    url=str(sup.get("order_api_url") or "").strip()
    if not url: raise ValueError("У поставщика не указан URL API заказа")
    cred=sup.get("credentials",{}) or {}; key=str(cred.get("key") or cred.get("api_key") or "").strip()
    payload={"order_number": new_order_number(), "company": settings.get("company_name") or "Нет данных", "city": settings.get("city") or "Нет данных", "items": []}
    for x in items:
        payload["items"].append({"article":x.get("article") or "Нет данных","brand":x.get("brand") or "Нет данных","name":x.get("name") or "Нет данных","quantity":max(1,int(x.get("qty",1) or 1)),"price":parse_price(x.get("price"))})
    data=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    headers={"Content-Type":"application/json","Accept":"application/json"}
    mode=str(sup.get("order_api_auth") or "bearer").lower()
    if key:
        if mode=="x-api-key": headers["X-API-Key"]=key
        else: headers["Authorization"]="Bearer "+key
    req=Request(url,data=data,headers=headers,method=str(sup.get("order_api_method") or "POST").upper())
    try:
        with urlopen(req,timeout=30) as r:
            raw=r.read().decode("utf-8","replace"); code=getattr(r,"status",200)
    except HTTPError as e:
        raw=e.read().decode("utf-8","replace")[:2000]; raise RuntimeError(f"API HTTP {e.code}: {raw or 'Нет данных'}")
    except Exception as e: raise RuntimeError(f"API: {e}")
    ext=""
    try:
        obj=json.loads(raw); ext=str(obj.get("order_id") or obj.get("order_number") or obj.get("id") or "")
    except Exception: obj={}
    if code < 200 or code >= 300: raise RuntimeError(f"API HTTP {code}: {raw[:500]}")
    return ext or "Нет данных"

def create_order(items, sup, channel, customer_id=""):
    oid, now = str(uuid.uuid4()), datetime.now().isoformat(timespec="seconds")
    subtotal = sum((money(parse_price(x.get("price"))) * max(1, int(x.get("qty",1) or 1))) for x in items)
    number = new_order_number()
    with db_conn() as c:
        c.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (oid, number, customer_id, (active_vehicle() or {}).get("id", ""), sup.get("id",""), sup.get("name","Нет данных"), channel, "Новый", "Не оплачено", "", subtotal, 0, subtotal, now, now, ""))
        for x in items:
            c.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), oid, x.get("article") or "Нет данных", x.get("brand") or "Нет данных", x.get("name") or "Нет данных", max(1,int(x.get("qty",1) or 1)), money(parse_price(x.get("price"))), money(parse_price(x.get("cost"))), x.get("term") or "Нет данных", x.get("supplier_article") or ""))
        c.execute("INSERT INTO order_events(order_id,status,message,created_at) VALUES(?,?,?,?)", (oid,"Новый","Заказ создан",now))
    return oid, number

def order_event(oid, status, message=""):
    now=datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?",(status,now,oid))
        c.execute("INSERT INTO order_events(order_id,status,message,created_at) VALUES(?,?,?,?)",(oid,status,message,now))

def record_finance(order_id="", customer_id="", supplier_id="", kind="", category="", amount=0, direction="in", note=""):
    tid=str(uuid.uuid4()); now=datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("INSERT INTO financial_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?)",(tid,order_id,customer_id,supplier_id,kind,category,float(amount),direction,"Проведено",note,now))
    return tid

def list_orders(status="", supplier_id="", q=""):
    sql="SELECT * FROM orders WHERE 1=1"; args=[]
    if status: sql+=" AND status=?"; args.append(status)
    if supplier_id: sql+=" AND supplier_id=?"; args.append(supplier_id)
    if q: sql+=" AND (number LIKE ? OR supplier_name LIKE ? OR external_no LIKE ?)"; args += [f"%{q}%"]*3
    sql+=" ORDER BY created_at DESC LIMIT 500"
    with db_conn() as c: return c.execute(sql,args).fetchall()

def order_detail(oid):
    with db_conn() as c:
        o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        items=c.execute("SELECT * FROM order_items WHERE order_id=?",(oid,)).fetchall()
        events=c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id DESC",(oid,)).fetchall()
    return o,items,events

def finance_summary():
    with db_conn() as c:
        ins=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE direction='in' AND status='Проведено'").fetchone()[0]
        outs=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE direction='out' AND status='Проведено'").fetchone()[0]
        revenue=c.execute("SELECT COALESCE(SUM(subtotal),0) FROM orders WHERE status NOT IN ('Отменён','Ошибка отправки')").fetchone()[0]
        cost=c.execute("SELECT COALESCE(SUM(cost),0) FROM orders WHERE status NOT IN ('Отменён','Ошибка отправки')").fetchone()[0]
        receiv=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Начисление клиенту' AND direction='in'").fetchone()[0]
        paidcust=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Оплата клиента' AND direction='in'").fetchone()[0]
        payable=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Начисление поставщику' AND direction='out'").fetchone()[0]
        paidsup=c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Оплата поставщику' AND direction='out'").fetchone()[0]
        # Платежи поставщикам отражают движение денег, а не дополнительный расход:
        # себестоимость уже учитывается отдельно из заказов. Поэтому для чистой
        # прибыли берём только прочие проведённые расходы, чтобы не задвоить закупку.
        other_expenses=c.execute("""SELECT COALESCE(SUM(amount),0) FROM financial_transactions
            WHERE direction='out' AND status='Проведено'
              AND kind NOT IN ('Оплата поставщику', 'Начисление поставщику')""").fetchone()[0]
    gross_profit = revenue - cost
    net_profit = gross_profit - other_expenses
    return dict(income=ins,expense=outs,cash=ins-outs,revenue=revenue,cost=cost,
                profit=gross_profit,other_expenses=other_expenses,net_profit=net_profit,
                receivable=max(0,receiv-paidcust),payable=max(0,payable-paidsup))

def create_pdf_like_html(oid):
    o,items,events=order_detail(oid)
    if not o: return None
    rows=''.join(f"<tr><td>{html.escape(x['article'] or 'Нет данных')}</td><td>{html.escape(x['brand'] or 'Нет данных')}</td><td>{html.escape(x['name'] or 'Нет данных')}</td><td>{x['qty']}</td><td>{fmt_price(x['price']) if x['price'] is not None else 'Нет данных'}</td></tr>" for x in items)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{o['number']}</title><style>body{{font-family:Arial;padding:30px}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:8px;text-align:left}}@media print{{button{{display:none}}}}</style></head><body><button onclick='print()'>Печать / Сохранить PDF</button><h1>Заказ {html.escape(o['number'])}</h1><p>Поставщик: {html.escape(o['supplier_name'] or 'Нет данных')}<br>Статус: {html.escape(o['status'])}</p><table><tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th></tr>{rows}</table><h3>Итого: {fmt_price(o['subtotal'])} ₽</h3></body></html>"""

class H(BaseHTTPRequestHandler):
    server_version = f"PartsManager/{VERSION}"
    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)
    def out(self, c, code=200):
        b = c.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)
    def red(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            logging.exception("GET error")
            self.out(layout("Ошибка", f"<div class='card err'>Внутренняя ошибка: {esc(e)}</div>"), 500)

    def _do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)

        if path == "/":
            v = active_vehicle()
            vh = (f"<div class='ok'><b>Автомобиль:</b> {esc(v.get('make'))} {esc(v.get('model'))}, "
                  f"{esc(v.get('year'))} · VIN <span class='vin'>{esc(v.get('vin'))}</span></div>"
                  if v else "<div class='note'>Автомобиль не выбран. Для точного подбора добавьте VIN.</div>")
            article = q.get("part", [""])[0].strip()
            min_p = q.get("min_price", [""])[0]
            max_p = q.get("max_price", [""])[0]
            max_d = q.get("max_days", [""])[0]
            in_stock = q.get("in_stock", [""])[0] == "1"
            cross_sort = q.get("cross_sort", ["price"])[0]
            sort_mode = q.get("sort", ["smart"])[0]
            if sort_mode not in ("smart", "price", "delivery", "stock"):
                sort_mode = "smart"
            if cross_sort not in ("price", "delivery", "standard"):
                cross_sort = "price"
            show_added = q.get("added", [""])[0] == "1"
            _current_article[0] = article
            _current_filters.clear()
            _current_filters.update({"min_price": min_p, "max_price": max_p, "max_days": max_d,
                                     "in_stock_only": in_stock})
            errors_html = ""
            results_html = ""
            if article:
                filters = {"min_price": parse_price(min_p), "max_price": parse_price(max_p),
                           "max_days": int(max_d) if max_d.isdigit() else None,
                           "in_stock_only": in_stock}
                main, crosses, errors = collect_offers(article, filters, sort_mode="price_delivery")
                cart_by_supplier = cart_index(); cart_by_article = cart_index_by_article()
                results_html = render_grouped_results(main, crosses, cart_by_supplier,
                                                       cart_by_article, sort_mode=sort_mode, cross_sort_mode=cross_sort,
                                                       requested_article=article)
                # Ошибки отдельных поставщиков не мешают проценке и не показываются пользователю.
                # Поставщик без ответа просто не даёт офферов в текущем запросе.
            else:
                results_html = "<div class='card'><div class='muted'>Введите артикул и нажмите «Проценить».</div></div>"
            flash = ""
            if show_added:
                flash = ("<div class='flash'>✓ Товар добавлен в корзину. "
                         "<a href='/cart'>Открыть корзину →</a></div>")
            body = f"""{flash}
<div class="hero">
<div class="card"><h1>Проценка запчасти</h1>
<div class="muted">Артикул, VIN и текстовый запрос — в одном месте.</div>{vh}
<form method="get" action="/">
<div class="grid" style="margin-top:15px">
<div class="field"><label>Артикул</label>
<input name="part" value="{esc(article)}" placeholder="Например, LC-1030"></div></div>
<div class="toolbar"><button class="green">🔎 Проценить</button>
<a class="btn secondary" href="/vehicle">Выбрать VIN</a>
<a class="btn orange" href="/smart">🤖 Умный подбор</a></div>
<h2 style="margin-top:20px">Фильтры</h2>
<div class="grid">
<div class="field"><label>Цена от, ₽</label><input type="number" min="0" name="min_price" value="{esc(min_p)}"></div>
<div class="field"><label>Цена до, ₽</label><input type="number" min="0" name="max_price" value="{esc(max_p)}"></div>
<div class="field"><label>Срок до, дней</label><input type="number" min="0" name="max_days" value="{esc(max_d)}"></div>
<div class="field"><label>Наличие</label><select name="in_stock">
<option value="">Любое</option>
<option value="1" {'selected' if in_stock else ''}>Только в наличии</option></select></div></div>
<div class="toolbar"><button>Применить</button><a class="btn secondary" href="/">Сбросить</a></div>
</form></div>
<div class="card"><h2>Быстрый доступ</h2><div class="quick">
<a class="btn" href="/vehicle">🚗 VIN</a>
<a class="btn" href="/suppliers">🏪 Поставщики</a>
<a class="btn" href="/cart">🛒 Корзина</a></div></div></div>
<div class="card"><h2>Результаты</h2>
{results_html}
{errors_html}</div>"""
            self.out(layout("Проценка", body))
            return

        if path == "/expand":
            article = q.get("article", [""])[0].strip()
            group = q.get("group", ["main"])[0]
            if not article:
                self.red("/"); return
            filters = {"min_price": None, "max_price": None, "max_days": None, "in_stock_only": False}
            main, crosses, errors = collect_offers(article, filters, sort_mode="price_delivery")
            cart_by_supplier = cart_index(); cart_by_article = cart_index_by_article()
            if group == "main":
                offers = main; title = f"Все предложения: {article}"
            else:
                offers = [o for o in crosses
                          if normalize_article(o.get("article")) == normalize_article(article)]
                title = f"Все предложения: {article} (аналог)"
            offers.sort(key=lambda o: o["price"])
            rows = render_offer_rows(offers, cart_by_supplier, cart_by_article)
            body = f"""<div class="card"><div class="title"><div><h1>{esc(title)}</h1>
<div class="muted">Найдено: {len(offers)}</div></div>
<a class="btn secondary" href="/?part={quote(article)}">← К результатам</a></div>
<table><tr><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{rows or "<tr><td colspan='6' class='muted'>Пусто</td></tr>"}</table></div>"""
            self.out(layout("Все предложения", body))
            return

        if path == "/smart":
            v = active_vehicle()
            vh = (f"<div class='ok'><b>Автомобиль:</b> {esc(v.get('make'))} {esc(v.get('model'))}, "
                  f"{esc(v.get('year'))} · VIN <span class='vin'>{esc(v.get('vin'))}</span></div>"
                  if v else "<div class='note'>Можно добавить VIN в разделе «VIN / Авто».</div>")
            s = read("settings")
            body = f"""<div class='card'><div class='title'><div><h1>🤖 Умный подбор</h1>
<div class='muted'>Опишите задачу обычными словами — программа сама разберёт её.</div></div>
<a class='btn secondary' href='/'>← Проценка</a></div>{vh}
<form method='post' action='/smart_request'>
<div class='field' style='margin-top:15px'><label>Артикул (если знаете)</label>
<input name='article' value='{esc(s.get("last_smart_article"))}' placeholder='Оставьте пустым, если не знаете'></div>
<div class='field' style='margin-top:12px'><label>Что нужно подобрать?</label>
<textarea name='request' placeholder='Например: передние тормозные колодки Brembo, до 10000 ₽' required>{esc(s.get('last_smart_request'))}</textarea></div>
<div class='grid' style='margin-top:15px'>
<div class='field'><label>Макс. цена, ₽</label><input name='max_price' type='number' min='0' value='{esc(s.get('last_smart_max_price'))}'></div>
<div class='field'><label>Макс. срок, дней</label><input name='max_days' type='number' min='0' value='{esc(s.get('last_smart_max_days'))}'></div>
</div>
<div class='toolbar'><button class='green'>🤖 Подобрать</button></div></form></div>"""
            self.out(layout("Умный подбор", body))
            return

        if path == "/vehicle":
            vs = read("vehicles")
            av = active_vehicle()
            av_id = av.get("id") if av else None
            rows = ""
            for v in reversed(vs):
                mark = " <span class='badge'>выбран</span>" if v["id"] == av_id else ""
                warn = ""
                if v.get("partial"):
                    warn = " <span class='badge' style='background:#fef3c7;color:#92400e'>проверить</span>"
                rows += (
                    f"<tr><td class='vin'>{esc(v['vin'])}</td>"
                    f"<td>{esc(v.get('make'))} {esc(v.get('model'))}{mark}{warn}</td>"
                    f"<td>{esc(v.get('year'))}</td>"
                    f"<td class='row-actions'>"
                    f"<a class='btn secondary' href='/select_vehicle?id={esc(v['id'])}'>Выбрать</a> "
                    f"<form method='post' action='/delete_vehicle' style='display:inline'>"
                    f"<input type='hidden' name='id' value='{esc(v['id'])}'>"
                    f"<button class='red'>Удалить</button></form></td></tr>")
            body = f"""<div class="card"><div class="title"><div><h1>🚗 VIN и автомобили</h1>
<div class="muted">Введите VIN — программа автоматически распознает автомобиль.</div></div>
<a class="btn secondary" href="/">← Проценка</a></div>
<form method="post" action="/save_vehicle_vin">
<div class="grid">
<div class="field"><label>VIN *</label>
<input class="vin" name="vin" minlength="17" maxlength="17" required placeholder="17 символов"></div>
</div>
<div class="toolbar"><button class="green">🔍 Распознать и сохранить</button></div></form></div>
<div class="card"><h2>Или ввести вручную</h2>
<form method="post" action="/save_vehicle">
<div class="grid">
<div class="field"><label>VIN *</label><input class="vin" name="vin" minlength="17" maxlength="17" required></div>
<div class="field"><label>Марка</label><input name="make"></div>
<div class="field"><label>Модель</label><input name="model"></div>
<div class="field"><label>Год</label><input name="year"></div>
<div class="field"><label>Двигатель</label><input name="engine"></div>
<div class="field"><label>Комментарий</label><input name="notes"></div></div>
<div class="toolbar"><button class="secondary">Сохранить вручную</button></div></form></div>
<div class="card"><h2>Сохранённые автомобили</h2>
<table><tr><th>VIN</th><th>Автомобиль</th><th>Год</th><th></th></tr>
{rows or "<tr><td colspan='4'>Нет сохранённых автомобилей</td></tr>"}</table></div>"""
            self.out(layout("VIN / Автомобиль", body))
            return

        if path == "/vehicle_detail":
            vid = q.get("id", [""])[0]
            v = vehicle(vid)
            if not v:
                self.out(layout("Ошибка", "<div class='card err'>Автомобиль не найден.</div>"), 404); return
            fields = [("VIN", v.get("vin")), ("Марка", v.get("make")), ("Модель", v.get("model")),
                      ("Год", v.get("year")), ("Двигатель", v.get("engine")),
                      ("Кузов", v.get("body_class")), ("Цилиндры", v.get("engine_cylinders")),
                      ("Мощность, HP", v.get("engine_hp")), ("Объём, л", v.get("displacement_l")),
                      ("Топливо", v.get("fuel_type")), ("КПП", v.get("transmission")),
                      ("Привод", v.get("drive_type")), ("Производитель", v.get("manufacturer")),
                      ("Страна сборки", v.get("plant_country"))]
            rows = "".join(f"<tr><td><b>{esc(k)}</b></td><td>{esc(val or '—')}</td></tr>"
                           for k, val in fields if val)
            warn = ""
            if v.get("partial"):
                warn = "<div class='warn'>VIN распознан частично.</div>"
            body = f"""<div class="card"><div class="title"><div><h1>{esc(v.get('make'))} {esc(v.get('model'))} {esc(v.get('year'))}</h1>
<div class="muted">VIN <span class="vin">{esc(v.get('vin'))}</span></div></div>
<a class="btn secondary" href="/vehicle">← Назад</a></div>
{warn}<table>{rows}</table>
<div class="toolbar"><form method="post" action="/select_vehicle"><input type="hidden" name="id" value="{esc(v['id'])}"><button class="green">Сделать активным</button></form></div></div>"""
            self.out(layout("Автомобиль", body)); return

        if path == "/suppliers":
            ss = read("suppliers")
            rows = ""
            for s in ss:
                cfg = "✓ проверен" if s.get("last_test", {}).get("ok") else ("⚠ не проверен" if s.get("credentials") else "—")
                rows += (
                    f"<tr><td><b>{esc(s.get('name'))}</b></td>"
                    f"<td><span class='badge'>{esc(s.get('template'))}</span></td>"
                    f"<td>{cfg}</td><td class='row-actions'>"
                    f"<a class='btn secondary' href='/supplier?id={esc(s['id'])}'>Настроить</a> "
                    f"<form method='post' action='/delete_supplier' style='display:inline'>"
                    f"<input type='hidden' name='id' value='{esc(s['id'])}'>"
                    f"<button class='red'>Удалить</button></form></td></tr>")
            body = f"""<div class="card"><div class="title"><div><h1>🏪 Поставщики</h1>
<div class="muted">Добавляйте любого поставщика самостоятельно.</div></div>
<a class="btn green" href="/wizard1">+ Добавить поставщика</a></div></div>
<div class="card"><table><tr><th>Поставщик</th><th>Тип API</th><th>Состояние</th><th></th></tr>
{rows or "<tr><td colspan='4'>Поставщиков нет. Нажмите «+ Добавить поставщика».</td></tr>"}</table></div>"""
            self.out(layout("Поставщики", body)); return

        if path == "/order_status":
            oid=d.get("id",[""])[0]; status=d.get("status",["Новый"])[0]
            if status not in ORDER_STATUSES: status="Новый"
            order_event(oid,status,d.get("message",[""])[0] or "Ручное изменение статуса")
            self.red("/order?id="+quote(oid)); return

        if path == "/finance_add":
            amount=money(d.get("amount",["0"])[0]); direction=d.get("direction",["in"])[0]
            if amount>0: record_finance(kind=d.get("kind",["Движение"])[0],category=d.get("category",["Прочее"])[0],amount=amount,direction=direction,note=d.get("note",[""])[0])
            self.red("/finance"); return

        if path == "/customer_add":
            name=d.get("name",[""])[0].strip()
            if name:
                with db_conn() as c: c.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),name,d.get("company",[""])[0],d.get("phone",[""])[0],d.get("email",[""])[0],d.get("city",[""])[0],d.get("notes",[""])[0],datetime.now().isoformat(timespec="seconds")))
            self.red("/customers"); return

        if path == "/wizard1":
            prefill_name = q.get("name", [""])[0]
            body = f"""<div class="card"><div class="title"><div><h1>Добавить поставщика</h1>
<div class="muted">Шаг 1 из 3</div></div>
<a class="btn secondary" href="/suppliers">← Назад</a></div>
{steps_html(1)}
<form method="post" action="/wizard1">
<div class="grid">
<div class="field"><label>Название поставщика *</label>
<input name="name" value="{esc(prefill_name)}" placeholder="Например, AVD или Рога и Копыта" required autofocus>
<small>Как вы будете его называть в списке. Можно любое.</small></div>
</div>
<div class="toolbar"><button class="green">Далее →</button>
<a class="btn secondary" href="/suppliers">Отмена</a></div>
</form></div>"""
            self.out(layout("Добавить поставщика", body)); return

        if path == "/wizard2":
            name = q.get("name", [""])[0]
            if not name:
                self.red("/wizard1"); return
            tiles = ""
            for k, v in API_TYPES.items():
                checked = "checked" if k == "avd" else ""
                tiles += f"""<label class="tile">
<input type="radio" name="tpl" value="{esc(k)}" style="width:auto;margin-right:10px" {checked}>
<span class="nm">{esc(v['name'])}</span>
<div class="hint">{esc(v['hint'])}</div>
</label>"""
            body = f"""<div class="card"><div class="title"><div><h1>Добавить поставщика</h1>
<div class="muted">Шаг 2 из 3 — выберите тип API для «{esc(name)}»</div></div>
<a class="btn secondary" href="/suppliers">← Назад</a></div>
{steps_html(2)}
<form method="post" action="/wizard2">
<input type="hidden" name="name" value="{esc(name)}">
{tiles}
<div class="toolbar"><a class="btn secondary" href="/wizard1?name={quote(name)}">← Назад</a>
<button class="green">Далее →</button></div>
</form></div>"""
            self.out(layout("Добавить поставщика", body)); return

        if path == "/wizard3":
            name = q.get("name", [""])[0]
            tpl = q.get("tpl", [""])[0]
            if not name or tpl not in API_TYPES:
                self.red("/wizard1"); return
            api = API_TYPES[tpl]
            fields_html = ""
            for spec in api["fields"]:
                key, label, hint, typ = spec[0], spec[1], spec[2], spec[3]
                req = spec[4] if len(spec) > 4 else False
                req_attr = "required" if req else ""
                star = " *" if req else ""
                default_val = ""
                if tpl == "avd" and key == "endpoint":
                    default_val = api.get("default_endpoint", "")
                if typ == "select":
                    fields_html += f"""<div class='field'><label>{esc(label)}{star}</label>
<select name='{esc(key)}'>
<option value='url'>В URL (параметром)</option>
<option value='header'>В заголовке Authorization</option>
</select><small>{esc(hint)}</small></div>"""
                else:
                    fields_html += f"""<div class='field'><label>{esc(label)}{star}</label>
<input type='{esc(typ)}' name='{esc(key)}' value='{esc(default_val)}' {req_attr}>
<small>{esc(hint)}</small></div>"""
            body = f"""<div class="card"><div class="title"><div><h1>Добавить поставщика</h1>
<div class="muted">Шаг 3 из 3 — данные доступа для «{esc(name)}» ({esc(api['name'])})</div></div>
<a class="btn secondary" href="/suppliers">← Назад</a></div>
{steps_html(3)}
<form method="post" action="/wizard_save_test">
<input type="hidden" name="name" value="{esc(name)}">
<input type="hidden" name="tpl" value="{esc(tpl)}">
<div class="grid">{fields_html}</div>
<div class="toolbar">
<a class="btn secondary" href="/wizard2?name={quote(name)}">← Назад</a>
<button class="green" type="submit">Сохранить и проверить</button>
{("<button class=\"orange\" type=\"submit\" name=\"after_save\" value=\"delivery\">🚚 Проверить доставку</button>" if tpl == "rossko" else "")}
</div>
</form></div>"""
            self.out(layout("Добавить поставщика", body)); return

        if path == "/supplier":
            s = supplier(q.get("id", [""])[0])
            if not s:
                self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
            self.out(layout("Настройки поставщика", self._supplier_form(s))); return

        if path == "/orders":
            status=q.get("status",[""])[0]; supid=q.get("supplier",[""])[0]; search=q.get("q",[""])[0]
            rows=[]
            for o in list_orders(status,supid,search):
                rows.append(f"<tr><td><a href='/order?id={quote(o['id'])}'>{esc(o['number'])}</a></td><td>{esc(o['supplier_name'] or 'Нет данных')}</td><td>{esc(o['channel'] or 'Нет данных')}</td><td>{esc(o['status'])}</td><td>{esc(o['payment_status'])}</td><td>{fmt_price(o['subtotal'])} ₽</td><td>{esc(o['created_at'])}</td></tr>")
            opts=''.join(f"<option {'selected' if status==x else ''}>{esc(x)}</option>" for x in ORDER_STATUSES)
            body=f"""<div class='card'><div class='title'><div><h1>📋 Центр заказов</h1><div class='muted'>Все заказы, отправки и статусы в одном месте.</div></div><a class='btn secondary' href='/cart'>Корзина</a></div><form method='get'><div class='grid'><div class='field'><label>Поиск</label><input name='q' value='{esc(search)}' placeholder='Номер заказа / поставщик / внешний номер'></div><div class='field'><label>Статус</label><select name='status'><option value=''>Все</option>{opts}</select></div></div><button>Фильтровать</button></form></div><div class='card'><table><tr><th>№</th><th>Поставщик</th><th>Канал</th><th>Статус</th><th>Оплата</th><th>Сумма</th><th>Дата</th></tr>{''.join(rows) or '<tr><td colspan=7>Нет данных</td></tr>'}</table></div>"""
            self.out(layout("Центр заказов",body)); return

        if path == "/order":
            oid=q.get("id",[""])[0]; o,items,events=order_detail(oid)
            if not o: self.out(layout("Заказ","<div class='card err'>Заказ не найден.</div>"),404); return
            rows=''.join(f"<tr><td>{esc(x['article'] or 'Нет данных')}</td><td>{esc(x['brand'] or 'Нет данных')}</td><td>{esc(x['name'] or 'Нет данных')}</td><td>{x['qty']}</td><td>{fmt_price(x['price']) if x['price'] is not None else 'Нет данных'} ₽</td></tr>" for x in items)
            ev=''.join(f"<tr><td>{esc(e['created_at'])}</td><td>{esc(e['status'])}</td><td>{esc(e['message'] or 'Нет данных')}</td></tr>" for e in events)
            opts=''.join(f"<option {'selected' if o['status']==x else ''}>{esc(x)}</option>" for x in ORDER_STATUSES)
            body=f"""<div class='card'><div class='title'><div><h1>Заказ {esc(o['number'])}</h1><div class='muted'>{esc(o['supplier_name'] or 'Нет данных')} · {esc(o['channel'] or 'Нет данных')}</div></div><a class='btn secondary' href='/orders'>← Заказы</a></div><div class='grid'><div><b>Статус:</b> {esc(o['status'])}</div><div><b>Оплата:</b> {esc(o['payment_status'])}</div><div><b>Внешний №:</b> {esc(o['external_no'] or 'Нет данных')}</div><div><b>Сумма:</b> {fmt_price(o['subtotal'])} ₽</div></div><form method='post' action='/order_status'><input type='hidden' name='id' value='{esc(oid)}'><select name='status'>{opts}</select> <input name='message' placeholder='Комментарий'><button>Изменить статус</button></form><div class='toolbar'><a class='btn' href='/order_print?id={quote(oid)}' target='_blank'>🖨 Печать / PDF</a></div></div><div class='card'><h2>Позиции</h2><table><tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th></tr>{rows}</table></div><div class='card'><h2>История</h2><table><tr><th>Дата</th><th>Статус</th><th>Событие</th></tr>{ev}</table></div>"""
            self.out(layout("Заказ "+o['number'],body)); return

        if path == "/order_print":
            oid=q.get("id",[""])[0]; doc=create_pdf_like_html(oid)
            if not doc: self.out("<h1>Заказ не найден</h1>",404); return
            self.out_raw(doc); return

        if path == "/finance":
            f=finance_summary()
            body=f"""<div class='card'><h1>💰 Финансы и взаиморасчёты</h1><div class='grid'>
<div class='tile'><b>Поступления</b><h2>{fmt_price(f['income'])} ₽</h2><div class='hint'>Фактически полученные деньги</div></div>
<div class='tile'><b>Выплаты</b><h2>{fmt_price(f['expense'])} ₽</h2><div class='hint'>Фактически выплаченные деньги</div></div>
<div class='tile'><b>Чистый денежный поток</b><h2>{fmt_price(f['cash'])} ₽</h2><div class='hint'>Поступления − выплаты</div></div>
<div class='tile'><b>Выручка</b><h2>{fmt_price(f['revenue'])} ₽</h2><div class='hint'>Продажи по заказам</div></div>
<div class='tile'><b>Себестоимость</b><h2>{fmt_price(f['cost'])} ₽</h2><div class='hint'>Стоимость закупки товаров</div></div>
<div class='tile'><b>Валовая прибыль</b><h2>{fmt_price(f['profit'])} ₽</h2><div class='hint'>Выручка − себестоимость</div></div>
<div class='tile'><b>Прочие расходы</b><h2>{fmt_price(f['other_expenses'])} ₽</h2><div class='hint'>Расходы сверх себестоимости</div></div>
<div class='tile'><b>Чистая прибыль</b><h2>{fmt_price(f['net_profit'])} ₽</h2><div class='hint'>После себестоимости и прочих расходов</div></div>
<div class='tile'><b>Дебиторская задолженность</b><h2>{fmt_price(f['receivable'])} ₽</h2><div class='hint'>Клиенты должны нам</div></div>
<div class='tile'><b>Кредиторская задолженность</b><h2>{fmt_price(f['payable'])} ₽</h2><div class='hint'>Мы должны поставщикам</div></div>
</div></div><div class='card'><h2>Добавить финансовую операцию</h2><form method='post' action='/finance_add'><div class='grid'><input name='kind' placeholder='Тип операции' required><input name='category' placeholder='Категория' required><input name='amount' type='number' step='0.01' min='0' placeholder='Сумма' required><select name='direction'><option value='in'>Поступление</option><option value='out'>Выплата</option></select><input name='note' placeholder='Комментарий'></div><button class='green'>Сохранить операцию</button></form></div>"""
            self.out(layout("Финансы",body)); return

        if path == "/customers":
            with db_conn() as c: cs=c.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()
            rows=''.join(f"<tr><td>{esc(x['name'])}</td><td>{esc(x['company'] or 'Нет данных')}</td><td>{esc(x['phone'] or 'Нет данных')}</td><td>{esc(x['email'] or 'Нет данных')}</td><td>{esc(x['city'] or 'Нет данных')}</td></tr>" for x in cs)
            body=f"""<div class='card'><h1>👥 Клиенты</h1><form method='post' action='/customer_add'><div class='grid'><input name='name' placeholder='Имя / название' required><input name='company' placeholder='Компания'><input name='phone' placeholder='Телефон'><input name='email' type='email' placeholder='E-mail'><input name='city' placeholder='Город'><input name='notes' placeholder='Заметки'></div><button class='green'>Добавить клиента</button></form></div><div class='card'><table><tr><th>Имя</th><th>Компания</th><th>Телефон</th><th>E-mail</th><th>Город</th></tr>{rows or '<tr><td colspan=5>Нет данных</td></tr>'}</table></div>"""
            self.out(layout("Клиенты",body)); return

        if path == "/cart":
            c = read("cart")
            rows = ""
            total = 0.0
            for x in c:
                price = parse_price(x.get("price"))
                qty = int(x.get("qty", 1) or 1)
                if price is not None:
                    total += price * qty
                rows += (
                    f"<tr><td>{esc(x.get('article'))}</td>"
                    f"<td>{esc(x.get('supplier_name'))}</td>"
                    f"<td>{esc(x.get('brand'))}</td>"
                    f"<td>{esc(x.get('name'))}</td>"
                    f"<td class='price'>{fmt_price(price) if price is not None else 'Нет данных'} ₽</td>"
                    f"<td><div class='qty-pm'>"
                    f"<form method='post' action='/cart_dec' style='display:inline'>"
                    f"<input type='hidden' name='id' value='{esc(x['id'])}'><button>−</button></form>"
                    f"<span class='num'>{esc(qty)}</span>"
                    f"<form method='post' action='/cart_inc' style='display:inline'>"
                    f"<input type='hidden' name='id' value='{esc(x['id'])}'><button>+</button></form>"
                    f"</div></td>"
                    f"<td>{esc(x.get('term') or 'Нет данных')}</td>"
                    f"<td><form method='post' action='/remove_cart' style='display:inline'>"
                    f"<input type='hidden' name='id' value='{esc(x['id'])}'>"
                    f"<button class='red'>Удалить</button></form></td></tr>")
            body = f"""<div class="card"><div class="title"><div><h1>🛒 Корзина</h1>
<div class="muted">Позиции для дальнейшего заказа.</div></div>
<a class="btn secondary" href="/">← К проценке</a></div>
<div class="toolbar">
<a class="btn orange" href="/cart_optimize">🎯 Оптимизировать корзину</a>
<form method='post' action='/send_orders' style='display:inline'><button class='green'>📧 Отправить e-mail</button></form><form method='post' action='/send_orders_api' style='display:inline'><button class='green'>🔌 Отправить по API</button></form>
<form method='post' action='/clear_cart' style='display:inline'><button class='red'>Очистить</button></form>
</div></div>
<div class="card"><table>
<tr><th>Артикул</th><th>Поставщик</th><th>Бренд</th><th>Деталь</th><th>Цена</th><th>Кол-во</th><th>Срок</th><th></th></tr>
{rows or "<tr><td colspan='8'>Корзина пуста.</td></tr>"}</table>
<div class='toolbar'><b>Итого: {fmt_price(total)} ₽</b></div></div>"""
            self.out(layout("Корзина", body)); return

        if path == "/cart_optimize":
            c = read("cart")
            if not c:
                self.out(layout("Оптимизация", "<div class='card err'>Корзина пуста.</div><a class='btn' href='/cart'>← Назад</a>")); return
            body = f"""<div class="card"><div class="title"><div><h1>🎯 Оптимизация корзины</h1>
<div class="muted">Программа перепроверит каждую позицию у всех поставщиков и предложит лучший вариант.</div></div>
<a class="btn secondary" href="/cart">← Назад</a></div>
<form method="post" action="/cart_optimize_result">
<h2>Стратегия</h2>
<div class="opt-radio">
<label><input type="radio" name="strategy" value="price">💰 Самая низкая цена</label>
<label><input type="radio" name="strategy" value="delivery">⚡ Самый быстрый срок</label>
<label><input type="radio" name="strategy" value="standard" checked>⚖ Стандарт (баланс)</label>
</div>
<h2 style="margin-top:20px">Аналоги</h2>
<div class="opt-radio">
<label><input type="radio" name="analogs" value="0" checked>🏷 Только выбранные бренды</label>
<label><input type="radio" name="analogs" value="1">🔄 Разрешить дешёвый/быстрый аналог</label>
</div>
<div class="toolbar"><button class="green">Оптимизировать</button><a class="btn secondary" href="/cart">Отмена</a></div>
</form></div>"""
            self.out(layout("Оптимизация корзины", body)); return

        if path == "/cart_optimize_apply":
            cart_new_path = os.path.join(DATA_DIR, "cart.new.json")
            if os.path.exists(cart_new_path):
                try:
                    with open(cart_new_path, "r", encoding="utf-8") as f:
                        new_cart = json.load(f)
                    backup_cart()
                    write("cart", new_cart)
                    os.remove(cart_new_path)
                    self.red("/cart"); return
                except Exception:
                    logging.exception("cart_optimize_apply error")
            self.out(layout("Оптимизация", "<div class='card err'>Нет данных для применения. Запустите оптимизацию заново.</div><a class='btn' href='/cart'>← Назад</a>")); return

        if path == "/settings":
            s = read("settings")
            body = f"""<div class="card"><h1>⚙ Настройки</h1>
<form method="post" action="/save_settings"><div class="grid">
<div class='field'><label>Название компании</label>
<input name='company_name' value='{esc(s.get("company_name"))}' placeholder="Например, ООО АвтоПлюс"></div>
<div class='field'><label>Город / регион доставки</label>
<input name='city' value='{esc(s.get("city"))}' placeholder="Москва"></div>
<div class='field'><label>SMTP-сервер</label>
<input name='smtp_host' value='{esc(s.get("smtp_host"))}' placeholder="smtp.example.com">
<small>Сервер исходящей почты вашей компании.</small></div>
<div class='field'><label>SMTP-порт</label>
<input name='smtp_port' type='number' value='{esc(s.get("smtp_port", 587))}' placeholder="587"></div>
<div class='field'><label>Шифрование</label>
<select name='smtp_security'>
<option value='starttls' {'selected' if s.get('smtp_security','starttls') == 'starttls' else ''}>STARTTLS — обычно порт 587</option>
<option value='ssl' {'selected' if s.get('smtp_security') == 'ssl' else ''}>SSL/TLS — обычно порт 465</option>
<option value='none' {'selected' if s.get('smtp_security') == 'none' else ''}>Без шифрования</option>
</select></div>
<div class='field'><label>Логин SMTP</label>
<input name='smtp_user' value='{esc(s.get("smtp_user"))}' placeholder="shop@example.com"></div>
<div class='field'><label>E-mail отправителя</label>
<input type='email' name='smtp_from_email' value='{esc(s.get("smtp_from_email", ""))}' placeholder="shop@example.com">
<small>Адрес, который будет указан в поле «От кого».</small></div>
<div class='field'><label>Пароль SMTP</label>
<input name='smtp_password' type='password' value='{esc(s.get("smtp_password"))}' placeholder="Пароль / пароль приложения"></div>
<div class='field'><label>Имя отправителя</label>
<input name='smtp_from_name' value='{esc(s.get("smtp_from_name", "Parts Manager"))}' placeholder="Автосервис / Parts Manager"></div>
</div><div class="toolbar"><button>Сохранить</button>
<a class='btn secondary' href='/ai_diagnose'>Проверить сервис подбора</a>
<a class='btn secondary' href='/about'>О программе</a></div></form></div>"""
            self.out(layout("Настройки", body)); return

        if path == "/ai_diagnose":
            code, body_raw = ai_diagnose()
            status = "ok" if code == 200 else "err"
            body = f"""<div class="card"><div class="title"><div><h1>Проверка сервиса подбора</h1>
<div class="muted">HTTP-код ответа: <b>{esc(code)}</b></div></div>
<a class="btn secondary" href="/settings">← Настройки</a></div>
<div class="{status}">{'Сервис отвечает нормально.' if code == 200 else 'Сервис вернул ошибку.'}</div>
<div class='xml-box'>{esc(body_raw[:8000])}</div></div>"""
            self.out(layout("Проверка подбора", body)); return

        if path == "/about":
            suppliers = read("suppliers"); vehicles = read("vehicles"); cart = read("cart")
            providers_list = "".join(
                f"<li>{esc(s.get('name','—'))} <span class='badge'>{esc(s.get('template',''))}</span></li>"
                for s in suppliers) or "<li>нет</li>"
            body = f"""<div class="card"><h1>О программе</h1>
<table>
<tr><td><b>Версия</b></td><td>{esc(VERSION)}</td></tr>
<tr><td><b>Поставщиков</b></td><td>{len(suppliers)}</td></tr>
<tr><td><b>Автомобилей в базе</b></td><td>{len(vehicles)}</td></tr>
<tr><td><b>Позиций в корзине</b></td><td>{len(cart)}</td></tr>
</table>
<h2 style="margin-top:20px">Подключённые поставщики</h2>
<ul>{providers_list}</ul></div>"""
            self.out(layout("О программе", body)); return

        self.out(layout("404", "<div class='card'><h1>404</h1><a class='btn' href='/'>На главную</a></div>"), 404)

    def _supplier_form(self, existing):
        s = existing
        tpl = s.get("template", "custom")
        if tpl not in API_TYPES:
            tpl = "custom"
        api = API_TYPES[tpl]
        cred = s.get("credentials", {}) or {}
        name_val = esc(s.get("name", ""))
        fields_html = f"""<div class='field'><label>Название поставщика</label>
<input name='name' value='{name_val}' required></div>"""
        fields_html += f"""<div class='field'><label>E-mail для заказов</label>
<input type='email' name='order_email' value='{esc(s.get("order_email", ""))}' placeholder='orders@example.com'>
<small>На этот адрес Parts Manager будет отправлять заказы из корзины.</small></div>
<div class='field'><label>URL API заказа</label><input name='order_api_url' value='{esc(s.get("order_api_url", ""))}' placeholder='https://supplier.example/api/orders'><small>Если не задан — API-заказ недоступен, можно использовать e-mail.</small></div>
<div class='field'><label>Авторизация API заказа</label><select name='order_api_auth'><option value='bearer' {'selected' if s.get('order_api_auth','bearer')=='bearer' else ''}>Bearer</option><option value='x-api-key' {'selected' if s.get('order_api_auth')=='x-api-key' else ''}>X-API-Key</option></select></div>
<div class='field'><label>API-ключ заказа</label><input type='password' name='order_api_key' value='{esc(s.get("order_api_key", ""))}' placeholder='Не показывать в интерфейсе'></div>
<div class='field'><label>Тип API</label>
<select name='template' disabled>
<option selected>{esc(api['name'])}</option>
</select>
<input type='hidden' name='template' value='{esc(tpl)}'>
<small>Чтобы сменить тип — удалите поставщика и добавьте заново.</small></div>"""
        for spec in api["fields"]:
            key, label, hint, typ = spec[0], spec[1], spec[2], spec[3]
            req = spec[4] if len(spec) > 4 else False
            val = esc(cred.get(key, ""))
            req_attr = "required" if req else ""
            star = " *" if req else ""
            if typ == "select":
                fields_html += f"""<div class='field'><label>{esc(label)}{star}</label>
<select name='{esc(key)}'>
<option value='url' {'selected' if cred.get(key) == 'url' else ''}>В URL (параметром)</option>
<option value='header' {'selected' if cred.get(key) == 'header' else ''}>В заголовке Authorization</option>
</select><small>{esc(hint)}</small></div>"""
            else:
                fields_html += f"""<div class='field'><label>{esc(label)}{star}</label>
<input type='{esc(typ)}' name='{esc(key)}' value='{val}' {req_attr}>
<small>{esc(hint)}</small></div>"""
        test_article = api.get("test_article", "") or "LC-1030"
        return f"""<div class="card">
<div class="title"><div><h1>{esc(s.get('name', ''))}</h1>
<div class="muted">Редактирование подключения</div></div>
<a class="btn secondary" href="/suppliers">← Назад</a></div>
<form method="post" action="/update_supplier">
<input type="hidden" name="id" value="{esc(s['id'])}">
<div class="grid">{fields_html}</div>
<div class="toolbar"><button class="green">Сохранить</button></div>
</form></div>
<div class="card"><h2>Проверка подключения</h2>
<p class="muted">Отправит тестовый запрос с артикулом <b>{esc(test_article)}</b>.</p>
<form method="post" action="/provider_test" style="display:inline">
<input type="hidden" name="id" value="{esc(s['id'])}">
<input type="hidden" name="article" value="{esc(test_article)}">
<button class="orange">🔍 Проверить</button>
</form></div>"""

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:
            logging.exception("POST error")
            self.out(layout("Ошибка", f"<div class='card err'>Внутренняя ошибка: {esc(e)}</div>"), 500)

    def _do_POST(self):
        u = urlparse(self.path)
        path = u.path
        d = postdata(self)

        if path == "/order_status":
            oid=d.get("id",[""])[0]; status=d.get("status",["Новый"])[0]
            if status not in ORDER_STATUSES: status="Новый"
            order_event(oid,status,d.get("message",[""])[0] or "Ручное изменение статуса")
            self.red("/order?id="+quote(oid)); return

        if path == "/finance_add":
            amount=money(d.get("amount",["0"])[0]); direction=d.get("direction",["in"])[0]
            if amount>0: record_finance(kind=d.get("kind",["Движение"])[0],category=d.get("category",["Прочее"])[0],amount=amount,direction=direction,note=d.get("note",[""])[0])
            self.red("/finance"); return

        if path == "/customer_add":
            name=d.get("name",[""])[0].strip()
            if name:
                with db_conn() as c: c.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),name,d.get("company",[""])[0],d.get("phone",[""])[0],d.get("email",[""])[0],d.get("city",[""])[0],d.get("notes",[""])[0],datetime.now().isoformat(timespec="seconds")))
            self.red("/customers"); return

        if path == "/wizard1":
            name = d.get("name", [""])[0].strip()
            if not name:
                self.red("/wizard1"); return
            self.red(f"/wizard2?name={quote(name)}"); return

        if path == "/wizard2":
            name = d.get("name", [""])[0].strip()
            tpl = d.get("tpl", [""])[0]
            if not name or tpl not in API_TYPES:
                self.red(f"/wizard2?name={quote(name)}"); return
            self.red(f"/wizard3?name={quote(name)}&tpl={quote(tpl)}"); return

        if path == "/wizard_save_test":
            name = d.get("name", [""])[0].strip()
            tpl = d.get("tpl", [""])[0]
            if not name or tpl not in API_TYPES:
                self.red("/wizard1"); return
            api = API_TYPES[tpl]
            cred = {}
            missing = []
            for spec in api["fields"]:
                key, label = spec[0], spec[1]
                req = spec[4] if len(spec) > 4 else False
                val = d.get(key, [""])[0].strip()
                if req and not val:
                    missing.append(label)
                cred[key] = val
            if missing:
                err = "Заполните обязательные поля: " + ", ".join(missing)
                body = f"""<div class="card"><h1>Не хватает данных</h1>
<div class="err">{esc(err)}</div>
<div class="toolbar">
<a class="btn orange" href="/wizard3?name={quote(name)}&tpl={quote(tpl)}">← Вернуться</a>
</div></div>"""
                self.out(layout("Добавить поставщика", body)); return
            new_id = str(uuid.uuid4())
            s = {"id": new_id, "name": name, "template": tpl, "credentials": cred,
                 "order_email": d.get("order_email", [""])[0].strip(),
                 "order_api_url": d.get("order_api_url", [""])[0].strip(),
                 "order_api_auth": d.get("order_api_auth", ["bearer"])[0],
                 "order_api_key": d.get("order_api_key", [""])[0].strip(),
                 "enabled": True, "created_at": datetime.now().isoformat(timespec="seconds"),
                 "last_test": None}
            test_article = api.get("test_article", "") or "LC-1030"
            try:
                if tpl == "rossko": main, crosses, err = rossko_search(s, test_article)
                elif tpl == "berg": main, crosses, err = berg_search(s, test_article)
                elif tpl == "zappro": main, crosses, err = zappro_search(s, test_article)
                elif tpl == "avd": main, crosses, err = avd_search(s, test_article)
                else: main, crosses, err = custom_search(s, test_article)
            except Exception as ex:
                main, crosses, err = [], [], f"ошибка: {ex}"
            if not err:
                s["last_test"] = {"ok": True, "at": datetime.now().isoformat(timespec="seconds"), "article": test_article}
                ss = read("suppliers"); ss.append(s); write("suppliers", ss)
            if not err and d.get("after_save", [""])[0] == "delivery" and tpl == "rossko":
                self._rossko_checkout({"id": [new_id]})
                return
            body = render_test_result(new_id, s, tpl, test_article, main, crosses, err)
            self.out(layout("Проверка поставщика", body)); return

        if path == "/delete_supplier":
            sid = d.get("id", [""])[0]
            write("suppliers", [x for x in read("suppliers") if x.get("id") != sid])
            self.red("/suppliers"); return
        if path == "/provider_test":
            self._provider_test(d); return
        if path == "/rossko_checkout_details":
            self._rossko_checkout(d); return
        if path == "/save_rossko_delivery":
            sid = d.get("id", [""])[0]
            ss = read("suppliers")
            for s in ss:
                if s.get("id") == sid:
                    s.setdefault("credentials", {})["delivery_id"] = d.get("delivery_id", [""])[0]
                    s["credentials"]["address_id"] = d.get("address_id", [""])[0]
            write("suppliers", ss)
            self.red("/supplier?id=" + quote(sid)); return

        if path == "/update_supplier":
            sid = d.get("id", [""])[0]
            ss = read("suppliers")
            for s in ss:
                if s.get("id") == sid:
                    new_tpl = d.get("template", [s.get("template", "custom")])[0]
                    if new_tpl not in API_TYPES: new_tpl = "custom"
                    s["template"] = new_tpl
                    s["name"] = d.get("name", [s.get("name", "")])[0].strip() or s.get("name", "")
                    s["order_email"] = d.get("order_email", [s.get("order_email", "")])[0].strip()
                    s["order_api_url"] = d.get("order_api_url", [s.get("order_api_url", "")])[0].strip()
                    s["order_api_auth"] = d.get("order_api_auth", [s.get("order_api_auth", "bearer")])[0]
                    s["order_api_key"] = d.get("order_api_key", [s.get("order_api_key", "")])[0].strip()
                    api = API_TYPES[new_tpl]
                    cred = s.setdefault("credentials", {})
                    for spec in api["fields"]:
                        key = spec[0]
                        if key in d:
                            cred[key] = d[key][0]
            write("suppliers", ss)
            self.red("/supplier?id=" + quote(sid)); return

        if path == "/smart_request":
            req = d.get("request", [""])[0].strip()
            art = d.get("article", [""])[0].strip()
            mp = d.get("max_price", [""])[0]
            md = d.get("max_days", [""])[0]
            st = read("settings")
            st["last_smart_request"] = req; st["last_smart_article"] = art
            st["last_smart_max_price"] = mp; st["last_smart_max_days"] = md
            write("settings", st)
            current_vehicle = active_vehicle()
            try:
                parsed = ai_parse_request(req, vehicle=current_vehicle) if req else {
                    "article": None, "brand": None, "name": "", "keywords": [],
                    "max_price": None, "max_days": None, "quantity": 1}
            except Exception as e:
                self.out(layout("Ошибка подбора",
                    f"<div class='card'><div class='err'><b>Не удалось разобрать запрос:</b><br>{esc(e)}</div>"
                    f"<div class='toolbar'><a class='btn' href='/smart'>← Назад</a>"
                    f"<a class='btn secondary' href='/ai_diagnose'>Проверить сервис подбора</a></div></div>"))
                return
            if art: parsed["article"] = art
            filters = {"min_price": None,
                       "max_price": parse_price(mp) or parsed.get("max_price"),
                       "max_days": int(md) if md.isdigit() else parsed.get("max_days"),
                       "in_stock_only": False}
            search_for = parsed.get("article") or parsed.get("name") or req
            main, crosses, errors = collect_offers(search_for, filters, ai_hint=parsed)
            cart_by_supplier = cart_index(); cart_by_article = cart_index_by_article()
            results_html = render_grouped_results(main, crosses, cart_by_supplier,
                                                   cart_by_article, sort_mode="smart", cross_sort_mode="price",
                                                   requested_article=search_for, requested_brand=parsed.get("brand") or "")
            err_block = ""
            # Ошибки отдельных поставщиков игнорируются: в результат попадают только реальные офферы.
            vehicle_line = ""
            if current_vehicle:
                vehicle_line = (f"<p><b>Автомобиль:</b> {esc(current_vehicle.get('make'))} "
                                f"{esc(current_vehicle.get('model'))}, {esc(current_vehicle.get('year'))} "
                                f"· VIN {esc(current_vehicle.get('vin'))}</p>")
            kw = ", ".join(parsed.get("keywords") or []) or "—"
            body = f"""<div class='card'><h1>Результат подбора</h1>
<div class='ok'>
<b>Артикул:</b> {esc(parsed.get('article') or '—')} ·
<b>Бренд:</b> {esc(parsed.get('brand') or '—')} ·
<b>Деталь:</b> {esc(parsed.get('name') or '—')}
</div>
{vehicle_line}<p><b>Ключевые слова:</b> {esc(kw)}</p></div>
{err_block}{results_html}
<div class='toolbar'><a class='btn secondary' href='/smart'>← Новый запрос</a></div>"""
            self.out(layout("Подбор", body)); return

        if path == "/cart_optimize_result":
            strategy = d.get("strategy", ["standard"])[0]
            analogs = d.get("analogs", ["0"])[0] == "1"
            new_cart, report, summary = optimize_cart(strategy=strategy, allow_analogs=analogs)
            # Записываем в папку приложения, а не cwd
            cart_new_path = os.path.join(DATA_DIR, "cart.new.json")
            with _lock:
                with open(cart_new_path, "w", encoding="utf-8") as f:
                    json.dump(new_cart, f, ensure_ascii=False, indent=2)
            backup_cart()
            diff = summary.get("diff", 0)
            diff_class = "saved" if diff > 0 else ("loss" if diff < 0 else "")
            diff_label = "Экономия" if diff > 0 else ("Прирост" if diff < 0 else "Без изменений")
            diff_abs = abs(diff)
            rows = ""
            for r in report:
                old_s = esc(r.get("old_supplier", "") or "—")
                new_s = esc(r.get("new_supplier", "") or "—")
                old_d = r.get("old_days"); new_d = r.get("new_days")
                old_d_txt = f"{old_d} дн." if old_d is not None else "—"
                new_d_txt = f"{new_d} дн." if new_d is not None else "—"
                money_diff = (r.get("old_price") or 0) - (r.get("new_price") or 0)
                money_cls = "saved" if money_diff > 0 else ("loss" if money_diff < 0 else "")
                cross_mark = " <span class='badge violet'>аналог</span>" if r.get("is_cross") else ""
                forced = r.get("is_forced", False)
                forced_mark = ""
                row_class = ""
                if forced:
                    reason = esc(r.get("reason", "Позиция недоступна"))
                    forced_mark = f' <span class="forced-icon" title="{reason}">⚠</span>'
                    row_class = " class='forced-row'"
                rows += f"""<tr{row_class}>
<td><b>{esc(r.get('article'))}</b><div class="warehouse">{esc(r.get('brand'))}</div></td>
<td>{old_s} · {fmt_price(r.get('old_price') or 0)} ₽ · {esc(old_d_txt)}</td>
<td>{new_s} · {fmt_price(r.get('new_price') or 0)} ₽ · {esc(new_d_txt)}{cross_mark}{forced_mark}</td>
<td class="{money_cls}">{'+' if money_diff > 0 else ''}{fmt_price(money_diff)} ₽</td>
</tr>"""
            body = f"""<div class="card"><div class="title"><div><h1>🎯 Результат оптимизации</h1>
<div class="muted">Стратегия: {esc(strategy)} · Аналоги: {'разрешены' if analogs else 'только выбранные бренды'}</div></div>
<a class="btn secondary" href="/cart">← Назад в корзину</a></div>
<table>
<tr><td><b>Было:</b></td><td>{fmt_price(summary.get('total_before', 0))} ₽</td></tr>
<tr><td><b>Стало:</b></td><td>{fmt_price(summary.get('total_after', 0))} ₽</td></tr>
<tr><td><b>{diff_label}:</b></td><td class="{diff_class}">{fmt_price(diff_abs)} ₽</td></tr>
<tr><td><b>Крайняя дата доставки:</b></td><td>{esc(summary.get('last_delivery_date', '—'))}</td></tr>
</table>
<div class="note" style="margin-top:10px">Значок <span class="forced-icon">⚠</span> означает, что старая позиция стала недоступна и её заменили вынужденно. Наведите курсор на значок, чтобы увидеть причину.</div>
</div>
<div class="card"><h2>Что изменится</h2>
<table>
<tr><th>Артикул / Бренд</th><th>Было</th><th>Станет</th><th>Разница</th></tr>
{rows or "<tr><td colspan='4' class='muted'>Ничего не найдено для замены</td></tr>"}
</table></div>
<div class="toolbar">
<form method="post" action="/cart_optimize_apply" style="display:inline"><button class="green">✓ Применить</button></form>
<a class="btn secondary" href="/cart">Отменить</a></div>"""
            self.out(layout("Оптимизация", body)); return

        if path == "/select_vehicle":
            vid = d.get("id", [""])[0]
            v = vehicle(vid)
            if not v:
                self.out(layout("Автомобиль", "<div class='card err'>Автомобиль не найден.</div>"), 404); return
            s = read("settings"); s["active_vehicle_id"] = vid; write("settings", s)
            self.red("/vehicle"); return

        if path == "/save_vehicle_vin":
            vin = d.get("vin", [""])[0].strip().upper()
            if len(vin) != 17:
                self.out(layout("Ошибка", "<div class='card err'>VIN должен быть 17 символов.</div><a class='btn' href='/vehicle'>← Назад</a>"), 400); return
            try:
                info, warning = decode_vin(vin)
            except Exception as e:
                self.out(layout("Ошибка VIN", f"<div class='card'><div class='err'>{esc(e)}</div><a class='btn' href='/vehicle'>← Назад</a></div>")); return
            v = {"id": str(uuid.uuid4()), "vin": vin,
                 "make": info.get("make") or "", "model": info.get("model") or "",
                 "year": info.get("year") or "", "engine": info.get("displacement_l") or "",
                 "body_class": info.get("body_class") or "", "engine_cylinders": info.get("engine_cylinders") or "",
                 "engine_hp": info.get("engine_hp") or "", "displacement_l": info.get("displacement_l") or "",
                 "fuel_type": info.get("fuel_type") or "", "transmission": info.get("transmission") or "",
                 "drive_type": info.get("drive_type") or "", "manufacturer": info.get("manufacturer") or "",
                 "plant_country": info.get("plant_country") or "", "notes": "", "partial": bool(warning)}
            vs = read("vehicles"); vs.append(v); write("vehicles", vs)
            s = read("settings"); s["active_vehicle_id"] = v["id"]; write("settings", s)
            self.red("/vehicle_detail?id=" + quote(v["id"])); return

        if path == "/save_vehicle":
            vin = d.get("vin", [""])[0].strip().upper()
            if len(vin) != 17:
                self.out(layout("Ошибка", "<div class='card err'>VIN должен быть 17 символов.</div>"), 400); return
            v = {"id": str(uuid.uuid4()), "vin": vin,
                 "make": d.get("make", [""])[0], "model": d.get("model", [""])[0],
                 "year": d.get("year", [""])[0], "engine": d.get("engine", [""])[0],
                 "notes": d.get("notes", [""])[0]}
            vs = read("vehicles"); vs.append(v); write("vehicles", vs)
            s = read("settings"); s["active_vehicle_id"] = v["id"]; write("settings", s)
            self.red("/vehicle"); return

        if path == "/delete_vehicle":
            vid = d.get("id", [""])[0]
            write("vehicles", [x for x in read("vehicles") if x.get("id") != vid])
            s = read("settings")
            if s.get("active_vehicle_id") == vid:
                s["active_vehicle_id"] = ""; write("settings", s)
            self.red("/vehicle"); return

        if path == "/save_settings":
            s = read("settings")
            for k in ("company_name", "city", "smtp_host", "smtp_user", "smtp_password", "smtp_from_email", "smtp_from_name", "smtp_security"):
                if k in d: s[k] = d[k][0]
            if "smtp_port" in d:
                try: s["smtp_port"] = int(d["smtp_port"][0] or 587)
                except ValueError: s["smtp_port"] = 587
            write("settings", s); self.red("/settings"); return

        if path == "/add_cart":
            item = {"id": str(uuid.uuid4()), "supplier_id": d.get("supplier_id", [""])[0],
                    "supplier_name": "", "article": d.get("article", [""])[0],
                    "brand": d.get("brand", [""])[0], "name": d.get("name", [""])[0],
                    "price": d.get("price", [""])[0], "term": d.get("term", [""])[0],
                    "qty": max(1, int(d.get("qty", ["1"])[0] or 1))}
            sup = supplier(item["supplier_id"])
            if sup: item["supplier_name"] = sup.get("name", "")
            c = read("cart")
            found = False
            for x in c:
                if normalize_article(x.get("article")) == normalize_article(item["article"]) and \
                   (x.get("supplier_id") or "") == (item["supplier_id"] or ""):
                    x["qty"] = int(x.get("qty", 1)) + 1; found = True; break
            if not found: c.append(item)
            write("cart", c)
            back = d.get("back", [""])[0] or "/"
            # Разрешаем только локальные URL, чтобы параметр back не стал open-redirect.
            if not back.startswith("/") or back.startswith("//"):
                back = "/"
            sep = "&" if "?" in back else "?"
            self.red(back + sep + "added=1"); return

        if path == "/cart_inc":
            cid = d.get("id", [""])[0]
            c = read("cart")
            for x in c:
                if x.get("id") == cid:
                    x["qty"] = int(x.get("qty", 1) or 1) + 1; break
            write("cart", c); self.red("/cart"); return

        if path == "/cart_dec":
            cid = d.get("id", [""])[0]
            c = read("cart"); new_c = []
            for x in c:
                if x.get("id") == cid:
                    nq = int(x.get("qty", 1) or 1) - 1
                    if nq <= 0: continue
                    x["qty"] = nq
                new_c.append(x)
            write("cart", new_c); self.red("/cart"); return

        if path == "/remove_cart":
            cid = d.get("id", [""])[0]
            write("cart", [x for x in read("cart") if x.get("id") != cid])
            self.red("/cart"); return

        if path == "/send_orders":
            c = read("cart")
            if not c:
                self.out(layout("Отправка заказов", "<div class='card'><div class='warn'>Корзина пуста.</div><a class='btn' href='/cart'>← Вернуться в корзину</a></div>")); return
            groups = {}
            for item in c:
                sid = item.get("supplier_id") or ""
                groups.setdefault(sid, []).append(item)
            settings = read("settings")
            results = []
            for sid, items in groups.items():
                sup = supplier(sid)
                if not sup:
                    results.append((False, "Неизвестный поставщик", "Поставщик позиции не найден в настройках.")); continue
                oid, order_no = create_order(items, sup, "E-mail")
                order_event(oid, "Отправляется", "Начата отправка заказа по e-mail")
                try:
                    email_no = send_supplier_order(sup, items, settings)
                    order_event(oid, "Отправлен", f"E-mail отправлен на {sup.get('order_email') or 'Нет данных'}")
                    with db_conn() as cc: cc.execute("UPDATE orders SET external_no=? WHERE id=?",(email_no,oid))
                    results.append((True, sup.get("name") or "Поставщик", f"Заказ {order_no} отправлен на {sup.get('order_email') or 'Нет данных'}."))
                except Exception as ex:
                    order_event(oid, "Ошибка отправки", str(ex))
                    with db_conn() as cc: cc.execute("UPDATE orders SET error=? WHERE id=?",(str(ex),oid))
                    results.append((False, sup.get("name") or "Поставщик", str(ex)))
            cards = []
            for ok, name, message in results:
                cls = "ok" if ok else "err"
                icon = "✅" if ok else "❌"
                cards.append(f"<div class='{cls}'><b>{icon} {esc(name)}</b><br>{esc(message)}</div>")
            self.out(layout("Отправка заказов", f"<div class='card'><div class='title'><div><h1>📧 Отправка заказов</h1><div class='muted'>Для каждого поставщика из корзины сформировано отдельное письмо.</div></div><a class='btn secondary' href='/cart'>← Корзина</a></div>{''.join(cards)}</div>")); return

        if path == "/send_orders_api":
            c=read("cart")
            if not c: self.red("/cart"); return
            groups={}
            for item in c: groups.setdefault(item.get("supplier_id") or "",[]).append(item)
            settings=read("settings"); cards=[]
            for sid,items in groups.items():
                sup=supplier(sid)
                if not sup: cards.append("<div class='err'>Нет данных: поставщик не найден.</div>"); continue
                oid,order_no=create_order(items,sup,"API"); order_event(oid,"Отправляется","Начата отправка по API")
                try:
                    ext=send_supplier_order_api(sup,items,settings)
                    with db_conn() as cc: cc.execute("UPDATE orders SET external_no=? WHERE id=?",(ext,oid))
                    order_event(oid,"Отправлен",f"API принят. Внешний номер: {ext}")
                    cards.append(f"<div class='ok'><b>✅ {esc(sup.get('name') or 'Поставщик')}</b><br>Заказ {esc(order_no)} отправлен. Внешний номер: {esc(ext)}</div>")
                except Exception as ex:
                    order_event(oid,"Ошибка отправки",str(ex))
                    with db_conn() as cc: cc.execute("UPDATE orders SET error=? WHERE id=?",(str(ex),oid))
                    cards.append(f"<div class='err'><b>❌ {esc(sup.get('name') or 'Поставщик')}</b><br>{esc(ex)}</div>")
            self.out(layout("API-заказ",f"<div class='card'><h1>🔌 Отправка по API</h1>{''.join(cards)}<div class='toolbar'><a class='btn' href='/orders'>Открыть центр заказов</a><a class='btn secondary' href='/cart'>Корзина</a></div></div>")); return

        if path == "/clear_cart":
            write("cart", []); self.red("/cart"); return

        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        b = layout("404", "<div class='card'><h1>404</h1></div>").encode("utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _provider_test(self, d):
        sid = d.get("id", [""])[0]
        s = supplier(sid)
        if not s:
            self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
        tpl = s.get("template", "custom")
        if tpl not in API_TYPES: tpl = "custom"
        api = API_TYPES[tpl]
        article = d.get("article", [""])[0].strip() or api.get("test_article", "") or "LC-1030"
        try:
            if tpl == "rossko": main, crosses, err = rossko_search(s, article)
            elif tpl == "berg": main, crosses, err = berg_search(s, article)
            elif tpl == "zappro": main, crosses, err = zappro_search(s, article)
            elif tpl == "avd": main, crosses, err = avd_search(s, article)
            else: main, crosses, err = custom_search(s, article)
        except Exception as ex:
            main, crosses, err = [], [], f"ошибка: {ex}"
        body = render_test_result(sid, s, tpl, article, main, crosses, err)
        self.out(layout("Проверка поставщика", body))

    def _rossko_checkout(self, d):
        sid = d.get("id", [""])[0]
        s = supplier(sid)
        if not s:
            self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
        cred = s.get("credentials", {}) or {}
        key1, key2 = cred.get("key1", ""), cred.get("key2", "")
        if not key1 or not key2:
            self.out(layout("ROSSKO", f"<div class='card err'><b>Не хватает KEY1 или KEY2.</b></div>"
                                       f"<a class='btn' href='/supplier?id={esc(sid)}'>Назад</a>")); return
        endpoint = BUILTIN["rossko_checkout"]
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
                    'xmlns:ns="https://api.rossko.ru/">'
                    '<soap:Body><ns:GetCheckoutDetails>'
                    f'<ns:KEY1>{xml_escape(key1)}</ns:KEY1>'
                    f'<ns:KEY2>{xml_escape(key2)}</ns:KEY2>'
                    '</ns:GetCheckoutDetails></soap:Body></soap:Envelope>').encode("utf-8")
        ctx = ssl.create_default_context()
        req = Request(endpoint, data=envelope, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{endpoint}"',
            "User-Agent": f"PartsManager/{VERSION}"})
        raw = None; err = None
        try:
            with urlopen(req, timeout=60, context=ctx) as resp:
                raw = resp.read()
        except Exception as ex:
            err = ex
            if hasattr(ex, "read"):
                try: raw = ex.read()
                except Exception: raw = None
        if raw is None:
            self.out(layout("ROSSKO — сеть", f"<div class='card'><div class='err'>{esc(err)}</div>"
                                                f"<a class='btn secondary' href='/supplier?id={esc(sid)}'>Назад</a></div>")); return
        try: text = raw.decode("utf-8", "replace")
        except Exception: text = repr(raw)
        import xml.etree.ElementTree as ET
        try: root = ET.fromstring(raw)
        except ET.ParseError:
            self.out(layout("ROSSKO", f"<div class='card'><h1>Ответ (не XML)</h1>"
                                         f"<div class='xml-box'>{esc(text[:5000])}</div>"
                                         f"<a class='btn secondary' href='/supplier?id={esc(sid)}'>Назад</a></div>")); return
        texts = {}
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if el.text and el.text.strip(): texts.setdefault(tag, []).append(el.text.strip())
        if texts.get("success", ["false"])[0].strip().lower() not in ("true", "1", "yes"):
            msg = texts.get("message", ["ошибка"])[0]
            self.out(layout("ROSSKO", f"<div class='card'><div class='err'>{esc(msg)}</div>"
                                         f"<a class='btn secondary' href='/supplier?id={esc(sid)}'>Назад</a></div>")); return
        deliveries, addresses = [], []
        for parent in root.iter():
            tag = parent.tag.split("}")[-1]
            if tag not in ("delivery", "address"): continue
            vals = {}
            for ch in parent.iter():
                ct = ch.tag.split("}")[-1]
                if ch.text and ch.text.strip(): vals[ct] = ch.text.strip()
            if vals: (deliveries if tag == "delivery" else addresses).append(vals)
        opts = "".join(f"<option value='{esc(x.get('id',''))}'>{esc(x.get('id',''))} — {esc(x.get('name',''))}</option>"
                       for x in deliveries) or "<option value=''>Не найдено</option>"
        aopts = "".join(
            f"<option value='{esc(x.get('id',''))}'>{esc(x.get('id',''))} — "
            f"{esc(', '.join(p for p in [x.get('city',''), x.get('street',''), x.get('house',''), x.get('office','')] if p) or '—')}</option>"
            for x in addresses) or "<option value=''>Не найдено</option>"
        body = f"""<div class='card'><h1>🚚 ROSSKO — варианты доставки</h1>
<div class='ok'>Доставок: {len(deliveries)}, адресов: {len(addresses)}.</div>
<form method='post' action='/save_rossko_delivery'>
<input type='hidden' name='id' value='{esc(sid)}'>
<div class='grid'>
<div class='field'><label>Способ доставки</label><select name='delivery_id'>{opts}</select></div>
<div class='field'><label>Адрес доставки</label><select name='address_id'>{aopts}</select></div>
</div>
<div class='toolbar'><button class='green'>Сохранить</button>
<a class='btn secondary' href='/supplier?id={esc(sid)}'>Отмена</a></div></form></div>"""
        self.out(layout("ROSSKO — доставка", body))


def _find_free_port(host="127.0.0.1", preferred=8000):
    """Выбирает свободный localhost-порт; 8000 используется первым для обратной совместимости."""
    for port in [preferred] + list(range(preferred + 1, preferred + 51)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise OSError("Не удалось найти свободный локальный порт")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _ensure_files()
    init_db()
    host = "127.0.0.1"
    preferred = int(os.environ.get("PARTS_MANAGER_PORT", "8000"))
    port = _find_free_port(host, preferred)
    srv = ThreadingHTTPServer((host, port), H)
    url = f"http://{host}:{port}"
    print(f"Parts Manager v{VERSION} запущен: {url}")
    print(f"Данные: {DATA_DIR}")
    # Открываем интерфейс автоматически при обычном запуске desktop-версии.
    if os.environ.get("PARTS_MANAGER_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено.")
    finally:
        srv.server_close()



if __name__ == "__main__":
    main()