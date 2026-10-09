"""Ustawienia plakatu w panelu operatora (O5): progi jakości, cennik formatów i papierów."""

from django import forms

from apps.catalog.models import Format, PaperOption, ShopSettings


class PosterSettingsForm(forms.ModelForm):
    """Pola plakatu z `ShopSettings`. Zapis tylko tych pól, żeby nie nadpisać ustawień rdzenia."""

    class Meta:
        model = ShopSettings
        fields = ["dpi_sharp", "dpi_ok", "upscale_factor"]

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=self.Meta.fields)
        return instance


FormatFormSet = forms.modelformset_factory(
    Format, fields=["code", "name", "base_price", "is_active"], extra=0, can_delete=False
)
PaperFormSet = forms.modelformset_factory(
    PaperOption, fields=["code", "name", "price_delta", "is_active"], extra=0, can_delete=False
)
