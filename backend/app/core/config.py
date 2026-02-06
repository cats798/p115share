import json
import os
from pydantic_settings import BaseSettings
from typing import Optional
from loguru import logger

CONFIG_FILE = "config.json"

class Settings(BaseSettings):
    # Telegram
    TG_BOT_TOKEN: Optional[str] = None
    TG_CHANNEL_ID: Optional[str] = None
    TG_USER_ID: Optional[str] = None
    TG_ALLOW_CHATS: Optional[str] = None # Comma separated list of IDs
    
    # 115
    P115_COOKIE: Optional[str] = None
    
    # App
    LOG_LEVEL: str = "INFO"
    P115_SAVE_DIR: str = "/分享保存"
    WEB_PORT: int = 8000
    
    # Cleanup scheduling
    P115_CLEANUP_DIR_CRON: str = "*/30 * * * *"  # Default: every 30 minutes
    P115_CLEANUP_TRASH_CRON: str = "0 */2 * * *"  # Default: every 2 hours
    P115_RECYCLE_PASSWORD: str = ""  # Recycle bin password, empty if no password
    
    class Config:
        env_file = ".env"

    def save_to_file(self):
        data = self.model_dump(exclude_unset=False)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Configuration saved to {CONFIG_FILE}")

    def load_from_file(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if hasattr(self, k):
                            setattr(self, k, v)
                logger.info(f"Configuration loaded from {CONFIG_FILE}")
            except Exception as e:
                logger.error(f"Failed to load configuration: {e}")

settings = Settings()
# Initialize and load
settings.load_from_file()
