"""
SchoolLogs · Module « Passage » · Lot 4 — suite de tests boîte noire.

Périmètre :
  1. Notifications sortantes aux parents (OutgoingMessage), plafond journalier par élève.
  2. Synthèse du jour assistée par IA (/responsable/synthese/) : métriques déterministes,
     fidélité numérique, relance unique, repli gabarit, plafonds d'appels et de coût, cache,
     confidentialité du payload, non-fuite de la clé API, contrôle d'accès, CSRF.

Règles respectées : ORM public + routes HTTP publiques + commande ``run_scheduler --once`` ;
passerelle IA simulée uniquement via ``passage.services.summaries.get_llm_gateway`` ;
aucun appel réseau ; aucune fonction interne importée ; horloge simulée via
``django.utils.timezone.now``.

Exécution :
    python manage.py test passage.tests.test_lot4_blackbox --verbosity=2
"""

import copy
import hashlib
import hmac
import json
import math
import re
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import StringIO
from unittest import mock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import models
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils.html import escape

from passage.models import (
    Anomaly,
    AuditLog,
    Badge,
    Checkpoint,
    DailySummary,
    ModelCallLog,
    OutgoingMessage,
    ParentStudentLink,
    Passage,
    RouteWindow,
    School,
    Student,
)
from passage.services.gateways.llm_gateway import (
    LLMResponse,
    LLMServiceError,
    LLMTimeoutError,
)

try:  # le contrat place User dans passage.models ; repli sur le modèle d'authentification
    from passage.models import User
except ImportError:  # pragma: no cover
    User = get_user_model()


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DOUALA = ZoneInfo("Africa/Douala")
MONDAY = date(2026, 11, 23)
TUESDAY = date(2026, 11, 24)
SUNDAY = date(2026, 11, 22)

URL_SYNC = "/api/sync/"
URL_SUMMARY = "/responsable/synthese/"

API_KEY = "sk-lot4-LEAKCANARY-8c41d2e9b7f3"
KEY_FRAGMENT = "LEAKCANARY-8c41d2e9b7f3"

AI_SETTINGS = dict(
    PASSAGE_AI_API_KEY=API_KEY,
    PASSAGE_AI_MODEL_NAME="gemini-lot4-test",
    PASSAGE_AI_DAILY_CALL_LIMIT=50,
    PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("5.00"),
    PASSAGE_AI_INPUT_COST_PER_1M_USD=Decimal("0.15"),
    PASSAGE_AI_OUTPUT_COST_PER_1M_USD=Decimal("0.60"),
    PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY=3,
)

DEFAULT_USER = object()  # sentinelle : « le responsable de l'établissement A »

# Textes de synthèse (classe de référence du contrat : 4 / 3 / 1 / 1 / 1 / ["14 h 10"])
GOOD_TEXT = (
    "Ce lundi, 3 élèves sont arrivés, 1 est absent, 1 est arrivé en retard "
    "et 1 est sorti hors plage horaire à 14 h 10."
)
GOOD_TEXT_AFTER_ARRIVAL = (
    "Ce lundi, 4 élèves sont arrivés, aucune absence, 1 est arrivé en retard "
    "et 1 est sorti hors plage horaire à 14 h 10."
)
CALM_TEXT = (
    "2 élèves sont présents, aucun retard, aucune absence et aucune sortie hors plage horaire."
)

LIES = {
    "chiffre_invente": (
        "Ce lundi, 3 élèves sont arrivés, 1 est absent, 1 est arrivé en retard, "
        "1 est sorti hors plage horaire à 14 h 10 et 7 incidents sont signalés."
    ),
    "heure_inventee": (
        "Ce lundi, 3 élèves sont arrivés, 1 est absent, 1 est arrivé en retard "
        "et 1 est sorti hors plage horaire à 14 h 10, puis un autre à 09 h 30."
    ),
    "heure_recomposee": (
        "Ce lundi, 3 élèves sont arrivés, 1 est absent, 1 est arrivé en retard "
        "et 1 est sorti hors plage horaire à 14 h 10, certains parlent de 10 h 14."
    ),
    "omission_presents": (
        "Ce lundi, 1 est absent, 1 est arrivé en retard et 1 est sorti hors plage horaire à 14 h 10."
    ),
    "omission_heure": (
        "Ce lundi, 3 élèves sont arrivés, 1 est absent, 1 est arrivé en retard "
        "et 1 est sorti hors plage horaire."
    ),
    "nombre_negatif": GOOD_TEXT + " Écart de -1 par rapport à hier.",
    "nombre_decimal": GOOD_TEXT + " Soit 1.4 sortie par classe.",
}

# Classe « compteurs tous distincts » : 9 / 6 / 3 / 2 / 4 + quatre heures
RICH_TIMES = ["12 h 40", "13 h 55", "14 h 25", "15 h 05"]
RICH_TAIL = "(12 h 40, 13 h 55, 14 h 25 et 15 h 05)."
RICH_GOOD = (
    "Sur 9 élèves, 6 sont arrivés, 3 sont absents, 2 sont arrivés en retard "
    "et 4 sont sortis hors plage horaire " + RICH_TAIL
)
RICH_LIES = {
    "omission_presents": (
        "Sur 9 élèves, 3 sont absents, 2 sont arrivés en retard "
        "et 4 sont sortis hors plage horaire " + RICH_TAIL
    ),
    "omission_absents": (
        "Sur 9 élèves, 6 sont arrivés, 2 sont arrivés en retard "
        "et 4 sont sortis hors plage horaire " + RICH_TAIL
    ),
    "omission_retards": (
        "Sur 9 élèves, 6 sont arrivés, 3 sont absents "
        "et 4 sont sortis hors plage horaire " + RICH_TAIL
    ),
    "omission_sorties": (
        "Sur 9 élèves, 6 sont arrivés, 3 sont absents, 2 sont arrivés en retard. "
        "Sorties hors plage horaire à 12 h 40, 13 h 55, 14 h 25 et 15 h 05."
    ),
    "omission_une_heure": (
        "Sur 9 élèves, 6 sont arrivés, 3 sont absents, 2 sont arrivés en retard "
        "et 4 sont sortis hors plage horaire (12 h 40, 13 h 55 et 14 h 25)."
    ),
    "minutes_permutees": (
        "Sur 9 élèves, 6 sont arrivés, 3 sont absents, 2 sont arrivés en retard "
        "et 4 sont sortis hors plage horaire (12 h 40, 13 h 55, 14 h 25 et 15 h 50)."
    ),
}


# ---------------------------------------------------------------------------
# Utilitaires génériques
# ---------------------------------------------------------------------------

def at(day, hour, minute=0, second=0):
    """Instant local (Africa/Douala) pour une date donnée."""
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=DOUALA)


@contextmanager
def frozen(moment):
    """Fige django.utils.timezone.now sur ``moment`` (datetime avec fuseau)."""
    if moment.tzinfo is None:
        raise ValueError("L'horloge simulée doit être un datetime avec fuseau.")
    with mock.patch(
        "django.utils.timezone.now", return_value=moment.astimezone(dt_timezone.utc)
    ):
        yield


def make_code(badge, scan_dt):
    """Code rotatif signé du contrat (section 4.1)."""
    time_step = math.floor(scan_dt.timestamp() / 30)
    message = f"v1.{badge.public_id}.{time_step}".encode()
    signature = hmac.new(badge.secret.encode(), message, hashlib.sha256).hexdigest()[:32]
    return f"v1.{badge.public_id}.{time_step}.{signature}"


def choice(model, member):
    """
    Retrouve une constante d'énumération (ex. ``HORS_FENETRE``) dans les classes
    ``Choices`` imbriquées du modèle, sans deviner le nom de la classe imbriquée.
    """
    for attr in vars(model).values():
        if isinstance(attr, type) and issubclass(attr, models.Choices) and member in attr.__members__:
            return attr[member]
    raise AssertionError(
        f"Constante {member!r} introuvable sur {model.__name__} (contrat section 5.1 non respecté)."
    )


def json_summary(text):
    return json.dumps({"summary": text}, ensure_ascii=False)


def llm_response(raw_text, input_tokens=1200, output_tokens=150, model_name=None):
    return LLMResponse(
        raw_text=raw_text,
        model_name=model_name or settings.PASSAGE_AI_MODEL_NAME,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class FakeGateway:
    """
    Double de LLMGateway, propre à chaque test. Chaque élément de la file est soit
    un texte brut (enveloppé dans un LLMResponse), soit un LLMResponse, soit une exception.
    """

    def __init__(self):
        self.queue = []
        self.calls = []

    def push(self, *items):
        self.queue.extend(items)
        return self

    def generate_summary(self, *, system_instruction, metrics_payload, retry_reason=None):
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "metrics_payload": copy.deepcopy(metrics_payload),
                "retry_reason": retry_reason,
            }
        )
        if not self.queue:
            raise AssertionError("Appel inattendu à la passerelle IA (file de réponses vide).")
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return llm_response(item)


# ---------------------------------------------------------------------------
# Oracle indépendant : chiffres recalculés depuis la base + contrôle de fidélité
# ---------------------------------------------------------------------------

COUNTER_KEYS = (
    "total_students",
    "present_count",
    "absent_count",
    "late_entry_count",
    "off_window_exit_count",
)
REQUIRED_NONZERO_KEYS = ("present_count", "absent_count", "late_entry_count", "off_window_exit_count")
MONTHS = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    "septembre|octobre|novembre|décembre|decembre"
)
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:h|:)\s*(\d{2})(?!\d)", re.IGNORECASE)


def db_figures(school, classroom, day):
    """Recalcule les chiffres de la synthèse à partir de la base (définition du contrat 4.2)."""
    tz = ZoneInfo(school.timezone)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    entry = Checkpoint.CheckpointType.ENTRY_GATE
    exit_gate = Checkpoint.CheckpointType.EXIT_GATE
    students = Student.objects.filter(school=school, classroom=classroom, status=Student.Status.ACTIVE)

    present_ids = set(
        Passage.objects.filter(
            student__in=students,
            status=choice(Passage, "VALID"),
            checkpoint__checkpoint_type=entry,
            retained_at__gte=start,
            retained_at__lt=end,
        ).values_list("student_id", flat=True)
    )
    expected_ids = set()
    for window in RouteWindow.objects.filter(
        student__in=students, checkpoint__checkpoint_type=entry, is_active=True
    ):
        if str(day.isoweekday()) in [w.strip() for w in window.weekdays.split(",")]:
            expected_ids.add(window.student_id)

    off_window = choice(Anomaly, "HORS_FENETRE")
    late = 0
    for anomaly in Anomaly.objects.filter(
        school=school,
        student__in=students,
        anomaly_type=off_window,
        date=day,
        checkpoint__checkpoint_type=entry,
    ).select_related("passage", "checkpoint"):
        window = RouteWindow.objects.filter(
            student=anomaly.student, checkpoint=anomaly.checkpoint, is_active=True
        ).first()
        if window and anomaly.passage_id:
            if anomaly.passage.retained_at.astimezone(tz).time() > window.end_time:
                late += 1

    exits = []
    for anomaly in Anomaly.objects.filter(
        school=school,
        student__in=students,
        anomaly_type=off_window,
        date=day,
        checkpoint__checkpoint_type=exit_gate,
    ).select_related("passage"):
        exits.append(anomaly.passage.retained_at.astimezone(tz))
    exits.sort()

    return {
        "classroom": classroom,
        "date": day.isoformat(),
        "total_students": students.count(),
        "present_count": len(present_ids),
        "absent_count": len(expected_ids - present_ids),
        "late_entry_count": late,
        "off_window_exit_count": len(exits),
        "off_window_exit_times": [f"{m.hour:02d} h {m.minute:02d}" for m in exits],
    }


def strip_context(text, classroom):
    """Neutralise le libellé de classe et les dates (contexte, pas des chiffres de la synthèse)."""
    cleaned = text.replace(classroom, " ")
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", " ", cleaned)
    cleaned = re.sub(r"\d{1,2}/\d{1,2}/\d{4}", " ", cleaned)
    cleaned = re.sub(
        rf"\d{{1,2}}(?:er)?\s+(?:{MONTHS})(?:\s+\d{{4}})?", " ", cleaned, flags=re.IGNORECASE
    )
    return cleaned


def fidelity_problems(text, figures, classroom):
    """Liste les écarts entre un texte et les chiffres de référence (liste vide = fidèle)."""
    problems = []
    cleaned = strip_context(text, classroom)

    allowed = {figures[key] for key in COUNTER_KEYS}
    expected_times = list(figures["off_window_exit_times"])
    for stamp in expected_times:
        hours, minutes = re.fullmatch(r"(\d{2}) h (\d{2})", stamp).groups()
        allowed.update({int(hours), int(minutes)})

    numbers = [int(n) for n in re.findall(r"\d+", cleaned)]
    for number in sorted(set(numbers)):
        if number not in allowed:
            problems.append(f"nombre inventé : {number}")
    if re.search(r"(?<![\w])[-−]\d", cleaned):
        problems.append("nombre négatif présent")
    if re.search(r"\d[.,]\d", cleaned):
        problems.append("nombre décimal présent")
    for key in REQUIRED_NONZERO_KEYS:
        if figures[key] != 0 and figures[key] not in numbers:
            problems.append(f"compteur non nul omis : {key}={figures[key]}")

    cited = {f"{int(h):02d} h {int(m):02d}" for h, m in TIME_RE.findall(cleaned)}
    for stamp in sorted(cited - set(expected_times)):
        problems.append(f"heure inventée : {stamp}")
    for stamp in expected_times:
        if stamp not in cited:
            problems.append(f"heure omise : {stamp}")
    return problems


def main_expected(classroom, day=MONDAY):
    return {
        "classroom": classroom,
        "date": day.isoformat(),
        "total_students": 4,
        "present_count": 3,
        "absent_count": 1,
        "late_entry_count": 1,
        "off_window_exit_count": 1,
        "off_window_exit_times": ["14 h 10"],
    }


def rich_expected(classroom, day=MONDAY):
    return {
        "classroom": classroom,
        "date": day.isoformat(),
        "total_students": 9,
        "present_count": 6,
        "absent_count": 3,
        "late_entry_count": 2,
        "off_window_exit_count": 4,
        "off_window_exit_times": list(RICH_TIMES),
    }


def calm_expected(classroom, day=MONDAY, size=2):
    return {
        "classroom": classroom,
        "date": day.isoformat(),
        "total_students": size,
        "present_count": size,
        "absent_count": 0,
        "late_entry_count": 0,
        "off_window_exit_count": 0,
        "off_window_exit_times": [],
    }


# ---------------------------------------------------------------------------
# Auto-test de l'oracle de fidélité (sans base de données)
# ---------------------------------------------------------------------------

class FidelityCheckerSelfTest(SimpleTestCase):
    """Garantit que le contrôle de fidélité échoue réellement sur un texte infidèle."""

    def test_texte_conforme_ne_remonte_aucun_probleme(self):
        self.assertEqual(fidelity_problems(GOOD_TEXT, main_expected("3e A"), "3e A"), [])
        self.assertEqual(fidelity_problems(RICH_GOOD, rich_expected("Tle D"), "Tle D"), [])
        self.assertEqual(fidelity_problems(CALM_TEXT, calm_expected("3e B"), "3e B"), [])
        self.assertEqual(
            fidelity_problems(GOOD_TEXT_AFTER_ARRIVAL, {**main_expected("3e A"), "present_count": 4, "absent_count": 0}, "3e A"),
            [],
        )

    def test_chaque_mensonge_du_catalogue_est_detecte(self):
        for name, text in LIES.items():
            with self.subTest(mensonge=name):
                self.assertNotEqual(
                    fidelity_problems(text, main_expected("3e A"), "3e A"),
                    [],
                    f"Le mensonge « {name} » n'est pas détecté par l'oracle de test.",
                )
        for name, text in RICH_LIES.items():
            with self.subTest(mensonge_riche=name):
                self.assertNotEqual(
                    fidelity_problems(text, rich_expected("Tle D"), "Tle D"),
                    [],
                    f"Le mensonge « {name} » n'est pas détecté par l'oracle de test.",
                )

    def test_le_libelle_de_classe_et_la_date_ne_sont_pas_pris_pour_des_chiffres(self):
        text = "Classe 3e A du 2026-11-23 : 3 élèves arrivés, 1 absent, 1 retard, 1 sortie à 14 h 10."
        self.assertEqual(fidelity_problems(text, main_expected("3e A"), "3e A"), [])

    def test_un_chiffre_modifie_est_detecte(self):
        text = GOOD_TEXT.replace("3 élèves", "5 élèves")
        problems = fidelity_problems(text, main_expected("3e A"), "3e A")
        self.assertTrue(any("5" in p for p in problems), problems)


# ---------------------------------------------------------------------------
# Base commune
# ---------------------------------------------------------------------------

@override_settings(**AI_SETTINGS)
class Lot4Base(TestCase):
    PASSWORD = "Lot4-Mot2Passe-Solide!"
    SUMMARY_NOW = at(MONDAY, 18, 0)  # toutes les fenêtres du lundi sont clôturées

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(
            name="Collège Saint-Exupéry", code="CSE", timezone="Africa/Douala"
        )
        cls.other_school = School.objects.create(
            name="Lycée Bilingue de Mendong", code="LBM", timezone="Africa/Douala"
        )
        gate = Checkpoint.CheckpointType
        cls.entry_cp = Checkpoint.objects.create(
            school=cls.school, label="Portail d'entrée", checkpoint_type=gate.ENTRY_GATE
        )
        cls.exit_cp = Checkpoint.objects.create(
            school=cls.school, label="Portail de sortie", checkpoint_type=gate.EXIT_GATE
        )
        cls.other_entry_cp = Checkpoint.objects.create(
            school=cls.other_school, label="Portail d'entrée", checkpoint_type=gate.ENTRY_GATE
        )
        cls.other_exit_cp = Checkpoint.objects.create(
            school=cls.other_school, label="Portail de sortie", checkpoint_type=gate.EXIT_GATE
        )
        make = User.objects.create_user
        cls.manager = make(username="resp-a", password=cls.PASSWORD, role=User.Role.MANAGER, school=cls.school)
        cls.other_manager = make(
            username="resp-b", password=cls.PASSWORD, role=User.Role.MANAGER, school=cls.other_school
        )
        cls.agent = make(username="agent-a", password=cls.PASSWORD, role=User.Role.AGENT, school=cls.school)
        cls.other_agent = make(
            username="agent-b", password=cls.PASSWORD, role=User.Role.AGENT, school=cls.other_school
        )
        cls.parent1 = make(username="parent-1", password=cls.PASSWORD, role=User.Role.PARENT, school=None)
        cls.parent2 = make(username="parent-2", password=cls.PASSWORD, role=User.Role.PARENT, school=None)

    def setUp(self):
        super().setUp()
        self.sent_codes = []
        self.gateway = FakeGateway()
        patcher = mock.patch(
            "passage.services.summaries.get_llm_gateway", return_value=self.gateway
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- données -----------------------------------------------------------

    def checkpoints_for(self, school):
        if school.pk == self.school.pk:
            return self.entry_cp, self.exit_cp
        return self.other_entry_cp, self.other_exit_cp

    def add_student(
        self,
        first_name,
        last_name,
        *,
        classroom="3e A",
        school=None,
        status=Student.Status.ACTIVE,
        parents=(),
        badge_status=Badge.Status.ACTIVE,
        windows=True,
        pk=None,
        entry=(7, 0, 7, 45),
        exit_=(15, 30, 16, 15),
    ):
        school = school or self.school
        entry_cp, exit_cp = self.checkpoints_for(school)
        fields = dict(
            school=school,
            first_name=first_name,
            last_name=last_name,
            classroom=classroom,
            status=status,
        )
        if pk is not None:
            fields["id"] = pk
        student = Student.objects.create(**fields)
        student.test_badge = Badge.objects.create(student=student, status=badge_status)
        if windows:
            RouteWindow.objects.create(
                student=student,
                checkpoint=entry_cp,
                order=1,
                start_time=time(entry[0], entry[1]),
                end_time=time(entry[2], entry[3]),
                weekdays="1,2,3,4,5",
                is_active=True,
            )
            RouteWindow.objects.create(
                student=student,
                checkpoint=exit_cp,
                order=2,
                start_time=time(exit_[0], exit_[1]),
                end_time=time(exit_[2], exit_[3]),
                weekdays="1,2,3,4,5",
                is_active=True,
            )
        for parent in parents:
            ParentStudentLink.objects.create(parent=parent, student=student)
        return student

    def agent_for(self, school):
        return self.agent if school.pk == self.school.pk else self.other_agent

    def build_main_scenario(self, classroom="3e A", school=None, names=None, ids=None):
        """Classe réaliste du contrat : 4 élèves, 3 présents, 1 retard, 1 sortie hors plage, 1 absent."""
        school = school or self.school
        entry_cp, exit_cp = self.checkpoints_for(school)
        names = names or [
            ("Aïcha", "Mbarga"),
            ("Brice", "Nkoulou"),
            ("Cynthia", "Tchoupo"),
            ("Daniel", "Essomba"),
        ]
        students = [
            self.add_student(first, last, classroom=classroom, school=school, pk=(ids[i] if ids else None))
            for i, (first, last) in enumerate(names)
        ]
        s1, s2, s3, _s4 = students
        tag = f"{school.code}-{classroom}"
        self.sync(
            [
                (f"{tag}-s3-in", s3.test_badge, entry_cp, at(MONDAY, 6, 50)),   # en avance : pas un retard
                (f"{tag}-s1-in", s1.test_badge, entry_cp, at(MONDAY, 7, 15)),   # à l'heure
                (f"{tag}-s2-in", s2.test_badge, entry_cp, at(MONDAY, 8, 12)),   # en retard
                (f"{tag}-s2-out", s2.test_badge, exit_cp, at(MONDAY, 14, 10)),  # sortie hors plage
            ],
            user=self.agent_for(school),
        )
        return students

    def build_rich_scenario(self, classroom="Tle D"):
        """Classe dont tous les compteurs diffèrent : 9 / 6 / 3 / 2 / 4 et quatre heures."""
        names = [
            ("Alice", "Abega"), ("Boris", "Bidzogo"), ("Carine", "Cheuteu"),
            ("Dieudonné", "Domche"), ("Estelle", "Eyenga"), ("Fabrice", "Fouda"),
            ("Gisèle", "Gwet"), ("Hugues", "Haman"), ("Inès", "Ilunga"),
        ]
        s = [self.add_student(f, l, classroom=classroom) for f, l in names]
        tag = f"rich-{classroom}"
        self.sync(
            [
                (f"{tag}-4-in", s[3].test_badge, self.entry_cp, at(MONDAY, 6, 40)),   # en avance
                (f"{tag}-1-in", s[0].test_badge, self.entry_cp, at(MONDAY, 7, 5)),
                (f"{tag}-2-in", s[1].test_badge, self.entry_cp, at(MONDAY, 7, 20)),
                (f"{tag}-3-in", s[2].test_badge, self.entry_cp, at(MONDAY, 7, 44)),
                (f"{tag}-5-in", s[4].test_badge, self.entry_cp, at(MONDAY, 8, 5)),    # retard
                (f"{tag}-6-in", s[5].test_badge, self.entry_cp, at(MONDAY, 8, 30)),   # retard
                (f"{tag}-1-out", s[0].test_badge, self.exit_cp, at(MONDAY, 12, 40)),
                (f"{tag}-2-out", s[1].test_badge, self.exit_cp, at(MONDAY, 13, 55)),
                (f"{tag}-3-out", s[2].test_badge, self.exit_cp, at(MONDAY, 14, 25)),
                (f"{tag}-4-out", s[3].test_badge, self.exit_cp, at(MONDAY, 15, 5)),
            ]
        )
        return s

    def build_calm_class(self, classroom="3e B", school=None, size=2):
        """Classe sans anomalie : tous les élèves arrivent à l'heure."""
        school = school or self.school
        entry_cp, _ = self.checkpoints_for(school)
        students = [
            self.add_student(f"Calme{i}", f"Eleve{i}", classroom=classroom, school=school)
            for i in range(size)
        ]
        self.sync(
            [
                (f"calm-{school.code}-{classroom}-{i}", st.test_badge, entry_cp, at(MONDAY, 7, 10 + 5 * i))
                for i, st in enumerate(students)
            ],
            user=self.agent_for(school),
        )
        return students

    # -- HTTP --------------------------------------------------------------

    def logged_client(self, user, when, enforce_csrf=False):
        client = Client(enforce_csrf_checks=enforce_csrf)
        if user is not None:
            with frozen(when):
                client.force_login(user)
        return client

    def call(self, client, method, path, when, **extra):
        with frozen(when):
            return getattr(client, method)(path, **extra)

    def request(self, user, method, path, when, enforce_csrf=False, **extra):
        client = self.logged_client(user, when, enforce_csrf)
        return self.call(client, method, path, when, **extra)

    def sync(self, events, user=None, expect_ok=True):
        """events : liste de (client_event_id, badge, checkpoint, datetime_local_du_scan)."""
        user = user or self.agent
        now = max(event[3] for event in events) + timedelta(minutes=1)
        payload_events = []
        for event_id, badge, checkpoint, when in events:
            code = make_code(badge, when)
            self.sent_codes.append(code)
            payload_events.append(
                {
                    "client_event_id": event_id,
                    "checkpoint_id": checkpoint.pk,
                    "code": code,
                    "local_scanned_at": when.isoformat(),
                }
            )
        body = {"device_id": "scanner-1", "device_time": now.isoformat(), "events": payload_events}
        response = self.request(
            user,
            "post",
            URL_SYNC,
            now,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        if expect_ok:
            self.assertTrue(
                200 <= response.status_code < 300,
                f"/api/sync/ a répondu {response.status_code} : {response.content[:300]!r}",
            )
        return response

    def scan(self, student, checkpoint, hour, minute, event_id, *, day=MONDAY, second=0, user=None):
        return self.sync(
            [(event_id, student.test_badge, checkpoint, at(day, hour, minute, second))], user=user
        )

    def scan_off_window(self, student, count, *, day=MONDAY, start_hour=10):
        """`count` scans au portail d'entrée, bien hors fenêtre, espacés de 10 minutes."""
        for i in range(count):
            self.scan(
                student, self.entry_cp, start_hour, 10 * i, f"hf-{student.pk}-{day.isoformat()}-{i}", day=day
            )

    def run_scheduler(self, when):
        with frozen(when):
            call_command("run_scheduler", "--once", stdout=StringIO(), stderr=StringIO())

    def summary_form(self, classroom, day):
        form = {"classroom": classroom}
        if day is not None:
            form["date"] = day.isoformat()
        return form

    def post_summary(
        self,
        user=DEFAULT_USER,
        *,
        classroom="3e A",
        day=MONDAY,
        when=None,
        accept_json=True,
        form=None,
        enforce_csrf=False,
    ):
        if user is DEFAULT_USER:
            user = self.manager
        form = self.summary_form(classroom, day) if form is None else form
        extra = {"data": urlencode(form), "content_type": "application/x-www-form-urlencoded"}
        if accept_json:
            extra["HTTP_ACCEPT"] = "application/json"
        return self.request(
            user, "post", URL_SUMMARY, when or self.SUMMARY_NOW, enforce_csrf=enforce_csrf, **extra
        )

    def get_summary(self, user=DEFAULT_USER, when=None):
        if user is DEFAULT_USER:
            user = self.manager
        return self.request(user, "get", URL_SUMMARY, when or self.SUMMARY_NOW)

    # -- lectures de la base ----------------------------------------------

    def anomalies_of(self, student, kind=None):
        queryset = Anomaly.objects.filter(student=student)
        if kind:
            queryset = queryset.filter(anomaly_type=choice(Anomaly, kind))
        return queryset.order_by("id")

    def messages_of(self, student):
        return OutgoingMessage.objects.filter(student=student).order_by("id")

    def call_results(self):
        return list(ModelCallLog.objects.order_by("id").values_list("result", flat=True))

    def denied_count(self, user):
        return AuditLog.objects.filter(action=AuditLog.Action.ACCESS_DENIED, actor=user).count()

    def success_audits(self):
        return AuditLog.objects.filter(
            action=AuditLog.Action.SUMMARY_GENERATED, outcome=AuditLog.Outcome.SUCCESS
        ).count()

    def seed_call_log(self, result=None, cost="0", day=MONDAY, school=None):
        """Insère un journal d'appel antérieur (daté du jour voulu)."""
        result = result or ModelCallLog.Result.SUCCESS
        moment = at(day, 9, 0)
        with frozen(moment):
            log = ModelCallLog.objects.create(
                school=school or self.school,
                called_at=moment,
                call_date=day,
                model_name="gemini-lot4-test",
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal(cost),
                result=result,
                error_code="",
            )
        ModelCallLog.objects.filter(pk=log.pk).update(called_at=moment, call_date=day)
        return log

    def persisted_dump(self):
        chunks = []
        for model in (DailySummary, ModelCallLog, AuditLog):
            chunks.append(json.dumps(list(model.objects.values()), default=str, ensure_ascii=False))
        return "\n".join(chunks)

    # -- assertions --------------------------------------------------------

    def assert_json_path(self, response, origin, path):
        self.assertEqual(response.status_code, 200, response.content[:400])
        data = response.json()
        self.assertEqual(data["origin"], origin, data)
        self.assertEqual(data["generation_path"], path, data)
        return data

    def assert_faithful(self, text, classroom, day=MONDAY, school=None):
        figures = db_figures(school or self.school, classroom, day)
        self.assertEqual(
            fidelity_problems(text, figures, classroom),
            [],
            f"Texte infidèle aux chiffres de la base.\nTexte : {text}\nChiffres : {figures}",
        )

    def assert_no_key_leak(self, response=None):
        haystacks = {"base de données": self.persisted_dump()}
        if response is not None:
            haystacks["réponse HTTP"] = response.content.decode("utf-8", "replace") + str(
                dict(response.items())
            )
        for where, haystack in haystacks.items():
            self.assertNotIn(API_KEY, haystack, f"La clé API fuit dans : {where}")
            self.assertNotIn(KEY_FRAGMENT, haystack, f"Un fragment de la clé API fuit dans : {where}")

    def assert_neutral(self, body, student):
        badge = student.test_badge
        for token in [badge.public_id, badge.secret, self.PASSWORD, *self.sent_codes]:
            self.assertNotIn(str(token), body, "Le message parent expose un secret ou un identifiant de badge.")
        lowered = body.lower()
        for word in ("hmac", "sha256", "argon2", "mot de passe", "password", "secret", "v1."):
            self.assertNotIn(word, lowered, f"Détail technique « {word} » dans le message parent.")
        if student.pk >= 100000:
            self.assertIsNone(
                re.search(rf"(?<!\d){student.pk}(?!\d)", body),
                "L'identifiant technique de l'élève apparaît dans le message parent.",
            )


# ---------------------------------------------------------------------------
# 1. Notifications sortantes : nominal, multi-parents, contenu neutre
# ---------------------------------------------------------------------------

@override_settings(PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY=10)
class NotificationsNominalTests(Lot4Base):

    def assert_all_anomalies_notified(self, student, parents):
        anomalies = list(self.anomalies_of(student))
        self.assertTrue(anomalies, "Précondition : aucune anomalie détectée pour l'élève.")
        for anomaly in anomalies:
            messages = list(OutgoingMessage.objects.filter(anomaly=anomaly))
            self.assertEqual(
                sorted(m.parent_id for m in messages),
                sorted(p.pk for p in parents),
                "Chaque parent rattaché doit recevoir exactement un message par anomalie.",
            )
            for message in messages:
                self.assertEqual(message.status, OutgoingMessage.Status.SENT)
                self.assertEqual(message.non_delivery_reason, "")
                self.assertIsNotNone(message.sent_at)
                self.assertEqual(message.student_id, student.pk)
                self.assertEqual(message.school_id, student.school_id)
                self.assertEqual(message.target_date, MONDAY)
                self.assertTrue(message.body.strip())
        self.assertEqual(self.messages_of(student).count(), len(anomalies) * len(parents))

    def test_sync_hors_fenetre_notifie_chaque_parent_sans_doublon(self):
        eleve = self.add_student("Aïcha", "Mbarga", parents=(self.parent1, self.parent2))
        self.scan(eleve, self.entry_cp, 14, 10, "nom-1")
        self.assertTrue(self.anomalies_of(eleve, "HORS_FENETRE").exists())
        self.assert_all_anomalies_notified(eleve, [self.parent1, self.parent2])

    def test_scheduler_point_manquant_notifie_chaque_parent_sans_doublon(self):
        eleve = self.add_student("Brice", "Nkoulou", parents=(self.parent1, self.parent2))
        self.run_scheduler(at(MONDAY, 8, 30))
        self.assertTrue(self.anomalies_of(eleve, "POINT_MANQUANT").exists())
        self.assert_all_anomalies_notified(eleve, [self.parent1, self.parent2])

    def test_eleve_sans_parent_anomalie_creee_sans_message_ni_erreur(self):
        eleve = self.add_student("Cynthia", "Tchoupo")
        self.scan(eleve, self.entry_cp, 14, 10, "orphelin-1")  # sync() vérifie l'absence d'erreur
        self.assertTrue(self.anomalies_of(eleve, "HORS_FENETRE").exists())
        self.assertEqual(OutgoingMessage.objects.count(), 0)

    def test_scheduler_eleve_sans_parent_anomalie_creee_sans_message(self):
        eleve = self.add_student("Daniel", "Essomba")
        self.run_scheduler(at(MONDAY, 8, 30))
        self.assertTrue(self.anomalies_of(eleve, "POINT_MANQUANT").exists())
        self.assertEqual(OutgoingMessage.objects.count(), 0)

    def test_messages_neutres_pour_les_trois_types_d_anomalie(self):
        hf = self.add_student("Hélène", "Fotso", pk=987651, parents=(self.parent1,))
        pm = self.add_student("Ibrahim", "Ngoh", pk=987652, parents=(self.parent1,))
        bn = self.add_student(
            "Jocelyne", "Biya", pk=987653, parents=(self.parent1,),
            badge_status=Badge.Status.SUSPENDED, windows=False,
        )
        self.scan(hf, self.entry_cp, 14, 10, "neutre-hf")
        self.scan(bn, self.entry_cp, 9, 0, "neutre-bn")
        self.run_scheduler(at(MONDAY, 17, 0))

        for student, kind in ((hf, "HORS_FENETRE"), (pm, "POINT_MANQUANT"), (bn, "BADGE_NON_ACTIF")):
            anomalies = self.anomalies_of(student, kind)
            self.assertTrue(anomalies.exists(), f"Précondition : anomalie {kind} absente.")
            for anomaly in anomalies:
                messages = list(OutgoingMessage.objects.filter(anomaly=anomaly))
                self.assertEqual(len(messages), 1, kind)
                body = messages[0].body
                self.assertIn(self.school.name, body, f"{kind} : nom de l'établissement absent.")
                self.assertIn(student.first_name, body, f"{kind} : prénom absent.")
                self.assert_neutral(body, student)
                if kind == "HORS_FENETRE":
                    self.assertIn("14 h 10", body, "L'heure locale du passage doit figurer (HH h MM).")

    def test_badges_non_actifs_tous_statuts_sont_notifies_sans_fuite(self):
        for index, status in enumerate(
            (Badge.Status.SUSPENDED, Badge.Status.LOST, Badge.Status.REVOKED)
        ):
            with self.subTest(statut=status):
                eleve = self.add_student(
                    f"Prenom{index}", f"Nom{index}", pk=987660 + index,
                    parents=(self.parent1,), badge_status=status, windows=False,
                )
                self.scan(eleve, self.entry_cp, 9, index * 10, f"bna-{index}")
                anomalies = self.anomalies_of(eleve, "BADGE_NON_ACTIF")
                self.assertTrue(anomalies.exists())
                for anomaly in anomalies:
                    message = OutgoingMessage.objects.get(anomaly=anomaly)
                    self.assertEqual(message.status, OutgoingMessage.Status.SENT)
                    self.assertIn(self.school.name, message.body)
                    self.assertIn(eleve.first_name, message.body)
                    self.assert_neutral(message.body, eleve)


# ---------------------------------------------------------------------------
# 2. Notifications sortantes : plafond journalier et temporalité
# ---------------------------------------------------------------------------

class NotificationsPlafondTests(Lot4Base):

    def assert_cap_pattern(self, messages, cap):
        for message in messages[:cap]:
            self.assertEqual(message.status, OutgoingMessage.Status.SENT)
            self.assertEqual(message.non_delivery_reason, "")
            self.assertIsNotNone(message.sent_at)
        for message in messages[cap:]:
            self.assertEqual(message.status, OutgoingMessage.Status.NOT_SENT)
            self.assertEqual(message.non_delivery_reason, "plafond atteint")
            self.assertIsNone(message.sent_at)

    def test_quatre_anomalies_trois_envoyes_un_non_envoye(self):
        eleve = self.add_student("Aïcha", "Mbarga", parents=(self.parent1,))
        self.scan_off_window(eleve, 4)
        nb = self.anomalies_of(eleve).count()
        self.assertGreaterEqual(nb, 4, "Précondition : 4 scans hors fenêtre doivent produire ≥ 4 anomalies.")
        self.assertEqual(Passage.objects.filter(student=eleve).count(), 4)
        messages = list(self.messages_of(eleve).filter(target_date=MONDAY))
        self.assertEqual(
            len(messages), nb, "Aucune anomalie ne doit rester sans message (même plafond atteint)."
        )
        self.assert_cap_pattern(messages, 3)
        self.assertEqual(sum(m.status == OutgoingMessage.Status.SENT for m in messages), 3)

    @override_settings(PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY=2)
    def test_le_plafond_est_celui_du_parametre(self):
        eleve = self.add_student("Brice", "Nkoulou", parents=(self.parent1,))
        self.scan_off_window(eleve, 3)
        nb = self.anomalies_of(eleve).count()
        self.assertGreaterEqual(nb, 3)
        messages = list(self.messages_of(eleve))
        self.assertEqual(len(messages), nb)
        self.assert_cap_pattern(messages, 2)

    def test_plafond_independant_par_eleve(self):
        saturé = self.add_student("Cynthia", "Tchoupo", parents=(self.parent1,))
        autre = self.add_student("Daniel", "Essomba", parents=(self.parent1,))
        self.scan_off_window(saturé, 4)
        self.scan(autre, self.entry_cp, 11, 0, "autre-eleve-1")
        self.assertTrue(
            self.messages_of(saturé).filter(status=OutgoingMessage.Status.NOT_SENT).exists(),
            "Précondition : le premier élève doit avoir atteint son plafond.",
        )
        anomalies = self.anomalies_of(autre)
        self.assertTrue(anomalies.exists())
        messages = list(self.messages_of(autre))
        self.assertEqual(len(messages), anomalies.count())
        for message in messages:
            self.assertEqual(message.status, OutgoingMessage.Status.SENT)
            self.assertIsNotNone(message.sent_at)

    def test_plafond_independant_par_journee(self):
        eleve = self.add_student("Estelle", "Eyenga", parents=(self.parent1,))
        self.scan_off_window(eleve, 4, day=MONDAY)
        self.assertTrue(
            self.messages_of(eleve).filter(status=OutgoingMessage.Status.NOT_SENT).exists(),
            "Précondition : plafond atteint le lundi.",
        )
        monday_before = list(self.messages_of(eleve).filter(target_date=MONDAY).values_list("id", "status"))
        self.scan(eleve, self.entry_cp, 10, 0, "lendemain-1", day=TUESDAY)
        tuesday = list(self.messages_of(eleve).filter(target_date=TUESDAY))
        self.assertTrue(tuesday, "Une anomalie du mardi doit produire un message.")
        for message in tuesday:
            self.assertEqual(message.status, OutgoingMessage.Status.SENT)
            self.assertEqual(message.non_delivery_reason, "")
            self.assertIsNotNone(message.sent_at)
        self.assertEqual(
            list(self.messages_of(eleve).filter(target_date=MONDAY).values_list("id", "status")),
            monday_before,
            "Les messages du lundi ne doivent pas être modifiés par ceux du mardi.",
        )

    def test_le_plafond_porte_sur_le_total_des_messages_de_l_eleve(self):
        eleve = self.add_student("Fabrice", "Fouda", parents=(self.parent1, self.parent2))
        self.scan_off_window(eleve, 3)
        nb = self.anomalies_of(eleve).count()
        self.assertGreaterEqual(nb, 3)
        messages = list(self.messages_of(eleve).filter(target_date=MONDAY))
        self.assertEqual(len(messages), nb * 2, "Un message (envoyé ou non) par couple (anomalie, parent).")
        sent = [m for m in messages if m.status == OutgoingMessage.Status.SENT]
        self.assertEqual(
            len(sent), 3,
            "Avec 2 parents, le plafond de 3 s'applique au total des messages de l'élève, pas par parent.",
        )
        self.assert_cap_pattern(messages, 3)

    def test_renvoi_du_meme_client_event_id_ne_cree_rien_et_ne_consomme_pas_le_quota(self):
        eleve = self.add_student("Gisèle", "Gwet", parents=(self.parent1,))
        event = ("evt-dup", eleve.test_badge, self.entry_cp, at(MONDAY, 10, 0))
        self.sync([event])
        messages_before = self.messages_of(eleve).count()
        anomalies_before = self.anomalies_of(eleve).count()
        self.assertGreaterEqual(anomalies_before, 1)
        for _ in range(5):
            self.sync([event])
        self.assertEqual(self.messages_of(eleve).count(), messages_before)
        self.assertEqual(self.anomalies_of(eleve).count(), anomalies_before)

        self.scan(eleve, self.entry_cp, 10, 10, "evt-new-1")
        self.scan(eleve, self.entry_cp, 10, 20, "evt-new-2")
        total = self.anomalies_of(eleve).count()
        self.assertLessEqual(total, 3, "Précondition : au plus 3 anomalies distinctes (plafond non atteint).")
        self.assertFalse(
            self.messages_of(eleve).filter(status=OutgoingMessage.Status.NOT_SENT).exists(),
            "Les renvois idempotents ont consommé le quota journalier.",
        )
        self.assertEqual(self.messages_of(eleve).count(), total)

    def test_relance_du_scheduler_ne_cree_ni_anomalie_ni_message(self):
        eleve = self.add_student("Hugues", "Haman", parents=(self.parent1, self.parent2))
        self.run_scheduler(at(MONDAY, 8, 30))
        anomalies = self.anomalies_of(eleve).count()
        messages = self.messages_of(eleve).count()
        self.assertGreaterEqual(anomalies, 1)
        self.assertEqual(messages, anomalies * 2)
        for when in (at(MONDAY, 8, 30), at(MONDAY, 8, 45), at(MONDAY, 9, 0)):
            self.run_scheduler(when)
        self.assertEqual(self.anomalies_of(eleve).count(), anomalies)
        self.assertEqual(self.messages_of(eleve).count(), messages)
        self.assertEqual(
            self.messages_of(eleve).filter(status=OutgoingMessage.Status.SENT).count(), messages
        )

    def test_scheduler_respecte_le_delai_de_grace(self):
        eleve = self.add_student("Inès", "Ilunga", parents=(self.parent1,))
        self.run_scheduler(at(MONDAY, 7, 54))  # fenêtre clôturée depuis 9 minutes seulement
        self.assertFalse(self.anomalies_of(eleve, "POINT_MANQUANT").exists())
        self.assertEqual(OutgoingMessage.objects.count(), 0)
        self.run_scheduler(at(MONDAY, 7, 56))  # clôturée depuis 11 minutes
        self.assertTrue(self.anomalies_of(eleve, "POINT_MANQUANT").exists())
        self.assertTrue(self.messages_of(eleve).exists())

    def test_sync_et_scheduler_partagent_le_meme_quota(self):
        eleve = self.add_student("Jules", "Kamga", parents=(self.parent1,))
        for i in range(3):  # sorties bien avant la fenêtre : 3 anomalies HORS_FENETRE
            self.scan(eleve, self.exit_cp, 12, 10 * i, f"mix-{i}")
        self.assertEqual(
            self.messages_of(eleve).filter(status=OutgoingMessage.Status.SENT).count(),
            3,
            "Précondition : le plafond de 3 doit être atteint par les anomalies de /api/sync/.",
        )
        known = set(self.messages_of(eleve).values_list("id", flat=True))
        self.run_scheduler(at(MONDAY, 17, 0))
        fresh = list(self.messages_of(eleve).exclude(id__in=known))
        self.assertTrue(fresh, "Le scheduler doit détecter au moins un POINT_MANQUANT.")
        for message in fresh:
            self.assertEqual(message.status, OutgoingMessage.Status.NOT_SENT)
            self.assertEqual(message.non_delivery_reason, "plafond atteint")
            self.assertIsNone(message.sent_at)
        self.assertEqual(
            self.messages_of(eleve).filter(status=OutgoingMessage.Status.SENT).count(), 3
        )
        self.assertTrue(self.anomalies_of(eleve, "POINT_MANQUANT").exists())


# ---------------------------------------------------------------------------
# 3. Synthèse : exactitude des métriques
# ---------------------------------------------------------------------------

@override_settings(PASSAGE_AI_API_KEY="")
class SummaryMetricsTests(Lot4Base):

    def test_chiffres_du_scenario_contractuel_en_mode_gabarit(self):
        students = self.build_main_scenario()
        expected = main_expected("3e A")
        self.assertEqual(db_figures(self.school, "3e A", MONDAY), expected, "L'oracle de test dévie du contrat.")
        self.assertTrue(
            self.anomalies_of(students[2], "HORS_FENETRE").exists(),
            "Précondition : l'arrivée en avance doit être une anomalie HORS_FENETRE.",
        )
        audits_before = self.success_audits()

        response = self.post_summary()
        data = self.assert_json_path(
            response, DailySummary.Origin.TEMPLATE, DailySummary.GenerationPath.GABARIT_SANS_CLE
        )
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        self.assertEqual(data["computed_metrics"], expected)
        self.assertEqual(data["classroom"], "3e A")
        self.assertEqual(data["date"], "2026-11-23")

        summary = DailySummary.objects.get()
        self.assertEqual(data["summary_id"], summary.pk)
        self.assertEqual(summary.school_id, self.school.pk)
        self.assertEqual(summary.classroom, "3e A")
        self.assertEqual(summary.date, MONDAY)
        self.assertEqual(summary.computed_metrics, expected)
        self.assertTrue(summary.metrics_hash)
        self.assertEqual(summary.summary_text, data["summary_text"])
        self.assertEqual(summary.origin, DailySummary.Origin.TEMPLATE)
        self.assertEqual(summary.generation_path, DailySummary.GenerationPath.GABARIT_SANS_CLE)
        self.assertEqual(summary.generated_by_id, self.manager.pk)

        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.success_audits(), audits_before + 1)
        self.assert_faithful(data["summary_text"], "3e A")

    def test_eleve_inactif_est_ignore(self):
        self.build_main_scenario()
        self.add_student("Zacharie", "Inactif", status=Student.Status.INACTIVE)
        data = self.post_summary().json()
        self.assertEqual(data["computed_metrics"], main_expected("3e A"))

    def test_perimetre_autre_classe_et_autre_etablissement_sans_effet(self):
        self.build_main_scenario()
        self.build_calm_class("3e B")
        self.build_main_scenario(classroom="3e A", school=self.other_school)
        data = self.post_summary().json()
        self.assertEqual(data["computed_metrics"], main_expected("3e A"))

    def test_sorties_hors_plage_comptees_avant_et_apres_la_fenetre_triees_par_heure(self):
        tardif = self.add_student("Karim", "Sortie-Tard")
        precoce = self.add_student("Lydie", "Sortie-Tot")
        self.sync(
            [
                ("sortie-tard", tardif.test_badge, self.exit_cp, at(MONDAY, 16, 40)),
                ("sortie-tot", precoce.test_badge, self.exit_cp, at(MONDAY, 14, 10)),
            ]
        )
        metrics = self.post_summary().json()["computed_metrics"]
        self.assertEqual(metrics["off_window_exit_count"], 2)
        self.assertEqual(metrics["off_window_exit_times"], ["14 h 10", "16 h 40"])
        self.assertEqual(metrics["late_entry_count"], 0)

    def test_classe_calme_tous_les_compteurs_a_zero(self):
        self.build_calm_class("3e B")
        data = self.post_summary(classroom="3e B").json()
        self.assertEqual(data["computed_metrics"], calm_expected("3e B"))
        self.assert_faithful(data["summary_text"], "3e B")

    def test_chiffres_de_la_classe_a_compteurs_distincts(self):
        self.build_rich_scenario("Tle D")
        self.assertEqual(db_figures(self.school, "Tle D", MONDAY), rich_expected("Tle D"))
        data = self.post_summary(classroom="Tle D").json()
        self.assertEqual(data["computed_metrics"], rich_expected("Tle D"))
        self.assert_faithful(data["summary_text"], "Tle D")

    def test_bascule_de_journee_utilise_le_fuseau_de_l_etablissement(self):
        # Scan à 00 h 20 locale le mardi = 23 h 20 UTC le lundi : il appartient au mardi.
        veilleur = self.add_student("Nuit", "Blanche", classroom="Nuit", entry=(0, 10, 0, 45), exit_=(22, 0, 22, 30))
        self.scan(veilleur, self.entry_cp, 0, 20, "minuit-1", day=TUESDAY)
        when = at(TUESDAY, 18, 0)
        monday = self.post_summary(classroom="Nuit", day=MONDAY, when=when).json()["computed_metrics"]
        tuesday = self.post_summary(classroom="Nuit", day=TUESDAY, when=when).json()["computed_metrics"]
        self.assertEqual(monday["present_count"], 0, "Le passage de 00 h 20 locale est compté la veille (UTC).")
        self.assertEqual(monday["absent_count"], 1)
        self.assertEqual(tuesday["present_count"], 1)
        self.assertEqual(tuesday["absent_count"], 0)

    def test_date_par_defaut_est_la_date_locale_de_l_etablissement(self):
        self.add_student("Aïcha", "Mbarga")
        # 23 h 30 UTC lundi = 00 h 30 mardi à Douala
        late_utc = datetime(2026, 11, 23, 23, 30, tzinfo=dt_timezone.utc)
        data = self.post_summary(day=None, when=late_utc).json()
        self.assertEqual(data["date"], "2026-11-24")
        self.assertEqual(data["computed_metrics"]["date"], "2026-11-24")
        self.assertEqual(DailySummary.objects.get(pk=data["summary_id"]).date, TUESDAY)
        # 22 h 30 UTC lundi = 23 h 30 lundi à Douala
        early_utc = datetime(2026, 11, 23, 22, 30, tzinfo=dt_timezone.utc)
        data = self.post_summary(day=None, when=early_utc).json()
        self.assertEqual(data["date"], "2026-11-23")


# ---------------------------------------------------------------------------
# 3 bis. Synthèse : fidélité numérique, sorties malformées, relance, repli
# ---------------------------------------------------------------------------

class SummaryFidelityTests(Lot4Base):

    # -- les trois situations obligatoires --------------------------------

    @override_settings(PASSAGE_AI_API_KEY="")
    def test_fidelite_en_mode_gabarit(self):
        self.build_main_scenario()
        data = self.assert_json_path(
            self.post_summary(),
            DailySummary.Origin.TEMPLATE,
            DailySummary.GenerationPath.GABARIT_SANS_CLE,
        )
        self.assert_faithful(data["summary_text"], "3e A")
        self.assertEqual(self.gateway.calls, [])

    def test_fidelite_reponse_modele_conforme(self):
        self.build_main_scenario()
        self.gateway.push(llm_response(json_summary(GOOD_TEXT), input_tokens=1200, output_tokens=150))
        data = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(data["summary_text"], GOOD_TEXT)
        self.assert_faithful(data["summary_text"], "3e A")
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertFalse(self.gateway.calls[0]["retry_reason"])
        log = ModelCallLog.objects.get()
        self.assertEqual(log.result, ModelCallLog.Result.SUCCESS)
        self.assertEqual(log.input_tokens, 1200)
        self.assertEqual(log.output_tokens, 150)
        self.assertGreater(log.estimated_cost_usd, 0)
        self.assertEqual(log.model_name, "gemini-lot4-test")
        self.assertEqual(log.call_date, MONDAY)
        summary = DailySummary.objects.get()
        self.assertEqual(summary.origin, DailySummary.Origin.MODEL)
        self.assertEqual(summary.summary_text, GOOD_TEXT)
        self.assertEqual(summary.generated_by_id, self.manager.pk)

    def test_cout_estime_selon_la_grille_par_defaut(self):
        self.build_main_scenario()
        self.gateway.push(
            llm_response(json_summary(GOOD_TEXT), input_tokens=1_000_000, output_tokens=500_000)
        )
        self.post_summary()
        self.assertEqual(Decimal(ModelCallLog.objects.get().estimated_cost_usd), Decimal("0.45"))

    @override_settings(
        PASSAGE_AI_INPUT_COST_PER_1M_USD=Decimal("2.00"),
        PASSAGE_AI_OUTPUT_COST_PER_1M_USD=Decimal("8.00"),
    )
    def test_cout_estime_suit_la_grille_configuree(self):
        self.build_main_scenario()
        self.gateway.push(
            llm_response(json_summary(GOOD_TEXT), input_tokens=500_000, output_tokens=250_000)
        )
        self.post_summary()
        self.assertEqual(Decimal(ModelCallLog.objects.get().estimated_cost_usd), Decimal("3.00"))

    # -- helpers de scénario ---------------------------------------------

    def _assert_relance_puis_succes(self, first_raw, *, good_text=GOOD_TEXT, classroom="3e A"):
        self.gateway.push(first_raw, json_summary(good_text))
        data = self.assert_json_path(
            self.post_summary(classroom=classroom),
            DailySummary.Origin.MODEL,
            DailySummary.GenerationPath.MODELE_APRES_RELANCE,
        )
        self.assertEqual(len(self.gateway.calls), 2, "Exactement deux appels : l'essai initial et une relance.")
        self.assertFalse(self.gateway.calls[0]["retry_reason"])
        reason = self.gateway.calls[1]["retry_reason"]
        self.assertTrue(
            isinstance(reason, str) and reason.strip(), "retry_reason doit être renseigné à la relance."
        )
        self.assertEqual(
            self.call_results(), [ModelCallLog.Result.INVALID_OUTPUT, ModelCallLog.Result.SUCCESS]
        )
        self.assertEqual(data["summary_text"], good_text)
        self.assertEqual(DailySummary.objects.count(), 1)
        self.assert_faithful(data["summary_text"], classroom)
        return data

    def _assert_echec_double(self, first_raw, second_raw, *, classroom="3e A"):
        self.gateway.push(first_raw, second_raw, json_summary(GOOD_TEXT))  # 3e réponse : ne doit jamais servir
        response = self.post_summary(classroom=classroom)
        data = self.assert_json_path(
            response, DailySummary.Origin.TEMPLATE, DailySummary.GenerationPath.GABARIT_APRES_ECHEC
        )
        self.assertEqual(len(self.gateway.calls), 2, "Jamais de troisième appel.")
        self.assertEqual(len(self.gateway.queue), 1)
        self.assertEqual(
            self.call_results(), [ModelCallLog.Result.INVALID_OUTPUT, ModelCallLog.Result.INVALID_OUTPUT]
        )
        self.assertEqual(DailySummary.objects.count(), 1)
        self.assert_faithful(data["summary_text"], classroom)
        for raw in (first_raw, second_raw):
            try:
                refused = json.loads(raw).get("summary")
            except (ValueError, AttributeError):
                refused = None
            if refused:
                self.assertNotEqual(data["summary_text"], refused, "Le texte refusé a été publié.")
        return data

    # -- mensonges au premier essai, conforme au second --------------------

    def test_chiffre_invente_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["chiffre_invente"]))

    def test_heure_inventee_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["heure_inventee"]))

    def test_heure_recomposee_a_partir_de_chiffres_autorises_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["heure_recomposee"]))

    def test_omission_du_compteur_de_presents_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["omission_presents"]))

    def test_omission_de_l_heure_de_sortie_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["omission_heure"]))

    def test_nombre_negatif_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["nombre_negatif"]))

    def test_nombre_decimal_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary(LIES["nombre_decimal"]))

    # -- sorties malformées -----------------------------------------------

    def test_json_invalide_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes("Voici la synthèse du jour : 3 élèves présents.")

    def test_objet_sans_cle_summary_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json.dumps({"resume": GOOD_TEXT}, ensure_ascii=False))

    def test_texte_trop_court_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary("OK"))

    def test_balise_script_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary("<script>alert(1)</script>"))

    def test_texte_valide_mais_enrobe_de_html_puis_conforme(self):
        self.build_main_scenario()
        self._assert_relance_puis_succes(json_summary("<b>" + GOOD_TEXT + "</b>"))

    # -- échec aux deux essais -> gabarit ----------------------------------

    def test_mensonge_aux_deux_essais_chiffre_inventé_replie_sur_gabarit(self):
        self.build_main_scenario()
        self._assert_echec_double(
            json_summary(LIES["chiffre_invente"]), json_summary(LIES["chiffre_invente"])
        )

    def test_heure_inventee_puis_omission_replie_sur_gabarit(self):
        self.build_main_scenario()
        self._assert_echec_double(
            json_summary(LIES["heure_inventee"]), json_summary(LIES["omission_presents"])
        )

    def test_json_invalide_puis_html_replie_sur_gabarit(self):
        self.build_main_scenario()
        self._assert_echec_double("pas du json", json_summary("<script>alert(1)</script>"))

    def test_texte_trop_court_puis_cle_absente_replie_sur_gabarit(self):
        self.build_main_scenario()
        self._assert_echec_double(
            json_summary("OK"), json.dumps({"texte": GOOD_TEXT}, ensure_ascii=False)
        )

    def test_html_refuse_n_apparait_dans_aucune_reponse(self):
        self.build_main_scenario()
        self.gateway.push(
            json_summary("<script>alert(1)</script>"), json_summary("<script>alert(1)</script>")
        )
        response = self.post_summary(accept_json=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert(1)", response.content.decode())
        self.assertFalse(DailySummary.objects.filter(summary_text__contains="<script>").exists())

    # -- compteurs tous distincts : chaque omission est détectée ----------

    def test_omission_de_chaque_compteur_non_nul_est_refusee(self):
        for name in ("omission_presents", "omission_absents", "omission_retards", "omission_sorties"):
            with self.subTest(omission=name):
                # classe distincte par sous-test : pas de cache croisé entre les cas
                classroom = f"Tle-{name}"
                self.build_rich_scenario(classroom)
                self.gateway.queue.clear()
                self.gateway.calls.clear()
                self.gateway.push(json_summary(RICH_LIES[name]), json_summary(RICH_GOOD))
                data = self.post_summary(classroom=classroom).json()
                self.assertEqual(
                    data["generation_path"],
                    DailySummary.GenerationPath.MODELE_APRES_RELANCE,
                    f"{name} : le texte incomplet a été accepté du premier coup.",
                )
                self.assertEqual(len(self.gateway.calls), 2)

    def test_heure_manquante_ou_permutee_est_refusee(self):
        for name in ("omission_une_heure", "minutes_permutees"):
            with self.subTest(mensonge=name):
                classroom = f"Tle-{name}"
                self.build_rich_scenario(classroom)
                self.gateway.queue.clear()
                self.gateway.calls.clear()
                self.gateway.push(json_summary(RICH_LIES[name]), json_summary(RICH_GOOD))
                data = self.post_summary(classroom=classroom).json()
                self.assertEqual(
                    data["generation_path"], DailySummary.GenerationPath.MODELE_APRES_RELANCE, name
                )

    def test_texte_conforme_de_la_classe_a_compteurs_distincts_est_accepte(self):
        self.build_rich_scenario("Tle D")
        self.gateway.push(json_summary(RICH_GOOD))
        data = self.post_summary(classroom="Tle D").json()
        self.assertEqual(data["generation_path"], DailySummary.GenerationPath.MODELE)
        self.assert_faithful(data["summary_text"], "Tle D")

    # -- classe calme : aucun chiffre ne doit être inventé ---------------

    def test_classe_calme_texte_conforme_accepte(self):
        self.build_calm_class("3e B")
        self.gateway.push(json_summary(CALM_TEXT))
        data = self.post_summary(classroom="3e B").json()
        self.assertEqual(data["generation_path"], DailySummary.GenerationPath.MODELE)
        self.assertEqual(len(self.gateway.calls), 1)

    def test_classe_calme_un_retard_invente_est_refuse(self):
        self.build_calm_class("3e B")
        lie = "2 élèves sont présents et 1 est arrivé en retard, aucune absence."
        self.gateway.push(json_summary(lie), json_summary(CALM_TEXT))
        data = self.post_summary(classroom="3e B").json()
        self.assertEqual(data["generation_path"], DailySummary.GenerationPath.MODELE_APRES_RELANCE)
        self.assertEqual(data["summary_text"], CALM_TEXT)

    # -- bornes de longueur (10 à 500 caractères) -------------------------

    def _bounded(self, text, expected_path):
        self.build_calm_class("3e B")
        self.gateway.push(json_summary(text), json_summary(CALM_TEXT))
        data = self.post_summary(classroom="3e B").json()
        self.assertEqual(data["generation_path"], expected_path, f"longueur={len(text)}")

    def test_texte_de_10_caracteres_accepte(self):
        text = "2 presents"
        self.assertEqual(len(text), 10)
        self._bounded(text, DailySummary.GenerationPath.MODELE)

    def test_texte_de_9_caracteres_refuse(self):
        text = "2 present"
        self.assertEqual(len(text), 9)
        self._bounded(text, DailySummary.GenerationPath.MODELE_APRES_RELANCE)

    def test_texte_de_500_caracteres_accepte(self):
        base = "2 presents, aucun retard, aucune absence, aucune sortie hors plage. "
        text = base + "z" * (500 - len(base))
        self.assertEqual(len(text), 500)
        self._bounded(text, DailySummary.GenerationPath.MODELE)

    def test_texte_de_501_caracteres_refuse(self):
        base = "2 presents, aucun retard, aucune absence, aucune sortie hors plage. "
        text = base + "z" * (501 - len(base))
        self.assertEqual(len(text), 501)
        self._bounded(text, DailySummary.GenerationPath.MODELE_APRES_RELANCE)

    # -- exceptions à la relance ------------------------------------------

    def test_panne_a_la_relance_replie_sur_service_indisponible(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(LIES["chiffre_invente"]), LLMServiceError("http_503"))
        data = self.assert_json_path(
            self.post_summary(),
            DailySummary.Origin.TEMPLATE,
            DailySummary.GenerationPath.GABARIT_SERVICE_INDISPONIBLE,
        )
        self.assertEqual(len(self.gateway.calls), 2)
        self.assertEqual(self.call_results(), [ModelCallLog.Result.INVALID_OUTPUT, ModelCallLog.Result.ERROR])
        self.assert_faithful(data["summary_text"], "3e A")


# ---------------------------------------------------------------------------
# 4. Confidentialité du payload IA et non-fuite de la clé API
# ---------------------------------------------------------------------------

class PrivacyTests(Lot4Base):
    NAMES = [
        ("Zéphirin", "Nkomo-Atangana"),
        ("Yolande", "Ewodo-Mballa"),
        ("Xavier", "Tchamba-Ondoa"),
        ("Wilfried", "Manga-Bekolo"),
    ]
    IDS = [880011, 880012, 880013, 880014]
    CLASSROOM = "Terminale-Zèbre-77"

    def test_payload_et_instruction_sans_donnees_nominatives_ni_identifiants(self):
        self.build_main_scenario(classroom=self.CLASSROOM, names=self.NAMES, ids=self.IDS)
        # essai initial mensonger pour inspecter aussi le payload de la relance
        self.gateway.push(json_summary(LIES["chiffre_invente"]), json_summary(GOOD_TEXT))
        response = self.post_summary(classroom=self.CLASSROOM)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.gateway.calls), 2)
        forbidden = [name for pair in self.NAMES for name in pair]
        forbidden += [str(i) for i in self.IDS] + [self.CLASSROOM, "Zèbre"]
        for index, call in enumerate(self.gateway.calls):
            payload = call["metrics_payload"]
            self.assertTrue(payload, f"appel {index} : payload vide")
            dumped = json.dumps(payload, default=str, ensure_ascii=False).lower()
            instruction = str(call["system_instruction"]).lower()
            for item in forbidden:
                self.assertNotIn(item.lower(), dumped, f"appel {index} : « {item} » transmis au modèle (payload).")
            for item in forbidden[: len(self.NAMES) * 2 + len(self.IDS)]:
                self.assertNotIn(
                    item.lower(), instruction, f"appel {index} : « {item} » transmis au modèle (instruction)."
                )

    def test_cle_api_absente_partout_au_cas_nominal_json_puis_html(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT))
        json_response = self.post_summary()
        self.assertEqual(json_response.status_code, 200)
        self.assert_no_key_leak(json_response)
        html_response = self.post_summary(accept_json=False)  # servi par le cache
        self.assertEqual(html_response.status_code, 200)
        self.assert_no_key_leak(html_response)

    def test_cle_api_absente_partout_apres_timeout_meme_si_le_message_la_contient(self):
        self.build_main_scenario()
        self.gateway.push(LLMTimeoutError("timeout", f"Délai dépassé sur ?key={API_KEY}"))
        response = self.post_summary()
        self.assertEqual(response.status_code, 200)
        self.assert_no_key_leak(response)

    def test_cle_api_absente_partout_apres_erreur_service_meme_si_le_message_la_contient(self):
        self.build_main_scenario()
        self.gateway.push(LLMServiceError("http_503", f"HTTP 503 pour la clé {API_KEY}"))
        response = self.post_summary(accept_json=False)
        self.assertEqual(response.status_code, 200)
        self.assert_no_key_leak(response)

    def test_cle_api_absente_partout_apres_sortie_invalide_puis_gabarit(self):
        self.build_main_scenario()
        self.gateway.push("pas du json", "toujours pas du json")
        response = self.post_summary()
        self.assertEqual(response.status_code, 200)
        self.assert_no_key_leak(response)

    def test_journal_d_audit_de_la_synthese_sans_cle(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT))
        self.post_summary()
        entries = AuditLog.objects.filter(
            action=AuditLog.Action.SUMMARY_GENERATED, outcome=AuditLog.Outcome.SUCCESS
        )
        self.assertEqual(entries.count(), 1)
        entry = entries.get()
        self.assertEqual(entry.actor_id, self.manager.pk)
        self.assertNotIn(API_KEY, json.dumps(entry.details, default=str))


# ---------------------------------------------------------------------------
# 5. Mode dégradé, pannes et plafonds IA
# ---------------------------------------------------------------------------

class DegradedModeAndLimitsTests(Lot4Base):

    def assert_blocked(self, response, classroom="3e A"):
        data = self.assert_json_path(
            response,
            DailySummary.Origin.TEMPLATE,
            DailySummary.GenerationPath.GABARIT_PLAFOND_ATTEINT,
        )
        self.assertEqual(
            ModelCallLog.objects.filter(result=ModelCallLog.Result.BLOCKED_LIMIT).count(), 1
        )
        self.assert_faithful(data["summary_text"], classroom)
        return data

    # -- pannes -----------------------------------------------------------

    def test_timeout_replie_sur_gabarit_sans_erreur_500(self):
        self.build_main_scenario()
        self.gateway.push(LLMTimeoutError())
        audits = self.success_audits()
        data = self.assert_json_path(
            self.post_summary(),
            DailySummary.Origin.TEMPLATE,
            DailySummary.GenerationPath.GABARIT_SERVICE_INDISPONIBLE,
        )
        self.assertEqual(self.call_results(), [ModelCallLog.Result.TIMEOUT])
        self.assertEqual(len(self.gateway.calls), 1, "Pas de relance après un timeout.")
        self.assertEqual(DailySummary.objects.count(), 1)
        self.assertEqual(self.success_audits(), audits + 1)
        self.assert_faithful(data["summary_text"], "3e A")

    def test_erreur_service_http_503_replie_sur_gabarit(self):
        self.build_main_scenario()
        self.gateway.push(LLMServiceError("http_503"))
        data = self.assert_json_path(
            self.post_summary(),
            DailySummary.Origin.TEMPLATE,
            DailySummary.GenerationPath.GABARIT_SERVICE_INDISPONIBLE,
        )
        self.assertEqual(self.call_results(), [ModelCallLog.Result.ERROR])
        self.assertEqual(len(self.gateway.calls), 1)
        self.assert_faithful(data["summary_text"], "3e A")

    def test_panne_en_mode_html_affiche_le_badge_gabarit(self):
        self.build_main_scenario()
        self.gateway.push(LLMServiceError("http_503"))
        response = self.post_summary(accept_json=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gabarit (mode dégradé)")
        self.assertNotContains(response, "Modèle IA")

    @override_settings(PASSAGE_AI_API_KEY="")
    def test_sans_cle_aucun_appel_et_aucun_journal(self):
        self.build_main_scenario()
        response = self.post_summary()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(ModelCallLog.objects.count(), 0)

    # -- plafond d'appels ---------------------------------------------------

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_plafond_d_appels_sur_une_autre_classe(self):
        self.build_main_scenario()
        self.build_calm_class("3e B")
        self.gateway.push(json_summary(GOOD_TEXT), json_summary(CALM_TEXT))
        first = self.post_summary(classroom="3e A")
        self.assert_json_path(first, DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE)
        second = self.post_summary(classroom="3e B")
        self.assert_blocked(second, "3e B")
        self.assertEqual(len(self.gateway.calls), 1, "Le gateway ne doit pas être sollicité au-delà du plafond.")
        self.assertEqual(
            self.call_results(), [ModelCallLog.Result.SUCCESS, ModelCallLog.Result.BLOCKED_LIMIT]
        )

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_plafond_d_appels_apres_changement_des_chiffres(self):
        students = self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT), json_summary(GOOD_TEXT_AFTER_ARRIVAL))
        self.post_summary()
        self.scan(students[3], self.entry_cp, 7, 20, "arrivee-s4")  # les chiffres changent
        response = self.post_summary()
        self.assert_blocked(response)
        self.assertEqual(len(self.gateway.calls), 1)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_plafond_atteint_au_premier_essai_bloque_la_relance(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(LIES["chiffre_invente"]), json_summary(GOOD_TEXT))
        response = self.post_summary()
        self.assert_blocked(response)
        self.assertEqual(len(self.gateway.calls), 1, "La relance doit aussi respecter le plafond.")
        self.assertEqual(
            self.call_results(), [ModelCallLog.Result.INVALID_OUTPUT, ModelCallLog.Result.BLOCKED_LIMIT]
        )

    def _assert_prior_call_counts(self, result):
        self.build_main_scenario()
        self.seed_call_log(result=result)
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_blocked(self.post_summary())
        self.assertEqual(self.gateway.calls, [])

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_un_appel_success_precedent_compte_dans_le_plafond(self):
        self._assert_prior_call_counts(ModelCallLog.Result.SUCCESS)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_un_appel_invalid_output_precedent_compte_dans_le_plafond(self):
        self._assert_prior_call_counts(ModelCallLog.Result.INVALID_OUTPUT)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_un_appel_error_precedent_compte_dans_le_plafond(self):
        self._assert_prior_call_counts(ModelCallLog.Result.ERROR)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_un_appel_timeout_precedent_compte_dans_le_plafond(self):
        self._assert_prior_call_counts(ModelCallLog.Result.TIMEOUT)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1)
    def test_les_journaux_blocked_limit_ne_comptent_pas_comme_appels(self):
        self.build_main_scenario()
        for _ in range(3):
            self.seed_call_log(result=ModelCallLog.Result.BLOCKED_LIMIT)
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 1)

    @override_settings(PASSAGE_AI_DAILY_CALL_LIMIT=1, PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.50"))
    def test_les_appels_de_la_veille_ne_comptent_pas(self):
        self.build_main_scenario()
        self.seed_call_log(result=ModelCallLog.Result.SUCCESS, cost="9.99", day=SUNDAY)
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 1)

    # -- plafond de coût ----------------------------------------------------

    @override_settings(PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.50"))
    def test_cout_egal_au_plafond_bloque_l_appel(self):
        self.build_main_scenario()
        self.seed_call_log(cost="0.50")
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_blocked(self.post_summary())
        self.assertEqual(self.gateway.calls, [])

    @override_settings(PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.50"))
    def test_cout_superieur_au_plafond_bloque_l_appel(self):
        self.build_main_scenario()
        self.seed_call_log(cost="0.60")
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_blocked(self.post_summary())
        self.assertEqual(self.gateway.calls, [])

    @override_settings(PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.50"))
    def test_la_somme_des_couts_du_jour_est_comparee_au_plafond(self):
        self.build_main_scenario()
        self.seed_call_log(cost="0.25")
        self.seed_call_log(cost="0.25")
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_blocked(self.post_summary())
        self.assertEqual(self.gateway.calls, [])

    @override_settings(PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.50"))
    def test_cout_juste_sous_le_plafond_autorise_l_appel(self):
        self.build_main_scenario()
        self.seed_call_log(cost="0.49")
        self.gateway.push(json_summary(GOOD_TEXT))
        self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 1)

    @override_settings(PASSAGE_AI_DAILY_COST_LIMIT_USD=Decimal("0.0001"))
    def test_plafond_de_cout_atteint_au_premier_essai_bloque_la_relance(self):
        self.build_main_scenario()
        self.gateway.push(
            llm_response(json_summary(LIES["chiffre_invente"]), input_tokens=1200, output_tokens=150),
            json_summary(GOOD_TEXT),
        )
        response = self.post_summary()
        self.assert_blocked(response)
        self.assertEqual(len(self.gateway.calls), 1, "La relance doit aussi respecter le plafond de coût.")
        self.assertEqual(
            self.call_results(), [ModelCallLog.Result.INVALID_OUTPUT, ModelCallLog.Result.BLOCKED_LIMIT]
        )


# ---------------------------------------------------------------------------
# 6. Cache et absence de double facturation
# ---------------------------------------------------------------------------

class CacheTests(Lot4Base):

    def test_deux_requetes_identiques_un_seul_appel_modele(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT))
        first = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        second = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.CACHE
        )
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(ModelCallLog.objects.count(), 1)
        self.assertEqual(second["summary_text"], first["summary_text"])
        rows = list(DailySummary.objects.order_by("id"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].origin, DailySummary.Origin.MODEL)
        self.assertEqual(rows[1].generation_path, DailySummary.GenerationPath.CACHE)
        self.assertEqual(rows[1].metrics_hash, rows[0].metrics_hash)
        self.assertEqual(rows[1].summary_text, rows[0].summary_text)

    def test_le_cache_sert_aussi_une_synthese_obtenue_apres_relance(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(LIES["chiffre_invente"]), json_summary(GOOD_TEXT))
        self.post_summary()
        calls_before = len(self.gateway.calls)
        logs_before = ModelCallLog.objects.count()
        data = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.CACHE
        )
        self.assertEqual(data["summary_text"], GOOD_TEXT)
        self.assertEqual(len(self.gateway.calls), calls_before)
        self.assertEqual(ModelCallLog.objects.count(), logs_before)

    def test_nouveau_passage_invalide_le_cache(self):
        students = self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT), json_summary(GOOD_TEXT_AFTER_ARRIVAL))
        self.post_summary()
        self.post_summary()
        self.assertEqual(len(self.gateway.calls), 1)
        hash_before = DailySummary.objects.order_by("id").first().metrics_hash

        self.scan(students[3], self.entry_cp, 7, 20, "arrivee-s4")  # l'absent arrive
        third = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 2, "Les chiffres ont changé : le cache ne doit pas servir.")
        self.assertEqual(ModelCallLog.objects.count(), 2)
        self.assertEqual(third["summary_text"], GOOD_TEXT_AFTER_ARRIVAL)
        self.assertEqual(third["computed_metrics"]["present_count"], 4)
        self.assertEqual(third["computed_metrics"]["absent_count"], 0)
        latest = DailySummary.objects.order_by("-id").first()
        self.assertNotEqual(latest.metrics_hash, hash_before)
        self.assert_faithful(third["summary_text"], "3e A")

    def test_le_cache_est_isole_par_classe(self):
        self.build_main_scenario()
        self.build_calm_class("3e B")
        self.gateway.push(json_summary(GOOD_TEXT), json_summary(CALM_TEXT))
        self.post_summary(classroom="3e A")
        data = self.assert_json_path(
            self.post_summary(classroom="3e B"), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 2)
        self.assertEqual(data["summary_text"], CALM_TEXT)

    def test_le_cache_est_isole_par_date(self):
        self.build_main_scenario()
        # Mardi : aucun passage, les 4 élèves attendus sont absents (4 / 0 / 4 / 0 / 0).
        tuesday_text = "4 élèves absents, aucun présent, aucun retard ni sortie hors plage."
        self.gateway.push(
            json_summary(GOOD_TEXT), json_summary(tuesday_text), json_summary(tuesday_text)
        )
        self.post_summary(day=MONDAY)
        data = self.post_summary(day=TUESDAY, when=at(TUESDAY, 18, 0)).json()
        self.assertGreaterEqual(
            len(self.gateway.calls), 2, "Une autre date ne doit pas réutiliser la synthèse du lundi."
        )
        self.assertNotEqual(data["generation_path"], DailySummary.GenerationPath.CACHE)
        self.assertNotEqual(data["summary_text"], GOOD_TEXT)

    def test_changer_de_modele_invalide_le_cache(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT), json_summary(GOOD_TEXT))
        self.post_summary()
        with override_settings(PASSAGE_AI_MODEL_NAME="gemini-lot4-autre-modele"):
            data = self.assert_json_path(
                self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
            )
        self.assertEqual(len(self.gateway.calls), 2)
        self.assertEqual(data["summary_text"], GOOD_TEXT)

    @override_settings(PASSAGE_AI_API_KEY="")
    def test_les_gabarits_ne_sont_jamais_servis_depuis_le_cache(self):
        self.build_main_scenario()
        for _ in range(2):
            self.assert_json_path(
                self.post_summary(),
                DailySummary.Origin.TEMPLATE,
                DailySummary.GenerationPath.GABARIT_SANS_CLE,
            )
        self.assertEqual(DailySummary.objects.count(), 2)
        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(self.gateway.calls, [])

    def test_un_repli_gabarit_n_est_pas_mis_en_cache(self):
        self.build_main_scenario()
        self.gateway.push(LLMTimeoutError(), json_summary(GOOD_TEXT))
        first = self.post_summary().json()
        self.assertEqual(first["origin"], DailySummary.Origin.TEMPLATE)
        second = self.assert_json_path(
            self.post_summary(), DailySummary.Origin.MODEL, DailySummary.GenerationPath.MODELE
        )
        self.assertEqual(len(self.gateway.calls), 2, "Un gabarit de repli ne doit pas bloquer un vrai appel ultérieur.")
        self.assertEqual(second["summary_text"], GOOD_TEXT)


# ---------------------------------------------------------------------------
# 7. Permissions, cloisonnement, validation d'entrée, CSRF, lecture sans effet de bord
# ---------------------------------------------------------------------------

class AccessAndValidationTests(Lot4Base):

    def assert_nothing_generated(self):
        self.assertEqual(DailySummary.objects.count(), 0)
        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.success_audits(), 0)

    def test_get_par_le_responsable_n_a_aucun_effet_de_bord(self):
        self.build_main_scenario()
        client = self.logged_client(self.manager, self.SUMMARY_NOW)
        audits_before = AuditLog.objects.count()
        response = self.call(client, "get", URL_SUMMARY, self.SUMMARY_NOW)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertEqual(DailySummary.objects.count(), 0)
        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), audits_before)
        self.assertEqual(self.gateway.calls, [])

    def test_post_html_affiche_le_texte_et_le_badge_modele_ia(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT))
        response = self.post_summary(accept_json=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        content = response.content.decode()
        self.assertTrue(GOOD_TEXT in content or escape(GOOD_TEXT) in content, "Texte de synthèse absent de la page.")
        self.assertIn("Modèle IA", content)
        self.assertNotIn("Gabarit (mode dégradé)", content)

    @override_settings(PASSAGE_AI_API_KEY="")
    def test_post_html_affiche_le_texte_et_le_badge_gabarit(self):
        self.build_main_scenario()
        response = self.post_summary(accept_json=False)
        self.assertEqual(response.status_code, 200)
        text = DailySummary.objects.get().summary_text
        content = response.content.decode()
        self.assertTrue(text in content or escape(text) in content, "Texte du gabarit absent de la page.")
        self.assertIn("Gabarit (mode dégradé)", content)
        self.assertNotIn("Modèle IA", content)

    def test_entrees_invalides_renvoient_400_sans_rien_creer(self):
        self.build_main_scenario()
        cases = {
            "classe vide": {"classroom": "", "date": "2026-11-23"},
            "classe absente": {"date": "2026-11-23"},
            "date jj-mm-aaaa": {"classroom": "3e A", "date": "23-11-2026"},
            "date avec barres": {"classroom": "3e A", "date": "2026/11/23"},
            "date compacte": {"classroom": "3e A", "date": "20261123"},
            "date en toutes lettres": {"classroom": "3e A", "date": "hier"},
            "date avec heure": {"classroom": "3e A", "date": "2026-11-23T08:00:00"},
        }
        for label, form in cases.items():
            with self.subTest(cas=label):
                response = self.post_summary(form=form)
                self.assertEqual(response.status_code, 400, label)
        self.assert_nothing_generated()

    def test_classe_sans_eleve_actif_est_refusee_sans_rien_creer(self):
        self.add_student("Paul", "Ancien", classroom="Ancienne", status=Student.Status.INACTIVE)
        response = self.post_summary(classroom="Ancienne")
        self.assertIn(response.status_code, (400, 403))
        self.assert_nothing_generated()

    def test_responsable_d_un_autre_etablissement_recoit_403_et_un_audit(self):
        self.build_main_scenario()
        denied_before = self.denied_count(self.other_manager)
        response = self.post_summary(user=self.other_manager, classroom="3e A")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.denied_count(self.other_manager), denied_before + 1)
        for name in ("Mbarga", "Nkoulou", "Tchoupo", "Essomba"):
            self.assertNotContains(response, name, status_code=403)
        self.assert_nothing_generated()

    def test_meme_nom_de_classe_dans_deux_etablissements_reste_cloisonne(self):
        self.build_main_scenario()  # « 3e A » de l'établissement A
        self.add_student("Zoé", "Etoundi", classroom="3e A", school=self.other_school)
        with override_settings(PASSAGE_AI_API_KEY=""):
            response = self.post_summary(user=self.other_manager, classroom="3e A")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["computed_metrics"]["total_students"], 1)
        self.assertEqual(data["computed_metrics"]["present_count"], 0)
        self.assertEqual(DailySummary.objects.get(pk=data["summary_id"]).school_id, self.other_school.pk)
        for name in ("Mbarga", "Nkoulou", "Tchoupo", "Essomba"):
            self.assertNotIn(name, response.content.decode())

    def test_agent_et_parent_recoivent_403_et_un_audit_sur_get_et_post(self):
        self.build_main_scenario()
        for user in (self.agent, self.parent1):
            with self.subTest(utilisateur=user.username):
                before = self.denied_count(user)
                get_response = self.get_summary(user)
                self.assertEqual(get_response.status_code, 403)
                self.assertEqual(self.denied_count(user), before + 1)
                post_response = self.post_summary(user=user)
                self.assertEqual(post_response.status_code, 403)
                self.assertEqual(self.denied_count(user), before + 2)
        self.assertEqual(DailySummary.objects.count(), 0)
        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(self.gateway.calls, [])

    def test_anonyme_est_redirige_vers_la_connexion(self):
        self.build_main_scenario()
        get_response = self.get_summary(user=None)
        self.assertEqual(get_response.status_code, 302)
        self.assertTrue(get_response["Location"].startswith("/connexion/"), get_response["Location"])
        post_response = self.post_summary(user=None)
        self.assertEqual(post_response.status_code, 302)
        self.assertTrue(post_response["Location"].startswith("/connexion/"), post_response["Location"])
        self.assert_nothing_generated()

    def test_post_sans_jeton_csrf_est_refuse(self):
        self.build_main_scenario()
        self.gateway.push(json_summary(GOOD_TEXT))
        response = self.post_summary(enforce_csrf=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(DailySummary.objects.count(), 0)
        self.assertEqual(ModelCallLog.objects.count(), 0)
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.success_audits(), 0)
