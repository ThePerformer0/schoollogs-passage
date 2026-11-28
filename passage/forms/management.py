"""Server-side validated forms for Manager data management (GES-01..07, SEC-06)."""

from __future__ import annotations

from django import forms

from passage.models import Checkpoint, ParentStudentLink, RouteWindow, School, Student, User


class StudentForm(forms.ModelForm):
    """Create or update a student within the manager's own school (GES-01, GES-07)."""

    class Meta:
        model = Student
        fields = ["first_name", "last_name", "classroom", "status"]

    def __init__(self, *args, school: School, **kwargs) -> None:
        self.school = school
        super().__init__(*args, **kwargs)

    def clean_first_name(self) -> str:
        value = self.cleaned_data["first_name"].strip()
        if not value:
            raise forms.ValidationError("Le prénom est obligatoire.")
        return value

    def clean_last_name(self) -> str:
        value = self.cleaned_data["last_name"].strip()
        if not value:
            raise forms.ValidationError("Le nom est obligatoire.")
        return value

    def clean_classroom(self) -> str:
        value = self.cleaned_data["classroom"].strip()
        if not value:
            raise forms.ValidationError("La classe est obligatoire.")
        return value

    def save(self, commit: bool = True) -> Student:
        student = super().save(commit=False)
        student.school = self.school
        student.full_clean()
        if commit:
            student.save()
        return student


class CheckpointForm(forms.ModelForm):
    """Create or update a checkpoint within the manager's own school (GES-03, GES-07)."""

    class Meta:
        model = Checkpoint
        fields = ["label", "checkpoint_type", "is_active"]

    def __init__(self, *args, school: School, **kwargs) -> None:
        self.school = school
        super().__init__(*args, **kwargs)

    def clean_label(self) -> str:
        value = self.cleaned_data["label"].strip()
        if not value:
            raise forms.ValidationError("Le libellé du point de contrôle est obligatoire.")
        qs = Checkpoint.objects.filter(school=self.school, label__iexact=value)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                "Un point de contrôle avec ce libellé existe déjà dans cet établissement."
            )
        return value

    def save(self, commit: bool = True) -> Checkpoint:
        checkpoint = super().save(commit=False)
        checkpoint.school = self.school
        checkpoint.full_clean()
        if commit:
            checkpoint.save()
        return checkpoint


class RouteWindowForm(forms.ModelForm):
    """Create or update an itinerary window with school & time validation (GES-04, GES-07)."""

    class Meta:
        model = RouteWindow
        fields = ["student", "checkpoint", "order", "start_time", "end_time", "weekdays", "is_active"]
        widgets = {
            "order": forms.NumberInput(attrs={"min": 1, "step": 1}),
            "start_time": forms.TimeInput(
                format="%H:%M",
                attrs={"type": "time", "step": 60},
            ),
            "end_time": forms.TimeInput(
                format="%H:%M",
                attrs={"type": "time", "step": 60},
            ),
            "weekdays": forms.TextInput(
                attrs={"placeholder": "1,2,3,4,5"},
            ),
        }

    def __init__(self, *args, school: School, **kwargs) -> None:
        self.school = school
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(school=school)
        self.fields["checkpoint"].queryset = Checkpoint.objects.filter(school=school)
        self.fields["start_time"].input_formats = ["%H:%M", "%H:%M:%S"]
        self.fields["end_time"].input_formats = ["%H:%M", "%H:%M:%S"]
        self.fields["start_time"].help_text = "Format 24 h (HH:MM), par ex. 07:00."
        self.fields["end_time"].help_text = "Format 24 h (HH:MM), après l'heure de début."
        self.fields["weekdays"].help_text = (
            "1 = Lun, 2 = Mar, 3 = Mer, 4 = Jeu, 5 = Ven, 6 = Sam, 7 = Dim (séparés par des virgules)."
        )

    def clean(self) -> dict:
        cleaned = super().clean()
        student = cleaned.get("student")
        checkpoint = cleaned.get("checkpoint")
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")

        if student and student.school_id != self.school.id:
            self.add_error("student", "Cet élève n'appartient pas à votre établissement.")
        if checkpoint and checkpoint.school_id != self.school.id:
            self.add_error("checkpoint", "Ce point n'appartient pas à votre établissement.")
        if start_time and end_time and end_time <= start_time:
            self.add_error("end_time", "L'heure de fin doit être postérieure à l'heure de début.")
        return cleaned


class ParentStudentLinkForm(forms.ModelForm):
    """Attach a parent account to a student of the manager's school (GES-02, GES-07)."""

    class Meta:
        model = ParentStudentLink
        fields = ["parent", "student"]

    def __init__(self, *args, school: School, **kwargs) -> None:
        self.school = school
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = User.objects.filter(
            role=User.Role.PARENT, is_active=True
        )
        self.fields["student"].queryset = Student.objects.filter(school=school)
