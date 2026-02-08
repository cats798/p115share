import sys
import os
import asyncio

# Add backend to path
sys.path.append(os.getcwd())

from app.core.config import settings
from app.services.tg_bot import tg_service

async def test_init():
    await settings.init_db()  # Initialize and load from DB
    
    token = settings.TG_BOT_TOKEN
    print(f"--- Configuration ---")
    print(f"Token: {token[:10]}...{token[-5:] if token else ''}")
    print(f"Proxy Enabled: {settings.PROXY_ENABLED}")
    print(f"Proxy Host: {settings.PROXY_HOST}")
    print(f"Proxy Port: {settings.PROXY_PORT}")
    print(f"Proxy Type: {settings.PROXY_TYPE}")
    print(f"----------------------")
    
    if not token:
        print("❌ Error: No bot token configured!")
        return

    try:
        tg_service.init_bot(token)
        # Wait a bit for the async verification task
        await asyncio.sleep(2)
        
        if tg_service.bot and tg_service.is_connected:
            me = await tg_service.bot.get_me()
            print(f"✅ Bot initialized and connected: @{me.username}")
        else:
            print("❌ Bot initialization failed or could not connect.")
            if not tg_service.bot:
                print("Reason: Bot object is None")
            else:
                print("Reason: Connection verification failed")
            
            if os.path.exists("tg_init_error.log"):
                with open("tg_init_error.log", "r", encoding="utf-8") as f:
                    print("\nError details from log:")
                    print(f.read())
    except Exception as e:
        import traceback
        print(f"❌ Direct error: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(test_init())
