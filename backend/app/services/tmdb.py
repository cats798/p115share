import aiohttp
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger
from app.core.config import settings
from aiohttp_socks import ProxyConnector
from enum import Enum
from collections import OrderedDict
from pathlib import Path

# ========== 媒体类型枚举 ==========
class MediaType(Enum):
    TV_SERIES = "tv_series"
    MOVIE = "movie"
    VARIETY_SHOW = "variety_show"
    DOCUMENTARY = "documentary"
    ANIME = "anime"
    UNKNOWN = "unknown"

# ========== 画质枚举 ==========
class QualityLevel(Enum):
    SD = "480p"
    HD = "720p"
    FHD = "1080p"
    UHD = "2160p"
    UNKNOWN = "unknown"

# ========== 解析后的媒体信息数据类 ==========
class ParsedMediaInfo:
    """从文件名解析出的媒体信息"""
    def __init__(self):
        self.title: str = ""
        self.year: Optional[int] = None
        self.season: Optional[int] = None
        self.episode: Optional[int] = None
        self.episode_title: str = ""
        self.base_episode: Optional[int] = None
        self.part_suffix: str = ""
        self.quality: QualityLevel = QualityLevel.UNKNOWN
        self.source: str = ""
        self.codec: str = ""
        self.audio: str = ""
        self.language: str = ""
        self.subtitle: str = ""
        self.group: str = ""
        self.extension: str = ""
        self.media_type: MediaType = MediaType.UNKNOWN
        self.original_filename: str = ""

# ========== 智能媒体分析器 ==========
class SmartMediaAnalyzer:
    """智能媒体分析器，处理各种规范和不规范的文件名"""

    def __init__(self):
        # 视频格式
        self.video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.rmvb'}

        # 画质标识
        self.quality_patterns = {
            r'2160p|4K|UHD': QualityLevel.UHD,
            r'1080p|FHD': QualityLevel.FHD,
            r'720p|HD': QualityLevel.HD,
            r'480p|SD': QualityLevel.SD
        }

        # 来源标识
        self.source_patterns = [
            'WEB-DL', 'WEBRip', 'BluRay', 'BDRip', 'DVDRip', 'HDTV', 'PDTV',
            'CAM', 'TS', 'TC', 'SCR', 'R5', 'DVDScr'
        ]

        # 编码标识
        self.codec_patterns = [
            'H264', 'H.264', 'x264', 'H265', 'H.265', 'x265', 'HEVC',
            'XviD', 'DivX', 'VP9', 'AV1'
        ]

        # 音频标识
        self.audio_patterns = [
            'AAC', 'AC3', 'DTS', 'DTS-HD', 'TrueHD', 'FLAC', 'MP3',
            'Atmos', 'DTS-X', '5.1', '7.1', '2.0'
        ]

        # 语言标识
        self.language_patterns = {
            'chinese': ['中文', '国语', '普通话', '粤语', 'Chinese', 'Mandarin', 'Cantonese'],
            'english': ['英语', 'English', 'ENG'],
            'japanese': ['日语', 'Japanese', 'JAP'],
            'korean': ['韩语', 'Korean', 'KOR']
        }

        # 字幕标识
        self.subtitle_patterns = [
            '中字', '英字', '双字', '内嵌', '外挂', 'SUB', 'DUB',
            '简体', '繁体', '中英', '多语'
        ]

        # 技术参数组合（用于清洗标题）- 增强版
        self.tech_patterns = [
            r'2160p|4K|UHD|1080p|720p|480p|HD|FHD|SD',
            r'WEB-?DL|WEBRip|BluRay|BDRip|HDTV|DVD|PDTV|CAM|TS|TC|SCR|R5',
            r'H\.?265|HEVC|H\.?264|AVC|XviD|DivX|VP9|AV1',
            r'AAC|AC3|DTS|DTS-?HD|TrueHD|FLAC|MP3|Atmos|DTS-?X',
            r'5\.1|7\.1|2\.0',
            r'60fps|30fps|24fps|HDR|SDR|DoVi|DV|HDR10|HDR10\+',
            r'REMUX|COMPLETE|FULL|REPACK|PROPER',
            r'第\d+[集期话]',
            r'tmdb[-\s]?\d+',
            r'\{[^}]+\}|\[[^\]]+\]|\([^)]+\)',
            r'S\d{1,2}E\d{1,3}',
            r'Season\s*\d+|Episode\s*\d+',
            r'WEB\s*DL|WEB\s*Rip',          # 额外匹配空格分隔的
            r'\bSDR\b|\bHDR\b|\bDV\b',
            r'\b10bit\b|\b8bit\b',
            r'\bHEVC\b|\bx265\b|\bx264\b',
            r'\bAAC\b|\bAC3\b|\bDTS\b',
        ]

        # 不规范文件名的处理策略 - 按优先级排序
        self.irregular_patterns = OrderedDict([
            ('pure_number', r'^(\d{1,3})\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('universal_episode_quality', r'^(?!.*[Ss]\d{1,2}[Ee]\d{1,3})(.+?)[\s\-_+.]*[Ee](\d{1,3})[\s\-_+.]*(?:1080p|720p|480p|4K|2160p|UHD|HD|FHD|SD)[\s\-_+.]*.*?\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('variety_date_episode', r'^[^第]*?(\d{2,4})[\s.\-_/]*(\d{1,2})[\s.\-_/]*(\d{1,2})[\s.\-_/]*第([一二三四五六七八九十百千万\d]{1,10})[期话]([上中下]?)[^.]*\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('variety_date_special', r'^[^纯花幕加完精未]*?(\d{2,4})[\s.\-_/]*(\d{1,2})[\s.\-_/]*(\d{1,2})[\s.\-_/]*(纯享版|花絮版|幕后版|加更版|完整版|精华版|未删减版)[^.]*\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('simple_episode', r'^第?(\d{1,3})[集期话]?\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('chinese_number', r'^第?([一二三四五六七八九十百]+)[集期话]?\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('english_episode', r'^(Episode|EP|E)(\d{1,3})\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('standard_with_chinese', r'^(.+?)\s*-\s*[Ss](\d{1,2})[Ee](\d{1,3})\s*-\s*第\s*(\d+)\s*[集期话]?\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('mixed_format', r'^(.+?)第(\d{1,3})[集期话]\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('title_number', r'^([^\d]+)(\d{1,3})\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('date_format', r'^(\d{4}[-_]?\d{2}[-_]?\d{2})\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('timestamp_format', r'^(\d{4}[-_]?\d{2}[-_]?\d{2}[-_]?\d{4})\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$'),
            ('random_name', r'^([a-zA-Z0-9_\-]+)\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|rmvb)$')
        ])

        # 中文数字映射
        self.chinese_numbers = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
            '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
            '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20,
            '二十一': 21, '二十二': 22, '二十三': 23, '二十四': 24, '二十五': 25,
            '三十': 30, '四十': 40, '五十': 50, '六十': 60, '七十': 70,
            '八十': 80, '九十': 90, '一百': 100
        }

    def analyze(self, filename: str, context: Dict[str, Any] = None) -> ParsedMediaInfo:
        """分析媒体文件信息"""
        info = ParsedMediaInfo()
        info.original_filename = filename
        context = context or {}

        # 提取扩展名
        info.extension = self._extract_extension(filename)
        if info.extension not in self.video_extensions:
            return info  # 非视频文件

        # 首先尝试处理不规范文件名
        irregular_result = self._handle_irregular_filename(filename, context)
        if irregular_result:
            # 将提取到的信息合并到 info
            for key, value in irregular_result.items():
                setattr(info, key, value)
            info.media_type = self._determine_media_type(info)
            return info

        # 标准文件名处理流程
        clean_name = self._clean_filename(filename)

        # 提取各种信息
        info.title = self._extract_title(clean_name)
        info.year = self._extract_year(clean_name)
        info.season, info.episode = self._extract_season_episode(clean_name)
        info.quality = self._extract_quality(clean_name)
        info.source = self._extract_source(clean_name)
        info.codec = self._extract_codec(clean_name)
        info.audio = self._extract_audio(clean_name)
        info.language = self._extract_language(clean_name)
        info.subtitle = self._extract_subtitle(clean_name)
        info.group = self._extract_group(clean_name)
        info.media_type = self._determine_media_type(info)

        return info

    def _extract_extension(self, filename: str) -> str:
        return Path(filename).suffix.lower()

    def _clean_filename(self, filename: str) -> str:
        name = Path(filename).stem
        name = re.sub(r'[._\-\[\](){}]', ' ', name)
        name = re.sub(r'\s+', ' ', name)
        return name.strip()

    def _handle_irregular_filename(self, filename: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        filename_lower = filename.lower()
        for pattern_name, pattern in self.irregular_patterns.items():
            match = re.match(pattern, filename_lower, re.IGNORECASE)
            if match:
                return self._process_irregular_match(pattern_name, match, filename, context)
        return None

    def _process_irregular_match(self, pattern_name: str, match: re.Match,
                                  filename: str, context: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        if pattern_name == 'pure_number':
            episode_num = int(match.group(1))
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', f"Series_{episode_num:02d}"),
                'season': context.get('season', 1),
                'extension': f".{match.group(2)}"
            })
        elif pattern_name == 'universal_episode_quality':
            prefix = match.group(1).strip()
            episode_num = int(match.group(2))
            extension = f".{match.group(3)}"
            quality_match = re.search(r'(1080p|720p|480p|4K|2160p|UHD|HD|FHD|SD)', match.group(0), re.IGNORECASE)
            quality_str = quality_match.group(1) if quality_match else 'UNKNOWN'
            quality_mapping = {
                '4k': QualityLevel.UHD, '4K': QualityLevel.UHD,
                '2160p': QualityLevel.UHD,
                'uhd': QualityLevel.UHD, 'UHD': QualityLevel.UHD,
                '1080p': QualityLevel.FHD,
                'fhd': QualityLevel.FHD, 'FHD': QualityLevel.FHD,
                '720p': QualityLevel.HD,
                'hd': QualityLevel.HD, 'HD': QualityLevel.HD,
                '480p': QualityLevel.SD,
                'sd': QualityLevel.SD, 'SD': QualityLevel.SD
            }
            title = context.get('series_title')
            if not title:
                clean_prefix = re.sub(r'[\s\-_+.]*(?:S\d{1,2})?[Ee]?\d{1,3}.*$', '', prefix, flags=re.IGNORECASE)
                title = clean_prefix.replace('_', ' ').replace('-', ' ').replace('+', ' ').strip()
                if not title or len(title) < 2:
                    title = f"Series_{episode_num:02d}"
            result.update({
                'episode': episode_num,
                'title': title,
                'season': context.get('season', 1),
                'quality': quality_mapping.get(quality_str.lower(), QualityLevel.UNKNOWN),
                'extension': extension
            })
        elif pattern_name == 'variety_date_episode':
            year_str = match.group(1)
            month_str = match.group(2)
            day_str = match.group(3)
            episode_str = match.group(4)
            part_indicator = match.group(5)
            extension = f".{match.group(6)}"
            if episode_str.isdigit():
                base_episode_num = int(episode_str)
            else:
                base_episode_num = self._convert_chinese_number(episode_str)
            episode_num = base_episode_num
            part_suffix = part_indicator
            if len(year_str) == 2:
                year = 2000 + int(year_str)
            else:
                year = int(year_str)
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', "Variety Show"),
                'season': context.get('season', 1),
                'media_type': MediaType.TV_SERIES,
                'year': year,
                'extension': extension,
                'base_episode': base_episode_num,
                'part_suffix': part_suffix
            })
        elif pattern_name == 'variety_date_special':
            year_str = match.group(1)
            month_str = match.group(2)
            day_str = match.group(3)
            special_type = match.group(4)
            extension = f".{match.group(5)}"
            if len(year_str) == 2:
                year = 2000 + int(year_str)
            else:
                year = int(year_str)
            result.update({
                'episode': None,
                'title': context.get('series_title', "Variety Show"),
                'season': context.get('season', 1),
                'media_type': MediaType.TV_SERIES,
                'year': year,
                'extension': extension,
                'part_suffix': special_type
            })
        elif pattern_name == 'simple_episode':
            episode_num = int(match.group(1))
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', "Unknown Series"),
                'season': context.get('season', 1),
                'extension': f".{match.group(2)}"
            })
        elif pattern_name == 'chinese_number':
            chinese_num = match.group(1)
            episode_num = self._convert_chinese_number(chinese_num)
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', "Unknown Series"),
                'season': context.get('season', 1),
                'extension': f".{match.group(2)}"
            })
        elif pattern_name == 'english_episode':
            episode_num = int(match.group(2))
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', "Unknown Series"),
                'season': context.get('season', 1),
                'extension': f".{match.group(3)}"
            })
        elif pattern_name == 'standard_with_chinese':
            title_part = match.group(1).strip()
            season_num = int(match.group(2))
            episode_num = int(match.group(3))
            extension = f".{match.group(5)}"
            result.update({
                'title': title_part,
                'season': season_num,
                'episode': episode_num,
                'extension': extension,
                'media_type': MediaType.TV_SERIES
            })
        elif pattern_name == 'mixed_format':
            title_part = match.group(1).strip()
            title_part = re.sub(r'\.+$', '', title_part)
            episode_num = int(match.group(2))
            result.update({
                'episode': episode_num,
                'title': title_part or context.get('series_title', "Unknown Series"),
                'season': context.get('season', 1),
                'extension': f".{match.group(3)}"
            })
        elif pattern_name == 'title_number':
            title_part = match.group(1).strip()
            episode_num = int(match.group(2))
            result.update({
                'title': title_part,
                'season': context.get('season', 1),
                'episode': episode_num,
                'extension': f".{match.group(3)}"
            })
        elif pattern_name in ['date_format', 'timestamp_format']:
            date_str = match.group(1)
            clean_date = re.sub(r'[-_]', '', date_str)
            if len(clean_date) >= 8:
                year = int(clean_date[:4])
                month = int(clean_date[4:6])
                day = int(clean_date[6:8])
                result.update({
                    'title': context.get('series_title', "Daily Show"),
                    'year': year,
                    'episode': day,
                    'extension': f".{match.group(2)}"
                })
        elif pattern_name == 'random_name':
            random_name = match.group(1)
            if re.search(r'[Ss]\d{1,2}[Ee]\d{1,3}', random_name):
                return None
            numbers = re.findall(r'\d+', random_name)
            episode_num = int(numbers[-1]) if numbers else 1
            result.update({
                'episode': episode_num,
                'title': context.get('series_title', "Unknown Series"),
                'season': context.get('season', 1),
                'extension': f".{match.group(2)}"
            })
        return result

    def _convert_chinese_number(self, chinese_num: str) -> int:
        if chinese_num in self.chinese_numbers:
            return self.chinese_numbers[chinese_num]
        if '十' in chinese_num:
            if chinese_num == '十':
                return 10
            elif chinese_num.startswith('十'):
                return 10 + self.chinese_numbers.get(chinese_num[1:], 0)
            elif chinese_num.endswith('十'):
                return self.chinese_numbers.get(chinese_num[:-1], 0) * 10
            else:
                parts = chinese_num.split('十')
                if len(parts) == 2:
                    tens = self.chinese_numbers.get(parts[0], 0) * 10
                    ones = self.chinese_numbers.get(parts[1], 0)
                    return tens + ones
        return 1

    def _extract_title(self, clean_name: str) -> str:
        """增强版标题提取，彻底移除技术参数"""
        title = clean_name
        # 移除年份
        title = re.sub(r'\b(19|20)\d{2}\b', '', title)
        # 移除季集信息
        title = re.sub(r'[\.\s]*[Ss]\d{1,2}[Ee]\d{1,3}[\.\s]*', '', title)
        title = re.sub(r'[\.\s]*第\s*\d+\s*[季集期话][\.\s]*', '', title)
        title = re.sub(r'[\.\s]*(Season|Episode)\s*\d+[\.\s]*', '', title, flags=re.IGNORECASE)
        # 批量移除技术参数
        for pattern in self.tech_patterns:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        # 清理多余空格和点
        title = re.sub(r'\.+', '.', title)
        title = re.sub(r'^\.|\.+$', '', title)
        title = re.sub(r'\s+', ' ', title).strip()
        # 如果标题为空，返回原始文件名的一部分
        if not title:
            title = clean_name[:30]
        return title

    def _extract_year(self, clean_name: str) -> Optional[int]:
        matches = re.findall(r'\b(19[5-9]\d|20[0-4]\d)\b', clean_name)
        if matches:
            return int(matches[0])
        return None

    def _extract_season_episode(self, clean_name: str) -> Tuple[Optional[int], Optional[int]]:
        season, episode = None, None
        match = re.search(r'\b[Ss](\d{1,2})[Ee](\d{1,3})\b', clean_name)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            return season, episode
        season_match = re.search(r'第\s*(\d+)\s*季', clean_name)
        if season_match:
            season = int(season_match.group(1))
        episode_match = re.search(r'第\s*(\d+)\s*[集期话]', clean_name)
        if episode_match:
            episode = int(episode_match.group(1))
        if not season:
            season_match = re.search(r'\bSeason\s*(\d+)\b', clean_name, re.IGNORECASE)
            if season_match:
                season = int(season_match.group(1))
        if not episode:
            episode_match = re.search(r'\bEpisode\s*(\d+)\b', clean_name, re.IGNORECASE)
            if episode_match:
                episode = int(episode_match.group(1))
        return season, episode

    def _extract_quality(self, clean_name: str) -> QualityLevel:
        for pattern, quality in self.quality_patterns.items():
            if re.search(pattern, clean_name, re.IGNORECASE):
                return quality
        return QualityLevel.UNKNOWN

    def _extract_source(self, clean_name: str) -> str:
        for source in self.source_patterns:
            if re.search(rf'\b{re.escape(source)}\b', clean_name, re.IGNORECASE):
                return source
        return ""

    def _extract_codec(self, clean_name: str) -> str:
        for codec in self.codec_patterns:
            if re.search(rf'\b{re.escape(codec)}\b', clean_name, re.IGNORECASE):
                return codec
        return ""

    def _extract_audio(self, clean_name: str) -> str:
        for audio in self.audio_patterns:
            if re.search(rf'\b{re.escape(audio)}\b', clean_name, re.IGNORECASE):
                return audio
        return ""

    def _extract_language(self, clean_name: str) -> str:
        for lang, patterns in self.language_patterns.items():
            for pattern in patterns:
                if pattern in clean_name:
                    return lang
        return ""

    def _extract_subtitle(self, clean_name: str) -> str:
        for sub in self.subtitle_patterns:
            if sub in clean_name:
                return sub
        return ""

    def _extract_group(self, clean_name: str) -> str:
        match = re.search(r'[\[\(]([^[\]()]+)[\]\)]$', clean_name)
        if match:
            group = match.group(1).strip()
            if not any(tech in group.upper() for tech in ['1080P', '720P', 'H264', 'X264', 'AAC']):
                return group
        return ""

    def _determine_media_type(self, info: ParsedMediaInfo) -> MediaType:
        if info.season is not None or info.episode is not None:
            variety_keywords = ['综艺', '节目', '秀', 'Show', '期']
            if any(keyword in info.title for keyword in variety_keywords):
                return MediaType.VARIETY_SHOW
            anime_keywords = ['动漫', '动画', 'Anime', '番']
            if any(keyword in info.title for keyword in anime_keywords):
                return MediaType.ANIME
            return MediaType.TV_SERIES
        doc_keywords = ['纪录片', 'Documentary', '记录', '探索']
        if any(keyword in info.title for keyword in doc_keywords):
            return MediaType.DOCUMENTARY
        return MediaType.MOVIE


# ========== TMDBClient 类 ==========
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


# ========== MediaOrganizer 类（已扩展）==========
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
        # 智能分析器实例
        self.analyzer = SmartMediaAnalyzer()

    # ===== 原有的方法 =====
    def extract_tmdb_id(self, text: str) -> Optional[int]:
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
        match = re.search(r'(?:^|\D)(\d{4})(?:\D|$)', text)
        if match:
            return int(match.group(1))
        return None

    def extract_season_episode(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        season = None
        episode = None
        match = re.search(r'S(\d{1,3})E(\d{1,4})', text, re.IGNORECASE)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            return season, episode
        match = re.search(r'S(\d{1,3})|Season[.\s]*(\d{1,3})', text, re.IGNORECASE)
        if match:
            season = int(match.group(1) or match.group(2))
        match = re.search(r'第\s*(\d+)\s*[集]', text)
        if match:
            episode = int(match.group(1))
        if episode is not None and season is None:
            season = 1
        return season, episode

    def extract_resolution(self, text: str) -> Optional[str]:
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
        cleaned = raw_title
        cleaned = re.sub(r'^[\U0001F300-\U0001F9FF\s]+', '', cleaned)
        cleaned = re.sub(r'^[🎬🎥🎞️📀📁]\s*标题[：:]\s*', '', cleaned)
        cleaned = re.sub(r'\s*[\(\[]?\d{4}[\)\]]?\s*', '', cleaned)
        cleaned = re.sub(r'\s*(?:[\(\{\[]?\s*(?:tmdb|id)[\s\-=]?\d+\s*[\)\}\]]?)', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*(?:S\d+E\d+|S\d+|第\s*\d+\s*[集]|Season\s*\d+)\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*(?:1080[pi]|2160p|4K|WEB-?DL|WEB-?Rip|HDTV|HDR|DV|FLAC|DDP|AAC|AC3|DTS|H\.?265|H\.?264|AVC|REMUX|BluRay|LINE\s*TV)', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\.(mkv|mp4|avi|ts|mov|flv|wmv)$', '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace('.', ' ').replace('-', ' ').replace('_', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            cleaned = raw_title[:30]
        return cleaned

    def parse_title_year(self, raw_title: str) -> Tuple[str, Optional[int]]:
        year = self.extract_year(raw_title)
        clean = self.clean_title(raw_title)
        return clean, year

    def match_rule(self, media_info: Dict) -> Optional[Dict]:
        media_type = 'movie' if media_info.get('media_type') == 'movie' else 'tv'
        genre_ids = media_info.get('genre_ids', [])
        for rule in self.rules:
            if media_type not in rule.get('media_types', []):
                continue
            conditions = rule.get('conditions', {})
            genre_cond = conditions.get('genre_ids')
            if genre_cond and not self._check_genre(genre_ids, genre_cond):
                continue
            countries = media_info.get('production_countries', [])
            country_codes = [c.get('iso_3166_1') for c in countries if c.get('iso_3166_1')]
            country_cond = conditions.get('production_countries')
            if country_cond and not self._check_countries(country_codes, country_cond):
                continue
            return rule
        return None

    def _check_genre(self, genre_ids: List[int], condition: str) -> bool:
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
        allowed = [c.strip() for c in condition.split(',')]
        return any(code in allowed for code in country_codes)

    # ===== 生成新文件名的方法（使用智能分析器）=====
    def generate_new_name(self, rule: Dict, media_info: Dict, original_filename: str = None) -> str:
        """根据重命名模板和原始文件名生成新文件名
           格式如：光阴之外.2025.S01E11.2160p.HEVC.AAC.mkv
           使用智能分析器从原始文件名中提取技术参数
        """
        media_type = media_info.get('media_type')
        
        # 获取并清理标题
        title = media_info.get('title') or media_info.get('name') or ''
        title = re.sub(r'[<>:"/\\|?*]', '', title)
        title = title.replace(' ', '.')
        title = re.sub(r'\.+', '.', title)
        
        # 获取年份
        year = media_info.get('release_date') or media_info.get('first_air_date') or ''
        if year:
            year = year[:4]
        
        # 构建文件名各部分
        parts = [title]
        if year:
            parts.append(year)
        
        # 从原始文件名中提取信息（使用智能分析器）
        source = ''
        resolution = ''
        video_codec = ''
        audio_codec = ''
        season_episode = ''
        if original_filename:
            parsed = self.analyzer.analyze(original_filename)
            if parsed.source:
                source = parsed.source
            if parsed.quality != QualityLevel.UNKNOWN:
                resolution = parsed.quality.value
            if parsed.codec:
                video_codec = parsed.codec
            if parsed.audio:
                audio_codec = parsed.audio
            if parsed.season and parsed.episode:
                season_episode = f"S{parsed.season:02d}E{parsed.episode:02d}"
        
        # 添加剧集信息（如果解析到了）
        if season_episode:
            parts.append(season_episode)
        
        # 添加技术参数（按特定顺序）
        if source:
            parts.append(source)
        if resolution:
            parts.append(resolution)
        if video_codec:
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
        base = settings.P115_ORGANIZE_BASE_DIR.strip()
        if not base:
            base = settings.P115_SAVE_DIR or '/分享保存'
        if path.startswith('/'):
            return path
        return base.rstrip('/') + '/' + path.lstrip('/')