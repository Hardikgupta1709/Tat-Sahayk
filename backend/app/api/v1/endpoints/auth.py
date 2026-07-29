import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.crud import user as crud_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import (
    Token,
    UserCreate,
    UserResponse,
    UserSignup,
    UserUpdate,
)
from app.services.authentication import (
    generate_unusable_password,
    uses_legacy_google_password,
)


router = APIRouter()
logger = logging.getLogger(__name__)


class GoogleLoginRequest(BaseModel):
    credential: str


class OTPRequest(BaseModel):
    phone: str


class OTPVerify(BaseModel):
    phone: str
    otp: str


def issue_access_token(user: User) -> dict[str, str]:
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        ),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


def raise_invalid_credentials() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/google", response_model=Token)
def google_login(
    payload: GoogleLoginRequest,
    db: Session = Depends(get_db),
):
    """Verify a Google ID token and sign in a citizen account."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login is not configured",
        )

    try:
        idinfo = id_token.verify_oauth2_token(
            payload.credential,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        ) from exc

    raw_email = idinfo.get("email")

    if (
        not raw_email
        or idinfo.get("email_verified") is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google email is not verified",
        )

    email = raw_email.strip().lower()
    name = (
        idinfo.get("name")
        or email.split("@", 1)[0]
    )

    if email.endswith("@tatsahayk.gov.in"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Government accounts cannot use Google login. "
                "Please use the administrator portal."
            ),
        )

    user = crud_user.get_user_by_email(
        db,
        email=email,
    )

    if not user:
        user = crud_user.create_user(
            db,
            user=UserCreate(
                email=email,
                full_name=name,
                password=generate_unusable_password(),
                role="citizen",
            ),
        )

    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Administrator accounts cannot use Google "
                "login. Please use the administrator portal."
            ),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    if uses_legacy_google_password(
        user.hashed_password
    ):
        user.hashed_password = get_password_hash(
            generate_unusable_password()
        )
        db.commit()
        db.refresh(user)

    return issue_access_token(user)


@router.post(
    "/signup",
    response_model=UserResponse,
)
def create_user(
    user_in: UserSignup,
    db: Session = Depends(get_db),
):
    user = crud_user.get_user_by_email(
        db,
        email=user_in.email,
    )

    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    citizen = UserCreate(
        email=user_in.email,
        full_name=user_in.full_name,
        password=user_in.password,
        role="citizen",
    )

    return crud_user.create_user(
        db,
        user=citizen,
    )


@router.post("/login", response_model=Token)
def login(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    user = crud_user.get_user_by_email(
        db,
        email=form_data.username,
    )

    if (
        not user
        or uses_legacy_google_password(
            user.hashed_password
        )
        or not verify_password(
            form_data.password,
            user.hashed_password,
        )
    ):
        raise_invalid_credentials()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    if user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Administrator accounts cannot use the "
                "citizen login."
            ),
        )

    return issue_access_token(user)


@router.post("/admin-login", response_model=Token)
def admin_login(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Authenticate an active administrator account."""
    user = crud_user.get_user_by_email(
        db,
        email=form_data.username,
    )

    if (
        not user
        or uses_legacy_google_password(
            user.hashed_password
        )
        or not verify_password(
            form_data.password,
            user.hashed_password,
        )
    ):
        raise_invalid_credentials()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This portal is for government administrators "
                "only. Please use the citizen login."
            ),
        )

    if not user.profile_photo:
        user.profile_photo = "/Admin DP.jpeg"
        db.commit()
        db.refresh(user)

    return issue_access_token(user)


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_me(
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    update_data = data.model_dump(
        exclude_unset=True
    )

    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)

    return current_user


@router.patch("/update-location")
def update_user_location(
    location_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    """Update district and state for location-based alerts."""
    district = location_data.get("district")
    state_name = location_data.get("state")

    if not district or not state_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both district and state are required",
        )

    current_user.district = district
    current_user.state = state_name
    db.commit()
    db.refresh(current_user)

    return {
        "message": "Location updated successfully",
        "district": district,
        "state": state_name,
    }


@router.post("/send-otp")
def send_otp(
    request: OTPRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    """Send an OTP through the currently configured AWS SNS path."""
    from app.services.aws_services import (
        generate_otp,
        send_otp_sms,
    )

    otp = generate_otp()
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=10)
    )

    current_user.phone = request.phone
    current_user.otp_code = otp
    current_user.otp_expires_at = expires_at
    current_user.phone_verified = False

    success = send_otp_sms(
        request.phone,
        otp,
    )

    if not success:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Phone verification is temporarily "
                "unavailable"
            ),
        )

    db.commit()

    return {
        "message": "OTP sent successfully",
        "expires_in_minutes": 10,
    }


@router.post("/verify-otp")
def verify_otp(
    request: OTPVerify,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    """Verify the current OTP and mark the phone as verified."""
    if (
        not current_user.otp_code
        or current_user.otp_code != request.otp
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )

    if (
        not current_user.otp_expires_at
        or current_user.otp_expires_at
        < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "OTP has expired. Please request a new one."
            ),
        )

    if current_user.phone != request.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number mismatch",
        )

    current_user.phone_verified = True
    current_user.otp_code = None
    current_user.otp_expires_at = None
    db.commit()
    db.refresh(current_user)

    return {
        "message": "Phone verified successfully",
        "phone_verified": True,
    }


@router.delete("/me")
def delete_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(
        deps.get_current_user
    ),
):
    """Permanently delete a citizen and their owned content."""
    from app.models.comment import Comment
    from app.models.confirmation import (
        ReportConfirmation,
    )
    from app.models.report import Report

    if current_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Administrator accounts cannot be deleted "
                "through this endpoint."
            ),
        )

    try:
        db.query(ReportConfirmation).filter(
            ReportConfirmation.user_id
            == current_user.id
        ).delete(synchronize_session=False)

        db.query(Comment).filter(
            Comment.user_id == current_user.id
        ).delete(synchronize_session=False)

        db.query(Report).filter(
            Report.user_id == current_user.id
        ).delete(synchronize_session=False)

        db.delete(current_user)
        db.commit()

        return {
            "message": "Account deleted successfully"
        }
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Failed to delete user account"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="Failed to delete account",
        ) from exc
