from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import User, UserSymbol


def create_user(db: Session, username: str, ntfy_topic: str | None = None) -> User:
    user = User(username=username, ntfy_topic=ntfy_topic)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User)).scalars())


def add_symbol(db: Session, user_id: int, symbol: str) -> UserSymbol:
    symbol = symbol.upper()
    existing = db.execute(
        select(UserSymbol).where(UserSymbol.user_id == user_id, UserSymbol.symbol == symbol)
    ).scalar_one_or_none()
    if existing:
        existing.is_active = True
        db.commit()
        db.refresh(existing)
        return existing

    entry = UserSymbol(user_id=user_id, symbol=symbol)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def remove_symbol(db: Session, user_id: int, symbol: str) -> bool:
    entry = db.execute(
        select(UserSymbol).where(
            UserSymbol.user_id == user_id, UserSymbol.symbol == symbol.upper(), UserSymbol.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if not entry:
        return False
    entry.is_active = False
    db.commit()
    return True


def list_symbols(db: Session, user_id: int) -> list[UserSymbol]:
    stmt = select(UserSymbol).where(UserSymbol.user_id == user_id, UserSymbol.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def all_active_symbols(db: Session) -> set[str]:
    stmt = select(UserSymbol.symbol).where(UserSymbol.is_active.is_(True)).distinct()
    return set(db.execute(stmt).scalars())


def users_subscribed_to(db: Session, symbol: str) -> list[User]:
    stmt = (
        select(User)
        .join(UserSymbol, UserSymbol.user_id == User.id)
        .where(UserSymbol.symbol == symbol.upper(), UserSymbol.is_active.is_(True), User.is_active.is_(True))
    )
    return list(db.execute(stmt).scalars())
