"""Checkpoints and student itinerary windows (GES-03, GES-04, ITI-01..03)."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from passage.models.school import School, Student

VALID_ISO_WEEKDAYS = frozenset({1, 2, 3, 4, 5, 6, 7})


class Checkpoint(models.Model):
    """Physical control point where badges are scanned (entry gate, exit gate, bus)."""

    class CheckpointType(models.TextChoices):
        ENTRY_GATE = "entry_gate", "Portail d'entrée"
        EXIT_GATE = "exit_gate", "Portail de sortie"
        BUS = "bus", "Autobus"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="checkpoints",
        verbose_name="établissement",
    )
    label = models.CharField("libellé", max_length=120)
    checkpoint_type = models.CharField(
        "type",
        max_length=20,
        choices=CheckpointType.choices,
        db_index=True,
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["school", "label"]
        verbose_name = "point de contrôle"
        verbose_name_plural = "points de contrôle"
        constraints = [
            models.UniqueConstraint(
                fields=["school", "label"],
                name="uniq_checkpoint_label_per_school",
            ),
            models.CheckConstraint(
                condition=Q(checkpoint_type__in=["entry_gate", "exit_gate", "bus"]),
                name="ck_checkpoint_valid_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.get_checkpoint_type_display()})"


class RouteWindow(models.Model):
    """Ordered checkpoint with expected time window and applicable ISO weekdays."""

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="route_windows",
        verbose_name="élève",
    )
    checkpoint = models.ForeignKey(
        Checkpoint,
        on_delete=models.PROTECT,
        related_name="route_windows",
        verbose_name="point de contrôle",
    )
    order = models.PositiveSmallIntegerField("ordre", default=1)
    start_time = models.TimeField("heure de début")
    end_time = models.TimeField("heure de fin")
    # Comma-separated ISO weekdays (1=Monday .. 7=Sunday), e.g. "1,2,3,4,5".
    weekdays = models.CharField("jours d'application", max_length=20, default="1,2,3,4,5")
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["student", "order", "start_time"]
        verbose_name = "fenêtre d'itinéraire"
        verbose_name_plural = "fenêtres d'itinéraire"
        indexes = [
            models.Index(fields=["student", "is_active"], name="idx_route_student_active"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="ck_route_window_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(order__gte=1),
                name="ck_route_window_positive_order",
            ),
        ]

    def parsed_weekdays(self) -> set[int]:
        if not self.weekdays or not self.weekdays.strip():
            return set()
        result: set[int] = set()
        for raw_part in self.weekdays.split(","):
            part = raw_part.strip()
            if not part:
                continue
            day = int(part)
            if day not in VALID_ISO_WEEKDAYS:
                raise ValueError(f"Invalid ISO weekday: {day}")
            result.add(day)
        return result

    def applies_to_weekday(self, iso_weekday: int) -> bool:
        return iso_weekday in self.parsed_weekdays()

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "L'heure de fin doit être postérieure à l'heure de début."

        if (
            self.student_id
            and self.checkpoint_id
            and self.student.school_id != self.checkpoint.school_id
        ):
            errors["checkpoint"] = (
                "Le point de contrôle doit appartenir au même établissement que l'élève."
            )

        try:
            days = self.parsed_weekdays()
            if not days:
                errors["weekdays"] = "Indiquez au moins un jour d'application (1 à 7)."
            else:
                self.weekdays = ",".join(str(day) for day in sorted(days))
        except ValueError:
            errors["weekdays"] = (
                "Les jours d'application doivent être des numéros ISO séparés par des virgules (1 à 7)."
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return (
            f"{self.student} — #{self.order} {self.checkpoint.label} "
            f"({self.start_time:%H:%M}-{self.end_time:%H:%M})"
        )
