"""Model CSDL (SQLAlchemy) và bố cục bảng.

Trách nhiệm: lưu metadata — artifact nặng (video, parquet) nằm trên đĩa dưới
`settings.storage_dir`, CSDL chỉ giữ con trỏ tới chúng. Nhồi frame vào CSDL
sẽ phá cả chi phí lưu trữ lẫn tốc độ truy vấn.

Bảng (bản Core — xem `plan_backend_core.md`):
    users            — tài khoản, vai trò (operator/reviewer/admin), mật khẩu đã băm
    tasks            — danh mục nhiệm vụ demo (name là khoá chính)
    episodes         — một demonstration: task, operator, trạng thái, nhãn, khoảng cắt
    datasets         — tập demo đã duyệt được đóng gói thành zip
    dataset_episodes — bảng nối datasets ↔ episodes (nhiều-nhiều)

Quan hệ quan trọng: xoá một `episode` chỉ xoá row `dataset_episodes` tương ứng
(cascade), KHÔNG đụng tới file zip đã đóng gói của dataset — zip là bản
snapshot độc lập tại thời điểm tạo. Xoá một `dataset` cascade xoá toàn bộ row
`dataset_episodes` của nó (không xoá `episodes` gốc).
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.config import get_settings
from src.models.enums import DatasetStatus, DemoOutcome, DemoStatus, UserRole


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.OPERATOR)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Task(Base):
    __tablename__ = "tasks"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    action_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)


class Episode(Base):
    """Một demo (video upload thủ công trong bản Core)."""

    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_name: Mapped[str] = mapped_column(ForeignKey("tasks.name"), nullable=False, index=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[DemoStatus] = mapped_column(String(20), nullable=False, default=DemoStatus.RECORDED)
    outcome: Mapped[DemoOutcome | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    trim_start_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    trim_end_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    has_wrist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_trajectory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    operator: Mapped["User"] = relationship(foreign_keys=[operator_id])
    reviewer: Mapped["User | None"] = relationship(foreign_keys=[reviewer_id])
    dataset_links: Mapped[list["DatasetEpisode"]] = relationship(
        back_populates="episode",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RawEpisodeManagement(Base):
    """Mutable management overlay; raw files and source records stay immutable."""

    __tablename__ = "raw_episode_management"

    episode_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RawEpisodeAudit(Base):
    """Append-only audit trail for management/review changes."""

    __tablename__ = "raw_episode_audit"
    __table_args__ = (Index("ix_raw_audit_episode_created", "episode_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    episode_id: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    task_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    include_failures: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[DatasetStatus] = mapped_column(String(20), nullable=False, default=DatasetStatus.BUILDING)
    zip_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    num_episodes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    num_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_source: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    collection_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    exporter_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    episode_inventory: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    schema_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    build_spec: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    episode_links: Mapped[list["DatasetEpisode"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def format(self) -> str:
        return "robomimic" if self.zip_path and self.zip_path.lower().endswith(".hdf5") else "raw"


class DatasetEpisode(Base):
    """Bảng nối datasets ↔ episodes (nhiều-nhiều)."""

    __tablename__ = "dataset_episodes"

    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True
    )

    dataset: Mapped["Dataset"] = relationship(back_populates="episode_links")
    episode: Mapped["Episode"] = relationship(back_populates="dataset_links")


def enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """SQLite mặc định TẮT ràng buộc khoá ngoại — bật lên để `ondelete=CASCADE` có tác dụng."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine bất đồng bộ mặc định, cache theo tiến trình (dựa trên `get_settings()`)."""
    settings = get_settings()
    connect_args: dict = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_async_engine(settings.database_url, connect_args=connect_args)
    if settings.database_url.startswith("sqlite"):
        enable_sqlite_foreign_keys(engine)
    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: một `AsyncSession` theo request, tự đóng khi xong."""
    factory = session_factory(get_engine())
    async with factory() as session:
        yield session


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Tạo toàn bộ bảng nếu chưa có. Chưa dùng Alembic ở bản Core."""
    target = engine or get_engine()
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            columns = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(datasets)")).all()}
            additions = {
                "data_source": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
                "collection_batch_id": "VARCHAR(64)",
                "created_by": "VARCHAR(150)",
                "exporter_version": "VARCHAR(20) NOT NULL DEFAULT '1.0'",
                "episode_inventory": "JSON NOT NULL DEFAULT '[]'",
                "schema_manifest": "JSON NOT NULL DEFAULT '{}'",
                "build_spec": "JSON NOT NULL DEFAULT '[]'",
            }
            for name, definition in additions.items():
                if name not in columns:
                    await conn.exec_driver_sql(f"ALTER TABLE datasets ADD COLUMN {name} {definition}")
