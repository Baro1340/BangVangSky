import json
import os
import psycopg2
from psycopg2.extras import Json
from datetime import datetime

# Cấu hình kết nối database Render
DATABASE_URL = "postgresql://lol_rank_db_user:lvrSSiiRyZ9UY7iDmJ4W9zv2HwBAJ4If@dpg-d6ro37pj16oc73eahfs0-a.singapore-postgres.render.com/lol_rank_db"

def migrate_data():
    """Đọc dữ liệu từ players.json và lưu vào PostgreSQL"""
    
    # 1. Kiểm tra file JSON có tồn tại không
    if not os.path.exists("players.json"):
        print("❌ Không tìm thấy file players.json")
        return
    
    # 2. Đọc dữ liệu từ file JSON
    with open("players.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    players = data.get("players", {})
    leaderboard_msg_id = data.get("leaderboard_message_id")
    
    print(f"📊 Tìm thấy {len(players)} người chơi trong file JSON")
    
    if len(players) == 0:
        print("⚠️ Không có dữ liệu để migrate")
        return
    
    # 3. Kết nối database
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        print("✅ Kết nối database thành công")
        
        # 4. Tạo bảng nếu chưa có
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
        print("✅ Đã tạo/kiểm tra bảng")
        
        # 5. Xóa dữ liệu cũ (nếu muốn ghi đè)
        # cur.execute("DELETE FROM players")
        # print("🗑️ Đã xóa dữ liệu cũ")
        
        # 6. Insert từng player vào database
        count = 0
        for riot_id, player_data in players.items():
            try:
                cur.execute("""
                    INSERT INTO players (riot_id, data, discord_id, discord_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (riot_id) DO UPDATE SET
                        data = EXCLUDED.data,
                        discord_id = EXCLUDED.discord_id,
                        discord_name = EXCLUDED.discord_name,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    riot_id,
                    Json(player_data),
                    player_data.get("discord_id"),
                    player_data.get("discord_name")
                ))
                count += 1
                print(f"  ✅ Đã thêm: {riot_id}")
            except Exception as e:
                print(f"  ❌ Lỗi khi thêm {riot_id}: {e}")
        
        # 7. Lưu leaderboard message ID nếu có
        if leaderboard_msg_id:
            cur.execute("DELETE FROM leaderboard_history")
            cur.execute("""
                INSERT INTO leaderboard_history (message_id, channel_id, date)
                VALUES (%s, %s, %s)
            """, (
                str(leaderboard_msg_id),
                "1482840300333301781",  # Channel ID của bạn
                datetime.now().strftime("%d/%m/%Y")
            ))
            print(f"✅ Đã lưu leaderboard message ID: {leaderboard_msg_id}")
        
        # 8. Commit tất cả thay đổi
        conn.commit()
        print(f"🎉 Thành công! Đã migrate {count}/{len(players)} người chơi lên database")
        
        # 9. Kiểm tra lại dữ liệu
        cur.execute("SELECT COUNT(*) FROM players")
        total = cur.fetchone()[0]
        print(f"📊 Tổng số người chơi trong database hiện tại: {total}")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Lỗi kết nối database: {e}")

def check_database():
    """Kiểm tra dữ liệu trong database"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Đếm số lượng players
        cur.execute("SELECT COUNT(*) FROM players")
        count = cur.fetchone()[0]
        print(f"📊 Tổng số players: {count}")
        
        # Xem 5 người đầu tiên
        if count > 0:
            cur.execute("""
                SELECT riot_id, discord_name, data->>'tier' as tier, 
                       data->>'lp' as lp, data->>'winrate' as winrate 
                FROM players LIMIT 5
            """)
            rows = cur.fetchall()
            print("\n📋 5 người chơi đầu tiên:")
            for row in rows:
                print(f"  - {row[0]} ({row[1]}): {row[2]} {row[3]} LP ({row[4]}% WR)")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Lỗi kiểm tra database: {e}")

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 BẮT ĐẦU MIGRATE DỮ LIỆU")
    print("=" * 50)
    
    # Chạy migrate
    migrate_data()
    
    print("\n" + "=" * 50)
    print("🔍 KIỂM TRA DỮ LIỆU SAU MIGRATE")
    print("=" * 50)
    check_database()