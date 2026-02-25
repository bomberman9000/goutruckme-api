from datetime import datetime, timedelta
from hashlib import sha256
from jose import JWTError, jwt
from fastapi import HTTPException, Request
from src.core.config import settings

SECRET_KEY = settings.secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

def hash_password(password: str) -> str:
    return sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

async def get_current_admin(request: Request):
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/admin/login"}
        )
    payload = verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/admin/login"}
        )
    return payload

ADMIN_PASSWORD_HASH = hash_password(settings.admin_password)
