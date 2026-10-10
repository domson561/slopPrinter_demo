"""Formularze plakatu: wgranie i konfigurator (S1, S2) oraz ustawienia w panelu operatora (O5)."""

import json

from django import forms

from .models import Format, PaperOption, PosterSettings

MAX_QUANTITY = 20  # [PROPOZYCJA]


class UploadForm(forms.Form):
    file = forms.FileField(label="Plik JPG, PNG lub WebP")


class ConfigureForm(forms.Form):
    format = forms.ModelChoiceField(
        queryset=Format.objects.filter(is_active=True), label="Format", empty_label=None
    )
    paper = forms.ModelChoiceField(
        queryset=PaperOption.objects.filter(is_active=True), label="Papier", empty_label=None
    )
    white_margin = forms.BooleanField(label="Biały margines", required=False)
    quantity = forms.IntegerField(label="Ilość", min_value=1, max_value=MAX_QUANTITY, initial=1)
    enhancer_enabled = forms.BooleanField(label="Popraw jakość (enhancer)", required=False)
    crop = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_crop(self):
        raw = self.cleaned_data.get("crop") or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


class PosterSettingsForm(forms.ModelForm):
    class Meta:
        model = PosterSettings
        fields = ["dpi_sharp", "dpi_ok", "upscale_factor"]


FormatFormSet = forms.modelformset_factory(
    Format, fields=["code", "name", "base_price", "is_active"], extra=0, can_delete=False
)
PaperFormSet = forms.modelformset_factory(
    PaperOption, fields=["code", "name", "price_delta", "is_active"], extra=0, can_delete=False
)
