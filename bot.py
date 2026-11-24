import asyncio, hashlib, hmac, json, os, random, sqlite3
from datetime import date, timedelta
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (Message, InlineKeyboardMarkup, InlineKeyboardButton,
                           WebAppInfo, MenuButtonWebApp)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])          # сюда приходят выигрыши
WEBAPP_URL = os.environ["WEBAPP_URL"]           # https://твой-домен (см. README ниже)
PORT = int(os.getenv("PORT", 8080))

# (эмодзи, название, вес). Порядок = порядок секторов на колесе.
PRIZES = [("🧸", "Мишка", 2), ("❌", "Не повезло", 12), ("🌹", "Роза", 5),
          ("❌", "Не повезло", 12), ("🎁", "Подарок", 1), ("❌", "Не повезло", 12),
          ("🎂", "Тортик", 3), ("💝", "Валентинка", 4)]
DAILY, PER_FRIEND, STREAK_BONUS_EVERY = 1, 1, 7  # +1 бонус каждый 7-й день серии

db = sqlite3.connect("bot.db")
db.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, spins INTEGER DEFAULT 0,
    last_daily TEXT, streak INTEGER DEFAULT 0, ref_by INTEGER, friends INTEGER DEFAULT 0)""")
db.commit()
bot, dp, BOT_USER = Bot(TOKEN), Dispatcher(), ""


def register(uid, ref=None):
    if db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        return False
    ref = ref if ref and ref != uid and db.execute("SELECT 1 FROM users WHERE id=?", (ref,)).fetchone() else None
    db.execute("INSERT INTO users(id, ref_by) VALUES(?,?)", (uid, ref))
    if ref:
        db.execute("UPDATE users SET spins=spins+?, friends=friends+1 WHERE id=?", (PER_FRIEND, ref))
    db.commit()
    return ref or False


def daily(uid):
    today = date.today()
    last, streak = db.execute("SELECT last_daily, streak FROM users WHERE id=?", (uid,)).fetchone()
    if last == str(today):
        return
    streak = streak + 1 if last == str(today - timedelta(days=1)) else 1
    bonus = 1 if streak % STREAK_BONUS_EVERY == 0 else 0
    db.execute("UPDATE users SET spins=spins+?, last_daily=?, streak=? WHERE id=?",
               (DAILY + bonus, str(today), streak, uid))
    db.commit()


def auth(init):
    d = dict(parse_qsl(init)); h = d.pop("hash", "")
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h)
    return json.loads(d["user"]) if ok and "user" in d else None


def state(uid):
    spins, friends, streak = db.execute("SELECT spins, friends, streak FROM users WHERE id=?", (uid,)).fetchone()
    total = sum(p[2] for p in PRIZES)
    odds = {}
    for e, n, w in PRIZES:
        odds[(e, n)] = odds.get((e, n), 0) + w / total * 100
    return {"spins": spins, "friends": friends, "streak": streak,
            "link": f"https://t.me/{BOT_USER}?start={uid}",
            "prizes": [{"e": p[0], "n": p[1]} for p in PRIZES],
            "odds": [{"e": e, "n": n, "p": round(v, 1)} for (e, n), v in odds.items()]}


async def api_state(r):
    u = auth((await r.json()).get("initData", ""))
    if not u:
        return web.json_response({"error": "auth"}, status=401)
    register(u["id"]); daily(u["id"])
    return web.json_response(state(u["id"]))


async def api_spin(r):
    u = auth((await r.json()).get("initData", ""))
    if not u:
        return web.json_response({"error": "auth"}, status=401)
    cur = db.execute("UPDATE users SET spins=spins-1 WHERE id=? AND spins>0", (u["id"],))
    db.commit()
    if cur.rowcount == 0:
        return web.json_response({"error": "no_spins"}, status=400)
    i = random.choices(range(len(PRIZES)), weights=[p[2] for p in PRIZES])[0]
    e, n, _ = PRIZES[i]
    if e != "❌":
        await bot.send_message(ADMIN_ID, f"Выигрыш: {e} {n}\n{u.get('first_name')} "
                                         f"(@{u.get('username')}, id {u['id']})")
    return web.json_response({"i": i, "spins": state(u["id"])["spins"]})


async def index(_):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    ref = int(command.args) if command.args and command.args.isdigit() else None
    inviter = register(m.from_user.id, ref)
    if inviter:
        await bot.send_message(inviter, f"🎉 Друг зашёл по твоей ссылке! +{PER_FRIEND} прокрут")
    await m.answer("Крути колесо и выигрывай призы! 🎡", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎡 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]]))


async def main():
    global BOT_USER
    app = web.Application()
    app.add_routes([web.get("/", index), web.post("/api/state", api_state), web.post("/api/spin", api_spin)])
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    BOT_USER = (await bot.get_me()).username
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🎡 Играть", web_app=WebAppInfo(url=WEBAPP_URL)))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
