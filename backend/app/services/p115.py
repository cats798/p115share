from p115client import P115Client, check_response
from p115client.fs import P115FileSystem
from p115client.util import share_extract_payload
from app.core.config import settings
from loguru import logger
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Literal, Optional
from app.core.database import async_session
from app.models.schema import PendingLink, LinkHistory
from sqlalchemy import select, delete

# 默认 API 请求超时（秒）
API_TIMEOUT = 60
# 默认 API 重试次数
API_MAX_RETRIES = 3
# 重试间隔（秒）
API_RETRY_DELAY = 5


class P115Service:
    def __init__(self):
        self.client = None
        self.fs = None
        self.is_connected = False
        self._task_lock: Optional[asyncio.Lock] = None  # Lazy initialize
        self._current_task: str | None = None  # Track current task type
        self._save_dir_cid: int = 0  # Cached save directory CID
        if settings.P115_COOKIE:
            self.init_client(settings.P115_COOKIE)

    async def _api_call_with_timeout(
        self,
        coro_func,
        *args,
        timeout: int = API_TIMEOUT,
        max_retries: int = API_MAX_RETRIES,
        retry_delay: int = API_RETRY_DELAY,
        label: str = "API",
        **kwargs,
    ):
        """带超时和重试的 API 调用包装器。
        
        Args:
            coro_func: 异步方法（如 self.client.share_snap）
            *args: 传给 coro_func 的位置参数
            timeout: 单次请求超时秒数
            max_retries: 最大重试次数
            retry_delay: 重试间隔秒数
            label: 日志标识
            **kwargs: 传给 coro_func 的关键字参数
        """
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    coro_func(*args, **kwargs),
                    timeout=timeout,
                )
                return result
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"{label} 请求超时 ({timeout}s), 尝试 {attempt}/{max_retries}")
                logger.warning(f"⏱️ {label} 请求超时 (尝试 {attempt}/{max_retries})")
            except Exception as e:
                # 非超时异常直接抛出，不重试
                raise
            
            if attempt < max_retries:
                logger.info(f"🔄 {label} 将在 {retry_delay}s 后重试...")
                await asyncio.sleep(retry_delay)
        
        raise last_error

    def init_client(self, cookie: str):
        try:
            # Apply proxy settings to environment if configured
            import os
            if settings.PROXY_ENABLED and settings.PROXY_HOST and settings.PROXY_PORT:
                proxy_type = settings.PROXY_TYPE.lower()
                auth = f"{settings.PROXY_USER}:{settings.PROXY_PASS}@" if settings.PROXY_USER and settings.PROXY_PASS else ""
                proxy_url = f"{proxy_type}://{auth}{settings.PROXY_HOST}:{settings.PROXY_PORT}"
                
                os.environ['HTTP_PROXY'] = proxy_url
                os.environ['http_proxy'] = proxy_url
                os.environ['HTTPS_PROXY'] = proxy_url
                os.environ['https_proxy'] = proxy_url
                
            self.client = P115Client(cookie, check_for_relogin=True)
            self.fs = P115FileSystem(self.client)
            
            proxy_info = ""
            if settings.PROXY_ENABLED:
                proxy_info = f" (Proxy: {settings.PROXY_TYPE}://{settings.PROXY_HOST}:{settings.PROXY_PORT})"
            logger.info(f"P115Client and FileSystem initialized successfully{proxy_info}")
            # Verify connection asynchronously
            asyncio.create_task(self.verify_connection())
        except Exception as e:
            logger.error(f"Failed to initialize P115Client: {e}")
            self.client = None
            self.fs = None
            self.is_connected = False

    @asynccontextmanager
    async def _acquire_task_lock(self, task_type: Literal["save_share", "cleanup"]):
        """Acquire task lock with timeout.
        
        Uses asyncio.wait_for on the actual lock acquisition instead of
        polling, which is both more efficient and avoids race conditions.
        """
        if self._task_lock is None:
            self._task_lock = asyncio.Lock()
            
        max_wait = 2100  # 35 minutes max wait (to accommodate network retry)
        
        if self._task_lock.locked():
            logger.info(f"⏳ {task_type} 任务等待中，当前任务: {self._current_task}")
        
        try:
            await asyncio.wait_for(self._task_lock.acquire(), timeout=max_wait)
        except asyncio.TimeoutError:
            raise TimeoutError(f"等待任务锁超时 ({max_wait}s): {task_type}，当前占用: {self._current_task}")
        
        self._current_task = task_type
        logger.info(f"🔒 {task_type} 任务已获取锁")
        try:
            yield
        finally:
            self._current_task = None
            self._task_lock.release()
            logger.info(f"🔓 {task_type} 任务已释放锁")

    async def verify_connection(self) -> bool:
        """Verify the 115 cookie connection"""
        if not self.client:
            self.is_connected = False
            return False
            
        try:
            # Simple API call to verify cookie
            resp = await self._api_call_with_timeout(
                self.client.user_info, async_=True,
                timeout=30, max_retries=2, label="user_info"
            )
            if resp.get("state"):
                self.is_connected = True
                logger.info("✅ 115 网盘登录验证成功")
                return True
        except Exception as e:
            logger.error(f"❌ 115 网盘登录验证失败: {e}")
            self.is_connected = False
            return False
            
        self.is_connected = False
        return False

    def clear_save_dir_cache(self):
        """Clear the cached save directory CID (e.g. after cleanup)"""
        self._save_dir_cid = 0
        logger.debug("🗑️ 已清除保存目录 CID 缓存")

    async def _ensure_save_dir(self, path: Optional[str] = None):
        """Ensure the save directory exists and return its CID.
        
        Uses a cached CID to avoid repeated API calls for the default path.
        If a custom path is provided, it will always verify/create it.
        """
        is_default = path is None
        path = path or settings.P115_SAVE_DIR or "/分享保存"
        
        # Return cached CID if available and using default path
        if is_default and self._save_dir_cid > 0:
            logger.debug(f"📂 使用缓存的保存目录 CID: {self._save_dir_cid}")
            return self._save_dir_cid
        
        logger.info(f"🔍 开始检查/创建保存目录: {path}")
        
        if not self.client:
            raise RuntimeError("P115Client 未初始化，无法创建保存目录")
        
        # Retry up to 3 times with timeout
        last_error = None
        for attempt in range(1, 4):
            try:
                logger.info(f"📁 调用 fs_makedirs_app 创建目录... (尝试 {attempt}/3)")
                # Add 30s timeout to prevent indefinite hanging
                resp = await asyncio.wait_for(
                    self.client.fs_makedirs_app(path, pid=0, async_=True),
                    timeout=30
                )
                logger.info(f"📋 fs_makedirs_app 响应: {resp}")
                check_response(resp)
                
                # The response structure has 'cid' at the top level (not in 'data')
                # Response format: {'state': True, 'error': '', 'errCode': 0, 'cid': '3358575817564146054'}
                cid = 0
                if "cid" in resp:
                    cid = int(resp["cid"])
                    logger.info(f"🔢 从响应中提取到 CID: {cid}")
                elif "data" in resp:
                    data = resp["data"]
                    cid = int(data.get("category_id") or data.get("cid") or data.get("id") or 0)
                    logger.info(f"🔢 从 data 字段中提取到 CID: {cid}")
                else:
                    logger.error(f"❌ 响应中没有 'cid' 或 'data' 字段: {resp}")
                    
                if cid == 0:
                    raise RuntimeError(f"无法从响应获取有效的 CID: {resp}")
                    
                # Cache the CID only if it's the default path
                if is_default:
                    self._save_dir_cid = cid
                logger.info(f"✅ 保存目录已确认: {path} (CID: {cid})")
                return cid
                
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"fs_makedirs_app 请求超时 (30s), 尝试 {attempt}/3")
                logger.warning(f"⏱️ fs_makedirs_app 请求超时 (尝试 {attempt}/3)")
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ 创建目录失败 (尝试 {attempt}/3): {e}")
            
            if attempt < 3:
                await asyncio.sleep(3)
        
        # All retries exhausted — raise to prevent saving to root
        raise RuntimeError(f"无法确保保存目录 {path} 存在 (已重试3次): {last_error}")

    async def save_share_link(self, share_url: str, metadata: dict = None, target_dir: Optional[str] = None):
        """Save a 115 share link to the configured directory
        
        Args:
            share_url: The 115 share URL to save
            metadata: Optional metadata dict containing description, full_text, photo_id, etc.
            target_dir: Optional target directory path
        """
        async with self._acquire_task_lock("save_share"):
            if not self.client:
                logger.warning("P115Client not initialized, cannot save link")
                return None
            
            logger.info(f"📥 开始处理分享链接: {share_url}")
            try:
                # 1. Extract share/receive codes
                payload = share_extract_payload(share_url)
                
                # 2. Get share snapshot to get file IDs and names (带超时重试)
                snap_resp = await self._api_call_with_timeout(
                    self.client.share_snap, payload, async_=True,
                    timeout=API_TIMEOUT, label="share_snap"
                )
                check_response(snap_resp)

                # Check for audit and violation status
                data = snap_resp.get("data", {})
                share_info = data.get("shareinfo" if "shareinfo" in data else "share_info", {})
                share_state = data.get("share_state", share_info.get("share_state", share_info.get("status"))) # Multiple fallbacks
                share_title = share_info.get("share_title", "")
                have_vio_file = share_info.get("have_vio_file", 0)

                # 优先判断违规内容，无论审核状态如何
                if have_vio_file == 1:
                    logger.warning(f"🚫 分享链接包含违规内容: {share_url}")
                    return {
                        "status": "error",
                        "error_type": "violated",
                        "message": "链接包含违规内容"
                    }

                if share_state == 0:
                    logger.info(f"🔍 分享链接处于审核中，进入轮询等待队列: {share_url}")
                    # Save to DB for persistence
                    async with async_session() as session:
                        new_task = PendingLink(
                            share_url=share_url,
                            metadata_json=metadata or {},
                            status="auditing"
                        )
                        session.add(new_task)
                        await session.commit()
                        db_id = new_task.id
                    
                    return {
                        "status": "pending",
                        "share_url": share_url,
                        "metadata": metadata or {},
                        "db_id": db_id
                    }
                
                if share_state == 7:
                    logger.warning(f"⚠️ 分享链接已过期: {share_url}")
                    return {
                        "status": "error",
                        "error_type": "expired",
                        "message": "链接已过期"
                    }
                
                if share_state != 1:
                    logger.warning(f"⚠️ 分享链接状态异常 (state={share_state}): {share_url}")
                    # Allow attempt if state is unknown but not explicitly pending/expired/prohibited
                
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
                
                # 3. Ensure save directory (with network recovery retry)
                #    If _ensure_save_dir fails (e.g. network issue), pause and retry
                #    for up to 30 minutes instead of discarding the task.
                to_cid = None
                max_network_wait = 1800  # 30 minutes
                network_start = time.time()
                network_attempt = 0
                
                while True:
                    try:
                        to_cid = await self._ensure_save_dir(target_dir)
                        if network_attempt > 0:
                            logger.info(f"🎉 网络已恢复，继续处理任务 (等待了 {time.time() - network_start:.0f}s)")
                        break
                    except Exception as dir_err:
                        network_attempt += 1
                        elapsed = time.time() - network_start
                        remaining = max_network_wait - elapsed
                        
                        if remaining <= 0:
                            logger.error(f"❌ 网盘网络恢复等待超时 (30分钟)，中止任务: {dir_err}")
                            return {
                                "status": "error",
                                "error_type": "dir_failed",
                                "message": f"网盘网络持续不可用 (已等待30分钟): {dir_err}"
                            }
                        
                        wait_time = min(30, remaining)
                        logger.warning(
                            f"⏸️ 网盘网络异常，任务暂停等待恢复 "
                            f"(第{network_attempt}次重试, 已等待 {elapsed:.0f}s, 剩余 {remaining:.0f}s): {dir_err}"
                        )
                        await asyncio.sleep(wait_time)
                
                # 4. Receive files
                receive_payload = {
                    "share_code": payload["share_code"],
                    "receive_code": payload["receive_code"] or "",
                    "file_id": ",".join(fids),
                    "cid": to_cid
                }
                
                try:
                    recv_resp = await self._api_call_with_timeout(
                        self.client.share_receive, receive_payload, async_=True,
                        timeout=API_TIMEOUT, label="share_receive"
                    )
                    check_response(recv_resp)
                    logger.info(f"✅ 链接转存指令已发送: {share_url} -> CID {to_cid}")
                except Exception as recv_error:
                    # Check if it's a "file already received" error (errno 4200045)
                    error_msg = str(recv_error)
                    if "4200045" in error_msg or "已经接收" in error_msg:
                        logger.warning(f"⚠️ 115 提示文件该分享已接收过: {share_url}")
                        # Verify if files really exist in to_cid
                        found_all = False
                        try:
                            # 用 _find_files_in_dir 查找（支持 search + list 双重查找）
                            found_files = await self._find_files_in_dir(to_cid, names)
                            found_count = len(found_files)
                            if found_count > 0:
                                logger.info(f"✅ 在保存目录中找到 {found_count} 个同名文件，继续处理")
                                # Continue to share creation with existing files
                            else:
                                logger.error("❌ 115 提示已接收，但在保存目录未找到文件（可能已被删除）。无法重新转存同一分享链接。")
                                return {
                                    "status": "error",
                                    "error_type": "already_exists_missing",
                                    "message": "该分享链接您已转存过。115 限制同一链接无法由于文件丢失而重复转存，请尝试寻找原文件或从回收站还原。"
                                }
                        except Exception as check_e:
                            logger.warning(f"⚠️ 检查文件是否存在时出错: {check_e}")
                            # Assume failure to be safe
                            return {
                                "status": "error", 
                                "error_type": "unknown",
                                "message": "保存失败，且无法验证文件是否存在"
                            }
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

    async def get_share_status(self, share_url: str):
        """Check the current status of a share link
        
        Returns:
            dict: {
                "share_state": int,
                "is_auditing": bool,
                "is_expired": bool,
                "is_prohibited": bool,
                "title": str
            }
        """
        try:
            payload = share_extract_payload(share_url)
            snap_resp = await self._api_call_with_timeout(
                self.client.share_snap, payload, async_=True,
                timeout=API_TIMEOUT, label="share_snap(status)"
            )
            check_response(snap_resp)
            
            data = snap_resp.get("data", {})
            share_info = data.get("shareinfo" if "shareinfo" in data else "share_info", {})
            share_state = data.get("share_state", share_info.get("share_state", share_info.get("status")))
            share_title = share_info.get("share_title", "")
            have_vio_file = share_info.get("have_vio_file", 0)
            
            res = {
                "share_state": share_state,
                "is_auditing": share_state == 0,
                "is_expired": share_state == 7,
                "is_prohibited": have_vio_file == 1,
                "title": share_title
            }
            logger.debug(f"📊 检查链接状态: {share_url} -> {res}")
            return res
        except Exception as e:
            logger.error(f"❌ 检查链接状态失败: {share_url}, 错误: {e}")
            return None

    async def _find_files_in_dir(self, cid: int, target_names: list) -> list:
        """在指定目录中查找文件，使用多种方式确保找到
        
        优先使用 fs_search（按文件名搜索），失败后回退到 fs_files（列目录）。
        
        Args:
            cid: 目录 ID
            target_names: 要查找的文件名列表
            
        Returns:
            匹配的文件列表 [{fid, name, size, time}, ...]
        """
        matched = []
        
        # 方式 1: 使用 fs_search 按文件名搜索（更可靠，不依赖目录缓存）
        for name in target_names:
            try:
                search_resp = await self._api_call_with_timeout(
                    self.client.fs_search,
                    {"search_value": name, "cid": cid, "limit": 20},
                    async_=True,
                    timeout=30, max_retries=2, label=f"fs_search({name})"
                )
                check_response(search_resp)
                search_data = search_resp.get("data", [])
                
                # fs_search 的结果可能在 data 数组或 data.list 中
                if isinstance(search_data, dict):
                    search_items = search_data.get("list", [])
                else:
                    search_items = search_data
                
                logger.debug(f"🔍 fs_search '{name}' 在 CID:{cid} 返回 {len(search_items)} 条结果")
                
                for item in search_items:
                    item_name = item.get("n") or item.get("file_name")
                    if item_name == name:
                        item_id = item.get("fid") or item.get("cid") or item.get("file_id") or item.get("category_id")
                        if item_id:
                            matched.append({
                                "fid": str(item_id),
                                "name": item_name,
                                "size": item.get("s", item.get("file_size", 0)),
                                "time": item.get("te", 0),
                            })
                            logger.info(f"📄 fs_search 找到: {item_name} (ID: {item_id})")
                            break
            except Exception as e:
                logger.warning(f"⚠️ fs_search 搜索 '{name}' 失败: {e}")
        
        if len(matched) == len(target_names):
            return matched
        
        # 方式 2: 回退到 fs_files 列目录
        found_names = {m["name"] for m in matched}
        remaining_names = [n for n in target_names if n not in found_names]
        logger.info(f"🔍 fs_search 找到 {len(matched)}/{len(target_names)} 个文件，尝试 fs_files 查找剩余: {remaining_names}")
        
        try:
            resp = await self._api_call_with_timeout(
                self.client.fs_files,
                {"cid": cid, "limit": 500, "show_dir": 1},
                async_=True,
                timeout=30, max_retries=2, label="fs_files"
            )
            check_response(resp)
            file_list = resp.get("data", [])
            
            # 检查 data 的类型，兼容不同响应格式
            if isinstance(file_list, dict):
                file_list = file_list.get("list", [])
            
            # 获取响应中的实际 CID，验证是否正确列出了目标目录
            resp_path = resp.get("path", [])
            resp_cid = None
            if resp_path:
                last_path = resp_path[-1] if isinstance(resp_path, list) else resp_path
                resp_cid = last_path.get("cid") if isinstance(last_path, dict) else None
            
            actual_count = resp.get("count", "?")
            logger.debug(f"📂 fs_files CID:{cid} 返回 {len(file_list)} 项 (总数: {actual_count}, 路径CID: {resp_cid})")
            
            # 验证返回的是否是正确的目录（防止 CID 不存在时回退到根目录）
            if resp_cid is not None and str(resp_cid) != str(cid):
                logger.warning(f"⚠️ fs_files 返回的目录 CID({resp_cid}) 与请求的 CID({cid}) 不匹配！可能目录不存在")
            
            # 日志打印目录中的前10个文件名，便于排查
            if file_list:
                dir_file_names = [item.get("n", "?") for item in file_list[:10]]
                logger.debug(f"📋 目录内文件(前10): {dir_file_names}")
            
            for item in file_list:
                item_name = item.get("n")
                if item_name in remaining_names:
                    item_id = item.get("fid") or item.get("cid")
                    if item_id:
                        matched.append({
                            "fid": str(item_id),
                            "name": item_name,
                            "size": item.get("s", 0),
                            "time": item.get("te", 0),
                        })
                        logger.info(f"📄 fs_files 找到: {item_name} (ID: {item_id})")
                        
        except Exception as e:
            logger.warning(f"⚠️ fs_files 列目录失败: {e}")
        
        return matched

    async def create_share_link(self, save_result: dict):
        if not self.client or not save_result:
            return None
            
        to_cid = save_result.get("to_cid")
        names = save_result.get("names", [])
        
        try:
            # 5. Wait for 10 seconds as requested
            logger.info(f"⏳ 等待 10 秒以确保文件保存完成...")
            await asyncio.sleep(10)
            
            # 6. Find files with polling (using search + list as fallback)
            new_fids = []
            matched_files = []
            
            max_poll_attempts = 5
            for poll_attempt in range(1, max_poll_attempts + 1):
                try:
                    logger.info(f"🔍 开始查找文件 (第 {poll_attempt}/{max_poll_attempts} 次), 目标目录 CID: {to_cid}, 查找: {names}")
                    current_matched = await self._find_files_in_dir(to_cid, names)
                    
                    if current_matched:
                        if matched_files:
                            # Compare with previous poll
                            stable = len(current_matched) == len(matched_files)
                            if stable:
                                for curr, prev in zip(sorted(current_matched, key=lambda x: x["fid"]), 
                                                     sorted(matched_files, key=lambda x: x["fid"])):
                                    if curr["fid"] != prev["fid"] or curr["size"] != prev["size"]:
                                        stable = False
                                        break
                            
                            if stable:
                                logger.info(f"✅ 文件状态已稳定，检测到 {len(current_matched)} 个文件")
                                new_fids = [f["fid"] for f in current_matched]
                                break
                            else:
                                logger.debug(f"🔄 文件状态变化中 (第 {poll_attempt}/{max_poll_attempts} 次轮询)")
                        
                        matched_files = current_matched
                        
                        if poll_attempt < max_poll_attempts:
                            await asyncio.sleep(5)
                    else:
                        logger.warning(f"⚠️ 轮询未找到文件 (第 {poll_attempt}/{max_poll_attempts} 次)")
                        if poll_attempt < max_poll_attempts:
                            await asyncio.sleep(5)
                            
                except Exception as e:
                    logger.warning(f"⚠️ 查找文件失败 (轮询 {poll_attempt}/{max_poll_attempts}): {e}")
                    if poll_attempt < max_poll_attempts:
                        await asyncio.sleep(5)
            
            # If polling didn't find stable files, use the last matched files
            if not new_fids and matched_files:
                logger.info(f"⚠️ 文件未完全稳定，但使用 {len(matched_files)} 个已匹配的文件尝试创建分享")
                new_fids = [f["fid"] for f in matched_files]
            
            if not new_fids:
                logger.warning(f"⚠️ 在保存目录 {to_cid} 中未找到对应的文件 {names}，可能保存尚未完成")
                return None
            
            # 7. Create new share with retry mechanism
            share_code = None
            receive_code = None
            max_share_retries = 3
            
            for retry_attempt in range(1, max_share_retries + 1):
                try:
                    logger.info(f"📤 正在创建分享链接 (尝试 {retry_attempt}/{max_share_retries}): {', '.join(names[:3])}...")
                    send_resp = await self._api_call_with_timeout(
                        self.client.share_send, ",".join(new_fids), async_=True,
                        timeout=API_TIMEOUT, max_retries=1, label="share_send"
                    )
                    check_response(send_resp)
                    
                    # Extract share_code
                    data = send_resp["data"]
                    share_code = data.get("share_code")
                    receive_code = data.get("receive_code") or data.get("recv_code")
                    
                    logger.info(f"✅ 分享链接创建成功: {share_code}")
                    break  # Success, exit retry loop
                    
                except Exception as share_error:
                    error_str = str(share_error)
                    # Check if it's error 4100005 (file moved or deleted)
                    if "4100005" in error_str or "已被移动或删除" in error_str:
                        if retry_attempt < max_share_retries:
                            logger.warning(f"⚠️ 文件尚未就绪 (错误 4100005)，等待 5 秒后重试...")
                            await asyncio.sleep(5)
                            continue
                        else:
                            logger.error(f"❌ 重试 {max_share_retries} 次后仍失败: {share_error}")
                            raise
                    else:
                        # Other errors, don't retry
                        logger.error(f"❌ 创建分享链接失败 (非时序问题): {share_error}")
                        raise
            
            if not share_code:
                logger.error("❌ 未能获取到 share_code")
                return None
            
            # 8. Update share to be permanent (share_duration=-1)
            if share_code:
                logger.info(f"🔄 正在将分享链接 {share_code} 转换为长期有效...")
                update_payload = {
                    "share_code": share_code,
                    "share_duration": -1
                }
                update_resp = await self._api_call_with_timeout(
                    self.client.share_update, update_payload, async_=True,
                    timeout=API_TIMEOUT, max_retries=2, label="share_update"
                )
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
        """Clean up the save directory by deleting the entire folder.
        It will be automatically recreated by _ensure_save_dir on next save."""
        async with self._acquire_task_lock("cleanup"):
            logger.info("🧹 开始清理保存目录...")
            try:
                save_dir_cid = await self._ensure_save_dir()
                if not save_dir_cid:
                    logger.error("无法获取保存目录 CID")
                    return False
                # Clear cache since we're deleting the directory
                self.clear_save_dir_cache()

                # 直接删除整个保存目录文件夹
                save_path = settings.P115_SAVE_DIR or "/分享保存"
                logger.info(f"🗑️ 正在删除保存目录: {save_path} (CID: {save_dir_cid})")
                del_resp = await self._api_call_with_timeout(
                    self.client.fs_delete, str(save_dir_cid), async_=True,
                    timeout=API_TIMEOUT, label="fs_delete"
                )
                check_response(del_resp)
                logger.info(f"✅ 保存目录已删除，下次保存时将自动重建")
                return True
            except Exception as e:
                logger.error(f"清理保存目录失败: {e}")
                return False

    async def get_history_link(self, original_url: str) -> str | None:
        """Check if a link has been processed before"""
        try:
            from app.models.schema import LinkHistory
            async with async_session() as session:
                result = await session.execute(
                    select(LinkHistory).where(LinkHistory.original_url == original_url)
                )
                record = result.scalar_one_or_none()
                if record:
                    return record.share_link
            return None
        except Exception as e:
            logger.error(f"查询历史记录失败: {e}")
            return None

    async def save_history_link(self, original_url: str, share_link: str):
        """Save a processed link to history"""
        try:
            from app.models.schema import LinkHistory
            async with async_session() as session:
                # Check existance first to avoid unique constraint error
                existing = await session.execute(
                    select(LinkHistory).where(LinkHistory.original_url == original_url)
                )
                if existing.scalar_one_or_none():
                    return
                
                new_record = LinkHistory(original_url=original_url, share_link=share_link)
                session.add(new_record)
                await session.commit()
                logger.info(f"已保存历史记录: {original_url} -> {share_link}")
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")

    async def delete_all_history_links(self):
        """Clear all history links"""
        try:
            from app.models.schema import LinkHistory
            from sqlalchemy import delete
            async with async_session() as session:
                await session.execute(delete(LinkHistory))
                await session.commit()
                logger.info("已清空所有历史记录")
                return True
        except Exception as e:
            logger.error(f"清空历史记录失败: {e}")
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
                resp = await self._api_call_with_timeout(
                    self.client.recyclebin_clean_app, payload, async_=True,
                    timeout=API_TIMEOUT, label="recyclebin_clean"
                )
                check_response(resp)
                
                logger.info("✅ 回收站已清空")
                return True
            except Exception as e:
                logger.error("❌ 清空回收站失败: {}", e)
                return False

p115_service = P115Service()
