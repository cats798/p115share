import os
import json
from pydantic_settings import BaseSettings
from typing import Optional
from loguru import logger
from sqlalchemy import select, update
from app.core.database import engine, async_session, Base
from app.models.schema import User as UserModel, SystemSettings

class Settings(BaseSettings):
    # Telegram
    TG_BOT_TOKEN: str = ""
    TG_CHANNEL_ID: str = ""
    TG_USER_ID: str = ""
    TG_ALLOW_CHATS: str = "" # Comma separated list of IDs
    
    # 115
    P115_COOKIE: str = ""
    P115_SAVE_DIR: str = "115-Share"
    
    # App
    WEB_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    
    # Cleanup scheduling
    P115_CLEANUP_DIR_CRON: str = "0 3 * * *"
    P115_CLEANUP_TRASH_CRON: str = "0 4 * * *"
    P115_RECYCLE_PASSWORD: str = ""

    async def init_db(self):
        """Initialize database tables and migrate from JSON if needed"""
        async with engine.begin() as conn:
            # Create tables
            await conn.run_sync(Base.metadata.create_all)
            
        async with async_session() as session:
            # Check if admin exists
            result = await session.execute(select(UserModel).where(UserModel.username == "admin"))
            if not result.scalar_one_or_none():
                from app.services.auth import get_password_hash
                admin = UserModel(
                    username="admin", 
                    hashed_password=get_password_hash("admin"),
                    avatar_url="/logo.png"
                )
                session.add(admin)
                logger.info("Default admin user created (admin/admin)")
            
            # Check if we need to migrate from config.json
            result = await session.execute(select(SystemSettings).limit(1))
            if not result.scalar_one_or_none():
                await self._init_default_settings(session)
            else:
                await self._load_from_db(session)
            
            await session.commit()

    async def _init_default_settings(self, session):
        """Initialize settings with defaults from class attributes"""
        logger.info("💾 Initializing default settings in database...")
        for field in self.model_fields:
            val = getattr(self, field)
            session.add(SystemSettings(key=field, value=str(val)))

    async def _load_from_db(self, session):
        """Load settings from system_settings table"""
        result = await session.execute(select(SystemSettings))
        rows = result.scalars().all()
        for row in rows:
            if hasattr(self, row.key):
                # Type casting
                field_type = self.model_fields[row.key].annotation
                try:
                    if field_type == int:
                        setattr(self, row.key, int(row.value))
                    elif field_type == bool:
                        setattr(self, row.key, row.value.lower() == "true")
                    else:
                        setattr(self, row.key, row.value)
                except Exception as e:
                    logger.error(f"Failed to cast setting {row.key}: {e}")

    async def save_setting(self, key: str, value: str):
        """Save a single setting to database"""
        if not hasattr(self, key):
            return False
            
        async with async_session() as session:
            await session.execute(
                update(SystemSettings)
                .where(SystemSettings.key == key)
                .values(value=str(value))
            )
            await session.commit()
            setattr(self, key, value)
            return True

    class Config:
        env_file = ".env"

settings = Settings()
