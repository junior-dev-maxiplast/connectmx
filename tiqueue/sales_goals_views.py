"""Tela "Metas de Vendas" do Dashes: grade família × mês (kg) que grava na USU_TORCMET do ERP Senior
e, abaixo dela, o % de cada representante na meta de cada família (USU_TORCGER).

Quem tem o painel liberado (DashboardAccess, slug `sales-goals`) vê e edita;
os administradores enxergam todos os painéis. A gravação só mexe no que foi
alterado e confere se ninguém gravou ali desde que a tela abriu.
Regras e SQL em `tiqueue/sales_goals.py` e `tiqueue/sales_goals_reps.py`.
"""

import json
import logging
import re

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import sales_goals, sales_goals_reps
from .views import _dashes_access_required, _is_system_admin, _maxisys_shell

logger = logging.getLogger(__name__)

DASHBOARD_SLUG = "sales-goals"
TITLE = "Metas de Vendas"
MAX_CELLS = 2000
_FAMILY_RE = re.compile(r"^[A-Z0-9]{1,6}$")


def _int(value):
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def _pick(value, choices, default):
    value = _int(value)
    if value in choices:
        return value
    return default if default in choices or not choices else choices[0]


def _year(value):
    value = _int(value)
    return value if value and 2000 <= value <= 2100 else None


def _grid_json(grid, family_names, rep_grid=None, reps_catalog=None):
    payload = {
        "year": grid["year"],
        "outside": grid["outside"],
        "rows": [
            {
                "family": row["family"],
                "name": family_names.get(row["family"], ""),
                "months": [sales_goals.weight_text(value) for value in row["months"]],
                "launches": row["launches"],
            }
            for row in grid["rows"]
        ],
    }
    if rep_grid is not None:
        payload["reps"] = _reps_json(rep_grid, family_names, reps_catalog or {})
    return payload


def _rep_info(reps_catalog, code):
    info = reps_catalog.get(code) or {}
    return info.get("name") or f"Representante {code}", info.get("active", True), bool(info)


def _reps_json(rep_grid, family_names, reps_catalog):
    families = []
    for item in rep_grid:
        reps = []
        for rep in item["reps"]:
            name, active, known = _rep_info(reps_catalog, rep["rep"])
            reps.append({
                "rep": rep["rep"],
                "name": name,
                "active": active,
                "known": known,
                "percent": sales_goals_reps.percent_text(rep["percent"]),
                "varies": rep["varies"],
                "months": [sales_goals.weight_text(value) for value in rep["months"]],
                "launches": rep["launches"],
            })
        families.append({
            "family": item["family"],
            "name": family_names.get(item["family"], ""),
            "meta": [sales_goals.weight_text(value) for value in item["meta"]],
            "reps": reps,
        })
    return families


@_dashes_access_required(DASHBOARD_SLUG)
def dashesSalesGoalsPage(request):
    context = {
        **_maxisys_shell(request, DASHBOARD_SLUG, TITLE),
        "data_error": None,
        "page_url": reverse("dashesSalesGoalsPage"),
        "save_url": reverse("dashesSalesGoalsSave"),
        "month_labels": sales_goals.MONTH_LABELS,
        "is_admin": _is_system_admin(request.user),
    }
    wants_json = request.GET.get("formato") == "json"
    try:
        schema_name = sales_goals.schema()
        catalog = sales_goals.catalog()
        company_codes = [company["code"] for company in catalog["companies"]]
        company = _pick(request.GET.get("empresa"), company_codes, 1)
        branches = catalog["branches"].get(company, [])
        branch = _pick(request.GET.get("filial"), [item["code"] for item in branches], 1)
        grid = sales_goals.load_grid(company, branch, _year(request.GET.get("ano")))
        rep_grid = sales_goals_reps.load_rep_grid(company, branch, grid["year"])
        reps_catalog = sales_goals_reps.representatives()
    except sales_goals.SalesGoalsError as exc:
        if wants_json:
            return JsonResponse({"error": str(exc)}, status=400)
        context["data_error"] = str(exc)
        return render(request, "tiqueue/maxisys/sales_goals.html", context)
    except Exception as exc:
        message = f"Não foi possível consultar o ERP Senior: {exc}"
        if wants_json:
            return JsonResponse({"error": message}, status=502)
        context["data_error"] = message
        return render(request, "tiqueue/maxisys/sales_goals.html", context)

    families = catalog["families"].get(company, [])
    family_names = {item["code"]: item["name"] for item in families}
    payload = _grid_json(grid, family_names, rep_grid, reps_catalog)
    if wants_json:
        return JsonResponse(payload)

    year = grid["year"]
    bases = sales_goals_reps.base_options(year)
    branch_labels = {item["code"]: item["label"] for item in branches}
    summary = []
    for item in grid["summary"]:
        if item["empresa"] != company:
            continue
        summary.append({
            "code": item["filial"],
            "label": branch_labels.get(item["filial"], f"Filial {item['filial']}"),
            "total": item["peso"],
            "total_display": sales_goals.format_kg(item["peso"]),
            "families": item["familias"],
            "active": item["filial"] == branch,
        })
    years = sorted(set(grid["years"]) | {year, year - 1, year + 1})
    context.update({
        "schema_name": schema_name,
        "table_ref": sales_goals.table_ref(schema_name),
        "is_simulation": sales_goals.is_simulation(schema_name),
        "schema_env": sales_goals.SCHEMA_ENV,
        "companies": catalog["companies"],
        "branches": branches,
        "years": years,
        "company": company,
        "branch": branch,
        "branch_label": branch_labels.get(branch, f"Filial {branch}"),
        "year": year,
        "summary": summary,
        "year_total_display": sales_goals.format_kg(sum((item["total"] for item in summary), 0)),
        "grid_payload": payload,
        "families_payload": families,
        "outside": grid["outside"],
        "ger_table_ref": sales_goals_reps.ger_table_ref(schema_name),
        "rep_bases": bases,
        "reps_catalog_payload": sorted(
            ({"code": code, "name": info["name"], "active": info["active"]} for code, info in reps_catalog.items()),
            key=lambda item: (not item["active"], item["name"].upper(), item["code"]),
        ),
        "compute_reps_url": reverse("dashesSalesGoalsRepsCompute"),
        "save_reps_url": reverse("dashesSalesGoalsRepsSave"),
    })
    return render(request, "tiqueue/maxisys/sales_goals.html", context)


def _bad_request(message):
    return JsonResponse({"ok": False, "error": message}, status=400)


def _parse_cells(raw_cells, family_codes):
    if not isinstance(raw_cells, list) or not raw_cells:
        raise sales_goals.SalesGoalsError("Nenhuma célula alterada para gravar.")
    if len(raw_cells) > MAX_CELLS:
        raise sales_goals.SalesGoalsError(f"Muitas células numa gravação só (máximo {MAX_CELLS}).")
    changes, seen = [], set()
    for raw in raw_cells:
        if not isinstance(raw, dict):
            raise sales_goals.SalesGoalsError("Célula em formato inválido.")
        family = str(raw.get("familia") or "").strip().upper()
        month = raw.get("mes")
        if not _FAMILY_RE.match(family):
            raise sales_goals.SalesGoalsError(f"Família inválida: {raw.get('familia')!r}.")
        if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
            raise sales_goals.SalesGoalsError(f"Mês inválido para a família {family}: {month!r}.")
        where = f" na família {family}, mês {month:02d}"
        value = sales_goals.check_weight(sales_goals.to_weight(raw.get("valor")), where)
        original = sales_goals.check_weight(sales_goals.to_weight(raw.get("original")), where)
        if value and family not in family_codes:
            raise sales_goals.SalesGoalsError(f"A família {family} não existe no cadastro (E012FAM) desta empresa.")
        if (family, month) in seen:
            raise sales_goals.SalesGoalsError(f"A célula da família {family}, mês {month:02d}, veio repetida.")
        seen.add((family, month))
        changes.append(sales_goals.Change(family, month, value, original))
    return changes


@_dashes_access_required(DASHBOARD_SLUG)
@require_POST
def dashesSalesGoalsSave(request):
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _bad_request("Envio inválido (JSON).")
    if not isinstance(body, dict):
        return _bad_request("Envio inválido (JSON).")
    company, branch, year = _int(body.get("empresa")), _int(body.get("filial")), _year(body.get("ano"))
    if not company or not branch or not year:
        return _bad_request("Informe empresa, filial e ano.")
    try:
        catalog = sales_goals.catalog()
        if branch not in {item["code"] for item in catalog["branches"].get(company, [])}:
            return _bad_request(f"A filial {branch} não existe na empresa {company}.")
        family_codes = {item["code"] for item in catalog["families"].get(company, [])}
        changes = _parse_cells(body.get("celulas"), family_codes)
        plan = sales_goals.save_grid(company, branch, year, changes)
    except sales_goals.SalesGoalsError as exc:
        return _bad_request(str(exc))
    except Exception as exc:
        logger.exception("Falha ao gravar metas %s/%s/%s", company, branch, year)
        return JsonResponse({"ok": False, "error": f"Não foi possível gravar no ERP Senior: {exc}"}, status=502)

    if plan.conflicts:
        return JsonResponse({
            "ok": False,
            "conflicts": plan.conflicts,
            "error": (
                "Outra pessoa gravou nestas células depois que você abriu a tela. "
                "Nada foi gravado: recarregue para ver os valores atuais."
            ),
        }, status=409)
    counts = plan.counts()
    logger.info(
        "Metas %s (%s/%s/%s) gravadas por %s: %s; peso de representantes refeito em %s linha(s)",
        sales_goals.table_ref(), company, branch, year, request.user.get_username(), counts, len(plan.rep_operations),
    )
    return JsonResponse({"ok": True, "counts": counts, "applied": plan.applied, "reps_updated": len(plan.rep_operations)})


# ---------------------------------------------------------------------------
# Metas por representante (USU_TORCGER)
# ---------------------------------------------------------------------------

MAX_REP_CHANGES = 2000


def _scope_from(source):
    return _int(source.get("empresa")), _int(source.get("filial")), _year(source.get("ano"))


@_dashes_access_required(DASHBOARD_SLUG)
def dashesSalesGoalsRepsCompute(request):
    """% sugerido de cada representante, por família com meta, pelo peso faturado no período-base. Não grava."""
    company, branch, year = _scope_from(request.GET)
    if not company or not branch or not year:
        return _bad_request("Informe empresa, filial e ano.")
    try:
        first, last = sales_goals_reps.parse_base(request.GET.get("base"), year)
        rep_grid = sales_goals_reps.load_rep_grid(company, branch, year)
        families = [item["family"] for item in rep_grid if any(item["meta"])]
        shares = sales_goals_reps.suggested_shares(company, branch, families, (first, last))
        reps_catalog = sales_goals_reps.representatives()
    except sales_goals.SalesGoalsError as exc:
        return _bad_request(str(exc))
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"Não foi possível consultar o ERP Senior: {exc}"}, status=502)
    result = {}
    for family, share in shares.items():
        rows = []
        for rep, percent in sorted(share["percents"].items(), key=lambda pair: (-pair[1], pair[0])):
            name, active, known = _rep_info(reps_catalog, rep)
            rows.append({
                "rep": rep, "name": name, "active": active, "known": known,
                "percent": sales_goals_reps.percent_text(percent),
                "base_kg": sales_goals.weight_text(share["weights"][rep].quantize(sales_goals.WEIGHT_STEP)),
            })
        result[family] = {
            "reps": rows,
            "total_kg": sales_goals.weight_text(share["total"].quantize(sales_goals.WEIGHT_STEP)),
            "groups": share["groups"],
        }
    return JsonResponse({"ok": True, "base": f"{first}" if first == last else f"{first}-{last}", "families": result})


def _parse_rep_changes(raw_changes, reps_catalog):
    if not isinstance(raw_changes, list) or not raw_changes:
        raise sales_goals.SalesGoalsError("Nenhum representante alterado para gravar.")
    if len(raw_changes) > MAX_REP_CHANGES:
        raise sales_goals.SalesGoalsError(f"Muitas alterações numa gravação só (máximo {MAX_REP_CHANGES}).")
    changes, seen = [], set()
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise sales_goals.SalesGoalsError("Representante em formato inválido.")
        family = str(raw.get("familia") or "").strip().upper()
        rep = raw.get("representante")
        if not _FAMILY_RE.match(family):
            raise sales_goals.SalesGoalsError(f"Família inválida: {raw.get('familia')!r}.")
        if not isinstance(rep, int) or isinstance(rep, bool) or not 0 < rep <= 999999:
            raise sales_goals.SalesGoalsError(f"Representante inválido na família {family}: {rep!r}.")
        where = f" na família {family}, representante {rep}"
        percent = sales_goals_reps.check_percent(sales_goals_reps.to_percent(raw.get("percentual")), where)
        original = sales_goals_reps.check_percent(sales_goals_reps.to_percent(raw.get("original")), where)
        if percent and rep not in reps_catalog:
            raise sales_goals.SalesGoalsError(f"O representante {rep} não existe no cadastro (E090REP).")
        if (family, rep) in seen:
            raise sales_goals.SalesGoalsError(f"O representante {rep} veio repetido na família {family}.")
        seen.add((family, rep))
        changes.append(sales_goals_reps.RepChange(family, rep, percent, original))
    return changes


@_dashes_access_required(DASHBOARD_SLUG)
@require_POST
def dashesSalesGoalsRepsSave(request):
    """Grava o % dos representantes alterados; o peso (USU_PESMET) é a meta da família no mês × %."""
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _bad_request("Envio inválido (JSON).")
    if not isinstance(body, dict):
        return _bad_request("Envio inválido (JSON).")
    company, branch, year = _scope_from(body)
    if not company or not branch or not year:
        return _bad_request("Informe empresa, filial e ano.")
    try:
        catalog = sales_goals.catalog()
        if branch not in {item["code"] for item in catalog["branches"].get(company, [])}:
            return _bad_request(f"A filial {branch} não existe na empresa {company}.")
        changes = _parse_rep_changes(body.get("representantes"), sales_goals_reps.representatives())
        plan = sales_goals_reps.save_reps(company, branch, year, changes)
    except sales_goals.SalesGoalsError as exc:
        return _bad_request(str(exc))
    except Exception as exc:
        logger.exception("Falha ao gravar metas por representante %s/%s/%s", company, branch, year)
        return JsonResponse({"ok": False, "error": f"Não foi possível gravar no ERP Senior: {exc}"}, status=502)
    if plan.conflicts:
        return JsonResponse({
            "ok": False,
            "conflicts": plan.conflicts,
            "error": (
                "Outra pessoa mudou o % destes representantes depois que você abriu a tela. "
                "Nada foi gravado: recarregue para ver os valores atuais."
            ),
        }, status=409)
    counts = plan.counts()
    logger.info(
        "Metas por representante %s (%s/%s/%s) gravadas por %s: %s",
        sales_goals_reps.ger_table_ref(), company, branch, year, request.user.get_username(), counts,
    )
    return JsonResponse({"ok": True, "counts": counts, "applied": plan.applied})
