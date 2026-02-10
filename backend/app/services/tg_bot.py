from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector
from app.core.config import settings
from app.services.p115 import p115_service
from loguru import logger
import asyncio
import re

class TGService:
    def __init__(self):
        self.bot = None
        self.dp = None
        self.polling_task = None
        self.is_connected = False
        self._lock = asyncio.Lock()
        self._current_polling_id = 0
        self._verify_tasks = []
        if settings.TG_BOT_TOKEN:
            self.init_bot(settings.TG_BOT_TOKEN)

    def init_bot(self, token: str):
        """Synchronous initialization for startup or immediate use. 
        Note: For clean restarts, use restart_polling instead."""
        try:
            # Configure proxy if set
            session = None
            if settings.PROXY_ENABLED and settings.PROXY_HOST and settings.PROXY_PORT:
                proxy_type = settings.PROXY_TYPE.lower()
                auth = f"{settings.PROXY_USER}:{settings.PROXY_PASS}@" if settings.PROXY_USER and settings.PROXY_PASS else ""
                proxy_url = f"{proxy_type}://{auth}{settings.PROXY_HOST}:{settings.PROXY_PORT}"
                session = AiohttpSession(proxy=proxy_url)
                logger.info(f"Telegram Bot using {settings.PROXY_TYPE} proxy: {settings.PROXY_HOST}:{settings.PROXY_PORT}")
                
            self.bot = Bot(token=token, session=session)
            self.dp = Dispatcher()
            self._register_handlers()
            logger.info("Telegram Bot initialized successfully")
            
            # Verify connection asynchronously and track the task
            v_task = asyncio.create_task(self.verify_connection())
            self._verify_tasks.append(v_task)
            # Cleanup finished verify tasks
            v_task.add_done_callback(lambda t: self._verify_tasks.remove(t) if t in self._verify_tasks else None)
        except Exception as e:
            import traceback
            logger.error(f"Failed to initialize Telegram Bot: {e}")
            logger.error(traceback.format_exc())
            self.bot = None
            self.is_connected = False

    async def _cleanup_bot(self, bot_instance=None):
        """Thoroughly clean up specified or current bot instance and its session"""
        target_bot = bot_instance or self.bot
        prefix = f"[Cleanup-Internal]" if bot_instance else f"[Cleanup-Main-ID:{self._current_polling_id}]"
        
        if target_bot:
            try:
                # Log with safe ID access (bot.id is an int)
                bot_id_str = str(getattr(target_bot, 'id', 'unknown'))
                logger.debug(f"{prefix} 🧹 正在清理 Bot 实例 (ID: {bot_id_str[:5]}...)")
                
                # 0. Cancel all pending verify tasks
                num_v = len(self._verify_tasks)
                for vt in self._verify_tasks[:]:
                    if not vt.done():
                        vt.cancel()
                self._verify_tasks.clear()
                if num_v > 0:
                    logger.debug(f"{prefix} 已取消 {num_v} 个验证任务")
                
                # 1. Webhook Cleanup (best effort, may fail if proxy is broken)
                try:
                    logger.debug(f"{prefix} 正在尝试删除 Webhook...")
                    await asyncio.wait_for(target_bot.delete_webhook(drop_pending_updates=True), timeout=3.0)
                    logger.debug(f"{prefix} ✅ Webhook 已删除")
                except asyncio.TimeoutError:
                    logger.debug(f"{prefix} Webhook 删除超时 (代理可能已失效)，跳过")
                except Exception as ex:
                    logger.debug(f"{prefix} Webhook 删除失败 (非致命): {ex}")

                # 2. 直接关闭 HTTP 会话 (强制断开所有 TCP 连接)
                if hasattr(target_bot, 'session') and target_bot.session:
                    try:
                        logger.debug(f"{prefix} 正在强制关闭 HTTP 会话...")
                        await target_bot.session.close()
                        logger.debug(f"{prefix} ✅ HTTP 会话已关闭，所有 TCP 连接已断开")
                    except Exception as ex:
                        logger.debug(f"{prefix} HTTP 会话关闭出错: {ex}")
            except Exception as e:
                logger.error(f"{prefix} ❌ 清理过程中发生严重错误: {e}")
            finally:
                if not bot_instance:
                    self.bot = None
                    self.dp = None
                    self.is_connected = False
                    logger.debug(f"{prefix} 状态变量已重置为 None")

    def _get_allowed_chats(self):
        if not settings.TG_ALLOW_CHATS:
            return []
        return [c.strip() for c in settings.TG_ALLOW_CHATS.split(",") if c.strip()]

    def _register_handlers(self):
        self.dp.message(Command("start"))(self.handle_start)
        self.dp.message(Command("help"))(self.handle_help)
        self.dp.message(Command("id"))(self.handle_id)
        self.dp.message()(self.handle_message)

    async def handle_start(self, message: types.Message):
        allowed = self._get_allowed_chats()
        if allowed and str(message.chat.id) not in allowed:
            logger.warning(f"Unauthorized chat access attempt for /start: {message.chat.id}")
            return
        help_text = (
            "👋 欢迎使用 P115-Share 机器人！\n\n"
            "直接发送 115 分享链接（支持 115.com, 115cdn.com, anxia.com），我将自动为你保存并创建长期分享。\n\n"
            "💡 可用命令：\n"
            "/start - 显示欢迎信息\n"
            "/help - 查看详细使用说明\n"
            "/id - 获取当前聊天的 ID (用于设置白名单)"
        )
        await message.answer(help_text)

    async def handle_help(self, message: types.Message):
        allowed = self._get_allowed_chats()
        if allowed and str(message.chat.id) not in allowed:
            logger.warning(f"Unauthorized chat access attempt for /help: {message.chat.id}")
            return
        await self.handle_start(message)

    async def handle_id(self, message: types.Message):
        allowed = self._get_allowed_chats()
        if allowed and str(message.chat.id) not in allowed:
            logger.warning(f"Unauthorized chat access attempt for /id: {message.chat.id}")
            return
        await message.answer(f"当前聊天 ID: `{message.chat.id}`", parse_mode="Markdown")

    async def handle_message(self, message: types.Message):
        # Whitelist check
        allowed = self._get_allowed_chats()
        if allowed and str(message.chat.id) not in allowed:
            logger.warning(f"Unauthorized chat access attempt: {message.chat.id}")
            return

        # Get message content - text from message or caption from photo message
        full_text = message.caption or message.text or ""
        photo = message.photo[-1] if message.photo else None  # Get highest resolution photo
        entities = message.caption_entities or message.entities or []
        
        # Debug logging
        logger.debug(f"📨 收到消息 - 文本长度: {len(full_text)}, 图片: {bool(photo)}, 实体数量: {len(entities)}")
        if entities:
            logger.debug(f"📋 实体详情: {[(e.type, e.url if hasattr(e, 'url') else None) for e in entities]}")
        
        # Extract URLs from entities (hyperlinks)
        entity_urls = []
        for entity in entities:
            if entity.type == "text_link" and hasattr(entity, 'url'):
                entity_urls.append(entity.url)
                logger.debug(f"🔗 从 text_link 实体提取到 URL: {entity.url}")
            elif entity.type == "url":
                # Extract plain URL from text
                start = entity.offset
                end = entity.offset + entity.length
                url = full_text[start:end]
                entity_urls.append(url)
                logger.debug(f"🔗 从 url 实体提取到 URL: {url}")
        
        # 115 Link Detection (Regex)
        link_pattern = r'https?://(?:115\.com|115cdn\.com|anxia\.com)/s/[a-zA-Z0-9]+(?:\?password=[a-zA-Z0-9]+)?'
        
        # First try to find link in text
        match = re.search(link_pattern, full_text)
        share_url = None
        
        if match:
            share_url = match.group(0)
            logger.info(f"✅ 从文本中检测到 115 链接: {share_url}")
        else:
            # Try entity URLs
            for url in entity_urls:
                if re.match(link_pattern, url):
                    share_url = url
                    logger.info(f"✅ 从实体中检测到 115 链接: {share_url}")
                    break
        
        if not share_url:
            logger.debug(f"❌ 未检测到 115 链接 - 文本: '{full_text[:100]}...', 实体URLs: {entity_urls}")
        
        if share_url:
            logger.info(f"🎯 开始处理来自 {message.chat.id} 的 115 链接: {share_url}")
            
            # Extract description
            description = ""
            if match:
                description = full_text[:match.start()].strip()
            else:
                description = full_text.strip()
            
            logger.debug(f"📝 提取的描述: {description[:100]}...")
            
            status_msg = await message.answer("⌛️ 正在处理链接，请稍候...")
            
            # 0. Check history first
            history_share_link = await p115_service.get_history_link(share_url)
            
            # Convert entities for JSON serialization
            ser_entities = []
            if entities:
                for e in entities:
                    try:
                        ser_entities.append(e.model_dump())
                    except AttributeError:
                        ser_entities.append(dict(e))

            if history_share_link:
                logger.info(f"✨ 发现历史记录，直接使用缓存链接: {share_url} -> {history_share_link}")
                await status_msg.edit_text("⚡ 发现历史记录，正在秒传...")
                
                # Post to channels
                await self.broadcast_to_channels(history_share_link, {
                    "full_text": full_text,
                    "entities": ser_entities,
                    "photo_id": photo.file_id if photo else None,
                    "share_url": share_url
                })

                await status_msg.edit_text(f"⚡ 秒传成功！(历史记录)\n长期分享链接：\n{history_share_link}")
                return
            
            # 1. Save link with metadata
            metadata = {
                "description": description,
                "full_text": full_text,
                "photo_id": photo.file_id if photo else None,
                "share_url": share_url,
                "entities": ser_entities
            }
            save_res = await p115_service.save_share_link(share_url, metadata=metadata)
            
            if save_res and save_res.get("status") == "success":
                await status_msg.edit_text("✅ 链接转存成功，正在为您生成长期分享链接 (预计等待 10 秒)...")
                
                # 2. Create long-term share
                share_link = await p115_service.create_share_link(save_res)
                
                # Save to history
                if share_link:
                    await p115_service.save_history_link(share_url, share_link)
                
                # 3. Post to channels
                await self.broadcast_to_channels(share_link, metadata)

                # 4. Notify user if ID configured
                if settings.TG_USER_ID:
                    try:
                        await self.bot.send_message(settings.TG_USER_ID, f"🔔 链接保存成功！\n原链接: {share_url}\n新分享: {share_link}")
                    except Exception as e:
                        logger.error(f"Failed to send notification to user: {e}")

                await status_msg.edit_text(f"✅ 处理成功！\n长期分享链接：\n{share_link}")
            elif save_res and save_res.get("status") == "pending":
                await status_msg.edit_text("🔍 分享链接正在审核中，将在审核通过后，进行保存分享处理")
                logger.info(f"🚀 启动后台轮询任务，处理审核中链接: {share_url}")
                asyncio.create_task(self.poll_pending_link(message, save_res))
            elif save_res and save_res.get("status") == "error":
                error_type = save_res.get("error_type")
                msg = save_res.get("message", "保存链接失败")
                if error_type == "expired":
                    await status_msg.edit_text(f"⚠️ {msg}，请检查分享是否已失效。")
                elif error_type == "violated":
                    await status_msg.edit_text(f"🚫 {msg}，115 暂不支持转存包含敏感内容的分享。")
                else:
                    await status_msg.edit_text(f"❌ {msg}")
            else:
                await status_msg.edit_text("❌ 保存链接失败，请检查 Cookie 或链接有效性。")
        elif full_text.startswith("/"):
             pass
        else:
            await message.answer("⚠️ 请发送有效的 115 分享链接。\n支持域名: 115.com, 115cdn.com, anxia.com")

    async def poll_pending_link(self, message: types.Message, pending_info: dict):
        """Poll the status of a pending link and process it when ready"""
        share_url = pending_info["share_url"]
        metadata = pending_info.get("metadata", {})
        max_attempts = 36  # 3 hours (5 mins * 36)
        interval = 300   # 5 minutes
        
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(interval)
            
            logger.info(f"🔄 正在进行第 {attempt}/{max_attempts} 次审核状态检查: {share_url}")
            status_info = await p115_service.get_share_status(share_url)
            
            if status_info is None:
                logger.warning(f"⚠️ 无法获取检查状态，将在下次重试: {share_url}")
                continue
            
            if status_info["is_prohibited"]:
                logger.warning(f"🚫 轮询检测到链接包含违规内容: {share_url}")
                await message.reply(f"🚫 链接审核未通过：检测到违规内容，无法继续处理。\n链接: {share_url}")
                await self._delete_pending_task(pending_info.get("db_id"))
                return
                
            if status_info["is_expired"]:
                logger.warning(f"⏰ 轮询检测到链接已过期: {share_url}")
                await message.reply(f"⏰ 链接已失效：在审核期间该分享已过期。\n链接: {share_url}")
                await self._delete_pending_task(pending_info.get("db_id"))
                return

            if not status_info["is_auditing"]:  # Audit passed
                logger.info(f"🎉 链接审核已通过 (status: {status_info['share_state']}): {share_url}")
                save_res = await p115_service.save_share_link(share_url, metadata=metadata)
                
                if save_res and save_res.get("status") == "success":
                    logger.info(f"✅ 审核通过后转存成功: {share_url}")
                    share_link = await p115_service.create_share_link(save_res)
                    
                    if share_link:
                        await p115_service.save_history_link(share_url, share_link)
                        await self.broadcast_to_channels(share_link, metadata)
                        
                        success_text = f"✅ 审核已通过！链接处理完成。\n原链接: {share_url}\n新分享: {share_link}"
                        await message.reply(success_text)
                        
                        if settings.TG_USER_ID and str(message.chat.id) != str(settings.TG_USER_ID):
                            try:
                                await self.bot.send_message(settings.TG_USER_ID, f"🔔 [后台任务] {success_text}")
                            except Exception:
                                pass
                    await self._delete_pending_task(pending_info.get("db_id"))
                    return 
                else:
                    logger.error(f"❌ 审核通过后转存仍然失败: {share_url}")
                    await message.reply(f"❌ 链接审核已通过，但自动转存失败，请手动尝试: {share_url}")
                    await self._delete_pending_task(pending_info.get("db_id"))
                    return
        
        logger.warning(f"⏰ 链接审核轮询超时 (3小时): {share_url}")
        await message.reply(f"⏰ 链接审核轮询超时 (已持续 3 小时)，请稍后手动检查: {share_url}")
        await self._delete_pending_task(pending_info.get("db_id"))

    def _get_utf16_len(self, text: str) -> int:
        """Calculate length in UTF-16 code units"""
        return len(text.encode('utf-16-le')) // 2

    def _update_access_codes(self, text: str, entities: list, share_link: str) -> tuple[str, list]:
        """Update access codes in text to match the new link"""
        from urllib.parse import urlparse, parse_qs
        import re
        
        parsed = urlparse(share_link)
        params = parse_qs(parsed.query)
        new_pwd = params.get("password", [""])[0]
        
        if not new_pwd:
            return text, entities

        patterns = [
            r'((?:访问码|提取码|密码)(?:：|:|%EF%BC%9A|%3A)\s*)([a-zA-Z0-9]{4})',
            r'((?:%E8%AE%BF%E9%97%AE%E7%A0%81|%E6%8F%90%E5%8F%96%E7%A0%81|%E5%AF%86%E7%A0%81)(?:%EF%BC%9A|%3A)(?:%20)*)([a-zA-Z0-9]{4})'
        ]
        
        current_text = text
        current_entities = entities
        
        for pattern in patterns:
            while True:
                match = re.search(pattern, current_text, flags=re.IGNORECASE)
                if not match:
                    break
                
                prefix, old_code = match.groups()
                if old_code == new_pwd:
                    break 

                old_str = f"{prefix}{old_code}"
                new_str = f"{prefix}{new_pwd}"
                current_text, current_entities = self._replace_text_and_adjust_entities(
                    current_text, current_entities, old_str, new_str
                )
        return current_text, current_entities

    def _replace_text_and_adjust_entities(self, text: str, entities: list, old_str: str, new_str: str):
        """Helper to replace text and shift entity offsets/lengths accordingly"""
        has_text_match = old_str in text
        
        if has_text_match:
            start_pos_char = text.find(old_str)
            end_pos_char = start_pos_char + len(old_str)
            
            start_pos_u16 = self._get_utf16_len(text[:start_pos_char])
            old_len_u16 = self._get_utf16_len(old_str)
            end_pos_u16 = start_pos_u16 + old_len_u16
            new_len_u16 = self._get_utf16_len(new_str)
            diff_u16 = new_len_u16 - old_len_u16
            
            new_text = text[:start_pos_char] + new_str + text[end_pos_char:]
        else:
            new_text = text
            start_pos_u16 = -1
            end_pos_u16 = -1
            old_len_u16 = self._get_utf16_len(old_str) 
            new_len_u16 = self._get_utf16_len(new_str)
            diff_u16 = new_len_u16 - old_len_u16

        new_entities = []
        if entities:
            from aiogram.types import MessageEntity
            for entity in entities:
                is_dict = isinstance(entity, dict)
                e_offset = entity.get("offset") if is_dict else entity.offset
                e_length = entity.get("length") if is_dict else entity.length
                e_url = (entity.get("url") if is_dict else getattr(entity, "url", None))
                e_type = entity.get("type") if is_dict else entity.type
                
                if has_text_match:
                    if e_offset >= end_pos_u16:
                        e_offset += diff_u16
                    elif e_offset <= start_pos_u16 and (e_offset + e_length) >= end_pos_u16:
                        e_length += diff_u16
                    elif e_offset == start_pos_u16 and e_length == old_len_u16:
                        e_length = new_len_u16
                
                if e_url == old_str:
                    e_url = new_str

                new_entities.append(MessageEntity(
                    type=e_type,
                    offset=e_offset,
                    length=e_length,
                    url=e_url,
                    user=entity.get("user") if is_dict else getattr(entity, "user", None),
                    language=entity.get("language") if is_dict else getattr(entity, "language", None),
                    custom_emoji_id=entity.get("custom_emoji_id") if is_dict else getattr(entity, "custom_emoji_id", None)
                ))
        return new_text, new_entities

    async def broadcast_to_channels(self, share_link: str, metadata: dict):
        """Broadcast processed link to all configured and enabled channels"""
        import json
        channels = []
        try:
            channels = json.loads(settings.TG_CHANNELS)
        except Exception:
            pass
            
        legacy_id = settings.TG_CHANNEL_ID
        if legacy_id and not any(c.get("id") == str(legacy_id) for c in channels):
            channels.append({"id": str(legacy_id), "enabled": True, "concise": False})
            
        enabled_channels = [c for c in channels if c.get("enabled")]
        
        if not enabled_channels:
            logger.debug("没有已配置或已启用的频道，跳过广播")
            return
            
        for chan in enabled_channels:
            await self._post_to_single_channel(chan, share_link, metadata)

    async def _post_to_single_channel(self, channel_config: dict, share_link: str, metadata: dict):
        """Helper to post to a single channel based on its configuration"""
        channel_id = channel_config.get("id")
        is_concise = channel_config.get("concise", False)
        
        if not channel_id:
            return
            
        full_text = metadata.get("full_text", "")
        photo_id = metadata.get("photo_id")
        share_url = metadata.get("share_url", "")
        entities_raw = metadata.get("entities", [])
        
        from aiogram.types import MessageEntity
        entities = []
        for e in entities_raw:
            if isinstance(e, dict):
                try:
                    entities.append(MessageEntity(**e))
                except Exception:
                    pass
            else:
                entities.append(e)

        try:
            if is_concise:
                new_text = f"✅ 处理成功！\n长期分享链接：\n{share_link}"
                new_entities = None
            else:
                if share_url:
                    new_text, new_entities = self._replace_text_and_adjust_entities(
                        full_text, entities, share_url, share_link
                    )
                    new_text, new_entities = self._update_access_codes(new_text, new_entities, share_link)
                else:
                    new_text = f"✅ 自动转存成功\n\n{full_text}\n\n🔗 长期有效链接: {share_link}"
                    new_entities = None

            if photo_id and not is_concise:
                max_len_utf16 = 1024
                current_len_utf16 = self._get_utf16_len(new_text)
                if current_len_utf16 > max_len_utf16:
                    new_text_encoded = new_text.encode('utf-16-le')
                    new_text = new_text_encoded[:max_len_utf16 * 2].decode('utf-16-le', errors='ignore')
                    if new_entities:
                        final_len_utf16 = self._get_utf16_len(new_text)
                        valid_entities = []
                        for e in new_entities:
                            if e.offset < final_len_utf16:
                                if e.offset + e.length > final_len_utf16:
                                    e.length = final_len_utf16 - e.offset
                                valid_entities.append(e)
                        new_entities = valid_entities

                await self.bot.send_photo(
                    channel_id, 
                    photo=photo_id, 
                    caption=new_text,
                    caption_entities=new_entities
                )
            else:
                await self.bot.send_message(
                    channel_id, 
                    text=new_text,
                    entities=new_entities,
                    disable_web_page_preview=False
                )
            logger.info(f"已将推送发送至频道: {channel_id} (简洁: {is_concise})")
        except Exception as e:
            logger.error(f"Failed to post to channel {channel_id}: {e}")

    async def _delete_pending_task(self, db_id: int):
        if db_id:
            from app.core.database import async_session
            from app.models.schema import PendingLink
            from sqlalchemy import delete
            async with async_session() as session:
                await session.execute(delete(PendingLink).where(PendingLink.id == db_id))
                await session.commit()

    async def recover_pending_tasks(self):
        from app.core.database import async_session
        from app.models.schema import PendingLink
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(select(PendingLink).where(PendingLink.status == "auditing"))
            tasks = result.scalars().all()
            if tasks:
                for task in tasks:
                    pending_info = {"share_url": task.share_url, "metadata": task.metadata_json, "db_id": task.id}
                    asyncio.create_task(self._recovered_poll(pending_info))

    async def _recovered_poll(self, pending_info: dict):
        class MockMessage:
            def __init__(self, bot, user_id):
                self.bot = bot
                self.chat = type('obj', (object,), {'id': user_id})
            async def reply(self, text):
                try: await self.bot.send_message(self.chat.id, text)
                except Exception: pass
        user_id = settings.TG_USER_ID or "0"
        mock_msg = MockMessage(self.bot, user_id)
        await self.poll_pending_link(mock_msg, pending_info)

    async def verify_connection(self) -> bool:
        if not self.bot:
            self.is_connected = False
            return False
        try:
            me = await self.bot.get_me()
            if me:
                self.is_connected = True
                logger.info(f"✅ Telegram Bot 连接验证成功: @{me.username}")
                return True
        except Exception as e:
            self.is_connected = False
            return False
        self.is_connected = False
        return False

    async def start_polling(self):
        if self.dp and self.bot:
            self._current_polling_id += 1
            try:
                await self.dp.start_polling(self.bot, skip_updates=True, handle_signals=False)
            except Exception as e:
                logger.error(f"Polling error: {e}")

    async def stop_polling(self):
        if self.dp:
            try: await self.dp.stop_polling()
            except Exception: pass
        if self.polling_task and not self.polling_task.done():
            try: await asyncio.wait_for(asyncio.shield(self.polling_task), timeout=3.0)
            except asyncio.TimeoutError:
                self.polling_task.cancel()
                try: await self.polling_task
                except: pass
            self.polling_task = None

    async def restart_polling(self):
        async with self._lock:
            await self.stop_polling()
            await self._cleanup_bot()
            await asyncio.sleep(5)
            if not settings.TG_BOT_TOKEN: return
            self.init_bot(settings.TG_BOT_TOKEN)
            if not self.bot: return
            try: await self.bot.delete_webhook(drop_pending_updates=True)
            except: pass
            await asyncio.sleep(2)
            self.polling_task = asyncio.create_task(self.start_polling())

    async def test_send_to_user(self):
        if not self.bot or not settings.TG_USER_ID: return False, "未配置"
        try:
            await self.bot.send_message(settings.TG_USER_ID, "🔔 测试成功")
            return True, "成功"
        except Exception as e: return False, str(e)

    async def test_send_to_channel(self, channel_id: str = None):
        target_id = channel_id or settings.TG_CHANNEL_ID
        if not self.bot or not target_id: return False, "未配置"
        try:
            await self.bot.send_message(target_id, "📢 测试成功")
            return True, "成功"
        except Exception as e: return False, str(e)

tg_service = TGService()
