"""Entrega dos arquivos enviados (MEDIA_ROOT) com checagem de acesso.

Em produção o Django não serve /media/ (DEBUG desligado), e servir a pasta
direto pelo nginx deixaria qualquer anexo de chamado aberto para quem tiver a
URL, sem login. Aqui cada arquivo só sai para quem pode ver o registro que
aponta para ele.
"""

import mimetypes
import posixpath

from django.conf import settings
from django.http import Http404
from django.views.static import serve

from .models import KnowledgeEntryAttachment, PortalChatUpload, PortalDemandAttachment

# Fotos dos pneus: a tela de pneus (/logistica/) é aberta sem login, então as
# fotos dela também precisam ser.
PUBLIC_PREFIXES = ("tires/",)

# Tipos que o navegador exibe sem executar nada. O resto vai como download:
# um .html ou .svg anexado rodaria script no domínio do ConnectMX.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "text/plain"}


def can_view_media(user, name):
    from .views import _portal_can_access_demand

    if name.startswith(PUBLIC_PREFIXES):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_support_staff", False):
        return True
    # A base de conhecimento é aberta a qualquer usuário logado, e a
    # documentação de um chamado reaproveita os arquivos dos anexos dele.
    if KnowledgeEntryAttachment.objects.filter(file=name).exists():
        return True
    for attachment in PortalDemandAttachment.objects.filter(file=name).select_related("demand"):
        if _portal_can_access_demand(user, attachment.demand):
            return True
    for upload in PortalChatUpload.objects.filter(file=name).select_related("claimed_demand"):
        if upload.user_id == user.id or (
            upload.claimed_demand and _portal_can_access_demand(user, upload.claimed_demand)
        ):
            return True
    return False


def protected_media(request, path):
    name = posixpath.normpath(path).lstrip("/")
    if name.startswith("..") or not can_view_media(request.user, name):
        # 404 também para "sem permissão": não confirma que o arquivo existe.
        raise Http404
    response = serve(request, name, document_root=settings.MEDIA_ROOT)
    content_type = (mimetypes.guess_type(name)[0] or "").lower()
    if content_type not in INLINE_TYPES:
        response["Content-Disposition"] = "attachment"
    response["Content-Security-Policy"] = "sandbox"
    return response
