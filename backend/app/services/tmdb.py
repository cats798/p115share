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
            
    # ========== 新增方法用于智能重命名 ==========
    async def get_episode_details(self, tv_id: int, season: int, episode: int) -> Optional[Dict]:
        """获取单集详细信息（用于获取剧集标题）"""
        if not self.api_key:
            return None
        params = {'api_key': self.api_key, 'language': 'zh-CN'}
        url = f"{self.BASE_URL}/tv/{tv_id}/season/{season}/episode/{episode}"
        try:
            data = await self._request('GET', url, params=params)
            return data
        except Exception as e:
            logger.error(f"获取剧集详情失败: {e}")
            return None

    async def search_tv(self, query: str, year: Optional[int] = None) -> Optional[Dict]:
        """专门搜索剧集"""
        if not self.api_key:
            return None
        params = {
            'api_key': self.api_key,
            'query': query,
            'language': 'zh-CN'
        }
        if year:
            params['first_air_date_year'] = year
        url = f"{self.BASE_URL}/search/tv"
        try:
            data = await self._request('GET', url, params=params)
            if data.get('results'):
                return data['results'][0]
        except Exception as e:
            logger.error(f"TMDB TV search error: {e}")
        return None

    async def search_movie(self, query: str, year: Optional[int] = None) -> Optional[Dict]:
        """专门搜索电影"""
        if not self.api_key:
            return None
        params = {
            'api_key': self.api_key,
            'query': query,
            'language': 'zh-CN'
        }
        if year:
            params['year'] = year
        url = f"{self.BASE_URL}/search/movie"
        try:
            data = await self._request('GET', url, params=params)
            if data.get('results'):
                return data['results'][0]
        except Exception as e:
            logger.error(f"TMDB movie search error: {e}")
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
        """从文本中提取 TMDB ID，支持格式：tmdb-12345, {tmdb-12345}, [tmdbid=12345], tmdb=12345"""
        patterns = [
            r'tmdb[-\s]?(\d+)',
            r'\{tmdb-(\d+)\}',
            r'\[tmdbid=(\d+)\]',
            r'tmdbid[:\s]?(\d+)',
            r'tmdb=(\d+)',
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def extract_year(self, text: str) -> Optional[int]:
        """从文本中提取年份，支持 (2024), [2024], 2024 等格式"""
        match = re.search(r'(?:^|\D)(\d{4})(?:\D|$)', text)
        if match:
            return int(match.group(1))
        return None

    def extract_season_episode(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        """从文本中提取季数和集数，支持复杂格式
           如：S05E158, S01E03, 第 168 集, 第 3 集, Season 5 Episode 158
        """
        season = None
        episode = None
        
        # 匹配 S05E158 格式（标准格式，优先使用）
        match = re.search(r'S(\d{1,3})E(\d{1,4})', text, re.IGNORECASE)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            logger.debug(f"从 SxxExx 格式提取: 季 {season}, 集 {episode}")
            return season, episode
        
        # 匹配 S05 或 Season 5 格式（只有季数）
        match = re.search(r'S(\d{1,3})|Season[.\s]*(\d{1,3})', text, re.IGNORECASE)
        if match:
            season = int(match.group(1) or match.group(2))
        
        # 匹配 第 168 集 格式（中文）
        match = re.search(r'第\s*(\d+)\s*[集]', text)
        if match:
            episode = int(match.group(1))
            logger.debug(f"从中文格式提取: 集 {episode}")
        
        # 如果没有找到季数但有集数，默认季为1
        if episode is not None and season is None:
            season = 1
            logger.debug(f"未找到季数，使用默认季 1")
        
        return season, episode

    def extract_resolution(self, text: str) -> Optional[str]:
        """从文本中提取分辨率"""
        # 按优先级排序，优先匹配更精确的格式
        resolutions = [
            (r'4K|2160p', '2160p'),
            (r'1080p|1080P', '1080p'),
            (r'1080i', '1080i'),
            (r'720p', '720p'),
            (r'480p', '480p'),
        ]
        for pattern, value in resolutions:
            if re.search(pattern, text, re.IGNORECASE):
                return value
        return None

    def extract_video_codec(self, text: str) -> Optional[str]:
        """从文本中提取视频编码"""
        codecs = [
            (r'H\.265|H265|HEVC', 'H.265'),
            (r'H\.264|H264|AVC', 'H.264'),
            (r'XviD', 'XviD'),
            (r'DivX', 'DivX'),
            (r'VP9', 'VP9'),
            (r'AV1', 'AV1'),
        ]
        for pattern, value in codecs:
            if re.search(pattern, text, re.IGNORECASE):
                return value
        return None

    def extract_audio_codec(self, text: str) -> Optional[str]:
        """从文本中提取音频编码"""
        codecs = [
            (r'AAC', 'AAC'),
            (r'AC3|DDP|Dolby\s*Digital', 'AC3'),
            (r'DTS', 'DTS'),
            (r'FLAC', 'FLAC'),
            (r'MP3', 'MP3'),
        ]
        for pattern, value in codecs:
            if re.search(pattern, text, re.IGNORECASE):
                return value
        return None

    def extract_source(self, text: str) -> Optional[str]:
        """从文本中提取片源类型"""
        sources = [
            (r'WEB-?DL', 'WEB-DL'),
            (r'WEB-?Rip', 'WEBRip'),
            (r'Blu-?Ray|BDRip', 'BluRay'),
            (r'HDTV', 'HDTV'),
            (r'DVD', 'DVD'),
            (r'REMUX', 'REMUX'),
        ]
        for pattern, value in sources:
            if re.search(pattern, text, re.IGNORECASE):
                return value
        return None

    def clean_title(self, raw_title: str) -> str:
        """去除常见前缀、后缀，移除年份、TMDB ID、剧集信息等，返回干净标题"""
        # 移除开头的表情符号和常见前缀
        raw_title = re.sub(r'^[\U0001F300-\U0001F9FF\s]+', '', raw_title)
        raw_title = re.sub(r'^[🎬🎥🎞️📀📁]\s*标题[：:]\s*', '', raw_title)
        
        # 移除年份（如 (2024)、[2024]、2024）
        raw_title = re.sub(r'\s*[\(\[]?\d{4}[\)\]]?\s*', '', raw_title)
        
        # 移除 TMDB ID 标记（如 {tmdb-12345}, [tmdbid=12345], tmdb-12345）
        raw_title = re.sub(r'\s*(?:[\(\{\[]?\s*(?:tmdb|id)[\s\-=]?\d+\s*[\)\}\]]?)', '', raw_title, flags=re.IGNORECASE)
        
        # 移除剧集信息，如 S05E158, S01E03, 第 168 集, Season 5 等
        raw_title = re.sub(r'\s*(?:S\d+E\d+|S\d+|第\s*\d+\s*[集]|Season\s*\d+)\s*', '', raw_title, flags=re.IGNORECASE)
        
        # 移除常见的视频格式信息
        raw_title = re.sub(r'\s*(?:1080[pi]|2160p|4K|WEB-?DL|WEB-?Rip|HDTV|HDR|DV|FLAC|DDP|AAC|H\.?265|H\.?264|REMUX|BluRay|LINE\s*TV)', '', raw_title, flags=re.IGNORECASE)
        
        # 去除多余空格和标点
        raw_title = re.sub(r'[.\-_]+$', '', raw_title)
        raw_title = re.sub(r'^\s+|\s+$', '', raw_title)
        
        # 将连续的点替换为单个点
        raw_title = re.sub(r'\.+', '.', raw_title)
        
        return raw_title

    def parse_title_year(self, raw_title: str) -> Tuple[str, Optional[int]]:
        """从原始标题中提取标题和年份，返回干净标题和年份"""
        year = self.extract_year(raw_title)
        clean = self.clean_title(raw_title)
        return clean, year

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

    def generate_new_name(self, rule: Dict, media_info: Dict, original_filename: str = None) -> str:
        """根据重命名模板和原始文件名生成新文件名
           格式如：恋爱等高线.2026.S01E03.1080p.H264.AAC
           或：恋爱等高线.2026.S05E158.1080p.H264.AAC
        """
        media_type = media_info.get('media_type')
        
        # 获取并清理标题
        title = media_info.get('title') or media_info.get('name') or ''
        # 移除标题中的特殊字符
        title = re.sub(r'[<>:"/\\|?*]', '', title)
        # 将空格替换为点
        title = title.replace(' ', '.')
        # 移除连续的点
        title = re.sub(r'\.+', '.', title)
        
        # 获取年份
        year = media_info.get('release_date') or media_info.get('first_air_date') or ''
        if year:
            year = year[:4]
        
        # 构建文件名各部分
        parts = [title]
        if year:
            parts.append(year)
        
        # 如果是剧集，尝试从原始文件名中提取季数和集数
        if media_type == 'tv' and original_filename:
            season, episode = self.extract_season_episode(original_filename)
            if season is not None and episode is not None:
                parts.append(f"S{season:02d}E{episode:02d}")
                logger.debug(f"添加剧集信息: S{season:02d}E{episode:02d}")
        
        # 从原始文件名中提取技术参数
        source = self.extract_source(original_filename) if original_filename else None
        resolution = self.extract_resolution(original_filename) if original_filename else None
        video_codec = self.extract_video_codec(original_filename) if original_filename else None
        audio_codec = self.extract_audio_codec(original_filename) if original_filename else None
        
        # 添加技术参数（按特定顺序）
        if source:
            parts.append(source)
        if resolution:
            parts.append(resolution)
        if video_codec:
            # 标准化视频编码名称
            if video_codec == 'H.265':
                parts.append('H.265')
            elif video_codec == 'H.264':
                parts.append('H.264')
            else:
                parts.append(video_codec)
        if audio_codec:
            parts.append(audio_codec)
        
        # 用点连接所有部分
        new_name = '.'.join(parts)
        
        # 保留原始文件扩展名
        if original_filename:
            ext_match = re.search(r'\.([a-zA-Z0-9]+)$', original_filename)
            if ext_match:
                ext = ext_match.group(1)
                new_name = f"{new_name}.{ext}"
        
        logger.info(f"生成新文件名: {new_name}")
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