from django.db import models


class ScreenVisit(models.Model):
    """Quantas vezes cada usuário abriu cada tela do menu.

    Alimenta as seções "Favoritos" (mais usadas) e "Recentes" no topo da
    sidebar. Guarda o `url_name` do catálogo de navegação, não o caminho: se a
    URL mudar, o histórico continua valendo.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="screen_visits")
    url_name = models.CharField(max_length=120)
    visit_count = models.PositiveIntegerField(default=0)
    # Favorito é escolha explícita do usuário (a estrela da sidebar), não
    # dedução por quantidade de acessos.
    is_favorite = models.BooleanField(default=False)
    favorited_at = models.DateTimeField(blank=True, null=True)
    last_visited_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visit_count", "-last_visited_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "url_name"], name="unique_user_screen_visit"),
        ]
        indexes = [
            models.Index(fields=["user", "-last_visited_at"], name="screenvisit_user_recent_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.url_name} ({self.visit_count})"
