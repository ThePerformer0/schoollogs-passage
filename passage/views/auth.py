"""Authentication views: home dispatch, login, and server-invalidating logout (AUT-01..07)."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from passage.forms import LoginForm
from passage.services.auth import (
    UNIFORM_LOGIN_ERROR,
    authenticate_and_login,
    get_home_url_for_user,
    logout_user,
)


@require_GET
def home_view(request: HttpRequest) -> HttpResponse:
    """Redirect authenticated users to their role screen and guests to login (AUT-06)."""
    if request.user.is_authenticated and request.user.is_active:
        return redirect(get_home_url_for_user(request.user))
    return redirect("passage:login")


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """Authenticate a user with lockout, constant-time failure handling, and uniform errors."""
    if request.user.is_authenticated and request.user.is_active:
        return redirect(get_home_url_for_user(request.user))

    error_message = ""
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            result = authenticate_and_login(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if result.succeeded:
                return redirect(result.redirect_url)
            error_message = result.error_message
        else:
            # Keep the error message strictly uniform even on malformed/empty input (AUT-04).
            error_message = UNIFORM_LOGIN_ERROR
    else:
        form = LoginForm()

    return render(
        request,
        "passage/login.html",
        {"form": form, "error_message": error_message},
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Terminate the current server-side session (POST + CSRF only, AUT-05, SEC-08)."""
    logout_user(request)
    return redirect("passage:login")
