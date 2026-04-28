import aiohttp, asyncio, time, os, random, re
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
MIN_RATIO = 1.6
MIN_PROFIT = 10000
CHECK_INTERVAL = 40

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PROXIES = []  # thêm proxy nếu có

COLLAB = [
    "travis","off-white","fragment","union","kith",
    "sacai","dior","supreme","トラヴィス","シュプリーム"
]

sent_cache = {}

# ================= CORE =================
def proxy():
    return random.choice(PROXIES) if PROXIES else None

async def fetch(session, url):
    for _ in range(3):
        try:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                proxy=proxy(),
                timeout=15
            ) as res:
                if res.status == 200:
                    return await res.text()
        except:
            await asyncio.sleep(1)
    return None

# ================= TELEGRAM =================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

def error(msg):
    send(f"⚠️ ERROR\n{msg}")

# ================= RETAIL =================
async def nike_launch(session):
    url = "https://www.nike.com/jp/launch"
    html = await fetch(session, url)
    items = []

    if not html:
        return items

    soup = BeautifulSoup(html, "lxml")

    for card in soup.select('div[data-testid="product-card"]'):
       try:
        name = card.select_one('div[data-testid="product-title"]').get_text(strip=True)

        price_text = card.select_one('div[data-testid="product-price"]').get_text(strip=True)
        price = int(re.sub(r"[^\d]", "", price_text))

        link = card.select_one("a")["href"]

        # SKU thường nằm trong link Nike
        sku_match = re.search(r"/([A-Z0-9]{6}-[A-Z0-9]{3})", link.upper())
        sku = sku_match.group(1) if sku_match else None

        items.append({
            "name": name,
            "sku": sku,
            "retail": price,
            "source": "NIKE"
        })

       except:
        continue
           
    if not items:
        for a in soup.find_all("a", href=True):
            try:
                href = a["href"]

                if "/launch/" in href:
                  text = a.get_text(" ", strip=True)

                  sku = re.findall(r"[A-Z0-9]{6}-[A-Z0-9]{3}", text.upper())
                  price = re.findall(r"\d{4,}", text)

                  items.append({
                    "name": text,
                    "sku": sku[0] if sku else None,
                    "retail": int(price[0]) if price else 0,
                    "source": "NIKE_FALLBACK"
            })

            except Exception as e:
                print(f"FALLBACK ERROR: {e}")
                continue

    return items

async def end_launch(session):
    url = "https://launches.endclothing.com/"
    html = await fetch(session, url)

    items = []
    if not html:
        return items

    soup = BeautifulSoup(html, "lxml")

    for p in soup.select(".ProductCard"):
        name = p.get_text(strip=True)
        items.append({
            "name": name,
            "raffle": True,
            "source": "END"
        })

    return items

# ================= RESALE =================
async def stockx_public(session):
    url = "https://stockx.com/api/browse?&_search=nike"
    try:
        async with session.get(url) as r:
            data = await r.json()
    except:
        return []

    items = []
    for p in data.get("Products", []):
        items.append({
            "name": p["title"],
            "sku": p.get("styleId"),
            "resale": p.get("market", {}).get("lowestAsk", 0),
            "volume": p.get("market", {}).get("numberOfAsks", 0),
            "source": "StockX"
        })

    return items

async def goat_fallback(session):
    # fallback nhẹ (demo)
    return []

# ================= LOGIC =================
def is_collab(name):
    return any(k in name.lower() for k in COLLAB)

def match(retail, resale):
    out = []

    for r in resale:
        best = None
        best_score = 0

        for s in retail:
            score = 0

            if r.get("sku") and s.get("sku") and r["sku"] == s["sku"]:
                score = 100
            else:
                score = fuzz.ratio(r["name"], s["name"])

            if score > best_score:
                best = s
                best_score = score

        if best and best_score > 85:
            out.append({
                "name": best["name"],
                "sku": best.get("sku") or r.get("sku"),
                "retail": best.get("retail", 0),
                "resale": r.get("resale", 0),
                "volume": r.get("volume", 0),
                "source": r["source"]
            })

    return out

def filter_deals(data):
    deals = []

    for d in data:
        if d["retail"] == 0 or d["resale"] == 0:
            continue

        ratio = d["resale"] / d["retail"]
        profit = d["resale"] - d["retail"]

        if ratio >= MIN_RATIO and profit >= MIN_PROFIT:
            d["ratio"] = round(ratio, 2)
            d["profit"] = int(profit)
            d["collab"] = is_collab(d["name"])
            deals.append(d)

    return sorted(deals, key=lambda x: x["ratio"], reverse=True)

def should_alert(key, ratio):
    now = time.time()

    if key not in sent_cache:
        sent_cache[key] = {"r": ratio, "t": now}
        return True

    old = sent_cache[key]

    if ratio > old["r"] * 1.1 or now - old["t"] > 1800:
        sent_cache[key] = {"r": ratio, "t": now}
        return True

    return False

# ================= MAIN =================
# ================= MAIN =================
async def main():
    print("GitHub Action bắt đầu quét...")

    async with aiohttp.ClientSession() as session:
        # Xóa bỏ 'while True' để bot chạy xong 1 lần rồi tự đóng
        try:
            nike, end, stockx, goat = await asyncio.gather(
                nike_launch(session),
                end_launch(session),
                stockx_public(session),
                goat_fallback(session)
            )

            retail = nike
            resale = stockx or goat or []

            merged = match(retail, resale)
            deals = filter_deals(merged)

            for d in deals:
                key = d["sku"] or d["name"]
                if should_alert(key, d["ratio"]):
                    tag = "🔥 COLLAB" if d["collab"] else "💰 NORMAL"
                    msg = f"{tag}\n{d['name']}\nSKU: {d['sku']}\nRatio: x{d['ratio']}\nProfit: ¥{d['profit']}"
                    send(msg)

            # Check Raffle
            for r in end:
                key = r["name"]
                if key not in sent_cache:
                    send(f"🎟 RAFFLE\n{r['name']}")
            
        except Exception as e:
            print(f"Lỗi: {e}")

if __name__ == "__main__":
    asyncio.run(main())
