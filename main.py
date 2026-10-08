import os, json, random, sqlite3, asyncio
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BASE=Path(__file__).resolve().parent.parent
load_dotenv(BASE/".env")
BOT_TOKEN=os.getenv("BOT_TOKEN","")
WEB_APP_URL=os.getenv("WEB_APP_URL","")
HOST=os.getenv("API_HOST","0.0.0.0")
PORT=int(os.getenv("PORT",os.getenv("API_PORT","8000")))
DB=BASE/"nomerok.sqlite3"

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      telegram_id INTEGER PRIMARY KEY,
      balance INTEGER NOT NULL DEFAULT 100000
    );
    CREATE TABLE IF NOT EXISTS numbers(
      number TEXT PRIMARY KEY,
      score INTEGER NOT NULL,
      rarity TEXT NOT NULL,
      price INTEGER NOT NULL,
      owner_id INTEGER,
      status TEXT NOT NULL DEFAULT 'owned'
    );
    CREATE TABLE IF NOT EXISTS collection(
      telegram_id INTEGER NOT NULL,
      number TEXT NOT NULL,
      PRIMARY KEY(telegram_id,number)
    );
    """)
    c.commit(); c.close()

def beauty(d):
    s=''.join(map(str,d)); counts={x:s.count(x) for x in set(s)}
    score=30; mx=max(counts.values())
    score += 55 if mx>=5 else 40 if mx>=4 else 23 if mx==3 else 8 if mx==2 else 0
    if len(set(s))==1: score=100
    for pat,pts in [('1234',28),('2345',28),('3456',28),('4567',28),('5678',28),('6789',28),
                    ('9876',25),('8765',25),('7654',25),('6543',25),('5432',25),('4321',25),('3210',25)]:
        if pat in s: score+=pts
    if s==s[::-1]: score+=25
    if any(s[i]==s[i+1] for i in range(6)): score+=4
    return min(100,score)

def rarity(score):
    return "Легендарный" if score>=90 else "Эпический" if score>=75 else "Редкий" if score>=60 else "Необычный" if score>=45 else "Обычный"

def price(score):
    if score>=95:return random.randint(1_000_000,9_000_000)
    if score>=90:return random.randint(250_000,999_999)
    if score>=75:return random.randint(30_000,249_999)
    if score>=60:return random.randint(8_000,29_999)
    if score>=45:return random.randint(2_000,7_999)
    return random.randint(300,1_999)

def generate():
    c=db()
    while True:
        d=[random.randrange(10) for _ in range(7)]
        n=f"+7 {''.join(map(str,d[:3]))} {''.join(map(str,d[3:5]))}-{''.join(map(str,d[5:]))}"
        if c.execute("SELECT 1 FROM numbers WHERE number=?",(n,)).fetchone() is None:
            s=beauty(d); r=rarity(s); p=price(s)
            c.execute("INSERT INTO numbers(number,score,rarity,price) VALUES(?,?,?,?)",(n,s,r,p))
            c.commit(); c.close()
            return {"number":n,"score":s,"rarity":r,"price":p}

def ensure_user(uid):
    c=db(); c.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES(?)",(uid,)); c.commit(); c.close()

app=FastAPI()

INDEX=BASE/"index.html"

@app.get("/", include_in_schema=False)
def home():
    return FileResponse(INDEX)

app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

class User(BaseModel): telegram_id:int
class Action(BaseModel): telegram_id:int; number:str

@app.get("/api/spin")
def spin(telegram_id:int):
    ensure_user(telegram_id); return generate()

@app.get("/api/profile")
def profile(telegram_id:int):
    ensure_user(telegram_id); c=db()
    u=c.execute("SELECT balance FROM users WHERE telegram_id=?",(telegram_id,)).fetchone()
    rows=c.execute("""SELECT n.number,n.score,n.rarity,n.price
                      FROM collection x JOIN numbers n ON n.number=x.number
                      WHERE x.telegram_id=? ORDER BY n.price DESC""",(telegram_id,)).fetchall()
    c.close()
    return {"balance":u["balance"],"collection":[dict(x) for x in rows]}

@app.post("/api/save")
def save(a:Action):
    ensure_user(a.telegram_id); c=db()
    n=c.execute("SELECT number FROM numbers WHERE number=?",(a.number,)).fetchone()
    if not n: c.close(); raise HTTPException(404,"Номер не найден")
    c.execute("INSERT OR IGNORE INTO collection(telegram_id,number) VALUES(?,?)",(a.telegram_id,a.number))
    c.commit(); c.close(); return {"ok":True}

@app.post("/api/sell")
def sell(a:Action):
    ensure_user(a.telegram_id); c=db()
    n=c.execute("SELECT price FROM numbers WHERE number=?",(a.number,)).fetchone()
    if not n: c.close(); raise HTTPException(404,"Номер не найден")
    c.execute("DELETE FROM collection WHERE telegram_id=? AND number=?",(a.telegram_id,a.number))
    c.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?",(n["price"],a.telegram_id))
    c.commit()
    bal=c.execute("SELECT balance FROM users WHERE telegram_id=?",(a.telegram_id,)).fetchone()["balance"]
    c.close(); return {"ok":True,"balance":bal}

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not WEB_APP_URL:
        await update.message.reply_text("Mini App URL пока не настроен.")
        return
    kb=[[InlineKeyboardButton("🎰 Открыть Номерок",web_app=WebAppInfo(url=WEB_APP_URL))]]
    await update.message.reply_text("Добро пожаловать в «Номерок»! Охоться за красивыми комбинациями.",reply_markup=InlineKeyboardMarkup(kb))

async def run_bot():
    if not BOT_TOKEN: raise RuntimeError("Укажи BOT_TOKEN в .env")
    application=Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",start))
    await application.initialize(); await application.start(); await application.updater.start_polling()
    return application

async def main():
    init_db()
    server=uvicorn.Server(uvicorn.Config(app,host=HOST,port=PORT,log_level="info"))
    bot=await run_bot()
    try: await server.serve()
    finally:
        await bot.updater.stop(); await bot.stop(); await bot.shutdown()

if __name__=="__main__":
    asyncio.run(main())
