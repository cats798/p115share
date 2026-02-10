from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str] = mapped_column(Text, default="/logo.png")  # Stores base64 data URI or path
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class SystemSettings(Base):
    __tablename__ = "system_settings"
    
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(255), nullable=True)

class PendingLink(Base):
    __tablename__ = "pending_links"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    share_url: Mapped[str] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="auditing") # auditing, failed, completed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_check: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class LinkHistory(Base):
    __tablename__ = "link_history"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    original_url: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    share_link: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ExcelTask(Base):
    __tablename__ = "excel_tasks"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="wait")  # wait, running, paused, completed, cancelled
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ExcelTaskItem(Base):
    __tablename__ = "excel_task_items"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    original_url: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=True)
    extraction_code: Mapped[str] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="待处理")  # 待处理, 处理中, 成功, 失败, 跳过
    error_msg: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
