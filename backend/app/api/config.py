from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from app.core.config import settings
from app.services.p115 import p115_service
from app.services.tg_bot import tg_service
from app.api.auth import get_current_user
from app.version import VERSION
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
    proxy_enabled: bool = False
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_user: str = ""
    proxy_pass: str = ""
    proxy_type: str = "HTTP"

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
    
    # Update Proxy settings
    proxy_changed = False
    if settings.PROXY_ENABLED != cfg.proxy_enabled:
        await settings.save_setting("PROXY_ENABLED", cfg.proxy_enabled)
        proxy_changed = True
    if settings.PROXY_HOST != cfg.proxy_host:
        await settings.save_setting("PROXY_HOST", cfg.proxy_host)
        proxy_changed = True
    if settings.PROXY_PORT != cfg.proxy_port:
        await settings.save_setting("PROXY_PORT", cfg.proxy_port)
        proxy_changed = True
    if settings.PROXY_USER != cfg.proxy_user:
        await settings.save_setting("PROXY_USER", cfg.proxy_user)
        proxy_changed = True
    if settings.PROXY_PASS != cfg.proxy_pass:
        await settings.save_setting("PROXY_PASS", cfg.proxy_pass)
        proxy_changed = True
    if settings.PROXY_TYPE != cfg.proxy_type:
        await settings.save_setting("PROXY_TYPE", cfg.proxy_type)
        proxy_changed = True
    
    # Reinitialize services if proxy changed
    if proxy_changed:
        logger.info("🌐 代理设置已更新，重新初始化服务...")
        if settings.P115_COOKIE:
            p115_service.init_client(settings.P115_COOKIE)
        if settings.TG_BOT_TOKEN:
            # Removed direct init_bot call to avoid conflict
            need_restart_bot = True
    
    # Update Cron tasks
    if settings.P115_CLEANUP_DIR_CRON != cfg.p115_cleanup_dir_cron:
        await settings.save_setting("P115_CLEANUP_DIR_CRON", cfg.p115_cleanup_dir_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_dir_job()
        
    if settings.P115_CLEANUP_TRASH_CRON != cfg.p115_cleanup_trash_cron:
        await settings.save_setting("P115_CLEANUP_TRASH_CRON", cfg.p115_cleanup_trash_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_trash_job()
    
    # Unified restart bot polling
    if need_restart_bot:
        asyncio.create_task(tg_service.restart_polling())
        logger.info("🔄 正在触发机器人安全重启任务...")
    
    logger.info("Configuration updated and saved to database")
    return {"status": "success", "bot_restarted": need_restart_bot}

@router.get("/")
async def get_config(user=Depends(get_current_user)):
    return {
        "tg_bot_token": settings.TG_BOT_TOKEN,
        "tg_bot_connected": tg_service.is_connected,
        "tg_channel_id": settings.TG_CHANNEL_ID,
        "tg_user_id": settings.TG_USER_ID,
        "tg_allow_chats": settings.TG_ALLOW_CHATS,
        "p115_cookie": settings.P115_COOKIE,
        "p115_logged_in": p115_service.is_connected,
        "p115_save_dir": settings.P115_SAVE_DIR,
        "p115_cleanup_dir_cron": settings.P115_CLEANUP_DIR_CRON,
        "p115_cleanup_trash_cron": settings.P115_CLEANUP_TRASH_CRON,
        "p115_recycle_password": settings.P115_RECYCLE_PASSWORD,
        "proxy_enabled": settings.PROXY_ENABLED,
        "proxy_host": settings.PROXY_HOST,
        "proxy_port": settings.PROXY_PORT,
        "proxy_user": settings.PROXY_USER,
        "proxy_pass": settings.PROXY_PASS,
        "proxy_type": settings.PROXY_TYPE,
        "version": VERSION
    }

@router.post("/test-proxy")
async def test_proxy(cfg: ConfigUpdate, user=Depends(get_current_user)):
    """Test proxy connectivity"""
    import aiohttp
    from aiohttp_socks import ProxyConnector
    
    if not cfg.proxy_enabled:
        return {"status": "error", "message": "代理未启用"}
    
    if not cfg.proxy_host or not cfg.proxy_port:
        return {"status": "error", "message": "代理地址或端口不能为空"}
        
    proxy_type = cfg.proxy_type.lower()
    auth = f"{cfg.proxy_user}:{cfg.proxy_pass}@" if cfg.proxy_user and cfg.proxy_pass else ""
    proxy_url = f"{proxy_type}://{auth}{cfg.proxy_host}:{cfg.proxy_port}"
    
    logger.info(f"🛠 测试代理连通性: {proxy_url}")
    
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        connector = None
        if proxy_type == 'socks5':
            connector = ProxyConnector.from_url(proxy_url)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Test with a reliable endpoint
            test_url = "https://api.telegram.org"
            proxy_arg = proxy_url if proxy_type != 'socks5' else None
            async with session.get(test_url, proxy=proxy_arg) as response:
                if response.status == 200:
                    return {"status": "success", "message": "代理连接成功"}
                else:
                    return {"status": "error", "message": f"代理返回错误状态码: {response.status}"}
    except Exception as e:
        logger.error(f"❌ 代理测试失败: {e}")
        err_msg = str(e)
        if "Timeout" in err_msg or "ConnectorError" in err_msg or "信号灯超时时间已到" in err_msg or "Cannot connect to host" in err_msg:
            return {"status": "error", "message": "测试连接失败，请检查网络环境"}
        return {"status": "error", "message": f"代理连接失败: {err_msg}"}

@router.post("/detect-proxy-protocol")
async def detect_proxy_protocol(cfg: ConfigUpdate, user=Depends(get_current_user)):
    """Auto-detect proxy protocol (HTTP or SOCKS5)"""
    import aiohttp
    from aiohttp_socks import ProxyConnector
    
    if not cfg.proxy_host or not cfg.proxy_port:
        return {"status": "error", "message": "代理地址或端口不能为空"}
        
    protocols = ["HTTP", "SOCKS5"]
    auth = f"{cfg.proxy_user}:{cfg.proxy_pass}@" if cfg.proxy_user and cfg.proxy_pass else ""
    
    for proto in protocols:
        proxy_url = f"{proto.lower()}://{auth}{cfg.proxy_host}:{cfg.proxy_port}"
        logger.info(f"🔍 尝试检测协议: {proxy_url}")
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            connector = ProxyConnector.from_url(proxy_url) if proto == "SOCKS5" else None
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                proxy_arg = proxy_url if proto == "HTTP" else None
                async with session.get("https://www.google.com", proxy=proxy_arg) as resp:
                    if resp.status == 200:
                        return {"status": "success", "protocol": proto, "message": f"检测到协议: {proto}"}
        except Exception:
            continue
            
    return {"status": "error", "message": "未能检测到有效协议，请手动指定"}

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
