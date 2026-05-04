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

# Allowed enum values are documented as constants for use in app code/tests.
PERSON_RELATIONS = {
    "я",
    "жена",
    "муж",
    "сын",
    "дочь",
    "мама",
    "папа",
    "брат",
    "сестра",
    "друг",
    "коллега",
    "иное",
}

DOCUMENT_KINDS = {
    "passport",
    "driver_license",
    "insurance",
    "visa",
    "certificate",
    "contract",
    "snils",
    "inn",
    "medical",
    "ticket",
    "other",
}

DOCUMENT_STATUSES = {"active", "replaced"}

BOT_STATES = {
    "awaiting_more_photos",
    "awaiting_ocr_verification",
    "awaiting_ocr_edit",
    "awaiting_dup_resolution",
    "awaiting_retrieve_choice",
}


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    relation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="owner", foreign_keys="Document.owner_person_id"
    )
    vehicles: Mapped[list[Vehicle]] = relationship(
        "Vehicle", back_populates="owner", foreign_keys="Vehicle.owner_person_id"
    )
    addresses: Mapped[list[Address]] = relationship(
        "Address", back_populates="person", foreign_keys="Address.person_id"
    )


class Document(Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_person_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id"), nullable=True
    )
    issued_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped[Person | None] = relationship(
        "Person", back_populates="documents", foreign_keys=[owner_person_id]
    )


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    plate: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    owner_person_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id"), nullable=True
    )
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped[Person | None] = relationship(
        "Person", back_populates="vehicles", foreign_keys=[owner_person_id]
    )


class Address(Base):
    __tablename__ = "address"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    person_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("person.id"), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    street: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    person: Mapped[Person | None] = relationship(
        "Person", back_populates="addresses", foreign_keys=[person_id]
    )


class Note(Base):
    __tablename__ = "note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BotState(Base):
    __tablename__ = "bot_state"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
