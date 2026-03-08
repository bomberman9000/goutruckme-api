"""
💬 API для форума обсуждения недобросовестных перевозчиков
"""
import html
import re

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.db.database import SessionLocal
from app.models.models import ForumPost, ForumComment, User
from app.services.ai_lawyer_llm import ai_lawyer_llm
from app.core.security import SECRET_KEY, ALGORITHM
from jose import jwt

router = APIRouter()
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_from_token(authorization: Optional[str] = Header(None)):
    """Получить user_id из токена."""
    if not authorization:
        return None
    try:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["id"]
    except:
        return None


def _is_admin(user: User | None) -> bool:
    if not user:
        return False
    role = getattr(user.role, "value", user.role)
    normalized = str(role or "").strip().lower()
    return normalized == "admin" or normalized.endswith("admin")


def _sanitize_forum_text(value: Optional[str]) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = _HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _escape_for_output(value: Optional[str]) -> str:
    return html.escape(str(value or ""), quote=False)


def _safe_preview(value: Optional[str], limit: int) -> str:
    safe = _escape_for_output(value)
    return safe[:limit] + "..." if len(safe) > limit else safe


# ============ SCHEMAS ============

class ForumPostCreate(BaseModel):
    """Создание поста на форуме."""
    title: str
    content: str
    post_type: str = "warning"  # warning / review / discussion / complaint
    target_user_id: Optional[int] = None
    target_company: Optional[str] = None
    target_phone: Optional[str] = None


class ForumCommentCreate(BaseModel):
    """Создание комментария."""
    content: str


# ============ ENDPOINTS ============

@router.post("/create")
def create_post(
    post: ForumPostCreate,
    db: Session = Depends(get_db),
    author_id: Optional[int] = Depends(get_user_from_token)
):
    """Создать пост на форуме."""
    if not author_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    safe_title = _sanitize_forum_text(post.title)
    safe_content = _sanitize_forum_text(post.content)
    safe_target_company = _sanitize_forum_text(post.target_company)
    safe_target_phone = _sanitize_forum_text(post.target_phone)
    if not safe_title or not safe_content:
        raise HTTPException(status_code=422, detail="title и content обязательны")
    
    # Проверка существования пользователя, если указан
    if post.target_user_id:
        target_user = db.query(User).filter(User.id == post.target_user_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # 🤖 AI-Юрист автоматически проверяет пост
    ai_analysis = None
    is_verified = False
    
    try:
        post_data = {
            "title": safe_title,
            "content": safe_content,
            "post_type": post.post_type,
            "target_company": safe_target_company or None,
            "target_phone": safe_target_phone or None
        }
        
        ai_analysis = ai_lawyer_llm.analyze_forum_post(post_data)
        
        # Если AI определил низкий риск - автоматически верифицируем
        if ai_analysis.get("can_publish", False) and ai_analysis.get("risk_score", 100) < 30:
            is_verified = True
    except Exception as e:
        # Не критично, если AI анализ не удался
        pass
    
    new_post = ForumPost(
        author_id=author_id,
        title=safe_title,
        content=safe_content,
        post_type=post.post_type,
        target_user_id=post.target_user_id,
        target_company=safe_target_company or None,
        target_phone=safe_target_phone or None,
        is_verified=is_verified
    )
    
    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    
    message = "Пост создан и автоматически проверен AI-Юристом" if is_verified else "Пост создан и отправлен на модерацию"
    
    return {
        "success": True,
        "post_id": new_post.id,
        "message": message,
        "ai_verified": is_verified,
        "ai_analysis": ai_analysis if ai_analysis else None
    }


@router.get("/list")
def get_posts(
    post_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Получить список постов форума."""
    query = db.query(ForumPost)
    
    # Фильтр по типу
    if post_type:
        query = query.filter(ForumPost.post_type == post_type)
    
    # Поиск
    if search:
        query = query.filter(
            (ForumPost.title.contains(search)) |
            (ForumPost.content.contains(search)) |
            (ForumPost.target_company.contains(search)) |
            (ForumPost.target_phone.contains(search))
        )
    
    # Сначала закреплённые, потом по дате
    posts = query.order_by(
        ForumPost.is_pinned.desc(),
        ForumPost.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    total = query.count()
    
    return {
        "total": total,
        "posts": [
            {
                "id": p.id,
                "author_id": p.author_id,
                "author_name": db.query(User).filter(User.id == p.author_id).first().fullname if db.query(User).filter(User.id == p.author_id).first() else "Аноним",
                "title": _escape_for_output(p.title),
                "content": _safe_preview(p.content, 200),
                "post_type": p.post_type,
                "target_user_id": p.target_user_id,
                "target_company": _escape_for_output(p.target_company) if p.target_company else None,
                "target_phone": _escape_for_output(p.target_phone) if p.target_phone else None,
                "is_verified": p.is_verified,
                "is_pinned": p.is_pinned,
                "views": p.views,
                "likes": p.likes,
                "dislikes": p.dislikes,
                "comments_count": len(p.comments),
                "created_at": p.created_at.isoformat() if p.created_at else None
            }
            for p in posts
        ]
    }


@router.get("/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    """Получить пост с комментариями."""
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    # Увеличиваем просмотры
    post.views += 1
    db.commit()
    
    author = db.query(User).filter(User.id == post.author_id).first()
    
    return {
        "id": post.id,
        "author": {
            "id": author.id if author else None,
            "fullname": _escape_for_output(author.fullname) if author else "Аноним",
            "rating": author.rating if author else 5.0,
            "points": author.points if author else 100
        },
        "title": _escape_for_output(post.title),
        "content": _escape_for_output(post.content),
        "post_type": post.post_type,
        "target_user_id": post.target_user_id,
        "target_company": _escape_for_output(post.target_company) if post.target_company else None,
        "target_phone": _escape_for_output(post.target_phone) if post.target_phone else None,
        "is_verified": post.is_verified,
        "is_pinned": post.is_pinned,
        "views": post.views,
        "likes": post.likes,
        "dislikes": post.dislikes,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "comments": [
            {
                "id": c.id,
                "author_id": c.author_id,
                "author_name": _escape_for_output(db.query(User).filter(User.id == c.author_id).first().fullname) if db.query(User).filter(User.id == c.author_id).first() else "Аноним",
                "content": _escape_for_output(c.content),
                "is_verified": c.is_verified,
                "likes": c.likes,
                "created_at": c.created_at.isoformat() if c.created_at else None
            }
            for c in post.comments
        ]
    }


@router.post("/{post_id}/comment")
def add_comment(
    post_id: int,
    comment: ForumCommentCreate,
    db: Session = Depends(get_db),
    author_id: Optional[int] = Depends(get_user_from_token)
):
    """Добавить комментарий к посту."""
    if not author_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    safe_content = _sanitize_forum_text(comment.content)
    if not safe_content:
        raise HTTPException(status_code=422, detail="content обязателен")

    new_comment = ForumComment(
        post_id=post_id,
        author_id=author_id,
        content=safe_content,
        is_verified=False
    )
    
    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)
    
    return {
        "success": True,
        "comment_id": new_comment.id,
        "message": "Комментарий добавлен"
    }


@router.post("/{post_id}/like")
def like_post(
    post_id: int,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Поставить лайк посту."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    post.likes += 1
    db.commit()
    
    return {"success": True, "likes": post.likes}


@router.post("/{post_id}/dislike")
def dislike_post(
    post_id: int,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Поставить дизлайк посту."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    post.dislikes += 1
    db.commit()
    
    return {"success": True, "dislikes": post.dislikes}


@router.post("/{post_id}/verify")
def verify_post(
    post_id: int,
    db: Session = Depends(get_db),
    admin_id: Optional[int] = Depends(get_user_from_token)
):
    """Верифицировать пост (только для админов)."""
    if not admin_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    admin = db.query(User).filter(User.id == admin_id).first()
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администраторов")
    
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    post.is_verified = True
    db.commit()
    
    return {"success": True, "message": "Пост верифицирован"}


@router.post("/{post_id}/pin")
def pin_post(
    post_id: int,
    db: Session = Depends(get_db),
    admin_id: Optional[int] = Depends(get_user_from_token)
):
    """Закрепить пост (только для админов)."""
    if not admin_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    admin = db.query(User).filter(User.id == admin_id).first()
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администраторов")
    
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    post.is_pinned = True
    db.commit()
    
    return {"success": True, "message": "Пост закреплён"}


@router.get("/search")
def search_posts(
    q: str,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """Поиск по форуму."""
    posts = db.query(ForumPost).filter(
        (ForumPost.title.contains(q)) |
        (ForumPost.content.contains(q)) |
        (ForumPost.target_company.contains(q)) |
        (ForumPost.target_phone.contains(q))
    ).order_by(ForumPost.created_at.desc()).limit(limit).all()
    
    return {
        "query": q,
        "results": [
            {
                "id": p.id,
                "title": _escape_for_output(p.title),
                "content": _safe_preview(p.content, 150),
                "target_company": _escape_for_output(p.target_company) if p.target_company else None,
                "target_phone": _escape_for_output(p.target_phone) if p.target_phone else None,
                "created_at": p.created_at.isoformat() if p.created_at else None
            }
            for p in posts
        ]
    }


@router.get("/{post_id}/ai-analysis")
def get_post_ai_analysis(post_id: int, db: Session = Depends(get_db)):
    """Получить AI-анализ поста."""
    post = db.query(ForumPost).filter(ForumPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    post_data = {
        "title": post.title,
        "content": post.content,
        "post_type": post.post_type,
        "target_company": post.target_company,
        "target_phone": post.target_phone
    }
    
    ai_analysis = ai_lawyer_llm.analyze_forum_post(post_data)
    
    return {
        "post_id": post_id,
        "ai_analysis": ai_analysis,
        "needs_moderation": ai_analysis.get("needs_moderation", False)
    }
