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
DATABASE_URL = os.getenv("RENDER_DATABASE_URL") or os.getenv("DATABASE_URL")

ALLOWED_GUILD_ID = 937257968968286328

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

DATA_FILE = "players.json"
LEADERBOARD_HISTORY_FILE = "leaderboard_history.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

HEADERS = {"X-Riot-Token": RIOT_API_KEY}
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

# ==================== DATABASE ====================

def init_database():
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
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT riot_id, data FROM players")
        rows = cur.fetchall()
        players = {riot_id: data for riot_id, data in rows}
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
    if not DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("DELETE FROM players")
        for riot_id, player_data in data["players"].items():
            cur.execute(
                "INSERT INTO players (riot_id, data, discord_id, discord_name, puuid) VALUES (%s, %s, %s, %s, %s)",
                (riot_id, Json(player_data), player_data.get("discord_id"), player_data.get("discord_name"), player_data.get("puuid"))
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

# ==================== JSON FALLBACK ====================

def load_from_json():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except Exception as e:
            print(f"❌ Lỗi đọc file JSON: {e}")
    return {"players": {}}

def save_to_json(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"❌ JSON save error: {e}")
        return False

def load_history():
    if os.path.exists(LEADERBOARD_HISTORY_FILE):
        with open(LEADERBOARD_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"messages": []}

def save_history(history):
    with open(LEADERBOARD_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def load_data():
    if DATABASE_URL:
        db_data = load_from_db()
        if db_data and db_data["players"]:
            return db_data
    print("⚠️ Using JSON fallback")
    return load_from_json()

def save_data(data):
    if DATABASE_URL:
        if not save_to_db(data):
            print("⚠️ Database save failed, saving to JSON only")
            save_to_json(data)
        else:
            save_to_json(data)
    else:
        save_to_json(data)

# ==================== RANK ====================

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
                if r.status != 200:
                    return {"error": f"Không tìm thấy tài khoản `{riot_id}`"}
                account = await r.json()
                puuid = account.get("puuid")

        url2 = f"https://vn2.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url2, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                entries = await r.json() if r.status == 200 else []

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
                    entries = await r.json() if r.status == 200 else []

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

        return {"riot_id": riot_id, "puuid": puuid, "tier": tier, "division": division, "lp": lp, "wins": wins, "losses": losses, "winrate": winrate}

    except Exception as e:
        import traceback
        print(f"[ERROR] {traceback.format_exc()}")
        return {"error": str(e)}

# ==================== EMBED ====================

def build_leaderboard_embed(players):
    embed = discord.Embed(
        title="🏆 Bảng Xếp Hạng LoL — VN2",
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
    vn_now = datetime.now(VN_TZ)
    target = vn_now.replace(hour=7, minute=0, second=0, microsecond=0)
    if vn_now >= target:
        target += timedelta(days=1)
    wait_seconds = (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    return wait_seconds, target

@tasks.loop(hours=24)
async def daily_leaderboard():
    data = load_data()
    if not data["players"]:
        return

    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return

    notify_ch = bot.get_channel(NOTIFY_CHANNEL_ID)
    vn_now = datetime.now(VN_TZ)
    print(f"[{vn_now.strftime('%H:%M:%S %d/%m/%Y')}] Đang tạo bảng xếp hạng mới...")

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

    print(f"[OK] Đã tạo bảng lúc {vn_now.strftime('%H:%M:%S %d/%m/%Y')} với {len(updated_players)} người.")

@daily_leaderboard.before_loop
async def before_daily():
    await bot.wait_until_ready()
    await asyncio.sleep(random.randint(5, 15))
    wait_seconds, target_time = await get_time_until_7am()
    hours = int(wait_seconds // 3600)
    minutes = int((wait_seconds % 3600) // 60)
    print(f"⏰ Lần chạy đầu tiên: {target_time.strftime('%H:%M %d/%m/%Y')} (giờ Việt Nam)")
    print(f"⏳ Còn {hours} giờ {minutes} phút nữa")
    await asyncio.sleep(wait_seconds)

# ==================== COMMANDS ====================

@bot.command(name="bangvang")
async def bangvang_cmd(ctx):
    msg = await ctx.send("🔄 Đang cập nhật rank từ Riot API... (có thể mất 10-20 giây)")
    data = load_data()

    if not data["players"]:
        await msg.edit(content="❌ Chưa có người chơi nào. Dùng `!register <Tên#TAG>` để đăng ký!")
        return

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
    if not riot_id:
        await ctx.send("❌ Cú pháp: `!register <Tên#TAG>`\nVD: `!register Faker#KR1`")
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

@bot.command(name="unregister")
async def unregister(ctx):
    data = load_data()
    found = next((k for k, v in data["players"].items() if v.get("discord_id") == ctx.author.id), None)
    if not found:
        await ctx.send("❌ Bạn chưa đăng ký. Dùng `!register <Tên#TAG>`.")
        return
    del data["players"][found]
    save_data(data)
    await ctx.send(f"✅ Đã xóa **{found}** khỏi bảng.")

@bot.command(name="addplayer")
@commands.has_permissions(manage_guild=True)
async def add_player(ctx, riot_id: str = None, member: discord.Member = None):
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

@bot.command(name="removeplayer")
@commands.has_permissions(manage_guild=True)
async def remove_player(ctx, *, riot_id: str):
    data = load_data()
    if riot_id in data["players"]:
        del data["players"][riot_id]
        save_data(data)
        await ctx.send(f"✅ Đã xóa **{riot_id}**.")
    else:
        await ctx.send(f"❌ Không tìm thấy `{riot_id}`.")

@bot.command(name="rank")
async def rank_cmd(ctx, *, riot_id: str = None):
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
    history = load_history()
    if not history["messages"]:
        await ctx.send("❌ Chưa có lịch sử bảng xếp hạng.")
        return
    embed = discord.Embed(title="📅 Lịch sử bảng xếp hạng", color=0x5865F2, timestamp=datetime.now(timezone.utc))
    lines = []
    for i, entry in enumerate(reversed(history["messages"][-10:])):
        jump_url = f"https://discord.com/channels/{ctx.guild.id}/{entry['channel_id']}/{entry['message_id']}"
        lines.append(f"`{i+1}.` **{entry['date']}** — [Xem bảng]({jump_url})")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Dùng !bangvang để xem bảng mới nhất")
    await ctx.send(embed=embed)

@bot.command(name="players")
async def players_cmd(ctx):
    data = load_data()
    if not data["players"]:
        await ctx.send("Chưa có ai. Dùng `!register <Tên#TAG>` để đăng ký!")
        return
    names = "\n".join([f"• {k}" for k in data["players"].keys()])
    await ctx.send(f"**{len(data['players'])} người:**\n{names}")

@bot.command(name="next")
async def next_cmd(ctx):
    wait_seconds, target_time = await get_time_until_7am()
    hours = int(wait_seconds // 3600)
    minutes = int((wait_seconds % 3600) // 60)
    vn_now = datetime.now(VN_TZ)
    embed = discord.Embed(title="⏰ Lịch cập nhật bảng xếp hạng", color=0x5865F2, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Hiện tại", value=f"{vn_now.strftime('%H:%M %d/%m/%Y')}", inline=True)
    embed.add_field(name="Cập nhật tiếp theo", value=f"{target_time.strftime('%H:%M %d/%m/%Y')}", inline=True)
    embed.add_field(name="Còn lại", value=f"{hours} giờ {minutes} phút", inline=True)
    embed.set_footer(text="Tự động cập nhật lúc 7h sáng mỗi ngày")
    await ctx.send(embed=embed)

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="📖 Hướng dẫn Bot LoL Rank",
        description="Bot tự động cập nhật bảng xếp hạng **lúc 7h sáng mỗi ngày** (giờ Việt Nam)",
        color=0x5865F2
    )
    embed.add_field(name="🙋 Mọi người", value=(
        "`!bangvang` — Cập nhật và xem bảng xếp hạng\n"
        "`!register <Tên#TAG>` — Đăng ký tham gia\n"
        "`!unregister` — Hủy đăng ký\n"
        "`!rank <Tên#TAG>` — Xem rank một người\n"
        "`!history` — Lịch sử các bảng\n"
        "`!players` — Danh sách người chơi\n"
        "`!next` — Lịch cập nhật tiếp theo"
    ), inline=False)
    embed.add_field(name="🔧 Admin", value=(
        "`!addplayer <Tên#TAG> [@discord]` — Thêm người chơi\n"
        "`!removeplayer <Tên#TAG>` — Xóa người chơi"
    ), inline=False)
    embed.set_footer(text="Cập nhật lúc 7h sáng hàng ngày • Riot API VN2")
    await ctx.send(embed=embed)

# ==================== EVENTS ====================

@bot.event
async def on_ready():
    await asyncio.sleep(random.randint(3, 8))
    print(f"✅ Bot online: {bot.user}")
    print(f"📋 Leaderboard channel: {LEADERBOARD_CHANNEL_ID}")
    print(f"🔔 Notify channel: {NOTIFY_CHANNEL_ID}")
    print(f"⏰ Tự động cập nhật lúc 7h sáng giờ Việt Nam")
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
        print(f"Lỗi lệnh: {error}")

# ==================== ENTRY POINT ====================

async def run_bot():
    for attempt in range(10):
        try:
            print(f"🔄 Đang kết nối Discord (lần {attempt + 1})...")
            await bot.start(TOKEN)
            break
        except discord.errors.LoginFailure as e:
            print(f"❌ LỖI LOGIN: Token không hợp lệ! {e}")
            break
        except discord.errors.PrivilegedIntentsRequired as e:
            print(f"❌ LỖI INTENTS: Chưa bật Privileged Intents! {e}")
            break
        except discord.errors.HTTPException as e:
            print(f"❌ LỖI HTTP {e.status}: {e.text[:200]}")
            if e.status == 429:
                wait_minutes = 30
                print(f"⏳ Bị rate limit, chờ {wait_minutes} phút trước khi thử lại...")
                await asyncio.sleep(wait_minutes * 60)
            else:
                break
        except Exception as e:
            import traceback
            print(f"❌ LỖI: {type(e).__name__}: {e}")
            print(traceback.format_exc())
            await asyncio.sleep(30)

if __name__ == "__main__":
    print("🚀 Khởi động web server (keep alive)...")
    keep_alive()
    print("🚀 Bắt đầu chạy bot...")
    asyncio.run(run_bot())