from p115client import P115Client, check_response
from p115client.fs import P115FileSystem
from p115client.util import share_extract_payload
from p115client.tool import share_iterdir_walk
from app.core.config import settings
from loguru import logger
import asyncio
import time
import random
import re
from contextlib import asynccontextmanager
from typing import Literal, Optional, Tuple, Union, List, Dict
from app.core.database import async_session
from app.models.schema import PendingLink, LinkHistory
from sqlalchemy import select, delete
from app.services.tmdb import TMDBClient, MediaOrganizer, SmartMediaAnalyzer, QualityLevel

# 默认 API 请求超时（秒）
API_TIMEOUT = 60
# 默认 API 重试次数
API_MAX_RETRIES = 3
# 重试间隔（秒）
API_RETRY_DELAY = 5

# iOS 用户代理
IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 115wangpan_ios/36.2.20"
)


class P115Service:
    def __init__(self):
        self.client = None
        self.fs = None
        self.is_connected = False
        self._task_lock: Optional[asyncio.Lock] = None
        self._current_task: str | None = None
        self._save_dir_cid: int = 0
        self._task_queue = asyncio.Queue()
        self._worker_task = None
        self._worker_lock = asyncio.Lock()
        self._current_task_info = None
        self._restriction_until: float = 0
        
        if settings.P115_COOKIE:
            self.init_client(settings.P115_COOKIE)

    @property
    def queue_size(self) -> int:
        return self._task_queue.qsize()

    @property
    def is_busy(self) -> bool:
        return self._current_task_info is not None or self.is_restricted

    @property
    def is_restricted(self) -> bool:
        return time.time() < self._restriction_until

    def set_restriction(self, hours: float = 1.0):
        self._restriction_until = time.time() + (hours * 3600)
        logger.warning(f"🚫 115 服务已进入全局限制模式，预计持续 {hours} 小时 (直到 {time.strftime('%H:%M:%S', time.localtime(self._restriction_until))})")

    def clear_restriction(self):
        if self._restriction_until > 0:
            self._restriction_until = 0
            logger.info("🔓 115 全局限制模式已解除")

    def _get_ios_ua_kwargs(self):
        return {
            "headers": {
                "user-agent": IOS_UA,
                "accept-encoding": "gzip, deflate"
            },
            "app": "ios"
        }

    async def _task_worker(self):
        logger.info("🚀 P115 任务队列 Worker 已启动")
        while True:
            task_func, args, kwargs, future, task_type = await self._task_queue.get()
            self._current_task_info = task_type
            try:
                logger.info(f"⚡ 队列正在处理任务: {task_type}")
                result = await task_func(*args, **kwargs)
                if not future.done():
                    future.set_result(result)
            except Exception as e:
                logger.error(f"❌ 队列执行任务 {task_type} 出错: {e}")
                if not future.done():
                    future.set_exception(e)
            finally:
                self._task_queue.task_done()
                self._current_task_info = None

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
                raise
            
            if attempt < max_retries:
                logger.info(f"🔄 {label} 将在 {retry_delay}s 后重试...")
                await asyncio.sleep(retry_delay)
        
        raise last_error

    def init_client(self, cookie: str):
        try:
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
            asyncio.create_task(self.verify_connection())
        except Exception as e:
            logger.error(f"Failed to initialize P115Client: {e}")
            self.client = None
            self.fs = None
            self.is_connected = False

    @asynccontextmanager
    async def _acquire_task_lock(self, task_type: Literal["save_share", "cleanup"], wait: bool = True):
        yield

    async def _enqueue_op(self, task_type: str, func, *args, **kwargs):
        if self._worker_task is None or self._worker_task.done():
            async with self._worker_lock:
                if self._worker_task is None or self._worker_task.done():
                    self._worker_task = asyncio.create_task(self._task_worker())
                    logger.info("⚡ 延迟启动 P115 任务队列 Worker")

        future = asyncio.get_running_loop().create_future()
        await self._task_queue.put((func, args, kwargs, future, task_type))
        return await future

    async def verify_connection(self) -> bool:
        if not self.client:
            self.is_connected = False
            return False
            
        try:
            resp = await self._api_call_with_timeout(
                self.client.user_info, async_=True,
                timeout=30, max_retries=2, label="user_info",
                **self._get_ios_ua_kwargs()
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
        self._save_dir_cid = 0
        logger.debug("🗑️ 已清除保存目录 CID 缓存")

    async def _ensure_save_dir(self, path: Optional[str] = None):
        is_default = path is None
        path = path or settings.P115_SAVE_DIR or "/分享保存"
        
        if is_default and self._save_dir_cid > 0:
            logger.debug(f"📂 使用缓存的保存目录 CID: {self._save_dir_cid}")
            return self._save_dir_cid
        
        logger.info(f"🔍 开始检查/创建保存目录: {path}")
        
        if not self.client:
            raise RuntimeError("P115Client 未初始化，无法创建保存目录")
        
        last_error = None
        for attempt in range(1, 4):
            try:
                logger.info(f"📁 调用 fs_makedirs_app 创建目录... (尝试 {attempt}/3)")
                resp = await asyncio.wait_for(
                    self.client.fs_makedirs_app(path, pid=0, async_=True, **self._get_ios_ua_kwargs()),
                    timeout=30
                )
                logger.info(f"📋 fs_makedirs_app 响应: {resp}")
                check_response(resp)
                
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
        
        raise RuntimeError(f"无法确保保存目录 {path} 存在 (已重试3次): {last_error}")

    async def _handle_already_received(self, to_cid: int, names: list[str], share_url: str, metadata: dict, have_vio_file: int, receive_payload: dict):
        logger.warning(f"⚠️ 115 提示文件该分享已接收过: {share_url}")
        try:
            found_files = await self._find_files_in_dir(to_cid, names)
            found_count = len(found_files)
            if found_count > 0:
                logger.info(f"✅ 在保存目录中找到 {found_count} 个同名文件，继续处理")
                return {
                    "status": "success", 
                    "to_cid": to_cid, 
                    "names": names,
                    "share_url": share_url,
                    "recursive_links": [],
                    "metadata": metadata or {},
                    "have_vio": have_vio_file == 1
                }
            else:
                logger.warning("⚠️ 115 提示已接收，但在保存目录未找到文件。尝试创建新目录重试转存...")
                new_folder_name = f"Retry_{int(time.time())}"
                resp = await self._api_call_with_timeout(
                    self.client.fs_makedirs_app, new_folder_name, pid=to_cid, async_=True,
                    **self._get_ios_ua_kwargs()
                )
                check_response(resp)
                new_cid = int(resp.get("cid") or resp.get("id") or (resp.get("data") or {}).get("cid") or 0)
                
                if not new_cid:
                    raise RuntimeError("创建重试目录失败，未获取到有效CID")
                    
                logger.info(f"📁 已创建重试目录: {new_folder_name} (CID: {new_cid})")
                
                retry_payload = receive_payload.copy()
                retry_payload["cid"] = new_cid
                
                recv_resp = await self._api_call_with_timeout(
                    self.client.share_receive_app, retry_payload, async_=True,
                    timeout=API_TIMEOUT, label="share_receive_retry",
                    **self._get_ios_ua_kwargs()
                )
                check_response(recv_resp)
                logger.info(f"✅ 在新目录转存成功: {share_url} -> CID {new_cid}")
                
                return {
                    "status": "success", 
                    "to_cid": new_cid, 
                    "names": names,
                    "share_url": share_url,
                    "recursive_links": [],
                    "metadata": metadata or {},
                    "have_vio": have_vio_file == 1
                }
                
        except Exception as check_e:
            logger.error(f"❌ 处理已接收逻辑(验证或重试转存)时出错: {check_e}")
            
            errno_val = getattr(check_e, "errno", None)
            if hasattr(check_e, 'args') and len(check_e.args) >= 2 and isinstance(check_e.args[1], dict):
                if not errno_val:
                    errno_val = check_e.args[1].get("errno")
                    
            if errno_val == 4200045 or "4200045" in str(check_e) or "已经接收" in str(check_e) or "已接收" in str(check_e):
                return {
                    "status": "error",
                    "error_type": "already_exists_missing",
                    "message": "该分享链接您已转存过。115 限制同一链接由于文件丢失而无法重复转存，重试转存也失败，请尝试寻找原文件或从回收站还原。"
                }
            return {
                "status": "error", 
                "error_type": "unknown",
                "message": f"保存失败，且重试转存报错: {str(check_e)}"
            }

    async def save_share_link(self, share_url: str, metadata: dict = None, target_dir: Optional[str] = None):
        return await self._enqueue_op("save_share", self._save_share_link_internal, share_url, metadata, target_dir)

    async def save_and_share(self, share_url: str, metadata: dict = None, target_dir: Optional[str] = None):
        async def _internal_flow():
            save_res = await self._save_share_link_internal(share_url, metadata, target_dir)
            if save_res and save_res.get("status") == "success":
                save_res = await self._organize_files(save_res, metadata)
                share_res = await self.create_share_link(save_res)
                if isinstance(share_res, str):
                    return {"status": "success", "share_link": share_res}
                elif isinstance(share_res, dict) and share_res.get("status") == "error":
                    return {
                        "status": "error",
                        "error_type": share_res.get("error_type", "share_failed"),
                        "message": share_res.get("message", "生成分享链接失败")
                    }
                return {
                    "status": "error",
                    "error_type": "share_failed",
                    "message": "转存成功但生成分享链接失败"
                }
            return save_res

        return await self._enqueue_op(f"save_and_share({share_url})", _internal_flow)

    async def _save_share_link_internal(self, share_url: str, metadata: dict = None, target_dir: Optional[str] = None):
        if not self.client:
            logger.warning("P115Client not initialized, cannot save link")
            return None
        
        logger.info(f"📥 开始处理分享链接: {share_url}")
        try:
            payload = share_extract_payload(share_url)
            
            snap_resp = await self._api_call_with_timeout(
                self.client.share_snap_app, payload, async_=True,
                timeout=API_TIMEOUT, label="share_snap",
                **self._get_ios_ua_kwargs()
            )
            check_response(snap_resp)
            logger.debug(f"📋 share_snap 响应数据: {snap_resp.get('data')}")

            data = snap_resp.get("data", {})
            if not data:
                logger.error("❌ share_snap 响应中缺少 data 字段")
                return {
                    "status": "error",
                    "error_type": "api_error",
                    "message": "获取分享信息失败：API 响应数据为空"
                }

            share_info = data.get("shareinfo" if "shareinfo" in data else "share_info", {})
            share_state = data.get("share_state", share_info.get("share_state", share_info.get("status")))
            if share_state is not None:
                try:
                    share_state = int(share_state)
                except (ValueError, TypeError):
                    pass
            share_title = share_info.get("share_title", "")
            have_vio_file = share_info.get("have_vio_file", 0)
            
            logger.info(f"📊 分享状态: {share_state}, 标题: {share_title}, 违规标志: {have_vio_file}")

            if have_vio_file == 1:
                logger.warning(f"⚠️ 分享链接包含违规内容标志 (have_vio_file=1): {share_url}")

            is_snapshotting = "正在生成文件快照" in str(snap_resp)
            if share_state == 0 or is_snapshotting:
                reason = "snapshotting" if is_snapshotting else "auditing"
                logger.info(f"🔍 分享链接处于{ '审核中' if reason == 'auditing' else '快照生成中' }，进入轮询等待队列: {share_url}")
                async with async_session() as session:
                    new_task = PendingLink(
                        share_url=share_url,
                        metadata_json=metadata or {},
                        status=reason
                    )
                    session.add(new_task)
                    await session.commit()
                    db_id = new_task.id
                
                return {
                    "status": "pending",
                    "reason": reason,
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
            
            items = data.get("list", [])
            if not items:
                logger.warning(f"⚠️ 分享链接内没有文件。have_vio_file={have_vio_file}, 状态: {snap_resp.get('state')}")
                if have_vio_file == 1:
                    return {
                        "status": "error",
                        "error_type": "violated",
                        "message": "链接包含违规内容，无法转存分享"
                    }
                return {
                    "status": "error",
                    "error_type": "empty_share",
                    "message": "分享链接内没有可供转存的文件"
                }
            
            fids = []
            names = []
            for item in items:
                fid = item.get("fid") or item.get("cid")
                if fid:
                    fids.append(str(fid))
                    raw_name = item.get("n") or item.get("fn") or item.get("name") or item.get("file_name") or item.get("title")
                    if not raw_name:
                        logger.warning(f"⚠️ 无法从分享项提取文件名，可用的键有: {list(item.keys())}")
                        raw_name = "未知"
                    cleaned_name = raw_name.replace("\\'", "'").replace('\\"', '"')
                    names.append(cleaned_name)
                else:
                    logger.warning(f"Item missing both fid and cid: {item}")
            
            if not fids:
                logger.error(f"❌ 未能从列表项提取到任何有效的文件或文件夹 ID。项目数: {len(items)}")
                return {
                    "status": "error",
                    "error_type": "parse_error",
                    "message": "解析分享文件列表失败，无法提取文件 ID"
                }
            
            logger.info(f"📦 检测到 {len(fids)} 个项目: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}")
            
            to_cid = None
            max_network_wait = 1800
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
            
            try:
                total_size = int(share_info.get("file_size") or 0)
            except (ValueError, TypeError):
                total_size = 0
            await self.check_and_prepare_capacity(file_count=len(fids), total_size=total_size)
            to_cid = await self._ensure_save_dir(target_dir)

            receive_payload = {
                "share_code": payload["share_code"],
                "receive_code": payload["receive_code"] or "",
                "file_id": ",".join(fids),
                "cid": to_cid
            }
            
            try:
                recv_resp = await self._api_call_with_timeout(
                    self.client.share_receive_app, receive_payload, async_=True,
                    timeout=API_TIMEOUT, label="share_receive",
                    **self._get_ios_ua_kwargs()
                )
                check_response(recv_resp)
                logger.info(f"✅ 链接转存指令已发送: {share_url} -> CID {to_cid}")
                recursive_links = []
            except Exception as recv_error:
                error_info = getattr(recv_error, "args", [None, {}])[1] if hasattr(recv_error, "args") and len(recv_error.args) >= 2 else {}
                errno_val = error_info.get("errno") if isinstance(error_info, dict) else None
                
                if errno_val == 4200044 or "超过当前等级限制" in str(recv_error):
                    logger.warning(f"⚠️ 触发 115 非会员 500 文件保存限制，尝试递归分批保存: {share_url}")
                    recursive_links = await self._save_share_recursive(share_url, to_cid)
                    logger.info(f"✅ 递归分批保存指令已处理完毕: {share_url}")
                elif errno_val == 4200045 or "4200045" in str(recv_error) or "已经接收" in str(recv_error) or "已接收" in str(recv_error):
                    return await self._handle_already_received(to_cid, names, share_url, metadata, have_vio_file, receive_payload)
                else:
                    raise
            
            return {
                "status": "success", 
                "to_cid": to_cid, 
                "names": names,
                "share_url": share_url,
                "recursive_links": recursive_links if 'recursive_links' in locals() else [],
                "metadata": metadata or {},
                "have_vio": have_vio_file == 1
            }
        except Exception as e:
            try:
                errno_val = getattr(e, "errno", None)
                if hasattr(e, 'args') and len(e.args) >= 2 and isinstance(e.args[1], dict):
                    error_msg = str(e.args[1].get('error', e))
                    if not errno_val:
                        errno_val = e.args[1].get('errno')
                else:
                    error_msg = str(e)
            except:
                error_msg = "未知异常"
                errno_val = None
            
            if "正在生成文件快照" in error_msg:
                logger.info(f"🔍 分享链接正在生成快照，进入轮询等待队列: {share_url}")
                async with async_session() as session:
                    new_task = PendingLink(
                        share_url=share_url,
                        metadata_json=metadata or {},
                        status="snapshotting"
                    )
                    session.add(new_task)
                    await session.commit()
                    db_id = new_task.id
                
                return {
                    "status": "pending",
                    "reason": "snapshotting",
                    "share_url": share_url,
                    "metadata": metadata or {},
                    "db_id": db_id
                }
            
            if "限制接收" in error_msg:
                logger.warning(f"🚫 触发 115 接收限制: {share_url}")
                self.set_restriction(hours=1.0)
                
                async with async_session() as session:
                    new_task = PendingLink(
                        share_url=share_url,
                        metadata_json=metadata or {},
                        status="restricted"
                    )
                    session.add(new_task)
                    await session.commit()
                    db_id = new_task.id
                
                return {
                    "status": "pending",
                    "reason": "restricted",
                    "share_url": share_url,
                    "metadata": metadata or {},
                    "db_id": db_id
                }

            if errno_val == 4200045 or "4200045" in error_msg or "已经接收" in error_msg or "已接收" in error_msg:
                retry_payload = {
                    "share_code": payload["share_code"],
                    "receive_code": payload["receive_code"] or "",
                    "file_id": ",".join(fids) if 'fids' in locals() else "",
                    "cid": to_cid
                }
                return await self._handle_already_received(to_cid, names, share_url, metadata, have_vio_file, retry_payload)

            logger.error("❌ 保存分享链接发生程序异常: {}", error_msg)
            return {
                "status": "error",
                "error_type": "exception",
                "message": f"程序异常: {error_msg}"
            }

    async def _save_share_recursive(self, share_url: str, target_pid: int) -> list[str]:
        payload = share_extract_payload(share_url)
        share_code = payload["share_code"]
        receive_code = payload["receive_code"] or ""
        
        cid_map = {0: target_pid}
        share_links = []
        files_saved_total = 0
        
        share_structure = {0: (None, "")}
        
        async def reconstruct_path(current_share_cid, current_cid_map):
            new_root_cid = await self._ensure_save_dir()
            current_cid_map.clear()
            current_cid_map[0] = new_root_cid
            
            path_names = []
            temp_cid = current_share_cid
            while temp_cid != 0:
                parent, name = share_structure[temp_cid]
                path_names.append(name)
                temp_cid = parent
            path_names.reverse()
            
            current_share = 0
            current_real = new_root_cid
            for name in path_names:
                child_share = next(s_cid for s_cid, info in share_structure.items() if info[0] == current_share and info[1] == name)
                resp = await self._api_call_with_timeout(
                    self.client.fs_makedirs_app, name, pid=current_real, async_=True,
                    **self._get_ios_ua_kwargs()
                )
                check_response(resp)
                current_real = int(resp.get("cid") or resp.get("id") or (resp.get("data") or {}).get("cid") or 0)
                current_cid_map[child_share] = current_real
                current_share = child_share
            
            return current_real

        async for pid, dirs, files in share_iterdir_walk(
            self.client, share_code, receive_code, async_=True
        ):
            if pid not in cid_map:
                logger.info(f"🔄 正在递归深度中重建目录结构 (Share CID: {pid})...")
                cid_map[pid] = await reconstruct_path(pid, cid_map)
                
            current_target_pid = cid_map[pid]
            
            for d in dirs:
                share_cid = d["id"]
                name = d["name"]
                share_structure[share_cid] = (pid, name)
                try:
                    resp = await self._api_call_with_timeout(
                        self.client.fs_makedirs_app, name, pid=current_target_pid, async_=True,
                        label=f"fs_makedirs({name})",
                        **self._get_ios_ua_kwargs()
                    )
                    check_response(resp)
                    new_cid = int(resp.get("cid") or resp.get("id") or (resp.get("data") or {}).get("cid") or 0)
                    if new_cid:
                        cid_map[share_cid] = new_cid
                except Exception as e:
                    if "已经存在" in str(e) or "40004" in str(e):
                        found = await self._find_files_in_dir(current_target_pid, [name])
                        if found:
                            cid_map[share_cid] = int(found[0]["fid"])
                    else:
                        logger.error(f"❌ 递归保存过程中创建子目录 {name} 失败: {e}")
            
            fids = [str(f["id"]) for f in files]
            if not fids:
                continue
                
            for i in range(0, len(fids), 500):
                need_cleanup = files_saved_total >= 10000
                if not need_cleanup and settings.P115_CLEANUP_CAPACITY_ENABLED:
                    used, total = await self.get_storage_stats()
                    if total > 0 and (used / total) > 0.9:
                        need_cleanup = True
                        logger.warning(f"⚠️ 容量逼近上限 ({used/total:.1%})，触发中转清理")

                if need_cleanup:
                    logger.info("📦 触发中转流程：正在生成当前已保存内容的分享链接...")
                    save_dir_cid = await self._ensure_save_dir()
                    ls_resp = await self._api_call_with_timeout(
                        self.client.fs_files_app2, save_dir_cid, async_=True,
                        **self._get_ios_ua_kwargs()
                    )
                    ls_items = ls_resp.get("data", [])
                    ls_names = [it["n"] for it in ls_items]
                    
                    if ls_names:
                        intermediate_link = await self.create_share_link({"to_cid": save_dir_cid, "names": ls_names})
                        if intermediate_link:
                            logger.info(f"📤 中转链接已生成: {intermediate_link}")
                            share_links.append(intermediate_link)

                    await self._do_cleanup_logic()
                    logger.info("🧹 中转清理完成，等待 5 秒恢复...")
                    await asyncio.sleep(5)
                    
                    files_saved_total = 0
                    current_target_pid = await reconstruct_path(pid, cid_map)
                
                batch = fids[i:i+500]
                try:
                    receive_payload = {
                        "share_code": share_code,
                        "receive_code": receive_code,
                        "file_id": ",".join(batch),
                        "cid": current_target_pid
                    }
                    recv_resp = await self._api_call_with_timeout(
                        self.client.share_receive_app, receive_payload, async_=True,
                        timeout=API_TIMEOUT, label=f"share_receive_batch({i//500})",
                        **self._get_ios_ua_kwargs()
                    )
                    check_response(recv_resp)
                    files_saved_total += len(batch)
                    logger.info(f"✅ 递归分批转存成功: {len(batch)} 个文件 -> CID {current_target_pid} (本轮累计: {files_saved_total})")
                    
                    await asyncio.sleep(random.randint(2, 3))
                except Exception as e:
                    errno_val = getattr(e, "errno", None)
                    if hasattr(e, 'args') and len(e.args) >= 2 and isinstance(e.args[1], dict):
                        if not errno_val:
                            errno_val = e.args[1].get("errno")
                            
                    if errno_val == 4200045 or "4200045" in str(e) or "已经接收" in str(e) or "已接收" in str(e):
                        continue
                    logger.error(f"❌ 递归转存文件包失败: {e}")
        
        return share_links

    async def get_share_status(self, share_url: str):
        try:
            payload = share_extract_payload(share_url)
            snap_resp = await self._api_call_with_timeout(
                self.client.share_snap_app, payload, async_=True,
                timeout=API_TIMEOUT, label="share_snap(status)",
                **self._get_ios_ua_kwargs()
            )
            check_response(snap_resp)
            
            data = snap_resp.get("data", {})
            share_info = data.get("shareinfo" if "shareinfo" in data else "share_info", {})
            share_state = data.get("share_state", share_info.get("share_state", share_info.get("status")))
            if share_state is not None:
                try:
                    share_state = int(share_state)
                except (ValueError, TypeError):
                    pass
            share_title = share_info.get("share_title", "")
            have_vio_file = share_info.get("have_vio_file", 0)
            
            is_snapshotting = "正在生成文件快照" in str(snap_resp)
            res = {
                "share_state": share_state,
                "is_auditing": share_state == 0,
                "is_snapshotting": is_snapshotting,
                "is_pending": share_state == 0 or is_snapshotting,
                "is_expired": share_state == 7,
                "is_prohibited": have_vio_file == 1,
                "title": share_title
            }
            if is_snapshotting:
                logger.info(f"📊 检查链接发现正在生成快照: {share_url}")
            logger.debug(f"📊 检查链接状态: {share_url} -> {res}")
            return res
        except Exception as e:
            error_msg = str(e)
            if any(code in error_msg for code in ["4100009", "4100010"]) or \
               any(msg in error_msg for msg in ["链接已失效", "分享已取消"]):
                logger.warning(f"⏰ 检查链接状态发现链接已失效或被取消: {share_url}")
                return {
                    "share_state": 7,
                    "is_auditing": False,
                    "is_expired": True,
                    "is_prohibited": False,
                    "title": ""
                }
            if "正在生成文件快照" in error_msg:
                logger.info(f"📊 检查链接状态发现正在生成快照: {share_url}")
                return {
                    "share_state": 0,
                    "is_auditing": False,
                    "is_snapshotting": True,
                    "is_pending": True,
                    "is_expired": False,
                    "is_prohibited": False,
                    "title": ""
                }
            logger.error(f"❌ 检查链接状态失败: {share_url}, 错误: {e}")
            return None

    async def _find_files_in_dir(self, cid: int, target_names: list) -> list:
        matched = []
        
        for name in target_names:
            try:
                search_resp = await self._api_call_with_timeout(
                    self.client.fs_search_app2,
                    {"search_value": name, "cid": cid, "limit": 20},
                    async_=True,
                    timeout=30, max_retries=2, label=f"fs_search({name})",
                    **self._get_ios_ua_kwargs()
                )
                check_response(search_resp)
                search_data = search_resp.get("data", [])
                
                if isinstance(search_data, dict):
                    search_items = search_data.get("list", [])
                else:
                    search_items = search_data
                
                logger.debug(f"🔍 fs_search '{name}' 在 CID:{cid} 返回 {len(search_items)} 条结果")
                
                for item in search_items:
                    item_name = item.get("n") or item.get("fn") or item.get("name") or item.get("file_name") or item.get("title") or item.get("category_name")
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
        
        found_names = {m["name"] for m in matched}
        remaining_names = [n for n in target_names if n not in found_names]
        logger.info(f"🔍 fs_search 找到 {len(matched)}/{len(target_names)} 个文件，尝试 fs_files 查找剩余: {remaining_names}")
        
        try:
            resp = await self._api_call_with_timeout(
                self.client.fs_files_app2,
                {"cid": cid, "limit": 500, "show_dir": 1},
                async_=True,
                timeout=30, max_retries=2, label="fs_files",
                **self._get_ios_ua_kwargs()
            )
            check_response(resp)
            file_list = resp.get("data", [])
            
            if isinstance(file_list, dict):
                file_list = file_list.get("list", [])
            
            resp_path = resp.get("path", [])
            resp_cid = None
            if resp_path:
                last_path = resp_path[-1] if isinstance(resp_path, list) else resp_path
                resp_cid = last_path.get("cid") if isinstance(last_path, dict) else None
            
            actual_count = resp.get("count", "?")
            logger.debug(f"📂 fs_files CID:{cid} 返回 {len(file_list)} 项 (总数: {actual_count}, 路径CID: {resp_cid})")
            
            if resp_cid is not None and str(resp_cid) != str(cid):
                logger.warning(f"⚠️ fs_files 返回的目录 CID({resp_cid}) 与请求的 CID({cid}) 不匹配！可能目录不存在")
            
            if file_list:
                dir_file_names = [(item.get("n") or item.get("fn") or item.get("name") or item.get("file_name") or item.get("title") or item.get("category_name") or f"? (keys: {list(item.keys())})") for item in file_list[:10]]
                logger.debug(f"📋 目录内文件(前10): {dir_file_names}")
            
            for item in file_list:
                item_name = item.get("n") or item.get("fn") or item.get("name") or item.get("file_name") or item.get("title") or item.get("category_name")
                if item_name in remaining_names:
                    item_id = item.get("fid") or item.get("cid") or item.get("file_id") or item.get("category_id") or item.get("id")
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
            logger.info(f"⏳ 等待 2 秒以确保文件保存开始...")
            await asyncio.sleep(2)
            
            new_fids = []
            matched_files = []
            
            max_poll_attempts = 10
            for poll_attempt in range(1, max_poll_attempts + 1):
                try:
                    logger.info(f"🔍 正在查找文件 (第 {poll_attempt}/{max_poll_attempts} 次), 目标目录 CID: {to_cid}")
                    current_matched = await self._find_files_in_dir(to_cid, names)
                    
                    if current_matched:
                        if len(current_matched) == len(names):
                            logger.info(f"✅ 文件已全部到达，共 {len(current_matched)} 个，立即继续")
                            new_fids = [f["fid"] for f in current_matched]
                            break
                        
                        if matched_files:
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
                            await asyncio.sleep(2)
                    else:
                        logger.warning(f"⚠️ 轮询未找到文件 (第 {poll_attempt}/{max_poll_attempts} 次)")
                        if poll_attempt < max_poll_attempts:
                            await asyncio.sleep(2)
                            
                except Exception as e:
                    logger.warning(f"⚠️ 查找文件失败 (轮询 {poll_attempt}/{max_poll_attempts}): {e}")
                    if poll_attempt < max_poll_attempts:
                        await asyncio.sleep(5)
            
            if not new_fids and matched_files:
                logger.info(f"⚠️ 文件未完全稳定，但使用 {len(matched_files)} 个已匹配的文件尝试创建分享")
                new_fids = [f["fid"] for f in matched_files]
            
            if not new_fids:
                logger.warning(f"⚠️ 在保存目录 {to_cid} 中未找到对应的文件 {names}，可能 115 处理延迟或保存失败")
                return None
            
            share_links = []
            fids_str_list = [str(fid) for fid in new_fids]
            max_share_retries = 3
            
            for batch_idx, i in enumerate(range(0, len(fids_str_list), 10000), 1):
                batch_fids = fids_str_list[i:i+10000]
                batch_share_code = None
                batch_receive_code = None
                
                for retry_attempt in range(1, max_share_retries + 1):
                    try:
                        logger.info(f"📤 正在创建分享链接 (分卷 {batch_idx}, 尝试 {retry_attempt}/{max_share_retries})...")
                        send_resp = await self._api_call_with_timeout(
                            self.client.share_send_app, ",".join(batch_fids), async_=True,
                            timeout=API_TIMEOUT, max_retries=1, label=f"share_send_batch_{batch_idx}",
                            **self._get_ios_ua_kwargs()
                        )
                        check_response(send_resp)
                        
                        data = send_resp["data"]
                        batch_share_code = data.get("share_code")
                        batch_receive_code = data.get("receive_code") or data.get("recv_code")
                        
                        logger.info(f"✅ 分享分卷 {batch_idx} 创建成功: {batch_share_code}")
                        break
                        
                    except Exception as share_error:
                        error_str = str(share_error)
                        if ("4100005" in error_str or "已被移动或删除" in error_str) and retry_attempt < max_share_retries:
                            logger.warning(f"⚠️ 文件尚未就绪，等待 5 秒后重试...")
                            await asyncio.sleep(5)
                        else:
                            logger.error(f"❌ 创建分享分卷 {batch_idx} 失败: {share_error}")
                            if batch_idx == 1: raise
                            break
                
                if batch_share_code:
                    try:
                        logger.info(f"🔄 正在将分享链接 {batch_share_code} 转换为长期有效...")
                        await self._api_call_with_timeout(
                            self.client.share_update_app, {"share_code": batch_share_code, "share_duration": -1},
                            async_=True, timeout=API_TIMEOUT, max_retries=2, label=f"share_update_{batch_idx}",
                            **self._get_ios_ua_kwargs()
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ 转换长期分享失败 (分卷 {batch_idx}): {e}")
                    
                    full_link = f"https://115.com/s/{batch_share_code}"
                    if batch_receive_code:
                        full_link += f"?password={batch_receive_code}"
                    share_links.append(full_link)
            
            if not share_links:
                logger.error("❌ 未能生成任何分享链接")
                return None
            
            if len(share_links) > 1:
                formatted_links = []
                for idx, link in enumerate(share_links, 1):
                    formatted_links.append(f"链接 {idx}: {link}")
                result_share = "\n".join(formatted_links)
                logger.info(f"🔗 已生成 {len(share_links)} 个分卷分享链接")
            else:
                result_share = share_links[0]
                logger.info(f"🔗 长期分享链接已生成: {result_share}")
                
            return result_share
            
        except Exception as e:
            logger.error(f"❌ 创建新分享链接失败: {e}")
            error_info = getattr(e, "args", [None, {}])[1] if hasattr(e, "args") and len(e.args) >= 2 else {}
            errno_val = error_info.get("errno") if isinstance(error_info, dict) else None
            
            if errno_val == 4100016 and save_result.get("have_vio"):
                return {
                    "status": "error",
                    "error_type": "violated",
                    "message": "链接包含违规内容，无法转存分享"
                }
            
            error_msg = str(e)
            if "限制分享" in error_msg:
                logger.warning(f"🚫 触发 115 分享限制")
                self.set_restriction(hours=1.0)
                return {
                    "status": "pending",
                    "reason": "restricted",
                    "share_url": save_result.get("share_url"),
                    "metadata": save_result.get("metadata", {})
                }

            return None

    async def cleanup_save_directory(self, wait: bool = True):
        try:
            async with self._acquire_task_lock("cleanup", wait=wait):
                return await self._cleanup_save_directory_internal()
        except BlockingIOError:
            return False

    async def _cleanup_save_directory_internal(self) -> bool:
        try:
            logger.info(f"🧹 开始清理保存目录: {settings.P115_SAVE_DIR}")
            cid = await self._ensure_save_dir()
            if not cid:
                return False
            
            resp = await self._api_call_with_timeout(
                self.client.fs_delete, cid, async_=True,
                timeout=API_TIMEOUT, label="fs_delete",
                **self._get_ios_ua_kwargs()
            )
            check_response(resp)
            
            self.clear_save_dir_cache()
            logger.info("✅ 保存目录清理完成")
            return True
        except Exception as e:
            logger.error(f"❌ 内部清理保存目录失败: {e}")
            return False

    async def get_storage_stats(self) -> Tuple[int, int]:
        if not self.client:
            return 0, 0
        try:
            resp = await self._api_call_with_timeout(
                self.client.user_space_info, async_=True,
                timeout=API_TIMEOUT, label="user_space_info",
                **self._get_ios_ua_kwargs()
            )
            check_response(resp)
            data = resp.get("data", {})
            
            def extract_size(val) -> int:
                if isinstance(val, dict):
                    return int(val.get("size") or val.get("size_total") or val.get("size_use") or 0)
                try:
                    return int(val) if val is not None else 0
                except (ValueError, TypeError):
                    return 0

            used = extract_size(data.get("all_used") or data.get("all_use") or data.get("used") or 0)
            total = extract_size(data.get("all_total") or data.get("total") or 0)
            return used, total
        except Exception as e:
            logger.error("❌ 获取网盘容量失败: {}", str(e))
            return 0, 0

    async def check_and_prepare_capacity(self, file_count: int = 0, total_size: int = 0):
        if not settings.P115_CLEANUP_CAPACITY_ENABLED:
            return

        used_bytes, total_bytes = await self.get_storage_stats()
        if total_bytes == 0:
            return
            
        remaining_bytes = total_bytes - used_bytes

        if file_count > 500 and total_size > remaining_bytes:
            logger.info(f"🚀 预测性清理：检测到大批量文件 ({file_count} 个, {total_size/(1024**3):.2f}GB)，剩余空间不足，执行清理...")
            await self._do_cleanup_logic()
            await asyncio.sleep(3)
            return

        if total_size > 0 and total_size > remaining_bytes:
             logger.warning(f"⚠️ 剩余空间不足 (需 {total_size/(1024**3):.2f}GB, 剩 {remaining_bytes/(1024**3):.2f}GB)，执行清理...")
             await self._do_cleanup_logic()
             await asyncio.sleep(3)

    async def check_capacity_and_cleanup(self, mode: str = "manual"):
        wait_for_lock = True
        if mode == "scheduled":
            wait_for_lock = False
            try:
                async with self._acquire_task_lock("capacity_check_probe", wait=False):
                    pass
            except BlockingIOError:
                logger.info("⏭️ 定时容量检查：检测到转存任务运行中，按计划跳过锁定监测")
                return False
        
        logger.debug(f"🔍 [容量检查] 模式: {mode}, 正在获取存储状态...")
            
        use_fallback = (mode == "batch" and not settings.P115_CLEANUP_CAPACITY_ENABLED)
        limit = settings.P115_CLEANUP_CAPACITY_LIMIT
        unit = settings.P115_CLEANUP_CAPACITY_UNIT
        
        used_bytes, total_bytes = await self.get_storage_stats()
        if total_bytes <= 0:
            return False

        should_cleanup = False
        
        if use_fallback:
            if (total_bytes - used_bytes) < (total_bytes * 0.1):
                logger.warning(f"🚨 [批量任务] 剩余空间不足 10% ({(total_bytes-used_bytes)/(1024**4):.2f}TB)，触发硬性清理")
                should_cleanup = True
        elif settings.P115_CLEANUP_CAPACITY_ENABLED and limit > 0:
            limit_bytes = limit * (1024**4) if unit == "TB" else limit * (1024**3)
            if used_bytes > limit_bytes:
                logger.info(f"📊 [{mode}] 网盘已用空间 ({used_bytes/(1024**4):.2f}TB) 超过阈值 ({limit} {unit})")
                should_cleanup = True
        
        if should_cleanup or mode == "manual":
            try:
                async with self._acquire_task_lock("cleanup", wait=wait_for_lock):
                    logger.info(f"🧹 执行容量管理清理 (模式: {mode})...")
                    await self._do_cleanup_logic()
                    return True
            except BlockingIOError:
                if mode == "scheduled":
                    logger.info("⏭️ 定时容量检查：转存锁获取冲突，按计划跳过任务")
                return False
        else:
            logger.debug(f"✅ [容量检查] 模式: {mode}, 当前空间充足 ({used_bytes/(1024**4):.2f}TB)，无需清理")
        return False

    async def _do_cleanup_logic(self):
        await self._cleanup_save_directory_internal()
        await self._cleanup_recycle_bin_internal()

    async def get_history_link(self, original_url: str) -> Optional[Union[str, list[str]]]:
        try:
            import json
            from app.models.schema import LinkHistory
            async with async_session() as session:
                result = await session.execute(
                    select(LinkHistory).where(LinkHistory.original_url == original_url)
                )
                record = result.scalar_one_or_none()
                if record:
                    link_val = record.share_link
                    if link_val.startswith("[") and link_val.endswith("]"):
                        try:
                            return json.loads(link_val)
                        except:
                            return link_val
                    return link_val
            return None
        except Exception as e:
            logger.error(f"查询历史记录失败: {e}")
            return None

    async def save_history_link(self, original_url: str, share_link: Union[str, list[str]]):
        try:
            import json
            from app.models.schema import LinkHistory
            
            if isinstance(share_link, list):
                if not share_link:
                    return
                link_to_store = json.dumps(share_link) if len(share_link) > 1 else share_link[0]
            else:
                link_to_store = share_link

            async with async_session() as session:
                existing = await session.execute(
                    select(LinkHistory).where(LinkHistory.original_url == original_url)
                )
                record = existing.scalar_one_or_none()
                if record:
                    record.share_link = link_to_store
                else:
                    new_record = LinkHistory(original_url=original_url, share_link=link_to_store)
                    session.add(new_record)
                await session.commit()
                logger.info(f"已保存历史记录: {original_url} -> {link_to_store[:50]}...")
        except Exception as e:
            logger.error(f"保存历史记录失败: {e}")

    async def delete_all_history_links(self):
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

    async def get_all_history_links(self, limit: int = 50) -> List[Dict]:
        try:
            from app.models.schema import LinkHistory
            from sqlalchemy import select, desc
            async with async_session() as session:
                result = await session.execute(
                    select(LinkHistory).order_by(desc(LinkHistory.created_at)).limit(limit)
                )
                records = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "original_url": r.original_url,
                        "share_link": r.share_link,
                        "created_at": r.created_at.isoformat() if r.created_at else None
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"获取历史记录失败: {e}")
            return []

    async def cleanup_recycle_bin(self, wait: bool = True):
        try:
            async with self._acquire_task_lock("cleanup", wait=wait):
                return await self._cleanup_recycle_bin_internal()
        except BlockingIOError:
            return False

    async def _cleanup_recycle_bin_internal(self) -> bool:
        try:
            logger.info("🗑️ 开始清空回收站...")
            payload = {}
            if settings.P115_RECYCLE_PASSWORD:
                payload["password"] = settings.P115_RECYCLE_PASSWORD
                logger.debug("使用回收站密码")
            
            resp = await self._api_call_with_timeout(
                self.client.recyclebin_clean_app, payload, async_=True,
                timeout=API_TIMEOUT, label="recyclebin_clean",
                **self._get_ios_ua_kwargs()
            )
            check_response(resp)
            logger.info("✅ 回收站已清空")
            return True
        except Exception as e:
            logger.error("❌ 内部清空回收站失败: {}", e)
            return False

    # ========== 整理方法（修复版，增强异常处理） ==========
    async def _organize_files(self, save_result: dict, metadata: dict) -> dict:
        """整理文件：识别媒体、移动目录、重命名
           使用转存后的文件/文件夹名作为标题来源
        """
        if not settings.TMDB_API_KEY:
            logger.debug("TMDB API Key 未配置，跳过整理")
            return save_result

        # 直接从 save_result 获取转存后的文件/文件夹名
        if not save_result.get('names'):
            logger.debug("save_result 中无文件名信息，跳过整理")
            return save_result

        title_candidate = save_result['names'][0]
        logger.info(f"使用转存后的文件/文件夹名作为标题: {title_candidate}")

        # 使用智能分析器解析文件名，获取干净标题和其他信息
        analyzer = SmartMediaAnalyzer()
        parsed = analyzer.analyze(title_candidate)
        clean_title = parsed.title
        logger.info(f"解析后的干净标题: {clean_title}")

        organizer = MediaOrganizer(settings.TMDB_CONFIG)
        tmdb = TMDBClient()
        try:
            # 1. 尝试从文件名中提取 TMDB ID
            tmdb_id = organizer.extract_tmdb_id(title_candidate)
            year = parsed.year or organizer.extract_year(title_candidate)  # 优先使用解析到的年份
            
            media_info = None
            match_method = None

            # 2. 优先使用 ID 查询
            if tmdb_id:
                for mtype in ['movie', 'tv']:
                    try:
                        media_info = await tmdb.get_details(mtype, tmdb_id)
                    except Exception as e:
                        logger.warning(f"获取媒体详情失败 (ID {tmdb_id}, type {mtype}): {e}")
                        continue
                    if media_info:
                        media_info['media_type'] = mtype
                        
                        # 获取媒体的年份
                        media_year = None
                        if mtype == 'movie':
                            release = media_info.get('release_date')
                            if release:
                                media_year = int(release[:4])
                        else:
                            first_air = media_info.get('first_air_date')
                            if first_air:
                                media_year = int(first_air[:4])
                        
                        # 验证年份（如果提供了年份）
                        if year and media_year and year != media_year:
                            logger.warning(f"ID {tmdb_id} 年份不匹配: 期望 {year}, 实际 {media_year}，放弃此结果")
                            media_info = None
                            continue
                        
                        logger.info(f"通过 ID {tmdb_id} 找到媒体: {media_info.get('title') or media_info.get('name')} (年份: {media_year})")
                        match_method = "id"
                        break
                
                if not media_info:
                    logger.info(f"通过 ID {tmdb_id} 未找到有效媒体，回退到标题搜索")

            # 3. 如果未通过 ID 找到，使用标题搜索
            if not media_info:
                search_title = clean_title if clean_title else title_candidate
                logger.info(f"使用标题搜索: {search_title}")
                try:
                    media_info = await tmdb.search_multi(search_title, year)
                except Exception as e:
                    logger.error(f"TMDB 搜索失败: {e}")
                    media_info = None
                
                if media_info:
                    # 验证搜索结果是否与提取的年份匹配
                    result_id = media_info.get('id')
                    result_year = None
                    mtype = media_info.get('media_type')
                    
                    if mtype == 'movie':
                        release = media_info.get('release_date')
                        if release:
                            result_year = int(release[:4])
                    else:
                        first_air = media_info.get('first_air_date')
                        if first_air:
                            result_year = int(first_air[:4])
                    
                    # 年份验证（如果提供了年份）
                    if year and result_year and year != result_year:
                        logger.warning(f"标题搜索到的媒体年份 {result_year} 与提取的年份 {year} 不一致，放弃此结果")
                        media_info = None
                    else:
                        logger.info(f"通过标题搜索找到媒体: {clean_title} (ID: {result_id}, 年份: {result_year})")
                        match_method = "title"

            if not media_info:
                logger.info(f"TMDB 未识别到媒体: {title_candidate[:50]}")
                return save_result

            # 4. 获取详细信息（如果需要）
            media_type = media_info.get('media_type')
            if media_type in ['movie', 'tv'] and 'genres' not in media_info:
                try:
                    details = await tmdb.get_details(media_type, media_info['id'])
                    if details:
                        media_info.update(details)
                except Exception as e:
                    logger.warning(f"获取媒体详情失败: {e}")

            # 5. 匹配规则
            rule = organizer.match_rule(media_info)
            if not rule:
                logger.info(f"未匹配到规则: {clean_title}")
                return save_result

            # 6. 确定目标目录
            target_path = organizer.get_target_path(rule)
            target_cid = await self._ensure_save_dir(target_path)

            # 7. 生成新名称（使用解析到的技术参数）
            # 从 parsed 中获取技术参数
            source = parsed.source
            resolution = parsed.quality.value if parsed.quality != QualityLevel.UNKNOWN else ''
            video_codec = parsed.codec
            audio_codec = parsed.audio
            season_episode = f"S{parsed.season:02d}E{parsed.episode:02d}" if parsed.season and parsed.episode else ''

            # 使用 TMDB 的标题和年份
            tmdb_title = media_info.get('title') or media_info.get('name') or ''
            tmdb_year = (media_info.get('release_date') or media_info.get('first_air_date') or '')[:4] if media_info else ''

            # 构建新文件名
            parts = [tmdb_title]
            if tmdb_year:
                parts.append(tmdb_year)
            if season_episode:
                parts.append(season_episode)
            if source:
                parts.append(source)
            if resolution:
                parts.append(resolution)
            if video_codec:
                parts.append(video_codec)
            if audio_codec:
                parts.append(audio_codec)

            new_name = '.'.join(parts)
            # 保留原始扩展名
            ext_match = re.search(r'\.([a-zA-Z0-9]+)$', title_candidate)
            if ext_match:
                new_name = f"{new_name}.{ext_match.group(1)}"

            logger.info(f"生成新文件名: {new_name}")

            # 8. 移动/重命名（修复解包错误，增强异常处理）
            to_cid = save_result.get('to_cid')
            names = save_result.get('names', [])
            if len(names) == 1:
                old_fid = await self._find_single_fid(to_cid, names[0])
                if old_fid:
                    try:
                        logger.debug(f"正在移动文件: old_fid={old_fid}, new_name={new_name}, target_cid={target_cid}")
                        # 调用 fs_rename，处理返回值
                        resp = await self._api_call_with_timeout(
                            self.client.fs_rename,
                            old_fid, new_name, pid=target_cid,
                            async_=True, **self._get_ios_ua_kwargs()
                        )
                        
                        # 判断响应是否成功
                        success = False
                        if resp is None:
                            success = False
                            logger.warning("fs_rename 返回 None")
                        elif isinstance(resp, dict):
                            success = resp.get('state', False) or resp.get('errCode') == 0
                            if not success:
                                logger.warning(f"fs_rename 返回失败字典: {resp}")
                        elif isinstance(resp, bool):
                            success = resp
                        elif isinstance(resp, int):
                            success = resp == 0
                        elif isinstance(resp, str):
                            success = resp.lower() in ('true', 'ok', 'success')
                        elif isinstance(resp, (list, tuple)):
                            # 避免解包错误，直接取第一个元素作为状态
                            if len(resp) > 0:
                                first = resp[0]
                                if isinstance(first, bool):
                                    success = first
                                elif isinstance(first, int):
                                    success = first == 0
                                elif isinstance(first, str):
                                    success = first.lower() in ('true', 'ok', 'success')
                                else:
                                    logger.warning(f"无法识别的元组元素类型: {type(first)}，假设成功")
                                    success = True
                            else:
                                success = False
                        else:
                            # 如果返回其他类型，可能是成功（根据p115client惯例）
                            logger.warning(f"未知响应类型: {type(resp)}，假设成功")
                            success = True
                        
                        if success:
                            logger.info(f"✅ 已移动并重命名 {names[0]} -> {target_path}/{new_name}")
                            # 更新 save_result，以便后续分享链接使用新位置和新名称
                            save_result['to_cid'] = target_cid
                            save_result['names'] = [new_name]
                        else:
                            logger.error(f"❌ 移动/重命名失败，响应: {resp}")
                    except Exception as e:
                        logger.error(f"❌ 移动/重命名过程发生异常: {e}", exc_info=True)
                else:
                    logger.warning(f"⚠️ 未找到文件 {names[0]} 进行整理")
            else:
                logger.warning("多文件整理暂未实现，跳过")

            return save_result
        except Exception as e:
            logger.error(f"整理过程出错: {e}")
            return save_result
        finally:
            await tmdb.close()

    async def _find_single_fid(self, cid: int, name: str) -> Optional[str]:
        """在指定目录中查找单个文件/文件夹的 ID"""
        files = await self._find_files_in_dir(cid, [name])
        if files:
            return files[0]['fid']
        return None

    
p115_service = P115Service()