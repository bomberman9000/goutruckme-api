from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
import re


class RegisterRequest(BaseModel):
    organization_type: str
    inn: str
    organization_name: str
    phone: str
    password: str
    role: str = "forwarder"
    email: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    bank_bik: Optional[str] = None
    bank_ks: Optional[str] = None
    fullname: Optional[str] = None
    company: Optional[str] = None

    @field_validator("inn")
    @classmethod
    def validate_inn(cls, v: str) -> str:
        if not re.match(r'^\d+$', v):
            raise ValueError('ИНН должен содержать только цифры')
        return v

    @field_validator("organization_type")
    @classmethod
    def validate_organization_type(cls, v: str) -> str:
        if v not in ['ИП', 'ООО']:
            raise ValueError('Тип организации должен быть "ИП" или "ООО"')
        return v

    @model_validator(mode="after")
    def validate_inn_length(self):
        if self.organization_type == 'ИП' and len(self.inn) != 12:
            raise ValueError('ИНН для ИП должен содержать 12 цифр')
        if self.organization_type == 'ООО' and len(self.inn) != 10:
            raise ValueError('ИНН для ООО должен содержать 10 цифр')
        return self

    @field_validator("role")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        role = (v or "").strip().lower()
        aliases = {"shipper": "client", "expeditor": "forwarder"}
        role = aliases.get(role, role)
        if role not in {"carrier", "client", "forwarder"}:
            raise ValueError('role должен быть одним из: carrier, client, forwarder')
        return role


class LoginRequest(BaseModel):
    phone: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
