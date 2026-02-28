# app/services/tmdb.py (完整文件，包含所有最新修改)

import aiohttp
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
from app.core.config import settings
from aiohttp_socks import ProxyConnector

class TMDBClient:
    """TMDB API 客户端，支持代理"""
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.TMDB_API_KEY
        self.session = None
        self._proxy = None
        self._connector = None

    async def _get_session(self):
        if self.session is None:
            # 配置代理
            if settings.PROXY_ENABLED and settings.PROXY_HOST and settings.PROXY_PORT:
                proxy_type = settings.PROXY_TYPE.lower()
                auth = f"{settings.PROXY_USER}:{settings.PROXY_PASS}@" if settings.PROXY_USER and settings.PROXY_PASS else ""
                proxy_url = f"{proxy_type}://{auth}{settings.PROXY_HOST}:{settings.PROXY_PORT}"
                
                if proxy_type == 'socks5':
                    self._connector = ProxyConnector.from_url(proxy_url)
                else:
                    self._proxy = proxy_url  # HTTP/HTTPS 代理
            self.session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()
        if self._connector:
            await self._connector.close()

    async def _request(self, method: str, url: str, **kwargs):
        """执行请求，自动处理代理"""
        session = await self._get_session()
        # 如果有 HTTP 代理，在请求时指定 proxy
        if self._proxy and method.lower() == 'get':
            kwargs['proxy'] = self._proxy
        async with session.request(method, url, **kwargs) as resp:
            return await resp.json()

    async def search_multi(self, query: str, year: Optional[int] = None) -> Optional[Dict]:
        """搜索多种类型（电影/剧集）"""
        if not self.api_key:
            return None
        params = {
            'api_key': self.api_key,
            'query': query,
            'language': 'zh-CN'
        }
        if year:
            params['year'] = year
        url = f"{self.BASE_URL}/search/multi"
        try:
            data = await self._request('GET', url, params=params)
            if data.get('results'):
                return data['results'][0]  # 取第一个结果
        except Exception as e:
            logger.error(f"TMDB search error: {e}")
        return None

    async def get_details(self, media_type: str, tmdb_id: int) -> Optional[Dict]:
        """获取详细信息（用于体裁、国家等）"""
        if not self.api_key:
            return None
        params = {'api_key': self.api_key, 'language': 'zh-CN'}
        url = f"{self.BASE_URL}/{media_type}/{tmdb_id}"
        try:
            return await self._request('GET', url, params=params)
        except Exception as e:
            logger.error(f"TMDB details error: {e}")
            return None


class MediaOrganizer:
    """媒体整理引擎，基于 TMDB 数据和规则配置"""

    def __init__(self, config_json: str = None):
        self.rules = []
        if config_json:
            try:
                config = json.loads(config_json)
                rules_list = config.get('tmdbDirectoryConfig', {}).values()
                self.rules = sorted(rules_list, key=lambda x: x.get('priority', 999))
            except Exception as e:
                logger.error(f"Failed to load TMDB config: {e}")

    def extract_tmdb_id(self, text: str) -> Optional[int]:
        """从文本中提取 TMDB ID，支持格式：tmdb-12345, {tmdb-12345}, [tmdbid=12345]"""
        patterns = [
            r'tmdb[-\s]?(\d+)',
            r'\{tmdb-(\d+)\}',
            r'\[tmdbid=(\d+)\]',
            r'tmdbid[:\s]?(\d+)',
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def clean_title(self, raw_title: str) -> str:
        """去除常见前缀，如 '🎬 标题：', '🎬 标题:' 等，并去除首尾空格"""
        # 移除常见前缀（支持中英文冒号）
        raw_title = re.sub(r'^[🎬🎥🎞️📀📁]\s*标题[：:]\s*', '', raw_title)
        # 移除其他可能的符号前缀（如表情符号）
        raw_title = re.sub(r'^[\U0001F300-\U0001F9FF\s]+', '', raw_title)
        return raw_title.strip()

    def parse_title_year(self, raw_title: str) -> Tuple[str, Optional[int]]:
        """从原始标题中提取标题和年份，格式如 '神探科莫兰 (2017)' 或 '神探科莫兰 2017'"""
        # 先清洗标题
        cleaned = self.clean_title(raw_title)
        
        # 尝试匹配括号内的年份
        match = re.search(r'(.+?)\s*[\(（](\d{4})[\)）]', cleaned)
        if match:
            title = match.group(1).strip()
            year = int(match.group(2))
            return title, year
        
        # 尝试匹配空格后的年份
        match = re.search(r'(.+?)\s+(\d{4})$', cleaned)
        if match:
            title = match.group(1).strip()
            year = int(match.group(2))
            return title, year
        
        # 无年份
        return cleaned, None

    def match_rule(self, media_info: Dict) -> Optional[Dict]:
        """根据媒体信息匹配规则"""
        media_type = 'movie' if media_info.get('media_type') == 'movie' else 'tv'
        genre_ids = media_info.get('genre_ids', [])
        for rule in self.rules:
            # 检查 media_type 是否匹配
            if media_type not in rule.get('media_types', []):
                continue
            conditions = rule.get('conditions', {})
            # 检查体裁条件
            genre_cond = conditions.get('genre_ids')
            if genre_cond and not self._check_genre(genre_ids, genre_cond):
                continue
            # 检查国家条件（如果 media_info 中有生产国家）
            countries = media_info.get('production_countries', [])
            country_codes = [c.get('iso_3166_1') for c in countries if c.get('iso_3166_1')]
            country_cond = conditions.get('production_countries')
            if country_cond and not self._check_countries(country_codes, country_cond):
                continue
            return rule
        return None

    def _check_genre(self, genre_ids: List[int], condition: str) -> bool:
        """处理包含排除的条件字符串，如 '16,!10762'"""
        parts = condition.split(',')
        for part in parts:
            part = part.strip()
            exclude = part.startswith('!')
            if exclude:
                part = part[1:]
            try:
                gid = int(part)
            except:
                continue
            if exclude and gid in genre_ids:
                return False
            if not exclude and gid not in genre_ids:
                return False
        return True

    def _check_countries(self, country_codes: List[str], condition: str) -> bool:
        """处理国家条件，例如 'CN,TW,HK'"""
        allowed = [c.strip() for c in condition.split(',')]
        # 只要有一个匹配就通过
        return any(code in allowed for code in country_codes)

    def generate_new_name(self, rule: Dict, media_info: Dict) -> str:
        """根据重命名模板生成新文件名"""
        template_key = 'movie' if media_info.get('media_type') == 'movie' else 'tv'
        template_name = rule.get('rename_templates', {}).get(template_key, 'movie_detailed')
        title = media_info.get('title') or media_info.get('name') or ''
        year = media_info.get('release_date') or media_info.get('first_air_date') or ''
        if year:
            year = year[:4]
        tmdb_id = media_info.get('id')
        # 简单模板示例，可根据需要扩展
        if template_name == 'movie_detailed':
            new_name = f"{title} ({year}) [tmdbid={tmdb_id}]"
        elif template_name == 'tv_detailed':
            new_name = f"{title} ({year}) [tmdbid={tmdb_id}]"
        else:
            new_name = title
        # 移除非法字符（文件名中禁止的字符）
        new_name = re.sub(r'[<>:"/\\|?*]', '', new_name)
        return new_name

    def get_target_path(self, rule: Dict) -> str:
        """获取目标路径，基于整理根目录"""
        path = rule.get('path', '').strip()
        # 确定基础目录：如果设置了整理根目录，则使用它；否则使用保存目录
        base = settings.P115_ORGANIZE_BASE_DIR.strip()
        if not base:
            base = settings.P115_SAVE_DIR or '/分享保存'
        # 如果 path 是绝对路径，直接返回（这种情况较少，但保留）
        if path.startswith('/'):
            return path
        # 相对路径，拼接
        return base.rstrip('/') + '/' + path.lstrip('/')