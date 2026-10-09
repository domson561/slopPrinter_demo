from django import forms

from apps.catalog.models import Format, PaperOption, ShopSettings
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
    class Meta:
        model = ShopSettings
        fields = [
            "dpi_sharp",
            "dpi_ok",
            "upscale_factor",
            "auto_approve",
            "lead_time_days",
            "retention_days_after_close",
            "draft_retention_days",
        ]


FormatFormSet = forms.modelformset_factory(
    Format, fields=["code", "name", "base_price", "is_active"], extra=0, can_delete=False
)
PaperFormSet = forms.modelformset_factory(
    PaperOption, fields=["code", "name", "price_delta", "is_active"], extra=0, can_delete=False
)


class OrderFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=[("", "wszystkie")] + list(OrderStatus.choices), required=False
    )
    q = forms.CharField(required=False, label="Numer lub e-mail")
    attention = forms.BooleanField(required=False, label="tylko wymagające uwagi")
