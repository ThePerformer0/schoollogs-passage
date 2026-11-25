"""Authentication form with strict server-side input validation (AUT-01, SEC-06)."""

from __future__ import annotations

from django import forms


class LoginForm(forms.Form):
    """Standard username + password login form."""

    username = forms.CharField(
        label="Identifiant",
        max_length=150,
        strip=True,
        widget=forms.TextInput(attrs={"autocomplete": "username", "required": True}),
    )
    password = forms.CharField(
        label="Mot de passe",
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "required": True}
        ),
    )
