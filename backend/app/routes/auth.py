from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.router.common import ErrorCode

from app.services.auth_providers import build_authorize_url, handle_callback
from app.core.auth import get_user_manager, auth_backend
from app.core.login_throttle import throttle_login

router = APIRouter()


@router.post(
    "/auth/ldap/login",
    name="auth:ldap.login",
    tags=["auth"],
    # ★Same rate limit as the local form, for the same reason and by the same
    # counter. A second unlimited password door beside a limited one is not a
    # second door, it is a way around the first.
    dependencies=[Depends(throttle_login)],
)
async def ldap_login(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),
    strategy=Depends(auth_backend.get_strategy),
):
    """Sign in against the LDAP directory, and only the directory.

    ★★★Deliberately a SEPARATE door from POST /api/auth/jwt/login, which now
    never talks to LDAP. The single combined path refused every ordinary local
    member whenever the directory was enabled and reachable — the directory
    answers "not in the directory" and "wrong password" with the same word, and
    the local fallback was granted to superusers only. Measured on the running
    instance: same user, same password, LDAP up → 400, LDAP down → 200. See
    `UserManager.authenticate` for the whole story.

    Request and response mirror /api/auth/jwt/login exactly, so a client can
    post the same form to either:

        POST  application/x-www-form-urlencoded   username=<addr>&password=<pw>
        200   {"access_token": "<jwt>", "token_type": "bearer"}
        400   {"detail": "LOGIN_BAD_CREDENTIALS"}

    ★★★ONE error, for every way of failing: not in the directory, wrong
    password, directory unreachable, LDAP not enabled on this instance. Open
    WebUI returns "User not found in the LDAP server" here, which hands an
    unauthenticated caller a membership oracle against the customer's Active
    Directory. The real reason is recorded in the audit log instead — see
    `UserManager._record_login_failure`.
    """
    user = await user_manager.authenticate_directory(credentials)

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.LOGIN_BAD_CREDENTIALS,
        )

    # ★The same backend and strategy the local route uses, not a parallel token
    # format. Two ways to mint a session is two things to get wrong at
    # expiry, revocation and rotation.
    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response


@router.get("/auth/{provider}/authorize")
async def authorize(provider: str, request: Request) -> JSONResponse:
    return await build_authorize_url(provider, request)


@router.get("/auth/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    user_manager=Depends(get_user_manager),
) -> RedirectResponse:
    return await handle_callback(
        provider, request, code, state, user_manager,
        error=error, error_description=error_description,
    )


