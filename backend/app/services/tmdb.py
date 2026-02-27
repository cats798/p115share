import aiohttp
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
from app.core.config import settings

class TMDBClient:
    """TMDB API 客户端"""
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.TMDB_API_KEY
        self.session = None

    async def _get_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()

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
        session = await self._get_session()
        try:
            async with session.get(f"{self.BASE_URL}/search/multi", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        return data['results'][0]  # 取第一个结果
                else:
                    logger.warning(f"TMDB search failed: {resp.status}")
        except Exception as e:
            logger.error(f"TMDB search error: {e}")
        return None

    async def get_details(self, media_type: str, tmdb_id: int) -> Optional[Dict]:
        """获取详细信息（用于体裁、国家等）"""
        if not self.api_key:
            return None
        params = {'api_key': self.api_key, 'language': 'zh-CN'}
        session = await self._get_session()
        try:
            async with session.get(f"{self.BASE_URL}/{media_type}/{tmdb_id}", params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
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

    def parse_title_year(self, raw_title: str) -> Tuple[str, Optional[int]]:
        """从原始标题中提取标题和年份，格式如 '神探科莫兰 (2017)'"""
        match = re.search(r'(.+?)\s*\((\d{4})\)', raw_title)
        if match:
            title = match.group(1).strip()
            year = int(match.group(2))
            return title, year
        # 无年份，直接返回原字符串
        return raw_title.strip(), None

    def match_rule(self, media_info: Dict) -> Optional[Dict]:
        """根据媒体信息匹配规则"""
        media_type = 'movie' if media_info.get('media_type') == 'movie' else 'tv'
        genre_ids = media_info.get('genre_ids', [])
        # 获取 production_countries（可能来自详细信息，但 search 结果中没有）
        # 我们暂时只使用 search 结果中的信息，如果需要更详细的条件，需额外调用 details
        for rule in self.rules:
            # 检查 media_type 是否匹配
            if media_type not in rule.get('media_types', []):
                continue
            conditions = rule.get('conditions', {})
            # 检查体裁条件
            genre_cond = conditions.get('genre_ids')
            if genre_cond and not self._check_genre(genre_ids, genre_cond):
                continue
            # 检查国家条件（如果从 search 结果中无法获取，可能需要后续扩展）
            # 先跳过国家检查，或者假设用户会在 media_info 中提供
            # 暂时匹配成功
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
        """获取目标路径（可能为绝对路径或相对路径）"""
        path = rule.get('path', '').strip()
        if not path.startswith('/'):
            # 相对于保存根目录
            base = settings.P115_SAVE_DIR or '/分享保存'
            path = base.rstrip('/') + '/' + path.lstrip('/')
        return path