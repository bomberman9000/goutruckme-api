"""
⚠️ API для системы претензий и жалоб
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)
from app.models.models import Complaint, User, Load
from app.services.rating_system import rating_system
from app.services.ai_lawyer_llm import ai_lawyer_llm
from app.services.geo import canonicalize_city_name
from app.trust.service import recalc_company_trust
from app.core.security import SECRET_KEY, ALGORITHM
from jose import jwt

router = APIRouter()


def _recalc_trust_safely(db: Session, *company_ids: int) -> None:
    seen: set[int] = set()
    for company_id in company_ids:
        if not company_id or company_id in seen:
            continue
        seen.add(company_id)
        try:
            recalc_company_trust(db, int(company_id))
        except Exception as e:
            logger.warning("recalc_company_trust failed for company_id=%s: %s", company_id, e)


def _normalize_role(role) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    return value


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


# ============ SCHEMAS ============

class ComplaintCreate(BaseModel):
    """Создание претензии."""
    defendant_id: int  # На кого жалуются
    load_id: Optional[int] = None
    title: str
    description: str
    complaint_type: str = "general"  # general / fraud / delay / damage / payment
    evidence: Optional[str] = None


class ComplaintResponse(BaseModel):
    """Ответ на претензию (для админа)."""
    complaint_id: int
    status: str  # reviewed / resolved / rejected
    admin_response: str


# ============ ENDPOINTS ============

@router.post("/create")
def create_complaint(
    complaint: ComplaintCreate,
    db: Session = Depends(get_db),
    complainant_id: Optional[int] = Depends(get_user_from_token)
):
    """Создать претензию."""
    if not complainant_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    # Проверка существования пользователя, на которого жалуются
    defendant = db.query(User).filter(User.id == complaint.defendant_id).first()
    if not defendant:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Проверка, что не жалуется на себя
    if complainant_id == complaint.defendant_id:
        raise HTTPException(status_code=400, detail="Нельзя пожаловаться на себя")
    
    # Проверка заявки, если указана
    if complaint.load_id:
        load = db.query(Load).filter(Load.id == complaint.load_id).first()
        if not load:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Создание претензии
    new_complaint = Complaint(
        complainant_id=complainant_id,
        defendant_id=complaint.defendant_id,
        load_id=complaint.load_id,
        title=complaint.title,
        description=complaint.description,
        complaint_type=complaint.complaint_type,
        evidence=complaint.evidence,
        status="pending"
    )
    
    db.add(new_complaint)
    db.commit()
    db.refresh(new_complaint)
    
    # 🤖 AI-Юрист автоматически анализирует претензию
    ai_analysis = None
    try:
        defendant = db.query(User).filter(User.id == complaint.defendant_id).first()
        load_data = None
        if complaint.load_id:
            load = db.query(Load).filter(Load.id == complaint.load_id).first()
            if load:
                load_data = {
                    "from_city": canonicalize_city_name(load.from_city),
                    "to_city": canonicalize_city_name(load.to_city),
                    "price": load.price,
                    "weight": load.weight,
                    "volume": load.volume
                }
        
        # Анализ претензии AI-Юристом
        analysis_data = {
            "complaint_title": complaint.title,
            "complaint_description": complaint.description,
            "complaint_type": complaint.complaint_type,
            "defendant_rating": defendant.rating if defendant else 5.0,
            "defendant_points": defendant.points if defendant else 100,
            "defendant_complaints": defendant.complaints if defendant else 0,
            "load_data": load_data
        }
        
        ai_analysis = ai_lawyer_llm.analyze_complaint(analysis_data)
        
        # Если AI определил высокий риск - автоматически подтверждаем
        if ai_analysis.get("risk_level") in ["high_risk", "critical"]:
            new_complaint.status = "reviewed"
            risk_score = ai_analysis.get("risk_score", 0)
            summary = ai_analysis.get("summary", "Высокий риск подтверждён автоматически")
            new_complaint.admin_response = f"🤖 AI-Юрист автоматически проверил претензию. Риск-скор: {risk_score}/100. {summary}"
            db.commit()
            
            # Автоматическое снятие баллов
            rating_system.on_complaint(db, complaint.defendant_id, complaint.complaint_type)
            
    except Exception as e:
        # Не критично, если AI анализ не удался
        pass

    _recalc_trust_safely(db, complaint.defendant_id, complainant_id)
    
    return {
        "success": True,
        "complaint_id": new_complaint.id,
        "message": "Претензия создана и отправлена на рассмотрение",
        "ai_analysis": ai_analysis if ai_analysis else None,
        "auto_reviewed": ai_analysis and ai_analysis.get("risk_level") in ["high_risk", "critical"] if ai_analysis else False
    }


@router.get("/my-complaints")
def get_my_complaints(
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Получить мои претензии (которые я подал)."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    complaints = db.query(Complaint).filter(
        Complaint.complainant_id == user_id
    ).order_by(Complaint.created_at.desc()).all()
    
    return {
        "complaints": [
            {
                "id": c.id,
                "defendant_id": c.defendant_id,
                "defendant_name": db.query(User).filter(User.id == c.defendant_id).first().fullname if db.query(User).filter(User.id == c.defendant_id).first() else "Неизвестно",
                "load_id": c.load_id,
                "title": c.title,
                "description": c.description,
                "complaint_type": c.complaint_type,
                "status": c.status,
                "admin_response": c.admin_response,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None
            }
            for c in complaints
        ]
    }


@router.get("/against-me")
def get_complaints_against_me(
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Получить претензии против меня."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    complaints = db.query(Complaint).filter(
        Complaint.defendant_id == user_id
    ).order_by(Complaint.created_at.desc()).all()
    
    return {
        "complaints": [
            {
                "id": c.id,
                "complainant_id": c.complainant_id,
                "complainant_name": db.query(User).filter(User.id == c.complainant_id).first().fullname if db.query(User).filter(User.id == c.complainant_id).first() else "Неизвестно",
                "load_id": c.load_id,
                "title": c.title,
                "description": c.description,
                "complaint_type": c.complaint_type,
                "status": c.status,
                "admin_response": c.admin_response,
                "created_at": c.created_at.isoformat() if c.created_at else None
            }
            for c in complaints
        ]
    }


@router.get("/user/{user_id}")
def get_user_complaints(user_id: int, db: Session = Depends(get_db)):
    """Получить все претензии против пользователя (публичная информация)."""
    complaints = db.query(Complaint).filter(
        Complaint.defendant_id == user_id,
        Complaint.status.in_(["reviewed", "resolved"])  # Только рассмотренные
    ).order_by(Complaint.created_at.desc()).limit(20).all()
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    return {
        "user_id": user_id,
        "user_name": user.fullname,
        "total_complaints": len(complaints),
        "complaints": [
            {
                "id": c.id,
                "title": c.title,
                "complaint_type": c.complaint_type,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None
            }
            for c in complaints
        ]
    }


@router.post("/{complaint_id}/resolve")
def resolve_complaint(
    complaint_id: int,
    response: ComplaintResponse,
    db: Session = Depends(get_db),
    admin_id: Optional[int] = Depends(get_user_from_token)
):
    """Рассмотреть претензию (только для админов)."""
    if not admin_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    admin = db.query(User).filter(User.id == admin_id).first()
    if not admin or _normalize_role(admin.role) != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Претензия не найдена")
    
    complaint.status = response.status
    complaint.admin_response = response.admin_response
    complaint.resolved_at = datetime.utcnow()
    
    # Если претензия подтверждена, дополнительно снимаем баллы
    if response.status == "resolved":
        try:
            rating_system.on_complaint(db, complaint.defendant_id, complaint.complaint_type)
        except Exception as e:
            logger.warning("rating_system.on_complaint failed: %s", e)
    
    db.commit()
    _recalc_trust_safely(db, complaint.defendant_id, complaint.complainant_id)
    
    return {
        "success": True,
        "complaint_id": complaint_id,
        "status": complaint.status,
        "message": "Претензия рассмотрена"
    }


@router.get("/stats")
def get_complaint_stats(db: Session = Depends(get_db)):
    """Общая статистика по претензиям."""
    total = db.query(Complaint).count()
    pending = db.query(Complaint).filter(Complaint.status == "pending").count()
    resolved = db.query(Complaint).filter(Complaint.status == "resolved").count()
    rejected = db.query(Complaint).filter(Complaint.status == "rejected").count()
    
    by_type = {}
    types = ["general", "fraud", "delay", "damage", "payment"]
    for t in types:
        by_type[t] = db.query(Complaint).filter(Complaint.complaint_type == t).count()
    
    return {
        "total": total,
        "pending": pending,
        "resolved": resolved,
        "rejected": rejected,
        "by_type": by_type
    }


@router.get("/{complaint_id}/ai-analysis")
def get_complaint_ai_analysis(complaint_id: int, db: Session = Depends(get_db)):
    """Получить AI-анализ претензии."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Претензия не найдена")
    
    defendant = db.query(User).filter(User.id == complaint.defendant_id).first()
    load_data = None
    
    if complaint.load_id:
        load = db.query(Load).filter(Load.id == complaint.load_id).first()
        if load:
            load_data = {
                "from_city": canonicalize_city_name(load.from_city),
                "to_city": canonicalize_city_name(load.to_city),
                "price": load.price,
                "weight": load.weight,
                "volume": load.volume
            }
    
    analysis_data = {
        "complaint_title": complaint.title,
        "complaint_description": complaint.description,
        "complaint_type": complaint.complaint_type,
        "defendant_rating": defendant.rating if defendant else 5.0,
        "defendant_points": defendant.points if defendant else 100,
        "defendant_complaints": defendant.complaints if defendant else 0,
        "load_data": load_data
    }
    
    ai_analysis = ai_lawyer_llm.analyze_complaint(analysis_data)
    
    return {
        "complaint_id": complaint_id,
        "ai_analysis": ai_analysis,
        "recommendation": ai_analysis.get("auto_action", "review")
    }
