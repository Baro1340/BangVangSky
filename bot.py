import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from keep_alive import keep_alive	


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
LEADERBOARD_CHANNEL_ID = int(os.getenv("LEADERBOARD_CHANNEL_ID", "0"))
NOTIFY_CHANNEL_ID = int(os.getenv("NOTIFY_CHANNEL_ID", "0"))
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

# Thêm debug
if not TOKEN:
    print("❌ Không tìm thấy DISCORD_TOKEN trong environment variables!")
    # Không exit ngay vì Render sẽ hiển thị lỗi

UPDATE_INTERVAL_HOURS = 24  # 24 giờ kể từ khi start
DATA_FILE = "players.json"
LEADERBOARD_HISTORY_FILE = "leaderboard_history.json"

if not TOKEN:
    print("❌ Không tìm thấy DISCORD_TOKEN trong file .env!")
    exit(1)
if not RIOT_API_KEY:
    print("❌ Không tìm thấy RIOT_API_KEY trong file .env!")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

HEADERS = {"X-Riot-Token": RIOT_API_KEY}

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

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"players": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_history():
    if os.path.exists(LEADERBOARD_HISTORY_FILE):
        with open(LEADERBOARD_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"messages": []}

def save_history(history):
    with open(LEADERBOARD_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

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
        # Bước 1: Lấy PUUID
        url1 = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name_enc}/{tag_line}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url1, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                print(f"[DEBUG] PUUID status: {r.status}")
                if r.status != 200:
                    body = await r.text()
                    print(f"[DEBUG] PUUID body: {body[:200]}")
                    return {"error": f"Không tìm thấy tài khoản `{riot_id}`"}
                account = await r.json()
                puuid = account.get("puuid")

        # Bước 2: Lấy rank bằng PUUID
        url2 = f"https://vn2.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
        async with aiohttp.ClientSession() as s:
            async with s.get(url2, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                print(f"[DEBUG] Rank by PUUID status: {r.status}")
                if r.status == 200:
                    entries = await r.json()
                    print(f"[DEBUG] Entries: {str(entries)[:300]}")
                else:
                    body = await r.text()
                    print(f"[DEBUG] Rank by PUUID failed: {body[:200]}")
                    entries = []

        # Nếu endpoint PUUID không có, fallback sang summoner ID
        if not entries:
            url3 = f"https://vn2.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url3, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    print(f"[DEBUG] Summoner status: {r.status}")
                    if r.status != 200:
                        body = await r.text()
                        print(f"[DEBUG] Summoner body: {body[:200]}")
                        return {"error": f"Không tìm thấy summoner `{riot_id}`"}
                    summoner = await r.json()
                    summoner_id = summoner.get("id")

            url4 = f"https://vn2.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url4, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    print(f"[DEBUG] Rank status: {r.status}")
                    if r.status != 200:
                        entries = []
                    else:
                        entries = await r.json()
                        print(f"[DEBUG] Entries: {str(entries)[:300]}")

        # Xử lý kết quả
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

def build_leaderboard_embed(players, date_str=None):
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
    
    embed = discord.Embed(
        title=f"🏆 Bảng Xếp Hạng LoL — VN2", 
        color=0xC89B3C, 
        timestamp=datetime.now(timezone.utc)
    )
    
    if not players:
        embed.description = "Chưa có ai.\nDùng `!register <Tên#TAG>` để đăng ký!"
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
    embed.set_footer(text="Đăng ký: !register <Tên#TAG> • Cập nhật mỗi 24h kể từ khi bot start")
    return embed

# SỬA: Chạy mỗi 24 giờ kể từ khi start
@tasks.loop(hours=24)  # 24 giờ kể từ lần chạy đầu tiên
async def daily_leaderboard():
    """Tạo bảng xếp hạng mới mỗi 24 giờ kể từ khi start"""
    data = load_data()
    if not data["players"]:
        return
    
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel:
        return
    
    notify_ch = bot.get_channel(NOTIFY_CHANNEL_ID)
    current_time = datetime.now(timezone.utc).strftime('%H:%M:%S %d/%m/%Y')
    print(f"[{current_time}] Đang tạo bảng xếp hạng mới (24h cycle)...")
    
    # Cập nhật rank cho tất cả players trước khi tạo bảng mới
    updated_players = []
    rank_changes = []
    
    for riot_id, pdata in data["players"].items():
        old_tier = pdata.get("tier", "UNRANKED")
        old_lp = pdata.get("lp", 0)
        old_division = pdata.get("division", "")
        
        new = await fetch_player_rank(riot_id)
        await asyncio.sleep(1.5)  # Tránh rate limit
        
        if "error" in new:
            updated_players.append(pdata)
            continue
        
        new["discord_name"] = pdata.get("discord_name", "")
        new["discord_id"] = pdata.get("discord_id", None)
        data["players"][riot_id] = new
        updated_players.append(new)
        
        # Ghi nhận thay đổi để thông báo
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
    
    # Tạo và gửi bảng xếp hạng mới
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
    embed = build_leaderboard_embed(updated_players, now)
    
    # Gửi bảng mới
    new_msg = await channel.send(embed=embed)
    
    # Lưu lịch sử
    history = load_history()
    history["messages"].append({
        "message_id": new_msg.id,
        "channel_id": channel.id,
        "date": now,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_history(history)
    
    # Thông báo thay đổi rank
    if rank_changes and notify_ch:
        notif = discord.Embed(
            title=f"📊 Cập nhật rank • {now}", 
            description="\n".join(rank_changes[:10]) + ("\n..." if len(rank_changes) > 10 else ""), 
            color=0x5865F2, 
            timestamp=datetime.now(timezone.utc)
        )
        notif.set_footer(text=f"Tổng số thay đổi: {len(rank_changes)}")
        await notify_ch.send(embed=notif)
    
    print(f"[OK] Đã tạo bảng xếp hạng mới lúc {now} với {len(updated_players)} người.")

# SỬA: Before loop - chạy lần đầu sau 24 giờ kể từ khi start
@daily_leaderboard.before_loop
async def before_daily():
    await bot.wait_until_ready()
    # Tính thời gian chờ lần đầu: 24 giờ từ bây giờ
    first_run = datetime.now(timezone.utc) + timedelta(hours=24)
    print(f"⏰ Lần chạy đầu tiên: {first_run.strftime('%d/%m/%Y %H:%M:%S')} UTC (sau 24 giờ)")
    print(f"   Bot sẽ tự động gửi bảng xếp hạng mới mỗi 24 giờ kể từ thời điểm này")

@bot.command(name="register")
async def register(ctx, *, riot_id: str = None):
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

@bot.command(name="today")
async def today_cmd(ctx):
    """Xem bảng xếp hạng mới nhất"""
    history = load_history()
    if not history["messages"]:
        await ctx.send("❌ Chưa có bảng xếp hạng nào.")
        return
    
    # Lấy bảng mới nhất
    latest = history["messages"][-1]
    channel = bot.get_channel(latest["channel_id"])
    
    try:
        msg = await channel.fetch_message(latest["message_id"])
        embed = msg.embeds[0]
        await ctx.send(embed=embed)
    except:
        await ctx.send("❌ Không thể tìm thấy bảng xếp hạng.")

@bot.command(name="history")
async def history_cmd(ctx):
    """Xem lịch sử các bảng xếp hạng"""
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
        lines.append(f"`{i+1}.` **{entry['date']}** — [Jump to message](https://discord.com/channels/{ctx.guild.id}/{entry['channel_id']}/{entry['message_id']})")
    
    embed.description = "\n".join(lines)
    embed.set_footer(text="Cập nhật mỗi 24h • Dùng !today để xem bảng mới nhất")
    await ctx.send(embed=embed)

@bot.command(name="lb")
async def lb_cmd(ctx):
    """Alias cho today"""
    await today_cmd(ctx)

@bot.command(name="players")
async def players_cmd(ctx):
    data = load_data()
    if not data["players"]:
        await ctx.send("Chưa có ai. Dùng `!register <Tên#TAG>` để đăng ký!")
        return
    
    names = "\n".join([f"• {k}" for k in data["players"].keys()])
    await ctx.send(f"**{len(data['players'])} người:**\n{names}")

@bot.command(name="update")
@commands.has_permissions(manage_guild=True)
async def update_cmd(ctx):
    """Force cập nhật và tạo bảng mới"""
    await ctx.send("🔄 Đang cập nhật và tạo bảng mới...")
    await daily_leaderboard()  # Gọi trực tiếp task
    await ctx.send("✅ Đã tạo bảng xếp hạng mới!")

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

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(
        title="📖 Hướng dẫn Bot LoL Rank",
        description="Bot tự động tạo bảng xếp hạng mới **mỗi 24 giờ** kể từ khi khởi động",
        color=0x5865F2
    )
    
    embed.add_field(name="🙋 Mọi người", value=(
        "`!register <Tên#TAG>` — Đăng ký\n"
        "`!unregister` — Hủy đăng ký\n"
        "`!rank <Tên#TAG>` — Xem rank\n"
        "`!today` hoặc `!lb` — Xem bảng mới nhất\n"
        "`!history` — Lịch sử bảng xếp hạng\n"
        "`!players` — Danh sách người chơi"
    ), inline=False)
    
    embed.add_field(name="🔧 Admin", value=(
        "`!addplayer <Tên#TAG> [@discord]` — Thêm người chơi\n"
        "`!removeplayer <Tên#TAG>` — Xóa người chơi\n"
        "`!update` — Force tạo bảng mới ngay"
    ), inline=False)
    
    embed.set_footer(text="Tự động tạo bảng mới mỗi 24h kể từ khi start • Riot API")
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    print(f"📋 Leaderboard channel: {LEADERBOARD_CHANNEL_ID}")
    print(f"🔔 Notify channel: {NOTIFY_CHANNEL_ID}")
    print(f"⏰ Sẽ tạo bảng mới mỗi 24 giờ kể từ khi bot start")
    
    # Khởi động task daily
    daily_leaderboard.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Bạn không có quyền dùng lệnh này.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Thiếu tham số. Dùng `!help` để xem hướng dẫn.")
    else:
        print(f"Lỗi: {error}")
# Giữ bot thức 24/7 trên Render
keep_alive()

bot.run(TOKEN)