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

from typing import Optional

class ConfigUpdate(BaseModel):
    tg_bot_token: Optional[str] = None
    tg_channel_id: Optional[str] = None
    tg_user_id: Optional[str] = None
    tg_allow_chats: Optional[str] = None
    tg_channels: Optional[str] = None
    p115_cookie: Optional[str] = None
    p115_save_dir: Optional[str] = None
    p115_cleanup_dir_cron: Optional[str] = None
    p115_cleanup_trash_cron: Optional[str] = None
    p115_recycle_password: Optional[str] = None
    proxy_enabled: Optional[bool] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[str] = None
    proxy_user: Optional[str] = None
    proxy_pass: Optional[str] = None
    proxy_type: Optional[str] = None
    p115_cleanup_capacity_enabled: Optional[bool] = None
    p115_cleanup_capacity_limit: Optional[float] = None
    p115_cleanup_capacity_unit: Optional[str] = None
    tmdb_api_key: Optional[str] = None
    tmdb_config: Optional[str] = None

    @field_validator('p115_cleanup_dir_cron', 'p115_cleanup_trash_cron')
    @classmethod
    def validate_cron(cls, v: Optional[str]):
        if v is None or v == "":
            return v
        try:
            CronTrigger.from_crontab(v)
            return v
        except Exception:
            raise ValueError('无效的 Cron 表达式')

    @field_validator('p115_cleanup_capacity_limit')
    @classmethod
    def validate_capacity_limit(cls, v: Optional[float]):
        if v is not None and v < 1:
            raise ValueError('容量限制最小值为 1 TB')
        return v

@router.post("/update")
async def update_config(cfg: ConfigUpdate, user=Depends(get_current_user)):
    # Use model_dump(exclude_unset=True) to get only fields sent by frontend
    update_data = cfg.model_dump(exclude_unset=True)
    if not update_data:
        return {"status": "success", "message": "无变更"}
    
    logger.info(f"⚙️ 收到配置更新请求，包含字段: {list(update_data.keys())}")
    need_restart_bot = False
    proxy_changed = False
    
    # 1. Update TG settings
    if "tg_bot_token" in update_data and settings.TG_BOT_TOKEN != cfg.tg_bot_token:
        await settings.save_setting("TG_BOT_TOKEN", cfg.tg_bot_token)
        need_restart_bot = True
        
    if "tg_channel_id" in update_data:
        await settings.save_setting("TG_CHANNEL_ID", cfg.tg_channel_id)
    if "tg_user_id" in update_data:
        await settings.save_setting("TG_USER_ID", cfg.tg_user_id)
    if "tg_allow_chats" in update_data:
        await settings.save_setting("TG_ALLOW_CHATS", cfg.tg_allow_chats)
    if "tg_channels" in update_data:
        await settings.save_setting("TG_CHANNELS", cfg.tg_channels)
    
    # 2. Update 115 settings
    if "p115_cookie" in update_data and settings.P115_COOKIE != cfg.p115_cookie:
        await settings.save_setting("P115_COOKIE", cfg.p115_cookie)
        p115_service.init_client(cfg.p115_cookie)
        
    if "p115_save_dir" in update_data:
        await settings.save_setting("P115_SAVE_DIR", cfg.p115_save_dir)
    if "p115_recycle_password" in update_data:
        await settings.save_setting("P115_RECYCLE_PASSWORD", cfg.p115_recycle_password)
    
    # 3. Update Proxy settings
    proxy_fields = ["proxy_enabled", "proxy_host", "proxy_port", "proxy_user", "proxy_pass", "proxy_type"]
    for field in proxy_fields:
        if field in update_data:
            current_val = getattr(settings, field.upper())
            new_val = getattr(cfg, field)
            if current_val != new_val:
                await settings.save_setting(field.upper(), new_val)
                proxy_changed = True
    
    # Reinitialize services if proxy changed
    if proxy_changed:
        logger.info("🌐 代理设置内容已发生实质变化，重新初始化相关服务...")
        if settings.P115_COOKIE:
            p115_service.init_client(settings.P115_COOKIE)
        if settings.TG_BOT_TOKEN:
            need_restart_bot = True
    
    # 4. Update Cron tasks
    if "p115_cleanup_dir_cron" in update_data and settings.P115_CLEANUP_DIR_CRON != cfg.p115_cleanup_dir_cron:
        await settings.save_setting("P115_CLEANUP_DIR_CRON", cfg.p115_cleanup_dir_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_dir_job()
        
    if "p115_cleanup_trash_cron" in update_data and settings.P115_CLEANUP_TRASH_CRON != cfg.p115_cleanup_trash_cron:
        await settings.save_setting("P115_CLEANUP_TRASH_CRON", cfg.p115_cleanup_trash_cron)
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_trash_job()

    # 4.5 Update Capacity Cleanup (Consolidated)
    capacity_fields = ["p115_cleanup_capacity_enabled", "p115_cleanup_capacity_limit", "p115_cleanup_capacity_unit"]
    capacity_changed = False
    for field in capacity_fields:
        if field in update_data:
            val = update_data[field]
            if field == "p115_cleanup_capacity_limit":
                val = max(1.0, float(val))
            elif field == "p115_cleanup_capacity_unit":
                val = "TB"
                
            current_val = getattr(settings, field.upper())
            if current_val != val:
                await settings.save_setting(field.upper(), val)
                capacity_changed = True
    
    if capacity_changed:
        from app.services.scheduler import cleanup_scheduler
        cleanup_scheduler.update_cleanup_capacity_job()
    
    # 5. TMDB settings
    if "tmdb_api_key" in update_data:
        await settings.save_setting("TMDB_API_KEY", cfg.tmdb_api_key)
    if "tmdb_config" in update_data:
        await settings.save_setting("TMDB_CONFIG", cfg.tmdb_config)
    
    # 6. Unified restart bot polling
    if need_restart_bot:
        asyncio.create_task(tg_service.restart_polling())
        logger.info("🔄 正在触发机器人安全重启任务...")
    
    return {"status": "success", "bot_restarted": need_restart_bot, "updated_fields": list(update_data.keys())}

@router.get("/")
async def get_config(user=Depends(get_current_user)):
    return {
        "tg_bot_token": settings.TG_BOT_TOKEN,
        "tg_bot_connected": tg_service.is_connected,
        "tg_channel_id": settings.TG_CHANNEL_ID,
        "tg_user_id": settings.TG_USER_ID,
        "tg_allow_chats": settings.TG_ALLOW_CHATS,
        "tg_channels": settings.TG_CHANNELS,
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
        "p115_cleanup_capacity_enabled": settings.P115_CLEANUP_CAPACITY_ENABLED,
        "p115_cleanup_capacity_limit": settings.P115_CLEANUP_CAPACITY_LIMIT,
        "p115_cleanup_capacity_unit": settings.P115_CLEANUP_CAPACITY_UNIT,
        "tmdb_api_key": settings.TMDB_API_KEY,
        "tmdb_config": settings.TMDB_CONFIG,
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
    import json
    channels = []
    try:
        channels = json.loads(settings.TG_CHANNELS)
    except Exception:
        pass
    
    # Also include the legacy channel ID if it exists and not in the list
    legacy_id = settings.TG_CHANNEL_ID
    if legacy_id and not any(c.get("id") == legacy_id for c in channels):
        channels.append({"id": legacy_id, "enabled": True, "concise": False})
    
    enabled_channels = [c for c in channels if c.get("enabled")]
    
    if not enabled_channels:
        return {"status": "error", "message": "未配置或未启用任何频道"}
    
    results = []
    for chan in enabled_channels:
        success, msg = await tg_service.test_send_to_channel(chan.get("id"))
        results.append({"id": chan.get("id"), "success": success, "message": msg})
    
    all_success = all(r["success"] for r in results)
    final_msg = "所有频道测试成功" if all_success else "部分频道测试失败"
    if len(results) == 1:
        final_msg = results[0]["message"]
        
    return {
        "status": "success" if all_success else "error", 
        "message": final_msg,
        "details": results
    }

class GetChatNameRequest(BaseModel):
    chat_id: str

@router.post("/get-telegram-chat-name")
async def get_telegram_chat_name(req: GetChatNameRequest, user=Depends(get_current_user)):
    """Get Telegram chat name by ID"""
    info = await tg_service.get_chat_info(req.chat_id)
    if info:
        return {"status": "success", "data": info}
    else:
        return {"status": "error", "message": "无法获取频道信息，请检查 ID 是否正确或机器人是否在频道中"}

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

# 新增 TMDB 测试接口
@router.post("/test-tmdb")
async def test_tmdb(cfg: ConfigUpdate, user=Depends(get_current_user)):
    from app.services.tmdb import TMDBClient
    if not cfg.tmdb_api_key:
        return {"status": "error", "message": "API Key 不能为空"}
    client = TMDBClient(cfg.tmdb_api_key)
    try:
        result = await client.search_multi("test")
        if result is not None:
            return {"status": "success", "message": "连接成功"}
        else:
            return {"status": "error", "message": "连接失败，请检查 API Key"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await client.close()