import hashlib
import hmac
import json
import math
import uuid
from datetime import timedelta
from urllib.parse import urlparse
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from passage.models import (
    School,
    User,
    Student,
    Checkpoint,
    Badge,
    BadgeStatusHistory,
    BadgeActivationLink,
    Passage,
    ClockOffset,
    Anomaly,
    AuditLog,
)


UserModel = get_user_model()


class BlackBoxBadgeTests(TestCase):
    """Tests boîte noire adversariaux du système de badges."""

    STEP_SECONDS = 30
    SYNC_URL = "/api/sync/"
    CHECKPOINTS_URL = "/api/checkpoints/"
    TIME_URL = "/api/time/"
    BADGE_LIST_URL = "/responsable/badges/"

    def setUp(self):
        self.now = timezone.now()

        self.school_a = self.create_school("Établissement A")
        self.school_b = self.create_school("Établissement B")

        self.responsable_a = self.create_user(
            "responsable_a",
            role=User.Role.MANAGER,
            school=self.school_a,
        )
        self.responsable_b = self.create_user(
            "responsable_b",
            role=User.Role.MANAGER,
            school=self.school_b,
        )
        self.agent_a = self.create_user(
            "agent_a",
            role=User.Role.AGENT,
            school=self.school_a,
        )
        self.agent_b = self.create_user(
            "agent_b",
            role=User.Role.AGENT,
            school=self.school_b,
        )
        self.parent_a = self.create_user(
            "parent_a",
            role=User.Role.PARENT,
            school=self.school_a,
        )

        self.student_a1 = self.create_student(
            self.school_a, "Alice", "Martin"
        )
        self.student_a2 = self.create_student(
            self.school_a, "Bob", "Dupont"
        )
        self.student_a3 = self.create_student(
            self.school_a, "Charlie", "Durand"
        )
        self.student_b1 = self.create_student(
            self.school_b, "ForeignStudent", "Test"
        )

        self.checkpoint_a1 = self.create_checkpoint(
            self.school_a, "Portail principal"
        )
        self.checkpoint_a2 = self.create_checkpoint(
            self.school_a, "Portail secondaire"
        )
        self.checkpoint_a_inactive = self.create_checkpoint(
            self.school_a, "Ancien portail", active=False
        )
        self.checkpoint_b1 = self.create_checkpoint(
            self.school_b, "Portail B"
        )

        self.client_a = Client()
        self.client_a.force_login(self.responsable_a)

        self.agent_client = Client()
        self.agent_client.force_login(self.agent_a)

        self.parent_client = Client()
        self.parent_client.force_login(self.parent_a)

        self.responsable_b_client = Client()
        self.responsable_b_client.force_login(self.responsable_b)

        self.anonymous_client = Client()

    # ------------------------------------------------------------------
    # Factories / helpers
    # ------------------------------------------------------------------

    def create_school(self, name):
        fields = self.model_fields(School)
        kwargs = {}

        for field in ("name", "label", "nom"):
            if field in fields:
                kwargs[field] = name
                break

        if "slug" in fields:
            kwargs["slug"] = f"{uuid.uuid4().hex[:10]}"

        if "code" in fields:
            kwargs["code"] = uuid.uuid4().hex[:10].upper()

        return School.objects.create(**kwargs)

    def create_user(self, username, role, school):
        fields = self.model_fields(UserModel)
        kwargs = {}

        if "username" in fields:
            kwargs["username"] = username
        elif "email" in fields:
            kwargs["email"] = f"{username}@example.test"

        if "password" in fields:
            kwargs["password"] = "Test-password-123!"

        role_field = next(
            (
                field
                for field in ("role", "user_type", "type")
                if field in fields
            ),
            None,
        )
        if role_field:
            kwargs[role_field] = role

        school_field = next(
            (
                field
                for field in ("school", "etablissement", "establishment")
                if field in fields
            ),
            None,
        )
        if school_field:
            kwargs[school_field] = school

        user = UserModel.objects.create(**kwargs)

        if hasattr(user, "set_password"):
            user.set_password("Test-password-123!")
            user.save()

        return user

    def create_student(self, school, first_name, last_name="Test"):
        fields = self.model_fields(Student)
        kwargs = {"classroom": "3e A"}

        name_field = next(
            (
                field
                for field in ("first_name", "prenom", "name", "nom")
                if field in fields
            ),
            None,
        )
        if name_field:
            kwargs[name_field] = first_name

        if "last_name" in fields:
            kwargs["last_name"] = last_name
        elif "nom" in fields and name_field != "nom":
            kwargs["nom"] = last_name

        if "matricule" in fields:
            kwargs["matricule"] = f"MAT-{uuid.uuid4().hex[:8]}"

        school_field = next(
            (
                field
                for field in ("school", "etablissement", "establishment")
                if field in fields
            ),
            None,
        )
        if school_field:
            kwargs[school_field] = school

        return Student.objects.create(**kwargs)

    def create_checkpoint(self, school, label, active=True):
        fields = self.model_fields(Checkpoint)
        kwargs = {}

        label_field = next(
            (
                field
                for field in ("label", "name", "nom")
                if field in fields
            ),
            None,
        )
        if label_field:
            kwargs[label_field] = label

        school_field = next(
            (
                field
                for field in ("school", "etablissement", "establishment")
                if field in fields
            ),
            None,
        )
        if school_field:
            kwargs[school_field] = school

        active_field = next(
            (
                field
                for field in ("is_active", "active", "actif")
                if field in fields
            ),
            None,
        )
        if active_field:
            kwargs[active_field] = active

        if "checkpoint_type" in fields:
            kwargs["checkpoint_type"] = Checkpoint.CheckpointType.ENTRY_GATE

        return Checkpoint.objects.create(**kwargs)

    @staticmethod
    def model_fields(model):
        return {
            field.name
            for field in model._meta.get_fields()
            if getattr(field, "concrete", False)
        }

    def json_post(self, client, url, payload, csrf=True):
        headers = {"HTTP_ACCEPT": "application/json"}

        if csrf:
            headers["HTTP_X_CSRFTOKEN"] = "test-csrf-token"

        return client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def responsible_post(self, url, payload):
        return self.client_a.post(
            url,
            data=payload,
            HTTP_ACCEPT="application/json",
        )

    def emit_badge(self, student, client=None):
        client = client or self.client_a
        url = f"/responsable/eleves/{student.pk}/badge/emettre/"

        response = client.post(
            url,
            HTTP_ACCEPT="application/json",
        )

        self.assertIn(
            response.status_code,
            (200, 201),
            f"Émission badge échouée : {response.status_code}, {response.content!r}",
        )

        data = self.response_json(response)

        badge = (
            Badge.objects.filter(student=student)
            .order_by("-issued_at")
            .first()
        )

        if badge is None and data.get("badge_id"):
            badge = Badge.objects.filter(pk=data["badge_id"]).first()

        self.assertIsNotNone(badge, "Le badge doit être créé en base.")
        return badge, data, response

    def activate_link(self, badge, client=None):
        client = client or self.client_a
        url = f"/responsable/badges/{badge.pk}/lien-activation/"

        response = client.post(url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 201, response.content)

        data = self.response_json(response)
        token = self.extract_token(data)

        self.assertTrue(token, "Le lien d'activation doit contenir un token.")
        return token, data, response

    @staticmethod
    def extract_token(data):
        for key in ("activation_url", "activation_path", "url", "path"):
            value = data.get(key)
            if value:
                parsed = urlparse(value)
                path = parsed.path if parsed.scheme else value
                pieces = path.strip("/").split("/")
                if pieces:
                    return pieces[-1]

        return data.get("token")

    def activation_url(self, token):
        return f"/badge/activer/{token}/"

    @staticmethod
    def response_json(response):
        try:
            return response.json()
        except (ValueError, AttributeError):
            return json.loads(response.content.decode("utf-8"))

    def make_code(self, badge, time_step=None):
        step_seconds = getattr(
            settings,
            "PASSAGE_CODE_STEP_SECONDS",
            self.STEP_SECONDS,
        )

        if time_step is None:
            time_step = math.floor(self.now.timestamp() / step_seconds)

        message = f"v1.{badge.public_id}.{time_step}".encode("utf-8")
        signature = hmac.new(
            badge.secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()[:32]

        return f"v1.{badge.public_id}.{time_step}.{signature}"

    def time_step_for(self, dt):
        step_seconds = getattr(
            settings,
            "PASSAGE_CODE_STEP_SECONDS",
            self.STEP_SECONDS,
        )
        return math.floor(dt.timestamp() / step_seconds)

    def iso(self, dt):
        return dt.isoformat()

    def sync_payload(self, events, device_id=None, device_time=None):
        return {
            "device_id": device_id or f"device-{uuid.uuid4().hex[:8]}",
            "device_time": self.iso(device_time or self.now),
            "events": events,
        }

    def scan_event(
        self,
        badge,
        checkpoint=None,
        code=None,
        client_event_id=None,
        local_scanned_at=None,
        time_step=None,
    ):
        checkpoint = checkpoint or self.checkpoint_a1
        local_scanned_at = local_scanned_at or self.now

        if code is None:
            if time_step is None:
                time_step = self.time_step_for(local_scanned_at)
            code = self.make_code(badge, time_step)

        return {
            "client_event_id": (
                client_event_id or f"evt-{uuid.uuid4().hex}"
            ),
            "checkpoint_id": checkpoint.pk,
            "code": code,
            "local_scanned_at": self.iso(local_scanned_at),
        }

    def sync(self, events, client=None, device_id=None, device_time=None):
        client = client or self.agent_client
        return self.json_post(
            client,
            self.SYNC_URL,
            self.sync_payload(
                events,
                device_id=device_id,
                device_time=device_time,
            ),
        )

    def create_activated_badge(self, student=None, client=None):
        student = student or self.student_a1
        client = client or self.client_a

        badge, _, _ = self.emit_badge(student, client=client)
        token, _, _ = self.activate_link(badge, client=client)

        response = self.anonymous_client.get(self.activation_url(token))
        self.assertEqual(response.status_code, 200, response.content)

        response = self.anonymous_client.post(
            self.activation_url(token),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        return badge, self.response_json(response)

    def create_twenty_activated_badges(self, prefix):
        badges = []

        for index in range(20):
            student = self.create_student(
                self.school_a,
                f"{prefix}{index}",
                "Batch",
            )
            badge, _ = self.create_activated_badge(student)
            badges.append(badge)

        return badges

    # ------------------------------------------------------------------
    # Cycle de vie des badges / secrets
    # ------------------------------------------------------------------

    def test_emit_reemit_revokes_previous_badge_and_links_replacement(self):
        first, _, _ = self.emit_badge(self.student_a1)
        second, _, _ = self.emit_badge(self.student_a1)

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.status, Badge.Status.REVOKED)
        self.assertEqual(second.status, Badge.Status.ACTIVE)
        self.assertEqual(second.replaced_badge_id, first.pk)

        self.assertEqual(
            Badge.objects.filter(
                student=self.student_a1,
                status=Badge.Status.ACTIVE,
            ).count(),
            1,
        )

    def test_badge_code_does_not_contain_student_name_or_database_id(self):
        badge, _, _ = self.emit_badge(self.student_a1)

        code = self.make_code(badge, self.time_step_for(self.now))
        public_id = str(badge.public_id)

        first_name = getattr(self.student_a1, "first_name", "Alice")
        last_name = getattr(self.student_a1, "last_name", "Martin")
        matricule = getattr(self.student_a1, "matricule", None)

        for sensitive_value in (
            first_name,
            last_name,
            matricule,
        ):
            if sensitive_value:
                self.assertNotIn(sensitive_value.lower(), code.lower())
                self.assertNotIn(sensitive_value.lower(), public_id.lower())

        # L'identifiant public ne doit pas être une copie directe
        # du matricule ou d'une identité lisible.
        self.assertNotEqual(public_id, str(self.student_a1.pk))
        if matricule:
            self.assertNotEqual(public_id, str(matricule))

        self.assertRegex(public_id, r"^[A-Za-z0-9_-]+$")

    def test_badge_secret_never_appears_in_responsible_page_or_api(self):
        badge, _, _ = self.emit_badge(self.student_a1)
        secret = badge.secret

        page = self.client_a.get(self.BADGE_LIST_URL)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(secret.encode(), page.content)

        creation_response = self.client_a.post(
            f"/responsable/eleves/{self.student_a2.pk}/badge/emettre/",
            HTTP_ACCEPT="application/json",
        )
        self.assertNotIn(secret.encode(), creation_response.content)

        listing = self.client_a.get(self.BADGE_LIST_URL)
        self.assertNotIn(secret.encode(), listing.content)

        token, link_data, link_response = self.activate_link(badge)
        self.assertNotIn(secret.encode(), link_response.content)
        self.assertNotIn(secret, json.dumps(link_data, default=str))
        self.assertNotIn(secret, token)

        scan_page = self.agent_client.get("/scan/")
        self.assertNotIn(secret.encode(), scan_page.content)

        for log in AuditLog.objects.all():
            self.assertNotIn(secret, str(log.__dict__))

    def test_badge_status_transition_matrix_and_audit_history(self):
        badge, _, _ = self.emit_badge(self.student_a1)

        allowed = (
            (Badge.Status.ACTIVE, Badge.Status.SUSPENDED),
            (Badge.Status.SUSPENDED, Badge.Status.ACTIVE),
            (Badge.Status.ACTIVE, Badge.Status.LOST),
        )

        for old_status, new_status in allowed:
            badge.refresh_from_db()
            badge.status = old_status
            badge.save()

            history_before = BadgeStatusHistory.objects.count()
            audit_before = AuditLog.objects.count()

            response = self.responsible_post(
                f"/responsable/badges/{badge.pk}/etat/",
                {"status": new_status, "reason": "test transition"},
            )

            self.assertIn(response.status_code, (200, 302))
            badge.refresh_from_db()
            self.assertEqual(badge.status, new_status)
            self.assertGreater(
                BadgeStatusHistory.objects.count(),
                history_before,
            )
            self.assertGreater(AuditLog.objects.count(), audit_before)

        for terminal in (Badge.Status.LOST, Badge.Status.REVOKED):
            badge.status = terminal
            badge.save()

            for target in (
                Badge.Status.ACTIVE,
                Badge.Status.SUSPENDED,
                Badge.Status.LOST,
                Badge.Status.REVOKED,
            ):
                response = self.responsible_post(
                    f"/responsable/badges/{badge.pk}/etat/",
                    {"status": target, "reason": "terminal state test"},
                )
                self.assertEqual(response.status_code, 400)

    def test_school_isolation_responsible_cannot_emit_or_modify_foreign_badge(self):
        badge_b, _, _ = self.emit_badge(
            self.student_b1,
            client=self.responsable_b_client,
        )

        before = AuditLog.objects.count()

        response = self.client_a.post(
            f"/responsable/eleves/{self.student_b1.pk}/badge/emettre/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

        response = self.client_a.post(
            f"/responsable/badges/{badge_b.pk}/etat/",
            {"status": Badge.Status.SUSPENDED},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

        response = self.client_a.post(
            f"/responsable/badges/{badge_b.pk}/lien-activation/",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

        self.assertGreater(AuditLog.objects.count(), before)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.ACCESS_DENIED).exists()
        )

    def test_agent_and_parent_are_denied_all_management_routes(self):
        badge, _, _ = self.emit_badge(self.student_a1)

        for client in (self.agent_client, self.parent_client):
            response = client.get(self.BADGE_LIST_URL)
            self.assertEqual(response.status_code, 403)

            response = client.post(
                f"/responsable/eleves/{self.student_a1.pk}/badge/emettre/",
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 403)

            response = client.post(
                f"/responsable/badges/{badge.pk}/etat/",
                {"status": Badge.Status.SUSPENDED},
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 403)

            response = client.post(
                f"/responsable/badges/{badge.pk}/lien-activation/",
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------
    # Liens d'activation
    # ------------------------------------------------------------------

    def test_activation_link_get_does_not_consume_token(self):
        badge, _, _ = self.emit_badge(self.student_a1)
        token, _, _ = self.activate_link(badge)

        url = self.activation_url(token)
        get_response = self.anonymous_client.get(url)
        self.assertEqual(get_response.status_code, 200)

        post_response = self.anonymous_client.post(
            url,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(post_response.status_code, 200)

    def test_activation_link_post_consumes_once_and_returns_only_device_credentials(self):
        badge, _, _ = self.emit_badge(self.student_a1)
        token, link_data, link_response = self.activate_link(badge)

        self.assertNotIn(badge.secret, link_response.content.decode(errors="ignore"))
        self.assertNotIn(badge.secret, json.dumps(link_data, default=str))

        response = self.anonymous_client.post(
            self.activation_url(token),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        data = self.response_json(response)
        for field in (
            "public_id",
            "secret",
            "code_step_seconds",
            "server_time",
        ):
            self.assertIn(field, data)

        self.assertEqual(data["public_id"], badge.public_id)
        self.assertEqual(data["secret"], badge.secret)

        serialized = json.dumps(data).lower()
        self.assertNotIn("alice", serialized)

        # Les réponses d'activation ne doivent pas exposer les clés
        # identifiantes de l'élève ni sa valeur d'identification.
        for forbidden_key in ("student_id", "student"):
            self.assertNotIn(forbidden_key, data)

        self.assertNotIn(str(self.student_a1.pk), data.values())

        second_get = self.anonymous_client.get(self.activation_url(token))
        self.assertEqual(second_get.status_code, 410)

        second_post = self.anonymous_client.post(
            self.activation_url(token),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(second_post.status_code, 410)

    def test_activation_link_expiration(self):
        badge, _, _ = self.emit_badge(self.student_a1)
        token, _, _ = self.activate_link(badge)

        duration = getattr(
            settings,
            "PASSAGE_ACTIVATION_LINK_DURATION",
            timedelta(hours=24),
        )
        if isinstance(duration, int):
            duration = timedelta(seconds=duration)

        link = BadgeActivationLink.objects.filter(badge=badge).latest("created_at")
        expired_now = link.created_at + duration + timedelta(seconds=1)

        with patch("django.utils.timezone.now", return_value=expired_now):
            response = self.anonymous_client.post(
                self.activation_url(token),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 410)
        self.assertNotIn(badge.secret.encode(), response.content)

    def test_activation_link_rejected_when_badge_revoked_after_link_creation(self):
        badge, _, _ = self.emit_badge(self.student_a1)
        token, _, _ = self.activate_link(badge)

        badge.status = Badge.Status.REVOKED
        badge.save()

        response = self.anonymous_client.post(
            self.activation_url(token),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 410)
        self.assertNotIn(badge.secret.encode(), response.content)

    def test_activation_link_unknown_token_returns_404(self):
        response = self.anonymous_client.get(
            self.activation_url("unknown-token-" + uuid.uuid4().hex)
        )
        self.assertEqual(response.status_code, 404)

    def test_activation_link_creation_is_audited_without_secret_or_token(self):
        badge, _, _ = self.emit_badge(self.student_a1)

        before = AuditLog.objects.count()
        token, data, response = self.activate_link(badge)

        self.assertEqual(response.status_code, 201)
        self.assertGreater(AuditLog.objects.count(), before)

        for log in AuditLog.objects.order_by("-pk")[:5]:
            text = str(log.__dict__)
            self.assertNotIn(token, text)
            self.assertNotIn(badge.secret, text)

    def test_activation_link_refused_for_non_active_badge(self):
        badge, _, _ = self.emit_badge(self.student_a1)

        for status in (
            Badge.Status.SUSPENDED,
            Badge.Status.LOST,
            Badge.Status.REVOKED,
        ):
            badge.status = status
            badge.save()

            response = self.client_a.post(
                f"/responsable/badges/{badge.pk}/lien-activation/",
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Validation cryptographique / anti-rejeu
    # ------------------------------------------------------------------

    def test_signed_code_is_accepted_at_current_and_tolerated_steps(self):
        badge, _ = self.create_activated_badge()
        tolerance = getattr(
            settings,
            "PASSAGE_CODE_STEP_TOLERANCE",
            1,
        )
        current_step = self.time_step_for(self.now)

        for offset in (-tolerance, 0, tolerance):
            event = self.scan_event(
                badge,
                local_scanned_at=self.now,
                time_step=current_step + offset,
            )
            response = self.sync([event])
            self.assertEqual(response.status_code, 200, response.content)

            result = self.response_json(response)["results"][0]
            self.assertEqual(result["status"], "accepted", result)
            self.assertTrue(
                Passage.objects.filter(
                    pk=result["passage_id"],
                    status=Passage.Status.VALID,
                ).exists()
            )

    def test_altered_signature_rejected_and_saved_without_parent_anomaly(self):
        badge, _ = self.create_activated_badge()
        code = self.make_code(badge)
        parts = code.split(".")
        parts[-1] = ("0" if parts[-1][0] != "0" else "1") + parts[-1][1:]
        altered = ".".join(parts)

        event = self.scan_event(badge, code=altered)
        anomalies_before = Anomaly.objects.count()

        response = self.sync([event])
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "invalid_signature")

        passage = Passage.objects.get(pk=result["passage_id"])
        self.assertEqual(passage.status, Passage.Status.REJECTED)
        self.assertEqual(
            passage.rejection_reason,
            Passage.RejectionReason.INVALID_SIGNATURE,
        )
        self.assertEqual(Anomaly.objects.count(), anomalies_before)

    def test_expired_code_is_rejected_and_saved(self):
        badge, _ = self.create_activated_badge()

        tolerance = getattr(
            settings,
            "PASSAGE_CODE_STEP_TOLERANCE",
            1,
        )
        old_step = self.time_step_for(self.now) - tolerance - 5

        event = self.scan_event(
            badge,
            local_scanned_at=self.now,
            time_step=old_step,
        )
        response = self.sync([event])
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "expired_code")
        self.assertTrue(
            Passage.objects.filter(
                pk=result["passage_id"],
                status=Passage.Status.REJECTED,
            ).exists()
        )

    def test_unknown_badge_and_malformed_code_are_rejected(self):
        unknown_code = (
            f"v1.unknown-{uuid.uuid4().hex}.123456."
            + "a" * 32
        )

        events = [
            {
                "client_event_id": f"unknown-{uuid.uuid4().hex}",
                "checkpoint_id": self.checkpoint_a1.pk,
                "code": unknown_code,
                "local_scanned_at": self.iso(self.now),
            },
            {
                "client_event_id": f"malformed-{uuid.uuid4().hex}",
                "checkpoint_id": self.checkpoint_a1.pk,
                "code": "not-a-valid-code",
                "local_scanned_at": self.iso(self.now),
            },
        ]

        response = self.sync(events)
        self.assertEqual(response.status_code, 200, response.content)

        results = self.response_json(response)["results"]
        self.assertEqual(results[0]["status"], "rejected")
        self.assertEqual(results[0]["reason"], "unknown_badge")
        self.assertEqual(results[1]["status"], "rejected")
        self.assertEqual(results[1]["reason"], "malformed_code")

        self.assertTrue(
            Passage.objects.filter(
                status=Passage.Status.REJECTED,
                rejection_reason=Passage.RejectionReason.UNKNOWN_BADGE,
            ).exists()
        )
        self.assertTrue(
            Passage.objects.filter(
                status=Passage.Status.REJECTED,
                rejection_reason=Passage.RejectionReason.MALFORMED_CODE,
            ).exists()
        )

    def test_foreign_school_badge_scan_is_rejected_without_student_leak(self):
        badge_b, _ = self.create_activated_badge(
            self.student_b1,
            client=self.responsable_b_client,
        )

        event = self.scan_event(badge_b, checkpoint=self.checkpoint_a1)
        response = self.sync([event])
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "school_mismatch")

        self.assertNotIn("ForeignStudent", response.content.decode(errors="ignore"))

    def test_same_code_step_replay_with_different_event_ids_is_rejected(self):
        badge, _ = self.create_activated_badge()
        code = self.make_code(badge)

        first = self.scan_event(
            badge,
            code=code,
            client_event_id="replay-first",
        )
        second = self.scan_event(
            badge,
            code=code,
            client_event_id="replay-second",
        )

        response = self.sync([first, second])
        self.assertEqual(response.status_code, 200, response.content)

        results = self.response_json(response)["results"]
        self.assertEqual(results[0]["status"], "accepted")
        self.assertEqual(results[1]["status"], "rejected")
        self.assertEqual(results[1]["reason"], "already_used")

        self.assertEqual(
            Passage.objects.filter(
                badge=badge,
                status=Passage.Status.VALID,
            ).count(),
            1,
        )
        self.assertTrue(
            Passage.objects.filter(
                badge=badge,
                status=Passage.Status.REJECTED,
                rejection_reason=Passage.RejectionReason.ALREADY_USED,
            ).exists()
        )
        self.assertFalse(
            Anomaly.objects.filter(
                anomaly_type=Anomaly.AnomalyType.BADGE_NON_ACTIF
            ).exists()
        )

    def test_inactive_badges_are_rejected_and_create_anomaly(self):
        badge, _ = self.create_activated_badge()

        for status in (
            Badge.Status.SUSPENDED,
            Badge.Status.LOST,
            Badge.Status.REVOKED,
        ):
            badge.status = status
            badge.save()

            code = self.make_code(badge)
            event = self.scan_event(badge, code=code)

            before = Anomaly.objects.filter(
                anomaly_type=Anomaly.AnomalyType.BADGE_NON_ACTIF
            ).count()

            response = self.sync([event])
            self.assertEqual(response.status_code, 200, response.content)

            result = self.response_json(response)["results"][0]
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"], "inactive_badge")

            self.assertGreater(
                Anomaly.objects.filter(
                    anomaly_type=Anomaly.AnomalyType.BADGE_NON_ACTIF
                ).count(),
                before,
            )

    # ------------------------------------------------------------------
    # Synchronisation hors ligne / idempotence
    # ------------------------------------------------------------------

    def test_twenty_valid_offline_scans_are_saved_individually(self):
        badges = self.create_twenty_activated_badges("offline20-")
        current_step = self.time_step_for(self.now)

        events = [
            self.scan_event(
                badge,
                local_scanned_at=self.now,
                time_step=current_step,
                client_event_id=f"batch20-{index}",
            )
            for index, badge in enumerate(badges)
        ]

        response = self.sync(events, device_id="batch-device-20")
        self.assertEqual(response.status_code, 200, response.content)

        results = self.response_json(response)["results"]
        self.assertEqual(len(results), 20)
        self.assertTrue(all(r["status"] == "accepted" for r in results))
        self.assertEqual(
            Passage.objects.filter(status=Passage.Status.VALID).count(),
            20,
        )

    def test_resending_same_twenty_event_batch_is_idempotent(self):
        badges = self.create_twenty_activated_badges("idem20-")
        current_step = self.time_step_for(self.now)

        events = [
            self.scan_event(
                badge,
                local_scanned_at=self.now,
                time_step=current_step,
                client_event_id=f"idem20-{index}",
            )
            for index, badge in enumerate(badges)
        ]

        device_id = "same-device-idempotency"

        first_response = self.sync(events, device_id=device_id)
        self.assertEqual(first_response.status_code, 200, first_response.content)
        self.assertEqual(
            len(self.response_json(first_response)["results"]),
            20,
        )

        second_response = self.sync(events, device_id=device_id)
        self.assertEqual(second_response.status_code, 200, second_response.content)

        second_results = self.response_json(second_response)["results"]
        self.assertEqual(len(second_results), 20)
        self.assertTrue(
            all(r["status"] == "duplicate" for r in second_results)
        )
        self.assertEqual(
            Passage.objects.filter(status=Passage.Status.VALID).count(),
            20,
        )

    def test_partial_batch_resume_returns_ten_duplicates_and_ten_accepted(self):
        badges = self.create_twenty_activated_badges("resume20-")
        current_step = self.time_step_for(self.now)

        first_ten = [
            self.scan_event(
                badges[index],
                local_scanned_at=self.now,
                time_step=current_step,
                client_event_id=f"resume-{index}",
            )
            for index in range(10)
        ]

        device_id = "resume-device"

        first_response = self.sync(first_ten, device_id=device_id)
        self.assertEqual(first_response.status_code, 200, first_response.content)
        self.assertEqual(
            sum(
                result["status"] == "accepted"
                for result in self.response_json(first_response)["results"]
            ),
            10,
        )

        second_ten = [
            self.scan_event(
                badges[index],
                local_scanned_at=self.now,
                time_step=current_step,
                client_event_id=f"resume-{index}",
            )
            for index in range(10, 20)
        ]

        resumed_response = self.sync(
            first_ten + second_ten,
            device_id=device_id,
        )
        self.assertEqual(resumed_response.status_code, 200, resumed_response.content)

        results = self.response_json(resumed_response)["results"]
        self.assertEqual(len(results), 20)
        self.assertEqual(
            sum(r["status"] == "duplicate" for r in results),
            10,
        )
        self.assertEqual(
            sum(r["status"] == "accepted" for r in results),
            10,
        )
        self.assertEqual(
            Passage.objects.filter(status=Passage.Status.VALID).count(),
            20,
        )

    # ------------------------------------------------------------------
    # Contrôle d'accès API / points de passage
    # ------------------------------------------------------------------

    def test_sync_and_checkpoints_require_authentication(self):
        sync_payload = self.sync_payload([])

        sync_response = self.json_post(
            self.anonymous_client,
            self.SYNC_URL,
            sync_payload,
        )
        self.assertEqual(sync_response.status_code, 401)

        checkpoints_response = self.anonymous_client.get(self.CHECKPOINTS_URL)
        self.assertEqual(checkpoints_response.status_code, 401)

    def test_parent_and_responsible_are_denied_sync_and_checkpoints(self):
        for client in (self.parent_client, self.client_a):
            sync_response = self.json_post(
                client,
                self.SYNC_URL,
                self.sync_payload([]),
            )
            self.assertEqual(sync_response.status_code, 403)

            checkpoints_response = client.get(self.CHECKPOINTS_URL)
            self.assertEqual(checkpoints_response.status_code, 403)

    def test_sync_requires_csrf_token_when_enforced(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.agent_a)

        payload = self.sync_payload([])
        response = csrf_client.post(
            self.SYNC_URL,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_checkpoints_only_returns_active_checkpoints_for_agent_school(self):
        response = self.agent_client.get(self.CHECKPOINTS_URL)
        self.assertEqual(response.status_code, 200)

        data = self.response_json(response)
        self.assertIn("checkpoints", data)

        returned_ids = {checkpoint["id"] for checkpoint in data["checkpoints"]}
        self.assertEqual(
            returned_ids,
            {self.checkpoint_a1.pk, self.checkpoint_a2.pk},
        )

        returned_serialized = json.dumps(data)
        self.assertNotIn("Ancien portail", returned_serialized)
        self.assertNotIn("Portail B", returned_serialized)

    def test_time_endpoint_is_public_and_returns_contract(self):
        response = self.anonymous_client.get(self.TIME_URL)
        self.assertEqual(response.status_code, 200)

        data = self.response_json(response)
        self.assertIn("server_time", data)
        self.assertIn("epoch_seconds", data)
        self.assertIn("code_step_seconds", data)
        self.assertEqual(
            data["code_step_seconds"],
            getattr(
                settings,
                "PASSAGE_CODE_STEP_SECONDS",
                self.STEP_SECONDS,
            ),
        )

    # ------------------------------------------------------------------
    # Gestion des écarts d'horloge
    # ------------------------------------------------------------------

    def test_device_clock_two_hours_ahead_is_corrected(self):
        badge, _ = self.create_activated_badge()

        local_time = self.now + timedelta(hours=2)
        event = self.scan_event(
            badge,
            local_scanned_at=local_time,
            code=self.make_code(badge, self.time_step_for(self.now)),
        )

        response = self.sync(
            [event],
            device_id="clock-ahead-device",
            device_time=local_time,
        )
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "accepted")

        passage = Passage.objects.get(pk=result["passage_id"])

        self.assertEqual(
            passage.local_scanned_at.replace(microsecond=0),
            local_time.replace(microsecond=0),
        )
        self.assertTrue(passage.clock_suspect)

        self.assertLessEqual(
            abs((passage.retained_at - self.now).total_seconds()),
            10,
        )
        self.assertLessEqual(
            abs((passage.corrected_at - self.now).total_seconds()),
            10,
        )

        self.assertTrue(
            ClockOffset.objects.filter(is_suspect=True).exists()
        )

    def test_device_clock_two_hours_behind_is_corrected(self):
        badge, _ = self.create_activated_badge()

        local_time = self.now - timedelta(hours=2)
        event = self.scan_event(
            badge,
            local_scanned_at=local_time,
            code=self.make_code(badge, self.time_step_for(self.now)),
        )

        response = self.sync(
            [event],
            device_id="clock-behind-device",
            device_time=local_time,
        )
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "accepted")

        passage = Passage.objects.get(pk=result["passage_id"])
        self.assertTrue(passage.clock_suspect)

        self.assertLessEqual(
            abs((passage.retained_at - self.now).total_seconds()),
            10,
        )
        self.assertLessEqual(
            abs((passage.corrected_at - self.now).total_seconds()),
            10,
        )

    def test_aberrant_local_time_is_retained_at_server_receipt(self):
        badge, _ = self.create_activated_badge()

        local_time = self.now - timedelta(days=10)
        event = self.scan_event(
            badge,
            local_scanned_at=local_time,
            code=self.make_code(badge, self.time_step_for(self.now)),
        )

        response = self.sync(
            [event],
            device_id="clock-aberrant-device",
            device_time=self.now,
        )
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "accepted")

        passage = Passage.objects.get(pk=result["passage_id"])
        self.assertTrue(passage.clock_suspect)

        received = passage.server_received_at
        retained = passage.retained_at

        self.assertLessEqual(
            abs((retained - received).total_seconds()),
            10,
        )

    def test_normal_clock_is_not_suspect(self):
        badge, _ = self.create_activated_badge()

        local_time = self.now + timedelta(minutes=2)
        event = self.scan_event(
            badge,
            local_scanned_at=local_time,
            code=self.make_code(badge, self.time_step_for(self.now)),
        )

        response = self.sync(
            [event],
            device_id="clock-normal-device",
            device_time=local_time,
        )
        self.assertEqual(response.status_code, 200, response.content)

        result = self.response_json(response)["results"][0]
        self.assertEqual(result["status"], "accepted")

        passage = Passage.objects.get(pk=result["passage_id"])
        self.assertFalse(passage.clock_suspect)