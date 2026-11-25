"""Idempotent seed command creating reproducible baseline data (CDC section 7)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from passage.models import (
    Anomaly,
    Badge,
    BadgeStatusHistory,
    Checkpoint,
    OutgoingMessage,
    ParentStudentLink,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)

# Demonstration-only credentials (documented in README, never reused elsewhere — CDC §7).
DEMO_ACCOUNTS = {
    "manager": ("responsable", "DemoResp!2026", "Marie Ndongo"),
    "agent": ("agent", "DemoAgent!2026", "Paul Essomba"),
    "parent1": ("parent1", "DemoParent1!2026", "Jean Ndi"),
    "parent2": ("parent2", "DemoParent2!2026", "Hélène Mballa"),
}

SEED_STUDENTS = [
    ("Amina", "Ndi", "3e A", "parent1", "seed-badge-001"),
    ("Samuel", "Ndi", "3e A", "parent1", "seed-badge-002"),
    ("Clarisse", "Mballa", "3e A", "parent2", "seed-badge-003"),
    ("Junior", "Fotso", "3e A", "parent2", "seed-badge-004"),
    ("Diane", "Kamga", "3e A", "parent2", "seed-badge-005"),
]


class Command(BaseCommand):
    help = (
        "Peuple la base avec un jeu de données de départ reproductible et rejouable "
        "(1 responsable, 1 agent, 2 parents, 5 élèves, points de contrôle, itinéraires, "
        "badges actifs et passages produisant 3 anomalies de types différents)."
    )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        school, _ = School.objects.get_or_create(
            code="lycee-pilote-yaounde",
            defaults={
                "name": "Lycée Pilote de Yaoundé",
                "timezone": "Africa/Douala",
                "is_active": True,
            },
        )

        users = self._seed_users(school)
        checkpoints = self._seed_checkpoints(school)
        students_by_name, badges_by_name = self._seed_students_routes_and_badges(
            school=school,
            users=users,
            checkpoints=checkpoints,
        )
        self._seed_passages_and_anomalies(
            school=school,
            agent=users["agent"],
            users=users,
            checkpoints=checkpoints,
            students_by_name=students_by_name,
            badges_by_name=badges_by_name,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Jeu de données initial prêt (idempotent) : "
                f"{School.objects.count()} établissement(s), "
                f"{User.objects.count()} utilisateur(s), "
                f"{Student.objects.count()} élève(s), "
                f"{Badge.objects.filter(status=Badge.Status.ACTIVE).count()} badge(s) actif(s), "
                f"{Passage.objects.count()} passage(s), "
                f"{Anomaly.objects.count()} anomalie(s)."
            )
        )

    def _seed_users(self, school: School) -> dict[str, User]:
        created_users: dict[str, User] = {}

        role_map = {
            "manager": (User.Role.MANAGER, school, True),
            "agent": (User.Role.AGENT, school, False),
            "parent1": (User.Role.PARENT, None, False),
            "parent2": (User.Role.PARENT, None, False),
        }

        for key, (username, demo_password, full_name) in DEMO_ACCOUNTS.items():
            role, user_school, is_staff = role_map[key]
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "full_name": full_name,
                    "role": role,
                    "school": user_school,
                    "is_staff": is_staff,
                    "is_active": True,
                },
            )
            if created:
                user.set_password(demo_password)
                user.save(update_fields=["password"])
            created_users[key] = user

        return created_users

    def _seed_checkpoints(self, school: School) -> dict[str, Checkpoint]:
        specs = {
            "entry": ("Portail d'entrée", Checkpoint.CheckpointType.ENTRY_GATE),
            "exit": ("Portail de sortie", Checkpoint.CheckpointType.EXIT_GATE),
            "bus": ("Autobus ligne 1", Checkpoint.CheckpointType.BUS),
        }
        checkpoints: dict[str, Checkpoint] = {}
        for key, (label, cp_type) in specs.items():
            cp, _ = Checkpoint.objects.get_or_create(
                school=school,
                label=label,
                defaults={"checkpoint_type": cp_type, "is_active": True},
            )
            checkpoints[key] = cp
        return checkpoints

    def _seed_students_routes_and_badges(
        self,
        *,
        school: School,
        users: dict[str, User],
        checkpoints: dict[str, Checkpoint],
    ) -> tuple[dict[str, Student], dict[str, Badge]]:
        students_by_name: dict[str, Student] = {}
        badges_by_name: dict[str, Badge] = {}
        manager = users["manager"]

        for first_name, last_name, classroom, parent_key, public_id in SEED_STUDENTS:
            student, _ = Student.objects.get_or_create(
                school=school,
                first_name=first_name,
                last_name=last_name,
                classroom=classroom,
                defaults={"status": Student.Status.ACTIVE},
            )
            ParentStudentLink.objects.get_or_create(
                parent=users[parent_key],
                student=student,
            )

            # Full-day itinerary: entry 07:00-07:45 and exit 15:30-16:15 (Monday-Friday).
            RouteWindow.objects.get_or_create(
                student=student,
                checkpoint=checkpoints["entry"],
                order=1,
                defaults={
                    "start_time": time(7, 0),
                    "end_time": time(7, 45),
                    "weekdays": "1,2,3,4,5",
                    "is_active": True,
                },
            )
            RouteWindow.objects.get_or_create(
                student=student,
                checkpoint=checkpoints["exit"],
                order=2,
                defaults={
                    "start_time": time(15, 30),
                    "end_time": time(16, 15),
                    "weekdays": "1,2,3,4,5",
                    "is_active": True,
                },
            )

            # One active badge per student; secret is randomly generated on first creation.
            badge = Badge.objects.filter(
                student=student, status=Badge.Status.ACTIVE
            ).first()
            if badge is None:
                badge, created = Badge.objects.get_or_create(
                    public_id=public_id,
                    defaults={
                        "student": student,
                        "status": Badge.Status.ACTIVE,
                    },
                )
                if created:
                    BadgeStatusHistory.objects.create(
                        badge=badge,
                        changed_by=manager,
                        previous_status="",
                        new_status=Badge.Status.ACTIVE,
                        reason="Émission initiale (jeu de démonstration).",
                    )

            students_by_name[first_name] = student
            badges_by_name[first_name] = badge

        return students_by_name, badges_by_name

    def _seed_passages_and_anomalies(
        self,
        *,
        school: School,
        agent: User,
        users: dict[str, User],
        checkpoints: dict[str, Checkpoint],
        students_by_name: dict[str, Student],
        badges_by_name: dict[str, Badge],
    ) -> None:
        school_tz = ZoneInfo(school.timezone)
        today = timezone.now().astimezone(school_tz).date()

        def make_dt(hour: int, minute: int, second: int = 0) -> datetime:
            return datetime(
                today.year, today.month, today.day, hour, minute, second, tzinfo=school_tz
            )

        device_id = "seed-device-01"

        def get_or_create_seed_passage(
            *,
            event_id: str,
            student_first_name: str,
            checkpoint: Checkpoint,
            when: datetime,
            time_step: int,
        ) -> Passage:
            student = students_by_name[student_first_name]
            badge = badges_by_name[student_first_name]
            passage, _ = Passage.objects.get_or_create(
                device_id=device_id,
                client_event_id=event_id,
                defaults={
                    "school": school,
                    "badge": badge,
                    "student": student,
                    "checkpoint": checkpoint,
                    "agent": agent,
                    "badge_public_id": badge.public_id,
                    "time_step": time_step,
                    "local_scanned_at": when,
                    "server_received_at": when,
                    "corrected_at": when,
                    "retained_at": when,
                    "clock_offset_seconds": 0,
                    "clock_suspect": False,
                    "status": Passage.Status.VALID,
                    "rejection_reason": Passage.RejectionReason.NONE,
                    "sync_status": Passage.SyncStatus.SYNCED,
                },
            )
            return passage

        # 1. Normal on-time passage (Amina at entry gate, 07:15).
        get_or_create_seed_passage(
            event_id="seed-evt-001",
            student_first_name="Amina",
            checkpoint=checkpoints["entry"],
            when=make_dt(7, 15, 0),
            time_step=1001,
        )

        # 2. HORS_FENETRE (Samuel arrives late at entry gate, 08:12 > 07:45).
        late_passage = get_or_create_seed_passage(
            event_id="seed-evt-002",
            student_first_name="Samuel",
            checkpoint=checkpoints["entry"],
            when=make_dt(8, 12, 0),
            time_step=1002,
        )
        anomaly_late, _ = Anomaly.objects.get_or_create(
            passage=late_passage,
            anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
            defaults={
                "school": school,
                "student": students_by_name["Samuel"],
                "checkpoint": checkpoints["entry"],
                "date": late_passage.retained_at.astimezone(school_tz).date(),
                "status": Anomaly.Status.OPEN,
                "details": "Passage au portail d'entrée en dehors de la fenêtre 07 h 00 – 07 h 45.",
            },
        )
        OutgoingMessage.objects.get_or_create(
            anomaly=anomaly_late,
            parent=users["parent1"],
            defaults={
                "school": school,
                "student": students_by_name["Samuel"],
                "target_date": anomaly_late.date,
                "body": (
                    f"{school.name} : Samuel a été enregistré(e) au portail d'entrée "
                    "à 08 h 12, en dehors de l'horaire habituel."
                ),
                "status": OutgoingMessage.Status.SENT,
                "sent_at": late_passage.retained_at,
            },
        )

        # 3. POINT_INCONNU (Clarisse scanned at Bus line 1, not in her itinerary).
        unknown_point_passage = get_or_create_seed_passage(
            event_id="seed-evt-003",
            student_first_name="Clarisse",
            checkpoint=checkpoints["bus"],
            when=make_dt(7, 25, 0),
            time_step=1003,
        )
        anomaly_unknown, _ = Anomaly.objects.get_or_create(
            passage=unknown_point_passage,
            anomaly_type=Anomaly.AnomalyType.POINT_INCONNU,
            defaults={
                "school": school,
                "student": students_by_name["Clarisse"],
                "checkpoint": checkpoints["bus"],
                "date": unknown_point_passage.retained_at.astimezone(school_tz).date(),
                "status": Anomaly.Status.OPEN,
                "details": "Passage à un point de contrôle absent de l'itinéraire.",
            },
        )
        OutgoingMessage.objects.get_or_create(
            anomaly=anomaly_unknown,
            parent=users["parent2"],
            defaults={
                "school": school,
                "student": students_by_name["Clarisse"],
                "target_date": anomaly_unknown.date,
                "body": (
                    f"{school.name} : Clarisse a été enregistré(e) à 07 h 25 "
                    "sur un point de contrôle inhabituel (Autobus ligne 1)."
                ),
                "status": OutgoingMessage.Status.SENT,
                "sent_at": unknown_point_passage.retained_at,
            },
        )

        # 4. DOUBLE_SCAN (Junior scanned twice 25s apart at entry gate).
        get_or_create_seed_passage(
            event_id="seed-evt-004",
            student_first_name="Junior",
            checkpoint=checkpoints["entry"],
            when=make_dt(7, 20, 0),
            time_step=1004,
        )
        double_scan_passage = get_or_create_seed_passage(
            event_id="seed-evt-005",
            student_first_name="Junior",
            checkpoint=checkpoints["entry"],
            when=make_dt(7, 20, 25),
            time_step=1005,
        )
        anomaly_double, _ = Anomaly.objects.get_or_create(
            passage=double_scan_passage,
            anomaly_type=Anomaly.AnomalyType.DOUBLE_SCAN,
            defaults={
                "school": school,
                "student": students_by_name["Junior"],
                "checkpoint": checkpoints["entry"],
                "date": double_scan_passage.retained_at.astimezone(school_tz).date(),
                "status": Anomaly.Status.OPEN,
                "details": "Deux passages enregistrés à 25 secondes d'intervalle.",
            },
        )
        OutgoingMessage.objects.get_or_create(
            anomaly=anomaly_double,
            parent=users["parent2"],
            defaults={
                "school": school,
                "student": students_by_name["Junior"],
                "target_date": anomaly_double.date,
                "body": (
                    f"{school.name} : deux passages rapprochés ont été enregistrés "
                    "pour Junior au portail d'entrée à 07 h 20."
                ),
                "status": OutgoingMessage.Status.SENT,
                "sent_at": double_scan_passage.retained_at,
            },
        )
