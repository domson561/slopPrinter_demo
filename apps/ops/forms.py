from django import forms

from apps.catalog.models import ShopSettings
from apps.orders.models import OrderStatus


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label="Powód", max_length=500, widget=forms.Textarea(attrs={"rows": 2})
    )


class ForceStatusForm(forms.Form):
    to_status = forms.ChoiceField(label="Nowy stan", choices=OrderStatus.choices)
    reason = forms.CharField(
        label="Powód", max_length=500, widget=forms.Textarea(attrs={"rows": 2})
    )


class ResolveComplaintForm(forms.Form):
    complaint_id = forms.IntegerField(widget=forms.HiddenInput)
    decision = forms.ChoiceField(
        label="Decyzja", choices=[("reprint", "Uznaj: dodruk"), ("rejected", "Odrzuć")]
    )
    resolution = forms.CharField(
        label="Uzasadnienie", max_length=1000, widget=forms.Textarea(attrs={"rows": 2})
    )


class ShopSettingsForm(forms.ModelForm):
    """Ustawienia rdzenia. Zapis tylko tych pól; pola produktów zapisują ich formularze."""

    class Meta:
        model = ShopSettings
        fields = [
            "auto_approve",
            "lead_time_days",
            "retention_days_after_close",
            "draft_retention_days",
        ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=self.Meta.fields)
        return instance


class OrderFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=[("", "wszystkie")] + list(OrderStatus.choices), required=False
    )
    q = forms.CharField(required=False, label="Numer lub e-mail")
    attention = forms.BooleanField(required=False, label="tylko wymagające uwagi")
