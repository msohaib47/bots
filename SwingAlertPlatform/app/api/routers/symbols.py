from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import SymbolCreate, UserSymbolOut
from app.users import service

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.post("", response_model=UserSymbolOut, status_code=201)
def add_symbol(payload: SymbolCreate, db: Session = Depends(get_db)):
    if not service.get_user(db, payload.user_id):
        raise HTTPException(404, "User not found")
    return service.add_symbol(db, payload.user_id, payload.symbol)


@router.get("", response_model=list[UserSymbolOut])
def list_symbols(user_id: int, db: Session = Depends(get_db)):
    if not service.get_user(db, user_id):
        raise HTTPException(404, "User not found")
    return service.list_symbols(db, user_id)


@router.delete("/{symbol}", status_code=204)
def remove_symbol(symbol: str, user_id: int, db: Session = Depends(get_db)):
    if not service.remove_symbol(db, user_id, symbol):
        raise HTTPException(404, "Symbol not found on that user's watchlist")
