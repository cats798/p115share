from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from app.core.config import settings
from app.services.p115 import p115_service
from app.services.tg_bot import tg_service
from app.api.auth import get_current_user
from loguru import logger
import asyncio
from apscheduler.triggers.cron import CronTrigger

router = APIRouter(prefix="/config", tags=["config"])

class ConfigUpdate(BaseModel):
    tg_bot_token: str = Field(..., min_length=1)
    tg_channel_id: str = Field(..., min_length=1)
    tg_user_id: str = Field(..., min_length=1)
    tg_allow_chats: str = Field(..., min_length=1)
    p115_cookie: str = Field(..., min_length=1)
    p115_save_dir: str = Field(..., min_length=1)
    p115_cleanup_dir_cron: str = ""
    p115_cleanup_trash_cron: str = ""
    p115_recycle_password: str = ""

    @field_validator('p115_cleanup_dir_cron', 'p115_cleanup_trash_cron')
    @classmethod
    def validate_cron(cls, v: str):
        if not v:
            return ""
        try:
            CronTrigger.from_crontab(v)
            return v
        except Exception:
            raise ValueError('无效的 Cron 表达式')

@router.post("/update")
async def update_config(cfg: ConfigUpdate, user=Depends(get_current_user)):
    need_restart_bot = False
    
    # Update TG settings
    if settings.TG_BOT_TOKEN != cfg.tg_bot_token:
        await settings.save_setting("TG_BOT_TOKEN", cfg.tg_bot_token)
        tg_service.init_bot(cfg.tg_bot_token)
        need_restart_bot = True
        
    await settings.save_setting("TG_CHANNEL_ID", cfg.tg_channel_id)
    await settings.save_setting("TG_USER_ID", cfg.tg_user_id)
    await settings.save_setting("TG_ALLOW_CHATS", cfg.tg_allow_chats)
    
    # Update 115 settings
    if settings.P115_COOKIE != cfg.p115_cookie:
        await settings.save_setting("P115_COOKIE", cfg.p115_cookie)
        p115_service.init_client(cfg.p115_cookie)
        
    await settings.save_setting("P115_SAVE_DIR", cfg.p115_save_dir)
    await settings.save_setting("P115_RECYCLE_PASSWORD", cfg.p115_recycle_password)
    
    # Update Cron tasks
    if settings.P115_CLEANUP_DIR_CRON != cfg.p115_cleanup_dir_cron:
        await settings.save_setting("P115_CLEANUP_DIR_CRON", cfg.p115_cleanup_dir_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_dir_job()
        
    if settings.P115_CLEANUP_TRASH_CRON != cfg.p115_cleanup_trash_cron:
        await settings.save_setting("P115_CLEANUP_TRASH_CRON", cfg.p115_cleanup_trash_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_trash_job()
    
    # Restart bot polling if token changed
    if need_restart_bot:
        asyncio.create_task(tg_service.restart_polling())
        logger.info("🔄 Bot token 更新，正在重启 polling...")
    
    logger.info("Configuration updated and saved to database")
    return {"status": "success", "bot_restarted": need_restart_bot}

@router.get("/")
async def get_config(user=Depends(get_current_user)):
    return {
        "tg_bot_token": settings.TG_BOT_TOKEN,
        "tg_bot_connected": tg_service.bot is not None,
        "tg_channel_id": settings.TG_CHANNEL_ID,
        "tg_user_id": settings.TG_USER_ID,
        "tg_allow_chats": settings.TG_ALLOW_CHATS,
        "p115_cookie": settings.P115_COOKIE,
        "p115_logged_in": p115_service.fs is not None,
        "p115_save_dir": settings.P115_SAVE_DIR,
        "p115_cleanup_dir_cron": settings.P115_CLEANUP_DIR_CRON,
        "p115_cleanup_trash_cron": settings.P115_CLEANUP_TRASH_CRON,
        "p115_recycle_password": settings.P115_RECYCLE_PASSWORD,
        "version": "1.0.5"
    }

@router.post("/test-bot")
async def test_bot(user=Depends(get_current_user)):
    logger.info("🛠 用户触发了机器人通知测试")
    success, msg = await tg_service.test_send_to_user()
    return {"status": "success" if success else "error", "message": msg}

@router.post("/test-channel")
async def test_channel(user=Depends(get_current_user)):
    logger.info("🛠 用户触发了频道广播测试")
    success, msg = await tg_service.test_send_to_channel()
    return {"status": "success" if success else "error", "message": msg}

@router.post("/cleanup-save-dir")
async def cleanup_save_dir(user=Depends(get_current_user)):
    logger.info("🛠 用户手动触发清理保存目录")
    success = await p115_service.cleanup_save_directory()
    return {"status": "success" if success else "error", "message": "清理成功" if success else "清理失败"}

@router.post("/cleanup-recycle-bin")
async def cleanup_recycle_bin(user=Depends(get_current_user)):
    logger.info("🛠 用户手动触发清空回收站")
    success = await p115_service.cleanup_recycle_bin()
    return {"status": "success" if success else "error", "message": "清空成功" if success else "清空失败"}

@router.post("/clear-history")
async def clear_history(user=Depends(get_current_user)):
    """Clear all link share history"""
    from app.services.p115 import p115_service
    result = await p115_service.delete_all_history_links()
    if result:
        return {"status": "success", "message": "已清空所有历史记录"}
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="清空历史记录失败")
