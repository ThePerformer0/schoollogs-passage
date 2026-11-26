"""Idempotent seed command creating reproducible baseline data (CDC section 7)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
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
    "parent3": ("parent3", "DemoParent3!2026", "Alain Tchamba"),
    "parent4": ("parent4", "DemoParent4!2026", "Sophie Owona"),
    "parent5": ("parent5", "DemoParent5!2026", "Rodrigue Atangana"),
    "parent6": ("parent6", "DemoParent6!2026", "Mireille Nkoulou"),
}

# 20 students total across 3 classes (3e A: 7, 4e B: 7, Tle D: 6), each with an active badge.
SEED_STUDENTS = [
    # Classe 3e A (7 élèves)
    ("Amina", "Ndi", "3e A", "parent1", "seed-badge-001"),
    ("Samuel", "Ndi", "3e A", "parent1", "seed-badge-002"),
    ("Clarisse", "Mballa", "3e A", "parent2", "seed-badge-003"),
    ("Junior", "Fotso", "3e A", "parent2", "seed-badge-004"),
    ("Diane", "Kamga", "3e A", "parent2", "seed-badge-005"),
    ("Moussa", "Bello", "3e A", "parent3", "seed-badge-006"),
    ("Farida", "Amadou", "3e A", "parent3", "seed-badge-007"),
    # Classe 4e B (7 élèves)
    ("Chloé", "Tchamba", "4e B", "parent3", "seed-badge-008"),
    ("Kévin", "Owona", "4e B", "parent4", "seed-badge-009"),
    ("Nadia", "Owona", "4e B", "parent4", "seed-badge-010"),
    ("Yannick", "Manga", "4e B", "parent4", "seed-badge-011"),
    ("Lionel", "Abega", "4e B", "parent5", "seed-badge-012"),
    ("Stéphanie", "Biya", "4e B", "parent5", "seed-badge-013"),
    ("Hervé", "Etoa", "4e B", "parent5", "seed-badge-014"),
    # Classe Tle D (6 élèves)
    ("Estelle", "Atangana", "Tle D", "parent5", "seed-badge-015"),
    ("Brice", "Nkoulou", "Tle D", "parent6", "seed-badge-016"),
    ("Carine", "Nkoulou", "Tle D", "parent6", "seed-badge-017"),
    ("Gilles", "Fokam", "Tle D", "parent6", "seed-badge-018"),
    ("Inès", "Mekongo", "Tle D", "parent1", "seed-badge-019"),
    ("Olivier", "Ngoh", "Tle D", "parent2", "seed-badge-020"),
]

# Class-specific weekly itinerary schedules (Monday to Friday, Africa/Douala):
#   - 3e A : entrée 07:00-07:45, sortie 15:30-16:15 (lun-ven)
#   - 4e B : entrée 07:00-07:45 (lun-ven), sortie 15:30-16:15 (lun,mar,jeu,ven) et 12:00-12:45 (mercredi)
#   - Tle D : entrée 07:30-08:15 (lun-ven), sortie 16:00-16:45 (lun-ven)
CLASS_SCHEDULES = {
    "3e A": [
        ("entry", 1, time(7, 0), time(7, 45), "1,2,3,4,5"),
        ("exit", 2, time(15, 30), time(16, 15), "1,2,3,4,5"),
    ],
    "4e B": [
        ("entry", 1, time(7, 0), time(7, 45), "1,2,3,4,5"),
        ("exit", 2, time(15, 30), time(16, 15), "1,2,4,5"),
        ("exit", 3, time(12, 0), time(12, 45), "3"),
    ],
    "Tle D": [
        ("entry", 1, time(7, 30), time(8, 15), "1,2,3,4,5"),
        ("exit", 2, time(16, 0), time(16, 45), "1,2,3,4,5"),
    ],
}


def _recent_school_week_days(today: date, count: int = 5) -> list[date]:
    """Return the `count` most recent weekdays (Mon-Fri) ending on or before `today`, oldest first."""
    days: list[date] = []
    cursor = today
    while len(days) < count:
        if cursor.isoweekday() <= 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


class Command(BaseCommand):
    help = (
        "Peuple la base avec un jeu de données de départ reproductible et rejouable "
        "(1 responsable, 1 agent, 6 parents, 20 élèves sur 3 classes, horaires sur la semaine, "
        "badges actifs, 5 jours ouvrés de passages, anomalies et messages sortants)."
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
        students_by_name, badges_by_name, parent_by_student = self._seed_students_routes_and_badges(
            school=school,
            users=users,
            checkpoints=checkpoints,
        )
        self._seed_week_passages_and_anomalies(
            school=school,
            agent=users["agent"],
            checkpoints=checkpoints,
            students_by_name=students_by_name,
            badges_by_name=badges_by_name,
            parent_by_student=parent_by_student,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Jeu de données initial prêt (idempotent) : "
                f"{School.objects.count()} établissement(s), "
                f"{User.objects.count()} utilisateur(s), "
                f"{Student.objects.count()} élève(s) sur 3 classes, "
                f"{Badge.objects.filter(status=Badge.Status.ACTIVE).count()} badge(s) actif(s), "
                f"{Passage.objects.count()} passage(s), "
                f"{Anomaly.objects.count()} anomalie(s), "
                f"{OutgoingMessage.objects.count()} message(s)."
            )
        )

    def _seed_users(self, school: School) -> dict[str, User]:
        created_users: dict[str, User] = {}

        role_map = {
            "manager": (User.Role.MANAGER, school, True),
            "agent": (User.Role.AGENT, school, False),
            "parent1": (User.Role.PARENT, None, False),
            "parent2": (User.Role.PARENT, None, False),
            "parent3": (User.Role.PARENT, None, False),
            "parent4": (User.Role.PARENT, None, False),
            "parent5": (User.Role.PARENT, None, False),
            "parent6": (User.Role.PARENT, None, False),
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
    ) -> tuple[dict[str, Student], dict[str, Badge], dict[str, User]]:
        students_by_name: dict[str, Student] = {}
        badges_by_name: dict[str, Badge] = {}
        parent_by_student: dict[str, User] = {}
        manager = users["manager"]

        for first_name, last_name, classroom, parent_key, public_id in SEED_STUDENTS:
            student, _ = Student.objects.get_or_create(
                school=school,
                first_name=first_name,
                last_name=last_name,
                classroom=classroom,
                defaults={"status": Student.Status.ACTIVE},
            )
            parent = users[parent_key]
            ParentStudentLink.objects.get_or_create(parent=parent, student=student)

            for cp_key, order, start_t, end_t, weekdays in CLASS_SCHEDULES[classroom]:
                RouteWindow.objects.get_or_create(
                    student=student,
                    checkpoint=checkpoints[cp_key],
                    order=order,
                    defaults={
                        "start_time": start_t,
                        "end_time": end_t,
                        "weekdays": weekdays,
                        "is_active": True,
                    },
                )

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
            parent_by_student[first_name] = parent

        return students_by_name, badges_by_name, parent_by_student

    def _seed_week_passages_and_anomalies(
        self,
        *,
        school: School,
        agent: User,
        checkpoints: dict[str, Checkpoint],
        students_by_name: dict[str, Student],
        badges_by_name: dict[str, Badge],
        parent_by_student: dict[str, User],
    ) -> None:
        school_tz = ZoneInfo(school.timezone)
        today = timezone.now().astimezone(school_tz).date()
        week_days = _recent_school_week_days(today, count=5)
        device_id = "seed-device-01"

        student_index = {first: idx + 1 for idx, (first, *_rest) in enumerate(SEED_STUDENTS)}

        def make_dt(target_day: date, hour: int, minute: int, second: int = 0) -> datetime:
            return datetime(
                target_day.year,
                target_day.month,
                target_day.day,
                hour,
                minute,
                second,
                tzinfo=school_tz,
            )

        def create_passage(
            *,
            target_day: date,
            student_first_name: str,
            checkpoint: Checkpoint,
            hour: int,
            minute: int,
            second: int = 0,
            slot: int = 1,
            status: str = Passage.Status.VALID,
            rejection_reason: str = Passage.RejectionReason.NONE,
            clock_suspect: bool = False,
            clock_offset_seconds: int = 0,
        ) -> Passage:
            student = students_by_name[student_first_name]
            badge = badges_by_name[student_first_name]
            when = make_dt(target_day, hour, minute, second)
            local_raw = when - timedelta(seconds=clock_offset_seconds)
            idx = student_index[student_first_name]
            event_id = f"seed-{target_day.isoformat()}-s{idx:02d}-slot{slot}"
            time_step = target_day.toordinal() * 1000 + idx * 10 + slot
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
                    "local_scanned_at": local_raw,
                    "server_received_at": when,
                    "corrected_at": when,
                    "retained_at": when,
                    "clock_offset_seconds": clock_offset_seconds,
                    "clock_suspect": clock_suspect,
                    "status": status,
                    "rejection_reason": rejection_reason,
                    "sync_status": Passage.SyncStatus.SYNCED,
                },
            )
            return passage

        def create_anomaly_with_msg(
            *,
            target_day: date,
            student_first_name: str,
            checkpoint: Checkpoint,
            anomaly_type: str,
            details: str,
            body: str,
            passage: Passage | None = None,
            sent_at: datetime | None = None,
            anomaly_status: str = Anomaly.Status.OPEN,
            msg_status: str = OutgoingMessage.Status.SENT,
            non_delivery_reason: str = "",
        ) -> Anomaly:
            student = students_by_name[student_first_name]
            parent = parent_by_student[student_first_name]
            if anomaly_type == Anomaly.AnomalyType.POINT_MANQUANT:
                anomaly, _ = Anomaly.objects.get_or_create(
                    student=student,
                    date=target_day,
                    checkpoint=checkpoint,
                    anomaly_type=anomaly_type,
                    defaults={
                        "school": school,
                        "passage": None,
                        "status": anomaly_status,
                        "details": details[:255],
                        "detected_at": sent_at or make_dt(target_day, 8, 0),
                    },
                )
            else:
                anomaly, _ = Anomaly.objects.get_or_create(
                    passage=passage,
                    anomaly_type=anomaly_type,
                    defaults={
                        "school": school,
                        "student": student,
                        "checkpoint": checkpoint,
                        "date": target_day,
                        "status": anomaly_status,
                        "details": details[:255],
                        "detected_at": sent_at or (passage.retained_at if passage else make_dt(target_day, 8, 0)),
                    },
                )
            OutgoingMessage.objects.get_or_create(
                anomaly=anomaly,
                parent=parent,
                defaults={
                    "school": school,
                    "student": student,
                    "target_date": target_day,
                    "body": body,
                    "status": msg_status,
                    "non_delivery_reason": non_delivery_reason,
                    "sent_at": (sent_at or anomaly.detected_at) if msg_status == OutgoingMessage.Status.SENT else None,
                },
            )
            return anomaly

        # Populate all 5 school days (from oldest day 0 to most recent day 4).
        for day_idx, target_day in enumerate(week_days):
            is_today = day_idx == len(week_days) - 1
            is_wednesday = target_day.isoweekday() == 3

            # --- Baseline normal entries & exits for all 20 students ---
            # Skip specific students who have deliberate absences/delays on each day.
            absent_on_day = {
                0: {"Diane"},
                1: {"Yannick", "Olivier"},
                2: {"Farida"},
                3: {"Hervé"},
                4: {"Diane", "Yannick", "Olivier"},
            }[day_idx]

            late_on_day = {
                0: {"Samuel": (8, 8)},
                1: {"Nadia": (8, 4), "Brice": (8, 32)},
                2: {"Junior": (8, 1), "Gilles": (8, 28)},
                3: {"Samuel": (8, 15), "Carine": (8, 25)},
                4: {"Samuel": (8, 12), "Nadia": (8, 5), "Brice": (8, 30)},
            }[day_idx]

            early_exit_on_day = {
                0: {"Chloé": (14, 15)},
                1: {"Amina": (14, 25)},
                2: {},
                3: {"Estelle": (14, 40)},
                4: {"Samuel": (14, 10), "Carine": (14, 35)},
            }[day_idx]

            for first_name, _last, classroom, _parent_key, _pub_id in SEED_STUDENTS:
                idx = student_index[first_name]
                if first_name in absent_on_day:
                    # Record POINT_MANQUANT for absent students (handled on past days, open today).
                    create_anomaly_with_msg(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["entry"],
                        anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
                        details="Aucun passage valide au Portail d'entrée sur la fenêtre prévue.",
                        body=(
                            f"{school.name} : {first_name} n'a pas été enregistré(e) "
                            f"au point Portail d'entrée le {target_day:%d/%m/%Y}."
                        ),
                        sent_at=make_dt(target_day, 8, 26),
                        anomaly_status=Anomaly.Status.OPEN if is_today else Anomaly.Status.HANDLED,
                    )
                    continue

                if first_name in late_on_day:
                    lh, lm = late_on_day[first_name]
                    late_p = create_passage(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["entry"],
                        hour=lh,
                        minute=lm,
                        slot=1,
                    )
                    create_anomaly_with_msg(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["entry"],
                        anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
                        passage=late_p,
                        details=f"Passage au Portail d'entrée à {lh:02d} h {lm:02d} en dehors de la fenêtre prévue.",
                        body=(
                            f"{school.name} : {first_name} a été enregistré(e) au point Portail d'entrée "
                            f"à {lh:02d} h {lm:02d}, en dehors de l'horaire habituel."
                        ),
                        anomaly_status=Anomaly.Status.OPEN if is_today else Anomaly.Status.HANDLED,
                    )
                else:
                    # Normal on-time entry within the class's morning window.
                    entry_hour = 7
                    entry_min = (35 + (idx % 20)) if classroom == "Tle D" else (8 + (idx % 28))
                    create_passage(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["entry"],
                        hour=entry_hour,
                        minute=entry_min,
                        slot=1,
                        clock_suspect=(is_today and first_name == "Moussa"),
                        clock_offset_seconds=(-7200 if (is_today and first_name == "Moussa") else 0),
                    )

                # Exit passage: either deliberate off-window exit or normal exit on completed past days.
                if first_name in early_exit_on_day:
                    eh, em = early_exit_on_day[first_name]
                    exit_p = create_passage(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["exit"],
                        hour=eh,
                        minute=em,
                        slot=2,
                    )
                    create_anomaly_with_msg(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["exit"],
                        anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
                        passage=exit_p,
                        details=f"Passage au Portail de sortie à {eh:02d} h {em:02d} en dehors de la fenêtre prévue.",
                        body=(
                            f"{school.name} : {first_name} a été enregistré(e) au point Portail de sortie "
                            f"à {eh:02d} h {em:02d}, en dehors de l'horaire habituel."
                        ),
                        anomaly_status=Anomaly.Status.OPEN if is_today else Anomaly.Status.HANDLED,
                    )
                elif not is_today:
                    if classroom == "4e B" and is_wednesday:
                        ex_h, ex_m = 12, 10 + (idx % 25)
                    elif classroom == "Tle D":
                        ex_h, ex_m = 16, 8 + (idx % 25)
                    else:
                        ex_h, ex_m = 15, 35 + (idx % 25)
                    create_passage(
                        target_day=target_day,
                        student_first_name=first_name,
                        checkpoint=checkpoints["exit"],
                        hour=ex_h,
                        minute=ex_m,
                        slot=2,
                    )

        # --- Specific rich anomalies on the latest day (today) ---
        latest_day = week_days[-1]

        # 1. POINT_INCONNU : Clarisse (3e A) scannée à l'autobus ligne 1.
        bus_p = create_passage(
            target_day=latest_day,
            student_first_name="Clarisse",
            checkpoint=checkpoints["bus"],
            hour=7,
            minute=25,
            slot=3,
        )
        create_anomaly_with_msg(
            target_day=latest_day,
            student_first_name="Clarisse",
            checkpoint=checkpoints["bus"],
            anomaly_type=Anomaly.AnomalyType.POINT_INCONNU,
            passage=bus_p,
            details="Passage au point Autobus ligne 1 absent de l'itinéraire prévu ce jour.",
            body=(
                f"{school.name} : Clarisse a été enregistré(e) à 07 h 25 "
                "sur un point de contrôle inhabituel (Autobus ligne 1)."
            ),
        )

        # 2. DOUBLE_SCAN : Junior (3e A) scanné 25 s après son passage d'entrée.
        jr_base_min = 8 + (student_index["Junior"] % 28)
        double_p = create_passage(
            target_day=latest_day,
            student_first_name="Junior",
            checkpoint=checkpoints["entry"],
            hour=7,
            minute=jr_base_min,
            second=25,
            slot=3,
        )
        create_anomaly_with_msg(
            target_day=latest_day,
            student_first_name="Junior",
            checkpoint=checkpoints["entry"],
            anomaly_type=Anomaly.AnomalyType.DOUBLE_SCAN,
            passage=double_p,
            details="Deux passages valides enregistrés à 25 seconde(s) d'intervalle.",
            body=(
                f"{school.name} : deux passages rapprochés ont été enregistrés "
                f"pour Junior au point Portail d'entrée à 07 h {jr_base_min:02d}."
            ),
        )

        # 3. Scans rejetés (code expiré + signature invalide) pour la table « Scans rejetés ».
        create_passage(
            target_day=latest_day,
            student_first_name="Amina",
            checkpoint=checkpoints["entry"],
            hour=7,
            minute=32,
            slot=4,
            status=Passage.Status.REJECTED,
            rejection_reason=Passage.RejectionReason.EXPIRED_CODE,
        )
        create_passage(
            target_day=latest_day,
            student_first_name="Estelle",
            checkpoint=checkpoints["entry"],
            hour=7,
            minute=48,
            slot=4,
            status=Passage.Status.REJECTED,
            rejection_reason=Passage.RejectionReason.INVALID_SIGNATURE,
        )
