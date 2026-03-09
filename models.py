from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # type ∈ document | trip | gift | certificate | subscription | payment | logistics
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # status ∈ active | expiring_soon | expired | archived | paused | closed
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # задел на v2
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    reminders: Mapped[list[Reminder]] = relationship(
        "Reminder", back_populates="entity", cascade="all, delete-orphan"
    )
    checklist_items: Mapped[list[ChecklistItem]] = relationship(
        "ChecklistItem", back_populates="entity", cascade="all, delete-orphan"
    )
    resources: Mapped[list[Resource]] = relationship(
        "Resource", back_populates="entity", cascade="all, delete-orphan"
    )
    event_logs: Mapped[list[EventLog]] = relationship("EventLog", back_populates="entity")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), nullable=False)
    trigger_date: Mapped[date] = mapped_column(Date, nullable=False)
    rule: Mapped[str] = mapped_column(String(50), nullable=False)
    # rule ∈ before_N_days | on_date | recurring_weekly | digest_only
    channel: Mapped[str] = mapped_column(String(50), nullable=False, default="telegram")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    # status ∈ pending | sent | snoozed | cancelled
    snoozed_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity: Mapped[Entity] = relationship("Entity", back_populates="reminders")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    # status ∈ open | done | skipped
    depends_on: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("checklist_items.id"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    entity: Mapped[Entity] = relationship("Entity", back_populates="checklist_items")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    gift_history: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    entity_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # type ∈ file | link | contact
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    entity: Mapped[Entity] = relationship("Entity", back_populates="resources")


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("entities.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity: Mapped[Entity | None] = relationship("Entity", back_populates="event_logs")


class ReferenceData(Base):
    __tablename__ = "reference_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # type ∈ person | car | address | document
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    r2_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # r2_key — ключ файла в Cloudflare R2 (скан документа)
    owner_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # owner_ref_id — id записи type=person в той же таблице (мягкая связь без FK)
    relation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # relation — роль человека: жена, муж, сын, дочь, мама, папа, друг, подруга, я
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
