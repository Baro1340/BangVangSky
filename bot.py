import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
import random
import psycopg2
from psycopg2.extras import Json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from keep_alive import keep_alive

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
LEADERBOARD_CHANNEL_ID = int(os.getenv("LEADERBOARD_CHANNEL_ID", "0"))
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID", "0"))
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

ALLOWED_GUILD_ID = 937257968968286328

# DEBUG CHI TIẾT
print("=" * 50)
print("🔍 DEBUG ENVIRONMENT VARIABLES:")
print(f"DISCORD_TOKEN: {'*' * 10}{TOKEN[-5:] if TOKEN else 'KHÔNG CÓ'}")
print(f"DISCORD_TOKEN length: {len(TOKEN) if TOKEN else 0}")
print(f"LEADERBOARD_CHANNEL_ID: {LEADERBOARD_CHANNEL_ID}")
print(f"NOTIFY_CHANNEL_ID: {NOTIFY_CHANNEL_ID}")
print(f"RIOT_API_KEY: {'*' * 10}{RIOT_API_KEY[-5:] if RIOT_API_KEY else 'KHÔNG CÓ'}")
print(f"DATABASE_URL: {'*' * 10}{DATABASE_URL[-10:] if DATABASE_URL else 'KHÔNG CÓ'}")
print(f"ALLOWED_GUILD_ID: {ALLOWED_GUILD_ID}")
print("=" * 50)

if not TOKEN:
    print("❌ Không tìm thấy DISCORD_TOKEN!")
    exit(1)
if not RIOT_API_KEY:
    print("❌ Không tìm thấy RIOT_API_KEY!")
    exit(1)

UPDATE_INTERVAL_HOURS = 24
DATA_FILE = "players.json"
LEADERBOARD_HISTORY_FILE = "leaderboard_history.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

HEADERS = {"X-Riot-Token": RIOT_API_KEY}

# Múi giờ Việt Nam
VN_TZ = timezone(timedelta(hours=7))

RANK_ORDER = {
    "UNRANKED": -1,
    "IRON": 0, "BRONZE": 4, "SILVER": 8, "GOLD": 12,
    "PLATINUM": 16, "EMERALD": 20, "DIAMOND": 24,
    "MASTER": 28, "GRANDMASTER": 29, "CHALLENGER": 30,
}
DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}
RANK_EMOJI = {
    "IRON": "⚫", "BRONZE": "🟤", "SILVER": "⚪", "GOLD": "🟡",
    "PLATINUM": "🔵", "EMERALD": "🟢", "DIAMOND": "💎",
    "MASTER": "🔮", "GRANDMASTER": "🔴", "CHALLENGER": "🏆",
    "UNRANKED": "❓",
}

# ==================== KIỂM TRA SERVER ====================

async def check_guild(ctx):
    return True

# ==================== DATABASE FUNCTIONS ====================

def init_database():
    """Khởi tạo database và tạo các bảng cần thiết"""
    if not DATABASE_URL:
        print("⚠️ DATABASE_URL not found, skipping database init")
        return False
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS players (
                riot_id TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                discord_id TEXT,
                discord_name TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leaderboard_history (
                id SERIAL PRIMARY KEY,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                date TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

def load_from_db():
    """Đọc dữ liệu từ database"""
    if not DATABASE_URL:
        return None
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        cur.execute("SELECT riot_id, data FROM players")
        rows = cur.fetchall()
        
        players = {}
        for riot_id, data in rows:
            players[riot_id] = data
        
        cur.execute("SELECT message_id FROM leaderboard_history ORDER BY id DESC LIMIT 1")
        msg_row = cur.fetchone()
        
        cur.close()
        conn.close()
        
        result = {"players": players}
        if msg_row:
            result["leaderboard_message_id"] = int(msg_row[0])
        
        print(f"✅ Loaded {len(players)} players from database")
        return result
    except Exception as e:
        print(f"❌ Database load error: {e}")
        return None

def save_to_db(data):
    """Lưu dữ liệu vào database"""
    if not DATABASE_URL:
        return False
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        cur.execute("DELETE FROM players")
        
        for riot_id, player_data in data["players"].items():
            cur.execute(
                "INSERT INTO players (riot_id, data, discord_id, discord_name) VALUES (%s, %s, %s, %s)",
                (riot_id, Json(player_data), player_data.get("discord_id"), player_data.get("discord_name"))
            )
        
        if data.get("leaderboard_message_id"):
            cur.execute("DELETE FROM leaderboard_history")
            cur.execute(
                "INSERT INTO leaderboard_history (message_id, channel_id, date) VALUES (%s, %s, %s)",
                (str(data["leaderboard_message_id"]), 
                 str(LEADERBOARD_CHANNEL_ID),
                 datetime.now(VN_TZ).strftime("%d/%m/%Y"))
            )
        
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Saved {len(data['players'])} players to database")
        return True
    except Exception as e:
        print(f"❌ Database save error: {e}")
        return False

# ==================== JSON FUNCTIONS (FALLBACK) ====================

def load_from_json():
    """Đọc dữ liệu từ file JSON (fallback)"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                print(f"📖 Đọc file JSON: {len(content)} ký tự")
                return json.loads(content)
        except Exception as e:
            print(f"❌ Lỗi đọc file JSON: {e}")
            return {"players": {}}
    return {"players": {}}

def save_to_json(data):
    """Lưu dữ liệu vào file JSON (fallback)"""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ Saved {len(data['players'])} players to JSON")
        return True
    except Exception as e:
        print(f"❌ JSON save error: {e}")
        return False

def load_history():
    """Đọc lịch sử từ file JSON"""
    if os.path.exists(LEADERBOARD_HISTORY_FILE):
        with open(LEADERBOARD_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"messages": []}

def save_history(history):
    """Lưu lịch sử vào file JSON"""
    with open(LEADERBOARD_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ==================== MAIN DATA FUNCTIONS ====================

def load_data():
    """Load data: ưu tiên database, fallback sang JSON"""
    if DATABASE_URL:
        db_data = load_from_db()
        if db_data and db_data["players"]:
            return db_data
    
    print("⚠️ Using JSON fallback for load_data")
    return load_from_json()

def save_data(data):
    """Save data: lưu vào database, JSON là backup"""
    if DATABASE_URL:
        db_success = save_to_db(data)
        if db_success:
            save_to_json(data)
        else:
            print("⚠️ Database save failed, saving to JSON only")
            save_to_json(data)
    else:
        print("✅ No database, saving to JSON only")
        save_to_json(data)

# ==================== RANK FUNCTIONS ====================

def rank_score(p):
    tier = p.get("tier", "UNRANKED").upper()
    division = p.get("division", "IV").upper()
    lp = p.get("lp", 0)
    return RANK_ORDER.get(tier, -1) * 10000 + DIVISION_ORDER.get(division, 0) * 1000 + lp

async def fetch_player_rank(riot_id: str) -> dict:
    if "#" not in riot_id:
        return {"error": "Riot ID phải có dạng Tên#TAG (VD: Faker#KR1)"}

    game_name, tag_line = riot_id.split("#", 1)
    name_enc = game_name.replace(" ", "%20")

    try:
        url1 = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_line}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url1, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                print(f"[DEBUG] PUUID status: {r.status}")
                if r.status != 200:
                    return {"error": f"Không tìm thấy tài khoản `{riot_id}`"}
                account = await r.json()
                puuid = account.get("puuid")

        url2 = f"https://vn2.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url2, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    entries = await r.json()
                else:
                    entries = []

        if not entries:
            url3 = f"https://vn2.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url3, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        return {"error": f"Không tìm thấy summoner `{riot_id}`"}
                    summoner = await r.json()
                    summoner_id = summoner.get("id")

            url4 = f"https://vn2.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url4, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        entries = await r.json()
                    else:
                        entries = []

        solo = next((e for e in entries if e.get("queueType") == "RANKED_SOLO_5x5"), None)
        if not solo:
            solo = next((e for e in entries if e.get("queueType") == "RANKED_FLEX_SR"), None)

        if not solo:
            return {"riot_id": riot_id, "tier": "UNRANKED", "division": "", "lp": 0, "wins": 0, "losses": 0, "winrate": 0}

        tier = solo.get("tier", "UNRANKED")
        division = solo.get("rank", "")
        lp = solo.get("leaguePoints", 0)
        wins = solo.get("wins", 0)
        losses = solo.get("losses", 0)
        total = wins + losses
        winrate = round(wins / total * 100) if total > 0 else 0

        return {"riot_id": riot_id, "tier": tier, "division": division, "lp": lp, "wins": wins, "losses": losses, "winrate": winrate}

    except Exception as e:
        import traceback
        print(f"[ERROR] {traceback.format_exc()}")
        return {"error": str(e)}

# ==================== EMBED FUNCTIONS ====================

def build_leaderboard_embed(players):
    """Tạo embed bảng xếp hạng với giờ Việt Nam"""
    vn_now = datetime.now(VN_TZ)
    
    embed = discord.Embed(
        title=f"🏆 Bảng Xếp Hạng LoL — VN2", 
        color=0xC89B3C, 
        timestamp=datetime.now(timezone.utc)
    )
    
    if not players:
        embed.description = "Chưa có ai.\nDùng `!register <Tên#TAG>` để đăng ký!"
        embed.set_footer(text="Cập nhật mỗi ngày lúc 7h sáng • Dùng !bangvang để xem bảng mới nhất")
        return embed
    
    sorted_players = sorted(players, key=rank_score, reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    
    for i, p in enumerate(sorted_players):
        pos = medals[i] if i < 3 else f"`{i+1}.`"
        tier = p.get("tier", "UNRANKED")
        emoji = RANK_EMOJI.get(tier.upper(), "❓")
        division = p.get("division", "")
        lp = p.get("lp", 0)
        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        winrate = p.get("winrate", 0)
        riot_id = p.get("riot_id", "Unknown")
        discord_name = p.get("discord_name", "")
        tag = f" ({discord_name})" if discord_name else ""
        
        if tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            rank_str = f"{tier} {lp} LP"
        elif tier.upper() == "UNRANKED":
            rank_str = "Unranked"
        else:
            rank_str = f"{tier} {division} {lp} LP"
        
        lines.append(f"{pos} {emoji} **{riot_id}**{tag}\n　`{rank_str}` | {winrate}% WR ({wins}W/{losses}L)")
    
    embed.description = "\n\n".join(lines)
    embed.set_footer(text="Cập nhật mỗi ngày lúc 7h sáng • Dùng !bangvang để xem bảng mới nhất")
    return embed

# ==================== TASKS ====================

async def get_time_until_7am():
    """Tính thời gian chờ đến 7h sáng hôm sau"""
    vn_now = datetime.now(VN_TZ)
    target = vn_now.replace(hour=7, minute=0, second=0, microsecond=0)
    
    if vn_now >= target:
        target += timedelta(days=1)
    
    target_utc = target.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    
    wait_seconds = (target_utc - now_utc).total_seconds()
    return wait_seconds, target

@tasks.loop(hours=24)
async def daily_leaderboard():
    """Tạo bảng xếp hạng mới lúc 7h sáng giờ Việt Nam"""
    data = load_data()
    if not data["players"]:
        return
    
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return
    
    notify_ch = bot.get_channel(NOTIFY_CHANNEL_ID)
    vn_now = datetime.now(VN_TZ)
    print(f"[{vn_now.strftime('%H:%M:%S %d/%m/%Y')}] Đang tạo bảng xếp hạng mới (7h sáng GMT+7)...")
    
    updated_players = []
    rank_changes = []
    
    for riot_id, pdata in data["players"].items():
        old_tier = pdata.get("tier", "UNRANKED")
        old_lp = pdata.get("lp", 0)
        old_division = pdata.get("division", "")
        
        new = await fetch_player_rank(riot_id)
        await asyncio.sleep(1.5)
        
        if "error" in new:
            updated_players.append(pdata)
            continue
        
        new["discord_name"] = pdata.get("discord_name", "")
        new["discord_id"] = pdata.get("discord_id", None)
        data["players"][riot_id] = new
        updated_players.append(new)
        
        new_tier = new.get("tier", "UNRANKED")
        new_lp = new.get("lp", 0)
        new_division = new.get("division", "")
        
        if new_tier != old_tier or new_division != old_division:
            direction = "📈" if rank_score(new) > rank_score(pdata) else "📉"
            rank_changes.append(f"{direction} **{riot_id}**: `{old_tier} {old_division}` → `{new_tier} {new_division}`")
        elif new_lp != old_lp:
            diff = new_lp - old_lp
            sign = "+" if diff > 0 else ""
            icon = "📈" if diff > 0 else "📉"
            rank_changes.append(f"{icon} **{riot_id}**: {sign}{diff} LP ({old_lp} → {new_lp})")
    
    save_data(data)
    
    embed = build_leaderboard_embed(updated_players)
    new_msg = await channel.send(embed=embed)
    
    history = load_history()
    history["messages"].append({
        "message_id": new_msg.id,
        "channel_id": channel.id,
        "date": vn_now.strftime("%d/%m/%Y"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_history(history)
    
    if rank_changes and notify_ch:
        notif = discord.Embed(
            title=f"📊 Cập nhật rank • {vn_now.strftime('%H:%M %d/%m/%Y')}", 
            description="\n".join(rank_changes[:10]) + ("\n..." if len(rank_changes) > 10 else ""), 
            color=0x5865F2, 
            timestamp=datetime.now(timezone.utc)
        )
        notif.set_footer(text=f"Tổng số thay đổi: {len(rank_changes)}")
        await notify_ch.send(embed=notif)
    
    print(f"[OK] Đã tạo bảng xếp hạng mới lúc {vn_now.strftime('%H:%M:%S %d/%m/%Y')} với {len(updated_players)} người.")

@daily_leaderboard.before_loop
async def before_daily():
    """Đợi đến 7h sáng hôm sau để chạy lần đầu"""
    await bot.wait_until_ready()
    
    await asyncio.sleep(random.randint(5, 15))
    
    wait_seconds, target_time = await get_time_until_7am()
    
    hours = int(wait_seconds // 3600)
    minutes = int((wait_seconds % 3600) // 60)
    
    print(f"⏰ Lần chạy đầu tiên: {target_time.strftime('%H:%M %d/%m/%Y')} (giờ Việt Nam)")
    print(f"⏳ Còn {hours} giờ {minutes} phút nữa sẽ tạo bảng đầu tiên")
    print(f"📅 Sau đó tự động cập nhật lúc 7h sáng mỗi ngày")
    
    await asyncio.sleep(wait_seconds)

# ==================== COMMANDS ====================

@bot.command(name="bangvang")
async def bangvang_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    msg = await ctx.send("🔄 Đang cập nhật rank và tạo bảng xếp hạng...")
    
    data = load_data()
    
    if not data["players"]:
        await msg.edit(content="❌ Chưa có người chơi nào. Dùng `!register <Tên#TAG>` để đăng ký!")
        return
    
    updated_players = []
    rank_changes = []
    
    await msg.edit(content="🔄 Đang cập nhật rank từ Riot API... (có thể mất 10-20 giây)")
    
    for riot_id, pdata in data["players"].items():
        old_tier = pdata.get("tier", "UNRANKED")
        old_lp = pdata.get("lp", 0)
        old_division = pdata.get("division", "")
        
        new = await fetch_player_rank(riot_id)
        await asyncio.sleep(1.5)
        
        if "error" in new:
            updated_players.append(pdata)
            continue
        
        new["discord_name"] = pdata.get("discord_name", "")
        new["discord_id"] = pdata.get("discord_id", None)
        data["players"][riot_id] = new
        updated_players.append(new)
        
        new_tier = new.get("tier", "UNRANKED")
        new_lp = new.get("lp", 0)
        new_division = new.get("division", "")
        
        if new_tier != old_tier or new_division != old_division:
            direction = "📈" if rank_score(new) > rank_score(pdata) else "📉"
            rank_changes.append(f"{direction} **{riot_id}**: `{old_tier} {old_division}` → `{new_tier} {new_division}`")
        elif new_lp != old_lp:
            diff = new_lp - old_lp
            sign = "+" if diff > 0 else ""
            icon = "📈" if diff > 0 else "📉"
            rank_changes.append(f"{icon} **{riot_id}**: {sign}{diff} LP ({old_lp} → {new_lp})")
    
    save_data(data)
    
    embed = build_leaderboard_embed(updated_players)
    
    await msg.delete()
    new_msg = await ctx.send(embed=embed)
    
    history = load_history()
    history["messages"].append({
        "message_id": new_msg.id,
        "channel_id": ctx.channel.id,
        "date": datetime.now(VN_TZ).strftime("%d/%m/%Y"),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_history(history)
    
    if rank_changes:
        notif = discord.Embed(
            title=f"📊 Cập nhật rank • {datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}", 
            description="\n".join(rank_changes[:10]) + ("\n..." if len(rank_changes) > 10 else ""), 
            color=0x5865F2, 
            timestamp=datetime.now(timezone.utc)
        )
        notif.set_footer(text=f"Tổng số thay đổi: {len(rank_changes)} • Người dùng: {ctx.author.display_name}")
        
        await ctx.send(embed=notif)
        
        if NOTIFY_CHANNEL_ID:
            notify_ch = bot.get_channel(NOTIFY_CHANNEL_ID)
            if notify_ch and ctx.channel.id != NOTIFY_CHANNEL_ID:
                await notify_ch.send(embed=notif)

@bot.command(name="register")
async def register(ctx, *, riot_id: str = None):
    if not await check_guild(ctx):
        return
    
    if not riot_id:
        await ctx.send("❌ Cú pháp: `!register <Tên#TAG>`\nVD: `!register 0 Iu Them Mot Ai#01053`")
        return
    if "#" not in riot_id:
        await ctx.send("❌ Riot ID phải có dạng `Tên#TAG`")
        return
    
    data = load_data()
    existing = next((k for k, v in data["players"].items() if v.get("discord_id") == ctx.author.id), None)
    if existing:
        await ctx.send(f"⚠️ Bạn đã đăng ký **{existing}** rồi. Dùng `!unregister` để hủy trước.")
        return
    
    msg = await ctx.send(f"⏳ Đang tìm **{riot_id}**...")
    result = await fetch_player_rank(riot_id)
    
    if "error" in result:
        await msg.edit(content=f"❌ {result['error']}")
        return
    
    result["discord_name"] = ctx.author.display_name
    result["discord_id"] = ctx.author.id
    data["players"][riot_id] = result
    save_data(data)
    
    tier = result.get("tier", "UNRANKED")
    emoji = RANK_EMOJI.get(tier.upper(), "❓")
    division = result.get("division", "")
    lp = result.get("lp", 0)
    rank_str = f"{tier} {division} {lp} LP".strip() if tier != "UNRANKED" else "Unranked"
    
    await msg.edit(content=f"✅ **{ctx.author.display_name}** đã đăng ký!\n{emoji} **{riot_id}** — `{rank_str}`")

@bot.command(name="addplayer")
@commands.has_permissions(manage_guild=True)
async def add_player(ctx, riot_id: str = None, member: discord.Member = None):
    if not await check_guild(ctx):
        return
    
    if not riot_id:
        await ctx.send("❌ Cú pháp: `!addplayer <Tên#TAG> [@discord]`")
        return
    
    msg = await ctx.send(f"⏳ Đang tìm **{riot_id}**...")
    result = await fetch_player_rank(riot_id)
    
    if "error" in result:
        await msg.edit(content=f"❌ {result['error']}")
        return
    
    data = load_data()
    result["discord_name"] = member.display_name if member else ""
    result["discord_id"] = member.id if member else None
    data["players"][riot_id] = result
    save_data(data)
    
    emoji = RANK_EMOJI.get(result.get("tier", "UNRANKED").upper(), "❓")
    await msg.edit(content=f"✅ Đã thêm {emoji} **{riot_id}**!")

@bot.command(name="unregister")
async def unregister(ctx):
    if not await check_guild(ctx):
        return
    
    data = load_data()
    found = next((k for k, v in data["players"].items() if v.get("discord_id") == ctx.author.id), None)
    
    if not found:
        await ctx.send("❌ Bạn chưa đăng ký. Dùng `!register <Tên#TAG>`.")
        return
    
    del data["players"][found]
    save_data(data)
    await ctx.send(f"✅ Đã xóa **{found}** khỏi bảng.")

@bot.command(name="removeplayer")
@commands.has_permissions(manage_guild=True)
async def remove_player(ctx, *, riot_id: str):
    if not await check_guild(ctx):
        return
    
    data = load_data()
    if riot_id in data["players"]:
        del data["players"][riot_id]
        save_data(data)
        await ctx.send(f"✅ Đã xóa **{riot_id}**.")
    else:
        await ctx.send(f"❌ Không tìm thấy `{riot_id}`.")

@bot.command(name="rank")
async def rank_cmd(ctx, *, riot_id: str = None):
    if not await check_guild(ctx):
        return
    
    if not riot_id:
        await ctx.send("❌ Cú pháp: `!rank <Tên#TAG>`")
        return
    
    msg = await ctx.send(f"⏳ Đang tải rank **{riot_id}**...")
    result = await fetch_player_rank(riot_id)
    
    if "error" in result:
        await msg.edit(content=f"❌ {result['error']}")
        return
    
    tier = result.get("tier", "UNRANKED")
    emoji = RANK_EMOJI.get(tier.upper(), "❓")
    division = result.get("division", "")
    lp = result.get("lp", 0)
    winrate = result.get("winrate", 0)
    wins = result.get("wins", 0)
    losses = result.get("losses", 0)
    
    color = 0xC89B3C if winrate >= 55 else (0x5865F2 if winrate >= 50 else 0xFF4444)
    embed = discord.Embed(title=f"{emoji} {riot_id}", color=color)
    
    if tier == "UNRANKED":
        embed.add_field(name="Rank", value="`Unranked`", inline=True)
    elif tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        embed.add_field(name="Rank", value=f"`{tier} {lp} LP`", inline=True)
    else:
        embed.add_field(name="Rank", value=f"`{tier} {division}`", inline=True)
        embed.add_field(name="LP", value=f"`{lp} LP`", inline=True)
    
    embed.add_field(name="Win Rate", value=f"`{winrate}%`", inline=True)
    embed.add_field(name="W / L", value=f"`{wins}W / {losses}L`", inline=True)
    embed.set_footer(text="VN2 • Riot API")
    
    await msg.edit(content=None, embed=embed)

@bot.command(name="history")
async def history_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    history = load_history()
    if not history["messages"]:
        await ctx.send("❌ Chưa có lịch sử bảng xếp hạng.")
        return
    
    embed = discord.Embed(
        title="📅 Lịch sử bảng xếp hạng",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    
    lines = []
    for i, entry in enumerate(reversed(history["messages"][-10:])):
        jump_url = f"https://discord.com/channels/{ctx.guild.id}/{entry['channel_id']}/{entry['message_id']}"
        lines.append(f"`{i+1}.` **{entry['date']}** — [Xem bảng]({jump_url})")
    
    embed.description = "\n".join(lines)
    embed.set_footer(text="Cập nhật mỗi ngày lúc 7h sáng • Dùng !bangvang để xem bảng mới nhất")
    await ctx.send(embed=embed)

@bot.command(name="players")
async def players_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    data = load_data()
    if not data["players"]:
        await ctx.send("Chưa có ai. Dùng `!register <Tên#TAG>` để đăng ký!")
        return
    
    names = "\n".join([f"• {k}" for k in data["players"].keys()])
    await ctx.send(f"**{len(data['players'])} người:**\n{names}")

@bot.command(name="next")
async def next_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    wait_seconds, target_time = await get_time_until_7am()
    
    hours = int(wait_seconds // 3600)
    minutes = int((wait_seconds % 3600) // 60)
    
    vn_now = datetime.now(VN_TZ)
    
    embed = discord.Embed(
        title="⏰ Lịch cập nhật bảng xếp hạng",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Hiện tại", value=f"{vn_now.strftime('%H:%M %d/%m/%Y')}", inline=True)
    embed.add_field(name="Cập nhật tiếp theo", value=f"{target_time.strftime('%H:%M %d/%m/%Y')}", inline=True)
    embed.add_field(name="Còn lại", value=f"{hours} giờ {minutes} phút", inline=True)
    embed.set_footer(text="Tự động cập nhật lúc 7h sáng mỗi ngày")
    
    await ctx.send(embed=embed)

@bot.command(name="invite")
@commands.has_permissions(administrator=True)
async def invite_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    permissions = discord.Permissions()
    permissions.send_messages = True
    permissions.embed_links = True
    permissions.read_message_history = True
    permissions.add_reactions = True
    permissions.use_slash_commands = True
    
    invite_url = discord.utils.oauth_url(
        bot.user.id,
        permissions=permissions,
        scopes=["bot", "applications.commands"]
    )
    
    embed = discord.Embed(
        title="🔗 Link Invite Bot",
        description=f"[Click vào đây để invite bot]({invite_url})\n\n**Lưu ý:** Bot đã được cấu hình chỉ hoạt động trong server Bảng Vàng Sky.",
        color=0x5865F2
    )
    await ctx.send(embed=embed)

@bot.command(name="help")
async def help_cmd(ctx):
    if not await check_guild(ctx):
        return
    
    embed = discord.Embed(
        title="📖 Hướng dẫn Bot LoL Rank",
        description="Bot tự động cập nhật bảng xếp hạng **lúc 7h sáng mỗi ngày** (giờ Việt Nam)\n\n"
                   "**🔒 BẢO MẬT:** Bot chỉ hoạt động trong server Bảng Vàng Sky!",
        color=0x5865F2
    )
    
    embed.add_field(name="🙋 Mọi người", value=(
        "`!bangvang` — **Cập nhật rank và xem bảng xếp hạng mới nhất**\n"
        "`!register <Tên#TAG>` — Đăng ký tham gia bảng xếp hạng\n"
        "`!unregister` — Hủy đăng ký của bạn\n"
        "`!rank <Tên#TAG>` — Xem rank của một người chơi\n"
        "`!history` — Xem lịch sử các bảng xếp hạng\n"
        "`!players` — Danh sách người chơi đã đăng ký\n"
        "`!next` — Xem lịch cập nhật tiếp theo"
    ), inline=False)
    
    embed.add_field(name="🔧 Admin (quyền quản lý)", value=(
        "`!addplayer <Tên#TAG> [@discord]` — Thêm người chơi (không cần họ tự đăng ký)\n"
        "`!removeplayer <Tên#TAG>` — Xóa người chơi khỏi bảng\n"
        "`!invite` — Tạo link invite bot"
    ), inline=False)
    
    embed.add_field(name="📌 Lưu ý", value=(
        "• Bảng xếp hạng tự động cập nhật lúc **7h sáng** mỗi ngày\n"
        "• **Mọi người đều có thể dùng `!bangvang`** để cập nhật rank và xem bảng mới nhất bất kỳ lúc nào\n"
        "• Dùng `!next` để xem thời gian cập nhật tự động tiếp theo\n"
        "• **🔒 Bot được bảo mật - chỉ hoạt động trong server này!**"
    ), inline=False)
    
    embed.set_footer(text="Cập nhật lúc 7h sáng hàng ngày • Riot API")
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    await asyncio.sleep(random.randint(3, 8))
    
    print(f"✅ Bot online: {bot.user}")
    print(f"📋 Leaderboard channel: {LEADERBOARD_CHANNEL_ID}")
    print(f"🔔 Notify channel: {NOTIFY_CHANNEL_ID}")
    print(f"⏰ Lịch cập nhật: 7h sáng giờ Việt Nam mỗi ngày")
    print(f"💡 Mọi người đều có thể dùng !bangvang để cập nhật và xem bảng")
    print(f"🔒 BẢO MẬT: Bot chỉ hoạt động trong server có ID {ALLOWED_GUILD_ID}")
    
    init_database()
    
    await asyncio.sleep(random.randint(2, 5))
    daily_leaderboard.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Bạn không có quyền dùng lệnh này.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Thiếu tham số. Dùng `!help` để xem hướng dẫn.")
    else:
        print(f"Lỗi: {error}")

# ==================== ENTRY POINT ====================

async def run_bot():
    max_retries = 10
    base_delay = 60
    
    for attempt in range(max_retries):
        try:
            print(f"🔄 Đang kết nối bot đến Discord (lần {attempt + 1})...")
            print(f"📌 Token bắt đầu bằng: {TOKEN[:10]}...")
            await bot.start(TOKEN)
            break
            
        except discord.errors.LoginFailure as e:
            print(f"❌ LỖI LOGIN: Token không hợp lệ! {e}")
            print(f"📌 5 ký tự cuối token: ...{TOKEN[-5:]}")
            print(f"🔄 Vui lòng kiểm tra lại token trên Discord Developer Portal")
            break
            
        except discord.errors.HTTPException as e:
            if e.status == 429:
                delay = base_delay * (2 ** min(attempt, 5)) + random.randint(30, 60)
                print(f"⚠️ Rate limited (attempt {attempt + 1}/{max_retries})")
                print(f"⏳ Chờ {int(delay/60)} phút {delay%60} giây trước khi thử lại...")
                await asyncio.sleep(delay)
            else:
                print(f"❌ Lỗi HTTP: {e}")
                raise e
                
        except Exception as e:
            print(f"❌ Lỗi không xác định: {type(e).__name__}: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    # Khởi động web server trong thread riêng TRƯỚC, sau đó mới chạy bot
    print("🚀 Khởi động web server...")
    keep_alive()
    print("🚀 Bắt đầu chạy bot...")
    asyncio.run(run_bot())