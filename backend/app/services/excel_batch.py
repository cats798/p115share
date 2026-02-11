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

    def _read_csv(self, content: bytes):
        """Try reading CSV with multiple encodings"""
        for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'gb18030']:
            try:
                return pd.read_csv(io.BytesIO(content), encoding=encoding)
            except UnicodeDecodeError:
                continue
            except Exception as e:
                raise e
        raise Exception("无法识别CSV文件编码，请确保文件是 UTF-8 或 GBK 格式")

    async def parse_file(self, content: bytes, filename: str):
        """Parse Excel/CSV file and return headers and sample data"""
        try:
            if filename.endswith('.csv'):
                df = self._read_csv(content)
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
                df = self._read_csv(content)
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
                # Check for tasks that are "running"
                async with async_session() as session:
                    result = await session.execute(
                        select(ExcelTask).where(ExcelTask.status == "running").limit(1)
                    )
                    task = result.scalar_one_or_none()
                    
                    if not task:
                        # If no running task, check for "queued" tasks
                        result = await session.execute(
                            select(ExcelTask).where(ExcelTask.status == "queued").order_by(ExcelTask.created_at).limit(1)
                        )
                        task = result.scalar_one_or_none()
                        if task:
                            # Start the queued task
                            task.status = "running"
                            await session.commit()
                            logger.info(f"队列任务 {task.id} ({task.name}) 开始运行")
                
                    if not task:
                        # If no running taskFound, exit worker
                        logger.info("Excel 批量转存服务工作线程退出（无运行中的任务）")
                        self.worker_task = None
                        break
                    
                    self.active_task_id = task.id
                    interval_min = task.interval_min
                    interval_max = task.interval_max
                    
                    try:
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
                                # Update current_row in ExcelTask and set is_waiting to False
                                await session.execute(
                                    update(ExcelTask).where(ExcelTask.id == task.id).values(
                                        current_row=item.row_index,
                                        is_waiting=False
                                    )
                                )
                                await session.commit()
                            else:
                                # No more pending items for this task
                                await session.execute(
                                    update(ExcelTask).where(ExcelTask.id == task.id).values(
                                        status="completed", 
                                        current_row=0,
                                        is_waiting=False
                                    )
                                )
                                await session.commit()
                                self.active_task_id = None
                                continue

                        # Process the item
                        await self._process_item(item_id)
                    finally:
                        # Find next row and set is_waiting to True before sleep
                        if item_id:
                            async with async_session() as session:
                                # Look ahead for next pending item
                                next_result = await session.execute(
                                    select(ExcelTaskItem.row_index).where(
                                        ExcelTaskItem.task_id == task.id,
                                        ExcelTaskItem.status == "待处理"
                                    ).order_by(ExcelTaskItem.row_index).limit(1)
                                )
                                next_row = next_result.scalar_one_or_none()
                                
                                if next_row:
                                    await session.execute(
                                        update(ExcelTask).where(ExcelTask.id == task.id).values(
                                            current_row=next_row,
                                            is_waiting=True
                                        )
                                    )
                                else:
                                    await session.execute(
                                        update(ExcelTask).where(ExcelTask.id == task.id).values(
                                            current_row=0,
                                            is_waiting=False
                                        )
                                    )
                                await session.commit()
                        self.active_task_id = None
                
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

    async def start_task(self, task_id: int, skip_count: int = 0, interval_min: int = 5, interval_max: int = 10):
        async with async_session() as session:
            # Get currrent status
            result = await session.execute(select(ExcelTask).where(ExcelTask.id == task_id))
            task = result.scalar_one()
            
            # Check if another task is already running
            result = await session.execute(
                select(ExcelTask).where(ExcelTask.status == "running", ExcelTask.id != task_id)
            )
            other_running = result.scalar_one_or_none()
            
            new_status = "queued" if other_running else "running"
            
            # If resume from paused, dont reset skip/pending
            is_resume = task.status == "paused"
            
            # Update intervals and status
            task.interval_min = interval_min
            task.interval_max = interval_max
            task.status = new_status
            
            if not is_resume:
                task.skip_count = skip_count
                task.current_row = 0
                # Mark first skip_count items as "跳过"
                await session.execute(
                    update(ExcelTaskItem).where(
                        ExcelTaskItem.task_id == task_id,
                        ExcelTaskItem.row_index <= skip_count
                    ).values(status="跳过", error_msg=None, new_share_url=None)
                )
                # Mark remaining items as "待处理"
                await session.execute(
                    update(ExcelTaskItem).where(
                        ExcelTaskItem.task_id == task_id,
                        ExcelTaskItem.row_index > skip_count
                    ).values(status="待处理", error_msg=None, new_share_url=None)
                )
            
            await session.commit()
            
            if new_status == "running":
                logger.info(f"任务 {task_id} 开始运行")
            else:
                logger.info(f"任务 {task_id} 已进入队列排队")

        await self._update_task_counts(task_id)
        if new_status == "running":
            await self.start_worker()

    async def shutdown(self):
        """Handle graceful shutdown: pause running tasks, reset queued tasks"""
        logger.info("Excel 批量转存服务正在关闭，正在保存任务状态...")
        async with async_session() as session:
            # Reset running, pausing, cancelling, and queued tasks to paused
            await session.execute(
                update(ExcelTask).where(
                    ExcelTask.status.in_(["running", "pausing", "cancelling", "queued"])
                ).values(status="paused", is_waiting=False)
            )
            await session.commit()
        
        # Wait for current processing item if any
        wait_start = datetime.now()
        while self.active_task_id is not None:
            await asyncio.sleep(0.1)
            if (datetime.now() - wait_start).total_seconds() > 30:
                logger.warning("Excel shutdown wait timeout")
                break
        
        logger.info("Excel 批量转存服务已关闭")


    async def pause_task(self, task_id: int):
        async with async_session() as session:
            # Set to transitional status first
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="pausing")
            )
            await session.commit()
        
        # Safety wait: wait until the current item processing finishes
        wait_start = datetime.now()
        while self.active_task_id == task_id:
            await asyncio.sleep(0.1)
            if (datetime.now() - wait_start).total_seconds() > 60:
                logger.warning(f"Pause task {task_id} safety wait timeout")
                break
        
        # Set to final status
        async with async_session() as session:
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="paused")
            )
            await session.commit()
        logger.info(f"Task {task_id} paused safely")

    async def cancel_task(self, task_id: int):
        async with async_session() as session:
            # Set to transitional status first
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="cancelling")
            )
            await session.commit()
            
        # Safety wait: same as pause
        wait_start = datetime.now()
        while self.active_task_id == task_id:
            await asyncio.sleep(0.1)
            if (datetime.now() - wait_start).total_seconds() > 60:
                break
        
        # Set to final status
        async with async_session() as session:
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="cancelled")
            )
            await session.commit()
        logger.info(f"Task {task_id} cancelled safely")

    async def recover_tasks(self):
        """Recover tasks from non-graceful shutdown"""
        logger.info("Excel 批量转存服务正在进行故障恢复...")
        async with async_session() as session:
            # 1. Reset tasks that were stuck in active or transitional states
            await session.execute(
                update(ExcelTask).where(
                    ExcelTask.status.in_(["running", "pausing", "cancelling", "queued"])
                ).values(status="paused", is_waiting=False)
            )
            # 2. Reset items that were stuck in "处理中"
            await session.execute(
                update(ExcelTaskItem).where(ExcelTaskItem.status == "处理中").values(status="待处理")
            )
            await session.commit()
        logger.info("Excel 故障恢复完成")

    async def delete_task(self, task_id: int):
        async with async_session() as session:
            await session.execute(delete(ExcelTaskItem).where(ExcelTaskItem.task_id == task_id))
            await session.execute(delete(ExcelTask).where(ExcelTask.id == task_id))
            await session.commit()

excel_batch_service = ExcelBatchService()
