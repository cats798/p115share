from p115client import P115Client, check_response
from p115client.fs import P115FileSystem
from p115client.util import share_extract_payload
from app.core.config import settings
from loguru import logger
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Literal

class P115Service:
    def __init__(self):
        self.client = None
        self.fs = None
        self._task_lock = asyncio.Lock()  # Task mutex
        self._current_task: str | None = None  # Track current task type
        if settings.P115_COOKIE:
            self.init_client(settings.P115_COOKIE)

    def init_client(self, cookie: str):
        try:
            self.client = P115Client(cookie, check_for_relogin=True)
            self.fs = P115FileSystem(self.client)
            logger.info("P115Client and FileSystem initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize P115Client: {e}")
            self.client = None
            self.fs = None

    @asynccontextmanager
    async def _acquire_task_lock(self, task_type: Literal["save_share", "cleanup"]):
        """Acquire task lock with waiting logic"""
        max_wait = 300  # 5 minutes max wait
        start_time = time.time()
        
        while self._task_lock.locked():
            if time.time() - start_time > max_wait:
                raise TimeoutError(f"等待任务锁超时: {task_type}")
            logger.info(f"⏳ {task_type} 任务等待中，当前任务: {self._current_task}")
            await asyncio.sleep(5)
        
        async with self._task_lock:
            self._current_task = task_type
            logger.info(f"🔒 {task_type} 任务已获取锁")
            try:
                yield
            finally:
                self._current_task = None
                logger.info(f"🔓 {task_type} 任务已释放锁")

    async def _ensure_save_dir(self):
        """Ensure the save directory exists and return its CID"""
        path = settings.P115_SAVE_DIR or "/分享保存"
        logger.info(f"🔍 开始检查/创建保存目录: {path}")
        
        if not self.client:
            logger.warning("⚠️ Client not initialized")
            return 0
        
        try:
            # fs_makedirs_app creates the directory if it doesn't exist
            # and returns the final directory's info
            logger.info(f"📁 调用 fs_makedirs_app 创建目录...")
            resp = await self.client.fs_makedirs_app(path, pid=0, async_=True)
            logger.info(f"📋 fs_makedirs_app 响应: {resp}")
            check_response(resp)
            
            # The response structure has 'cid' at the top level (not in 'data')
            # Response format: {'state': True, 'error': '', 'errCode': 0, 'cid': '3358575817564146054'}
            cid = 0
            if "cid" in resp:
                # CID is returned as a string, convert to int
                cid = int(resp["cid"])
                logger.info(f"🔢 从响应中提取到 CID: {cid}")
            elif "data" in resp:
                # Fallback: check if it's in a 'data' field (for compatibility)
                data = resp["data"]
                cid = int(data.get("category_id") or data.get("cid") or data.get("id") or 0)
                logger.info(f"🔢 从 data 字段中提取到 CID: {cid}")
            else:
                logger.error(f"❌ 响应中没有 'cid' 或 'data' 字段: {resp}")
                
            if cid == 0:
                logger.error(f"❌ 无法从响应获取有效的 CID: {resp}")
                return 0
                
            logger.info(f"✅ 保存目录已确认: {path} (CID: {cid})")
            return cid
        except Exception as e:
            logger.error(f"❌ 无法确保保存目录 {path} 存在: {e}", exc_info=True)
            return 0

    async def save_share_link(self, share_url: str, metadata: dict = None):
        """Save a 115 share link to the configured directory
        
        Args:
            share_url: The 115 share URL to save
            metadata: Optional metadata dict containing description, full_text, photo_id, etc.
        """
        async with self._acquire_task_lock("save_share"):
            if not self.client:
                logger.warning("P115Client not initialized, cannot save link")
                return None
            
            logger.info(f"📥 开始处理分享链接: {share_url}")
            try:
                # 1. Extract share/receive codes
                payload = share_extract_payload(share_url)
                
                # 2. Get share snapshot to get file IDs and names
                snap_resp = await self.client.share_snap(payload, async_=True)
                check_response(snap_resp)
                
                items = snap_resp["data"]["list"]
                if not items:
                    logger.warning("分享链接内没有文件")
                    return None
                
                # Extract file/folder IDs and names
                # Files use 'fid', folders use 'cid'
                fids = []
                names = []
                for item in items:
                    # Try to get fid (file) or cid (folder)
                    fid = item.get("fid") or item.get("cid")
                    if fid:
                        fids.append(str(fid))
                        names.append(item.get("n", "未知"))
                    else:
                        logger.warning(f"Item missing both fid and cid: {item}")
                
                if not fids:
                    logger.error("未能提取到任何有效的文件或文件夹 ID")
                    return None
                
                logger.info(f"📦 检测到 {len(fids)} 个项目: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}")
                
                # 3. Ensure save directory
                to_cid = await self._ensure_save_dir()
                
                # 4. Receive files
                receive_payload = {
                    "share_code": payload["share_code"],
                    "receive_code": payload["receive_code"] or "",
                    "file_id": ",".join(fids),
                    "cid": to_cid
                }
                
                try:
                    recv_resp = await self.client.share_receive(receive_payload, async_=True)
                    check_response(recv_resp)
                    logger.info(f"✅ 链接转存指令已发送: {share_url} -> CID {to_cid}")
                except Exception as recv_error:
                    # Check if it's a "file already received" error (errno 4200045)
                    error_msg = str(recv_error)
                    if "4200045" in error_msg or "已接收" in error_msg:
                        logger.warning(f"⚠️ 文件已在目标位置，跳过转存: {share_url}")
                        # Continue to share creation with existing files
                    else:
                        # Other errors, re-raise
                        raise
                
                return {
                    "status": "success", 
                    "to_cid": to_cid, 
                    "names": names,
                    "share_url": share_url,
                    "metadata": metadata or {}  # Include metadata in return value
                }
            except Exception as e:
                logger.error(f"❌ 保存分享链接失败", exc_info=True)
                return None

    async def create_share_link(self, save_result: dict):
        if not self.client or not save_result:
            return None
            
        to_cid = save_result.get("to_cid")
        names = save_result.get("names", [])
        
        try:
            # 5. Wait for 10 seconds as requested
            logger.info(f"⏳ 等待 10 秒以确保文件保存完成...")
            await asyncio.sleep(10)
            
            # 6. Find the new file IDs in the destination folder
            # Note: 115 might not have finished the transfer even after 10s for large files,
            # but we try our best.
            new_fids = []
            
            # Use self.client.fs_files or self.fs.iterdir
            # For simplicity and robustness, let's use the raw API or the fs object
            items_iterator = self.fs.iterdir(to_cid, async_=True)
            async for item in items_iterator:
                if item["name"] in names:
                    new_fids.append(str(item["id"]))
            
            if not new_fids:
                logger.warning("⚠️ 在保存目录中未找到对应的文件，可能保存尚未完成或名称不匹配")
                return None
            
            # 7. Create new share (Standard 15-day share first)
            logger.info(f"📤 正在为保存的文件创建初始分享: {', '.join(names[:3])}...")
            send_resp = await self.client.share_send(",".join(new_fids), async_=True)
            check_response(send_resp)
            
            # Extract share_code to update it to long-term
            data = send_resp["data"]
            share_code = data.get("share_code")
            receive_code = data.get("receive_code") or data.get("recv_code")
            
            if share_code:
                # 8. Update share to be permanent (share_duration=-1)
                logger.info(f"🔄 正在将分享链接 {share_code} 转换为长期有效...")
                update_payload = {
                    "share_code": share_code,
                    "share_duration": -1
                }
                update_resp = await self.client.share_update(update_payload, async_=True)
                check_response(update_resp)
                logger.debug(f"Share update response: {update_resp}")

            new_share = f"https://115.com/s/{share_code}"
            if receive_code:
                new_share += f"?password={receive_code}"
                
            logger.info(f"🔗 长期分享链接已生成: {new_share}")
            return new_share
            
        except Exception as e:
            logger.error(f"❌ 创建新分享链接失败: {e}")
            return None

    async def cleanup_save_directory(self):
        """Clean up the save directory"""
        async with self._acquire_task_lock("cleanup"):
            logger.info("🧹 开始清理保存目录...")
            try:
                save_dir_cid = await self._ensure_save_dir()
                if not save_dir_cid:
                    logger.error("无法获取保存目录 CID")
                    return False
                
                logger.info(f"🔎 准备查询保存目录内容，CID: {save_dir_cid} (类型: {type(save_dir_cid)})")
                
                # Get all files in save directory (使用 app API)
                # 注意：必须作为位置参数传递，不能使用 cid= 关键字参数
                resp = await self.client.fs_files_app(save_dir_cid, async_=True)
                logger.debug(f"📋 fs_files_app 完整响应: {resp}")
                files = resp.get("data", [])
                
                if not files:
                    logger.info("✅ 保存目录为空，无需清理")
                    return True
                
                # Delete all files and folders
                file_ids = []
                for f in files:
                    file_name = f.get("fn") or f.get("n") or f.get("name") or "Unknown项目"
                    # According to 115 app API response structure:
                    # - Folders: have 'fid' field containing the folder ID (string)
                    # - Files: have 'file_id' field
                    fid = None
                    is_folder = False
                    
                    # Check if it has file_id (it's a file)
                    if "file_id" in f:
                        fid = f["file_id"]
                        is_folder = False
                    # Otherwise, use fid (it's a folder)
                    elif "fid" in f:
                        fid = f["fid"]
                        is_folder = True
                    
                    if fid:
                        file_ids.append(str(fid))
                        logger.debug("📍 发现可清理项目: {} (ID: {}, 类型: {})", file_name, fid, "文件夹" if is_folder else "文件")
                    else:
                        logger.warning("⚠️ 无法获取项目的 ID: {}", f)
                
                if not file_ids:
                    logger.info("✅ 未发现可清理的文件或文件夹")
                    return True
                
                # 调用删除接口 (使用 app API 保持一致)
                logger.info(f"🗑️ 准备删除 {len(file_ids)} 个项目: {file_ids}")
                delete_resp = await self.client.fs_delete_app(",".join(file_ids), async_=True)
                logger.info(f"🗑️ 删除接口响应: {delete_resp}")
                
                try:
                    check_response(delete_resp)
                    logger.info("✅ 清理保存目录成功，删除了 {} 个项目", len(file_ids))
                    return True
                except Exception as delete_error:
                    # Check for specific error codes
                    error_str = str(delete_error)
                    # errno 231011 means files already deleted
                    if "231011" in error_str:
                        logger.warning(f"⚠️ 部分项目已在 115 端被删除: {delete_error}")
                        logger.info("✅ 清理完成（项目已被删除）")
                        return True
                    else:
                        logger.error(f"❌ 删除失败: {delete_error}")
                        raise
            except Exception as e:
                logger.error("❌ 清理保存目录失败: {}", e)
                return False

    async def cleanup_recycle_bin(self):
        """Empty the recycle bin"""
        async with self._acquire_task_lock("cleanup"):
            logger.info("🗑️ 开始清空回收站...")
            try:
                # Prepare payload with optional password
                payload = {}
                if settings.P115_RECYCLE_PASSWORD:
                    payload["password"] = settings.P115_RECYCLE_PASSWORD
                    logger.debug("使用回收站密码")
                
                # Call recycle bin cleanup API
                resp = await self.client.recyclebin_clean_app(payload, async_=True)
                check_response(resp)
                
                logger.info("✅ 回收站已清空")
                return True
            except Exception as e:
                logger.error("❌ 清空回收站失败: {}", e)
                return False

p115_service = P115Service()
