from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
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
        if settings.TG_BOT_TOKEN:
            self.init_bot(settings.TG_BOT_TOKEN)

    def init_bot(self, token: str):
        try:
            # Configure proxy if set
            session = None
            if settings.HTTP_PROXY or settings.HTTPS_PROXY:
                proxy = settings.HTTPS_PROXY or settings.HTTP_PROXY
                session = AiohttpSession(proxy=proxy)
                logger.info(f"Telegram Bot using proxy: {proxy}")
                
            self.bot = Bot(token=token, session=session)
            self.dp = Dispatcher()
            self._register_handlers()
            logger.info("Telegram Bot initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Telegram Bot: {e}")
            self.bot = None

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
            # text_link: [文字](URL) format
            # url: plain URL in text
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
            
            # Extract description (text before the link in full_text)
            description = ""
            if match:  # If found in text
                description = full_text[:match.start()].strip()
            else:  # If from entity, use all text except the link placeholder
                description = full_text.strip()
            
            logger.debug(f"📝 提取的描述: {description[:100]}...")
            
            status_msg = await message.answer("⌛️ 正在处理链接，请稍候...")
            
            # 0. Check history first
            history_share_link = await p115_service.get_history_link(share_url)
            if history_share_link:
                logger.info(f"✨ 发现历史记录，直接使用缓存链接: {share_url} -> {history_share_link}")
                await status_msg.edit_text("⚡ 发现历史记录，正在秒传...")
                
                # Replace link in text
                new_text, new_entities = self._replace_text_and_adjust_entities(
                    full_text, entities, share_url, history_share_link
                )
                
                # Update access codes
                new_text, new_entities = self._update_access_codes(new_text, new_entities, history_share_link)

                # Post to channel
                if settings.TG_CHANNEL_ID:
                    try:
                        if photo:
                            # Caption limit is 1024 UTF-16 code units
                            max_len_utf16 = 1024
                            current_len_utf16 = self._get_utf16_len(new_text)
                            if current_len_utf16 > max_len_utf16:
                                # UTF-16 aware truncation
                                new_text_encoded = new_text.encode('utf-16-le')
                                new_text = new_text_encoded[:max_len_utf16 * 2].decode('utf-16-le', errors='ignore')
                                
                                # Filter entities
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
                                settings.TG_CHANNEL_ID,
                                photo=photo_id,
                                caption=new_text,
                                caption_entities=new_entities
                            )
                        else:
                            await self.bot.send_message(
                                settings.TG_CHANNEL_ID,
                                text=new_text,
                                entities=new_entities
                            )
                        logger.info(f"⚡ 已使用历史链接转发到频道")
                    except Exception as e:
                        logger.error(f"Failed to post history link to channel: {e}", exc_info=True)

                await status_msg.edit_text(f"⚡ 秒传成功！(历史记录)\n长期分享链接：\n{history_share_link}")
                return
            
            # 1. Save link with metadata
            # Convert entities to dicts for JSON serialization in DB
            ser_entities = []
            if entities:
                for e in entities:
                    try:
                        ser_entities.append(e.model_dump())
                    except AttributeError:
                        # Fallback for older aiogram or if model_dump not available
                        ser_entities.append(dict(e))

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
                
                # 3. Post to channel with rich format
                if settings.TG_CHANNEL_ID and share_link:
                    try:
                        # Replace original link in text and adjust entities
                        new_text, new_entities = self._replace_text_and_adjust_entities(
                            full_text, entities, share_url, share_link
                        )
                        
                        # Update access codes in text if present
                        new_text, new_entities = self._update_access_codes(new_text, new_entities, share_link)
                        
                        if photo:
                            # Caption limit is 1024 UTF-16 code units
                            max_len_utf16 = 1024
                            current_len_utf16 = self._get_utf16_len(new_text)
                            if current_len_utf16 > max_len_utf16:
                                # UTF-16 aware truncation
                                new_text_encoded = new_text.encode('utf-16-le')
                                new_text = new_text_encoded[:max_len_utf16 * 2].decode('utf-16-le', errors='ignore')
                                
                                # Filter entities
                                if new_entities:
                                    final_len_utf16 = self._get_utf16_len(new_text)
                                    valid_entities = []
                                    for e in new_entities:
                                        if e.offset < final_len_utf16:
                                            if e.offset + e.length > final_len_utf16:
                                                e.length = final_len_utf16 - e.offset
                                            valid_entities.append(e)
                                    new_entities = valid_entities

                            # Send photo with caption and entities
                            await self.bot.send_photo(
                                settings.TG_CHANNEL_ID,
                                photo=photo.file_id,
                                caption=new_text,
                                caption_entities=new_entities
                            )
                            logger.info(f"📸 已转发图片消息到频道")
                        else:
                            # Send text message with entities
                            await self.bot.send_message(
                                settings.TG_CHANNEL_ID,
                                text=new_text,
                                entities=new_entities
                            )
                            logger.info(f"📝 已转发文本消息到频道")
                    except Exception as e:
                        logger.error(f"Failed to post to channel: {e}", exc_info=True)

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
             # Unknown command handled by default or ignored
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
            
            # 链接已被判定为违规或已过期
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

            if not status_info["is_auditing"]:  # Audit passed (presumably status 1)
                logger.info(f"🎉 链接审核已通过 (status: {status_info['share_state']}): {share_url}")
                # Try saving again
                save_res = await p115_service.save_share_link(share_url, metadata=metadata)
                
                if save_res and save_res.get("status") == "success":
                    logger.info(f"✅ 审核通过后转存成功: {share_url}")
                    # Create long-term share
                    share_link = await p115_service.create_share_link(save_res)
                    
                    if share_link:
                        # Save to history
                        await p115_service.save_history_link(share_url, share_link)

                        # Broadcast to channel
                        await self._post_to_channel(share_link, metadata)
                        
                        # Notify user
                        success_text = f"✅ 审核已通过！链接处理完成。\n原链接: {share_url}\n新分享: {share_link}"
                        await message.reply(success_text)
                        
                        if settings.TG_USER_ID and str(message.chat.id) != str(settings.TG_USER_ID):
                            try:
                                await self.bot.send_message(settings.TG_USER_ID, f"🔔 [后台任务] {success_text}")
                            except Exception:
                                pass
                    await self._delete_pending_task(pending_info.get("db_id"))
                    return  # Success, exit polling
                else:
                    logger.error(f"❌ 审核通过后转存仍然失败: {share_url}")
                    await message.reply(f"❌ 链接审核已通过，但自动转存失败，请手动尝试: {share_url}")
                    await self._delete_pending_task(pending_info.get("db_id"))
                    return
        
        logger.warning(f"⏰ 链接审核轮询超时 (3小时): {share_url}")
        await message.reply(f"⏰ 链接审核轮询超时 (已持续 3 小时)，请稍后手动检查: {share_url}")
        await self._delete_pending_task(pending_info.get("db_id"))

    async def _post_to_channel(self, share_link: str, metadata: dict):
        """Helper to post processed link to channel"""
        if not settings.TG_CHANNEL_ID:
            return
            
        full_text = metadata.get("full_text", "")
        photo_id = metadata.get("photo_id")
        share_url = metadata.get("share_url", "")
        entities = metadata.get("entities", [])
        
        try:
            # Replace link and adjust entities if possible
            if share_url:
                new_text, new_entities = self._replace_text_and_adjust_entities(
                    full_text, entities, share_url, share_link
                )
                
                # Update access codes in text if present
                new_text, new_entities = self._update_access_codes(new_text, new_entities, share_link)
            else:
                new_text = f"✅ 自动转存成功 (审核通过)\n\n{full_text}\n\n🔗 长期有效链接: {share_link}"
                new_entities = None
            
            if photo_id:
                # Caption limit is 1024 UTF-16 code units
                max_len_utf16 = 1024
                # Check if truncation is needed
                current_len_utf16 = self._get_utf16_len(new_text)
                if current_len_utf16 > max_len_utf16:
                    # Perform UTF-16 aware truncation
                    new_text_encoded = new_text.encode('utf-16-le')
                    # Each UTF-16 code unit is 2 bytes
                    new_text = new_text_encoded[:max_len_utf16 * 2].decode('utf-16-le', errors='ignore')
                    
                    # Filter entities that are now out of range
                    if new_entities:
                        final_len_utf16 = self._get_utf16_len(new_text)
                        valid_entities = []
                        for e in new_entities:
                            if e.offset < final_len_utf16:
                                # Adjust length if partially truncated
                                if e.offset + e.length > final_len_utf16:
                                    e.length = final_len_utf16 - e.offset
                                valid_entities.append(e)
                        new_entities = valid_entities

                await self.bot.send_photo(
                    settings.TG_CHANNEL_ID, 
                    photo=photo_id, 
                    caption=new_text,
                    caption_entities=new_entities
                )
            else:
                await self.bot.send_message(
                    settings.TG_CHANNEL_ID, 
                    text=new_text,
                    entities=new_entities
                )
            logger.info("已将轮询成功的链接发送到频道")
        except Exception as e:
            logger.error(f"Failed to post to channel in background task: {e}")

    def _get_utf16_len(self, text: str) -> int:
        """Calculate length in UTF-16 code units (as expected by Telegram)"""
        return len(text.encode('utf-16-le')) // 2

    def _update_access_codes(self, text: str, entities: list, share_link: str) -> tuple[str, list]:
        """Update access codes in text (including URL-encoded ones) to match the new link"""
        # 1. Extract new password from share link
        from urllib.parse import urlparse, parse_qs
        import re
        
        parsed = urlparse(share_link)
        params = parse_qs(parsed.query)
        new_pwd = params.get("password", [""])[0]
        
        if not new_pwd:
            return text, entities

        # 2. Define patterns
        # Group 1: Prefix (e.g. "访问码："), Group 2: The code (4 chars)
        patterns = [
            # Plain text: 访问码/提取码/密码 + : or ： + 4 chars
            r'((?:访问码|提取码|密码)(?:：|:|%EF%BC%9A|%3A)\s*)([a-zA-Z0-9]{4})',
            # URL encoded: %E8%AE%BF%E9%97%AE%E7%A0%81 = 访问码, etc.
            r'((?:%E8%AE%BF%E9%97%AE%E7%A0%81|%E6%8F%90%E5%8F%96%E7%A0%81|%E5%AF%86%E7%A0%81)(?:%EF%BC%9A|%3A)(?:%20)*)([a-zA-Z0-9]{4})'
        ]
        
        current_text = text
        current_entities = entities
        
        for pattern in patterns:
            # We use an iterator loop to handle multiple occurrences and shifting offsets
            while True:
                match = re.search(pattern, current_text, flags=re.IGNORECASE)
                if not match:
                    break
                
                prefix, old_code = match.groups()
                
                # If code is already correct, skip this match to avoid infinite loop
                # We move start pos check forward
                if old_code == new_pwd:
                    # Manually advance to avoid infinite loop if we don't replace
                    # Since python re.search doesn't support 'start from', we use a trick or just break if we assume unique usage
                    # (unlikely in one msg). Let's assume replace all occurrences if different.
                    
                    # Actually, if we have multiple "访问码：old" and "访问码：old", and we replace one, the next iteration catches the next.
                    # If we find "访问码：new", we should look for others? 
                    # Re.sub is risky with entities. We need separate `replace`.
                    
                    # If the found match is already new_pwd, we look for *other* matches
                    # But re.search finds the first. If the first is already correct, we might miss subsequent incorrect ones.
                    # Helper finding:
                    next_pos = match.end()
                    suffix = current_text[next_pos:]
                    sub_match = re.search(pattern, suffix, flags=re.IGNORECASE)
                    if sub_match:
                         # There is another match after this one, we might need to handle it.
                         # But `_replace_text_and_adjust_entities` replaces specific substrings.
                         # Let's simplify: replace specific substring "prefix+old_code" -> "prefix+new_pwd"
                         pass
                    break 

                old_str = f"{prefix}{old_code}"
                new_str = f"{prefix}{new_pwd}"
                
                # Use our entity-aware replacer
                # Note: this replaces ALL occurrences of old_str. 
                # This is generally desired.
                current_text, current_entities = self._replace_text_and_adjust_entities(
                    current_text, current_entities, old_str, new_str
                )
                
                # If we replaced, the text changed. Loop again to find other patterns or same pattern again if somehow multiple different old codes existed
                # (unlikely but safe to loop)
                
        return current_text, current_entities

    def _replace_text_and_adjust_entities(self, text: str, entities: list, old_str: str, new_str: str):
        """Helper to replace text and shift entity offsets/lengths accordingly using UTF-16 offsets"""
        has_text_match = old_str in text
        
        # We must calculate offsets in UTF-16 units for Telegram compatibility
        if has_text_match:
            start_pos_char = text.find(old_str)
            end_pos_char = start_pos_char + len(old_str)
            
            # UTF-16 offsets and lengths
            start_pos_u16 = self._get_utf16_len(text[:start_pos_char])
            old_len_u16 = self._get_utf16_len(old_str)
            end_pos_u16 = start_pos_u16 + old_len_u16
            new_len_u16 = self._get_utf16_len(new_str)
            diff_u16 = new_len_u16 - old_len_u16
            
            # New text
            new_text = text[:start_pos_char] + new_str + text[end_pos_char:]
        else:
            new_text = text
            start_pos_u16 = -1
            end_pos_u16 = -1
            old_len_u16 = self._get_utf16_len(old_str) 
            new_len_u16 = self._get_utf16_len(new_str)
            diff_u16 = new_len_u16 - old_len_u16

        # 2. Adjusted entities
        new_entities = []
        if entities:
            from aiogram.types import MessageEntity
            for entity in entities:
                # Handle both MessageEntity objects and dictionaries (from DB)
                is_dict = isinstance(entity, dict)
                e_offset = entity.get("offset") if is_dict else entity.offset
                e_length = entity.get("length") if is_dict else entity.length
                e_url = (entity.get("url") if is_dict else getattr(entity, "url", None))
                e_type = entity.get("type") if is_dict else entity.type
                
                # Update offset/length based on UTF-16 units
                if has_text_match:
                    if e_offset >= end_pos_u16:
                        # Entity starts after the replacement
                        e_offset += diff_u16
                    elif e_offset <= start_pos_u16 and (e_offset + e_length) >= end_pos_u16:
                        # Entity wraps the replacement
                        e_length += diff_u16
                    elif e_offset == start_pos_u16 and e_length == old_len_u16:
                        # Entity is the replacement string itself
                        e_length = new_len_u16
                
                # Update URL if it's the target link (Crucial for text_link)
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

    async def _delete_pending_task(self, db_id: int):
        """Delete task from DB"""
        if db_id:
            from app.core.database import async_session
            from app.models.schema import PendingLink
            from sqlalchemy import delete
            async with async_session() as session:
                await session.execute(delete(PendingLink).where(PendingLink.id == db_id))
                await session.commit()
                logger.debug(f"🗑 已从数据库删除任务 ID: {db_id}")

    async def recover_pending_tasks(self):
        """Recover pending polling tasks from DB on startup"""
        from app.core.database import async_session
        from app.models.schema import PendingLink
        from sqlalchemy import select
        
        async with async_session() as session:
            result = await session.execute(select(PendingLink).where(PendingLink.status == "auditing"))
            tasks = result.scalars().all()
            
            if tasks:
                logger.info(f"♻️ 发现 {len(tasks)} 个未完成的审核任务，正在恢复轮询...")
                for task in tasks:
                    pending_info = {
                        "share_url": task.share_url,
                        "metadata": task.metadata_json,
                        "db_id": task.id
                    }
                    asyncio.create_task(self._recovered_poll(pending_info))

    async def _recovered_poll(self, pending_info: dict):
        """Polling logic for recovered tasks (no original message object)"""
        class MockMessage:
            def __init__(self, bot, user_id):
                self.bot = bot
                self.chat = type('obj', (object,), {'id': user_id})
                
            async def reply(self, text):
                try:
                    await self.bot.send_message(self.chat.id, text)
                except Exception:
                    pass
        
        user_id = settings.TG_USER_ID or "0"
        mock_msg = MockMessage(self.bot, user_id)
        await self.poll_pending_link(mock_msg, pending_info)

    async def start_polling(self):
        if self.dp and self.bot:
            logger.info("Starting Telegram Bot polling...")
            await self.dp.start_polling(self.bot)

    async def stop_polling(self):
        """Stop current polling task"""
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                logger.info("✅ Telegram Bot polling stopped")
            self.polling_task = None

    async def restart_polling(self):
        """Restart polling with updated configuration"""
        await self.stop_polling()
        if self.bot and self.dp:
            self.polling_task = asyncio.create_task(self.start_polling())
            logger.info("🔄 Telegram Bot polling restarted")

    async def test_send_to_user(self):
        if not self.bot or not settings.TG_USER_ID:
            logger.warning("Bot or User ID not configured for test")
            return False, "机器人或用户 ID 未配置"
        try:
            await self.bot.send_message(settings.TG_USER_ID, "🔔 P115-Share 机器人测试通知成功！")
            logger.info(f"✅ 已向用户 {settings.TG_USER_ID} 发送测试消息")
            return True, "测试消息已模拟发送"
        except Exception as e:
            logger.error(f"❌ 向用户发送测试消息失败: {e}")
            return False, str(e)

    async def test_send_to_channel(self):
        if not self.bot or not settings.TG_CHANNEL_ID:
            logger.warning("Bot or Channel ID not configured for test")
            return False, "机器人或频道 ID 未配置"
        try:
            await self.bot.send_message(settings.TG_CHANNEL_ID, "📢 P115-Share 频道广播测试成功！")
            logger.info(f"✅ 已向频道 {settings.TG_CHANNEL_ID} 发送测试消息")
            return True, "测试消息已模拟发送"
        except Exception as e:
            logger.error(f"❌ 向频道发送测试消息失败: {e}")
            return False, str(e)

tg_service = TGService()
