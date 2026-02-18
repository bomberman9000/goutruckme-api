from datetime import datetime
from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base schema with common configuration."""
    
    model_config = ConfigDict(from_attributes=True)


class TimestampSchema(BaseSchema):
    """Schema with timestamp fields."""
    
    created_at: datetime
    updated_at: datetime


class ResponseSchema(BaseSchema):
    """Standard API response schema."""
    
    success: bool = True
    message: str = "Operation completed successfully"


class PaginatedResponse(BaseSchema):
    """Paginated response schema."""
    
    items: list
    total: int
    page: int
    size: int
    pages: int




