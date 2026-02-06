from fastapi import APIRouter
from pydantic import BaseModel
from app.core.config import settings
from app.services.p115 import p115_service
from app.services.tg_bot import tg_service
from loguru import logger
import asyncio

router = APIRouter(prefix="/config", tags=["config"])

class ConfigUpdate(BaseModel):
    tg_bot_token: str = None
    tg_channel_id: str = None
    tg_user_id: str = None
    tg_allow_chats: str = None
    p115_cookie: str = None
    p115_save_dir: str = None
    p115_cleanup_dir_cron: str = None
    p115_cleanup_trash_cron: str = None
    p115_recycle_password: str = None

@router.post("/update")
async def update_config(cfg: ConfigUpdate):
    if cfg.tg_bot_token:
        settings.TG_BOT_TOKEN = cfg.tg_bot_token
        tg_service.init_bot(cfg.tg_bot_token)
    if cfg.tg_channel_id:
        settings.TG_CHANNEL_ID = cfg.tg_channel_id
    if cfg.tg_user_id:
        settings.TG_USER_ID = cfg.tg_user_id
    if cfg.tg_allow_chats:
        settings.TG_ALLOW_CHATS = cfg.tg_allow_chats
    if cfg.p115_cookie:
        settings.P115_COOKIE = cfg.p115_cookie
        p115_service.init_client(cfg.p115_cookie)
    if cfg.p115_save_dir:
        settings.P115_SAVE_DIR = cfg.p115_save_dir
    if cfg.p115_cleanup_dir_cron:
        settings.P115_CLEANUP_DIR_CRON = cfg.p115_cleanup_dir_cron
    if cfg.p115_cleanup_trash_cron:
        settings.P115_CLEANUP_TRASH_CRON = cfg.p115_cleanup_trash_cron
    if cfg.p115_recycle_password is not None:  # Allow empty string
        settings.P115_RECYCLE_PASSWORD = cfg.p115_recycle_password
    
    # Save to persistent storage
    settings.save_to_file()
    
    logger.info("Configuration updated and saved to file")
    return {"status": "success"}

@router.get("/")
async def get_config():
    return {
        "tg_bot_token": settings.TG_BOT_TOKEN,
        "tg_channel_id": settings.TG_CHANNEL_ID,
        "tg_user_id": settings.TG_USER_ID,
        "tg_allow_chats": settings.TG_ALLOW_CHATS,
        "p115_cookie": settings.P115_COOKIE,
        "p115_save_dir": settings.P115_SAVE_DIR,
        "p115_cleanup_dir_cron": settings.P115_CLEANUP_DIR_CRON,
        "p115_cleanup_trash_cron": settings.P115_CLEANUP_TRASH_CRON,
        "p115_recycle_password": settings.P115_RECYCLE_PASSWORD,
        "version": "1.0.4"
    }

@router.post("/test-bot")
async def test_bot():
    logger.info("🛠 用户触发了机器人通知测试")
    success, msg = await tg_service.test_send_to_user()
    return {"status": "success" if success else "error", "message": msg}

@router.post("/test-channel")
async def test_channel():
    logger.info("🛠 用户触发了频道广播测试")
    success, msg = await tg_service.test_send_to_channel()
    return {"status": "success" if success else "error", "message": msg}

@router.post("/cleanup-save-dir")
async def cleanup_save_dir():
    logger.info("🛠 用户手动触发清理保存目录")
    success = await p115_service.cleanup_save_directory()
    return {"status": "success" if success else "error", "message": "清理成功" if success else "清理失败"}

@router.post("/cleanup-recycle-bin")
async def cleanup_recycle_bin():
    logger.info("🛠 用户手动触发清空回收站")
    success = await p115_service.cleanup_recycle_bin()
    return {"status": "success" if success else "error", "message": "清空成功" if success else "清空失败"}
