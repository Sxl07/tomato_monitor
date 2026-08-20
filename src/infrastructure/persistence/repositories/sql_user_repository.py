"""Concrete repository implementation for User entities using SQLAlchemy."""

from typing import Optional

from sqlalchemy.orm import Session

from src.domain.entities.user import User
from src.domain.repositories.user_repository import UserRepository
from src.infrastructure.persistence.models.user_model import UserModel


class SqlUserRepository(UserRepository):
    """SQLAlchemy-based implementation of UserRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, user: User) -> User:
        """Persist a new user. Return it with assigned id."""
        model = self._to_model(user)
        self._session.add(model)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, id: int) -> Optional[User]:
        """Return the user with the given id, or None if not found."""
        model = self._session.query(UserModel).filter(UserModel.id == id).first()
        if model is None:
            return None
        return self._to_entity(model)

    def get_by_email(self, email: str) -> Optional[User]:
        """Return the user with the given email, or None if not found."""
        model = (
            self._session.query(UserModel).filter(UserModel.email == email).first()
        )
        if model is None:
            return None
        return self._to_entity(model)

    def update(self, id: int, fields: dict) -> User:
        """Update user fields. Return updated entity."""
        model = self._session.query(UserModel).filter(UserModel.id == id).one()
        allowed_fields = {
            "full_name",
            "email",
            "password_hash",
            "role",
            "is_active",
            "remote_user_id",
            "sync_status",
            "last_login_at",
        }
        for key, value in fields.items():
            if key in allowed_fields:
                setattr(model, key, value)
        self._session.flush()
        self._session.commit()
        self._session.refresh(model)
        return self._to_entity(model)

    def list_active(self) -> list[User]:
        """Return all active users."""
        models = (
            self._session.query(UserModel)
            .filter(UserModel.is_active == True)  # noqa: E712
            .order_by(UserModel.full_name)
            .all()
        )
        return [self._to_entity(m) for m in models]

    def _to_entity(self, model: UserModel) -> User:
        return User(
            id=model.id,
            full_name=model.full_name,
            email=model.email,
            password_hash=model.password_hash,
            role=model.role,
            is_active=model.is_active,
            remote_user_id=model.remote_user_id,
            sync_status=model.sync_status,
            created_at=model.created_at,
            updated_at=model.updated_at,
            last_login_at=model.last_login_at,
        )

    def _to_model(self, entity: User) -> UserModel:
        return UserModel(
            full_name=entity.full_name,
            email=entity.email,
            password_hash=entity.password_hash,
            role=entity.role,
            is_active=entity.is_active,
            remote_user_id=entity.remote_user_id,
            sync_status=entity.sync_status,
        )
