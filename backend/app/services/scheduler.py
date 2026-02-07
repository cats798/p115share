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
        self.update_cleanup_dir_job()
        self.update_cleanup_trash_job()
        self.scheduler.start()
        logger.info("⏰ 定时清理任务已启动")

    def update_cleanup_dir_job(self):
        """Update or remove the cleanup save directory job based on config"""
        job_id = "cleanup_save_dir"
        if settings.P115_CLEANUP_DIR_CRON:
            try:
                self.scheduler.add_job(
                    p115_service.cleanup_save_directory,
                    CronTrigger.from_crontab(settings.P115_CLEANUP_DIR_CRON),
                    id=job_id,
                    name="清理保存目录",
                    replace_existing=True
                )
                logger.info(f"✅ 已设置清理保存目录定时任务: {settings.P115_CLEANUP_DIR_CRON}")
            except Exception as e:
                logger.error(f"❌ 设置清理保存目录定时任务失败: {e}")
        else:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info("🚫 已移除清理保存目录定时任务")

    def update_cleanup_trash_job(self):
        """Update or remove the cleanup recycle bin job based on config"""
        job_id = "cleanup_recycle_bin"
        if settings.P115_CLEANUP_TRASH_CRON:
            try:
                self.scheduler.add_job(
                    p115_service.cleanup_recycle_bin,
                    CronTrigger.from_crontab(settings.P115_CLEANUP_TRASH_CRON),
                    id=job_id,
                    name="清空回收站",
                    replace_existing=True
                )
                logger.info(f"✅ 已设置清空回收站定时任务: {settings.P115_CLEANUP_TRASH_CRON}")
            except Exception as e:
                logger.error(f"❌ 设置清空回收站定时任务失败: {e}")
        else:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info("🚫 已移除清空回收站定时任务")
    
    def shutdown(self):
        """Shutdown the scheduler"""
        self.scheduler.shutdown()
        logger.info("⏰ 定时清理任务已停止")

cleanup_scheduler = CleanupScheduler()
