from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from app.core.config import settings
from app.services.p115 import p115_service
from loguru import logger
import asyncio
import re

class TGService:
    def __init__(self):
        self.bot = None
        self.dp = None
        if settings.TG_BOT_TOKEN:
            self.init_bot(settings.TG_BOT_TOKEN)

    def init_bot(self, token: str):
        try:
            self.bot = Bot(token=token)
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
            
            # 1. Save link with metadata
            metadata = {
                "description": description,
                "full_text": full_text,
                "photo_id": photo.file_id if photo else None
            }
            save_res = await p115_service.save_share_link(share_url, metadata=metadata)
            
            if save_res:
                await status_msg.edit_text("✅ 链接转存成功，正在为您生成长期分享链接 (预计等待 10 秒)...")
                
                # 2. Create long-term share
                share_link = await p115_service.create_share_link(save_res)
                
                # 3. Post to channel with rich format
                if settings.TG_CHANNEL_ID and share_link:
                    try:
                        # Rebuild entities to replace old link URL with new share link
                        # Keep the display text but update the URL in text_link entities
                        new_entities = []
                        for entity in entities:
                            if entity.type == "text_link" and hasattr(entity, 'url'):
                                # Check if this entity points to a 115 link
                                if re.match(link_pattern, entity.url):
                                    # Create new entity with updated URL
                                    from aiogram.types import MessageEntity
                                    new_entity = MessageEntity(
                                        type="text_link",
                                        offset=entity.offset,
                                        length=entity.length,
                                        url=share_link  # Replace with new share link
                                    )
                                    new_entities.append(new_entity)
                                    logger.debug(f"🔄 更新超链接实体: '{full_text[entity.offset:entity.offset+entity.length]}' -> {share_link}")
                                else:
                                    new_entities.append(entity)
                            else:
                                # Keep other entities as-is (bold, hashtag, etc.)
                                new_entities.append(entity)
                        
                        if photo:
                            # Send photo with caption and entities
                            await self.bot.send_photo(
                                settings.TG_CHANNEL_ID,
                                photo=photo.file_id,
                                caption=full_text,  # Keep original text
                                caption_entities=new_entities  # Use rebuilt entities
                            )
                            logger.info(f"📸 已转发图片消息到频道")
                        else:
                            # Send text message with entities
                            await self.bot.send_message(
                                settings.TG_CHANNEL_ID,
                                text=full_text,  # Keep original text
                                entities=new_entities  # Use rebuilt entities
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
            else:
                await status_msg.edit_text("❌ 保存链接失败，请检查 Cookie 或链接有效性。")
        elif full_text.startswith("/"):
             # Unknown command handled by default or ignored
             pass
        else:
            await message.answer("⚠️ 请发送有效的 115 分享链接。\n支持域名: 115.com, 115cdn.com, anxia.com")

    async def start_polling(self):
        if self.dp and self.bot:
            logger.info("Starting Telegram Bot polling...")
            await self.dp.start_polling(self.bot)

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
