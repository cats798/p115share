import asyncio
import io
import pandas as pd
from datetime import datetime
from loguru import logger
from sqlalchemy import select, update, delete, func
from app.core.database import async_session
from app.models.schema import ExcelTask, ExcelTaskItem
from app.services.p115 import p115_service

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
                    await asyncio.sleep(5)
                    continue
                
                self.active_task_id = task.id
                
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
                
            except Exception as e:
                logger.error(f"Excel 工作线程出错: {e}")
                await asyncio.sleep(5)
            
            # Rate limiting
            await asyncio.sleep(2)

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

            try:
                metadata = {
                    "description": item.title or "Excel Batch Import",
                    "share_url": original_url
                }
                
                # Combine password if present for saving
                url_to_save = original_url
                if item.extraction_code and "?password=" not in url_to_save:
                    url_to_save = f"{url_to_save}?password={item.extraction_code}"

                save_res = await p115_service.save_share_link(url_to_save, metadata=metadata)
                
                if save_res:
                    if save_res.get("status") in ["success", "pending"]:
                        # Create share link
                        share_link = await p115_service.create_share_link(save_res)
                        if share_link:
                            await p115_service.save_history_link(original_url, share_link)
                            item.status = "成功"
                        else:
                            # If pending, we mark as success because it's queued in 115
                            if save_res.get("status") == "pending":
                                item.status = "成功"
                                item.error_msg = "已在115审核队列"
                            else:
                                item.status = "失败"
                                item.error_msg = "转存成功但创建分享失败"
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

    async def start_task(self, task_id: int, item_ids: list = None):
        async with async_session() as session:
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
            
            await session.execute(
                update(ExcelTask).where(ExcelTask.id == task_id).values(status="running")
            )
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
