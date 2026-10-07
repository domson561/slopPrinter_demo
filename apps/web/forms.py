import json

from django import forms

from apps.catalog.models import DeliveryMethod, Format, PaperOption

MAX_QUANTITY = 20  # [PROPOZYCJA]


class UploadForm(forms.Form):
    file = forms.FileField(label="Plik JPG, PNG lub WebP")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)] if data else []


class ConfigureForm(forms.Form):
    format = forms.ModelChoiceField(queryset=Format.objects.filter(is_active=True), label="Format", empty_label=None)
    paper = forms.ModelChoiceField(queryset=PaperOption.objects.filter(is_active=True), label="Papier", empty_label=None)
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


class CheckoutForm(forms.Form):
    email = forms.EmailField(label="E-mail")
    full_name = forms.CharField(label="Imię i nazwisko", max_length=120)
    phone = forms.CharField(label="Telefon", max_length=30)
    line1 = forms.CharField(label="Ulica i numer", max_length=120)
    line2 = forms.CharField(label="Lokal / uwagi dla kuriera", max_length=120, required=False)
    postal_code = forms.CharField(label="Kod pocztowy", max_length=10)
    city = forms.CharField(label="Miejscowość", max_length=80)
    delivery_method = forms.ModelChoiceField(
        queryset=DeliveryMethod.objects.filter(is_active=True), label="Dostawa", empty_label=None, widget=forms.RadioSelect
    )
    consent_terms = forms.BooleanField(label="Akceptuję regulamin.")
    consent_no_withdrawal = forms.BooleanField(
        label="Rozumiem, że plakat jest produktem personalizowanym i nie przysługuje mi prawo odstąpienia od umowy."
    )
    consent_rights = forms.BooleanField(label="Oświadczam, że mam prawo do wykorzystania wgranego obrazka.")

    def address(self):
        d = self.cleaned_data
        return {
            "line1": d["line1"],
            "line2": d.get("line2", ""),
            "postal_code": d["postal_code"],
            "city": d["city"],
            "country": "PL",
        }

    def consents(self):
        d = self.cleaned_data
        return {
            "terms": d["consent_terms"],
            "no_withdrawal": d["consent_no_withdrawal"],
            "image_rights": d["consent_rights"],
        }


class ComplaintForm(forms.Form):
    description = forms.CharField(label="Opisz problem", widget=forms.Textarea(attrs={"rows": 5}))
    photos = MultipleFileField(label="Zdjęcia (opcjonalnie)", required=False)
