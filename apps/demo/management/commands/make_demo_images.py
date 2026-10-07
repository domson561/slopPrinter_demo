from django.core.management.base import BaseCommand

from apps.demo.images_gen import DEMO_IMAGES, IMAGES_DIR, ensure_demo_images


class Command(BaseCommand):
    help = "Generuje obrazki testowe do apps/demo/images/ (rysowane Pillow)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Nadpisz istniejące pliki.")

    def handle(self, *args, **options):
        created = ensure_demo_images(force=options["force"])
        for path in created:
            self.stdout.write(f"utworzono {path.name}")
        self.stdout.write(f"Obrazki demo w {IMAGES_DIR}: {', '.join(DEMO_IMAGES)}")
