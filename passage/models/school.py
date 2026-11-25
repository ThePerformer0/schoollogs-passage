"""Core organizational models: schools, custom users, students, and parent links."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class School(models.Model):
    """Educational institution providing tenant isolation (GES-05)."""

    name = models.CharField("nom", max_length=150, unique=True)
    code = models.SlugField("code", max_length=50, unique=True)
    timezone = models.CharField("fuseau horaire", max_length=64, default="Africa/Douala")
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "établissement"
        verbose_name_plural = "établissements"

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    """Custom user carrying application role and school scope (AUT-01, GES-05)."""

    class Role(models.TextChoices):
        AGENT = "agent", "Agent de portail"
        MANAGER = "manager", "Responsable"
        PARENT = "parent", "Parent"

    full_name = models.CharField("nom complet", max_length=150, blank=True)
    role = models.CharField(
        "rôle",
        max_length=20,
        choices=Role.choices,
        default=Role.PARENT,
        db_index=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="établissement",
    )

    class Meta:
        ordering = ["username"]
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=["agent", "manager", "parent"]),
                name="ck_user_valid_role",
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_superuser=True)
                    | Q(role="parent")
                    | Q(school__isnull=False)
                ),
                name="ck_user_school_required_for_staff_roles",
            ),
        ]

    @property
    def display_name(self) -> str:
        if self.full_name.strip():
            return self.full_name.strip()
        joined = self.get_full_name().strip()
        return joined or self.username

    def clean(self) -> None:
        super().clean()
        if not self.is_superuser and self.role in {self.Role.AGENT, self.Role.MANAGER}:
            if self.school_id is None:
                raise ValidationError(
                    {"school": "Un responsable ou un agent doit être rattaché à un établissement."}
                )

    def __str__(self) -> str:
        return f"{self.display_name} ({self.get_role_display()})"


class Student(models.Model):
    """Minor student tracked via a rotating digital badge (GES-01)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        INACTIVE = "inactive", "Désactivé"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="students",
        verbose_name="établissement",
    )
    first_name = models.CharField("prénom", max_length=100)
    last_name = models.CharField("nom", max_length=100)
    classroom = models.CharField("classe", max_length=64, db_index=True)
    status = models.CharField(
        "état",
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    parents = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ParentStudentLink",
        related_name="children",
        verbose_name="parents",
    )
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("mis à jour le", auto_now=True)

    class Meta:
        ordering = ["classroom", "last_name", "first_name"]
        verbose_name = "élève"
        verbose_name_plural = "élèves"
        indexes = [
            models.Index(fields=["school", "classroom"], name="idx_student_school_class"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["active", "inactive"]),
                name="ck_student_valid_status",
            ),
        ]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:
        return f"{self.full_name} ({self.classroom})"


class ParentStudentLink(models.Model):
    """Many-to-many link between parent accounts and students (GES-02)."""

    parent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_links",
        verbose_name="parent",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="parent_links",
        verbose_name="élève",
    )
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        verbose_name = "lien parent-élève"
        verbose_name_plural = "liens parent-élève"
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "student"],
                name="uniq_parent_student_link",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.parent_id and getattr(self.parent, "role", None) != User.Role.PARENT:
            raise ValidationError(
                {"parent": "Seul un utilisateur ayant le rôle Parent peut être rattaché à un élève."}
            )

    def __str__(self) -> str:
        return f"{self.parent} -> {self.student}"
