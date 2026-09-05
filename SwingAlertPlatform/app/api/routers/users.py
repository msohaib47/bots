from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import UserCreate, UserOut
from app.users import service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    if service.get_user_by_username(db, payload.username):
        raise HTTPException(409, f"Username '{payload.username}' already exists")
    return service.create_user(db, payload.username, payload.ntfy_topic)


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return service.list_users(db)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = service.get_user(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user
