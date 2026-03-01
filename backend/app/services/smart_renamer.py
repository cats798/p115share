import re
from typing import Optional, Dict, List, Any, Tuple
from loguru import logger
from app.services.tmdb import MediaOrganizer, TMDBClient
from app.core.config import settings

class SmartRenamer:
    """智能重命名器，整合TMDB识别和格式化重命名"""
    
    # 重命名风格模板
    STYLES = {
        "simple": {
            "movie": "{title}.{year}.{quality}",
            "tv": "{title}.{year}.S{season:02d}E{episode:02d}.{quality}"
        },
        "detailed": {
            "movie": "{title}.{year}.{source}.{resolution}.{video_codec}.{audio_codec}",
            "tv": "{title}.{year}.S{season:02d}E{episode:02d}.{source}.{resolution}.{video_codec}.{audio_codec}"
        },
        "plex": {
            "movie": "{title} ({year}) [{source} {resolution}]",
            "tv": "{title} - S{season:02d}E{episode:02d} - {episode_title}"
        },
        "emby": {
            "movie": "{title} ({year})",
            "tv": "{title} ({year}) - S{season:02d}E{episode:02d}"
        },
        "kodi": {
            "movie": "{title} ({year})",
            "tv": "{title} - {season}x{episode:02d} - {episode_title}"
        }
    }
    
    def __init__(self, tmdb_client: Optional[TMDBClient] = None):
        self.tmdb_client = tmdb_client or TMDBClient()
        self.organizer = MediaOrganizer(settings.TMDB_CONFIG)
        self.custom_templates = {}  # 可以保存用户自定义模板
        
    async def analyze_filename(self, filename: str) -> Dict[str, Any]:
        """分析文件名，提取所有可能的元数据"""
        info = {
            "original": filename,
            "title": None,
            "year": None,
            "season": None,
            "episode": None,
            "resolution": None,
            "source": None,
            "video_codec": None,
            "audio_codec": None,
            "tmdb_id": None,
            "episode_title": None,
            "media_type": None  # movie 或 tv
        }
        
        # 提取 TMDB ID
        info["tmdb_id"] = self.organizer.extract_tmdb_id(filename)
        
        # 提取年份
        info["year"] = self.organizer.extract_year(filename)
        
        # 提取季数和集数
        season, episode = self.organizer.extract_season_episode(filename)
        info["season"] = season
        info["episode"] = episode
        
        # 提取技术参数
        info["resolution"] = self.organizer.extract_resolution(filename)
        info["source"] = self.organizer.extract_source(filename)
        info["video_codec"] = self.organizer.extract_video_codec(filename)
        info["audio_codec"] = self.organizer.extract_audio_codec(filename)
        
        # 判断媒体类型
        if info["season"] is not None or info["episode"] is not None:
            info["media_type"] = "tv"
        else:
            info["media_type"] = "movie"
            
        return info
    
    async def get_tmdb_info(self, filename: str, info: Optional[Dict] = None) -> Optional[Dict]:
        """从TMDB获取媒体信息"""
        if not info:
            info = await self.analyze_filename(filename)
            
        media_info = None
        
        # 优先使用TMDB ID查询
        if info["tmdb_id"]:
            for mtype in ['movie', 'tv']:
                media_info = await self.tmdb_client.get_details(mtype, info["tmdb_id"])
                if media_info:
                    media_info['media_type'] = mtype
                    logger.info(f"通过ID {info['tmdb_id']} 找到媒体")
                    break
        
        # 如果没有ID或ID查询失败，使用标题搜索
        if not media_info:
            # 从文件名提取干净标题
            clean_title, _ = self.organizer.parse_title_year(filename)
            media_info = await self.tmdb_client.search_multi(clean_title, info["year"])
            if media_info:
                logger.info(f"通过标题搜索找到媒体: {clean_title}")
        
        return media_info
    
    async def preview_rename(self, filename: str, style: str = "simple", 
                            custom_title: Optional[str] = None,
                            custom_template: Optional[str] = None,
                            custom_season: Optional[int] = None) -> Dict[str, Any]:
        """预览重命名结果"""
        # 分析文件名
        info = await self.analyze_filename(filename)
        
        # 获取TMDB信息
        tmdb_info = await self.get_tmdb_info(filename, info)
        
        if tmdb_info:
            # 使用TMDB的标题
            if info["media_type"] == "tv":
                info["title"] = tmdb_info.get('name')
                # 如果有季数信息，尝试获取集标题
                if info["season"] and info["episode"]:
                    # 这里可以调用获取具体集信息的API
                    pass
            else:
                info["title"] = tmdb_info.get('title')
        
        # 使用自定义标题覆盖
        if custom_title:
            info["title"] = custom_title
            
        # 使用自定义季数覆盖
        if custom_season is not None:
            info["season"] = custom_season
        
        # 生成新文件名
        if custom_template:
            new_name = self._apply_template(custom_template, info)
        else:
            new_name = self._apply_style(style, info)
        
        # 返回预览结果
        return {
            "original": filename,
            "new": new_name,
            "info": info,
            "tmdb_info": tmdb_info,
            "suggestions": await self._generate_suggestions(info)
        }
    
    async def rename_file(self, filename: str, style: str = "simple",
                         custom_title: Optional[str] = None,
                         custom_template: Optional[str] = None,
                         custom_season: Optional[int] = None) -> str:
        """重命名单个文件"""
        preview = await self.preview_rename(filename, style, custom_title, custom_template, custom_season)
        return preview["new"]
    
    async def batch_preview(self, filenames: List[str], 
                           directory_path: str = "",
                           custom_title: Optional[str] = None,
                           custom_season: Optional[int] = None) -> Dict[str, Any]:
        """批量预览重命名"""
        results = []
        common_info = {}
        
        # 从目录名推断共同信息
        if directory_path:
            dir_name = directory_path.split('/')[-1]
            # 尝试从目录名提取标题
            common_info["title"] = self._extract_title_from_dir(dir_name)
        
        # 批量分析
        for filename in filenames:
            info = await self.analyze_filename(filename)
            
            # 应用共同信息
            if custom_title:
                info["title"] = custom_title
            elif common_info.get("title"):
                info["title"] = common_info["title"]
            
            if custom_season is not None:
                info["season"] = custom_season
            elif common_info.get("season"):
                info["season"] = common_info["season"]
            
            # 尝试获取TMDB信息
            tmdb_info = await self.get_tmdb_info(filename, info)
            if tmdb_info and not info.get("title"):
                if info["media_type"] == "tv":
                    info["title"] = tmdb_info.get('name')
                else:
                    info["title"] = tmdb_info.get('title')
            
            # 生成新文件名
            new_name = self._apply_style("detailed", info)
            
            results.append({
                "original": filename,
                "new": new_name,
                "info": info
            })
        
        # 检测系列
        series_detected = self._detect_series(results)
        
        return {
            "results": results,
            "series_detected": series_detected,
            "common_info": common_info
        }
    
    async def batch_rename_with_context(self, filenames: List[str],
                                       directory_path: str = "",
                                       custom_title: Optional[str] = None,
                                       custom_season: Optional[int] = None) -> Dict[str, str]:
        """带上下文的批量重命名"""
        preview = await self.batch_preview(filenames, directory_path, custom_title, custom_season)
        
        result_map = {}
        for item in preview["results"]:
            result_map[item["original"]] = item["new"]
        
        return result_map
    
    async def get_suggestions(self, filename: str, custom_title: Optional[str] = None) -> List[Dict[str, str]]:
        """获取重命名建议"""
        suggestions = []
        
        # 分析文件名
        info = await self.analyze_filename(filename)
        
        # 生成各种风格的建议
        for style_name in self.STYLES.keys():
            new_name = self._apply_style(style_name, info)
            suggestions.append({
                "style": style_name,
                "name": new_name,
                "description": self._get_style_description(style_name)
            })
        
        # 如果有TMDB ID，添加基于TMDB的建议
        if info["tmdb_id"]:
            tmdb_info = await self.get_tmdb_info(filename, info)
            if tmdb_info:
                # 使用TMDB的标题
                info_with_tmdb = info.copy()
                if info["media_type"] == "tv":
                    info_with_tmdb["title"] = tmdb_info.get('name')
                else:
                    info_with_tmdb["title"] = tmdb_info.get('title')
                
                new_name = self._apply_style("detailed", info_with_tmdb)
                suggestions.append({
                    "style": "tmdb_detailed",
                    "name": new_name,
                    "description": "使用TMDB标题的详细格式"
                })
        
        return suggestions
    
    def _apply_style(self, style: str, info: Dict[str, Any]) -> str:
        """应用命名风格"""
        if style not in self.STYLES and style not in self.custom_templates:
            style = "simple"
        
        if style in self.custom_templates:
            template = self.custom_templates[style]
        else:
            template = self.STYLES.get(style, self.STYLES["simple"])
        
        # 根据媒体类型选择模板
        media_type = info.get("media_type", "movie")
        if isinstance(template, dict):
            template_str = template.get(media_type, template["movie"])
        else:
            template_str = template
        
        return self._format_template(template_str, info)
    
    def _apply_template(self, template: str, info: Dict[str, Any]) -> str:
        """应用自定义模板"""
        return self._format_template(template, info)
    
    def _format_template(self, template: str, info: Dict[str, Any]) -> str:
        """格式化模板字符串"""
        # 准备格式参数
        params = {
            "title": info.get("title") or "Unknown",
            "year": info.get("year") or "",
            "season": info.get("season") or 1,
            "episode": info.get("episode") or 1,
            "resolution": info.get("resolution") or "",
            "source": info.get("source") or "",
            "video_codec": info.get("video_codec") or "",
            "audio_codec": info.get("audio_codec") or "",
            "quality": self._get_quality_string(info),
            "episode_title": info.get("episode_title") or "",
            "tmdb_id": info.get("tmdb_id") or ""
        }
        
        # 处理特殊格式：零填充数字
        result = template.format(
            **params,
            season=params["season"],
            episode=params["episode"]
        )
        
        # 清理多余的点
        result = re.sub(r'\.+', '.', result)
        result = re.sub(r'\.$', '', result)
        
        # 添加扩展名
        if info.get("original"):
            ext_match = re.search(r'\.([a-zA-Z0-9]+)$', info["original"])
            if ext_match:
                result = f"{result}.{ext_match.group(1)}"
        
        return result
    
    def _get_quality_string(self, info: Dict[str, Any]) -> str:
        """获取质量字符串"""
        parts = []
        if info.get("resolution"):
            parts.append(info["resolution"])
        if info.get("source"):
            parts.append(info["source"])
        if info.get("video_codec"):
            parts.append(info["video_codec"])
        if info.get("audio_codec"):
            parts.append(info["audio_codec"])
        return '.'.join(parts) if parts else "Unknown"
    
    def _get_style_description(self, style: str) -> str:
        """获取风格描述"""
        descriptions = {
            "simple": "简单格式：标题.年份.质量",
            "detailed": "详细格式：包含所有技术参数",
            "plex": "Plex 兼容格式",
            "emby": "Emby 兼容格式",
            "kodi": "Kodi 兼容格式"
        }
        return descriptions.get(style, style)
    
    def _extract_title_from_dir(self, dir_name: str) -> Optional[str]:
        """从目录名提取标题"""
        # 移除年份
        dir_name = re.sub(r'\s*[\(\[]?\d{4}[\)\]]?\s*', '', dir_name)
        # 移除常见后缀
        dir_name = re.sub(r'\s*(4K|1080p|2160p|BluRay|WEB-DL).*$', '', dir_name, flags=re.IGNORECASE)
        return dir_name.strip()
    
    def _detect_series(self, results: List[Dict]) -> Dict[str, Any]:
        """检测是否为系列文件"""
        if len(results) < 2:
            return {}
        
        # 检查是否有共同的标题
        titles = set()
        for r in results:
            if r["info"].get("title"):
                titles.add(r["info"]["title"])
        
        if len(titles) == 1:
            common_title = list(titles)[0]
            # 检查是否有连续的季数/集数
            episodes = []
            for r in results:
                if r["info"].get("season") is not None and r["info"].get("episode") is not None:
                    episodes.append({
                        "season": r["info"]["season"],
                        "episode": r["info"]["episode"]
                    })
            
            if episodes:
                return {
                    "title": common_title,
                    "episodes": episodes,
                    "count": len(episodes)
                }
        
        return {}


# API 路由文件 (routes/smart_rename.py)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
from api.deps import get_current_user
from models.user import User
from schemas.response import Response
from app.services.smart_renamer import SmartRenamer
from loguru import logger

router = APIRouter(prefix="/smart-rename", tags=["智能重命名"])

class RenameFileRequest(BaseModel):
    filename: str = Field(..., description="原文件名")
    custom_title: Optional[str] = Field(None, description="自定义标题")
    style: str = Field("detailed", description="重命名风格")
    custom_template: Optional[str] = Field(None, description="自定义模板")
    custom_season: Optional[int] = Field(None, description="自定义季数")

class BatchRenameRequest(BaseModel):
    filenames: List[str] = Field(..., description="文件名列表")
    custom_title: Optional[str] = Field(None, description="自定义标题")
    custom_season: Optional[int] = Field(None, description="自定义季数")
    directory_path: str = Field("", description="目录路径")

class PreviewRenameRequest(BaseModel):
    filename: str = Field(..., description="原文件名")
    custom_title: Optional[str] = Field(None, description="自定义标题")
    style: str = Field("detailed", description="重命名风格")
    custom_template: Optional[str] = Field(None, description="自定义模板")
    custom_season: Optional[int] = Field(None, description="自定义季数")

class BatchPreviewRequest(BaseModel):
    filenames: List[str] = Field(..., description="文件名列表")
    custom_title: Optional[str] = Field(None, description="自定义标题")
    custom_season: Optional[int] = Field(None, description="自定义季数")
    directory_path: str = Field("", description="目录路径")

class SuggestionsRequest(BaseModel):
    filename: str = Field(..., description="原文件名")
    custom_title: Optional[str] = Field(None, description="自定义标题")

# 创建全局实例
smart_renamer = SmartRenamer()

@router.post("/preview", response_model=Response[Dict[str, Any]])
async def preview_rename(
    req: PreviewRenameRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        result = await smart_renamer.preview_rename(
            filename=req.filename,
            style=req.style,
            custom_title=req.custom_title,
            custom_template=req.custom_template,
            custom_season=req.custom_season
        )
        return Response(data=result, message="预览成功")
    except Exception as e:
        logger.error(f"预览重命名失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/batch/preview", response_model=Response[Dict[str, Any]])
async def preview_batch_rename(
    req: BatchPreviewRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        result = await smart_renamer.batch_preview(
            filenames=req.filenames,
            directory_path=req.directory_path,
            custom_title=req.custom_title,
            custom_season=req.custom_season
        )
        return Response(data=result, message="批量预览成功")
    except Exception as e:
        logger.error(f"批量预览失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/file", response_model=Response[str])
async def rename_single_file(
    req: RenameFileRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        result = await smart_renamer.rename_file(
            filename=req.filename,
            style=req.style,
            custom_title=req.custom_title,
            custom_template=req.custom_template,
            custom_season=req.custom_season
        )
        return Response(data=result, message="重命名成功")
    except Exception as e:
        logger.error(f"单文件重命名失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/batch", response_model=Response[Dict[str, str]])
async def rename_batch_files(
    req: BatchRenameRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        results = await smart_renamer.batch_rename_with_context(
            filenames=req.filenames,
            directory_path=req.directory_path,
            custom_title=req.custom_title,
            custom_season=req.custom_season
        )
        return Response(data=results, message="批量重命名成功")
    except Exception as e:
        logger.error(f"批量重命名失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/suggestions", response_model=Response[List[Dict[str, str]]])
async def get_rename_suggestions(
    req: SuggestionsRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        suggestions = await smart_renamer.get_suggestions(
            filename=req.filename,
            custom_title=req.custom_title
        )
        return Response(data=suggestions, message="获取建议成功")
    except Exception as e:
        logger.error(f"获取建议失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
"""
