"""Tests boîte noire — lot 3.

Ces tests n'utilisent que :
  * les modèles Django de ``passage.models`` (noms de champs documentés) ;
  * ``POST /api/sync/`` ;
  * ``call_command("run_scheduler", "--once")`` ;
  * ``POST /responsable/anomalies/<id>/traiter/``.

Gestion de l'heure serveur simulée
----------------------------------
Le planificateur et la synchronisation dépendent de l'heure serveur. Elle est
figée avec ``freezegun`` s'il est installé, sinon en patchant
``django.utils.timezone.now``. Si le code testé lit l'heure autrement
(``datetime.now()`` direct, par exemple), installer ``freezegun``.

Calendrier utilisé : lundi 2026-11-23 (ISO 1), samedi 2026-11-28 (ISO 6).
"""
import hashlib
import hmac
import json
import math
from contextlib import contextmanager
from datetime import date, datetime, time
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import Client, TestCase

from passage.models import (
    Anomaly,
    AuditLog,
    Badge,
    Checkpoint,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)

try:  # pragma: no cover - dépend de l'environnement
    from freezegun import freeze_time
except ImportError:  # pragma: no cover
    freeze_time = None


DOUALA = ZoneInfo("Africa/Douala")
MONDAY = (2026, 11, 23)
SATURDAY = (2026, 11, 28)
MONDAY_DATE = date(*MONDAY)
PASSWORD = "S3cret-pass-2026"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def at(hour, minute=0, second=0, day=MONDAY):
    """Datetime aware en heure de Douala (UTC+1)."""
    return datetime(*day, hour, minute, second, tzinfo=DOUALA)


@contextmanager
def server_now(moment):
    """Fige l'heure serveur simulée sur ``moment``."""
    if freeze_time is not None:
        with freeze_time(moment):
            yield
    else:
        with mock.patch("django.utils.timezone.now", return_value=moment):
            yield


def signed_code(badge, scan_dt):
    """Code signé calculé sur l'heure réelle du scan."""
    time_step = math.floor(scan_dt.timestamp() / 30)
    sig = hmac.new(
        badge.secret.encode(),
        f"v1.{badge.public_id}.{time_step}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"v1.{badge.public_id}.{time_step}.{sig}"


class BlackBoxBase(TestCase):
    """Jeu de données commun : un établissement, un élève, un portail d'entrée."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(
            name="Lycée Bilingue Test", code="LBT", timezone="Africa/Douala", is_active=True
        )
        cls.other_school = School.objects.create(
            name="Collège Autre", code="COA", timezone="Africa/Douala", is_active=True
        )

        cls.manager = User.objects.create_user(
            username="resp1", password=PASSWORD, role=User.Role.MANAGER, school=cls.school
        )
        cls.other_manager = User.objects.create_user(
            username="resp2", password=PASSWORD, role=User.Role.MANAGER, school=cls.other_school
        )
        cls.agent = User.objects.create_user(
            username="agent1", password=PASSWORD, role=User.Role.AGENT, school=cls.school
        )
        cls.parent = User.objects.create_user(
            username="parent1", password=PASSWORD, role=User.Role.PARENT, school=None
        )

        cls.gate = Checkpoint.objects.create(
            school=cls.school,
            label="Portail principal",
            checkpoint_type=Checkpoint.CheckpointType.ENTRY_GATE,
            is_active=True,
        )
        cls.bus = Checkpoint.objects.create(
            school=cls.school,
            label="Bus 1",
            checkpoint_type=Checkpoint.CheckpointType.BUS,
            is_active=True,
        )

        cls.student = Student.objects.create(
            school=cls.school,
            first_name="Awa",
            last_name="Mbarga",
            classroom="3e A",
            status=Student.Status.ACTIVE,
        )
        cls.window = RouteWindow.objects.create(
            student=cls.student,
            checkpoint=cls.gate,
            order=1,
            start_time=time(7, 0),
            end_time=time(7, 45),
            weekdays="1,2,3,4,5",
            is_active=True,
        )
        cls.badge = Badge.objects.create(student=cls.student, status=Badge.Status.ACTIVE)

    # ----- fabriques ------------------------------------------------------- #
    def make_student(self, first_name, badge_status=Badge.Status.ACTIVE, with_window=True):
        student = Student.objects.create(
            school=self.school,
            first_name=first_name,
            last_name="Test",
            classroom="3e A",
            status=Student.Status.ACTIVE,
        )
        if with_window:
            RouteWindow.objects.create(
                student=student,
                checkpoint=self.gate,
                order=1,
                start_time=time(7, 0),
                end_time=time(7, 45),
                weekdays="1,2,3,4,5",
                is_active=True,
            )
        badge = Badge.objects.create(student=student, status=badge_status)
        return student, badge

    # ----- clients --------------------------------------------------------- #
    def logged_client(self, user, **client_kwargs):
        client = Client(**client_kwargs)
        self.assertTrue(client.login(username=user.username, password=PASSWORD))
        return client

    # ----- synchronisation ------------------------------------------------- #
    @staticmethod
    def event(badge, checkpoint, scan_dt, event_id):
        return {
            "client_event_id": event_id,
            "checkpoint_id": checkpoint.id,
            "code": signed_code(badge, scan_dt),
            "local_scanned_at": scan_dt.isoformat(),
        }

    def sync(self, events, device_dt, server_dt=None, client=None):
        """Envoie un lot à /api/sync/.

        ``device_dt`` : heure de l'appareil à la synchronisation.
        ``server_dt`` : heure serveur simulée (par défaut = ``device_dt``,
        soit un décalage d'horloge nul).
        """
        client = client or self.logged_client(self.agent)
        payload = {
            "device_id": "scanner-1",
            "device_time": device_dt.isoformat(),
            "events": events,
        }
        with server_now(server_dt or device_dt):
            return client.post(
                "/api/sync/",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
            )

    def assertSyncOk(self, response):
        self.assertIn(response.status_code, (200, 201), response.content)

    # ----- planificateur --------------------------------------------------- #
    def run_scheduler(self, moment):
        with server_now(moment):
            call_command("run_scheduler", "--once", stdout=StringIO(), stderr=StringIO())

    # ----- requêtes ORM utilitaires --------------------------------------- #
    def anomalies(self, anomaly_type, student=None, **extra):
        qs = Anomaly.objects.filter(anomaly_type=anomaly_type, **extra)
        if student is not None:
            qs = qs.filter(student=student)
        return qs

    def passages(self, student=None):
        return Passage.objects.filter(student=student or self.student).order_by("id")


# --------------------------------------------------------------------------- #
# Synchronisation tardive (hors ligne)
# --------------------------------------------------------------------------- #
class LateSyncTests(BlackBoxBase):
    def test_sync_tardive_est_valide_et_utilise_l_heure_reelle_du_scan(self):
        """Scan à 07:20 (dans la fenêtre), synchronisé à 08:30 (hors fenêtre)."""
        scan = at(7, 20)
        response = self.sync(
            [self.event(self.badge, self.gate, scan, "evt-1")], device_dt=at(8, 30)
        )
        self.assertSyncOk(response)

        passage = self.passages().get()
        self.assertEqual(passage.status, Passage.Status.VALID)
        self.assertEqual(passage.student, self.student)
        self.assertEqual(passage.checkpoint, self.gate)
        self.assertEqual(passage.badge, self.badge)
        self.assertFalse(passage.clock_suspect)

        # L'heure retenue est celle du scan, pas celle de la synchronisation.
        self.assertIsNotNone(passage.retained_at)
        self.assertLessEqual(abs((passage.retained_at - scan).total_seconds()), 2)

    def test_sync_tardive_ne_cree_pas_d_anomalie_hors_fenetre(self):
        """L'heure d'envoi (08:30) est hors fenêtre, mais pas l'heure du scan."""
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(8, 30))
        self.assertFalse(self.anomalies(Anomaly.AnomalyType.HORS_FENETRE).exists())

    def test_scan_reellement_hors_fenetre_cree_une_anomalie(self):
        """Scan à 08:10, fenêtre 07:00-07:45."""
        self.sync([self.event(self.badge, self.gate, at(8, 10), "evt-1")], device_dt=at(8, 30))
        anomaly = self.anomalies(Anomaly.AnomalyType.HORS_FENETRE, student=self.student).get()
        self.assertEqual(anomaly.school, self.school)
        self.assertEqual(anomaly.checkpoint, self.gate)
        self.assertEqual(anomaly.date, MONDAY_DATE)

    def test_renvoi_du_meme_evenement_est_idempotent(self):
        events = [self.event(self.badge, self.gate, at(7, 20), "evt-1")]
        self.assertSyncOk(self.sync(events, device_dt=at(8, 30)))
        self.assertSyncOk(self.sync(events, device_dt=at(8, 31)))
        self.assertEqual(self.passages().count(), 1)

    def test_meme_badge_meme_pas_de_temps_est_rejete_already_used(self):
        """Deux événements distincts, même (badge, time_step) : le 2e est rejeté."""
        events = [
            self.event(self.badge, self.gate, at(7, 20, 5), "evt-1"),
            self.event(self.badge, self.gate, at(7, 20, 15), "evt-2"),
        ]
        self.assertSyncOk(self.sync(events, device_dt=at(8, 30)))

        first, second = list(self.passages())
        self.assertEqual(first.status, Passage.Status.VALID)
        self.assertEqual(second.status, Passage.Status.REJECTED)
        self.assertEqual(second.rejection_reason, "already_used")

    def test_horloge_appareil_decalee_marque_clock_suspect(self):
        """Appareil en avance de 2 h sur le serveur."""
        self.sync(
            [self.event(self.badge, self.gate, at(7, 20), "evt-1")],
            device_dt=at(10, 30),
            server_dt=at(8, 30),
        )
        passage = self.passages().get()
        self.assertTrue(passage.clock_suspect)

    def test_horloge_appareil_synchrone_n_est_pas_suspecte(self):
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(8, 30))
        self.assertFalse(self.passages().get().clock_suspect)


# --------------------------------------------------------------------------- #
# Double scan
# --------------------------------------------------------------------------- #
class DoubleScanTests(BlackBoxBase):
    def test_deux_scans_valides_a_moins_de_60s_creent_une_anomalie_double_scan(self):
        events = [
            self.event(self.badge, self.gate, at(7, 20, 20), "evt-1"),
            self.event(self.badge, self.gate, at(7, 20, 40), "evt-2"),
        ]
        self.assertSyncOk(self.sync(events, device_dt=at(8, 30)))

        statuses = [p.status for p in self.passages()]
        self.assertEqual(statuses, [Passage.Status.VALID, Passage.Status.VALID])

        anomaly = self.anomalies(Anomaly.AnomalyType.DOUBLE_SCAN, student=self.student).get()
        self.assertEqual(anomaly.school, self.school)
        self.assertEqual(anomaly.checkpoint, self.gate)
        self.assertEqual(anomaly.date, MONDAY_DATE)
        self.assertEqual(anomaly.status, Anomaly.Status.OPEN)

    def test_deux_scans_espaces_de_plus_de_60s_ne_sont_pas_un_double_scan(self):
        events = [
            self.event(self.badge, self.gate, at(7, 20, 10), "evt-1"),
            self.event(self.badge, self.gate, at(7, 22, 10), "evt-2"),
        ]
        self.assertSyncOk(self.sync(events, device_dt=at(8, 30)))
        self.assertEqual(
            [p.status for p in self.passages()], [Passage.Status.VALID, Passage.Status.VALID]
        )
        self.assertFalse(self.anomalies(Anomaly.AnomalyType.DOUBLE_SCAN).exists())

    def test_un_seul_scan_ne_cree_pas_de_double_scan(self):
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(8, 30))
        self.assertFalse(self.anomalies(Anomaly.AnomalyType.DOUBLE_SCAN).exists())


# --------------------------------------------------------------------------- #
# Badge non actif / point inconnu
# --------------------------------------------------------------------------- #
class BadgeAndCheckpointTests(BlackBoxBase):
    def test_badge_non_actif_est_rejete_avec_anomalie(self):
        for status in (Badge.Status.SUSPENDED, Badge.Status.LOST, Badge.Status.REVOKED):
            with self.subTest(badge_status=status):
                student, badge = self.make_student(f"Eleve-{status}", badge_status=status)
                response = self.sync(
                    [self.event(badge, self.gate, at(7, 20), f"evt-{status}")],
                    device_dt=at(8, 30),
                )
                self.assertSyncOk(response)

                passage = self.passages(student).get()
                self.assertEqual(passage.status, Passage.Status.REJECTED)

                anomaly = self.anomalies(
                    Anomaly.AnomalyType.BADGE_NON_ACTIF, student=student
                ).get()
                self.assertEqual(anomaly.school, self.school)
                self.assertEqual(anomaly.date, MONDAY_DATE)

    def test_scan_sur_un_point_hors_parcours_cree_point_inconnu(self):
        """Le bus existe dans l'établissement mais n'est pas dans le parcours de l'élève."""
        self.sync([self.event(self.badge, self.bus, at(7, 20), "evt-1")], device_dt=at(8, 30))
        anomaly = self.anomalies(Anomaly.AnomalyType.POINT_INCONNU, student=self.student).get()
        self.assertEqual(anomaly.checkpoint, self.bus)
        self.assertEqual(anomaly.date, MONDAY_DATE)

    def test_scan_conforme_ne_cree_aucune_anomalie(self):
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(8, 30))
        self.assertFalse(Anomaly.objects.filter(student=self.student).exists())


# --------------------------------------------------------------------------- #
# Planificateur autonome (points manquants)
# --------------------------------------------------------------------------- #
class SchedulerTests(BlackBoxBase):
    def missing(self, student=None, **extra):
        return self.anomalies(Anomaly.AnomalyType.POINT_MANQUANT, student=student, **extra)

    def test_point_manquant_apres_la_fin_de_fenetre(self):
        self.run_scheduler(at(12, 0))
        anomaly = self.missing(self.student).get()
        self.assertEqual(anomaly.school, self.school)
        self.assertEqual(anomaly.checkpoint, self.gate)
        self.assertEqual(anomaly.date, MONDAY_DATE)
        self.assertEqual(anomaly.status, Anomaly.Status.OPEN)
        self.assertIsNone(anomaly.resolved_at)
        self.assertIsNotNone(anomaly.detected_at)

    def test_planificateur_est_idempotent(self):
        self.run_scheduler(at(12, 0))
        self.run_scheduler(at(12, 5))
        self.assertEqual(self.missing(self.student).count(), 1)

    def test_pas_d_anomalie_si_le_scan_a_eu_lieu(self):
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(7, 21))
        self.run_scheduler(at(12, 0))
        self.assertFalse(self.missing(self.student).exists())

    def test_pas_d_anomalie_avant_la_fin_de_fenetre(self):
        self.run_scheduler(at(7, 30))
        self.assertFalse(self.missing(self.student).exists())

    def test_pas_d_anomalie_un_jour_hors_weekdays(self):
        """Samedi (ISO 6) : la fenêtre n'est active que du lundi au vendredi."""
        self.run_scheduler(at(12, 0, day=SATURDAY))
        self.assertFalse(self.missing(self.student).exists())

    def test_eleve_inactif_est_ignore(self):
        student, _ = self.make_student("Inactif")
        student.status = Student.Status.INACTIVE
        student.save()
        self.run_scheduler(at(12, 0))
        self.assertFalse(self.missing(student).exists())

    def test_fenetre_inactive_est_ignoree(self):
        student, _ = self.make_student("SansFenetre")
        RouteWindow.objects.filter(student=student).update(is_active=False)
        self.run_scheduler(at(12, 0))
        self.assertFalse(self.missing(student).exists())

    def test_une_anomalie_par_point_manquant(self):
        RouteWindow.objects.create(
            student=self.student,
            checkpoint=self.bus,
            order=2,
            start_time=time(7, 45),
            end_time=time(8, 15),
            weekdays="1,2,3,4,5",
            is_active=True,
        )
        self.run_scheduler(at(12, 0))
        self.assertEqual(
            set(self.missing(self.student).values_list("checkpoint_id", flat=True)),
            {self.gate.id, self.bus.id},
        )

    def test_seul_le_point_absent_est_signale(self):
        RouteWindow.objects.create(
            student=self.student,
            checkpoint=self.bus,
            order=2,
            start_time=time(7, 45),
            end_time=time(8, 15),
            weekdays="1,2,3,4,5",
            is_active=True,
        )
        self.sync([self.event(self.badge, self.gate, at(7, 20), "evt-1")], device_dt=at(7, 21))
        self.run_scheduler(at(12, 0))
        anomaly = self.missing(self.student).get()
        self.assertEqual(anomaly.checkpoint, self.bus)


# --------------------------------------------------------------------------- #
# Interaction planificateur <-> synchronisation tardive <-> traitement manuel
# --------------------------------------------------------------------------- #
class MissingPointLifecycleTests(BlackBoxBase):
    def missing(self):
        return self.anomalies(Anomaly.AnomalyType.POINT_MANQUANT, student=self.student)

    def late_scan(self):
        """Scan de 07:20 synchronisé à 10:30."""
        return self.sync(
            [self.event(self.badge, self.gate, at(7, 20), "evt-late")], device_dt=at(10, 30)
        )

    def test_scan_tardif_resout_automatiquement_le_point_manquant(self):
        self.run_scheduler(at(10, 0))
        anomaly = self.missing().get()
        self.assertEqual(anomaly.status, Anomaly.Status.OPEN)

        self.assertSyncOk(self.late_scan())

        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, Anomaly.Status.AUTO_RESOLVED)
        self.assertIsNotNone(anomaly.resolved_at)

        # Un nouveau cycle ne rouvre pas et ne duplique pas l'anomalie.
        self.run_scheduler(at(10, 45))
        self.assertEqual(self.missing().count(), 1)
        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, Anomaly.Status.AUTO_RESOLVED)

    def test_anomalie_traitee_manuellement_reste_traitee_apres_scan_tardif(self):
        self.run_scheduler(at(10, 0))
        anomaly = self.missing().get()

        manager_client = self.logged_client(self.manager)
        response = manager_client.post(
            f"/responsable/anomalies/{anomaly.id}/traiter/", HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.status_code, 200)

        self.assertSyncOk(self.late_scan())

        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, Anomaly.Status.HANDLED)
        self.assertEqual(anomaly.handled_by, self.manager)

    def test_planificateur_ne_recree_pas_une_anomalie_deja_traitee(self):
        self.run_scheduler(at(10, 0))
        anomaly = self.missing().get()
        self.logged_client(self.manager).post(
            f"/responsable/anomalies/{anomaly.id}/traiter/", HTTP_ACCEPT="application/json"
        )

        self.run_scheduler(at(10, 30))

        self.assertEqual(self.missing().count(), 1)
        anomaly.refresh_from_db()
        self.assertEqual(anomaly.status, Anomaly.Status.HANDLED)


# --------------------------------------------------------------------------- #
# Traitement d'une anomalie par le responsable
# --------------------------------------------------------------------------- #
class HandleAnomalyTests(BlackBoxBase):
    def setUp(self):
        self.anomaly = Anomaly.objects.create(
            school=self.school,
            student=self.student,
            checkpoint=self.gate,
            anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
            date=MONDAY_DATE,
            status=Anomaly.Status.OPEN,
            details={},
        )
        self.url = f"/responsable/anomalies/{self.anomaly.id}/traiter/"

    def denied_count(self):
        return AuditLog.objects.filter(action=AuditLog.Action.ACCESS_DENIED).count()

    def test_responsable_du_meme_etablissement_json_200(self):
        client = self.logged_client(self.manager)
        response = client.post(self.url, HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        response.json()  # le corps doit être du JSON valide

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, Anomaly.Status.HANDLED)
        self.assertEqual(self.anomaly.handled_by, self.manager)

    def test_responsable_du_meme_etablissement_sans_accept_json_redirige(self):
        client = self.logged_client(self.manager)
        response = client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, Anomaly.Status.HANDLED)
        self.assertEqual(self.anomaly.handled_by, self.manager)

    def test_acces_refuse_aux_autres_profils(self):
        forbidden = {
            "responsable_autre_etablissement": self.other_manager,
            "agent": self.agent,
            "parent": self.parent,
        }
        for label, user in forbidden.items():
            with self.subTest(profil=label):
                before = self.denied_count()
                client = self.logged_client(user)

                response = client.post(self.url, HTTP_ACCEPT="application/json")

                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.denied_count(), before + 1)
                self.anomaly.refresh_from_db()
                self.assertEqual(self.anomaly.status, Anomaly.Status.OPEN)
                self.assertIsNone(self.anomaly.handled_by)

    def test_csrf_est_applique(self):
        client = self.logged_client(self.manager, enforce_csrf_checks=True)
        response = client.post(self.url, HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 403)
        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, Anomaly.Status.OPEN)
        self.assertIsNone(self.anomaly.handled_by)
