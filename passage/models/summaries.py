"""AI-assisted daily class summaries and LLM cost/call tracking (SYN-01..12)."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from passage.models.school import School


class DailySummary(models.Model):
    """Persisted daily summary for a classroom, with full provenance (SYN-05, SYN-10)."""

    class Origin(models.TextChoices):
        MODEL = "model", "Modèle"
        TEMPLATE = "template", "Gabarit"

    class GenerationPath(models.TextChoices):
        MODELE = "modele", "Modèle"
        MODELE_APRES_RELANCE = "modele_apres_relance", "Modèle après relance"
        GABARIT_APRES_ECHEC = "gabarit_apres_echec", "Gabarit après échec"
        GABARIT_SANS_CLE = "gabarit_sans_cle", "Gabarit sans clé"
        GABARIT_SERVICE_INDISPONIBLE = (
            "gabarit_service_indisponible",
            "Gabarit service indisponible",
        )
        GABARIT_PLAFOND_ATTEINT = "gabarit_plafond_atteint", "Gabarit plafond atteint"
        CACHE = "cache", "Cache"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="daily_summaries",
        verbose_name="établissement",
    )
    classroom = models.CharField("classe", max_length=64, db_index=True)
    date = models.DateField("date", db_index=True)
    computed_metrics = models.JSONField("chiffres calculés")
    metrics_hash = models.CharField("empreinte de déduplication", max_length=64, db_index=True)
    prompt_version = models.CharField("version de consigne", max_length=32, default="v1")
    model_name = models.CharField("modèle", max_length=80, blank=True)
    summary_text = models.TextField("texte produit")
    origin = models.CharField("origine", max_length=20, choices=Origin.choices)
    generation_path = models.CharField(
        "chemin emprunté",
        max_length=40,
        choices=GenerationPath.choices,
        db_index=True,
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_summaries",
        verbose_name="demandée par",
    )
    created_at = models.DateTimeField("créée le", default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "synthèse quotidienne"
        verbose_name_plural = "synthèses quotidiennes"
        indexes = [
            models.Index(
                fields=["school", "classroom", "date"],
                name="idx_summary_school_class_date",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(origin__in=["model", "template"]),
                name="ck_daily_summary_valid_origin",
            ),
            models.CheckConstraint(
                condition=Q(
                    generation_path__in=[
                        "modele",
                        "modele_apres_relance",
                        "gabarit_apres_echec",
                        "gabarit_sans_cle",
                        "gabarit_service_indisponible",
                        "gabarit_plafond_atteint",
                        "cache",
                    ]
                ),
                name="ck_daily_summary_valid_path",
            ),
        ]

    def __str__(self) -> str:
        return f"Synthèse {self.classroom} ({self.date}) [{self.generation_path}]"


class ModelCallLog(models.Model):
    """Per-call audit and cost accounting for external LLM invocations (SYN-07, SYN-08)."""

    class Result(models.TextChoices):
        SUCCESS = "success", "Succès"
        INVALID_OUTPUT = "invalid_output", "Sortie non conforme"
        ERROR = "error", "Erreur service"
        TIMEOUT = "timeout", "Délai dépassé"
        BLOCKED_LIMIT = "blocked_limit", "Plafond atteint"

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="model_call_logs",
        verbose_name="établissement",
    )
    called_at = models.DateTimeField("horodatage", default=timezone.now, db_index=True)
    call_date = models.DateField("date d'appel", db_index=True)
    model_name = models.CharField("modèle", max_length=80)
    input_tokens = models.PositiveIntegerField("jetons entrants", default=0)
    output_tokens = models.PositiveIntegerField("jetons sortants", default=0)
    estimated_cost_usd = models.DecimalField(
        "coût estimé (USD)",
        max_digits=10,
        decimal_places=6,
        default=Decimal("0"),
    )
    result = models.CharField("résultat", max_length=24, choices=Result.choices)
    error_code = models.CharField("code d'erreur", max_length=64, blank=True)

    class Meta:
        ordering = ["-called_at"]
        verbose_name = "appel au modèle IA"
        verbose_name_plural = "appels au modèle IA"
        constraints = [
            models.CheckConstraint(
                condition=Q(estimated_cost_usd__gte=Decimal("0")),
                name="ck_model_call_non_negative_cost",
            ),
            models.CheckConstraint(
                condition=Q(
                    result__in=[
                        "success",
                        "invalid_output",
                        "error",
                        "timeout",
                        "blocked_limit",
                    ]
                ),
                name="ck_model_call_valid_result",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.model_name} ({self.result}) @ {self.called_at:%Y-%m-%d %H:%M}"
