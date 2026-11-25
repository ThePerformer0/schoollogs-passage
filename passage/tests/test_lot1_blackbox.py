"""
Tests boîte noire - Lot 1 (authentification, rôles, validation, intégrité, seed_demo).

Exécution : python manage.py test passage.tests.test_lot1_blackbox
Aucune fonction interne de passage.services / passage.repositories n'est importée.
"""
import datetime
import re
import time
import uuid
from contextlib import ExitStack, contextmanager
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from passage.models import (
    Anomaly,
    AuditLog,
    Badge,
    Checkpoint,
    OutgoingMessage,
    ParentStudentLink,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)

PW = "Test!Passw0rd#2026"
XSS = '<script>alert("xss")</script>'
XSS_ESCAPED = "&lt;script&gt;"


# --------------------------------------------------------------------------
# Helpers de fabrication de données
# --------------------------------------------------------------------------
def make_school(name, code):
    return School.objects.create(name=name, code=code)


def make_user(username, role, school=None, full_name="", is_staff=False, is_active=True, password=PW):
    user = User(
        username=username,
        full_name=full_name or username,
        role=role,
        school=school,
        is_staff=is_staff,
        is_active=is_active,
    )
    user.set_password(password)
    user.save()
    return user


def make_student(school, first_name, last_name, classroom="6eA", status=None):
    return Student.objects.create(
        school=school,
        first_name=first_name,
        last_name=last_name,
        classroom=classroom,
        status=status or Student.Status.ACTIVE,
    )


def make_badge(student, status=None, replaced_badge=None):
    return Badge.objects.create(
        student=student,
        public_id=uuid.uuid4().hex,
        secret="SECRET-" + uuid.uuid4().hex,
        status=status or Badge.Status.ACTIVE,
        replaced_badge=replaced_badge,
    )


def make_checkpoint(school, label="Portail principal", cp_type=None):
    return Checkpoint.objects.create(
        school=school,
        label=label,
        checkpoint_type=cp_type or Checkpoint.CheckpointType.ENTRY_GATE,
    )


_step_counter = [1000]


def make_passage(badge, checkpoint, agent, **overrides):
    now = timezone.now()
    _step_counter[0] += 1
    data = dict(
        school=badge.student.school,
        badge=badge,
        student=badge.student,
        checkpoint=checkpoint,
        agent=agent,
        badge_public_id=badge.public_id,
        time_step=_step_counter[0],
        device_id="device-1",
        client_event_id=uuid.uuid4().hex,
        local_scanned_at=now,
        server_received_at=now,
        corrected_at=now,
        retained_at=now,
        status=Passage.Status.VALID,
        rejection_reason=Passage.RejectionReason.NONE,
    )
    data.update(overrides)
    return Passage.objects.create(**data)


def normalize_html(html):
    """Retire jeton CSRF et valeurs de champs réinjectées pour comparer deux pages."""
    html = re.sub(r"<input[^>]*csrfmiddlewaretoken[^>]*>", "", html)
    html = re.sub(r'value="[^"]*"', 'value=""', html)
    return re.sub(r"\s+", " ", html).strip()


def login(client, username, password=PW, ip="10.1.1.1"):
    return client.post(
        "/connexion/",
        {"username": username, "password": password},
        REMOTE_ADDR=ip,
    )


def is_authenticated(client):
    return "_auth_user_id" in client.session


# --------------------------------------------------------------------------
# Base commune
# --------------------------------------------------------------------------
class BaseData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school_a = make_school("Ecole Alpha", "ALPHA")
        cls.school_b = make_school("Ecole Beta", "BETA")

        cls.manager_a = make_user(
            "manager_a", User.Role.MANAGER, cls.school_a, "Resp Alpha", is_staff=True
        )
        cls.manager_b = make_user(
            "gestionnaire_beta_zz", User.Role.MANAGER, cls.school_b, "Resp Beta", is_staff=True
        )
        cls.agent_a = make_user("agent_a", User.Role.AGENT, cls.school_a, "Agent Alpha")
        cls.parent1 = make_user("parent_un", User.Role.PARENT, None, "ParentZzSecret")
        cls.parent2 = make_user("parent_deux", User.Role.PARENT, None, "Parent Deux")

        cls.student_a1 = make_student(cls.school_a, "Alphonse", "Zzkamdem")
        cls.student_a2 = make_student(cls.school_a, "Brunhilde", "Xxmbarga")
        cls.student_b1 = make_student(cls.school_b, "Betaprenom", "Qqnkoulou")

        ParentStudentLink.objects.create(parent=cls.parent1, student=cls.student_a1)
        ParentStudentLink.objects.create(parent=cls.parent2, student=cls.student_a2)

        cls.badge_a1 = make_badge(cls.student_a1)
        cls.badge_b1 = make_badge(cls.student_b1)

        cls.cp_a = make_checkpoint(cls.school_a, "Portail Alpha")
        cls.cp_b = make_checkpoint(cls.school_b, "Portail Beta ZzCheckpointB")

    def setUp(self):
        cache.clear()
        self.client = Client()

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c


# --------------------------------------------------------------------------
# 1. Authentification, blocage et sessions
# --------------------------------------------------------------------------
class AuthenticationTests(BaseData):
    def test_passwords_are_hashed_with_argon2id(self):
        for u in (self.manager_a, self.agent_a, self.parent1):
            u.refresh_from_db()
            self.assertTrue(u.password.startswith("argon2$argon2id$"), u.password)
            self.assertNotIn(PW, u.password)

    def test_same_error_message_in_four_failure_cases(self):
        make_user("inactif", User.Role.PARENT, None, is_active=False)
        make_user("bloque", User.Role.PARENT, None)
        limit = settings.PASSAGE_MAX_LOGIN_ATTEMPTS
        for _ in range(limit):
            login(Client(), "bloque", "mauvais", ip="10.9.9.9")

        c1, c2, c3, c4 = Client(), Client(), Client(), Client()
        r_unknown = login(c1, "inexistant", PW, ip="10.2.0.1")
        r_wrong = login(c2, "parent_un", "mauvais", ip="10.2.0.2")
        r_inactive = login(c3, "inactif", PW, ip="10.2.0.3")
        r_blocked = login(c4, "bloque", PW, ip="10.9.9.9")

        for c in (c1, c2, c3, c4):
            self.assertFalse(is_authenticated(c))
        codes = {r.status_code for r in (r_unknown, r_wrong, r_inactive, r_blocked)}
        self.assertEqual(len(codes), 1, f"Codes HTTP différents : {codes}")
        pages = {
            normalize_html(r.content.decode())
            for r in (r_unknown, r_wrong, r_inactive, r_blocked)
        }
        self.assertEqual(len(pages), 1, "Les messages d'erreur divergent selon le cas")

    def test_lockout_after_max_attempts_for_username_and_ip(self):
        limit = settings.PASSAGE_MAX_LOGIN_ATTEMPTS
        ip = "10.3.3.3"
        for _ in range(limit):
            login(Client(), "parent_un", "mauvais", ip=ip)

        # Même couple (username, IP) : refusé même avec le bon mot de passe
        c = Client()
        login(c, "parent_un", PW, ip=ip)
        self.assertFalse(is_authenticated(c))

        # Autre IP : autorisé
        c_other_ip = Client()
        login(c_other_ip, "parent_un", PW, ip="10.3.3.4")
        self.assertTrue(is_authenticated(c_other_ip))

        # Autre identifiant, même IP : autorisé
        c_other_user = Client()
        login(c_other_user, "parent_deux", PW, ip=ip)
        self.assertTrue(is_authenticated(c_other_user))

    def test_below_threshold_login_still_allowed(self):
        limit = settings.PASSAGE_MAX_LOGIN_ATTEMPTS
        for _ in range(limit - 1):
            login(Client(), "parent_un", "mauvais", ip="10.4.4.4")
        c = Client()
        login(c, "parent_un", PW, ip="10.4.4.4")
        self.assertTrue(is_authenticated(c))

    def test_redirect_to_role_screen(self):
        cases = [
            ("agent_a", "/scan/"),
            ("manager_a", "/responsable/"),
            ("parent_un", "/parent/"),
        ]
        for username, expected in cases:
            with self.subTest(username=username):
                c = Client()
                resp = login(c, username)
                self.assertRedirects(resp, expected, fetch_redirect_response=False)

    def test_session_duration_and_httponly_cookie(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 28800)
        c = Client()
        login(c, "parent_un")
        cookie = c.cookies[settings.SESSION_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])

    def test_logout_requires_post(self):
        c = self.client_for(self.parent1)
        self.assertEqual(c.get("/deconnexion/").status_code, 405)
        self.assertTrue(is_authenticated(c))

    def test_logout_destroys_server_side_session(self):
        c = Client()
        login(c, "parent_un")
        old_key = c.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(c.get("/parent/").status_code, 200)

        resp = c.post("/deconnexion/")
        self.assertIn(resp.status_code, (301, 302))

        replay = Client()
        replay.cookies[settings.SESSION_COOKIE_NAME] = old_key
        resp = replay.get("/parent/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/connexion/"), resp["Location"])

    def test_audit_log_for_login_failure_and_logout_without_password(self):
        secret_pw = "MotDePasseTresSecret!987"
        login(Client(), "parent_un", secret_pw, ip="10.5.5.5")
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.LOGIN_FAILURE, actor_identifier="parent_un"
            ).exists()
        )

        c = Client()
        login(c, "parent_un", PW, ip="10.5.5.6")
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.LOGIN_SUCCESS, actor=self.parent1
            ).exists()
        )
        c.post("/deconnexion/")
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.LOGOUT, actor=self.parent1).exists()
        )

        for log in AuditLog.objects.all():
            dump = " ".join(
                str(getattr(log, f.name, "")) for f in AuditLog._meta.get_fields()
                if hasattr(f, "attname")
            )
            self.assertNotIn(secret_pw, dump)
            self.assertNotIn(PW, dump)


# --------------------------------------------------------------------------
# 2. Rôles et cloisonnement
# --------------------------------------------------------------------------
class ParentIsolationTests(BaseData):
    def test_parent_accesses_own_children(self):
        c = self.client_for(self.parent1)
        self.assertEqual(c.get("/parent/").status_code, 200)
        resp = c.get(f"/parent/eleves/{self.student_a1.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_parent_denied_on_other_parents_student(self):
        c = self.client_for(self.parent1)
        before = AuditLog.objects.filter(action=AuditLog.Action.ACCESS_DENIED).count()
        resp = c.get(f"/parent/eleves/{self.student_a2.id}/")
        self.assertEqual(resp.status_code, 403)
        body = resp.content.decode()
        self.assertNotIn("Brunhilde", body)
        self.assertNotIn("Xxmbarga", body)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.ACCESS_DENIED).count(), before + 1
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ACCESS_DENIED,
                outcome=AuditLog.Outcome.DENIED,
                actor=self.parent1,
            ).exists()
        )

    def test_parent_denied_on_nonexistent_student(self):
        c = self.client_for(self.parent1)
        resp = c.get("/parent/eleves/99999999/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ACCESS_DENIED,
                outcome=AuditLog.Outcome.DENIED,
                actor=self.parent1,
            ).exists()
        )

    def test_parent_dashboard_does_not_list_other_children(self):
        resp = self.client_for(self.parent1).get("/parent/")
        body = resp.content.decode()
        self.assertNotIn("Brunhilde", body)
        self.assertNotIn("Betaprenom", body)

    def test_parent_denied_on_manager_and_admin_pages(self):
        c = self.client_for(self.parent1)
        for url in ("/responsable/", "/responsable/audit/", "/admin/"):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 403)


class ManagerIsolationTests(BaseData):
    def test_manager_a_sees_no_school_b_data(self):
        AuditLog.objects.create(
            occurred_at=timezone.now(),
            school=self.school_b,
            actor=self.manager_b,
            actor_identifier="intrus-etab-B-xyz",
            action=AuditLog.Action.LOGIN_SUCCESS,
            target_type="user",
            target_id=str(self.manager_b.id),
            outcome=AuditLog.Outcome.SUCCESS,
            ip_address="10.8.8.8",
            details={},
        )
        login(Client(), "gestionnaire_beta_zz", PW, ip="10.8.8.9")

        c = self.client_for(self.manager_a)
        for url in ("/responsable/", "/responsable/audit/"):
            with self.subTest(url=url):
                resp = c.get(url)
                self.assertEqual(resp.status_code, 200)
                body = resp.content.decode()
                for forbidden in (
                    "Betaprenom",
                    "Qqnkoulou",
                    "Ecole Beta",
                    "ZzCheckpointB",
                    "gestionnaire_beta_zz",
                    "intrus-etab-B-xyz",
                    self.badge_b1.secret,
                ):
                    self.assertNotIn(forbidden, body)

    def test_manager_a_can_see_own_student(self):
        resp = self.client_for(self.manager_a).get(f"/responsable/eleves/{self.student_a1.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_manager_a_denied_on_school_b_student(self):
        c = self.client_for(self.manager_a)
        resp = c.get(f"/responsable/eleves/{self.student_b1.id}/")
        self.assertEqual(resp.status_code, 403)
        body = resp.content.decode()
        self.assertNotIn("Betaprenom", body)
        self.assertNotIn("Qqnkoulou", body)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ACCESS_DENIED, actor=self.manager_a
            ).exists()
        )

    def test_manager_denied_on_parent_and_scan_screens_is_not_required_but_parent_page_denied(self):
        # Le responsable n'est pas un parent : /parent/ ne doit pas exposer de données.
        resp = self.client_for(self.manager_a).get("/parent/")
        self.assertIn(resp.status_code, (302, 403))
        self.assertNotIn("Alphonse", resp.content.decode())


class AgentRestrictionTests(BaseData):
    def test_agent_scan_only_without_personal_data(self):
        Anomaly.objects.create(
            school=self.school_a,
            student=self.student_a1,
            checkpoint=self.cp_a,
            passage=None,
            anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
            date=timezone.localdate(),
        )
        c = self.client_for(self.agent_a)
        resp = c.get("/scan/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        for forbidden in ("Alphonse", "Zzkamdem", "Brunhilde", "ParentZzSecret"):
            self.assertNotIn(forbidden, body)
        self.assertNotIn("POINT_MANQUANT", body)
        self.assertNotIn("anomalie", body.lower())

    def test_agent_denied_elsewhere(self):
        c = self.client_for(self.agent_a)
        for url in ("/parent/", "/responsable/", "/responsable/audit/", "/admin/"):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 403)


class AdminSiteTests(BaseData):
    def test_manager_can_open_admin(self):
        self.assertEqual(self.client_for(self.manager_a).get("/admin/").status_code, 200)

    def test_agent_and_parent_denied_admin(self):
        for user in (self.agent_a, self.parent1):
            with self.subTest(user=user.username):
                self.assertEqual(self.client_for(user).get("/admin/").status_code, 403)

    def test_manager_a_cannot_open_school_b_student_in_admin(self):
        c = self.client_for(self.manager_a)
        resp = c.get(f"/admin/passage/student/{self.student_b1.id}/change/")
        self.assertNotEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("Betaprenom", body)
        self.assertNotIn("Qqnkoulou", body)

    def test_badge_secret_never_displayed_in_admin(self):
        c = self.client_for(self.manager_a)
        for badge in (self.badge_a1, self.badge_b1):
            with self.subTest(badge=badge.id):
                resp = c.get(f"/admin/passage/badge/{badge.id}/change/")
                self.assertNotIn(badge.secret, resp.content.decode())


# --------------------------------------------------------------------------
# 3. Validation serveur, CSRF, XSS
# --------------------------------------------------------------------------
class CsrfTests(BaseData):
    def test_post_without_csrf_token_is_rejected(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.manager_a)
        payloads = {
            "/connexion/": {"username": "manager_a", "password": PW},
            "/deconnexion/": {},
            "/responsable/eleves/nouveau/": {
                "first_name": "Csrf",
                "last_name": "Test",
                "classroom": "6eA",
                "status": "active",
            },
        }
        for url, data in payloads.items():
            with self.subTest(url=url):
                self.assertEqual(c.post(url, data).status_code, 403)
        self.assertFalse(Student.objects.filter(first_name="Csrf").exists())

    def test_anonymous_login_post_without_csrf_rejected(self):
        c = Client(enforce_csrf_checks=True)
        resp = c.post("/connexion/", {"username": "manager_a", "password": PW})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(is_authenticated(c))


class RouteWindowValidationTests(BaseData):
    URL = "/responsable/itineraires/nouveau/"

    def _payload(self, **kw):
        data = {
            "student": self.student_a1.id,
            "checkpoint": self.cp_a.id,
            "order": 1,
            "start_time": "07:00",
            "end_time": "08:00",
            "weekdays": "1,2,3,4,5",
            "is_active": "on",
        }
        data.update(kw)
        return data

    def test_valid_window_is_created(self):
        c = self.client_for(self.manager_a)
        c.post(self.URL, self._payload())
        self.assertEqual(RouteWindow.objects.filter(student=self.student_a1).count(), 1)

    def test_end_before_or_equal_start_rejected(self):
        c = self.client_for(self.manager_a)
        for start, end in (("08:00", "07:00"), ("08:00", "08:00")):
            with self.subTest(start=start, end=end):
                c.post(self.URL, self._payload(start_time=start, end_time=end))
                self.assertEqual(RouteWindow.objects.count(), 0)

    def test_checkpoint_from_other_school_rejected(self):
        c = self.client_for(self.manager_a)
        c.post(self.URL, self._payload(checkpoint=self.cp_b.id))
        self.assertEqual(RouteWindow.objects.count(), 0)

    def test_student_from_other_school_rejected(self):
        c = self.client_for(self.manager_a)
        c.post(self.URL, self._payload(student=self.student_b1.id))
        self.assertEqual(RouteWindow.objects.count(), 0)


class XssEscapingTests(BaseData):
    def test_script_payload_is_escaped_on_manager_and_parent_pages(self):
        evil = make_student(self.school_a, XSS, "Evil", classroom=XSS)
        ParentStudentLink.objects.create(parent=self.parent1, student=evil)

        manager = self.client_for(self.manager_a)
        parent = self.client_for(self.parent1)
        pages = [
            (manager, "/responsable/"),
            (manager, f"/responsable/eleves/{evil.id}/"),
            (parent, "/parent/"),
            (parent, f"/parent/eleves/{evil.id}/"),
        ]
        for client, url in pages:
            with self.subTest(url=url):
                resp = client.get(url)
                self.assertEqual(resp.status_code, 200)
                body = resp.content.decode()
                self.assertNotIn(XSS, body)
                self.assertNotIn('<script>alert("xss")', body)
                self.assertIn(XSS_ESCAPED, body)


# --------------------------------------------------------------------------
# 4. Contraintes d'intégrité
# --------------------------------------------------------------------------
class IntegrityTests(BaseData):
    def test_two_active_badges_for_same_student_forbidden(self):
        student = make_student(self.school_a, "Uni", "Badge")
        make_badge(student, Badge.Status.ACTIVE)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_badge(student, Badge.Status.ACTIVE)

    def test_one_revoked_and_one_active_badge_allowed(self):
        student = make_student(self.school_a, "Uni", "Revoque")
        make_badge(student, Badge.Status.REVOKED)
        make_badge(student, Badge.Status.ACTIVE)
        self.assertEqual(Badge.objects.filter(student=student).count(), 2)

    def test_duplicate_device_and_client_event_forbidden(self):
        make_passage(
            self.badge_a1, self.cp_a, self.agent_a, device_id="dev-X", client_event_id="evt-1"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_passage(
                    self.badge_a1,
                    self.cp_a,
                    self.agent_a,
                    device_id="dev-X",
                    client_event_id="evt-1",
                    time_step=999999,
                )

    def test_same_client_event_on_other_device_allowed(self):
        make_passage(
            self.badge_a1, self.cp_a, self.agent_a, device_id="dev-X", client_event_id="evt-2"
        )
        make_passage(
            self.badge_a1, self.cp_a, self.agent_a, device_id="dev-Y", client_event_id="evt-2"
        )
        self.assertEqual(Passage.objects.filter(client_event_id="evt-2").count(), 2)

    def test_two_valid_passages_same_badge_and_time_step_forbidden(self):
        make_passage(self.badge_a1, self.cp_a, self.agent_a, time_step=424242)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_passage(
                    self.badge_a1,
                    self.cp_a,
                    self.agent_a,
                    time_step=424242,
                    client_event_id=uuid.uuid4().hex,
                )

    def test_rejected_already_used_duplicate_allowed(self):
        make_passage(self.badge_a1, self.cp_a, self.agent_a, time_step=434343)
        make_passage(
            self.badge_a1,
            self.cp_a,
            self.agent_a,
            time_step=434343,
            client_event_id=uuid.uuid4().hex,
            status=Passage.Status.REJECTED,
            rejection_reason=Passage.RejectionReason.ALREADY_USED,
        )
        self.assertEqual(Passage.objects.filter(time_step=434343).count(), 2)

    def _anomaly(self, anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT, day=None):
        return Anomaly.objects.create(
            school=self.school_a,
            student=self.student_a1,
            checkpoint=self.cp_a,
            passage=None,
            anomaly_type=anomaly_type,
            date=day or datetime.date(2026, 3, 2),
        )

    def test_duplicate_point_manquant_anomaly_forbidden(self):
        self._anomaly()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._anomaly()

    def test_point_manquant_on_other_date_allowed(self):
        self._anomaly(day=datetime.date(2026, 3, 2))
        self._anomaly(day=datetime.date(2026, 3, 3))
        self.assertEqual(
            Anomaly.objects.filter(anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT).count(), 2
        )

    def test_duplicate_outgoing_message_per_anomaly_and_parent_forbidden(self):
        anomaly = self._anomaly()

        def msg():
            return OutgoingMessage.objects.create(
                school=self.school_a,
                parent=self.parent1,
                student=self.student_a1,
                anomaly=anomaly,
                target_date=anomaly.date,
                body="Votre enfant n'a pas été scanné.",
            )

        msg()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                msg()

    def test_route_window_end_not_after_start_forbidden(self):
        for start, end in (
            (datetime.time(8, 0), datetime.time(8, 0)),
            (datetime.time(8, 0), datetime.time(7, 0)),
        ):
            with self.subTest(start=start, end=end):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        RouteWindow.objects.create(
                            student=self.student_a1,
                            checkpoint=self.cp_a,
                            order=1,
                            start_time=start,
                            end_time=end,
                        )


# --------------------------------------------------------------------------
# 5. Commande seed_demo
# --------------------------------------------------------------------------
class SeedDemoTests(TestCase):
    ACCOUNTS = {
        "responsable": ("DemoResp!2026", "/responsable/"),
        "agent": ("DemoAgent!2026", "/scan/"),
        "parent1": ("DemoParent1!2026", "/parent/"),
        "parent2": ("DemoParent2!2026", "/parent/"),
    }

    def setUp(self):
        cache.clear()

    def _seed(self):
        call_command("seed_demo", stdout=StringIO(), stderr=StringIO())

    def _counts(self):
        return {
            model.__name__: model.objects.count()
            for model in (
                School, User, Student, ParentStudentLink, Checkpoint, RouteWindow,
                Badge, Passage, Anomaly, OutgoingMessage,
            )
        }

    def test_seed_creates_expected_data(self):
        self._seed()
        self.assertGreaterEqual(User.objects.filter(role=User.Role.MANAGER).count(), 1)
        self.assertGreaterEqual(User.objects.filter(role=User.Role.AGENT).count(), 1)
        self.assertGreaterEqual(User.objects.filter(role=User.Role.PARENT).count(), 2)
        self.assertGreaterEqual(Student.objects.count(), 5)

        # Un parent rattaché à au moins 2 élèves
        per_parent = {}
        for link in ParentStudentLink.objects.all():
            per_parent.setdefault(link.parent_id, set()).add(link.student_id)
        self.assertTrue(any(len(v) >= 2 for v in per_parent.values()))

        types = set(Checkpoint.objects.values_list("checkpoint_type", flat=True))
        self.assertIn(Checkpoint.CheckpointType.ENTRY_GATE, types)
        self.assertIn(Checkpoint.CheckpointType.EXIT_GATE, types)

        windows = RouteWindow.objects.all()
        self.assertTrue(any(w.start_time.hour < 12 for w in windows), "pas de fenêtre du matin")
        self.assertTrue(any(w.start_time.hour >= 12 for w in windows), "pas de fenêtre du soir")

        for student in Student.objects.all():
            self.assertEqual(
                Badge.objects.filter(student=student, status=Badge.Status.ACTIVE).count(),
                1,
                f"badge actif attendu pour {student}",
            )

        self.assertGreaterEqual(Passage.objects.count(), 1)
        distinct_types = set(Anomaly.objects.values_list("anomaly_type", flat=True))
        self.assertGreaterEqual(len(distinct_types), 3, distinct_types)

    def test_seed_is_replayable_without_duplicates(self):
        self._seed()
        first = self._counts()
        self._seed()
        self.assertEqual(self._counts(), first)

    def test_demo_accounts_use_argon2id_and_can_login(self):
        self._seed()
        for username, (password, target) in self.ACCOUNTS.items():
            with self.subTest(username=username):
                user = User.objects.get(username=username)
                self.assertTrue(user.password.startswith("argon2$argon2id$"))
                c = Client()
                resp = login(c, username, password, ip="10.7.7.7")
                self.assertRedirects(resp, target, fetch_redirect_response=False)
                self.assertTrue(is_authenticated(c))


# --------------------------------------------------------------------------
# Outils de manipulation du temps (boîte noire : aucune donnée interne touchée)
# --------------------------------------------------------------------------
@contextmanager
def time_travel(seconds):
    """
    Avance l'horloge vue par l'application de `seconds` secondes.

    Méthode privilégiée : `freezegun` (pip install freezegun), qui couvre
    timezone.now(), datetime.now() et time.time() partout dans le code.
    Repli sans dépendance : patch de django.utils.timezone.now et de time.time
    (suffisant pour les backends de session DB/cache et pour un blocage
    calculé via timezone.now()).
    """
    delta = datetime.timedelta(seconds=seconds)
    try:
        from freezegun import freeze_time
    except ImportError:
        freeze_time = None

    if freeze_time is not None:
        with freeze_time(timezone.now() + delta, tick=True):
            yield
        return

    real_now = timezone.now
    real_time = time.time
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch("django.utils.timezone.now", side_effect=lambda: real_now() + delta)
        )
        stack.enter_context(
            mock.patch("time.time", side_effect=lambda: real_time() + seconds)
        )
        yield


def _duration_seconds(setting_name, default_seconds, small_value_unit_seconds):
    """Lit une durée de settings : timedelta, secondes, ou petit entier (heures/minutes)."""
    value = getattr(settings, setting_name, None)
    if value is None:
        return default_seconds
    if isinstance(value, datetime.timedelta):
        return int(value.total_seconds())
    value = int(value)
    return value * small_value_unit_seconds if value < 100 else value


def session_duration_seconds():
    return _duration_seconds("PASSAGE_SESSION_DURATION", 8 * 3600, 3600)


def lockout_duration_seconds():
    return _duration_seconds("PASSAGE_LOGIN_LOCKOUT_DURATION", 15 * 60, 60)


# --------------------------------------------------------------------------
# 6. Expiration réelle de la session
# --------------------------------------------------------------------------
class SessionExpirationTests(BaseData):
    ROLE_PAGES = (
        ("agent_a", "/scan/"),
        ("manager_a", "/responsable/"),
        ("parent_un", "/parent/"),
    )

    def _assert_denied(self, response):
        self.assertIn(response.status_code, (302, 401, 403), response.status_code)
        if response.status_code == 302:
            self.assertTrue(response["Location"].startswith("/connexion/"), response["Location"])
        body = response.content.decode()
        for private in ("Alphonse", "Zzkamdem", "Brunhilde", "Ecole Alpha"):
            self.assertNotIn(private, body)

    def test_session_still_valid_just_before_max_duration(self):
        duration = session_duration_seconds()
        for i, (username, page) in enumerate(self.ROLE_PAGES):
            with self.subTest(username=username):
                c = Client()
                login(c, username, ip=f"10.20.0.{i + 1}")
                with time_travel(duration - 60):
                    self.assertEqual(c.get(page).status_code, 200)

    def test_session_rejected_after_max_duration(self):
        duration = session_duration_seconds()
        for i, (username, page) in enumerate(self.ROLE_PAGES):
            with self.subTest(username=username):
                c = Client()
                login(c, username, ip=f"10.21.0.{i + 1}")
                self.assertEqual(c.get(page).status_code, 200)
                key = c.cookies[settings.SESSION_COOKIE_NAME].value

                with time_travel(duration + 5):
                    # Rejeu du cookie par un client neuf
                    replay = Client()
                    replay.cookies[settings.SESSION_COOKIE_NAME] = key
                    self._assert_denied(replay.get(page))
                    # Même client que celui de la connexion
                    self._assert_denied(c.get(page))
                    self.assertFalse(is_authenticated(c))

    def test_new_login_works_after_previous_session_expired(self):
        duration = session_duration_seconds()
        c = Client()
        login(c, "parent_un", ip="10.22.0.1")
        with time_travel(duration + 5):
            self._assert_denied(c.get("/parent/"))
            resp = login(c, "parent_un", ip="10.22.0.1")
            self.assertRedirects(resp, "/parent/", fetch_redirect_response=False)
            self.assertEqual(c.get("/parent/").status_code, 200)


# --------------------------------------------------------------------------
# 7. Caractère temporaire du blocage anti-force brute
# --------------------------------------------------------------------------
class LockoutExpirationTests(BaseData):
    def _lock(self, username, ip):
        for _ in range(settings.PASSAGE_MAX_LOGIN_ATTEMPTS):
            login(Client(), username, "mauvais", ip=ip)

    def test_lockout_still_active_just_before_end_of_duration(self):
        window = min(lockout_duration_seconds(), 15 * 60)
        ip = "10.30.0.1"
        self._lock("parent_un", ip)
        with time_travel(window - 60):
            c = Client()
            login(c, "parent_un", PW, ip=ip)
            self.assertFalse(is_authenticated(c))

    def test_same_pair_can_login_after_lockout_duration(self):
        wait = max(lockout_duration_seconds(), 15 * 60) + 5
        ip = "10.30.0.2"
        self._lock("parent_un", ip)  # aucune tentative pendant le blocage
        with time_travel(wait):
            c = Client()
            resp = login(c, "parent_un", PW, ip=ip)
            self.assertRedirects(resp, "/parent/", fetch_redirect_response=False)
            self.assertTrue(is_authenticated(c))
            self.assertEqual(c.get("/parent/").status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.LOGIN_SUCCESS, actor=self.parent1
            ).exists()
        )

    def test_old_failures_do_not_count_after_lockout_expiry(self):
        wait = max(lockout_duration_seconds(), 15 * 60) + 5
        ip = "10.30.0.3"
        self._lock("parent_un", ip)
        with time_travel(wait):
            # Un seul échec récent ne doit pas rebloquer le couple...
            login(Client(), "parent_un", "mauvais", ip=ip)
            c = Client()
            login(c, "parent_un", PW, ip=ip)
            self.assertTrue(is_authenticated(c))

    def test_lockout_expiry_is_scoped_to_the_pair_only(self):
        wait = max(lockout_duration_seconds(), 15 * 60) + 5
        self._lock("parent_un", "10.30.0.4")
        with time_travel(wait):
            c = Client()
            login(c, "parent_un", PW, ip="10.30.0.4")
            self.assertTrue(is_authenticated(c))


# --------------------------------------------------------------------------
# 8. Isolation des rôles en écriture et sur /scan/
# --------------------------------------------------------------------------
class RoleWriteIsolationTests(BaseData):
    STUDENT_URL = "/responsable/eleves/nouveau/"
    CHECKPOINT_URL = "/responsable/points/nouveau/"
    ROUTE_URL = "/responsable/itineraires/nouveau/"

    def _snapshot(self):
        return (
            Student.objects.count(),
            Checkpoint.objects.count(),
            RouteWindow.objects.count(),
        )

    def _write_payloads(self, tag):
        return {
            self.STUDENT_URL: {
                "first_name": f"Ecriture{tag}",
                "last_name": "Interdite",
                "classroom": "6eA",
                "status": Student.Status.ACTIVE.value,
            },
            self.CHECKPOINT_URL: {
                "label": f"PointEcriture{tag}",
                "checkpoint_type": Checkpoint.CheckpointType.ENTRY_GATE.value,
                "is_active": "on",
            },
            self.ROUTE_URL: {
                "student": self.student_a1.id,
                "checkpoint": self.cp_a.id,
                "order": 1,
                "start_time": "07:00",
                "end_time": "08:00",
                "weekdays": "1,2,3,4,5",
                "is_active": "on",
            },
        }

    # ---- /scan/ ---------------------------------------------------------
    def test_parent_denied_on_scan(self):
        resp = self.client_for(self.parent1).get("/scan/")
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("Alphonse", resp.content.decode())

    def test_agent_still_reaches_scan(self):
        self.assertEqual(self.client_for(self.agent_a).get("/scan/").status_code, 200)

    # ---- Agent / Parent : écriture interdite ----------------------------
    def test_agent_cannot_write_anything(self):
        c = self.client_for(self.agent_a)
        before = self._snapshot()
        for url, data in self._write_payloads("Agent").items():
            with self.subTest(url=url):
                self.assertEqual(c.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(Student.objects.filter(first_name="EcritureAgent").exists())
        self.assertFalse(Checkpoint.objects.filter(label="PointEcritureAgent").exists())

    def test_agent_write_denied_even_with_csrf_enforced(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.agent_a)
        before = self._snapshot()
        for url, data in self._write_payloads("AgentCsrf").items():
            with self.subTest(url=url):
                self.assertEqual(c.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_parent_cannot_write_anything(self):
        c = self.client_for(self.parent1)
        before = self._snapshot()
        for url, data in self._write_payloads("Parent").items():
            with self.subTest(url=url):
                self.assertEqual(c.post(url, data).status_code, 403)
        self.assertEqual(self._snapshot(), before)

    def test_anonymous_cannot_write_anything(self):
        c = Client()
        before = self._snapshot()
        for url, data in self._write_payloads("Anonyme").items():
            with self.subTest(url=url):
                resp = c.post(url, data)
                self.assertIn(resp.status_code, (302, 401, 403))
                if resp.status_code == 302:
                    self.assertTrue(resp["Location"].startswith("/connexion/"))
        self.assertEqual(self._snapshot(), before)

    # ---- Responsable : pas de contournement inter-établissements -------
    def test_manager_creates_student_and_checkpoint_in_own_school(self):
        """Témoin positif : sans lui, les tests négatifs ci-dessous seraient vides."""
        c = self.client_for(self.manager_a)
        c.post(self.STUDENT_URL, self._write_payloads("Legitime")[self.STUDENT_URL])
        c.post(self.CHECKPOINT_URL, self._write_payloads("Legitime")[self.CHECKPOINT_URL])
        student = Student.objects.get(first_name="EcritureLegitime")
        checkpoint = Checkpoint.objects.get(label="PointEcritureLegitime")
        self.assertEqual(student.school_id, self.school_a.id)
        self.assertEqual(checkpoint.school_id, self.school_a.id)

    def test_manager_cannot_create_student_in_other_school(self):
        c = self.client_for(self.manager_a)
        b_students = Student.objects.filter(school=self.school_b).count()
        for field in ("school", "school_id", "school_code"):
            with self.subTest(field=field):
                data = self._write_payloads(f"Inj{field}")[self.STUDENT_URL]
                data[field] = self.school_b.id if field != "school_code" else self.school_b.code
                c.post(self.STUDENT_URL, data)
                self.assertFalse(
                    Student.objects.filter(
                        first_name=data["first_name"], school=self.school_b
                    ).exists()
                )
        self.assertEqual(Student.objects.filter(school=self.school_b).count(), b_students)
        for created in Student.objects.filter(first_name__startswith="EcritureInj"):
            self.assertEqual(created.school_id, self.school_a.id)

    def test_manager_cannot_create_checkpoint_in_other_school(self):
        c = self.client_for(self.manager_a)
        b_points = Checkpoint.objects.filter(school=self.school_b).count()
        for field in ("school", "school_id", "school_code"):
            with self.subTest(field=field):
                data = self._write_payloads(f"Inj{field}")[self.CHECKPOINT_URL]
                data[field] = self.school_b.id if field != "school_code" else self.school_b.code
                c.post(self.CHECKPOINT_URL, data)
                self.assertFalse(
                    Checkpoint.objects.filter(label=data["label"], school=self.school_b).exists()
                )
        self.assertEqual(Checkpoint.objects.filter(school=self.school_b).count(), b_points)
        for created in Checkpoint.objects.filter(label__startswith="PointEcritureInj"):
            self.assertEqual(created.school_id, self.school_a.id)

    def test_manager_cannot_build_route_with_other_school_objects(self):
        c = self.client_for(self.manager_a)
        combos = (
            (self.student_b1, self.cp_a),
            (self.student_b1, self.cp_b),
            (self.student_a1, self.cp_b),
        )
        for student, checkpoint in combos:
            with self.subTest(student=student.id, checkpoint=checkpoint.id):
                data = self._write_payloads("Route")[self.ROUTE_URL]
                data["student"] = student.id
                data["checkpoint"] = checkpoint.id
                c.post(self.ROUTE_URL, data)
                self.assertEqual(RouteWindow.objects.count(), 0)
        self.assertFalse(RouteWindow.objects.filter(student=self.student_b1).exists())

    def test_school_b_data_untouched_after_cross_school_attempts(self):
        c = self.client_for(self.manager_a)
        student_b_before = (self.student_b1.first_name, self.student_b1.classroom)
        for url, data in self._write_payloads("Contournement").items():
            data = dict(data, school=self.school_b.id)
            if url == self.ROUTE_URL:
                data["student"] = self.student_b1.id
                data["checkpoint"] = self.cp_b.id
            c.post(url, data)
        self.student_b1.refresh_from_db()
        self.assertEqual(
            (self.student_b1.first_name, self.student_b1.classroom), student_b_before
        )
        self.assertEqual(Student.objects.filter(school=self.school_b).count(), 1)
        self.assertEqual(Checkpoint.objects.filter(school=self.school_b).count(), 1)
        self.assertEqual(RouteWindow.objects.filter(student__school=self.school_b).count(), 0)