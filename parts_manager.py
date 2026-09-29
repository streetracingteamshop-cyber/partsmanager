from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime, timedelta
import json, os, uuid, html, ssl, threading, logging, re, socket, webbrowser, time, sys, smtplib, sqlite3, imaplib, email, email.utils, email.header, csv, io, zipfile, hashlib, tempfile, shutil, subprocess
from logging.handlers import RotatingFileHandler

VERSION = "12.6.0"
HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Parts Manager"

_DEFAULT_DATA_ROOT = os.environ.get("PARTS_MANAGER_DATA_DIR")
if not _DEFAULT_DATA_ROOT:
    if os.name == "nt":
        _local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        _DEFAULT_DATA_ROOT = os.path.join(_local, "Parts Manager", "data")
    else:
        _DEFAULT_DATA_ROOT = os.path.join(os.path.expanduser("~/.parts_manager"), "data")
DATA_DIR = _DEFAULT_DATA_ROOT
os.makedirs(DATA_DIR, exist_ok=True)
LOG_DIR = os.path.join(os.path.dirname(DATA_DIR), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

BUILTIN = {
    "vin_url": "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues",
    "rossko_search": "https://api.rossko.ru/service/v2.1/GetSearch",
    "rossko_checkout": "https://api.rossko.ru/service/v2.1/GetCheckout",
    "rossko_details": "https://api.rossko.ru/service/v2.1/GetCheckoutDetails",
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
        "name": "REST/JSON", "hint": "1 поле — API-ключ",
        "fields": [("key", "API-ключ", "Ключ из личного кабинета поставщика", "password", True)],
        "test_article": "GDB1044", "test_brand": "TRW",
    },
    "zappro": {
        "name": "ZAP-PRO (PARTS SOFT API)", "hint": "2 поля — адрес портала и API-ключ",
        "fields": [
            ("base_url", "Адрес портала", "https://portal.zap-pro.ru", "text", True),
            ("api_key", "API-ключ", "Персональный ключ от ZAP-PRO", "password", True),
        ],
        "default_base_url": "https://portal.zap-pro.ru",
        "test_article": "OP572", "test_brand": "FILTRON",
    },
    "rossko": {
        "name": "SOAP (KEY1/KEY2)", "hint": "4 поля — два ключа и параметры доставки",
        "fields": [
            ("key1", "KEY1", "Первый ключ", "password", True),
            ("key2", "KEY2", "Второй ключ", "password", True),
            ("delivery_id", "delivery_id", "Способ доставки", "text", False),
            ("address_id", "address_id", "Адрес доставки", "text", False),
        ],
        "test_article": "lc-1400",
    },
    "avd": {
        "name": "SOAP WCF (login/password)", "hint": "3 поля — адрес сервиса, логин, пароль",
        "fields": [
            ("endpoint", "Адрес WEB-сервиса", "https://ws1.avdmotors.ru/AvdUserService.svc/secure", "text", True),
            ("login", "Логин", "Логин AVD", "text", True),
            ("password", "Пароль", "Пароль AVD", "password", True),
            ("wsKey", "Ключ WS (если выдан)", "Доп. ключ AVD", "password", False),
        ],
        "default_endpoint": "https://ws1.avdmotors.ru/AvdUserService.svc/secure",
        "test_article": "LC-1030",
    },
    "autoeuro": {
        "name": "AutoEuro API v2", "hint": "API-ключ, delivery_key и payer_key",
        "fields": [
            ("api_key", "API-ключ", "Ключ из shop.autoeuro.ru", "password", True),
            ("delivery_key", "delivery_key", "Ключ способа получения", "text", True),
            ("payer_key", "payer_key", "Ключ плательщика", "text", True),
            ("base_url", "API URL", "https://api.autoeuro.ru/api/v2/json", "text", False),
        ],
        "test_article": "IK16",
    },
    "mparts": {
        "name": "MParts / v01 REST", "hint": "Логин, MD5-пароль и параметры доставки/оплаты",
        "fields": [
            ("userlogin", "Логин MParts", "Имя пользователя", "text", True),
            ("userpsw", "MD5-пароль", "MD5-хэш пароля", "password", True),
            ("paymentMethod", "ID оплаты", "basket/paymentMethod", "text", True),
            ("shipmentMethod", "ID доставки", "basket/shipmentMethod", "text", True),
            ("shipmentAddress", "Адрес доставки", "ID адреса", "text", False),
            ("shipmentOffice", "Пункт самовывоза", "ID офиса", "text", False),
        ],
        "test_article": "334420",
    },
    "custom": {
        "name": "Свой URL (REST/JSON)", "hint": "4 поля — URL, ключ, тип авторизации, имя параметра",
        "fields": [
            ("url", "URL запроса", "https://api.example.ru/search?q={article}", "text", True),
            ("api_key", "API-ключ", "Если нужен", "password", False),
            ("auth_mode", "Куда ключ", "select:url|header", "select", False),
            ("url_article_param", "Имя параметра артикула", "article, number, q", "text", False),
        ],
        "test_article": "LC-1030",
    },
}

DBS = {k: os.path.join(DATA_DIR, v) for k, v in {
    "suppliers": "suppliers.json", "vehicles": "vehicles.json",
    "settings": "settings.json", "cart": "cart.json", "cart_backup": "cart_backup.json",
}.items()}

DEFAULTS = {
    "suppliers": [], "vehicles": [],
    "settings": {
        "company_name": "", "city": "",
        "smtp_host": "", "smtp_port": 587, "smtp_security": "starttls",
        "smtp_user": "", "smtp_password": "", "smtp_from_email": "", "smtp_from_name": "Parts Manager",
        "imap_host": "", "imap_port": 993, "imap_security": "ssl", "imap_user": "",
        "imap_password": "", "imap_folder": "INBOX", "price_poll_minutes": 15,
        "theme": "auto", "cart_customer_id": "",
        "github_repo": os.environ.get("PARTS_MANAGER_GITHUB_REPO", "streetracingteamshop-cyber/partsmanager"), "auto_update": True,
    },
    "cart": [], "cart_backup": [],
}
_lock = threading.Lock()


def _ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    for k, p in DBS.items():
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


def esc(v):
    return html.escape(str(v if v is not None else ""))


def parse_price(s):
    if s is None: return None
    if isinstance(s, (int, float)): return float(s)
    t = str(s).strip().replace(" ", "").replace("\u00a0", "").replace("₽", "")
    if "." in t and "," in t:
        if t.rfind(",") > t.rfind("."): t = t.replace(".", "").replace(",", ".")
        else: t = t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        t = ("".join(parts) if len(parts) > 1 and len(parts[-1]) == 3 else
             "".join(parts[:-1]) + "." + parts[-1])
    elif t.count(".") > 1:
        parts = t.split(".")
        t = ("".join(parts) if len(parts[-1]) == 3 else
             "".join(parts[:-1]) + "." + parts[-1])
    try: return float(t)
    except ValueError: return None


def fmt_price(v):
    if v is None: return "—"
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


def normalize_article(s):
    return re.sub(r"[^a-zA-Z0-9]", "", str(s or "")).lower()


def normalize_brand(s):
    if not s: return ""
    t = str(s).strip().lower()
    rm = {"а":"a","е":"e","о":"o","р":"p","с":"c","у":"y","х":"x","в":"b","к":"k","м":"m","н":"h","т":"t"}
    out = [rm.get(ch, ch) for ch in t]
    return re.sub(r"[^a-z0-9]", "", "".join(out))


def delivery_days(value):
    if value is None: return None
    if isinstance(value, (int, float)):
        try: return int(value)
        except (ValueError, TypeError): return None
    s = str(value).strip().lower()
    if not s: return None
    if s in ("сегодня", "today", "0"): return 0
    if s in ("завтра", "tomorrow", "1"): return 1
    if s in ("послезавтра", "2"): return 2
    m = re.match(r"^\s*(\d+)\s*[-–—]\s*(\d+)\s*", s)
    if m: return max(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^\s*(\d+)\s*", s)
    if m: return int(m.group(1))
    return None


def human_delivery(delivery_value, delivery_start_iso=""):
    days = delivery_days(delivery_value)
    if days is not None:
        if days == 0: return "сегодня"
        if days == 1: return "завтра"
        if days == 2: return "послезавтра"
        return f"через {days} дн."
    if delivery_start_iso:
        try:
            dt = datetime.fromisoformat(delivery_start_iso[:19])
            return dt.strftime("%d.%m")
        except Exception: pass
    raw = str(delivery_value or "").strip()
    return raw if raw else "срок не указан"


def smtp_security_mode(value):
    value = str(value or "starttls").lower().strip()
    return value if value in ("starttls", "ssl", "none") else "starttls"


def split_emails(value):
    return [x.strip().lower() for x in re.split(r"[,;\n]+", str(value or "")) if x.strip()]



# ---------- GitHub updater ----------
GITHUB_API = "https://api.github.com"

def github_repo_name():
    repo = str(read("settings").get("github_repo") or os.environ.get("PARTS_MANAGER_GITHUB_REPO", "")).strip()
    repo = re.sub(r"^https?://github\.com/", "", repo).strip("/")
    repo = repo.removesuffix(".git")
    return repo if re.fullmatch(r"[^/\s]+/[^/\s]+", repo) else ""

def github_version_key(value):
    nums = re.findall(r"\d+", str(value or ""))
    return tuple(int(x) for x in nums[:4]) if nums else (0,)

def github_latest():
    repo = github_repo_name()
    if not repo:
        return None, "GitHub-репозиторий не настроен"
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"PartsManager/{VERSION}"})
    try:
        with urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as ex:
        return None, f"GitHub HTTP {ex.code}"
    except Exception as ex:
        return None, f"GitHub: {ex}"
    tag = str(obj.get("tag_name") or "").lstrip("vV")
    if not tag:
        return None, "GitHub: у latest release нет tag_name"
    assets = obj.get("assets") or []
    installer = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe") and "partsmanager-setup" in str(a.get("name", "")).lower()), None)
    return {
        "tag": tag,
        "name": obj.get("name") or tag,
        "html_url": obj.get("html_url") or "",
        "installer_url": installer.get("browser_download_url") if installer else None,
        "installer_name": installer.get("name") if installer else None,
        "zipball_url": obj.get("zipball_url"),
    }, ""

def update_from_github():
    latest, err = github_latest()
    if err:
        return False, err
    if github_version_key(latest["tag"]) <= github_version_key(VERSION):
        return False, f"Установлена актуальная версия {VERSION}"
    if os.name != "nt":
        return False, "Автоустановка GitHub Release поддерживается в Windows-версии приложения"
    installer_url = latest.get("installer_url")
    if not installer_url:
        return False, "GitHub: в последнем Release не найден установщик PartsManager-Setup-*.exe"
    temp = tempfile.mkdtemp(prefix="parts_manager_update_")
    try:
        installer = os.path.join(temp, latest.get("installer_name") or f"PartsManager-Setup-{latest['tag']}.exe")
        req = Request(installer_url, headers={"Accept": "application/octet-stream", "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=120, context=ssl.create_default_context()) as r:
            with open(installer, "wb") as f:
                shutil.copyfileobj(r, f)
        if not os.path.exists(installer) or os.path.getsize(installer) < 100_000:
            raise RuntimeError("GitHub: установщик скачан некорректно")
        subprocess.Popen([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], cwd=temp, close_fds=True)
        logging.info("Starting installer update %s -> %s", VERSION, latest["tag"])
        # The installer must be able to replace application files. Exit this process after
        # launching it; user data lives outside the installation directory.
        threading.Timer(1.0, lambda: os._exit(0)).start()
        return True, f"Запущено обновление до {latest['tag']}. Приложение будет закрыто."
    except Exception as ex:
        logging.exception("GitHub update failed")
        return False, f"Обновление не выполнено: {ex}"
    finally:
        # The installer is independent of this process. Keep the temp directory until reboot
        # because Windows may still be reading the downloaded installer.
        pass

def start_auto_update():
    settings = read("settings")
    if not settings.get("auto_update", True) or not github_repo_name():
        return
    def worker():
        try:
            latest, err = github_latest()
            if err or not latest or github_version_key(latest["tag"]) <= github_version_key(VERSION):
                return
            ok, msg = update_from_github()
            if ok:
                logging.info("%s. Перезапустите приложение для применения обновления.", msg)
        except Exception:
            logging.exception("auto update")
    threading.Thread(target=worker, name="github-updater", daemon=True).start()

# ---------- Провайдеры ----------
def rossko_search(sup, article):
    cred = sup.get("credentials", {}) or {}
    key1 = cred.get("key1", "").strip(); key2 = cred.get("key2", "").strip()
    delivery_id = cred.get("delivery_id", "").strip(); address_id = cred.get("address_id", "").strip()
    if not (key1 and key2 and article):
        return [], [], "не заполнены ключи или артикул"
    od = (f"<ns:delivery_id>{xml_escape(delivery_id)}</ns:delivery_id>" if delivery_id else "")
    oa = (f"<ns:address_id>{xml_escape(address_id)}</ns:address_id>" if address_id else "")
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="https://api.rossko.ru/">
  <soap:Body><ns:GetSearch><ns:KEY1>{xml_escape(key1)}</ns:KEY1><ns:KEY2>{xml_escape(key2)}</ns:KEY2>
  <ns:text>{xml_escape(article)}</ns:text>{od}{oa}</ns:GetSearch></soap:Body></soap:Envelope>'''.encode("utf-8")
    try:
        req = Request(BUILTIN["rossko_search"], data=envelope, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"https://api.rossko.ru/GetSearch"',
            "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=60, context=ssl.create_default_context()) as resp:
            raw = resp.read()
    except Exception as ex:
        body = ""
        if hasattr(ex, "read"):
            try: body = ex.read().decode("utf-8", "replace")
            except Exception: pass
        return [], [], f"сеть ROSSKO: {ex} {body[:300]}"
    try: root = ET.fromstring(raw)
    except ET.ParseError as e: return [], [], f"XML: {e}"
    texts = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el.text and el.text.strip(): texts.setdefault(tag, []).append(el.text.strip())
    if (texts.get("success", ["false"])[0] or "").strip().lower() not in ("true", "1", "yes"):
        return [], [], f"ROSSKO: {texts.get('message', ['нет данных'])[0]}"
    def pp(pe):
        info = {"guid": "", "brand": "", "partnumber": "", "name": "", "stocks": []}
        for ch in pe:
            tag = ch.tag.split("}")[-1]
            if tag in ("guid", "brand", "partnumber", "name"): info[tag] = (ch.text or "").strip()
            elif tag == "stocks":
                for st in ch:
                    if st.tag.split("}")[-1] != "stock": continue
                    s = {}
                    for f in st:
                        ft = f.tag.split("}")[-1]
                        if f.text: s[ft] = f.text.strip()
                    info["stocks"].append(s)
        return info
    main, crosses = [], []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "PartsList":
            for p in el:
                if p.tag.split("}")[-1] != "Part": continue
                info = pp(p)
                for s in info["stocks"]:
                    price = parse_price(s.get("price"))
                    if price is None: continue
                    main.append({"id": str(uuid.uuid4()), "supplier_id": sup["id"],
                        "supplier_name": sup.get("name", "ROSSKO"),
                        "brand": info["brand"], "partnumber": info["partnumber"],
                        "article": info["partnumber"], "name": info["name"],
                        "price": price, "count": int(s.get("count", 0) or 0),
                        "multiplicity": int(s.get("multiplicity", 1) or 1),
                        "delivery": s.get("delivery", ""), "delivery_start": s.get("deliveryStart", ""),
                        "delivery_end": s.get("deliveryEnd", ""), "warehouse": s.get("description", ""),
                        "warehouse_id": s.get("id", ""), "stock": s.get("id", ""),
                        "extra": s.get("extra", "0") == "1", "is_cross": False})
        elif tag == "crosses":
            for p in el:
                if p.tag.split("}")[-1] != "Part": continue
                info = pp(p)
                for s in info["stocks"]:
                    price = parse_price(s.get("price"))
                    if price is None: continue
                    crosses.append({"id": str(uuid.uuid4()), "supplier_id": sup["id"],
                        "supplier_name": sup.get("name", "ROSSKO"),
                        "brand": info["brand"], "partnumber": info["partnumber"],
                        "article": info["partnumber"], "name": info["name"],
                        "price": price, "count": int(s.get("count", 0) or 0),
                        "multiplicity": int(s.get("multiplicity", 1) or 1),
                        "delivery": s.get("delivery", ""), "delivery_start": s.get("deliveryStart", ""),
                        "delivery_end": s.get("deliveryEnd", ""), "warehouse": s.get("description", ""),
                        "warehouse_id": s.get("id", ""), "stock": s.get("id", ""),
                        "extra": s.get("extra", "0") == "1", "is_cross": True})
    return main, crosses, ""


def berg_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    key = (cred.get("key", "") or cred.get("api_key", "")).strip()
    if not key: key = BUILTIN.get("berg_key_default", "").strip()
    if not key: return [], [], "не заполнен API-ключ"
    if not article: return [], [], "не указан артикул"
    base = (sup.get("endpoint") or BUILTIN["berg_base"]).rstrip("/")
    ver = BUILTIN["berg_version"]
    ctx = ssl.create_default_context()
    def do_req(art, br=""):
        params = {"key": key, "analogs": "1", "warehouse_types[]": ["1","2","3"],
                  "items[0][resource_article]": art}
        if br: params["items[0][brand_name]"] = br
        qs = urlencode(params, doseq=True)
        url = f"{base}/{ver}/ordering/get_stock.json?{qs}"
        try:
            req = Request(url, headers={"User-Agent": f"PartsManager/{VERSION}",
                "Accept": "application/json", "X-Berg-API-Key": key})
            with urlopen(req, timeout=45, context=ctx) as r:
                raw = r.read().decode("utf-8", "replace")
        except HTTPError as ex:
            try: body = ex.read().decode("utf-8", "replace")
            except Exception: body = str(ex)
            if ex.code == 300:
                try: return json.loads(body), ""
                except Exception: pass
            if ex.code == 401: return None, "Ключ не подошёл."
            return None, f"HTTP {ex.code}: {body[:400]}"
        except Exception as ex: return None, f"сеть: {ex}"
        try: return json.loads(raw), ""
        except json.JSONDecodeError as e: return None, f"JSON: {e} | {raw[:300]}"
    def parse_all(data, oa):
        out = []
        if not isinstance(data, dict) or data.get("errors"): return out
        for res in (data.get("resources") or []):
            if not isinstance(res, dict): continue
            info = {"id": res.get("id"), "name": res.get("name") or "",
                    "article": res.get("article") or oa,
                    "brand": (res.get("brand") or {}).get("name") or ""}
            for off in (res.get("offers") or []):
                if not isinstance(off, dict): continue
                price = parse_price(off.get("price"))
                if price is None: continue
                term = off.get("average_period") or off.get("assured_period") or ""
                wh = off.get("warehouse") or {}
                out.append({"id": str(uuid.uuid4()), "supplier_id": sup["id"],
                    "supplier_name": sup.get("name", "BERG"), "brand": info["brand"],
                    "partnumber": info["article"], "article": info["article"], "name": info["name"],
                    "price": price, "count": int(off.get("quantity") or 0),
                    "multiplicity": int(off.get("multiplication_factor") or 1),
                    "delivery": str(term), "delivery_start": "", "delivery_end": "",
                    "warehouse": wh.get("name") or "BERG", "warehouse_id": str(wh.get("id") or ""),
                    "resource_id": info.get("id"), "extra": False, "is_cross": False})
        return out
    data, err = do_req(article, brand)
    if err: return [], [], err
    all_offers = parse_all(data, article)
    if not all_offers and isinstance(data, dict):
        wl = data.get("warnings") or []
        ia = any((w or {}).get("code") == "WARN_ARTICLE_IS_AMBIGUOUS" for w in wl if isinstance(w, dict))
        res_list = data.get("resources") or []
        if ia and res_list:
            seen = []
            for r in res_list:
                if not isinstance(r, dict): continue
                b = (r.get("brand") or {}).get("name")
                if b and b not in seen: seen.append(b)
            for b in seen[:5]:
                d2, e2 = do_req(article, b)
                if e2: continue
                all_offers += parse_all(d2, article)
    rn = normalize_article(article)
    main, crosses = [], []
    for o in all_offers:
        if normalize_article(o.get("article")) == rn: main.append(o)
        else: o["is_cross"] = True; crosses.append(o)
    return main, crosses, ""


def zappro_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    api_key = (cred.get("api_key") or "").strip()
    base_url = (cred.get("base_url") or "https://portal.zap-pro.ru").strip().rstrip("/")
    if not api_key: return [], [], "ZAP-PRO: не задан API-ключ"
    if not article: return [], [], "ZAP-PRO: не указан артикул"
    params = {"api_key": api_key, "oem": article}
    if brand: params["make_name"] = brand
    url = f"{base_url}/backend/price_items/api/v1/search/get_offers_by_oem_and_make_name?{urlencode(params)}"
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            raw = r.read().decode("utf-8", "replace")
    except HTTPError as ex:
        try: body = ex.read().decode("utf-8", "replace")
        except Exception: body = str(ex)
        return [], [], f"ZAP-PRO HTTP {ex.code}: {body[:500]}"
    except Exception as ex: return [], [], f"ZAP-PRO: сеть: {ex}"
    try: obj = json.loads(raw)
    except json.JSONDecodeError as e: return [], [], f"ZAP-PRO: JSON: {e} | {raw[:300]}"
    if not isinstance(obj, dict): return [], [], "ZAP-PRO: неожиданный ответ"
    if obj.get("result") != "ok": return [], [], f"ZAP-PRO: {obj.get('error', 'нет данных')}"
    data = obj.get("data") or []
    if not isinstance(data, list): return [], [], "ZAP-PRO: пустой ответ"
    main, crosses = [], []
    rn = normalize_article(article)
    for it in data:
        if not isinstance(it, dict): continue
        price = parse_price(it.get("cost"))
        if price is None: continue
        try:
            qnt = int(it.get("qnt") or 0)
            if qnt < 0: qnt = 0
        except (TypeError, ValueError): qnt = 0
        try: min_qnt = int(it.get("min_qnt") or 1)
        except (TypeError, ValueError): min_qnt = 1
        try: min_days = int(it.get("min_delivery_day") or 0)
        except (TypeError, ValueError): min_days = 0
        try: max_days = int(it.get("max_delivery_day") or 0)
        except (TypeError, ValueError): max_days = 0
        item_oem = str(it.get("oem") or article)
        is_cross = normalize_article(item_oem) != rn
        delivery = str(min_days) if min_days == max_days and min_days > 0 else (f"{min_days}-{max_days}" if min_days != max_days else "0")
        o = {"id": str(uuid.uuid4()), "supplier_id": sup["id"],
             "supplier_name": sup.get("name", "ZAP-PRO"),
             "brand": str(it.get("make_name") or brand or ""),
             "partnumber": item_oem, "article": item_oem,
             "name": str(it.get("detail_name") or ""),
             "price": price, "count": qnt, "multiplicity": min_qnt,
             "delivery": delivery, "delivery_start": "", "delivery_end": "",
             "warehouse": str(it.get("sup_logo") or "ZAP-PRO"), "warehouse_id": "",
             "system_hash": str(it.get("system_hash") or ""),
             "min_delivery_day": min_days, "max_delivery_day": max_days,
             "stat_group": it.get("stat_group"), "extra": False, "is_cross": is_cross}
        (crosses if is_cross else main).append(o)
    return main, crosses, ""


def avd_soap_request(method, params, endpoint=None):
    endpoint = (endpoint or BUILTIN["avd_url"]).strip() or BUILTIN["avd_url"]
    ns = BUILTIN["avd_ns"]
    action = f"{ns}IAvdUserService/{method}"
    fx = ""
    for key, val in params.items():
        if val is None: continue
        sval = str(val)
        if isinstance(val, bool): sval = "true" if val else "false"
        fx += f"<{key}>{xml_escape(sval)}</{key}>"
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <{method} xmlns="{ns}">
      {fx}
    </{method}>
  </s:Body>
</s:Envelope>'''.encode("utf-8")
    try:
        req = Request(endpoint, data=envelope, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{action}"',
            "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=60, context=ssl.create_default_context()) as resp:
            return resp.read().decode("utf-8", "replace"), ""
    except HTTPError as ex:
        try: body = ex.read().decode("utf-8", "replace")
        except Exception: body = str(ex)
        fm = ""
        try:
            root = ET.fromstring(body)
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("faultstring", "Message", "message", "Reason") and el.text:
                    fm = el.text.strip(); break
        except Exception: pass
        if ex.code == 404:
            return body, f"HTTP 404 — адрес сервиса не найден. Отправляли на: {endpoint}"
        return body, f"HTTP {ex.code}: {fm or body[:500]}"
    except Exception as ex: return "", f"сеть: {ex}"


def avd_parse_result(xml_text):
    out = []
    try: root = ET.fromstring(xml_text)
    except ET.ParseError: return out
    def tof(el, name):
        for ch in el.iter():
            if ch.tag.split("}")[-1] == name and ch.text: return ch.text.strip()
        return ""
    candidates = []
    for el in root.iter():
        ct = {c.tag.split("}")[-1] for c in el}
        if "Price" in ct or "ItemNumber" in ct: candidates.append(el)
    seen = set()
    for el in candidates:
        price = parse_price(tof(el, "Price"))
        if price is None: continue
        inn = tof(el, "ItemNumber") or tof(el, "Number")
        hv = tof(el, "Hash")
        u = (inn, hv or "", str(price))
        if u in seen: continue
        seen.add(u)
        try: qty = int(float(re.sub(r"[^\d.\-]", "", tof(el, "Quantity") or "0") or 0))
        except Exception: qty = 0
        try: mult = int(float(re.sub(r"[^\d.\-]", "", tof(el, "Multiply") or "1") or 1))
        except Exception: mult = 1
        out.append({"ItemNumber": inn, "ItemName": tof(el, "ItemName") or tof(el, "Name"),
            "CatalogName": tof(el, "CatalogName") or tof(el, "Catalog"),
            "Price": price, "Quantity": qty, "Multiply": mult,
            "SupplierName": tof(el, "SupplierName") or tof(el, "Supplier"),
            "SupplierPeriod": tof(el, "SupplierPeriod") or tof(el, "Period"),
            "SupplierRegion": tof(el, "SupplierRegion"), "DealerStore": tof(el, "DealerStore"),
            "Hash": hv})
    return out


def avd_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    endpoint = (cred.get("endpoint", "") or "").strip() or BUILTIN["avd_url"]
    login = cred.get("login", "").strip(); password = cred.get("password", "").strip()
    if not login or not password: return [], [], "не заполнены логин и пароль"
    if not article: return [], [], "не указан артикул"
    body, err = avd_soap_request("GetOriginalPrice",
        {"login": login, "password": password, "number": article}, endpoint=endpoint)
    if err: return [], [], err
    original = avd_parse_result(body)
    crosses = []
    if original:
        cat = original[0].get("CatalogName") or ""
        if cat:
            b2, e2 = avd_soap_request("GetFastCrossesPrice",
                {"login": login, "password": password, "number": article, "catalog": cat}, endpoint=endpoint)
            if not e2: crosses = avd_parse_result(b2)
    def to_o(x, ic):
        return {"id": str(uuid.uuid4()), "supplier_id": sup["id"],
            "supplier_name": sup.get("name", "AVD"),
            "brand": x.get("SupplierName") or brand or "",
            "partnumber": x.get("ItemNumber") or article, "article": x.get("ItemNumber") or article,
            "name": x.get("ItemName") or "", "price": x.get("Price"),
            "count": x.get("Quantity") or 0, "multiplicity": x.get("Multiply") or 1,
            "delivery": str(x.get("SupplierPeriod") or ""), "delivery_start": "", "delivery_end": "",
            "warehouse": x.get("DealerStore") or x.get("SupplierRegion") or "AVD",
            "warehouse_id": x.get("Hash") or "", "hash": x.get("Hash") or "",
            "extra": False, "is_cross": ic}
    return [to_o(x, False) for x in original], [to_o(x, True) for x in crosses], ""


def autoeuro_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    key = cred.get("api_key", "").strip(); dl = cred.get("delivery_key", "").strip()
    if not key or not dl: return [], [], "AutoEuro: не заданы API-ключ или delivery_key"
    base = (cred.get("base_url") or "https://api.autoeuro.ru/api/v2/json").rstrip("/")
    if not brand: return [], [], "AutoEuro: для API v2 нужен бренд"
    params = {"key": key, "brand": brand, "code": article, "delivery_key": dl,
              "with_crosses": 0, "with_offers": 1}
    try:
        req = Request(base + "/search_items/?" + urlencode(params),
                      headers={"Accept": "application/json", "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as ex: return [], [], f"AutoEuro: {ex}"
    data = obj.get("DATA") if isinstance(obj, dict) else None
    if not isinstance(data, list):
        return [], [], str(obj.get("ERROR", {}).get("message") if isinstance(obj, dict) else "AutoEuro: неожиданный ответ")
    main, crosses = [], []
    for it in data:
        price = parse_price(it.get("price"))
        if price is None: continue
        ic = it.get("cross") is not None
        o = {"id": str(uuid.uuid4()), "supplier_id": sup["id"],
             "supplier_name": sup.get("name", "AutoEuro"),
             "brand": it.get("brand") or brand, "partnumber": it.get("code") or article,
             "article": it.get("code") or article, "name": it.get("name") or "",
             "price": price, "count": int(it.get("amount") or 0),
             "multiplicity": int(it.get("packing") or 1),
             "delivery": str(it.get("delivery_time") or it.get("delivery_time_max") or ""),
             "delivery_start": "", "delivery_end": "",
             "warehouse": it.get("warehouse_name") or "AutoEuro",
             "warehouse_id": it.get("warehouse_key") or "",
             "offer_key": it.get("offer_key") or "", "delivery_key": dl,
             "payer_key": cred.get("payer_key") or "", "extra": False, "is_cross": ic}
        (crosses if ic else main).append(o)
    return main, crosses, ""


def mparts_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    user = cred.get("userlogin", "").strip(); psw = cred.get("userpsw", "").strip()
    if not user or not psw: return [], [], "MParts: не заданы userlogin/userpsw"
    base = (cred.get("base_url") or "https://v01.ru/api/devinsight").rstrip("/")
    params = {"userlogin": user, "userpsw": psw, "number": article, "useOnlineStocks": 1}
    if brand: params["brand"] = brand
    try:
        req = Request(base + "/search/articles/?" + urlencode(params),
            headers={"Accept": "application/json", "User-Agent": f"PartsManager/{VERSION}"})
        with urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as ex: return [], [], f"MParts: {ex}"
    if not isinstance(obj, list): return [], [], "MParts: неожиданный ответ API"
    main, crosses = [], []
    for it in obj:
        price = parse_price(it.get("price"))
        if price is None: continue
        art = it.get("number") or article
        ic = normalize_article(art) != normalize_article(article)
        o = {"id": str(uuid.uuid4()), "supplier_id": sup["id"],
             "supplier_name": sup.get("name", "MParts"), "brand": it.get("brand") or brand,
             "partnumber": art, "article": art, "name": it.get("description") or "",
             "price": price,
             "count": int(float(it.get("availability") or 0) if str(it.get("availability") or "").replace('.', '', 1).isdigit() else 0),
             "multiplicity": int(float(it.get("packing") or 1)),
             "delivery": str(it.get("deliveryPeriod") or ""), "delivery_start": "", "delivery_end": "",
             "warehouse": it.get("distributorCode") or "MParts",
             "warehouse_id": str(it.get("distributorId") or it.get("supplierCode") or ""),
             "supplierCode": str(it.get("supplierCode") or ""),
             "itemKey": str(it.get("itemKey") or ""), "code": str(it.get("code") or ""),
             "extra": False, "is_cross": ic}
        (crosses if ic else main).append(o)
    return main, crosses, ""


def custom_search(sup, article, brand=""):
    cred = sup.get("credentials", {}) or {}
    url_tpl = (cred.get("url", "") or "").strip()
    api_key = (cred.get("api_key", "") or "").strip()
    auth_mode = (cred.get("auth_mode", "url") or "url").strip()
    ap = (cred.get("url_article_param", "") or "article").strip()
    if not url_tpl: return [], [], "не задан URL запроса"
    if not article: return [], [], "не указан артикул"
    if "{article}" in url_tpl: url = url_tpl.replace("{article}", quote(article))
    else:
        sep = "&" if "?" in url_tpl else "?"
        url = f"{url_tpl}{sep}{urlencode({ap: article})}"
    h = {"User-Agent": f"PartsManager/{VERSION}", "Accept": "application/json"}
    if api_key and auth_mode == "header":
        h["Authorization"] = f"Bearer {api_key}"; h["X-API-Key"] = api_key
    elif api_key and auth_mode == "url":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode({'key': api_key})}"
    try:
        req = Request(url, headers=h)
        with urlopen(req, timeout=45, context=ssl.create_default_context()) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except HTTPError as ex:
        try: body = ex.read().decode("utf-8", "replace")
        except Exception: body = str(ex)
        return [], [], f"HTTP {ex.code}: {body[:500]}"
    except Exception as ex: return [], [], f"сеть: {ex}"
    try: data = json.loads(raw)
    except json.JSONDecodeError as e: return [], [], f"JSON: {e} | {raw[:300]}"
    items = []
    if isinstance(data, list): items = data
    elif isinstance(data, dict):
        for k in ("items", "goods", "results", "offers", "products", "parts", "data"):
            if isinstance(data.get(k), list): items = data[k]; break
    all_o = []
    for it in items:
        if not isinstance(it, dict): continue
        price = None
        for pk in ("price", "Price", "cost", "amount"):
            price = parse_price(it.get(pk))
            if price is not None: break
        if price is None: continue
        art = it.get("article") or it.get("Article") or it.get("number") or it.get("sku") or article
        cnt = 0
        for ck in ("count", "quantity", "stock", "qty", "available"):
            try: cnt = int(it.get(ck) or 0); break
            except (ValueError, TypeError): continue
        term = it.get("delivery") or it.get("term") or it.get("days") or it.get("deliveryDays") or ""
        all_o.append({"id": str(uuid.uuid4()), "supplier_id": sup["id"],
            "supplier_name": sup.get("name", "Поставщик"),
            "brand": it.get("brand") or it.get("Brand") or brand or "",
            "partnumber": art, "article": art,
            "name": it.get("name") or it.get("description") or it.get("title") or "",
            "price": price, "count": cnt,
            "multiplicity": int(it.get("multiplicity") or it.get("rate") or 1),
            "delivery": str(term), "delivery_start": "", "delivery_end": "",
            "warehouse": it.get("warehouse") or it.get("store") or sup.get("name", ""),
            "warehouse_id": str(it.get("warehouse_id") or ""),
            "extra": False, "is_cross": bool(it.get("is_cross") or it.get("analog"))})
    rn = normalize_article(article)
    main, crosses = [], []
    for o in all_o:
        if o.get("is_cross") or normalize_article(o.get("article")) != rn:
            o["is_cross"] = True; crosses.append(o)
        else: main.append(o)
    return main, crosses, ""


def collect_offers(article, filters, ai_hint=None, sort_mode="price_delivery"):
    suppliers = read("suppliers")
    if not suppliers: return [], [], []
    main_offers, all_crosses, errors = [], [], []
    def af(lst):
        out = []
        for o in lst:
            if filters.get("min_price") is not None and o["price"] < filters["min_price"]: continue
            if filters.get("max_price") is not None and o["price"] > filters["max_price"]: continue
            if filters.get("max_days") is not None:
                d = delivery_days(o.get("delivery"))
                if d is not None and d > filters["max_days"]: continue
            if filters.get("in_stock_only") and o.get("count", 0) <= 0: continue
            out.append(o)
        return out
    brand = (ai_hint or {}).get("brand") or ""
    for s in suppliers:
        tpl = s.get("template", "custom")
        if tpl not in API_TYPES: tpl = "custom"
        try:
            if tpl == "rossko": m, c, err = rossko_search(s, article)
            elif tpl == "berg": m, c, err = berg_search(s, article, brand)
            elif tpl == "zappro": m, c, err = zappro_search(s, article, brand)
            elif tpl == "avd": m, c, err = avd_search(s, article, brand)
            elif tpl == "autoeuro": m, c, err = autoeuro_search(s, article, brand)
            elif tpl == "mparts": m, c, err = mparts_search(s, article, brand)
            else: m, c, err = custom_search(s, article, brand)
        except Exception as ex:
            logging.exception("provider error")
            m, c, err = [], [], f"ошибка: {ex}"
        m = [o for o in (m or []) if not o.get("demo") and not o.get("is_demo")]
        c = [o for o in (c or []) if not o.get("demo") and not o.get("is_demo")]
        if err:
            errors.append(f"{s.get('name', tpl)}: {err}")
            with db_conn() as dbc:
                ps = dbc.execute("SELECT * FROM supplier_prices WHERE supplier_id=? AND (article=? OR article LIKE ?) ORDER BY received_at DESC LIMIT 20",
                                 (s.get("id", ""), article, f"%{article}%")).fetchall()
            if ps:
                for p in ps:
                    sr = str(p["stock"] or "")
                    try: cnt = int(float(re.sub(r"[^0-9.\-]", "", sr))) if sr else 0
                    except Exception: cnt = 0
                    m.append({"id": str(uuid.uuid4()), "supplier_id": s.get("id", ""),
                              "supplier_name": s.get("name", "Нет данных"),
                              "article": p["article"] or article,
                              "brand": p["brand"] or "Нет данных",
                              "name": p["name"] or "Нет данных",
                              "price": p["price"], "count": cnt,
                              "stock": sr or "Нет данных",
                              "delivery": p["delivery"] or "Нет данных",
                              "delivery_start": "", "delivery_end": "",
                              "source": "Прайс e-mail",
                              "price_received_at": p["received_at"]})
        if m: main_offers += m
        if c: all_crosses += c
    main_offers = af(main_offers); all_crosses = af(all_crosses)
    return main_offers, all_crosses, errors


def offer_sort_key(o, mode="smart", ra="", rb=""):
    p = parse_price(o.get("price")); p = p if p is not None else 10**12
    d = delivery_days(o.get("delivery")); dk = d if d is not None else 9999
    s = 0 if int(o.get("count", 0) or 0) > 0 else 1
    ea = 0 if normalize_article(o.get("article")) == normalize_article(ra) else 1
    eb = 0 if rb and normalize_brand(o.get("brand")) == normalize_brand(rb) else 1
    if mode == "price": return (ea, p, dk, s)
    if mode == "delivery": return (ea, dk, p, s)
    if mode == "stock": return (ea, s, dk, p)
    return (ea, eb, s, dk, p)


def sort_offers(offers, mode="smart", ra="", rb=""):
    return sorted(list(offers or []), key=lambda o: offer_sort_key(o, mode, ra, rb))


def group_by_article(offers):
    groups = {}
    for o in offers:
        k = normalize_article(o.get("article") or "")
        if k not in groups:
            groups[k] = {"article": o.get("article") or "", "brand": o.get("brand") or "",
                         "name": o.get("name") or "", "offers": []}
        groups[k]["offers"].append(o)
        if not groups[k]["name"] and o.get("name"): groups[k]["name"] = o["name"]
        if not groups[k]["brand"] and o.get("brand"): groups[k]["brand"] = o["brand"]
    res = list(groups.values())
    for g in res:
        g["offers"].sort(key=lambda x: (parse_price(x.get("price")) if parse_price(x.get("price")) is not None else 10**12,
                                        delivery_days(x.get("delivery")) if delivery_days(x.get("delivery")) is not None else 9999))
        g["min_price"] = g["offers"][0]["price"] if g["offers"] else 0
    res.sort(key=lambda g: g["min_price"])
    return res


def top_selection(offers):
    if not offers: return [], False
    bp = sorted(offers, key=lambda o: o["price"])
    def dk(o):
        d = delivery_days(o.get("delivery"))
        return d if d is not None else 999
    bd = sorted(offers, key=lambda o: (dk(o), o["price"]))
    pk, sn = [], set()
    if bp: pk.append(bp[0]); sn.add(bp[0]["id"])
    for o in bd:
        if len(pk) >= COLLAPSED_ROWS: break
        if o["id"] in sn: continue
        pk.append(o); sn.add(o["id"])
    for o in bp:
        if len(pk) >= COLLAPSED_ROWS: break
        if o["id"] in sn: continue
        pk.append(o); sn.add(o["id"])
    return pk, len(offers) > len(pk)


# ---------- БД ----------
DB_PATH = os.path.join(DATA_DIR, "parts_manager.db")
ORDER_STATUSES = ["Новый", "Отправляется", "Отправлен", "Подтверждён поставщиком",
                  "В обработке", "Отгружен", "Завершён", "Отменён", "Ошибка отправки"]
PAYMENT_STATUSES = ["Не оплачено", "Частично оплачено", "Оплачено", "Возврат"]


def db_conn():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON")
    return c


def _table_has_column(c, t, col):
    try:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()}
        return col in cols
    except Exception: return False


def init_db():
    with db_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT NOT NULL,
          kind TEXT DEFAULT 'person', phone TEXT NOT NULL DEFAULT '', email TEXT DEFAULT '',
          company TEXT DEFAULT '', city TEXT DEFAULT '', notes TEXT DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
        CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
        CREATE TABLE IF NOT EXISTS customer_vehicles(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
          vin TEXT DEFAULT '', make TEXT DEFAULT '', model TEXT DEFAULT '', year TEXT DEFAULT '',
          engine TEXT DEFAULT '', body_class TEXT DEFAULT '', drive_type TEXT DEFAULT '',
          fuel_type TEXT DEFAULT '', nickname TEXT DEFAULT '', is_primary INTEGER DEFAULT 0,
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_cv_customer ON customer_vehicles(customer_id);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL,
          number_year INTEGER DEFAULT 0, number_seq INTEGER DEFAULT 0, customer_id TEXT DEFAULT '',
          vehicle_id TEXT DEFAULT '', supplier_id TEXT DEFAULT '', supplier_name TEXT DEFAULT '',
          channel TEXT DEFAULT '', status TEXT NOT NULL, payment_status TEXT NOT NULL,
          external_no TEXT DEFAULT '', subtotal REAL DEFAULT 0, cost REAL DEFAULT 0, profit REAL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT DEFAULT '', fingerprint TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
        CREATE TABLE IF NOT EXISTS order_items(id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
          article TEXT, brand TEXT, name TEXT, qty INTEGER DEFAULT 1, price REAL, cost REAL,
          term TEXT, supplier_article TEXT DEFAULT '',
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS order_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id TEXT NOT NULL, status TEXT, message TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS order_external_numbers(id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id TEXT NOT NULL, supplier_id TEXT DEFAULT '', supplier_name TEXT DEFAULT '',
          external_no TEXT DEFAULT '', created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_oext_order ON order_external_numbers(order_id);
        CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS financial_transactions(
          id TEXT PRIMARY KEY, order_id TEXT DEFAULT '', customer_id TEXT DEFAULT '',
          supplier_id TEXT DEFAULT '', kind TEXT NOT NULL, category TEXT NOT NULL,
          amount REAL NOT NULL, direction TEXT NOT NULL, status TEXT DEFAULT 'Проведено',
          note TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_fin_created ON financial_transactions(created_at);
        CREATE INDEX IF NOT EXISTS idx_fin_order ON financial_transactions(order_id);
        CREATE TABLE IF NOT EXISTS supplier_prices(id TEXT PRIMARY KEY, supplier_id TEXT DEFAULT '',
          supplier_name TEXT DEFAULT '', article TEXT DEFAULT '', brand TEXT DEFAULT '',
          name TEXT DEFAULT '', price REAL, currency TEXT DEFAULT 'RUB', stock TEXT DEFAULT '',
          delivery TEXT DEFAULT '', supplier_article TEXT DEFAULT '', source_file TEXT DEFAULT '',
          source_email TEXT DEFAULT '', received_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_supplier_prices_article ON supplier_prices(article);
        CREATE TABLE IF NOT EXISTS supplier_price_mail_log(id TEXT PRIMARY KEY, supplier_id TEXT DEFAULT '',
          sender TEXT DEFAULT '', subject TEXT DEFAULT '', message_id TEXT DEFAULT '',
          received_at TEXT NOT NULL, status TEXT DEFAULT '', details TEXT DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS idx_spml_message ON supplier_price_mail_log(message_id) WHERE message_id<>'';
        CREATE INDEX IF NOT EXISTS idx_spml_supplier ON supplier_price_mail_log(supplier_id, received_at);
        """)
        for col, ddl in [("kind", "TEXT DEFAULT 'person'"), ("phone", "TEXT NOT NULL DEFAULT ''"),
                         ("email", "TEXT DEFAULT ''"), ("company", "TEXT DEFAULT ''"),
                         ("city", "TEXT DEFAULT ''"), ("notes", "TEXT DEFAULT ''"),
                         ("updated_at", "TEXT DEFAULT ''")]:
            if not _table_has_column(c, "customers", col):
                try: c.execute(f"ALTER TABLE customers ADD COLUMN {col} {ddl}")
                except Exception: logging.exception("migration customers.%s", col)
        for col, ddl in [("fingerprint", "TEXT DEFAULT ''"), ("number_year", "INTEGER DEFAULT 0"),
                         ("number_seq", "INTEGER DEFAULT 0"), ("customer_id", "TEXT DEFAULT ''"),
                         ("vehicle_id", "TEXT DEFAULT ''")]:
            if not _table_has_column(c, "orders", col):
                try: c.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
                except Exception: logging.exception("migration orders.%s", col)
        try:
            c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_fp_active ON orders(fingerprint)
                         WHERE fingerprint<>'' AND status NOT IN ('Отменён','Ошибка отправки')""")
        except Exception: logging.exception("migration idx_orders_fp_active")


def next_order_number():
    y2 = datetime.now().strftime("%y")
    key = f"order_{y2}"
    with db_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO counters(name, value) VALUES(?, 0)", (key,))
        c.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (key,))
        seq = c.execute("SELECT value FROM counters WHERE name = ?", (key,)).fetchone()[0]
    return f"pm{y2}-{seq}", int(y2), int(seq)


def money(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def order_fingerprint(items, sup_id, cust_id=""):
    parts = []
    for x in sorted(items, key=lambda z: (normalize_article(z.get("article") or ""), str(z.get("brand") or ""))):
        parts.append("|".join([normalize_article(x.get("article") or ""), str(x.get("brand") or ""),
                               str(int(x.get("qty", 1) or 1)), str(parse_price(x.get("price")) or "")]))
    return hashlib.sha256((sup_id + "::" + (cust_id or "") + "::" + "||".join(parts)).encode("utf-8")).hexdigest()


def existing_order_by_fingerprint(fp):
    if not fp: return None
    with db_conn() as c:
        return c.execute("""SELECT * FROM orders WHERE fingerprint=? AND status NOT IN ('Отменён','Ошибка отправки')
                            ORDER BY created_at DESC LIMIT 1""", (fp,)).fetchone()


def create_order(items, sup, channel, customer_id="", vehicle_id=""):
    fp = order_fingerprint(items, sup.get("id", ""), customer_id)
    dup = existing_order_by_fingerprint(fp)
    if dup: return dup["id"], dup["number"], True
    oid = str(uuid.uuid4()); now = datetime.now().isoformat(timespec="seconds")
    number, y2, seq = next_order_number()
    sub = sum((money(parse_price(x.get("price"))) * max(1, int(x.get("qty", 1) or 1))) for x in items)
    try:
        with db_conn() as c:
            c.execute("""INSERT INTO orders(id, number, number_year, number_seq, customer_id, vehicle_id,
                         supplier_id, supplier_name, channel, status, payment_status, external_no, subtotal,
                         cost, profit, created_at, updated_at, error, fingerprint)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (oid, number, y2, seq, customer_id, vehicle_id, sup.get("id", ""), sup.get("name", ""),
                       channel, "Новый", "Не оплачено", "", sub, 0, sub, now, now, "", fp))
            for x in items:
                c.execute("""INSERT INTO order_items(id, order_id, article, brand, name, qty, price, cost, term, supplier_article)
                             VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (str(uuid.uuid4()), oid, x.get("article") or "—", x.get("brand") or "—",
                           x.get("name") or "—", max(1, int(x.get("qty", 1) or 1)),
                           money(parse_price(x.get("price"))), money(parse_price(x.get("cost"))),
                           x.get("term") or "—", x.get("supplier_article") or ""))
            c.execute("INSERT INTO order_events(order_id,status,message,created_at) VALUES(?,?,?,?)",
                      (oid, "Новый", "Заказ создан", now))
    except sqlite3.IntegrityError:
        dup = existing_order_by_fingerprint(fp)
        if dup: return dup["id"], dup["number"], True
        raise
    return oid, number, False


def add_external_number(order_id, sup_id, sup_name, ext_no):
    if not ext_no: return
    now = datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("""INSERT INTO order_external_numbers(order_id, supplier_id, supplier_name, external_no, created_at)
                     VALUES(?,?,?,?,?)""", (order_id, sup_id, sup_name, ext_no, now))


def order_external_numbers(order_id):
    with db_conn() as c:
        return c.execute("SELECT * FROM order_external_numbers WHERE order_id=? ORDER BY id", (order_id,)).fetchall()


def order_event(oid, status, message=""):
    now = datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, now, oid))
        c.execute("INSERT INTO order_events(order_id,status,message,created_at) VALUES(?,?,?,?)",
                  (oid, status, message, now))


def list_orders(status="", sup_id="", q="", cust_id=""):
    sql = "SELECT * FROM orders WHERE 1=1"; args = []
    if status: sql += " AND status=?"; args.append(status)
    if sup_id: sql += " AND supplier_id=?"; args.append(sup_id)
    if cust_id: sql += " AND customer_id=?"; args.append(cust_id)
    if q:
        sql += " AND (number LIKE ? OR supplier_name LIKE ? OR external_no LIKE ?)"
        args += [f"%{q}%"]*3
    sql += " ORDER BY created_at DESC LIMIT 500"
    with db_conn() as c: return c.execute(sql, args).fetchall()


def order_detail(oid):
    with db_conn() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        items = c.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
        events = c.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id DESC", (oid,)).fetchall()
        ext = c.execute("SELECT * FROM order_external_numbers WHERE order_id=? ORDER BY id", (oid,)).fetchall()
    return o, items, events, ext


def finance_summary():
    with db_conn() as c:
        ins = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE direction='in' AND status='Проведено'").fetchone()[0]
        outs = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE direction='out' AND status='Проведено'").fetchone()[0]
        revenue = c.execute("SELECT COALESCE(SUM(subtotal),0) FROM orders WHERE status NOT IN ('Отменён','Ошибка отправки')").fetchone()[0]
        cost = c.execute("SELECT COALESCE(SUM(cost),0) FROM orders WHERE status NOT IN ('Отменён','Ошибка отправки')").fetchone()[0]
        receiv = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Начисление клиенту' AND direction='in'").fetchone()[0]
        paidcust = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Оплата клиента' AND direction='in'").fetchone()[0]
        payable = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Начисление поставщику' AND direction='out'").fetchone()[0]
        paidsup = c.execute("SELECT COALESCE(SUM(amount),0) FROM financial_transactions WHERE kind='Оплата поставщику' AND direction='out'").fetchone()[0]
        other = c.execute("""SELECT COALESCE(SUM(amount),0) FROM financial_transactions
            WHERE direction='out' AND status='Проведено'
              AND kind NOT IN ('Оплата поставщику', 'Начисление поставщику', 'Закупка')""").fetchone()[0]
    gross = revenue - cost; net = gross - other
    return dict(income=ins, expense=outs, cash=ins-outs, revenue=revenue, cost=cost,
                profit=gross, other_expenses=other, net_profit=net,
                receivable=max(0, receiv-paidcust), payable=max(0, payable-paidsup))


def record_finance(order_id="", customer_id="", supplier_id="", kind="", category="",
                   amount=0, direction="in", note=""):
    tid = str(uuid.uuid4()); now = datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("""INSERT INTO financial_transactions(id, order_id, customer_id, supplier_id,
                     kind, category, amount, direction, status, note, created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (tid, order_id, customer_id, supplier_id, kind, category,
                   float(amount), direction, "Проведено", note, now))
    return tid


def list_finance(limit=200):
    with db_conn() as c:
        return c.execute("""SELECT * FROM financial_transactions ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()


def normalize_phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    if not d: return ""
    if len(d) == 11 and d[0] in "78": return "+7" + d[1:]
    if len(d) == 10: return "+7" + d
    return "+" + d


def customer(cid):
    with db_conn() as c:
        return c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()


def customer_by_phone(phone):
    np = normalize_phone(phone)
    if not np: return None
    with db_conn() as c:
        return c.execute("SELECT * FROM customers WHERE phone=?", (np,)).fetchone()


def list_customers(q=""):
    with db_conn() as c:
        if q:
            return c.execute("""SELECT * FROM customers WHERE name LIKE ? OR phone LIKE ? OR email LIKE ?
                                ORDER BY created_at DESC LIMIT 200""",
                             (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
        return c.execute("SELECT * FROM customers ORDER BY created_at DESC LIMIT 200").fetchall()


def customer_vehicles(cid):
    with db_conn() as c:
        return c.execute("SELECT * FROM customer_vehicles WHERE customer_id=? ORDER BY is_primary DESC, created_at",
                         (cid,)).fetchall()


def create_customer(name, phone, email="", company="", city="", notes="", kind="person"):
    cid = str(uuid.uuid4()); now = datetime.now().isoformat(timespec="seconds")
    with db_conn() as c:
        c.execute("""INSERT INTO customers(id, name, kind, phone, email, company, city, notes, created_at, updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (cid, name.strip(), kind, normalize_phone(phone), email.strip(),
                   company.strip(), city.strip(), notes.strip(), now, now))
    return cid


def update_customer(cid, **kw):
    now = datetime.now().isoformat(timespec="seconds")
    allowed = ("name", "kind", "phone", "email", "company", "city", "notes")
    sets, args = [], []
    for k, v in kw.items():
        if k in allowed:
            if k == "phone": v = normalize_phone(v)
            sets.append(f"{k}=?"); args.append(v)
    if not sets: return
    sets.append("updated_at=?"); args.append(now); args.append(cid)
    with db_conn() as c:
        c.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id=?", args)


def add_customer_vehicle(cid, vin="", make="", model="", year="", engine="",
                         body_class="", drive_type="", fuel_type="", nickname="", is_primary=False):
    ex = customer_vehicles(cid)
    if len(ex) >= 3: raise ValueError("У клиента уже 3 автомобиля.")
    vid = str(uuid.uuid4()); now = datetime.now().isoformat(timespec="seconds")
    if is_primary or not ex:
        is_primary = True
        with db_conn() as c:
            c.execute("UPDATE customer_vehicles SET is_primary=0 WHERE customer_id=?", (cid,))
    with db_conn() as c:
        c.execute("""INSERT INTO customer_vehicles(id, customer_id, vin, make, model, year, engine,
                     body_class, drive_type, fuel_type, nickname, is_primary, created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (vid, cid, vin.strip(), make.strip(), model.strip(), year.strip(), engine.strip(),
                   body_class.strip(), drive_type.strip(), fuel_type.strip(), nickname.strip(),
                   1 if is_primary else 0, now))
    return vid


def delete_customer_vehicle(vid):
    with db_conn() as c:
        c.execute("DELETE FROM customer_vehicles WHERE id=?", (vid,))


def set_primary_vehicle(cid, vid):
    with db_conn() as c:
        c.execute("UPDATE customer_vehicles SET is_primary=0 WHERE customer_id=?", (cid,))
        c.execute("UPDATE customer_vehicles SET is_primary=1 WHERE id=?", (vid,))


def cart_index():
    idx = {}
    for x in read("cart"):
        k = (normalize_article(x.get("article") or ""), x.get("supplier_id") or "")
        idx[k] = idx.get(k, 0) + int(x.get("qty", 1) or 1)
    return idx


def cart_index_by_article():
    idx = {}
    for x in read("cart"):
        k = normalize_article(x.get("article") or "")
        idx[k] = idx.get(k, 0) + int(x.get("qty", 1) or 1)
    return idx


def cart_item_id(article, sup_id):
    for x in read("cart"):
        if normalize_article(x.get("article") or "") == normalize_article(article) and \
           (x.get("supplier_id") or "") == (sup_id or ""):
            return x.get("id")
    return None


def backup_cart():
    try: write("cart_backup", read("cart"))
    except Exception: logging.exception("cart backup failed")


def optimize_cart(strategy="standard", allow_analogs=True):
    cart = read("cart")
    if not cart: return [], [], {}
    new_cart, report = [], []
    tb = 0.0; ta = 0.0; mda = 0
    for item in cart:
        po = parse_price(item.get("price")) or 0.0
        qty = int(item.get("qty", 1) or 1)
        do = delivery_days(item.get("term"))
        tb += po * qty
        art = item.get("article") or ""
        bo = item.get("brand") or ""
        so = item.get("supplier_name") or ""
        sio = item.get("supplier_id") or ""
        ni = dict(item)
        am, ac, _ = collect_offers(art, {"min_price": None, "max_price": None, "max_days": None,
                                          "in_stock_only": False}, sort_mode="price_delivery")
        cs = list(am)
        if allow_analogs:
            for c in ac: c["is_cross"] = True; cs.append(c)
        if not allow_analogs:
            nb = normalize_brand(bo)
            if nb:
                fl = [c for c in cs if normalize_brand(c.get("brand")) == nb]
                cs = fl if fl else []
        oof = None
        for c in cs:
            if c.get("supplier_id") == sio and normalize_article(c.get("article") or "") == normalize_article(art):
                oof = c; break
        osa = bool(oof and int(oof.get("count", 0)) > 0)
        def pb(lst):
            v = [c for c in lst if int(c.get("count", 0) or 0) > 0] or list(lst)
            if strategy == "price":
                v.sort(key=lambda c: ((parse_price(c.get("price")) if parse_price(c.get("price")) is not None else 10**12),
                                       delivery_days(c.get("delivery")) or 999))
            elif strategy == "delivery":
                v.sort(key=lambda c: (delivery_days(c.get("delivery")) or 999,
                                       (parse_price(c.get("price")) if parse_price(c.get("price")) is not None else 10**12)))
            else:
                v.sort(key=lambda c: ((parse_price(c.get("price")) if parse_price(c.get("price")) is not None else 10**12),
                                       delivery_days(c.get("delivery")) or 999))
            return v[0] if v else None
        if osa:
            best = pb(cs) or oof
            if best["price"] >= po - 0.01:
                ta += po * qty
                if do is not None: mda = max(mda, do)
                new_cart.append(ni)
                report.append({"article": art, "article_new": art, "brand": bo,
                    "old_price": po, "old_supplier": so, "old_days": do,
                    "new_price": po, "new_supplier": so, "new_days": do,
                    "changed": False, "is_cross": False, "is_forced": False, "reason": ""})
                continue
            bo_offer = best; forced = False; reason = ""
        else:
            bo_offer = pb(cs)
            if not bo_offer:
                ta += po * qty
                if do is not None: mda = max(mda, do)
                new_cart.append(ni)
                report.append({"article": art, "article_new": art, "brand": bo,
                    "old_price": po, "old_supplier": so, "old_days": do,
                    "new_price": po, "new_supplier": so, "new_days": do,
                    "changed": False, "is_cross": False, "is_forced": True,
                    "reason": "Позиция недоступна ни у одного поставщика"})
                continue
            forced = True
            reason = ("Позиция пропала у поставщика" if oof is None else "Товар выкуплен")
            reason += f" (было {fmt_price(po)} ₽ → стало {fmt_price(bo_offer['price'])} ₽)"
        np = bo_offer["price"]
        nd = delivery_days(bo_offer.get("delivery"))
        ni["price"] = np
        ni["supplier_id"] = bo_offer.get("supplier_id", ni.get("supplier_id", ""))
        ni["supplier_name"] = bo_offer.get("supplier_name", "")
        ni["term"] = bo_offer.get("delivery", "")
        ni["brand"] = bo_offer.get("brand") or bo
        ni["article"] = bo_offer.get("article") or art
        ni["partnumber"] = bo_offer.get("partnumber") or ni["article"]
        ni["name"] = bo_offer.get("name") or ni.get("name", "")
        mk = ("warehouse_id", "supplier_article", "offer_key", "delivery_key", "payer_key",
              "hash", "supplierCode", "itemKey", "code", "stock", "delivery_id", "address_id",
              "resource_id", "system_hash", "min_delivery_day", "max_delivery_day")
        ni["offer_meta"] = {k: bo_offer.get(k) for k in mk if bo_offer.get(k) is not None}
        ta += np * qty
        if nd is not None: mda = max(mda, nd)
        new_cart.append(ni)
        report.append({"article": art, "article_new": ni["article"], "brand": bo, "brand_new": ni["brand"],
            "old_price": po, "old_supplier": so, "old_days": do,
            "new_price": np, "new_supplier": bo_offer.get("supplier_name", ""), "new_days": nd,
            "changed": (po != np) or (so != bo_offer.get("supplier_name", "")) or (normalize_article(art) != normalize_article(ni["article"])),
            "is_cross": bo_offer.get("is_cross", False), "is_forced": bool(forced), "reason": reason})
    today = datetime.now()
    ld = today + timedelta(days=mda) if mda else None
    return new_cart, report, {"total_before": tb, "total_after": ta, "diff": tb - ta,
        "last_delivery_date": ld.strftime("%d.%m.%Y") if ld else "—", "max_days_after": mda}
# ---------- E-mail ----------
def last_price_mail_message_id(supplier_id):
    try:
        with db_conn() as c:
            row = c.execute("""SELECT message_id FROM supplier_price_mail_log
                               WHERE supplier_id=? AND message_id<>''
                               ORDER BY received_at DESC LIMIT 1""", (supplier_id,)).fetchone()
            return str(row[0]) if row else ""
    except Exception:
        return ""


def send_supplier_order(sup, items, settings, order_no=None, reply_to_message_id=""):
    host = str(settings.get("smtp_host") or "").strip()
    user = str(settings.get("smtp_user") or "").strip()
    password = str(settings.get("smtp_password") or "")
    from_name = str(settings.get("smtp_from_name") or APP_NAME).strip() or APP_NAME
    from_email = str(settings.get("smtp_from_email") or user).strip()
    recipient = str(sup.get("order_email") or "").strip()
    if not recipient: raise ValueError("У поставщика не указан e-mail для заказов")
    if not host: raise ValueError("Не настроен SMTP-сервер в разделе «Настройки»")
    if not from_email: raise ValueError("Не указан e-mail отправителя")
    try: port = int(settings.get("smtp_port") or 587)
    except (TypeError, ValueError): port = 587
    security = smtp_security_mode(settings.get("smtp_security"))
    order_no = order_no or "pm" + datetime.now().strftime("%y-%m%d-%H%M%S")
    company = str(settings.get("company_name") or "").strip()
    city = str(settings.get("city") or "").strip()
    total = 0.0; rows, text_rows = [], []
    for item in items:
        qty = max(1, int(item.get("qty", 1) or 1))
        price = parse_price(item.get("price"))
        lt = price * qty if price is not None else None
        if lt is not None: total += lt
        art = str(item.get("article") or "—"); br = str(item.get("brand") or "—"); nm = str(item.get("name") or "—")
        pt = f"{fmt_price(price)} ₽" if price is not None else "уточнить"
        ltxt = f"{fmt_price(lt)} ₽" if lt is not None else "уточнить"
        rows.append(f"<tr><td>{html.escape(art)}</td><td>{html.escape(br)}</td><td>{html.escape(nm)}</td><td>{qty}</td><td>{html.escape(pt)}</td><td>{html.escape(ltxt)}</td></tr>")
        text_rows.append(f"{art} | {br} | {nm} | {qty} шт. | {pt} | {ltxt}")
    total_txt = f"{fmt_price(total)} ₽"
    subject = f"Заказ {order_no} — {sup.get('name') or 'Поставщик'}"
    signature = company or from_name
    extra = f"<p><b>Компания:</b> {html.escape(company)}</p>" if company else ""
    if city: extra += f"<p><b>Город / регион:</b> {html.escape(city)}</p>"
    html_body = f"""<!doctype html><html><body style="font-family:Arial,sans-serif;color:#172033">
<h2>Заказ {html.escape(order_no)}</h2><p>Добрый день!</p>{extra}
<p><b>Размещаем заказ.</b> Просим подтвердить наличие, цену и срок.</p>
<table cellpadding="7" cellspacing="0" border="1" style="border-collapse:collapse">
<tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr>
{''.join(rows)}</table>
<p><b>Итого: {html.escape(total_txt)}</b></p>
<p>Спасибо!<br>{html.escape(signature)}</p></body></html>"""
    text_body = (f"Заказ {order_no}\n\nДобрый день!\n\n" +
                 (f"Компания: {company}\n" if company else "") +
                 (f"Город: {city}\n" if city else "") +
                 "\nАртикул | Бренд | Деталь | Кол-во | Цена | Сумма\n" + "\n".join(text_rows) +
                 f"\n\nИтого: {total_txt}\n\n{signature}\n")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = recipient
    if reply_to_message_id:
        mid = reply_to_message_id.strip()
        if not mid.startswith("<"): mid = "<" + mid
        if not mid.endswith(">"): mid = mid + ">"
        msg["In-Reply-To"] = mid; msg["References"] = mid
    msg.set_content(text_body); msg.add_alternative(html_body, subtype="html")
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            if user: server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context()); server.ehlo()
            if user: server.login(user, password)
            server.send_message(msg)
    return order_no


# ---------- API-отправка заказов ----------
def rossko_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    key1 = cred.get("key1", "").strip(); key2 = cred.get("key2", "").strip()
    if not key1 or not key2: raise ValueError("ROSSKO: не заданы KEY1/KEY2")
    delivery_id = str(cred.get("delivery_id") or "").strip()
    address_id = str(cred.get("address_id") or "").strip()
    if not delivery_id: raise ValueError("ROSSKO: не выбран delivery_id.")
    parts = []
    for x in items:
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        stock = m.get("stock") or m.get("warehouse_id") or ""
        if not stock: raise ValueError(f"ROSSKO: у позиции «{x.get('article') or '—'}» нет склада.")
        parts.append({"partnumber": x.get("article") or "", "brand": x.get("brand") or "",
                      "stock": stock, "count": max(1, int(x.get("qty", 1) or 1)),
                      "comment": x.get("comment", "")})
    env = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="https://api.rossko.ru/"><soap:Body><ns:GetCheckout>'
           f'<ns:KEY1>{xml_escape(key1)}</ns:KEY1><ns:KEY2>{xml_escape(key2)}</ns:KEY2>'
           f'<ns:delivery_id>{xml_escape(delivery_id)}</ns:delivery_id>'
           f'<ns:address_id>{xml_escape(address_id)}</ns:address_id><ns:PARTS>')
    for part in parts:
        env += '<ns:PART>'
        for k, v in part.items(): env += f'<ns:{k}>{xml_escape(str(v))}</ns:{k}>'
        env += '</ns:PART>'
    env += '</ns:PARTS></ns:GetCheckout></soap:Body></soap:Envelope>'
    req = Request(BUILTIN["rossko_checkout"], data=env.encode("utf-8"), method="POST", headers={
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": '"https://api.rossko.ru/GetCheckout"',
        "User-Agent": f"PartsManager/{VERSION}"})
    try:
        with urlopen(req, timeout=60, context=ssl.create_default_context()) as r: raw = r.read()
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"ROSSKO HTTP {e.code}: {body[:800]}")
    try: root = ET.fromstring(raw)
    except ET.ParseError: raise RuntimeError("ROSSKO: не удалось разобрать ответ")
    texts = {}
    for el in root.iter():
        t = el.tag.split("}")[-1]
        if el.text and el.text.strip(): texts.setdefault(t, []).append(el.text.strip())
    if (texts.get("success", ["false"])[0] or "").lower() not in ("true", "1", "yes"):
        raise RuntimeError("ROSSKO: " + (texts.get("message", ["заказ не принят"])[0]))
    ext = (texts.get("order_id") or texts.get("id") or texts.get("number") or texts.get("order_number") or [""])[0]
    return str(ext) if ext else "Нет данных"


def berg_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    key = (cred.get("key", "") or cred.get("api_key", "")).strip() or BUILTIN.get("berg_key_default", "").strip()
    if not key: raise ValueError("Berg: не задан API-ключ")
    base = (sup.get("endpoint") or BUILTIN["berg_base"]).rstrip("/")
    url = f"{base}/{BUILTIN['berg_version']}/ordering/place_order.json"
    berg_items = []
    for x in items:
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        rid = m.get("resource_id"); wid = m.get("warehouse_id")
        if not rid: raise ValueError(f"Berg: у позиции «{x.get('article') or '—'}» нет resource_id.")
        if not wid: raise ValueError(f"Berg: у позиции «{x.get('article') or '—'}» нет warehouse_id.")
        try: rid = int(rid); wid = int(wid)
        except Exception: raise ValueError("Berg: неверные resource_id/warehouse_id")
        berg_items.append({"resource_id": rid, "warehouse_id": wid,
                           "quantity": max(1, int(x.get("qty", 1) or 1)),
                           "comment": x.get("comment", "") or ""})
    if not berg_items: raise ValueError("Berg: пустой заказ")
    dt = int(sup.get("dispatch_type") or 2); dti = int(sup.get("dispatch_time") or 1)
    pt = int(sup.get("payment_type") or 1)
    order_obj = {"is_test": 0, "reference": order_no, "payment_type": pt,
                 "dispatch_type": dt, "dispatch_time": dti,
                 "dispatch_at": datetime.now().strftime("%Y-%m-%d"),
                 "comment": f"Заказ {order_no} от Parts Manager", "items": berg_items}
    if dt != 2:
        addr = str(sup.get("shipment_address") or "").strip()
        aid = sup.get("shipment_address_id")
        if aid: order_obj["shipment_address_id"] = int(aid)
        elif addr: order_obj["shipment_address"] = addr
        else: raise ValueError("Berg: для доставки нужен адрес или shipment_address_id.")
    comp = str(settings.get("company_name") or "").strip()
    if comp: order_obj["person"] = comp
    ph = str(settings.get("phone") or "").strip()
    if ph: order_obj["phone"] = ph
    payload = json.dumps({"force": 1, "order": order_obj}, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
        "X-Berg-API-Key": key, "User-Agent": f"PartsManager/{VERSION}"})
    try:
        with urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            raw = r.read().decode("utf-8", "replace")
    except HTTPError as e:
        try: body = e.read().decode("utf-8", "replace")
        except Exception: body = str(e)
        em = body[:800]
        try:
            obj = json.loads(body); errs = obj.get("errors") or []
            if errs: em = "; ".join(f"{x.get('code','')}: {x.get('text','')}" for x in errs)
        except Exception: pass
        raise RuntimeError(f"Berg HTTP {e.code}: {em}")
    except Exception as ex: raise RuntimeError(f"Berg: {ex}")
    try: obj = json.loads(raw)
    except json.JSONDecodeError: raise RuntimeError(f"Berg: {raw[:300]}")
    errs = obj.get("errors") or []
    if errs: raise RuntimeError("Berg: " + "; ".join(f"{x.get('code','')}: {x.get('text','')}" for x in errs))
    oid = (obj.get("order") or {}).get("id")
    if not oid: raise RuntimeError(f"Berg: заказ создан, но нет id. Ответ: {raw[:300]}")
    return str(oid)


def zappro_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    api_key = (cred.get("api_key") or "").strip()
    base_url = (cred.get("base_url") or "https://portal.zap-pro.ru").strip().rstrip("/")
    if not api_key: raise ValueError("ZAP-PRO: не задан API-ключ")
    ctx = ssl.create_default_context()
    h = {"Accept": "application/json", "User-Agent": f"PartsManager/{VERSION}"}
    try:
        req = Request(f"{base_url}/api/v1/baskets/clear?api_key={quote(api_key)}",
                      data=b"", method="POST",
                      headers={**h, "Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=30, context=ctx) as r: r.read()
    except Exception as ex: logging.warning("ZAP-PRO clear: %s", ex)
    for x in items:
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        ah = str(m.get("system_hash") or "").strip()
        if not ah: raise ValueError(f"ZAP-PRO: у позиции «{x.get('article') or '—'}» нет system_hash.")
        try: md = int(m.get("min_delivery_day") or 0)
        except (TypeError, ValueError): md = 0
        try: xd = int(m.get("max_delivery_day") or 0)
        except (TypeError, ValueError): xd = 0
        payload = json.dumps({"oem": x.get("article") or "", "make_name": x.get("brand") or "",
                              "detail_name": x.get("name") or "", "qnt": max(1, int(x.get("qty", 1) or 1)),
                              "comment": f"Заказ {order_no}", "min_delivery_day": md,
                              "max_delivery_day": xd, "api_hash": ah}, ensure_ascii=False).encode("utf-8")
        req = Request(f"{base_url}/api/v1/baskets?api_key={quote(api_key)}",
                      data=payload, method="POST",
                      headers={**h, "Content-Type": "application/json; charset=utf-8"})
        try:
            with urlopen(req, timeout=45, context=ctx) as r:
                result = json.loads(r.read().decode("utf-8", "replace"))
        except HTTPError as ex:
            body = ex.read().decode("utf-8", "replace") if hasattr(ex, "read") else str(ex)
            raise RuntimeError(f"ZAP-PRO: {x.get('article')} — HTTP {ex.code}: {body[:300]}")
        except Exception as ex: raise RuntimeError(f"ZAP-PRO: {x.get('article')}: {ex}")
        if result.get("result") != "ok":
            raise RuntimeError(f"ZAP-PRO: {x.get('article')} — {result.get('error', 'не добавлена')}")
    req = Request(f"{base_url}/api/v1/baskets/order?api_key={quote(api_key)}",
                  data=b"", method="POST",
                  headers={**h, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=60, context=ctx) as r:
            raw = r.read().decode("utf-8", "replace")
    except HTTPError as ex:
        body = ex.read().decode("utf-8", "replace") if hasattr(ex, "read") else str(ex)
        raise RuntimeError(f"ZAP-PRO: заказ — HTTP {ex.code}: {body[:300]}")
    except Exception as ex: raise RuntimeError(f"ZAP-PRO: заказ: {ex}")
    try: result = json.loads(raw)
    except json.JSONDecodeError: raise RuntimeError(f"ZAP-PRO: {raw[:300]}")
    if result.get("result") != "ok":
        raise RuntimeError(f"ZAP-PRO: {result.get('error', 'заказ не создан')}")
    return f"ZP-{order_no}"


def avd_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    login = cred.get("login", ""); password = cred.get("password", "")
    endpoint = cred.get("endpoint") or BUILTIN["avd_url"]
    if not login or not password: raise ValueError("AVD: не заданы логин/пароль")
    for x in items:
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        hv = m.get("hash") or m.get("warehouse_id")
        if not hv: raise ValueError(f"AVD: у позиции «{x.get('article') or '—'}» нет Hash.")
        body, err = avd_soap_request("InsertToBasket",
            {"login": login, "password": password, "hash": hv,
             "quantity": max(1, int(x.get("qty", 1) or 1)), "comment": order_no, "OnlyThis": True}, endpoint)
        if err: raise RuntimeError("AVD InsertToBasket: " + err)
    body, err = avd_soap_request("CreateOrder", {"login": login, "password": password, "comment": order_no}, endpoint)
    if err: raise RuntimeError("AVD CreateOrder: " + err)
    vals = re.findall(r"<(?:[^:>]+:)?(?:CreateOrderResult|OrderId|OrderNumber)>([^<]+)</", body, re.I)
    return vals[-1] if vals else "Нет данных"


def mparts_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    user = cred.get("userlogin", "").strip(); psw = cred.get("userpsw", "").strip()
    if not user or not psw: raise ValueError("MParts: не заданы userlogin/userpsw")
    base = (cred.get("base_url") or "https://v01.ru/api/devinsight").rstrip("/")
    pm = str(cred.get("paymentMethod") or "").strip()
    sm = str(cred.get("shipmentMethod") or "").strip()
    sa = str(cred.get("shipmentAddress") or "").strip()
    so = str(cred.get("shipmentOffice") or "0").strip()
    if not pm: raise ValueError("MParts: не задан paymentMethod")
    if not sm: raise ValueError("MParts: не задан shipmentMethod")
    positions = []
    for i, x in enumerate(items):
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        num = str(x.get("article") or "").strip(); br = str(x.get("brand") or "").strip()
        sc = str(m.get("supplierCode") or "").strip()
        if not num: raise ValueError(f"MParts: у позиции #{i+1} нет артикула")
        if not sc: raise ValueError(f"MParts: у позиции «{num}» нет supplierCode.")
        pos = {"number": num, "brand": br, "supplierCode": sc,
               "quantity": max(1, int(x.get("qty", 1) or 1)), "comment": f"Заказ {order_no}"}
        p = parse_price(x.get("price"))
        if p is not None: pos["price"] = p
        positions.append(pos)
    params = {"userlogin": user, "userpsw": psw, "paymentMethod": pm, "shipmentMethod": sm,
              "shipmentAddress": sa or 0, "shipmentOffice": so or 0,
              "comment": f"Заказ {order_no} от Parts Manager"}
    for i, pos in enumerate(positions):
        for k, v in pos.items(): params[f"positions[{i}][{k}]"] = v
    body = urlencode(params, doseq=True).encode("utf-8")
    req = Request(base + "/orders/instant", data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json", "User-Agent": f"PartsManager/{VERSION}"})
    try:
        with urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            raw = r.read().decode("utf-8", "replace")
    except HTTPError as e:
        try: eb = e.read().decode("utf-8", "replace")
        except Exception: eb = str(e)
        raise RuntimeError(f"MParts HTTP {e.code}: {eb[:800]}")
    except Exception as ex: raise RuntimeError(f"MParts: {ex}")
    try: obj = json.loads(raw)
    except json.JSONDecodeError: raise RuntimeError(f"MParts: {raw[:400]}")
    if not isinstance(obj, dict): raise RuntimeError(f"MParts: {raw[:400]}")
    st = obj.get("status")
    if st != 1 and st != "1":
        em = obj.get("errorMessage")
        if isinstance(em, list):
            parts = []
            for e in em:
                if isinstance(e, dict): parts.append(f"{e.get('brand','')} {e.get('number','')}: {e.get('errorMessage','')}")
                else: parts.append(str(e))
            em = "; ".join(parts)
        raise RuntimeError(f"MParts: заказ не создан. {em or 'нет деталей'}")
    orders = obj.get("orders"); oid = None
    if isinstance(orders, list) and orders:
        f = orders[0]
        if isinstance(f, dict): oid = f.get("number") or f.get("id")
    elif isinstance(orders, dict) and orders:
        fk = next(iter(orders.keys())); fv = orders[fk]
        oid = fv.get("number") if isinstance(fv, dict) else fk
        oid = oid or fk
    if not oid: raise RuntimeError(f"MParts: заказ создан, но номер не найден. {raw[:400]}")
    return str(oid)


def autoeuro_create_order(sup, items, settings, order_no):
    cred = sup.get("credentials", {}) or {}
    key = cred.get("api_key", "").strip(); dl = cred.get("delivery_key", "").strip()
    payer = cred.get("payer_key", "").strip()
    if not key or not dl or not payer: raise ValueError("AutoEuro: нужны API-ключ, delivery_key, payer_key")
    base = (cred.get("base_url") or "https://api.autoeuro.ru/api/v2/json").rstrip("/")
    stock = []
    for x in items:
        m = x.get("offer_meta") or {}
        if not isinstance(m, dict): m = {}
        offer = m.get("offer_key")
        if not offer: raise ValueError(f"AutoEuro: у позиции «{x.get('article') or '—'}» нет offer_key.")
        stock.append({"offer_key": offer, "quantity": max(1, int(x.get("qty", 1) or 1)),
                      "price": float(parse_price(x.get("price")) or 0), "comment": order_no})
    payload = json.dumps({"key": key, "delivery_key": dl, "payer_key": payer,
                          "stock_items": stock, "wait_all_goods": 1, "comment": order_no},
                         ensure_ascii=False).encode("utf-8")
    req = Request(base + "/create_order/", data=payload, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": key, "User-Agent": f"PartsManager/{VERSION}"})
    try:
        with urlopen(req, timeout=45, context=ssl.create_default_context()) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"AutoEuro HTTP {e.code}: {body[:800]}")
    data = obj.get("DATA") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        if data.get("result") is False: raise RuntimeError("AutoEuro: " + str(data.get("result_description") or "не принят"))
        if data.get("order_id") is not None: return str(data["order_id"])
    if isinstance(data, list) and data and data[0].get("order_id") is not None:
        return str(data[0]["order_id"])
    if isinstance(obj, dict) and obj.get("ERROR"):
        raise RuntimeError("AutoEuro: " + str(obj["ERROR"].get("message") or "не принят"))
    return "Нет данных"


def api_adapter_order(sup, items, settings, order_no):
    tpl = (sup.get("template") or "").lower()
    if tpl == "rossko": return rossko_create_order(sup, items, settings, order_no)
    if tpl == "berg": return berg_create_order(sup, items, settings, order_no)
    if tpl == "zappro": return zappro_create_order(sup, items, settings, order_no)
    if tpl == "avd": return avd_create_order(sup, items, settings, order_no)
    if tpl == "autoeuro": return autoeuro_create_order(sup, items, settings, order_no)
    if tpl == "mparts": return mparts_create_order(sup, items, settings, order_no)
    raise ValueError(f"Для шаблона «{tpl or 'custom'}» нет API-отправки заказов. Используйте e-mail.")


def order_channel(sup):
    tpl = (sup.get("template") or "").lower()
    email_addr = str(sup.get("order_email") or "").strip()
    mode = str(sup.get("order_channel") or "auto").lower().strip()
    API_ORDER_TEMPLATES = ("berg", "zappro", "rossko", "avd", "autoeuro", "mparts")
    api_supported = tpl in API_ORDER_TEMPLATES
    if mode == "email":
        if not email_addr: raise ValueError("Выбран e-mail, но адрес не задан")
        return "E-mail"
    if mode == "api":
        if not api_supported: raise ValueError(f"Шаблон «{tpl}» не поддерживает API-заказ.")
        return "API"
    if api_supported:
        cred = sup.get("credentials", {}) or {}
        api_ready = False
        if tpl == "berg" and cred.get("key"): api_ready = True
        elif tpl == "zappro" and cred.get("api_key"): api_ready = True
        elif tpl == "rossko" and cred.get("key1") and cred.get("key2") and cred.get("delivery_id"): api_ready = True
        elif tpl == "avd" and cred.get("login") and cred.get("password"): api_ready = True
        elif tpl == "autoeuro" and cred.get("api_key") and cred.get("delivery_key") and cred.get("payer_key"): api_ready = True
        elif tpl == "mparts" and cred.get("userlogin") and cred.get("userpsw") and cred.get("paymentMethod") and cred.get("shipmentMethod"): api_ready = True
        if api_ready: return "API"
    if email_addr: return "E-mail"
    if api_supported: raise ValueError("Не хватает данных для API-заказа. Заполните ключи или укажите e-mail.")
    raise ValueError(f"У поставщика «{sup.get('name') or tpl}» нет API-отправки. Укажите e-mail.")


def send_order_to_supplier(sup, items, settings, order_no):
    ch = order_channel(sup)
    if ch == "API":
        return "API", api_adapter_order(sup, items, settings, order_no)
    ext = send_supplier_order(sup, items, settings, order_no=order_no,
                               reply_to_message_id=last_price_mail_message_id(sup.get("id", "")))
    return "E-mail", ext


# ---------- Прайсы (IMAP) ----------
def parse_xlsx_rows(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("m:si", ns):
                shared.append("".join((t.text or "") for t in si.findall(".//m:t", ns)))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rel = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relmap = {r.attrib.get("Id"): r.attrib.get("Target") for r in rel}
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
              "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
        sheets = wb.find("m:sheets", ns)
        sheet = sheets[0] if sheets is not None and len(sheets) else None
        if sheet is None: return []
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = relmap.get(rid, "worksheets/sheet1.xml")
        target = target.lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        try: sheet_xml = z.read(target)
        except KeyError: return []
        root = ET.fromstring(sheet_xml)
        rows = []
        for row in root.findall(".//m:row", ns):
            vals = []
            for cell in row.findall("m:c", ns):
                typ = cell.attrib.get("t")
                v = cell.find("m:v", ns)
                val = (v.text if v is not None else "") or ""
                if typ == "s" and val.isdigit() and int(val) < len(shared): val = shared[int(val)]
                vals.append(val)
            rows.append(vals)
        return rows


def parse_price_file(data, filename):
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".xlsx": return parse_xlsx_rows(data)
    text = data.decode("utf-8-sig", "replace")
    sample = text[:4096]
    try: dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except Exception:
        dialect = csv.excel; dialect.delimiter = ";" if ";" in sample else ","
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def normalize_price_rows(rows):
    if not rows: return []
    headers = [str(x or "").strip().lower() for x in rows[0]]
    aliases = {
        "article": ["article", "артикул", "код", "номер", "part", "part number", "sku"],
        "brand": ["brand", "бренд", "марка"],
        "name": ["name", "название", "наименование", "detail", "деталь"],
        "price": ["price", "цена", "розница", "закупка", "cost"],
        "stock": ["stock", "остаток", "наличие", "количество"],
        "delivery": ["delivery", "срок", "доставка"],
        "supplier_article": ["supplier_article", "артикул поставщика"],
    }
    idx = {}
    for key, names in aliases.items():
        for i, h in enumerate(headers):
            if h in names or any(n in h for n in names): idx[key] = i; break
    out = []
    if "article" not in idx: return out
    for row in rows[1:]:
        def gv(k):
            i = idx.get(k)
            return str(row[i]).strip() if i is not None and i < len(row) else ""
        art = gv("article")
        if not art: continue
        raw = gv("price").replace(" ", "").replace(",", ".")
        try: price = float(re.sub(r"[^0-9.\-]", "", raw)) if raw else None
        except Exception: price = None
        out.append({"article": art, "brand": gv("brand"), "name": gv("name"),
                    "price": price, "stock": gv("stock"), "delivery": gv("delivery"),
                    "supplier_article": gv("supplier_article")})
    return out


def fetch_supplier_price_emails():
    st = read("settings")
    host = str(st.get("imap_host") or "").strip(); user = str(st.get("imap_user") or "").strip()
    password = str(st.get("imap_password") or "")
    folder = str(st.get("imap_folder") or "INBOX").strip() or "INBOX"
    if not host or not user or not password: raise ValueError("Не настроен IMAP")
    try: port = int(st.get("imap_port") or 993)
    except Exception: port = 993
    security = str(st.get("imap_security") or "ssl").lower().strip()
    if security == "ssl": M = imaplib.IMAP4_SSL(host, port)
    else:
        M = imaplib.IMAP4(host, port)
        if security == "starttls": M.starttls()
    processed = 0; imported = 0; messages = []
    try:
        M.login(user, password); M.select(folder)
        typ, data = M.search(None, "UNSEEN")
        for num in (data[0].split() if data and data[0] else []):
            try:
                typ, msgdata = M.fetch(num, "(RFC822)")
                raw = msgdata[0][1]
                msg = email.message_from_bytes(raw)
                sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
                subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
                message_id = str(msg.get("Message-ID", "")).strip()
                if message_id:
                    with db_conn() as c:
                        ex = c.execute("SELECT 1 FROM supplier_price_mail_log WHERE message_id=? LIMIT 1", (message_id,)).fetchone()
                    if ex:
                        M.store(num, '+FLAGS', '\\Seen'); processed += 1; continue
                suppliers = read("suppliers")
                sup = next((x for x in suppliers if sender in split_emails(x.get("price_email"))), None)
                if not sup:
                    with db_conn() as c:
                        c.execute("INSERT INTO supplier_price_mail_log VALUES(?,?,?,?,?,?,?,?)",
                                  (str(uuid.uuid4()), "", sender, subject, message_id,
                                   datetime.now().isoformat(timespec="seconds"), "ignored", "Поставщик не определён"))
                    M.store(num, '+FLAGS', '\\Seen'); processed += 1; continue
                im = 0; files = []
                for part in msg.walk():
                    fn = part.get_filename()
                    if not fn: continue
                    fn = str(email.header.make_header(email.header.decode_header(fn)))
                    ext = os.path.splitext(fn.lower())[1]
                    if ext not in (".csv", ".txt", ".xlsx"): continue
                    data_bytes = part.get_payload(decode=True) or b""
                    try: rows = normalize_price_rows(parse_price_file(data_bytes, fn))
                    except Exception as ex:
                        messages.append(f"{sup.get('name')}: {fn}: ошибка: {ex}"); continue
                    with db_conn() as c:
                        for r in rows:
                            c.execute("INSERT INTO supplier_prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (str(uuid.uuid4()), sup.get("id", ""), sup.get("name", "—"),
                                       r["article"], r["brand"], r["name"], r["price"], "RUB",
                                       r["stock"], r["delivery"], r["supplier_article"], fn, sender,
                                       datetime.now().isoformat(timespec="seconds")))
                    imported += len(rows); im += len(rows); files.append(fn)
                status = "imported" if im else "empty"
                details = f"{im} позиций из {', '.join(files) if files else 'письма'}".strip()
                with db_conn() as c:
                    c.execute("INSERT INTO supplier_price_mail_log VALUES(?,?,?,?,?,?,?,?)",
                              (str(uuid.uuid4()), sup.get("id", ""), sender, subject, message_id,
                               datetime.now().isoformat(timespec="seconds"), status, details))
                M.store(num, '+FLAGS', '\\Seen'); processed += 1
                messages.append(f"{sup.get('name')}: {details}")
            except Exception as ex:
                messages.append(f"Ошибка: {ex}")
                try: M.store(num, '+FLAGS', '\\Seen')
                except Exception: pass
                processed += 1
    finally:
        try: M.logout()
        except Exception: pass
    return processed, imported, messages


# ---------- SVG-иконки ----------
ICONS = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    "cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    "clipboard": '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6"/><path d="M9 16h6"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "store": '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/><path d="M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7"/>',
    "package": '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "wallet": '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "pencil": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "printer": '<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect width="12" height="8" x="6" y="14"/>',
    "sparkles": '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "truck": '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
}


def icon(name, size=16, cls=""):
    if name not in ICONS: return ""
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            f'class="{cls}" aria-hidden="true">{ICONS[name]}</svg>')


CSS = r"""
:root{--bg:#f5f7fb;--surface:#ffffff;--surface-2:#f8fafc;--text:#172033;--muted:#667085;--line:#e4e7ec;--line-2:#d0d5dd;--primary-50:#eff6ff;--primary-100:#dbeafe;--primary-500:#3b82f6;--primary-600:#2563eb;--primary-700:#1d4ed8;--success-50:#ecfdf3;--success-500:#22c55e;--success-700:#15803d;--warning-50:#fffaeb;--warning-500:#f59e0b;--warning-700:#b45309;--danger-50:#fef2f2;--danger-500:#ef4444;--danger-700:#b91c1c;--violet-50:#ede9fe;--violet-700:#5b21b6;--shadow-sm:0 1px 2px rgba(16,24,40,.05);--shadow-md:0 4px 12px rgba(16,24,40,.08);--shadow-lg:0 12px 32px rgba(16,24,40,.12);--radius-sm:6px;--radius-md:10px;--radius-lg:14px}
[data-theme="dark"]{--bg:#0f172a;--surface:#1e293b;--surface-2:#334155;--text:#f1f5f9;--muted:#94a3b8;--line:#334155;--line-2:#475569;--primary-50:#1e3a8a;--primary-100:#1e40af;--shadow-sm:0 1px 2px rgba(0,0,0,.3);--shadow-md:0 4px 12px rgba(0,0,0,.4);--shadow-lg:0 12px 32px rgba(0,0,0,.5)}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Inter,-apple-system,'Segoe UI',Arial,sans-serif}
a{color:var(--primary-600);text-decoration:none}a:hover{text-decoration:underline}
.topbar{background:var(--surface);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:50;box-shadow:var(--shadow-sm)}
.topbar-inner{max-width:1400px;margin:0 auto;min-height:64px;padding:8px 24px;display:flex;align-items:center;gap:24px}
.brand-wrap{display:flex;align-items:center;gap:10px;flex:0 0 auto}
.brand{font-size:18px;font-weight:800;letter-spacing:-.3px}
.ver{font-size:11px;font-weight:700;color:var(--muted);background:var(--surface-2);border:1px solid var(--line);border-radius:999px;padding:2px 8px}
.main-nav{display:flex;align-items:center;gap:4px;flex:1}
.main-nav a{display:inline-flex;align-items:center;gap:7px;color:var(--muted);text-decoration:none;padding:9px 14px;border-radius:var(--radius-md);font-weight:600;white-space:nowrap}
.main-nav a:hover{background:var(--surface-2);color:var(--text);text-decoration:none}
.main-nav a.active{background:var(--primary-50);color:var(--primary-700)}
.main-nav a.cart-link{background:var(--primary-600);color:#fff}
.main-nav a.cart-link:hover{background:var(--primary-700)}
.main-nav .nav-badge{display:inline-block;min-width:18px;height:18px;line-height:18px;padding:0 5px;border-radius:99px;background:rgba(255,255,255,.25);font-size:11px;font-weight:800;text-align:center}
.header-actions{display:flex;align-items:center;gap:8px}
.icon-btn{display:inline-flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:var(--radius-md);background:transparent;border:1px solid var(--line);color:var(--muted);cursor:pointer;padding:0}
.icon-btn:hover{background:var(--surface-2);color:var(--text)}
main{max-width:1400px;margin:24px auto;padding:0 24px 60px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:22px;margin-bottom:18px;box-shadow:var(--shadow-sm)}
.card-title{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:6px}
h1{font-size:24px;line-height:1.2;letter-spacing:-.5px;margin:0 0 6px}
h2{font-size:18px;margin:0 0 12px}h3{font-size:15px;margin:0 0 8px}
.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.field{display:flex;flex-direction:column;gap:6px}
.field label{font-weight:700;font-size:13px}
.field small{color:var(--muted);line-height:1.35;font-size:12px}
.field .required{color:var(--danger-500);margin-left:2px}
input,select,textarea{font:inherit;width:100%;border:1px solid var(--line-2);border-radius:var(--radius-md);padding:10px 12px;background:var(--surface);color:var(--text);outline:none;transition:border .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{border-color:var(--primary-500);box-shadow:0 0 0 3px rgba(59,130,246,.15)}
textarea{min-height:95px;resize:vertical}select{cursor:pointer}
button,.btn{font:inherit;font-weight:700;border:0;border-radius:var(--radius-md);padding:10px 16px;background:var(--primary-600);color:#fff;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px;cursor:pointer;transition:background .15s,box-shadow .15s}
button:hover,.btn:hover{background:var(--primary-700);box-shadow:var(--shadow-sm);text-decoration:none}
button:active,.btn:active{transform:translateY(1px)}
.btn--secondary{background:var(--surface-2);color:var(--text);border:1px solid var(--line)}
.btn--secondary:hover{background:var(--line)}
.btn--success{background:var(--success-500)}.btn--success:hover{background:var(--success-700)}
.btn--danger{background:var(--danger-500)}.btn--danger:hover{background:var(--danger-700)}
.btn--warning{background:var(--warning-500)}.btn--warning:hover{background:var(--warning-700)}
.btn--sm{padding:6px 12px;font-size:13px}
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;background:var(--surface-2);color:var(--muted)}
.badge--new{background:var(--primary-50);color:var(--primary-700)}
.badge--progress{background:var(--warning-50);color:var(--warning-700)}
.badge--done{background:var(--success-50);color:var(--success-700)}
.badge--error{background:var(--danger-50);color:var(--danger-700)}
.badge--violet{background:var(--violet-50);color:var(--violet-700)}
.note,.ok,.warn,.err{padding:12px 14px;border-radius:var(--radius-md);margin:11px 0;border:1px solid transparent;display:flex;align-items:flex-start;gap:10px}
.note{background:var(--primary-50);color:var(--primary-700);border-color:var(--primary-100)}
.ok{background:var(--success-50);color:var(--success-700)}
.warn{background:var(--warning-50);color:var(--warning-700)}
.err{background:var(--danger-50);color:var(--danger-700)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:separate;border-spacing:0}
th,td{padding:12px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;background:var(--surface-2);position:sticky;top:0;z-index:1}
tbody tr:nth-child(even){background:var(--surface-2)}
tbody tr:hover{background:var(--primary-50)}
[data-theme="dark"] tbody tr:hover{background:var(--surface-2)}
.price{font-weight:800;font-size:15px}
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:18px;box-shadow:var(--shadow-sm)}
.stat .label{font-size:12px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.stat .value{font-size:24px;font-weight:800;margin-top:6px;letter-spacing:-.5px}
.stat--primary .value{color:var(--primary-600)}.stat--warning .value{color:var(--warning-700)}
.stat--success .value{color:var(--success-700)}.stat--danger .value{color:var(--danger-700)}
.search-hero{background:linear-gradient(135deg,var(--primary-600),var(--primary-700));color:#fff;border-radius:var(--radius-lg);padding:28px;margin-bottom:18px}
.search-hero h2{color:#fff;margin:0 0 4px;font-size:22px}
.search-hero p{color:rgba(255,255,255,.85);margin:0 0 18px}
.search-hero form{display:flex;gap:10px;flex-wrap:wrap}
.search-hero input[type=text]{flex:1;min-width:240px;padding:14px 16px;font-size:15px;border:0}
.search-hero button{padding:14px 24px;font-size:15px}
.art-block{border:1px solid var(--line);border-radius:var(--radius-md);margin-bottom:16px;overflow:hidden;background:var(--surface)}
.art-head{background:var(--surface-2);padding:12px 16px;border-bottom:1px solid var(--line)}
.art-head .art-line{font-size:15px;font-weight:800}
.art-head .art-line .art-brand{color:var(--violet-700)}
.art-head .art-name{color:var(--muted);font-size:13px;margin-top:2px}
.art-more{padding:10px 16px;background:var(--surface-2);border-top:1px solid var(--line);font-size:13px}
.cross-tag{display:inline-block;background:var(--violet-50);color:var(--violet-700);font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;margin-left:6px}
.cart-btn{display:inline-flex;align-items:center;justify-content:center;padding:6px 10px;background:var(--primary-600);color:#fff;border-radius:var(--radius-sm);text-decoration:none;font-weight:700;border:0;cursor:pointer;font-size:14px;line-height:1}
.cart-btn:hover{background:var(--primary-700);text-decoration:none}
.cart-btn.in-cart{background:var(--success-50);color:var(--success-700);border:1px solid var(--success-500)}
.cart-in-row{background:var(--success-50)!important}
[data-theme="dark"] .cart-in-row{background:rgba(34,197,94,.1)!important}
.qty-pm{display:inline-flex;align-items:center;gap:6px;font-weight:700}
.qty-pm button{width:28px;height:28px;line-height:1;padding:0;background:var(--surface-2);color:var(--text);border-radius:var(--radius-sm);border:1px solid var(--line);cursor:pointer;font-weight:800;font-size:14px}
.qty-pm button:hover{background:var(--line)}
.qty-pm .num{min-width:24px;text-align:center}
.order-number{font:700 15px 'JetBrains Mono',Consolas,monospace;color:var(--primary-700);cursor:help;border-bottom:1px dashed var(--primary-500)}
[data-tooltip]{position:relative;cursor:help}
[data-tooltip]:hover::after{content:attr(data-tooltip);position:absolute;bottom:100%;left:50%;transform:translateX(-50%);background:#111827;color:#fff;padding:8px 12px;border-radius:var(--radius-sm);font-size:12px;font-weight:500;line-height:1.5;white-space:pre;z-index:100;margin-bottom:6px;box-shadow:var(--shadow-lg)}
.flash{position:fixed;top:84px;left:50%;transform:translateX(-50%);background:var(--success-500);color:#fff;padding:12px 20px;border-radius:var(--radius-md);box-shadow:var(--shadow-lg);z-index:100;display:flex;gap:14px;align-items:center;font-weight:600}
.opt-radio{display:flex;gap:12px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.opt-radio label{display:inline-flex;gap:8px;align-items:center;padding:12px 16px;background:var(--surface-2);border:2px solid var(--line);border-radius:var(--radius-md);cursor:pointer;font-weight:600}
.opt-radio label:hover{border-color:var(--primary-500)}
.opt-radio input{width:auto}
.saved{color:var(--success-500);font-weight:800}.loss{color:var(--danger-500);font-weight:800}
.steps{display:flex;gap:8px;margin-bottom:16px;font-size:13px}
.steps span{padding:6px 14px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-weight:600}
.steps span.active{background:var(--primary-600);color:#fff}
.steps span.done{background:var(--success-50);color:var(--success-700)}
.tile{display:block;background:var(--surface);border:2px solid var(--line);border-radius:var(--radius-lg);padding:18px;text-decoration:none;color:var(--text);transition:border .15s,box-shadow .15s;margin-bottom:10px;cursor:pointer}
.tile:hover{border-color:var(--primary-500);box-shadow:var(--shadow-md);text-decoration:none}
.tile.selected{border-color:var(--primary-600);background:var(--primary-50)}
.tile .nm{font-size:16px;font-weight:800;display:block}
.tile .hint{color:var(--muted);font-size:13px;margin-top:4px}
.forced-icon{display:inline-block;margin-left:6px;color:var(--warning-500);font-weight:900;cursor:help}
.forced-row{background:var(--warning-50)!important}
.row-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.row-actions form{display:inline}
.xml-box{background:#0f172a;color:#a5f3fc;padding:15px;border-radius:var(--radius-md);font:12px 'JetBrains Mono',Consolas,monospace;white-space:pre-wrap;max-height:500px;overflow:auto;word-break:break-all}
:focus-visible{outline:3px solid var(--primary-100);outline-offset:2px}
button:disabled{opacity:.55;cursor:not-allowed}
.toolbar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:16px}
.toolbar--top{margin-top:0;margin-bottom:16px}
.mt-2{margin-top:8px}.mt-3{margin-top:12px}.mt-4{margin-top:16px}
.vehicle-card{display:flex;justify-content:space-between;align-items:flex-start;padding:14px 16px;background:var(--surface-2);border-radius:var(--radius-md);margin-bottom:8px}
.vehicle-card .vname{font-weight:800;font-size:15px}
.vehicle-card .vinfo{font-size:12px;color:var(--muted);margin-top:2px}
.customer-header{display:flex;gap:24px;align-items:flex-start}
.customer-avatar{width:60px;height:60px;border-radius:50%;background:var(--primary-50);color:var(--primary-700);display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:800;flex:0 0 auto}
"""

JS = r"""
(function () {
  function applyTheme() {
    var stored = localStorage.getItem('pm_theme') || 'auto';
    var theme = stored;
    if (stored === 'auto') theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
  }
  applyTheme();
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    e.preventDefault();
    var cur = document.documentElement.getAttribute('data-theme') || 'light';
    var next = cur === 'dark' ? 'light' : 'dark';
    localStorage.setItem('pm_theme', next);
    document.documentElement.setAttribute('data-theme', next);
    location.reload();
  });
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.flash').forEach(function (el) {
      setTimeout(function () {
        el.style.transition = 'opacity .5s'; el.style.opacity = '0';
        setTimeout(function () { el.remove(); }, 500);
      }, 3000);
    });
  });
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();
"""


def theme_icon_svg():
    return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>'


def nav_link(href, label, icon_name, active=False, badge=None, cls=""):
    badge_html = f'<span class="nav-badge">{badge}</span>' if badge else ""
    active_cls = " active" if active else ""
    return f'<a href="{href}" class="{cls}{active_cls}">{icon(icon_name, 16)}{esc(label)}{badge_html}</a>'


def layout(title, body, current="", extra_head="", extra_script=""):
    cart_items = read("cart")
    cart_qty = sum(int(x.get("qty", 1) or 1) for x in cart_items) if cart_items else 0
    nav = "".join([
        nav_link("/", "Работа", "search", current == "home"),
        nav_link("/orders", "Заказы", "clipboard", current == "orders"),
        nav_link("/customers", "Клиенты", "users", current == "customers"),
        nav_link("/cart", "Корзина", "cart", current == "cart",
                 badge=cart_qty if cart_qty else None, cls="cart-link"),
    ])
    return f"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Parts Manager</title>
<style>{CSS}</style>{extra_head}
</head><body>
<header class="topbar"><div class="topbar-inner">
<a class="brand-wrap" href="/" style="text-decoration:none;color:inherit" title="На главную">
  <div class="brand">Parts Manager</div>
  <span class="ver">v{VERSION}</span>
</a>
<nav class="main-nav">{nav}</nav>
<div class="header-actions">
  <a class="icon-btn" href="/finance" title="Финансы">{icon("wallet", 18)}</a>
  <a class="icon-btn" href="/suppliers" title="Поставщики">{icon("store", 18)}</a>
  <a class="icon-btn" href="/prices" title="Прайсы">{icon("package", 18)}</a>
  <a class="icon-btn" href="/settings" title="Настройки">{icon("settings", 18)}</a>
  <button type="button" class="icon-btn" data-theme-toggle title="Тема">{theme_icon_svg()}</button>
</div>
</div></header>
<main>{body}</main>
<script>{JS}</script>{extra_script}
</body></html>"""


def steps_html(current):
    def cls(n):
        if n < current: return "done"
        if n == current: return "active"
        return ""
    return f'<div class="steps"><span class="{cls(1)}">1. Название</span><span class="{cls(2)}">2. Способ</span><span class="{cls(3)}">3. Данные</span></div>'


def postdata(h):
    n = int(h.headers.get("Content-Length", "0"))
    return parse_qs(h.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)


def supplier(sid):
    return next((x for x in read("suppliers") if x.get("id") == sid), None)


def render_cart_cell(o, cart_by_supplier, cart_by_article):
    key = (normalize_article(o.get("article") or ""), o.get("supplier_id") or "")
    art_key = normalize_article(o.get("article") or "")
    qty = cart_by_supplier.get(key, 0)
    if qty > 0:
        item_id = cart_item_id(o.get("article"), o.get("supplier_id"))
        return (f'<div class="qty-pm">'
                f'<form method="post" action="/cart_dec" style="display:inline">'
                f'<input type="hidden" name="id" value="{esc(item_id or "")}">'
                f'<button title="Убрать">−</button></form>'
                f'<span class="num">{qty}</span>'
                f'<form method="post" action="/cart_inc" style="display:inline">'
                f'<input type="hidden" name="id" value="{esc(item_id or "")}">'
                f'<button title="Добавить">+</button></form>'
                f'</div>')
    if art_key and art_key in cart_by_article:
        return f'<span class="cart-btn in-cart" title="Уже в корзине: {cart_by_article[art_key]} шт.">✓ · {cart_by_article[art_key]}</span>'
    meta = {k: o.get(k) for k in ("warehouse_id","supplier_article","offer_key","delivery_key","payer_key",
                                   "hash","supplierCode","itemKey","code","stock","delivery_id","address_id",
                                   "resource_id","system_hash","min_delivery_day","max_delivery_day") if o.get(k) is not None}
    return (f'<form method="post" action="/add_cart" style="display:inline">'
            f'<input type="hidden" name="supplier_id" value="{esc(o.get("supplier_id",""))}">'
            f'<input type="hidden" name="article" value="{esc(o.get("article",""))}">'
            f'<input type="hidden" name="brand" value="{esc(o.get("brand",""))}">'
            f'<input type="hidden" name="name" value="{esc(o.get("name",""))}">'
            f'<input type="hidden" name="price" value="{esc(o.get("price",0))}">'
            f'<input type="hidden" name="term" value="{esc(o.get("delivery",""))}">'
            f'<input type="hidden" name="offer_meta" value="{esc(json.dumps(meta, ensure_ascii=False))}">'
            f'<input type="hidden" name="qty" value="1">'
            f'<button class="cart-btn" title="В корзину">{icon("cart", 14)}</button>'
            f'</form>')


def render_offer_rows(offers, cart_by_supplier, cart_by_article):
    rows = ""
    for o in offers:
        term = human_delivery(o.get("delivery"), o.get("delivery_start", ""))
        wh_text = esc(o.get("warehouse", "") or "—")
        if len(wh_text) > 60: wh_text = wh_text[:60] + "…"
        count = o.get("count", 0); mult = o.get("multiplicity", 1)
        count_txt = f"{count} шт." + (f" ×{mult}" if mult > 1 else "")
        stock_badge = ("<span class='badge badge--done'>в наличии</span>" if count > 0
                       else "<span class='badge badge--error'>под заказ</span>")
        key = (normalize_article(o.get("article") or ""), o.get("supplier_id") or "")
        in_cart = cart_by_supplier.get(key, 0) > 0
        rows += f"""<tr{' class="cart-in-row"' if in_cart else ''}>
<td><b>{esc(o.get('supplier_name','—'))}</b>{('<div class="muted">'+esc(o.get('source'))+'</div>') if o.get('source') else ''}</td>
<td>{esc(term)}</td>
<td>{wh_text}</td>
<td>{count_txt} {stock_badge}</td>
<td class="price">{fmt_price(o['price'])} ₽</td>
<td>{render_cart_cell(o, cart_by_supplier, cart_by_article)}</td>
</tr>"""
    return rows


def render_grouped_results(main_offers, crosses, cbs, cba, sort_mode="smart", cross_sort_mode="price",
                           requested_article="", requested_brand="", filters=None):
    parts = []
    filters = filters or {}
    if main_offers:
        ms = sort_offers(main_offers, sort_mode, requested_article, requested_brand)
        mt, mhm = top_selection(ms)
        mr = render_offer_rows(mt, cbs, cba)
        art = mt[0].get("article", "") if mt else ""
        brand = mt[0].get("brand", "") if mt else ""
        name = mt[0].get("name", "") if mt else ""
        mh = ""
        if mhm:
            mh = (f'<div class="art-more">Показать все предложения (ещё {len(ms) - len(mt)}) '
                  f'<a href="/expand?article={quote(art)}&group=main&min_price={quote(str(filters.get("min_price") or ""))}&max_price={quote(str(filters.get("max_price") or ""))}&max_days={quote(str(filters.get("max_days") or ""))}&in_stock={"1" if filters.get("in_stock_only") else ""}">развернуть</a></div>')
        parts.append(f"""<div class="art-block">
<div class="art-head">
  <div class="art-line">{esc(art)} <span class="art-brand">· {esc(brand)}</span></div>
  <div class="art-name">{esc(name)}</div>
</div>
<div class="table-wrap"><table>
<tr><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{mr}
</table></div>{mh}</div>""")
    else:
        parts.append("<div class='card'><div class='muted'>По запрошенному артикулу предложений нет.</div></div>")
    if crosses:
        groups = group_by_article(crosses)
        def gk(g):
            d = delivery_days(g["offers"][0].get("delivery")) if g["offers"] else 999
            return (d if d is not None else 999, g["min_price"])
        if cross_sort_mode == "price": groups.sort(key=lambda g: g["min_price"])
        elif cross_sort_mode == "delivery": groups.sort(key=gk)
        else: groups.sort(key=lambda g: (g["min_price"], gk(g)[0]))
        blocks = []
        for g in groups:
            t, hm = top_selection(g["offers"])
            r = render_offer_rows(t, cbs, cba)
            mo = ""
            if hm:
                mo = (f'<div class="art-more">Показать все ({len(g["offers"]) - len(t)}) '
                      f'<a href="/expand?article={quote(g["article"])}&group=cross&min_price={quote(str(filters.get("min_price") or ""))}&max_price={quote(str(filters.get("max_price") or ""))}&max_days={quote(str(filters.get("max_days") or ""))}&in_stock={"1" if filters.get("in_stock_only") else ""}">развернуть</a></div>')
            blocks.append(f"""<div class="art-block">
<div class="art-head">
  <div class="art-line">{esc(g['article'])} <span class="art-brand">· {esc(g['brand'])}</span><span class="cross-tag">аналог</span></div>
  <div class="art-name">{esc(g['name'])}</div>
</div>
<div class="table-wrap"><table>
<tr><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{r}
</table></div>{mo}</div>""")
        parts.append("<div class='card'><h2>Аналоги</h2>" + "".join(blocks) + "</div>")
    return "".join(parts)
class H(BaseHTTPRequestHandler):
    server_version = "PartsManager"
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
        try: self._do_GET()
        except Exception as e:
            logging.exception("GET error")
            self.out(layout("Ошибка", f"<div class='card err'>Внутренняя ошибка: {esc(e)}</div>"), 500)
    def _do_GET(self):
        u = urlparse(self.path); path, q = u.path, parse_qs(u.query)
        if path == "/": self._page_home(q); return
        if path == "/expand": self._page_expand(q); return
        if path == "/suppliers": self._page_suppliers(); return
        if path == "/wizard1": self._page_wizard1(q); return
        if path == "/wizard2": self._page_wizard2(q); return
        if path == "/wizard3": self._page_wizard3(q); return
        if path == "/supplier": self._page_supplier_edit(q); return
        if path == "/orders": self._page_orders(q); return
        if path == "/order": self._page_order(q); return
        if path == "/order_print": self._page_order_print(q); return
        if path == "/prices": self._page_prices(q); return
        if path == "/finance": self._page_finance(); return
        if path == "/settings": self._page_settings(); return
        if path == "/update": self._page_update(); return
        if path == "/about": self._page_about(); return
        if path == "/cart": self._page_cart(); return
        if path == "/cart_optimize": self._page_cart_optimize(); return
        if path == "/checkout": self._page_checkout(); return
        if path == "/customers": self._page_customers(q); return
        if path == "/customer": self._page_customer(q); return
        if path == "/customer_new": self._page_customer_new(q); return
        self.out(layout("404", "<div class='card'><h1>404</h1><p>Страница не найдена.</p><a class='btn' href='/'>На главную</a></div>"), 404)
    def do_POST(self):
        try: self._do_POST()
        except Exception as e:
            logging.exception("POST error")
            self.out(layout("Ошибка", f"<div class='card err'>Внутренняя ошибка: {esc(e)}</div>"), 500)
    def _do_POST(self):
        u = urlparse(self.path); path = u.path; d = postdata(self)
        if path == "/add_cart": self._post_add_cart(d); return
        if path == "/cart_inc": self._post_cart_inc(d); return
        if path == "/cart_dec": self._post_cart_dec(d); return
        if path == "/remove_cart":
            cid = d.get("id", [""])[0]
            write("cart", [x for x in read("cart") if x.get("id") != cid])
            self.red("/cart"); return
        if path == "/clear_cart": write("cart", []); self.red("/cart"); return
        if path == "/cart_optimize_result": self._post_cart_optimize_result(d); return
        if path == "/cart_optimize_apply": self._post_cart_optimize_apply(); return
        if path == "/wizard1":
            name = d.get("name", [""])[0].strip()
            if not name: self.red("/wizard1"); return
            self.red(f"/wizard2?name={quote(name)}"); return
        if path == "/wizard2":
            name = d.get("name", [""])[0].strip(); mode = d.get("mode", [""])[0]
            if not name or (mode != "email" and mode not in API_TYPES):
                self.red(f"/wizard2?name={quote(name)}"); return
            self.red(f"/wizard3?name={quote(name)}&tpl={quote(mode)}"); return
        if path == "/wizard_save_test": self._post_wizard_save_test(d); return
        if path == "/update_supplier": self._post_update_supplier(d); return
        if path == "/delete_supplier":
            sid = d.get("id", [""])[0]
            write("suppliers", [x for x in read("suppliers") if x.get("id") != sid])
            self.red("/suppliers"); return
        if path == "/provider_test": self._post_provider_test(d); return
        if path == "/rossko_checkout_details": self._post_rossko_checkout(d); return
        if path == "/save_rossko_delivery": self._post_save_rossko_delivery(d); return
        if path == "/place_order": self._post_place_order(d); return
        if path == "/order_status": self._post_order_status(d); return
        if path == "/finance_add": self._post_finance_add(d); return
        if path == "/save_settings": self._post_save_settings(d); return
        if path == "/update_github":
            ok, msg = update_from_github()
            self.out(layout("Обновление", f"<div class='card'><h1>Обновление</h1><div class='{'ok' if ok else 'err'}'>{esc(msg)}</div><p>Если обновление установлено, перезапустите приложение.</p><a class='btn' href='/'>На главную</a></div>"))
            return
        if path == "/fetch_prices": self._post_fetch_prices(); return
        if path == "/customer_add": self._post_customer_add(d); return
        if path == "/customer_vehicle_add": self._post_customer_vehicle_add(d); return
        if path == "/customer_vehicle_delete":
            vid = d.get("id", [""])[0]; cid = d.get("customer_id", [""])[0]
            delete_customer_vehicle(vid); self.red(f"/customer?id={quote(cid)}"); return
        if path == "/customer_vehicle_primary":
            vid = d.get("id", [""])[0]; cid = d.get("customer_id", [""])[0]
            set_primary_vehicle(cid, vid); self.red(f"/customer?id={quote(cid)}"); return
        if path == "/customer_update":
            cid = d.get("id", [""])[0]
            update_customer(cid, name=d.get("name", [""])[0], phone=d.get("phone", [""])[0],
                email=d.get("email", [""])[0], company=d.get("company", [""])[0],
                city=d.get("city", [""])[0], notes=d.get("notes", [""])[0])
            self.red(f"/customer?id={quote(cid)}"); return
        if path == "/customer_delete":
            cid = d.get("id", [""])[0]
            with db_conn() as c: c.execute("DELETE FROM customers WHERE id=?", (cid,))
            self.red("/customers"); return
        if path == "/customer_set_for_cart": self._post_customer_set_for_cart(d); return
        self.out(layout("404", "<div class='card'><h1>404</h1></div>"), 404)

    def _page_home(self, q):
        article = q.get("part", [""])[0].strip()
        min_p = q.get("min_price", [""])[0]; max_p = q.get("max_price", [""])[0]
        max_d = q.get("max_days", [""])[0]; in_stock = q.get("in_stock", [""])[0] == "1"
        sort_mode = q.get("sort", ["smart"])[0]
        if sort_mode not in ("smart", "price", "delivery", "stock"): sort_mode = "smart"
        with db_conn() as c:
            nc = c.execute("SELECT COUNT(*) FROM orders WHERE status='Новый'").fetchone()[0]
            pc = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('Отправлен','В обработке','Отправляется','Подтверждён поставщиком')").fetchone()[0]
            rc = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('Отгружен','Завершён')").fetchone()[0]
            ec = c.execute("SELECT COUNT(*) FROM orders WHERE status='Ошибка отправки'").fetchone()[0]
            recent = c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 6").fetchall()
        stats = f"""<div class="stat-grid">
<div class="stat stat--primary"><div class="label">Новые</div><div class="value">{nc}</div></div>
<div class="stat stat--warning"><div class="label">В работе</div><div class="value">{pc}</div></div>
<div class="stat stat--success"><div class="label">Готовы</div><div class="value">{rc}</div></div>
<div class="stat stat--danger"><div class="label">Ошибки</div><div class="value">{ec}</div></div>
</div>"""
        rr = ""
        for o in recent:
            bc = ("badge--new" if o["status"] == "Новый" else
                  "badge--progress" if o["status"] in ("Отправлен", "В обработке", "Отправляется", "Подтверждён поставщиком") else
                  "badge--done" if o["status"] in ("Отгружен", "Завершён") else
                  "badge--error" if o["status"] in ("Отменён", "Ошибка отправки") else "")
            ext = order_external_numbers(o["id"])
            tt = "\n".join(f"{e['supplier_name']}: {e['external_no']}" for e in ext) if ext else ""
            ta = f' data-tooltip="{esc(tt)}"' if tt else ""
            rr += f"""<tr>
<td><a href="/order?id={esc(o['id'])}"><span class="order-number"{ta}>{esc(o['number'])}</span></a></td>
<td>{esc(o['supplier_name'] or '—')}</td>
<td><span class="badge {bc}">{esc(o['status'])}</span></td>
<td class="price">{fmt_price(o['subtotal'])} ₽</td>
</tr>"""
        sc = f"""<div class="search-hero">
<h2>{icon("search", 22)} Что ищем?</h2>
<p>Введите артикул — программа опросит всех поставщиков.</p>
<form method="get" action="/">
<input type="text" name="part" value="{esc(article)}" placeholder="Артикул или название" autofocus>
<button type="submit">{icon("search", 18)} Проценить</button>
</form>
</div>"""
        rh = ""
        if article:
            filt = {"min_price": parse_price(min_p), "max_price": parse_price(max_p),
                    "max_days": int(max_d) if max_d.isdigit() else None, "in_stock_only": in_stock}
            m, cs, errs = collect_offers(article, filt, sort_mode="price_delivery")
            cbs = cart_index(); cba = cart_index_by_article()
            rh = render_grouped_results(m, cs, cbs, cba, sort_mode=sort_mode, cross_sort_mode="price",
                requested_article=article, filters={"min_price": min_p, "max_price": max_p, "max_days": max_d, "in_stock_only": in_stock})
            if errs:
                err_html = "".join(f"<div class='warn'>⚠ {esc(e)}</div>" for e in errs)
                rh = f"<div class='card'><h3>Поставщики, которые не ответили</h3>{err_html}</div>" + rh
            rh = f'<div class="card"><h2>Результаты по «{esc(article)}»</h2>{rh}</div>'
        body = f"""{sc}
{stats}
<div class="grid">
<div class="card"><h2>Последние заказы</h2>
<div class="table-wrap"><table><tr><th>№</th><th>Поставщик</th><th>Статус</th><th>Сумма</th></tr>
{rr or '<tr><td colspan=4 class="muted">Нет заказов</td></tr>'}
</table></div>
<a class="btn btn--secondary btn--sm mt-3" href="/orders">Все заказы {icon("arrow-right", 14)}</a>
</div>
<div class="card"><h2>Быстрые действия</h2>
<div class="toolbar toolbar--top">
<a class="btn" href="/cart_optimize">{icon("sparkles", 16)} Оптимизация корзины</a>
<a class="btn btn--secondary" href="/customers">{icon("users", 16)} Клиенты</a>
<a class="btn btn--secondary" href="/finance">{icon("wallet", 16)} Финансы</a>
<a class="btn btn--secondary" href="/suppliers">{icon("store", 16)} Поставщики</a>
</div>
</div>
</div>
{rh}"""
        self.out(layout("Работа", body, current="home"))

    def _page_expand(self, q):
        article = q.get("article", [""])[0].strip()
        group = q.get("group", ["main"])[0]
        if not article: self.red("/"); return
        min_p = q.get("min_price", [""])[0]; max_p = q.get("max_price", [""])[0]; max_d = q.get("max_days", [""])[0]
        filt = {"min_price": parse_price(min_p), "max_price": parse_price(max_p),
                "max_days": int(max_d) if str(max_d).isdigit() else None,
                "in_stock_only": q.get("in_stock", [""])[0] == "1"}
        m, cs, _ = collect_offers(article, filt, sort_mode="price_delivery")
        cbs = cart_index(); cba = cart_index_by_article()
        if group == "main":
            offers = list(m); title = f"Все предложения: {article}"
        else:
            offers = list(cs); title = f"Все аналоги: {article}"
        offers.sort(key=lambda o: (o.get("price") if o.get("price") is not None else 10**12,
                                    delivery_days(o.get("delivery")) if delivery_days(o.get("delivery")) is not None else 9999))
        rows = ""
        for o in offers:
            term = human_delivery(o.get("delivery"), o.get("delivery_start", ""))
            wh = esc(o.get("warehouse", "") or "—")
            if len(wh) > 60: wh = wh[:60] + "…"
            cnt = o.get("count", 0); mult = o.get("multiplicity", 1)
            cnt_txt = f"{cnt} шт." + (f" ×{mult}" if mult > 1 else "")
            sb = ("<span class='badge badge--done'>в наличии</span>" if cnt > 0
                  else "<span class='badge badge--error'>под заказ</span>")
            key = (normalize_article(o.get("article") or ""), o.get("supplier_id") or "")
            in_cart = cbs.get(key, 0) > 0
            ct = ' <span class="cross-tag">аналог</span>' if o.get("is_cross") else ''
            rows += f"""<tr{' class="cart-in-row"' if in_cart else ''}>
<td><b>{esc(o.get('article','—'))}</b>{ct}<div class="muted" style="font-size:12px">{esc(o.get('brand',''))}</div></td>
<td><b>{esc(o.get('supplier_name','—'))}</b></td>
<td>{esc(term)}</td>
<td>{wh}</td>
<td>{cnt_txt} {sb}</td>
<td class="price">{fmt_price(o['price'])} ₽</td>
<td>{render_cart_cell(o, cbs, cba)}</td>
</tr>"""
        body = f"""<div class="card"><div class="card-title"><div><h1>{esc(title)}</h1>
<div class="muted">Найдено: {len(offers)}</div></div>
<a class="btn btn--secondary" href="/?part={quote(article)}">{icon("arrow-left", 16)} Назад</a></div>
<div class="table-wrap"><table><tr><th>Артикул</th><th>Поставщик</th><th>Срок</th><th>Склад</th><th>Наличие</th><th>Цена</th><th></th></tr>
{rows or "<tr><td colspan='7' class='muted'>Пусто</td></tr>"}</table></div></div>"""
        self.out(layout("Все предложения", body))

    def _page_suppliers(self):
        ss = read("suppliers"); rows = ""
        for s in ss:
            tpl = s.get("template", "custom")
            if s.get("last_test", {}) and s["last_test"].get("ok"):
                st = '<span class="badge badge--done">✓ проверен</span>'
            elif s.get("last_test"):
                st = '<span class="badge badge--error">⚠ ошибка</span>'
            else:
                st = '<span class="badge">не проверен</span>'
            rows += (f"<tr><td><b>{esc(s.get('name'))}</b></td>"
                     f"<td><span class='badge badge--violet'>{esc(tpl)}</span></td>"
                     f"<td>{st}</td>"
                     f"<td class='row-actions'>"
                     f"<a class='btn btn--secondary btn--sm' href='/supplier?id={esc(s['id'])}'>{icon('pencil',14)} Настроить</a> "
                     f"<form method='post' action='/delete_supplier' data-confirm='Удалить поставщика?' style='display:inline'>"
                     f"<input type='hidden' name='id' value='{esc(s['id'])}'>"
                     f"<button class='btn btn--danger btn--sm'>{icon('trash',14)}</button></form></td></tr>")
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("store", 24)} Поставщики</h1>
<div class="muted">Настройка подключений к поставщикам.</div></div>
<a class="btn btn--success" href="/wizard1">{icon("plus", 16)} Добавить поставщика</a></div></div>
<div class="card"><div class="table-wrap"><table>
<tr><th>Поставщик</th><th>Тип</th><th>Состояние</th><th></th></tr>
{rows or "<tr><td colspan='4' class='muted'>Пока нет ни одного поставщика.</td></tr>"}
</table></div></div>"""
        self.out(layout("Поставщики", body))

    def _page_wizard1(self, q):
        prefill = q.get("name", [""])[0]
        body = f"""<div class="card"><div class="card-title"><div><h1>Добавить поставщика</h1>
<div class="muted">Шаг 1 из 3</div></div>
<a class="btn btn--secondary" href="/suppliers">{icon("arrow-left", 16)} Назад</a></div>
{steps_html(1)}
<form method="post" action="/wizard1">
<div class="grid">
<div class="field"><label>Название поставщика <span class="required">*</span></label>
<input name="name" list="supplier_catalog" value="{esc(prefill)}" placeholder="Выберите или введите" required autofocus>
<datalist id="supplier_catalog">
<option value="Rossko"><option value="Berg"><option value="MParts">
<option value="ZAP-PRO"><option value="AVD"><option value="AutoEuro">
</datalist>
<small>Название, под которым поставщик будет отображаться в списке.</small></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">Далее {icon("arrow-right", 16)}</button>
<a class="btn btn--secondary" href="/suppliers">Отмена</a></div>
</form></div>"""
        self.out(layout("Добавить поставщика", body))

    def _page_wizard2(self, q):
        name = q.get("name", [""])[0]; selected = q.get("mode", [""])[0]
        if not name: self.red("/wizard1"); return
        norm = re.sub(r"[^a-zа-я0-9]+", "", name.lower().replace("ё", "е"))
        pa = [
            (("rossko", "росско"), "rossko"),
            (("берг", "berg"), "berg"),
            (("mparts", "мпартс"), "mparts"),
            (("zappro", "заппро", "зап-про", "зап про"), "zappro"),
            (("avd", "авд"), "avd"),
            (("autoeuro", "автоевро"), "autoeuro"),
        ]
        sg = ""
        for aliases, adapter in pa:
            if any(re.sub(r"[^a-zа-я0-9]+", "", a.lower().replace("ё", "е")) in norm for a in aliases):
                sg = adapter; break
        if selected not in ("email", *API_TYPES.keys()): selected = sg or "email"
        def ch(value, title, desc, rec=False):
            ck = " checked" if selected == value else ""
            r = " <span class='badge badge--new'>Подходит по названию</span>" if rec else ""
            return f"""<label class="tile"><input type="radio" name="mode" value="{esc(value)}" style="width:auto;margin-right:12px"{ck} required>
<span><span class="nm">{title}{r}</span><div class="hint">{desc}</div></span></label>"""
        ec = ch("email", "Получать прайсы по e-mail",
                "Поставщик присылает файл с ценами на почту. Не нужны API-ключи.", not sg)
        ac = ""
        if sg:
            api = API_TYPES[sg]
            ac = ch(sg, f"Автоматически: {esc(name)}",
                    f"Готовое подключение. {esc(api['hint'])}", True)
        others = ""
        for k, v in API_TYPES.items():
            if k == sg: continue
            details = {
                "berg": "БЕРК: API-ключ.",
                "zappro": "ZAP-PRO: адрес портала + API-ключ.",
                "rossko": "ROSSKO: KEY1 и KEY2.",
                "avd": "AVD: адрес сервиса, логин, пароль.",
                "autoeuro": "AutoEuro: API-ключ, delivery_key, payer_key.",
                "mparts": "MParts: логин, MD5-пароль, оплата, доставка.",
                "custom": "Свой REST/JSON API.",
            }.get(k, v["hint"])
            others += ch(k, esc(v["name"]), details)
        body = f"""<div class="card"><div class="card-title"><div><h1>Как получать прайсы?</h1>
<div class="muted">Шаг 2 из 3 — {esc(name)}</div></div>
<a class="btn btn--secondary" href="/suppliers">{icon("arrow-left", 16)} Назад</a></div>
{steps_html(2)}
<div class="note">{icon("info", 18)} Выберите, как программа будет получать цены. Если поставщик не давал API-доступ — выбирайте e-mail.</div>
<form method="post" action="/wizard2">
<input type="hidden" name="name" value="{esc(name)}">
{ac}{ec}
<details style="margin:14px 0"><summary style="cursor:pointer;font-weight:700">У меня есть API-доступ, показать другие варианты</summary><div style="margin-top:12px">{others}</div></details>
<div class="toolbar"><a class="btn btn--secondary" href="/wizard1?name={quote(name)}">{icon("arrow-left", 16)} Назад</a>
<button class="btn btn--success" type="submit">Далее {icon("arrow-right", 16)}</button></div>
</form></div>"""
        self.out(layout("Добавить поставщика", body))

    def _page_wizard3(self, q):
        name = q.get("name", [""])[0]; tpl = q.get("tpl", [""])[0]
        if not name or (tpl != "email" and tpl not in API_TYPES): self.red("/wizard1"); return
        if tpl == "email":
            body = f"""<div class="card"><div class="card-title"><div><h1>Прайсы по e-mail</h1>
<div class="muted">Шаг 3 из 3 — {esc(name)}</div></div></div>{steps_html(3)}
<form method="post" action="/wizard_save_test">
<input type="hidden" name="name" value="{esc(name)}"><input type="hidden" name="tpl" value="email">
<div class="grid">
<div class="field"><label>E-mail для прайсов <span class="required">*</span></label>
<input type="email" name="price_email" required placeholder="prices@example.com"></div>
<div class="field"><label>E-mail для заказов</label>
<input type="email" name="order_email" placeholder="orders@example.com"></div>
<div class="field"><label>Канал заказов</label>
<select name="order_channel"><option value="auto">Автоматически</option><option value="email">Только e-mail</option></select></div>
</div>
<div class="toolbar"><a class="btn btn--secondary" href="/wizard2?name={quote(name)}&mode={quote(tpl)}">{icon("arrow-left", 16)} Назад</a>
<button class="btn btn--success" type="submit">Сохранить</button></div>
</form></div>"""
            self.out(layout("Добавить поставщика", body)); return
        api = API_TYPES[tpl]; fh = ""
        for spec in api["fields"]:
            key, label, hint, typ = spec[0], spec[1], spec[2], spec[3]
            req = spec[4] if len(spec) > 4 else False
            ra = "required" if req else ""; star = ' <span class="required">*</span>' if req else ""
            dv = ""
            if tpl == "avd" and key == "endpoint": dv = api.get("default_endpoint", "")
            if tpl == "zappro" and key == "base_url": dv = api.get("default_base_url", "")
            if typ == "select":
                fh += f"""<div class='field'><label>{esc(label)}{star}</label>
<select name='{esc(key)}'><option value='url'>В URL</option><option value='header'>В заголовке</option></select>
<small>{esc(hint)}</small></div>"""
            else:
                fh += f"""<div class='field'><label>{esc(label)}{star}</label>
<input type='{esc(typ)}' name='{esc(key)}' value='{esc(dv)}' {ra}><small>{esc(hint)}</small></div>"""
        extra = ""
        if tpl == "rossko":
            extra = '<div class="note">Поля delivery_id и address_id можно оставить пустыми. После сохранения откройте карточку поставщика и нажмите «🚚 Проверить доставку».</div>'
        body = f"""<div class="card"><div class="card-title"><div><h1>Данные доступа — {esc(name)}</h1>
<div class="muted">Шаг 3 из 3 · {esc(api['name'])}</div></div></div>{steps_html(3)}
{extra}
<form method="post" action="/wizard_save_test">
<input type="hidden" name="name" value="{esc(name)}"><input type="hidden" name="tpl" value="{esc(tpl)}">
<div class="grid">{fh}</div>
<div class="toolbar"><a class="btn btn--secondary" href="/wizard2?name={quote(name)}&mode={quote(tpl)}">{icon("arrow-left", 16)} Назад</a>
<button class="btn btn--success" type="submit">{icon("clipboard", 16)} Сохранить и проверить</button></div>
</form></div>"""
        self.out(layout("Добавить поставщика", body))

    def _page_supplier_edit(self, q):
        s = supplier(q.get("id", [""])[0])
        if not s:
            self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
        self.out(layout("Настройки поставщика", self._supplier_form(s)))

    def _supplier_form(self, s):
        ot = s.get("template", "custom"); ie = ot == "email"
        tpl = ot if ot in API_TYPES else "custom"; api = API_TYPES[tpl]
        cred = s.get("credentials", {}) or {}
        fh = f"""<div class='field'><label>Название</label><input name='name' value='{esc(s.get("name", ""))}' required></div>"""
        if ie:
            fh += f"""<div class='field'><label>E-mail прайсов</label><input type='email' name='price_email' value='{esc(s.get("price_email", ""))}'></div>
<div class='field'><label>E-mail заказов</label><input type='email' name='order_email' value='{esc(s.get("order_email", ""))}'></div>
<div class='field'><label>Канал заказов</label><select name='order_channel'><option value='auto' {'selected' if s.get('order_channel','auto')=='auto' else ''}>Авто</option><option value='email' {'selected' if s.get('order_channel')=='email' else ''}>E-mail</option></select></div>"""
        else:
            cho = ""
            for val, lbl in [("auto", "Автоматически"), ("api", "Только API"), ("email", "Только e-mail")]:
                sel = " selected" if s.get("order_channel", "auto") == val else ""
                cho += f'<option value="{val}"{sel}>{lbl}</option>'
            fh += f"""<div class='field'><label>Канал заказов</label><select name='order_channel'>{cho}</select></div>
<div class='field'><label>E-mail заказов</label><input type='email' name='order_email' value='{esc(s.get("order_email", ""))}'></div>
<div class='field'><label>E-mail прайсов</label><input type='email' name='price_email' value='{esc(s.get("price_email", ""))}'></div>
<input type='hidden' name='template' value='{esc(tpl)}'>"""
            for spec in api["fields"]:
                key, label, hint, typ = spec[0], spec[1], spec[2], spec[3]
                req = spec[4] if len(spec) > 4 else False
                val = esc(cred.get(key, ""))
                ra = "required" if req else ""; star = ' <span class="required">*</span>' if req else ""
                if typ == "select":
                    fh += f"""<div class='field'><label>{esc(label)}{star}</label>
<select name='{esc(key)}'><option value='url' {'selected' if cred.get(key) == 'url' else ''}>В URL</option><option value='header' {'selected' if cred.get(key) == 'header' else ''}>В заголовке</option></select>
<small>{esc(hint)}</small></div>"""
                else:
                    fh += f"""<div class='field'><label>{esc(label)}{star}</label>
<input type='{esc(typ)}' name='{esc(key)}' value='{val}' {ra}><small>{esc(hint)}</small></div>"""
        test_a = api.get("test_article", "") or "LC-1030"
        test_b = api.get("test_brand", "")
        db = ""
        if tpl == "rossko":
            db = f"""<form method="post" action="/rossko_checkout_details" style="display:inline">
<input type="hidden" name="id" value="{esc(s['id'])}">
<button class="btn btn--warning" type="submit">{icon("truck", 16)} Проверить доставку</button></form>"""
        bi = ""
        if test_b: bi = f'<input type="hidden" name="brand" value="{esc(test_b)}">'
        return f"""<div class="card"><div class="card-title"><div><h1>{esc(s.get('name', ''))}</h1>
<div class="muted">Настройки подключения</div></div>
<a class="btn btn--secondary" href="/suppliers">{icon("arrow-left", 16)} Назад</a></div>
<form method="post" action="/update_supplier">
<input type="hidden" name="id" value="{esc(s['id'])}">
<div class="grid">{fh}</div>
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Сохранить</button></div>
</form></div>
<div class="card"><h2>Проверка подключения</h2>
<p class="muted">Отправит запрос с артикулом <b>{esc(test_a)}</b>{f' и брендом <b>{esc(test_b)}</b>' if test_b else ''}.</p>
<div class="toolbar">
<form method="post" action="/provider_test" style="display:inline">
<input type="hidden" name="id" value="{esc(s['id'])}">
<input type="hidden" name="article" value="{esc(test_a)}">
{bi}
<button class="btn btn--warning" type="submit">{icon("refresh", 16)} Проверить поиск</button>
</form>
{db}
</div></div>"""

    def _page_orders(self, q):
        status = q.get("status", [""])[0]; sp = q.get("supplier", [""])[0]; search = q.get("q", [""])[0]
        rows = ""
        for o in list_orders(status, sp, search):
            bc = ("badge--new" if o["status"] == "Новый" else
                  "badge--progress" if o["status"] in ("Отправлен", "В обработке", "Отправляется", "Подтверждён поставщиком") else
                  "badge--done" if o["status"] in ("Отгружен", "Завершён") else
                  "badge--error" if o["status"] in ("Отменён", "Ошибка отправки") else "")
            ext = order_external_numbers(o["id"])
            tt = "\n".join(f"{e['supplier_name']}: {e['external_no']}" for e in ext) if ext else ""
            tp = f' data-tooltip="{esc(tt)}"' if tt else ""
            cu = customer(o["customer_id"]) if o["customer_id"] else None
            cn = cu["name"] if cu else "—"
            rows += f"""<tr>
<td><a href="/order?id={esc(o['id'])}"><span class="order-number"{tp}>{esc(o['number'])}</span></a></td>
<td>{esc(cn)}</td><td>{esc(o['supplier_name'] or '—')}</td>
<td>{esc(o['channel'] or '—')}</td>
<td><span class="badge {bc}">{esc(o['status'])}</span></td>
<td class="price">{fmt_price(o['subtotal'])} ₽</td>
<td class="muted">{esc(o['created_at'][:16].replace('T', ' '))}</td>
</tr>"""
        opts = ''.join(f"<option {'selected' if status == x else ''}>{esc(x)}</option>" for x in ORDER_STATUSES)
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("clipboard", 24)} Центр заказов</h1>
<div class="muted">Все заказы в одном месте.</div></div>
<a class="btn btn--secondary" href="/cart">{icon("cart", 16)} Корзина</a></div>
<form method="get"><div class="grid">
<div class="field"><label>Поиск</label><input name="q" value="{esc(search)}" placeholder="Номер / поставщик / внешний номер"></div>
<div class="field"><label>Статус</label><select name="status"><option value="">Все</option>{opts}</select></div>
</div><div class="toolbar"><button class="btn" type="submit">{icon("search", 16)} Фильтр</button><a class="btn btn--secondary" href="/orders">Сбросить</a></div>
</form></div>
<div class="card"><div class="table-wrap"><table>
<tr><th>№</th><th>Клиент</th><th>Поставщик</th><th>Канал</th><th>Статус</th><th>Сумма</th><th>Дата</th></tr>
{rows or "<tr><td colspan='7' class='muted'>Заказов нет.</td></tr>"}
</table></div></div>"""
        self.out(layout("Заказы", body, current="orders"))

    def _page_order(self, q):
        oid = q.get("id", [""])[0]
        o, items, events, ext = order_detail(oid)
        if not o:
            self.out(layout("Заказ", "<div class='card err'>Заказ не найден.</div>"), 404); return
        rows = ""
        for x in items:
            rows += f"<tr><td>{esc(x['article'] or '—')}</td><td>{esc(x['brand'] or '—')}</td><td>{esc(x['name'] or '—')}</td><td>{x['qty']}</td><td class='price'>{fmt_price(x['price'])} ₽</td></tr>"
        ev = ""
        for e in events:
            ev += f"<tr><td class='muted'>{esc(e['created_at'])}</td><td>{esc(e['status'])}</td><td>{esc(e['message'] or '—')}</td></tr>"
        opts = ''.join(f"<option {'selected' if o['status'] == x else ''}>{esc(x)}</option>" for x in ORDER_STATUSES)
        eh = ""
        if ext:
            eh = '<div class="mt-3"><b>Внешние номера:</b><ul>'
            for e in ext: eh += f"<li>{esc(e['supplier_name'] or '—')}: <code>{esc(e['external_no'])}</code></li>"
            eh += "</ul></div>"
        cu = customer(o["customer_id"]) if o["customer_id"] else None
        ch = f'<div class="mt-3"><b>Клиент:</b> <a href="/customer?id={esc(cu["id"])}">{esc(cu["name"])}</a> · {esc(cu["phone"] or "—")}</div>' if cu else '<div class="mt-3 muted">Клиент не указан</div>'
        body = f"""<div class="card"><div class="card-title"><div>
<h1>{icon("clipboard", 24)} Заказ <span class="order-number">{esc(o['number'])}</span></h1>
<div class="muted">{esc(o['supplier_name'] or '—')} · канал: {esc(o['channel'] or '—')}</div>
{ch}{eh}</div>
<a class="btn btn--secondary" href="/orders">{icon("arrow-left", 16)} Заказы</a></div>
<div class="grid mt-3">
<div><b>Статус:</b> {esc(o['status'])}</div>
<div><b>Оплата:</b> {esc(o['payment_status'])}</div>
<div><b>Сумма:</b> <span class="price">{fmt_price(o['subtotal'])} ₽</span></div>
<div><b>Создан:</b> {esc(o['created_at'][:16].replace('T', ' '))}</div>
</div>
<div class="toolbar"><a class="btn" href="/order_print?id={quote(oid)}" target="_blank">{icon("printer", 16)} Печать / PDF</a></div>
<form method="post" action="/order_status" class="toolbar">
<input type="hidden" name="id" value="{esc(oid)}">
<select name="status" style="width:auto">{opts}</select>
<input name="message" placeholder="Комментарий" style="flex:1">
<button class="btn btn--warning" type="submit">{icon("refresh", 16)} Изменить статус</button>
</form></div>
<div class="card"><h2>Позиции</h2>
<div class="table-wrap"><table>
<tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th></tr>
{rows}</table></div></div>
<div class="card"><h2>История</h2>
<div class="table-wrap"><table><tr><th>Дата</th><th>Статус</th><th>Сообщение</th></tr>
{ev or "<tr><td colspan=3 class='muted'>Нет событий</td></tr>"}</table></div></div>"""
        self.out(layout("Заказ " + o['number'], body, current="orders"))

    def _page_order_print(self, q):
        oid = q.get("id", [""])[0]
        o, items, events, ext = order_detail(oid)
        if not o: self.out("<h1>Заказ не найден</h1>", 404); return
        rows = ''.join(f"<tr><td>{html.escape(x['article'] or '—')}</td><td>{html.escape(x['brand'] or '—')}</td><td>{html.escape(x['name'] or '—')}</td><td>{x['qty']}</td><td>{fmt_price(x['price']) if x['price'] is not None else '—'}</td></tr>" for x in items)
        doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(o['number'])}</title>
<style>body{{font-family:Arial;padding:30px;color:#172033}}table{{width:100%;border-collapse:collapse;margin-top:16px}}
td,th{{border:1px solid #ccc;padding:8px;text-align:left}}th{{background:#f2f4f7}}
button{{padding:10px 20px;font-size:14px;cursor:pointer;background:#2563eb;color:#fff;border:0;border-radius:8px}}
h1{{margin:0}}@media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()">🖨 Печать / Сохранить PDF</button>
<h1>Заказ {html.escape(o['number'])}</h1>
<p>Поставщик: {html.escape(o['supplier_name'] or '—')}<br>Статус: {html.escape(o['status'])}<br>Дата: {html.escape(o['created_at'][:16].replace('T', ' '))}</p>
<table><tr><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Кол-во</th><th>Цена</th></tr>{rows}</table>
<h3>Итого: {fmt_price(o['subtotal'])} ₽</h3></body></html>"""
        self.out(doc)

    def _page_prices(self, q):
        qart = q.get("article", [""])[0].strip()
        with db_conn() as c:
            if qart:
                ps = c.execute("SELECT * FROM supplier_prices WHERE article LIKE ? ORDER BY received_at DESC LIMIT 500", (f"%{qart}%",)).fetchall()
            else:
                ps = c.execute("SELECT * FROM supplier_prices ORDER BY received_at DESC LIMIT 500").fetchall()
        rows = ''.join(f"<tr><td>{esc(x['supplier_name'] or '—')}</td><td>{esc(x['article'] or '—')}</td><td>{esc(x['brand'] or '—')}</td><td>{esc(x['name'] or '—')}</td><td class='price'>{fmt_price(x['price'])} ₽</td><td>{esc(x['stock'] or '—')}</td><td>{esc(x['delivery'] or '—')}</td><td class='muted'>{esc(x['received_at'][:16].replace('T', ' '))}</td></tr>" for x in ps)
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("package", 24)} Прайсы поставщиков</h1>
<div class="muted">Последние полученные позиции из входящей почты.</div></div>
<form method="post" action="/fetch_prices" style="display:inline"><button class="btn btn--warning" type="submit">{icon("download", 16)} Проверить почту</button></form></div>
<form method="get"><div class="grid">
<div class="field"><label>Артикул</label><input name="article" value="{esc(qart)}" placeholder="Поиск по артикулу"></div>
</div><div class="toolbar"><button class="btn" type="submit">{icon("search", 16)} Найти</button></div></form>
</div>
<div class="card"><div class="table-wrap"><table>
<tr><th>Поставщик</th><th>Артикул</th><th>Бренд</th><th>Деталь</th><th>Цена</th><th>Остаток</th><th>Срок</th><th>Получен</th></tr>
{rows or "<tr><td colspan=8 class='muted'>Нет данных</td></tr>"}
</table></div></div>"""
        self.out(layout("Прайсы", body))

    def _page_finance(self):
        f = finance_summary()
        ops = list_finance(100)
        rows = ""
        for x in ops:
            direction_badge = ('<span class="badge badge--done">Поступление</span>' if x["direction"] == "in"
                               else '<span class="badge badge--error">Выплата</span>')
            amount_cls = "saved" if x["direction"] == "in" else "loss"
            amount_sign = "+" if x["direction"] == "in" else "−"
            rows += f"""<tr>
<td class="muted">{esc(x['created_at'][:16].replace('T', ' '))}</td>
<td>{esc(x['kind'] or '—')}</td>
<td>{esc(x['category'] or '—')}</td>
<td>{direction_badge}</td>
<td class="{amount_cls}">{amount_sign}{fmt_price(x['amount'])} ₽</td>
<td class="muted">{esc(x['note'] or '')}</td>
</tr>"""
        body = f"""<div class="card"><div class="card-title"><div><h1>💰 Финансы и взаиморасчёты</h1>
<div class="muted">Движение денег, прибыль, задолженности.</div></div>
<a class="btn btn--secondary" href="/">{icon("arrow-left", 16)} На главную</a></div>
<div class="stat-grid">
<div class="stat stat--success"><div class="label">Поступления</div><div class="value">{fmt_price(f['income'])} ₽</div></div>
<div class="stat stat--danger"><div class="label">Выплаты</div><div class="value">{fmt_price(f['expense'])} ₽</div></div>
<div class="stat stat--primary"><div class="label">Чистый поток</div><div class="value">{fmt_price(f['cash'])} ₽</div></div>
<div class="stat"><div class="label">Выручка</div><div class="value">{fmt_price(f['revenue'])} ₽</div></div>
<div class="stat"><div class="label">Себестоимость</div><div class="value">{fmt_price(f['cost'])} ₽</div></div>
<div class="stat stat--success"><div class="label">Валовая прибыль</div><div class="value">{fmt_price(f['profit'])} ₽</div></div>
<div class="stat"><div class="label">Прочие расходы</div><div class="value">{fmt_price(f['other_expenses'])} ₽</div></div>
<div class="stat stat--primary"><div class="label">Чистая прибыль</div><div class="value">{fmt_price(f['net_profit'])} ₽</div></div>
<div class="stat stat--warning"><div class="label">Дебиторка (нам должны)</div><div class="value">{fmt_price(f['receivable'])} ₽</div></div>
<div class="stat stat--danger"><div class="label">Кредиторка (мы должны)</div><div class="value">{fmt_price(f['payable'])} ₽</div></div>
</div></div>
<div class="card"><h2>Добавить операцию</h2>
<form method="post" action="/finance_add">
<div class="grid">
<div class="field"><label>Тип операции <span class="required">*</span></label>
<input name="kind" required placeholder="Оплата клиента / Начисление поставщику / Прочее"></div>
<div class="field"><label>Категория <span class="required">*</span></label>
<input name="category" required placeholder="Продажа / Закупка / Аренда / Зарплата"></div>
<div class="field"><label>Сумма, ₽ <span class="required">*</span></label>
<input name="amount" type="number" step="0.01" min="0.01" required placeholder="10000"></div>
<div class="field"><label>Направление</label>
<select name="direction"><option value="in">Поступление (получили)</option><option value="out">Выплата (отдали)</option></select></div>
<div class="field"><label>Комментарий</label>
<input name="note" placeholder="Необязательно"></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Сохранить операцию</button>
<a class="btn btn--secondary" href="/finance">Отмена</a></div>
</form></div>
<div class="card"><h2>Последние операции ({len(ops)})</h2>
<div class="table-wrap"><table>
<tr><th>Дата</th><th>Тип</th><th>Категория</th><th>Направление</th><th>Сумма</th><th>Комментарий</th></tr>
{rows or "<tr><td colspan='6' class='muted'>Операций пока нет.</td></tr>"}
</table></div></div>"""
        self.out(layout("Финансы", body))

    def _page_settings(self):
        s = read("settings")
        body = f"""<div class="card"><h1>{icon("settings", 24)} Настройки</h1>
<form method="post" action="/save_settings">
<h2 class="mt-4">Компания</h2>
<div class="grid">
<div class="field"><label>Название компании</label><input name="company_name" value="{esc(s.get('company_name'))}"></div>
<div class="field"><label>Город / регион</label><input name="city" value="{esc(s.get('city'))}"></div>
</div>
<h2 class="mt-4">SMTP — отправка заказов</h2>
<div class="grid">
<div class="field"><label>SMTP-сервер</label><input name="smtp_host" value="{esc(s.get('smtp_host'))}" placeholder="smtp.example.com"></div>
<div class="field"><label>Порт</label><input type="number" name="smtp_port" value="{esc(s.get('smtp_port', 587))}"></div>
<div class="field"><label>Шифрование</label><select name="smtp_security">
<option value="starttls" {'selected' if s.get('smtp_security') == 'starttls' else ''}>STARTTLS (587)</option>
<option value="ssl" {'selected' if s.get('smtp_security') == 'ssl' else ''}>SSL/TLS (465)</option>
<option value="none" {'selected' if s.get('smtp_security') == 'none' else ''}>Без шифрования</option>
</select></div>
<div class="field"><label>Логин</label><input name="smtp_user" value="{esc(s.get('smtp_user'))}"></div>
<div class="field"><label>Пароль</label><input type="password" name="smtp_password" value="{esc(s.get('smtp_password'))}"></div>
<div class="field"><label>E-mail отправителя</label><input type="email" name="smtp_from_email" value="{esc(s.get('smtp_from_email'))}"></div>
<div class="field"><label>Имя отправителя</label><input name="smtp_from_name" value="{esc(s.get('smtp_from_name'))}"></div>
</div>
<h2 class="mt-4">IMAP — получение прайсов</h2>
<div class="grid">
<div class="field"><label>IMAP-сервер</label><input name="imap_host" value="{esc(s.get('imap_host'))}" placeholder="imap.example.com"></div>
<div class="field"><label>Порт</label><input type="number" name="imap_port" value="{esc(s.get('imap_port', 993))}"></div>
<div class="field"><label>Шифрование</label><select name="imap_security">
<option value="ssl" {'selected' if s.get('imap_security') == 'ssl' else ''}>SSL/TLS</option>
<option value="starttls" {'selected' if s.get('imap_security') == 'starttls' else ''}>STARTTLS</option>
<option value="none" {'selected' if s.get('imap_security') == 'none' else ''}>Без шифрования</option>
</select></div>
<div class="field"><label>Логин</label><input name="imap_user" value="{esc(s.get('imap_user'))}"></div>
<div class="field"><label>Пароль</label><input type="password" name="imap_password" value="{esc(s.get('imap_password'))}"></div>
<div class="field"><label>Папка</label><input name="imap_folder" value="{esc(s.get('imap_folder', 'INBOX'))}"></div>
<div class="field"><label>Автопроверка</label><select name="price_poll_minutes">
<option value="5" {'selected' if int(s.get('price_poll_minutes', 15) or 15) == 5 else ''}>Каждые 5 минут</option>
<option value="15" {'selected' if int(s.get('price_poll_minutes', 15) or 15) == 15 else ''}>Каждые 15 минут</option>
<option value="30" {'selected' if int(s.get('price_poll_minutes', 15) or 15) == 30 else ''}>Каждые 30 минут</option>
<option value="60" {'selected' if int(s.get('price_poll_minutes', 15) or 15) == 60 else ''}>Каждый час</option>
</select></div>
</div>
<h2 class="mt-4">Автообновление из GitHub</h2>
<div class="grid">
<div class="field"><label>Репозиторий GitHub</label><input name="github_repo" value="{esc(s.get('github_repo'))}" placeholder="owner/repository"><small class="muted">Берётся последний опубликованный GitHub Release.</small></div>
<div class="field"><label>Автоматически проверять при запуске</label><select name="auto_update">
<option value="1" {'selected' if s.get('auto_update', True) else ''}>Да</option>
<option value="0" {'selected' if not s.get('auto_update', True) else ''}>Нет</option>
</select></div>
</div>
<div class="toolbar">
<button class="btn btn--success" type="submit">{icon("clipboard", 16)} Сохранить</button>
<a class="btn btn--secondary" href="/update">Проверить обновления</a>
<a class="btn btn--secondary" href="/about">О программе</a>
</div>
</form></div>"""
        self.out(layout("Настройки", body))

    def _page_update(self):
        latest, err = github_latest()
        if err:
            body = f"<div class='card'><h1>Обновление</h1><div class='warn'>{esc(err)}</div><p>Укажите GitHub-репозиторий в Настройки → Автообновление.</p><a class='btn btn--secondary' href='/settings'>Настройки</a></div>"
        elif github_version_key(latest["tag"]) <= github_version_key(VERSION):
            body = f"<div class='card'><h1>Обновление</h1><div class='ok'>Установлена актуальная версия {esc(VERSION)}.</div><p>Последний релиз GitHub: {esc(latest['tag'])}</p><a class='btn btn--secondary' href='/settings'>Назад</a></div>"
        else:
            body = f"""<div class='card'><h1>Доступно обновление</h1>
<p>Текущая версия: <b>{esc(VERSION)}</b><br>Новая версия: <b>{esc(latest['tag'])}</b></p>
<form method='post' action='/update_github'><button class='btn btn--success'>Установить обновление</button></form>
<a class='btn btn--secondary' href='/settings'>Отмена</a></div>"""
        self.out(layout("Обновление", body))

    def _page_about(self):
        ss = read("suppliers"); vs = read("vehicles"); cart = read("cart")
        with db_conn() as c:
            cc = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            oc = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            fc = c.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0]
        body = f"""<div class="card"><h1>О программе</h1>
<div class="table-wrap"><table>
<tr><td><b>Версия</b></td><td>{esc(VERSION)}</td></tr>
<tr><td><b>Папка данных</b></td><td><code>{esc(DATA_DIR)}</code></td></tr>
<tr><td><b>Лог-файл</b></td><td><code>{esc(LOG_FILE)}</code></td></tr>
<tr><td><b>Поставщиков</b></td><td>{len(ss)}</td></tr>
<tr><td><b>Клиентов</b></td><td>{cc}</td></tr>
<tr><td><b>Заказов</b></td><td>{oc}</td></tr>
<tr><td><b>Финансовых операций</b></td><td>{fc}</td></tr>
<tr><td><b>Позиций в корзине</b></td><td>{len(cart)}</td></tr>
</table></div></div>"""
        self.out(layout("О программе", body))

    def _page_cart(self):
        c = read("cart"); s = read("settings")
        cci = s.get("cart_customer_id") or ""
        cust = customer(cci) if cci else None
        rows = ""; total = 0.0
        for x in c:
            price = parse_price(x.get("price")); qty = int(x.get("qty", 1) or 1)
            if price is not None: total += price * qty
            term = human_delivery(x.get("term"))
            rows += f"""<tr>
<td>{esc(x.get('article'))}</td>
<td>{esc(x.get('supplier_name'))}</td>
<td>{esc(x.get('brand'))}</td>
<td>{esc(x.get('name'))}</td>
<td class="price">{fmt_price(price)} ₽</td>
<td><div class="qty-pm">
<form method="post" action="/cart_dec" style="display:inline"><input type="hidden" name="id" value="{esc(x['id'])}"><button>−</button></form>
<span class="num">{esc(qty)}</span>
<form method="post" action="/cart_inc" style="display:inline"><input type="hidden" name="id" value="{esc(x['id'])}"><button>+</button></form>
</div></td>
<td>{esc(term)}</td>
<td><form method="post" action="/remove_cart" style="display:inline"><input type="hidden" name="id" value="{esc(x['id'])}"><button class="btn btn--danger btn--sm">{icon("trash", 14)}</button></form></td>
</tr>"""
        cb = ""
        if cust:
            cb = f'<div class="ok">{icon("user", 16)} Клиент: <b>{esc(cust["name"])}</b> · {esc(cust["phone"] or "—")} <a href="/customer?id={esc(cust["id"])}">открыть</a> · <form method="post" action="/customer_set_for_cart" style="display:inline"><input type="hidden" name="customer_id" value=""><button class="btn btn--secondary btn--sm">отвязать</button></form></div>'
        else:
            with db_conn() as cc:
                custs = cc.execute("SELECT id, name, phone FROM customers ORDER BY created_at DESC LIMIT 100").fetchall()
            opts = "".join(f'<option value="{esc(x["id"])}">{esc(x["name"])} · {esc(x["phone"] or "")}</option>' for x in custs)
            if opts:
                cb = f"""<form method="post" action="/customer_set_for_cart" class="toolbar toolbar--top">
<select name="customer_id" style="width:auto"><option value="">— выберите клиента (опционально) —</option>{opts}</select>
<button class="btn btn--secondary btn--sm" type="submit">{icon("user", 14)} Привязать</button></form>"""
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("cart", 24)} Корзина</h1>
<div class="muted">Позиции для заказа.</div></div>
<a class="btn btn--secondary" href="/">{icon("arrow-left", 16)} К поиску</a></div>
{cb}
<div class="toolbar">
<a class="btn btn--warning" href="/cart_optimize">{icon("sparkles", 16)} Оптимизировать</a>
<a class="btn btn--success" href="/checkout">{icon("clipboard", 16)} Оформить заказ</a>
<form method="post" action="/clear_cart" data-confirm="Очистить корзину?" style="display:inline"><button class="btn btn--danger">{icon("trash", 16)} Очистить</button></form>
</div></div>
<div class="card"><div class="table-wrap"><table>
<tr><th>Артикул</th><th>Поставщик</th><th>Бренд</th><th>Деталь</th><th>Цена</th><th>Кол-во</th><th>Поставка</th><th></th></tr>
{rows or "<tr><td colspan='8' class='muted'>Корзина пуста.</td></tr>"}
</table></div>
<div class="toolbar"><b>Итого: <span class="price">{fmt_price(total)} ₽</span></b></div></div>"""
        self.out(layout("Корзина", body, current="cart"))

    def _page_cart_optimize(self):
        c = read("cart")
        if not c:
            self.out(layout("Оптимизация", "<div class='card err'>Корзина пуста.</div><a class='btn' href='/cart'>← Назад</a>")); return
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("sparkles", 24)} Оптимизация корзины</h1>
<div class="muted">Программа перепроверит каждую позицию у всех поставщиков.</div></div>
<a class="btn btn--secondary" href="/cart">{icon("arrow-left", 16)} Назад</a></div>
<form method="post" action="/cart_optimize_result">
<h2>Стратегия</h2>
<div class="opt-radio">
<label><input type="radio" name="strategy" value="price"> 💰 Самая низкая цена</label>
<label><input type="radio" name="strategy" value="delivery"> ⚡ Самый быстрый срок</label>
<label><input type="radio" name="strategy" value="standard" checked> ⚖ Баланс</label>
</div>
<h2 class="mt-4">Аналоги</h2>
<div class="opt-radio">
<label><input type="radio" name="analogs" value="0" checked> 🏷 Только выбранные бренды</label>
<label><input type="radio" name="analogs" value="1"> 🔄 Разрешить аналоги</label>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">Оптимизировать</button>
<a class="btn btn--secondary" href="/cart">Отмена</a></div>
</form></div>"""
        self.out(layout("Оптимизация корзины", body))

    def _page_checkout(self):
        c = read("cart")
        if not c:
            self.out(layout("Оформление", "<div class='card'><div class='warn'>Корзина пуста.</div><a class='btn' href='/cart'>← В корзину</a></div>")); return
        groups = {}
        for item in c: groups.setdefault(item.get("supplier_id") or "", []).append(item)
        s = read("settings"); cci = s.get("cart_customer_id") or ""
        cust = customer(cci) if cci else None
        cl = (f"<div class='ok'>{icon('user', 16)} Клиент: <b>{esc(cust['name'])}</b> · {esc(cust['phone'] or '—')}</div>"
              if cust else "<div class='note'>Клиент не выбран — заказ будет без привязки.</div>")
        cards = []
        for sid, items in groups.items():
            sup = supplier(sid)
            if not sup: cards.append("<div class='err'>Поставщик не найден.</div>"); continue
            total = sum((parse_price(x.get("price")) or 0) * max(1, int(x.get("qty", 1) or 1)) for x in items)
            try:
                ch = order_channel(sup); chb = f'<span class="badge badge--new">{esc(ch)}</span>'
            except Exception as ex: chb = f'<span class="badge badge--error">{esc(ex)}</span>'
            cards.append(f"<div class='tile'><span class='nm'>{esc(sup.get('name') or 'Поставщик')} {chb}</span><div class='hint'>Позиций: {len(items)} · Сумма: {fmt_price(total)} ₽</div></div>")
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("clipboard", 24)} Оформление заказа</h1>
<div class="muted">Программа разделит корзину по поставщикам и выберет канал: API или e-mail.</div></div>
<a class="btn btn--secondary" href="/cart">{icon("arrow-left", 16)} В корзину</a></div>
{cl}
{''.join(cards)}
<form method="post" action="/place_order">
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Оформить и отправить</button>
<a class="btn btn--secondary" href="/cart">Отмена</a></div>
</form></div>"""
        self.out(layout("Оформление", body))

    def _page_customers(self, q):
        search = q.get("q", [""])[0]
        cs = list_customers(search); rows = ""
        for x in cs:
            vc = len(customer_vehicles(x["id"]))
            rows += f"""<tr>
<td><a href="/customer?id={esc(x['id'])}"><b>{esc(x['name'])}</b></a></td>
<td>{esc(x['phone'] or '—')}</td>
<td>{esc(x['email'] or '—')}</td>
<td>{esc(x['company'] or '—')}</td>
<td>{vc} / 3</td>
<td class="muted">{esc(x['created_at'][:10])}</td>
</tr>"""
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("users", 24)} Клиенты</h1>
<div class="muted">Карточки клиентов с автопарком.</div></div>
<a class="btn btn--success" href="/customer_new">{icon("plus", 16)} Добавить клиента</a></div>
<form method="get"><div class="grid">
<div class="field"><label>Поиск</label><input name="q" value="{esc(search)}" placeholder="Имя / телефон / email"></div>
</div><div class="toolbar"><button class="btn" type="submit">{icon("search", 16)} Найти</button><a class="btn btn--secondary" href="/customers">Сбросить</a></div></form></div>
<div class="card"><div class="table-wrap"><table>
<tr><th>Имя</th><th>Телефон</th><th>E-mail</th><th>Компания</th><th>Авто</th><th>Создан</th></tr>
{rows or "<tr><td colspan='6' class='muted'>Клиентов пока нет.</td></tr>"}
</table></div></div>"""
        self.out(layout("Клиенты", body, current="customers"))

    def _page_customer_new(self, q):
        body = f"""<div class="card"><div class="card-title"><div><h1>Новый клиент</h1>
<div class="muted">Заполните обязательные поля.</div></div>
<a class="btn btn--secondary" href="/customers">{icon("arrow-left", 16)} Назад</a></div>
<form method="post" action="/customer_add">
<div class="grid">
<div class="field"><label>Имя / название <span class="required">*</span></label><input name="name" required autofocus></div>
<div class="field"><label>Телефон <span class="required">*</span></label><input name="phone" required placeholder="+7 999 123-45-67"></div>
<div class="field"><label>E-mail</label><input type="email" name="email"></div>
<div class="field"><label>Компания</label><input name="company"></div>
<div class="field"><label>Город</label><input name="city"></div>
<div class="field"><label>Комментарий</label><input name="notes"></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Создать</button>
<a class="btn btn--secondary" href="/customers">Отмена</a></div>
</form></div>"""
        self.out(layout("Новый клиент", body, current="customers"))

    def _page_customer(self, q):
        cid = q.get("id", [""])[0]
        c = customer(cid)
        if not c:
            self.out(layout("Клиент", "<div class='card err'>Клиент не найден.</div>"), 404); return
        vehicles = customer_vehicles(cid); vh = ""
        for v in vehicles:
            pb = '<span class="badge badge--new">основной</span>' if v["is_primary"] else ""
            acts = ""
            if not v["is_primary"]:
                acts += f"""<form method="post" action="/customer_vehicle_primary" style="display:inline"><input type="hidden" name="id" value="{esc(v['id'])}"><input type="hidden" name="customer_id" value="{esc(cid)}"><button class="btn btn--secondary btn--sm" type="submit">Сделать основным</button></form>"""
            acts += f"""<form method="post" action="/customer_vehicle_delete" data-confirm="Удалить авто?" style="display:inline"><input type="hidden" name="id" value="{esc(v['id'])}"><input type="hidden" name="customer_id" value="{esc(cid)}"><button class="btn btn--danger btn--sm" type="submit">{icon("trash", 14)}</button></form>"""
            name = f"{v['make'] or ''} {v['model'] or ''}".strip() or "Автомобиль"
            info = " · ".join(x for x in [v["year"], v["engine"], v["body_class"]] if x)
            vh += f"""<div class="vehicle-card"><div>
<div class="vname">{esc(name)} {pb}</div>
<div class="vinfo">{esc(info or '—')} · VIN: {esc(v['vin'] or '—')} {esc(' · ' + v['nickname'] if v['nickname'] else '')}</div>
</div><div class="row-actions">{acts}</div></div>"""
        if not vh: vh = "<div class='muted'>Автомобилей пока нет.</div>"
        with db_conn() as cc:
            orders = cc.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 30", (cid,)).fetchall()
        oh = ""
        for o in orders:
            bc = ("badge--new" if o["status"] == "Новый" else
                  "badge--progress" if o["status"] in ("Отправлен", "В обработке", "Отправляется", "Подтверждён поставщиком") else
                  "badge--done" if o["status"] in ("Отгружен", "Завершён") else
                  "badge--error" if o["status"] in ("Отменён", "Ошибка отправки") else "")
            oh += f"""<tr>
<td><a href="/order?id={esc(o['id'])}"><span class="order-number">{esc(o['number'])}</span></a></td>
<td class="muted">{esc(o['created_at'][:10])}</td>
<td><span class="badge {bc}">{esc(o['status'])}</span></td>
<td class="price">{fmt_price(o['subtotal'])} ₽</td>
</tr>"""
        vc = len(vehicles); cav = vc < 3
        avb = ""
        if cav:
            avb = f"""<details class="mt-3"><summary style="cursor:pointer;font-weight:700">{icon("plus", 14)} Добавить автомобиль</summary>
<form method="post" action="/customer_vehicle_add" class="mt-3">
<input type="hidden" name="customer_id" value="{esc(cid)}">
<div class="grid">
<div class="field"><label>VIN</label><input name="vin" placeholder="Любая строка"></div>
<div class="field"><label>Марка</label><input name="make"></div>
<div class="field"><label>Модель</label><input name="model"></div>
<div class="field"><label>Год</label><input name="year"></div>
<div class="field"><label>Двигатель</label><input name="engine"></div>
<div class="field"><label>Кузов</label><input name="body_class"></div>
<div class="field"><label>Псевдоним</label><input name="nickname"></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">Добавить</button></div>
</form></details>"""
        else:
            avb = '<div class="note mt-3">Достигнут лимит 3 автомобиля.</div>'
        body = f"""<div class="card"><div class="card-title">
<div class="customer-header">
<div class="customer-avatar">{esc((c['name'] or '?')[0].upper())}</div>
<div><h1>{esc(c['name'])}</h1>
<div class="muted">{icon("phone", 14)} {esc(c['phone'] or '—')} · {icon("mail", 14)} {esc(c['email'] or '—')}</div>
{f'<div class="muted mt-2">{esc(c["company"])}</div>' if c['company'] else ''}
{f'<div class="muted mt-2">{esc(c["notes"])}</div>' if c['notes'] else ''}
</div></div>
<a class="btn btn--secondary" href="/customers">{icon("arrow-left", 16)} Клиенты</a>
</div>
<details class="mt-3"><summary style="cursor:pointer;font-weight:700">{icon("pencil", 14)} Редактировать</summary>
<form method="post" action="/customer_update" class="mt-3">
<input type="hidden" name="id" value="{esc(cid)}">
<div class="grid">
<div class="field"><label>Имя</label><input name="name" value="{esc(c['name'])}"></div>
<div class="field"><label>Телефон</label><input name="phone" value="{esc(c['phone'])}"></div>
<div class="field"><label>E-mail</label><input name="email" value="{esc(c['email'])}"></div>
<div class="field"><label>Компания</label><input name="company" value="{esc(c['company'])}"></div>
<div class="field"><label>Город</label><input name="city" value="{esc(c['city'])}"></div>
<div class="field"><label>Комментарий</label><input name="notes" value="{esc(c['notes'])}"></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 14)} Сохранить</button>
<form method="post" action="/customer_delete" data-confirm="Удалить клиента?" style="display:inline"><input type="hidden" name="id" value="{esc(cid)}"><button class="btn btn--danger" type="submit">{icon("trash", 14)} Удалить</button></form>
</div>
</form>
</details>
</div>
<div class="card"><h2>Автомобили ({vc} / 3)</h2>
{vh}
{avb}
</div>
<div class="card"><h2>История заказов</h2>
<div class="table-wrap"><table>
<tr><th>№</th><th>Дата</th><th>Статус</th><th>Сумма</th></tr>
{oh or "<tr><td colspan='4' class='muted'>Заказов пока нет.</td></tr>"}
</table></div></div>"""
        self.out(layout(c["name"], body, current="customers"))

    def _post_add_cart(self, d):
        item = {"id": str(uuid.uuid4()), "supplier_id": d.get("supplier_id", [""])[0],
                "supplier_name": "", "article": d.get("article", [""])[0],
                "brand": d.get("brand", [""])[0], "name": d.get("name", [""])[0],
                "price": d.get("price", [""])[0], "term": d.get("term", [""])[0],
                "qty": max(1, int(d.get("qty", ["1"])[0] or 1)), "offer_meta": {}}
        if d.get("offer_meta", [""])[0]:
            try: item["offer_meta"] = json.loads(d["offer_meta"][0])
            except Exception: item["offer_meta"] = {}
        sup = supplier(item["supplier_id"])
        if sup: item["supplier_name"] = sup.get("name", "")
        c = read("cart"); found = False
        for x in c:
            if normalize_article(x.get("article")) == normalize_article(item["article"]) and \
               (x.get("supplier_id") or "") == (item["supplier_id"] or ""):
                x["qty"] = int(x.get("qty", 1)) + 1; found = True; break
        if not found: c.append(item)
        write("cart", c); self.red("/?added=1")

    def _post_cart_inc(self, d):
        cid = d.get("id", [""])[0]; c = read("cart")
        for x in c:
            if x.get("id") == cid: x["qty"] = int(x.get("qty", 1) or 1) + 1; break
        write("cart", c); self.red("/cart")

    def _post_cart_dec(self, d):
        cid = d.get("id", [""])[0]; c = read("cart"); nc = []
        for x in c:
            if x.get("id") == cid:
                nq = int(x.get("qty", 1) or 1) - 1
                if nq <= 0: continue
                x["qty"] = nq
            nc.append(x)
        write("cart", nc); self.red("/cart")

    def _post_cart_optimize_result(self, d):
        strategy = d.get("strategy", ["standard"])[0]
        analogs = d.get("analogs", ["0"])[0] == "1"
        nc, report, summary = optimize_cart(strategy=strategy, allow_analogs=analogs)
        pn = os.path.join(DATA_DIR, "cart.new.json")
        with _lock:
            with open(pn, "w", encoding="utf-8") as f: json.dump(nc, f, ensure_ascii=False, indent=2)
        backup_cart()
        diff = summary.get("diff", 0)
        dc = "saved" if diff > 0 else ("loss" if diff < 0 else "")
        lbl = "Экономия" if diff > 0 else ("Прирост" if diff < 0 else "Без изменений")
        rows = ""
        for r in report:
            md = (r.get("old_price") or 0) - (r.get("new_price") or 0)
            mc = "saved" if md > 0 else ("loss" if md < 0 else "")
            cm = ' <span class="badge badge--violet">аналог</span>' if r.get("is_cross") else ""
            ac = normalize_article(r.get("article") or "") != normalize_article(r.get("article_new") or "")
            ao = esc(r.get("article") or ""); an = esc(r.get("article_new") or ao)
            ah = f"{ao} <span style='color:var(--warning-700)'>→ {an}</span>" if ac else ao
            frc = r.get("is_forced", False); fm = ""; rc = ""
            if frc:
                rs = esc(r.get("reason", "")); fm = f' <span class="forced-icon" title="{rs}">⚠</span>'; rc = ' class="forced-row"'
            od = r.get("old_days"); nd = r.get("new_days")
            odt = f"{od} дн." if od is not None else "—"; ndt = f"{nd} дн." if nd is not None else "—"
            rows += f"""<tr{rc}>
<td><b>{ah}</b>{cm}{fm}<div class="muted" style="font-size:12px">{esc(r.get('brand') or '')}</div></td>
<td>{esc(r.get('old_supplier') or '—')} · {fmt_price(r.get('old_price') or 0)} ₽ · {esc(odt)}</td>
<td>{esc(r.get('new_supplier') or '—')} · {fmt_price(r.get('new_price') or 0)} ₽ · {esc(ndt)}</td>
<td class="{mc}">{'+' if md > 0 else ''}{fmt_price(md)} ₽</td>
</tr>"""
        body = f"""<div class="card"><div class="card-title"><div><h1>Результат оптимизации</h1>
<div class="muted">Стратегия: {esc(strategy)} · Аналоги: {'разрешены' if analogs else 'нет'}</div></div>
<a class="btn btn--secondary" href="/cart">{icon("arrow-left", 16)} В корзину</a></div>
<div class="grid">
<div class="tile"><span class="nm">Было</span><div class="hint">{fmt_price(summary.get('total_before', 0))} ₽</div></div>
<div class="tile"><span class="nm">Стало</span><div class="hint">{fmt_price(summary.get('total_after', 0))} ₽</div></div>
<div class="tile"><span class="nm">{lbl}</span><div class="hint {dc}">{fmt_price(abs(diff))} ₽</div></div>
<div class="tile"><span class="nm">Крайняя дата</span><div class="hint">{esc(summary.get('last_delivery_date', '—'))}</div></div>
</div></div>
<div class="card"><h2>Изменения</h2>
<div class="table-wrap"><table>
<tr><th>Артикул / Бренд</th><th>Было</th><th>Станет</th><th>Разница</th></tr>
{rows or "<tr><td colspan='4' class='muted'>Нет изменений</td></tr>"}
</table></div></div>
<div class="toolbar">
<form method="post" action="/cart_optimize_apply" style="display:inline"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Применить</button></form>
<a class="btn btn--secondary" href="/cart">Отмена</a></div>"""
        self.out(layout("Оптимизация", body, current="cart"))

    def _post_cart_optimize_apply(self):
        pn = os.path.join(DATA_DIR, "cart.new.json")
        if not os.path.exists(pn):
            self.out(layout("Оптимизация", "<div class='card err'>Нет данных для применения.</div><a class='btn' href='/cart'>← Назад</a>")); return
        try:
            with open(pn, "r", encoding="utf-8") as f: nc = json.load(f)
            if not isinstance(nc, list): raise ValueError("bad")
            backup_cart(); write("cart", nc); os.remove(pn); self.red("/cart")
        except Exception:
            logging.exception("optimize apply")
            self.out(layout("Оптимизация", "<div class='card err'>Ошибка применения.</div><a class='btn' href='/cart'>← Назад</a>"), 500)

    def _post_wizard_save_test(self, d):
        name = d.get("name", [""])[0].strip(); tpl = d.get("tpl", [""])[0]
        if not name or (tpl != "email" and tpl not in API_TYPES): self.red("/wizard1"); return
        if tpl == "email":
            nid = str(uuid.uuid4())
            s = {"id": nid, "name": name, "template": "email", "credentials": {},
                 "order_email": d.get("order_email", [""])[0].strip(),
                 "price_email": d.get("price_email", [""])[0].strip(),
                 "order_channel": d.get("order_channel", ["auto"])[0],
                 "order_api_url": "", "order_api_auth": "bearer", "order_api_key": "",
                 "enabled": True, "created_at": datetime.now().isoformat(timespec="seconds"),
                 "last_test": {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                                "article": "", "error": "Ожидается первый прайс"}}
            if not s["price_email"]:
                self.out(layout("Поставщик", "<div class='card err'>Укажите e-mail для прайсов.</div><a class='btn' href='/wizard3?name=" + quote(name) + "&tpl=email'>← Назад</a>")); return
            ss = read("suppliers"); ss.append(s); write("suppliers", ss)
            self.out(layout("Поставщик добавлен", f"<div class='card'><h1>✓ Поставщик сохранён</h1><p>Прайсы: {esc(s['price_email'])}.</p><a class='btn btn--success' href='/supplier?id={quote(nid)}'>Открыть настройки</a></div>"))
            return
        api = API_TYPES[tpl]; cred = {}; missing = []
        for spec in api["fields"]:
            key, label = spec[0], spec[1]
            req = spec[4] if len(spec) > 4 else False
            val = d.get(key, [""])[0].strip()
            if req and not val: missing.append(label)
            cred[key] = val
        if missing:
            err = "Заполните обязательные поля: " + ", ".join(missing)
            body = f"<div class='card'><h1>Не хватает данных</h1><div class='err'>{esc(err)}</div><div class='toolbar'><a class='btn btn--warning' href='/wizard3?name={quote(name)}&tpl={quote(tpl)}'>← Назад</a></div></div>"
            self.out(layout("Ошибка", body)); return
        nid = str(uuid.uuid4())
        s = {"id": nid, "name": name, "template": tpl, "credentials": cred,
             "order_email": d.get("order_email", [""])[0].strip(),
             "price_email": d.get("price_email", [""])[0].strip(),
             "order_channel": d.get("order_channel", ["auto"])[0],
             "order_api_url": d.get("order_api_url", [""])[0].strip(),
             "order_api_auth": d.get("order_api_auth", ["bearer"])[0],
             "order_api_key": d.get("order_api_key", [""])[0].strip(),
             "enabled": True, "created_at": datetime.now().isoformat(timespec="seconds"),
             "last_test": None}
        try:
            ta = api["test_article"]; tb = api.get("test_brand", "")
            if tpl == "rossko": m, c, err = rossko_search(s, ta)
            elif tpl == "berg": m, c, err = berg_search(s, ta, tb)
            elif tpl == "zappro": m, c, err = zappro_search(s, ta, tb)
            elif tpl == "avd": m, c, err = avd_search(s, ta)
            elif tpl == "autoeuro": m, c, err = autoeuro_search(s, ta, "BOSCH")
            elif tpl == "mparts": m, c, err = mparts_search(s, ta)
            else: m, c, err = custom_search(s, ta)
        except Exception as ex:
            logging.exception("provider test"); m, c, err = [], [], f"ошибка: {ex}"
        s["last_test"] = {"ok": not bool(err), "at": datetime.now().isoformat(timespec="seconds"),
                          "article": api["test_article"], "error": err or ""}
        ss = read("suppliers"); ss.append(s); write("suppliers", ss)
        if err:
            body = f"""<div class="card"><h1>{icon("alert", 24)} Проверка не прошла</h1>
<div class="err"><b>{esc(err)}</b></div>
<div class="note">Поставщик <b>сохранён</b>. Исправьте данные и проверьте снова.</div>
<div class="toolbar"><a class="btn btn--warning" href="/supplier?id={quote(nid)}">Открыть настройки</a>
<a class="btn btn--secondary" href="/suppliers">К списку поставщиков</a></div></div>"""
            self.out(layout("Проверка", body)); return
        cbs = cart_index(); cba = cart_index_by_article()
        h = render_grouped_results(m, c, cbs, cba, cross_sort_mode="price")
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("check-circle", 24)} Всё работает</h1>
<div class="muted">Поставщик: {esc(name)} · Тестовый артикул: {esc(api['test_article'])}</div></div>
<a class="btn btn--secondary" href="/supplier?id={esc(nid)}">Открыть карточку</a></div>
<div class="ok">Найдено: основных {len(m)}, аналогов {len(c)}.</div></div>
{h}
<div class="toolbar"><a class="btn btn--success" href="/suppliers">Готово</a></div>"""
        self.out(layout("Проверка", body))

    def _post_update_supplier(self, d):
        sid = d.get("id", [""])[0]; ss = read("suppliers")
        for s in ss:
            if s.get("id") == sid:
                nt = d.get("template", [s.get("template", "custom")])[0]
                if nt == "email": s["template"] = "email"
                else:
                    if nt not in API_TYPES: nt = "custom"
                    s["template"] = nt
                s["name"] = d.get("name", [s.get("name", "")])[0].strip() or s.get("name", "")
                s["order_email"] = d.get("order_email", [s.get("order_email", "")])[0].strip()
                s["price_email"] = d.get("price_email", [s.get("price_email", "")])[0].strip()
                s["order_channel"] = d.get("order_channel", [s.get("order_channel", "auto")])[0]
                if nt != "email":
                    api = API_TYPES[nt]; cred = s.setdefault("credentials", {})
                    for spec in api["fields"]:
                        key = spec[0]
                        if key in d: cred[key] = d[key][0]
                else: s["credentials"] = {}
        write("suppliers", ss); self.red("/supplier?id=" + quote(sid))

    def _post_provider_test(self, d):
        sid = d.get("id", [""])[0]; s = supplier(sid)
        if not s:
            self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
        tpl = s.get("template", "custom")
        if tpl not in API_TYPES: tpl = "custom"
        api = API_TYPES[tpl]
        article = d.get("article", [""])[0].strip() or api.get("test_article", "") or "LC-1030"
        brand = d.get("brand", [""])[0].strip() or api.get("test_brand", "")
        try:
            if tpl == "rossko": m, c, err = rossko_search(s, article)
            elif tpl == "berg": m, c, err = berg_search(s, article, brand)
            elif tpl == "zappro": m, c, err = zappro_search(s, article, brand)
            elif tpl == "avd": m, c, err = avd_search(s, article, brand)
            elif tpl == "autoeuro": m, c, err = autoeuro_search(s, article, brand or "BOSCH")
            elif tpl == "mparts": m, c, err = mparts_search(s, article, brand)
            else: m, c, err = custom_search(s, article, brand)
        except Exception as ex:
            logging.exception("test"); m, c, err = [], [], f"ошибка: {ex}"
        if err:
            body = f"""<div class="card"><h1>{icon("alert", 24)} Проверка не прошла</h1>
<div class="err"><b>{esc(err)}</b></div>
<div class="toolbar"><a class="btn btn--warning" href="/supplier?id={quote(sid)}">← Назад</a></div></div>"""
            self.out(layout("Проверка", body)); return
        cbs = cart_index(); cba = cart_index_by_article()
        h = render_grouped_results(m, c, cbs, cba, cross_sort_mode="price")
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("check-circle", 24)} Всё работает</h1>
<div class="muted">Найдено: основных {len(m)}, аналогов {len(c)}.</div></div>
<a class="btn btn--secondary" href="/supplier?id={quote(sid)}">← Назад</a></div></div>{h}"""
        self.out(layout("Проверка", body))

    def _post_rossko_checkout(self, d):
        sid = d.get("id", [""])[0]; s = supplier(sid)
        if not s:
            self.out(layout("Ошибка", "<div class='card err'>Поставщик не найден.</div>"), 404); return
        cred = s.get("credentials", {}) or {}
        k1, k2 = cred.get("key1", ""), cred.get("key2", "")
        if not k1 or not k2:
            self.out(layout("ROSSKO", f"<div class='card err'>Нет KEY1/KEY2.</div><a class='btn' href='/supplier?id={esc(sid)}'>← Назад</a>")); return
        env = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="https://api.rossko.ru/">'
               f'<soap:Body><ns:GetCheckoutDetails><ns:KEY1>{xml_escape(k1)}</ns:KEY1><ns:KEY2>{xml_escape(k2)}</ns:KEY2></ns:GetCheckoutDetails></soap:Body></soap:Envelope>').encode("utf-8")
        req = Request(BUILTIN["rossko_details"], data=env, method="POST", headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"https://api.rossko.ru/GetCheckoutDetails"',
            "User-Agent": f"PartsManager/{VERSION}"})
        try:
            with urlopen(req, timeout=60, context=ssl.create_default_context()) as r: raw = r.read()
        except Exception as ex:
            body = ""
            if hasattr(ex, "read"):
                try: body = ex.read().decode("utf-8", "replace")[:500]
                except Exception: pass
            self.out(layout("ROSSKO", f"<div class='card err'>{esc(ex)}<br>{esc(body)}</div><a class='btn' href='/supplier?id={esc(sid)}'>← Назад</a>")); return
        try: root = ET.fromstring(raw)
        except ET.ParseError:
            text = raw.decode("utf-8", "replace")[:5000]
            self.out(layout("ROSSKO", f"<div class='card'><h1>Ответ не XML</h1><div class='xml-box'>{esc(text)}</div><a class='btn' href='/supplier?id={esc(sid)}'>← Назад</a></div>")); return
        dvs, ads = [], []
        for par in root.iter():
            t = par.tag.split("}")[-1]
            if t not in ("delivery", "address"): continue
            vals = {}
            for ch in par.iter():
                ct = ch.tag.split("}")[-1]
                if ch.text and ch.text.strip(): vals[ct] = ch.text.strip()
            if vals:
                if t == "delivery": dvs.append(vals)
                else: ads.append(vals)
        cd = cred.get("delivery_id", ""); ca = cred.get("address_id", "")
        def do(x):
            i = x.get("id") or x.get("delivery_id") or ""
            n = x.get("name") or x.get("delivery_name") or x.get("description") or ""
            s = " selected" if str(i) == str(cd) else ""
            return f'<option value="{esc(i)}"{s}>{esc(i)} — {esc(n)}</option>'
        def ao(x):
            i = x.get("id") or x.get("address_id") or ""
            parts = [x.get("city", ""), x.get("street", ""), x.get("house", ""), x.get("office", "")]
            n = ", ".join(p for p in parts if p) or x.get("name", "") or "—"
            s = " selected" if str(i) == str(ca) else ""
            return f'<option value="{esc(i)}"{s}>{esc(i)} — {esc(n)}</option>'
        opts = "".join(do(x) for x in dvs) or "<option value=''>— нет данных —</option>"
        aopts = "".join(ao(x) for x in ads) or "<option value=''>— нет данных —</option>"
        body = f"""<div class="card"><div class="card-title"><div><h1>{icon("truck", 24)} ROSSKO — варианты доставки</h1>
<div class="muted">Выберите способ доставки и адрес из списка, который вернул ROSSKO.</div></div>
<a class="btn btn--secondary" href="/supplier?id={esc(sid)}">{icon("arrow-left", 16)} Назад</a></div>
<div class="ok">Найдено: способов доставки — {len(dvs)}, адресов — {len(ads)}.</div>
<form method="post" action="/save_rossko_delivery">
<input type="hidden" name="id" value="{esc(sid)}">
<div class="grid">
<div class="field"><label>Способ доставки (delivery_id)</label><select name="delivery_id">{opts}</select></div>
<div class="field"><label>Адрес доставки (address_id)</label><select name="address_id">{aopts}</select></div>
</div>
<div class="toolbar"><button class="btn btn--success" type="submit">{icon("clipboard", 16)} Сохранить выбор</button>
<a class="btn btn--secondary" href="/supplier?id={esc(sid)}">Отмена</a></div>
</form></div>"""
        self.out(layout("ROSSKO — доставка", body))

    def _post_save_rossko_delivery(self, d):
        sid = d.get("id", [""])[0]; ss = read("suppliers")
        for s in ss:
            if s.get("id") == sid:
                s.setdefault("credentials", {})["delivery_id"] = d.get("delivery_id", [""])[0]
                s["credentials"]["address_id"] = d.get("address_id", [""])[0]
        write("suppliers", ss); self.red("/supplier?id=" + quote(sid))

    def _post_place_order(self, d):
        c = read("cart")
        if not c: self.red("/cart"); return
        s = read("settings"); cci = s.get("cart_customer_id") or ""
        groups = {}
        for item in c: groups.setdefault(item.get("supplier_id") or "", []).append(item)
        results = []; sent = set()
        for sid, items in groups.items():
            sup = supplier(sid)
            if not sup: results.append((False, "—", "Поставщик не найден.")); continue
            try:
                oid, num, dup = create_order(items, sup, "Авто", customer_id=cci)
                if dup:
                    results.append((True, sup.get("name") or "—", f"Заказ {num} уже создан — повторно не отправляем."))
                    sent.update(x.get("id") for x in items); continue
                order_event(oid, "Отправляется", "Автоотправка")
                try:
                    ch, ext = send_order_to_supplier(sup, items, s, num)
                    if ext: add_external_number(oid, sup.get("id", ""), sup.get("name", ""), ext)
                    order_event(oid, "Отправлен", f"{ch}. Внешний номер: {ext or '—'}")
                    results.append((True, sup.get("name") or "—", f"Заказ {num} отправлен через {ch}. Внешний номер: {ext or '—'}"))
                    sent.update(x.get("id") for x in items)
                except Exception as ex:
                    logging.exception("send order")
                    order_event(oid, "Ошибка отправки", str(ex))
                    results.append((False, sup.get("name") or "—", str(ex)))
            except Exception as ex:
                logging.exception("create order")
                results.append((False, sup.get("name") or "—", str(ex)))
        if sent: write("cart", [x for x in c if x.get("id") not in sent])
        cards = "".join(f"<div class='{('ok' if ok else 'err')}'><b>{'✅' if ok else '❌'} {esc(n)}</b><br>{esc(m)}</div>" for ok, n, m in results)
        body = f"<div class='card'><h1>Результат</h1>{cards}<div class='toolbar'><a class='btn btn--success' href='/orders'>К заказам</a><a class='btn btn--secondary' href='/cart'>Корзина</a></div></div>"
        self.out(layout("Результат", body, current="orders"))

    def _post_order_status(self, d):
        oid = d.get("id", [""])[0]; status = d.get("status", ["Новый"])[0]
        if status not in ORDER_STATUSES: status = "Новый"
        msg = d.get("message", [""])[0] or "Ручное изменение"
        order_event(oid, status, msg); self.red("/order?id=" + quote(oid))

    def _post_finance_add(self, d):
        amount = money(d.get("amount", ["0"])[0])
        direction = d.get("direction", ["in"])[0]
        kind = d.get("kind", ["Движение"])[0].strip() or "Движение"
        category = d.get("category", ["Прочее"])[0].strip() or "Прочее"
        note = d.get("note", [""])[0].strip()
        if amount > 0:
            record_finance(kind=kind, category=category, amount=amount,
                           direction=direction, note=note)
        self.red("/finance")

    def _post_save_settings(self, d):
        s = read("settings")
        for k in ("company_name", "city", "smtp_host", "smtp_user", "smtp_password",
                  "smtp_from_email", "smtp_from_name", "smtp_security",
                  "imap_host", "imap_user", "imap_password", "imap_folder", "imap_security", "github_repo"):
            if k in d: s[k] = d[k][0]
        if "smtp_port" in d:
            try: s["smtp_port"] = int(d["smtp_port"][0] or 587)
            except ValueError: s["smtp_port"] = 587
        if "imap_port" in d:
            try: s["imap_port"] = int(d["imap_port"][0] or 993)
            except ValueError: s["imap_port"] = 993
        if "price_poll_minutes" in d:
            try: s["price_poll_minutes"] = max(5, int(d["price_poll_minutes"][0] or 15))
            except ValueError: s["price_poll_minutes"] = 15
        s["auto_update"] = d.get("auto_update", ["0"])[0] == "1"
        write("settings", s); self.red("/settings")

    def _post_fetch_prices(self):
        try:
            processed, imported, messages = fetch_supplier_price_emails()
            cards = "".join(f"<div class='ok'>{esc(m)}</div>" for m in messages) or "<div class='ok'>Новых писем нет.</div>"
            body = f"<div class='card'><h1>Получение прайсов</h1><p>Обработано писем: {processed} · Импортировано: {imported}</p>{cards}<div class='toolbar'><a class='btn btn--success' href='/prices'>К прайсам</a><a class='btn btn--secondary' href='/settings'>Настройки</a></div></div>"
            self.out(layout("Прайсы", body))
        except Exception as ex:
            logging.exception("fetch prices")
            self.out(layout("Ошибка", f"<div class='card err'>{esc(ex)}</div><a class='btn' href='/settings'>← Настройки</a>"))

    def _post_customer_add(self, d):
        name = d.get("name", [""])[0].strip(); phone = d.get("phone", [""])[0].strip()
        if not name or not phone:
            self.out(layout("Ошибка", "<div class='card err'>Имя и телефон обязательны.</div><a class='btn' href='/customer_new'>← Назад</a>")); return
        np = normalize_phone(phone)
        ex = customer_by_phone(phone)
        if ex and d.get("force", [""])[0] != "1":
            body = f"""<div class="card"><h1>{icon("alert", 24)} Такой телефон уже есть</h1>
<div class="warn">Клиент с телефоном <b>{esc(np)}</b>: <b>{esc(ex['name'])}</b> · создан {esc(ex['created_at'][:10])}</div>
<div class="toolbar">
<a class="btn btn--success" href="/customer?id={esc(ex['id'])}">Открыть существующего</a>
<form method="post" action="/customer_add" style="display:inline">
<input type="hidden" name="name" value="{esc(name)}"><input type="hidden" name="phone" value="{esc(phone)}">
<input type="hidden" name="email" value="{esc(d.get('email', [''])[0])}">
<input type="hidden" name="company" value="{esc(d.get('company', [''])[0])}">
<input type="hidden" name="city" value="{esc(d.get('city', [''])[0])}">
<input type="hidden" name="notes" value="{esc(d.get('notes', [''])[0])}">
<input type="hidden" name="force" value="1">
<button class="btn btn--warning" type="submit">Всё равно создать</button></form>
<a class="btn btn--secondary" href="/customers">Отмена</a>
</div></div>"""
            self.out(layout("Дубль клиента", body)); return
        cid = create_customer(name, phone, d.get("email", [""])[0], d.get("company", [""])[0],
                              d.get("city", [""])[0], d.get("notes", [""])[0])
        self.red(f"/customer?id={quote(cid)}")

    def _post_customer_vehicle_add(self, d):
        cid = d.get("customer_id", [""])[0]
        if not cid: self.red("/customers"); return
        try:
            add_customer_vehicle(cid, vin=d.get("vin", [""])[0], make=d.get("make", [""])[0],
                model=d.get("model", [""])[0], year=d.get("year", [""])[0],
                engine=d.get("engine", [""])[0], body_class=d.get("body_class", [""])[0],
                nickname=d.get("nickname", [""])[0])
        except ValueError as ex:
            self.out(layout("Ошибка", f"<div class='card err'>{esc(ex)}</div><a class='btn' href='/customer?id={quote(cid)}'>← Назад</a>")); return
        self.red(f"/customer?id={quote(cid)}")

    def _post_customer_set_for_cart(self, d):
        cid = d.get("customer_id", [""])[0]
        s = read("settings"); s["cart_customer_id"] = cid; write("settings", s)
        self.red("/cart")


def price_sync_worker():
    time.sleep(5)
    while True:
        st = read("settings")
        try: minutes = max(5, int(st.get("price_poll_minutes") or 15))
        except Exception: minutes = 15
        try:
            if str(st.get("imap_host") or "").strip() and str(st.get("imap_user") or "").strip():
                pr, im, _ = fetch_supplier_price_emails()
                if pr or im: logging.info("Прайсы: писем=%s, позиций=%s", pr, im)
        except Exception as ex:
            logging.warning("Auto IMAP: %s", ex)
        time.sleep(minutes * 60)


def _find_free_port(host="127.0.0.1", preferred=8000):
    for port in [preferred] + list(range(preferred + 1, preferred + 51)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port)); return port
            except OSError: continue
    raise OSError("Нет свободного порта")


def setup_logging():
    logger = logging.getLogger(); logger.setLevel(logging.INFO)
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    except Exception: pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(sh)


def main():
    setup_logging(); _ensure_files(); init_db()
    start_auto_update()
    threading.Thread(target=price_sync_worker, name="price-sync", daemon=True).start()
    host = "127.0.0.1"
    preferred = int(os.environ.get("PARTS_MANAGER_PORT", "8000"))
    port = _find_free_port(host, preferred)
    srv = ThreadingHTTPServer((host, port), H)
    url = f"http://{host}:{port}"
    print(f"Parts Manager v{VERSION} запущен: {url}")
    print(f"Данные: {DATA_DIR}")
    print(f"Лог:    {LOG_FILE}")
    if os.environ.get("PARTS_MANAGER_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("Остановлено.")
    finally: srv.server_close()


if __name__ == "__main__":
    main()