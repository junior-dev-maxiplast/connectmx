"""Filtro por clique nas telas montadas no servidor (Painel Gerencial, Mapa, Carteira).

Os filtros vão no endereço como listas separadas por vírgula (`?rep=10004,10010&grupo=...`),
então o link compartilhado abre igual. Na tela, `maxisysFilterLinks.js` troca o parâmetro ao
clicar num elemento com `data-mx-filter` (Ctrl + clique soma ou tira o valor).
"""

from urllib.parse import urlencode


def values(params, key):
    raw = params.get(key) or ""
    seen = []
    for part in raw.split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def ints(params, key):
    result = []
    for part in values(params, key):
        try:
            number = int(part)
        except ValueError:
            continue
        if number not in result:
            result.append(number)
    return result


def month_keys(params, key="meses"):
    """`AAAA-MM` separados por vírgula → chaves de mês (ano × 12 + mês de 0 a 11)."""
    keys = set()
    for part in values(params, key):
        try:
            year, month = (int(piece) for piece in part.split("-"))
        except ValueError:
            continue
        if 1 <= month <= 12:
            keys.add(year * 12 + month - 1)
    return sorted(keys)


def month_param(key):
    return f"{key // 12}-{key % 12 + 1:02d}"


def url_without(path, params, *keys):
    query = {name: value for name, value in params.items() if name not in keys and value}
    return f"{path}?{urlencode(query)}" if query else path


def url_with(path, params, drop=(), **changes):
    query = {name: value for name, value in params.items() if name not in drop and value}
    for name, value in changes.items():
        if value:
            query[name] = value
        else:
            query.pop(name, None)
    return f"{path}?{urlencode(query)}" if query else path


def chips(path, params, specs):
    """`specs`: [(chave, rótulo, [nomes escolhidos])] → etiquetas dos filtros ativos, com o link que tira cada um."""
    result = []
    for key, label, names in specs:
        if not names:
            continue
        text = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
        result.append({"key": key, "label": label, "text": text, "title": ", ".join(names), "remove_url": url_without(path, params, key)})
    return result
