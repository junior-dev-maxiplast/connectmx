"""Malhas do IBGE para o mapa de clientes (API pública e gratuita do IBGE).

Cada malha é buscada uma vez e guardada no banco (`IbgeMalha`): as fronteiras
das UFs e dos municípios praticamente não mudam. As coordenadas são arredondadas
para 3 casas (~100 m), o que mantém o desenho fiel na escala da tela e deixa o
arquivo leve.
"""

import requests
from django.utils import timezone

from .models import IbgeMalha

BASE_URL = "https://servicodados.ibge.gov.br/api/v3/malhas"
NAMES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{key}/municipios"
TIMEOUT_SECONDS = 30
DECIMALS = 3


class IbgeError(RuntimeError):
    pass


def mesh_url(key):
    """`BR` = UFs do Brasil; código IBGE de uma UF (ex.: 41) = municípios da UF."""
    query = "formato=application/vnd.geo%2Bjson&qualidade=minima"
    if key == "BR":
        return f"{BASE_URL}/paises/BR?{query}&intrarregiao=UF"
    return f"{BASE_URL}/estados/{int(key)}?{query}&intrarregiao=municipio"


def _round_ring(ring):
    points = []
    for lon, lat, *_rest in ring:
        point = [round(lon, DECIMALS), round(lat, DECIMALS)]
        if not points or point != points[-1]:
            points.append(point)
    return points


def simplify(geojson):
    """Arredonda as coordenadas, tira pontos repetidos e só deixa o código da área."""
    features = []
    for feature in geojson.get("features") or []:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind == "Polygon":
            coordinates = [_round_ring(ring) for ring in geometry.get("coordinates") or []]
        elif kind == "MultiPolygon":
            coordinates = [[_round_ring(ring) for ring in polygon] for polygon in geometry.get("coordinates") or []]
        else:
            continue
        code = str((feature.get("properties") or {}).get("codarea") or "")
        features.append({"type": "Feature", "properties": {"code": code}, "geometry": {"type": kind, "coordinates": coordinates}})
    return {"type": "FeatureCollection", "features": features}


def _fetch(url):
    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"Accept": "application/vnd.geo+json, application/json"})
    except requests.RequestException as exc:
        raise IbgeError(f"Sem resposta da API de malhas do IBGE ({exc.__class__.__name__}).") from exc
    if response.status_code != 200:
        raise IbgeError(f"A API de malhas do IBGE respondeu {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise IbgeError("A API de malhas do IBGE não devolveu um GeoJSON.") from exc


def mesh(key):
    """GeoJSON da malha (do banco; na primeira vez, da API do IBGE)."""
    key = "BR" if str(key).upper() == "BR" else str(int(key))
    record = IbgeMalha.objects.filter(chave=key).first()
    if record:
        return record.geojson
    url = mesh_url(key)
    geojson = simplify(_fetch(url))
    if not geojson["features"]:
        raise IbgeError("A malha do IBGE veio vazia.")
    if key != "BR":
        # A malha só traz o código: o nome do município vem da API de localidades.
        try:
            names = {str(item.get("id")): item.get("nome") for item in _fetch(NAMES_URL.format(key=key)) if isinstance(item, dict)}
        except IbgeError:
            names = {}
        for feature in geojson["features"]:
            feature["properties"]["name"] = names.get(feature["properties"]["code"], "")
    IbgeMalha.objects.update_or_create(chave=key, defaults={"geojson": geojson, "origem": url, "obtida_em": timezone.now()})
    return geojson
