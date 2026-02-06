import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.services.p115 import p115_service

logger = logging.getLogger(__name__)

class CleanupScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        
    def start(self):
        """Start the scheduler"""
        # Clean save directory
        self.scheduler.add_job(
            p115_service.cleanup_save_directory,
            CronTrigger.from_crontab(settings.P115_CLEANUP_DIR_CRON),
            id="cleanup_save_dir",
            name="清理保存目录"
        )
        
        # Clean recycle bin
        self.scheduler.add_job(
            p115_service.cleanup_recycle_bin,
            CronTrigger.from_crontab(settings.P115_CLEANUP_TRASH_CRON),
            id="cleanup_recycle_bin",
            name="清空回收站"
        )
        
        self.scheduler.start()
        logger.info("⏰ 定时清理任务已启动")
    
    def shutdown(self):
        """Shutdown the scheduler"""
        self.scheduler.shutdown()
        logger.info("⏰ 定时清理任务已停止")

cleanup_scheduler = CleanupScheduler()
