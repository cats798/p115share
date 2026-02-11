import asyncio
import random
import io
import pandas as pd
from datetime import datetime
from loguru import logger
from sqlalchemy import select, update, delete, func
from app.core.database import async_session
from app.models.schema import ExcelTask, ExcelTaskItem
from app.services.p115 import p115_service
from app.services.tg_bot import tg_service
from app.core.config import settings

class ExcelBatchService:
    def __init__(self):
        self.worker_task = None
        self.active_task_id = None
        self._lock = asyncio.Lock()

    async def parse_file(self, content: bytes, filename: str):
        """Parse Excel/CSV file and return headers and sample data"""
        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(content))
            else:
                df = pd.read_excel(io.BytesIO(content))
            
            headers = df.columns.tolist()
            # Convert NaN to None for JSON serialization
            df_cleaned = df.where(pd.notnull(df), None)
            preview_data = df_cleaned.head(5).to_dict(orient='records')
            
            return {
                "headers": headers,
                "preview": preview_data,
                "total_rows": len(df)
            }
        except Exception as e:
            logger.error(f"解析文件失败 {filename}: {e}")
            raise Exception(f"解析文件失败: {str(e)}")

    async def create_task(self, filename: str, mapping: dict, content: bytes):
        """Create task and items based on mapping"""
        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(io.BytesIO(content))
            else:
                df = pd.read_excel(io.BytesIO(content))
            
            df = df.where(pd.notnull(df), None)
            
            link_col = mapping.get('link')
            title_col = mapping.get('title')
            code_col = mapping.get('code')
            
            if not link_col:
                raise Exception("未指定链接列")

            async with async_session() as session:
                task = ExcelTask(
                    name=filename,
                    status="wait",
                    total_count=len(df)
                )
                session.add(task)
                await session.flush()
                
                # Add items
                for idx, row in df.iterrows():
                    item = ExcelTaskItem(
                        task_id=task.id,
                        row_index=int(idx) + 1,
                        original_url=str(row[link_col]) if row[link_col] else "",
                        title=str(row[title_col]) if title_col and row[title_col] else None,
                        extraction_code=str(row[code_col]) if code_col and row[code_col] else None,
                        status="待处理"
                    )
                    session.add(item)
                
                await session.commit()
                return task.id
        except Exception as e:
            logger.error(f"创建任务失败: {e}")
            raise e

    async def start_worker(self):
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker())
        logger.info("Excel 批量转存服务工作线程启动")

    async def _worker(self):
        while True:
            try:
                # Check for tasks that are "running" but have pending items
                async with async_session() as session:
                    result = await session.execute(
                        select(ExcelTask).where(ExcelTask.status == "running").limit(1)
                    )
                    task = result.scalar_one_or_none()
                
                if not task:
                    # No running task found, exit worker instead of polling
                    logger.info("Excel 批量转存服务工作线程退出（无运行中的任务）")
                    self.worker_task = None
                    break
                
                self.active_task_id = task.id
                interval_min = task.interval_min
                interval_max = task.interval_max
                
                # Get one pending item
                async with async_session() as session:
                    result = await session.execute(
                        select(ExcelTaskItem).where(
                            ExcelTaskItem.task_id == task.id,
                            ExcelTaskItem.status == "待处理"
                        ).order_by(ExcelTaskItem.row_index).limit(1)
                    )
                    item = result.scalar_one_or_none()
                    
                    if item:
                        item.status = "处理中"
                        item_id = item.id
                        await session.commit()
                    else:
                        # No more pending items for this task
                        await session.execute(
                            update(ExcelTask).where(ExcelTask.id == task.id).values(status="completed")
                        )
                        await session.commit()
                        self.active_task_id = None
                        continue

                # Process the item
                await self._process_item(item_id)
                
                # Rate limiting (Random interval)
                interval = random.randint(interval_min, interval_max)
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.error(f"Excel 工作线程出错: {e}")
                await asyncio.sleep(5)

    async def _process_item(self, item_id: int):
        async with async_session() as session:
            result = await session.execute(select(ExcelTaskItem).where(ExcelTaskItem.id == item_id))
            item = result.scalar_one()
            task_id = item.task_id
            
            original_url = item.original_url
            if not original_url:
                item.status = "失败"
                item.error_msg = "链接为空"
                await session.commit()
                await self._update_task_counts(task_id)
                return

            # 1. Check history first
            history_url = await p115_service.get_history_link(original_url)
            if history_url:
                item.status = "成功"
                item.new_share_url = history_url
                await session.commit()
                await self._update_task_counts(task_id)
                if tg_service:
                    msg_content = f"资源名称：{item.title or '未知'}\n分享链接：{history_url}"
                    await tg_service.broadcast_to_channels({original_url: history_url}, {"full_text": msg_content})
                return

            try:
                metadata = {
                    "description": item.title or "Excel Batch Import",
                    "share_url": original_url
                }
                
                # Combine password if present for saving
                url_to_save = original_url
                if item.extraction_code and "?password=" not in url_to_save:
                    url_to_save = f"{url_to_save}?password={item.extraction_code}"

                save_res = await p115_service.save_and_share(
                    url_to_save, 
                    metadata=metadata,
                    target_dir=settings.P115_SAVE_DIR
                )
                
                if save_res:
                    if save_res.get("status") == "success":
                        share_link = save_res.get("share_link")
                        if share_link:
                            await p115_service.save_history_link(original_url, share_link)
                            item.new_share_url = share_link
                            item.status = "成功"
                            
                            # Broadcast to channels
                            if tg_service:
                                msg_content = f"资源名称：{item.title or '未知'}\n分享链接：{share_link}"
                                await tg_service.broadcast_to_channels({original_url: share_link}, {"full_text": msg_content})
                        else:
                            item.status = "失败"
                            item.error_msg = "转存成功但生成分享链接返回为空"
                    elif save_res.get("status") == "pending":
                        item.status = "成功"
                        item.error_msg = "已在115审核队列"
                    else:
                        item.status = "失败"
                        item.error_msg = save_res.get("message", "转存失败")
                else:
                    item.status = "失败"
                    item.error_msg = "转存服务无响应"
            except Exception as e:
                item.status = "失败"
                item.error_msg = str(e)
            
            await session.commit()
            await self._update_task_counts(task_id)

    async def _update_task_counts(self, task_id: int):
        async with async_session() as session:
            # Get success count
            success_count = await session.scalar(
                select(func.count(ExcelTaskItem.id)).where(
                    ExcelTaskItem.task_id == task_id, 
                    ExcelTaskItem.status == "成功"
                )
            )
            # Get fail count
            fail_count = await session.scalar(
                select(func.count(ExcelTaskItem.id)).where(
                    ExcelTaskItem.task_id == task_id, 
                    ExcelTaskItem.status == "失败"
                )
            )
            
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(
                    success_count=success_count,
                    fail_count=fail_count
                )
            )
            await session.commit()

    async def start_task(self, task_id: int, item_ids: list = None, interval_min: int = 5, interval_max: int = 10):
        async with async_session() as session:
            # Update intervals and status
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(
                    interval_min=interval_min,
                    interval_max=interval_max,
                    status="running"
                )
            )
            
            if item_ids:
                # 将选中的项设为待处理
                await session.execute(
                    update(ExcelTaskItem).where(
                        ExcelTaskItem.task_id == task_id,
                        ExcelTaskItem.id.in_(item_ids)
                    ).values(status="待处理")
                )
                # 将其他原本待处理的项设为跳过
                await session.execute(
                    update(ExcelTaskItem).where(
                        ExcelTaskItem.task_id == task_id,
                        ExcelTaskItem.id.notin_(item_ids),
                        ExcelTaskItem.status == "待处理"
                    ).values(status="跳过")
                )
            else:
                # 如果没有选择特定项，确保原本待处理的项依然是待处理 (应对重新开始已暂停的任务)
                pass
            
            await session.commit()
        await self._update_task_counts(task_id)
        await self.start_worker()

    async def pause_task(self, task_id: int):
        async with async_session() as session:
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="paused")
            )
            await session.commit()

    async def cancel_task(self, task_id: int):
        async with async_session() as session:
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="cancelled")
            )
            await session.commit()

    async def delete_task(self, task_id: int):
        async with async_session() as session:
            await session.execute(delete(ExcelTaskItem).where(ExcelTaskItem.task_id == task_id))
            await session.execute(delete(ExcelTask).where(ExcelTask.id == task_id))
            await session.commit()

excel_batch_service = ExcelBatchService()
