import os
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal


CUSTOMER_DNA_SQL = """
SELECT
  A.CODEMP AS EMPRESA,
  A.CODFIL AS FILIAL,
  A.NUMNFV AS NOTA_FISCAL,
  A.SEQIPV AS SEQUENCIA_NOTA,
  A.NUMPED AS PEDIDO,
  A.CODPRO AS CODIGO_PRODUTO,
  A.VLRBRU AS VALOR_BRUTO_FATURADO,
  A.QTDFAT AS QUANTIDADE_FATURADA,
  A.CODEMP || '-' || A.CODFIL || '-' || A.NUMPED AS CHV_PEDIDO,
  A.DATGER AS DATA_GERACAO,
  B.CODCLI AS CODIGO_CLIENTE,
  A.PESBRU AS PESO_BRUTO_FATURADO,
  A.CODEMP || '-' || A.CODPRO AS CHV_PRODUTO,
  C.CODCLI || ' - ' || C.NOMCLI || ', ' || C.CGCCPF AS NOME_CLIENTE_CNPJ_CODIGO,
  C.NOMCLI AS NOME_CLIENTE,
  C.CGCCPF AS CNPJ_CLIENTE,
  C.SIGUFS AS SIGLA_ESTADO_CLIENTE,
  C.TIPMER AS TIPO_MERCADO_CLIENTE,
  C.ENDCLI AS ENDERECO_CLIENTE,
  C.CIDCLI AS CIDADE_CLIENTE,
  SUBSTR(C.NOMCLI, 0, INSTR(C.NOMCLI, ' ')) AS NOME_CLIENTE_AGRUPADOR,
  CASE
    WHEN C.SIGUFS IN ('SC', 'RS', 'PR') THEN 'Sul'
    WHEN C.SIGUFS IN ('SP', 'RJ', 'MG', 'ES') THEN 'Sudeste'
    WHEN C.SIGUFS IN ('MT', 'MS', 'GO', 'DF') THEN 'Centro-Oeste'
    WHEN C.SIGUFS IN ('BA', 'SE', 'AL', 'PE', 'PB', 'RN', 'CE', 'PI', 'MA') THEN 'Nordeste'
    WHEN C.SIGUFS IN ('AM', 'PA', 'AC', 'RO', 'RR', 'AP', 'TO') THEN 'Norte'
    ELSE 'Não definido'
  END AS REGIAO_CLIENTE,
  D.TNSPRO AS TRANSACAO_PRODUTO_CODIGO,
  G.DESTNS AS TRANSACAO_DESCRICAO_COMPLETA,
  D.DATEMI AS EMISSAO_PEDIDO,
  D.DATPRV AS PREVISAO_PEDIDO,
  D.CODREP AS REPRESENTANTE_PEDIDO_CODIGO,
  D.QTDORI AS QUANTIDADE_PEDIDA,
  D.VLRORI AS VALOR_PEDIDO,
  D.VLRLIQ AS VALOR_LIQUIDO_PEDIDO,
  D.CODCPG AS CODIGO_CONDICAO_PAGAMENTO_PEDIDO,
  E.DESCPG AS DESCRICAO_CONDICAO_PAGAMENTO_PEDIDO,
  D.CODREP || ' - ' || F.NOMREP AS REPRESENTANTE_COMPLETO_PEDIDO,
  CASE
    WHEN D.SITPED = 1 THEN 'Aberto Total'
    WHEN D.SITPED = 2 THEN 'Aberto Parcial'
    WHEN D.SITPED = 3 THEN 'Suspenso'
    WHEN D.SITPED = 4 THEN 'Liquidado'
    WHEN D.SITPED = 5 THEN 'Cancelado'
    WHEN D.SITPED = 6 THEN 'Aguardando Integração WMS'
    WHEN D.SITPED = 7 THEN 'Em Transmissão'
    WHEN D.SITPED = 8 THEN 'Preparação Análise ou NF'
    WHEN D.SITPED = 9 THEN 'Não Fechado'
    ELSE 'Outro'
  END AS SITUACAO_PEDIDO,
  CASE
    WHEN D.SITPED IN (1, 2, 3, 6, 7, 8, 9) THEN 'Aberto'
    WHEN D.SITPED = 4 THEN 'Liquidado'
    WHEN D.SITPED = 5 THEN 'Cancelado'
    ELSE 'Aberto'
  END AS SITUACAO_RESUMIDA_PEDIDO,
  J.DESPRO AS DESCRICAO_PRODUTO,
  J.CODPRO || ' - ' || J.DESPRO AS PRODUTO_DESCRICAO_COMPLETA_CODIGO,
  J.CODFAM AS FAMILIA_CODIGO,
  K.DESFAM AS FAMILIA_DESCRICAO,
  SUBSTR(J.CODFAM, 0, 3) AS ORIGEM_PRODUTO
FROM E140IPV A
LEFT JOIN E140NFV B
  ON A.CODEMP = B.CODEMP AND A.CODFIL = B.CODFIL AND A.NUMNFV = B.NUMNFV
LEFT JOIN E085CLI C ON B.CODCLI = C.CODCLI
LEFT JOIN E120PED D
  ON A.NUMPED = D.NUMPED AND A.CODEMP = D.CODEMP AND A.CODFIL = D.CODFIL
LEFT JOIN E028CPG E ON D.CODEMP = E.CODEMP AND D.CODCPG = E.CODCPG
LEFT JOIN E090REP F ON D.CODREP = F.CODREP
LEFT JOIN E001TNS G ON D.TNSPRO = G.CODTNS AND D.CODEMP = G.CODEMP
LEFT JOIN E075PRO J ON J.CODPRO = A.CODPRO AND J.CODFAM = A.CODFAM
LEFT JOIN E012FAM K ON J.CODFAM = K.CODFAM AND J.CODEMP = K.CODEMP
WHERE B.CODCLI NOT IN (1001, 1002, 1004)
  AND A.CODSNF = 'NFE'
  AND D.SITPED <> 5
  AND D.VLRORI <> 0
  AND D.TNSPRO NOT IN ('90109', '90121', '90108')
  AND B.CODCLI = :customer_id
ORDER BY A.DATGER DESC, A.NUMNFV DESC, A.SEQIPV DESC
"""


CUSTOMER_SEARCH_SQL = """
SELECT * FROM (
  SELECT DISTINCT
    C.CODCLI AS CODIGO,
    C.NOMCLI AS NOME,
    C.CGCCPF AS CNPJ,
    C.CIDCLI AS CIDADE,
    C.SIGUFS AS UF
  FROM E085CLI C
  INNER JOIN E140NFV N ON N.CODCLI = C.CODCLI
  WHERE C.CODCLI NOT IN (1001, 1002, 1004)
    AND (
      :term IS NULL
      OR TO_CHAR(C.CODCLI) LIKE :pattern
      OR UPPER(C.NOMCLI) LIKE :pattern
      OR REPLACE(REPLACE(REPLACE(C.CGCCPF, '.', ''), '/', ''), '-', '') LIKE :digits_pattern
      OR UPPER(C.CIDCLI) LIKE :pattern
    )
  ORDER BY C.NOMCLI
)
WHERE ROWNUM <= 12
"""


CUSTOMER_COMPLAINTS_SQL = """
SELECT
  A.USU_CODEMP AS EMPRESA,
  A.USU_CODFIL AS FILIAL,
  A.USU_DATREC AS DATA_RECLAMACAO,
  A.USU_CODREC AS COD_RECLAMACAO,
  A.USU_CODCLI AS COD_CLIENTE,
  A.USU_UNICLI AS UNIDADE_CLIENTE,
  A.USU_NUMNFV AS NOTA_FISCAL,
  A.USU_PROESP AS PROBLEMA_ESPECIFICO,
  A.USU_VOLNCO AS VOLUME_NAO_CONFORME,
  A.USU_CODCRE AS CODIGO_RECURSO,
  A.USU_VOLFAT AS VOLUME_FATURADO,
  A.USU_VOLDEV AS VOLUME_DEVOLVIDO,
  A.USU_UNIMED AS UNIDADE_MEDIDA,
  B.DESCRE AS MAQUINA,
  CASE
    WHEN A.USU_CLSPRO = 'L' THEN 'Leve'
    WHEN A.USU_CLSPRO = 'M' THEN 'Média'
    WHEN A.USU_CLSPRO = 'G' THEN 'Grave'
    ELSE 'Leve'
  END AS CLASSIFICACAO
FROM USU_TRECCLI A
LEFT JOIN E725CRE B
  ON A.USU_CODEMP = B.CODEMP AND A.USU_CODCRE = B.CODCRE
WHERE A.USU_CODCLI = :customer_id
ORDER BY A.USU_DATREC DESC, A.USU_CODREC DESC
"""


CUSTOMER_RETURNS_SQL = """
SELECT
  A.USU_CODEMP AS EMPRESA,
  A.USU_CODFIL AS FILIAL,
  A.USU_SEQDOC AS SEQUENCIA,
  A.USU_CODEMP || '-' || A.USU_CODFIL || '-' || A.USU_SEQDOC AS CHV_DEVOLUCAO,
  A.USU_OBSDEV AS OBSERVACAO,
  A.USU_CODCLI AS CLIENTE,
  A.USU_NUMNFV AS NOTA,
  A.USU_TOTDEV AS DEVOLVIDO,
  A.USU_DATDEV AS DATA_DEVOLUCAO,
  B.USU_DESPRB AS PROBLEMA,
  C.USU_DESSET AS SETOR,
  D.SIGFIL AS FILIAL_DES
FROM USU_TDEVCLI A
LEFT JOIN USU_TCadPrB B ON A.USU_CODPRB = B.USU_CODPRB
LEFT JOIN USU_TCADSET C ON A.USU_SETCAU = C.USU_CODSET
LEFT JOIN E070FIL D
  ON A.USU_CODFIL = D.CODFIL AND A.USU_CODEMP = D.CODEMP
WHERE A.USU_CODCLI = :customer_id
ORDER BY A.USU_DATDEV DESC, A.USU_SEQDOC DESC
"""


CUSTOMER_CLICHES_SQL = """
SELECT
  A.USU_CODLAN AS LANCAMENTO,
  A.USU_CODEMP AS EMPRESA,
  A.USU_CODFIL AS FILIAL,
  A.USU_NUMPED AS PEDIDO,
  A.USU_CODCLI AS CLIENTE,
  A.USU_ARETOT AS AREA,
  A.USU_VLRCTT AS VALOR,
  A.USU_DATDES AS DATA_DESPACHE,
  CASE WHEN A.USU_TIPTRO = 'MX' THEN 'Maxiplast' ELSE 'Cliente' END AS CUSTO_TROCA
FROM USU_TPRECLI A
WHERE A.USU_CODCLI = :customer_id
ORDER BY A.USU_DATDES DESC NULLS LAST, A.USU_CODLAN DESC
"""


def _oracle_connection():
    host = os.getenv("ERP_DB_HOST", "192.168.30.2")
    port = int(os.getenv("ERP_DB_PORT", "1521"))
    service_name = os.getenv("ERP_DB_NAME", "dbprod")
    user = os.getenv("ERP_DB_USER", "sapiens")
    password = os.getenv("ERP_DB_PASSWORD", "sapiens")
    last_error = None

    for driver_name in ("oracledb", "cx_Oracle"):
        try:
            if driver_name == "oracledb":
                import oracledb as oracle_driver
            else:
                import cx_Oracle as oracle_driver
            dsn = oracle_driver.makedsn(host, port, service_name=service_name)
            return oracle_driver.connect(user=user, password=password, dsn=dsn)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Não foi possível conectar ao ERP Senior: {last_error}")


def _oracle_connection_safe():
    host = os.getenv("ERP_DB_HOST", "192.168.30.2")
    port = int(os.getenv("ERP_DB_PORT", "1521"))
    service_name = os.getenv("ERP_DB_NAME", "dbprod")
    user = os.getenv("ERP_DB_USER", "sapiens")
    password = os.getenv("ERP_DB_PASSWORD", "sapiens")
    driver_errors = {}

    for driver_name in ("oracledb", "cx_Oracle"):
        try:
            if driver_name == "oracledb":
                import oracledb as oracle_driver
            else:
                import cx_Oracle as oracle_driver
            dsn = oracle_driver.makedsn(host, port, service_name=service_name)
            return oracle_driver.connect(user=user, password=password, dsn=dsn)
        except Exception as exc:
            driver_errors[driver_name] = str(exc)

    primary_error = driver_errors.get("oracledb") or driver_errors.get("cx_Oracle") or "driver Oracle nao encontrado"
    fallback_error = driver_errors.get("cx_Oracle")
    if fallback_error and fallback_error != primary_error:
        raise RuntimeError(
            "Nao foi possivel conectar ao ERP Senior. "
            f"oracledb: {primary_error}. "
            f"Fallback cx_Oracle: {fallback_error}."
        )
    raise RuntimeError(f"Nao foi possivel conectar ao ERP Senior: {primary_error}")


def _query(sql, params):
    connection = _oracle_connection_safe()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        columns = [column[0].lower() for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _query_many(statements, params):
    connection = _oracle_connection_safe()
    results = {}
    try:
        for name, sql in statements.items():
            cursor = connection.cursor()
            try:
                cursor.execute(sql, params)
                columns = [column[0].lower() for column in cursor.description]
                results[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                cursor.close()
    finally:
        connection.close()
    return results


def _load_customer_sources(customer_id):
    return _query_many(
        {
            "sales": CUSTOMER_DNA_SQL,
            "complaints": CUSTOMER_COMPLAINTS_SQL,
            "returns": CUSTOMER_RETURNS_SQL,
            "cliches": CUSTOMER_CLICHES_SQL,
        },
        {"customer_id": customer_id},
    )


def search_customers(term):
    normalized = (term or "").strip().upper()
    digits = "".join(character for character in normalized if character.isdigit())
    rows = _query(
        CUSTOMER_SEARCH_SQL,
        {
            "term": normalized or None,
            "pattern": f"%{normalized}%",
            "digits_pattern": f"%{digits}%" if digits else "%__NO_DIGITS__%",
        },
    )
    return [
        {
            "code": row["codigo"],
            "name": row["nome"] or "Cliente sem nome",
            "cnpj": row["cnpj"] or "",
            "city": row["cidade"] or "",
            "state": row["uf"] or "",
        }
        for row in rows
    ]


def _number(value):
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _format_money(value, compact=False):
    amount = float(_number(value))
    suffix = ""
    if compact and abs(amount) >= 1_000_000:
        amount /= 1_000_000
        suffix = " Mi"
    elif compact and abs(amount) >= 1_000:
        amount /= 1_000
        suffix = " mil"
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}{suffix}"


def _format_weight(value):
    weight = float(_number(value))
    if abs(weight) >= 1_000_000:
        return f"{weight / 1_000_000:,.2f} mil t".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(weight) >= 1_000:
        return f"{weight / 1_000:,.2f} t".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{weight:,.2f} kg".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_number(value, decimals=1):
    return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _iso_date(value):
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else ""


def _display_date(value):
    parsed = _date_value(value)
    return parsed.strftime("%d/%m/%Y") if parsed else "-"


def _distribution(values):
    counts = defaultdict(int)
    for value in values:
        label = str(value or "Não informado").strip() or "Não informado"
        counts[label] += 1
    total = sum(counts.values())
    return [
        {"label": label, "count": count, "share_pct": round(count / total * 100, 1) if total else 0.0}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _build_operational_data(complaint_rows, return_rows, cliche_rows, total_revenue):
    complaints = []
    for row in complaint_rows:
        nonconforming_volume = _number(row.get("volume_nao_conforme"))
        billed_volume = _number(row.get("volume_faturado"))
        complaints.append({
            "code": row.get("cod_reclamacao"),
            "date": _iso_date(row.get("data_reclamacao")),
            "date_display": _display_date(row.get("data_reclamacao")),
            "unit": row.get("unidade_cliente") or "Não informada",
            "invoice": row.get("nota_fiscal"),
            "problem": row.get("problema_especifico") or "Não informado",
            "nonconforming_volume": float(nonconforming_volume),
            "billed_volume": float(billed_volume),
            "returned_volume": float(_number(row.get("volume_devolvido"))),
            "incidence_pct": round(float(nonconforming_volume / billed_volume * 100), 2) if billed_volume else 0.0,
            "measurement_unit": (row.get("unidade_medida") or "").strip() or "-",
            "machine": row.get("maquina") or "Não informada",
            "classification": row.get("classificacao") or "Leve",
        })
    returned_documents = [
        {
            "key": row.get("chv_devolucao") or str(row.get("sequencia") or ""),
            "sequence": row.get("sequencia"),
            "date": _iso_date(row.get("data_devolucao")),
            "date_display": _display_date(row.get("data_devolucao")),
            "invoice": row.get("nota"),
            "value": float(_number(row.get("devolvido"))),
            "value_display": _format_money(row.get("devolvido")),
            "problem": row.get("problema") or "Não informado",
            "sector": row.get("setor") or "Não informado",
            "branch": row.get("filial_des") or str(row.get("filial") or "-"),
            "observation": row.get("observacao") or "",
        }
        for row in return_rows
    ]
    cliches = [
        {
            "launch": row.get("lancamento"),
            "order": row.get("pedido"),
            "date": _iso_date(row.get("data_despache")),
            "date_display": _display_date(row.get("data_despache")),
            "area": float(_number(row.get("area"))),
            "area_display": _format_number(row.get("area"), 2),
            "value": float(_number(row.get("valor"))),
            "value_display": _format_money(row.get("valor")),
            "exchange_cost": row.get("custo_troca") or "Cliente",
        }
        for row in cliche_rows
    ]

    complaint_units = defaultdict(lambda: {"count": 0, "nonconforming_volume": Decimal("0"), "billed_volume": Decimal("0"), "returned_volume": Decimal("0")})
    for item in complaints:
        unit = item["measurement_unit"]
        complaint_units[unit]["count"] += 1
        complaint_units[unit]["nonconforming_volume"] += _number(item["nonconforming_volume"])
        complaint_units[unit]["billed_volume"] += _number(item["billed_volume"])
        complaint_units[unit]["returned_volume"] += _number(item["returned_volume"])
    volume_by_unit = []
    for unit, values in sorted(complaint_units.items(), key=lambda item: item[1]["count"], reverse=True):
        volume_by_unit.append({
            "unit": unit,
            "count": values["count"],
            "nonconforming_volume": float(values["nonconforming_volume"]),
            "nonconforming_volume_display": _format_number(values["nonconforming_volume"], 2),
            "billed_volume": float(values["billed_volume"]),
            "returned_volume": float(values["returned_volume"]),
            "incidence_pct": round(float(values["nonconforming_volume"] / values["billed_volume"] * 100), 2) if values["billed_volume"] else 0.0,
        })
    valid_incidence = [item["incidence_pct"] for item in complaints if item["billed_volume"]]
    average_incidence = round(sum(valid_incidence) / len(valid_incidence), 2) if valid_incidence else 0.0
    return_value = sum((_number(item["value"]) for item in returned_documents), Decimal("0"))
    cliche_value = sum((_number(item["value"]) for item in cliches), Decimal("0"))
    cliche_area = sum((_number(item["area"]) for item in cliches), Decimal("0"))
    severe_count = sum(1 for item in complaints if item["classification"] == "Grave")
    customer_cliche_value = sum((_number(item["value"]) for item in cliches if item["exchange_cost"] == "Cliente"), Decimal("0"))
    maxiplast_cliche_value = cliche_value - customer_cliche_value

    return {
        "complaints": {
            "count": len(complaints),
            "severe_count": severe_count,
            "severe_share_pct": round(severe_count / len(complaints) * 100, 1) if complaints else 0.0,
            "incidence_pct": average_incidence,
            "measurement_units_count": len(volume_by_unit),
            "volume_by_unit": volume_by_unit,
            "classification_distribution": _distribution(item["classification"] for item in complaints),
            "problem_distribution": _distribution(item["problem"] for item in complaints),
            "machine_distribution": _distribution(item["machine"] for item in complaints),
            "items": complaints,
            "recent_items": complaints[:8],
        },
        "returns": {
            "count": len(returned_documents),
            "total_value": float(return_value),
            "total_value_display": _format_money(return_value, compact=True),
            "revenue_share_pct": round(float(return_value / total_revenue * 100), 2) if total_revenue else 0.0,
            "problem_distribution": _distribution(item["problem"] for item in returned_documents),
            "sector_distribution": _distribution(item["sector"] for item in returned_documents),
            "items": returned_documents,
            "recent_items": returned_documents[:8],
        },
        "cliches": {
            "count": len(cliches),
            "total_value": float(cliche_value),
            "total_value_display": _format_money(cliche_value, compact=True),
            "total_area": float(cliche_area),
            "total_area_display": _format_number(cliche_area, 2),
            "average_value": float(cliche_value / len(cliches)) if cliches else 0.0,
            "average_value_display": _format_money(cliche_value / len(cliches) if cliches else 0),
            "customer_cost_value": float(customer_cliche_value),
            "customer_cost_value_display": _format_money(customer_cliche_value, compact=True),
            "maxiplast_cost_value": float(maxiplast_cliche_value),
            "maxiplast_cost_value_display": _format_money(maxiplast_cliche_value, compact=True),
            "cost_distribution": _distribution(item["exchange_cost"] for item in cliches),
            "items": cliches,
            "recent_items": cliches[:8],
        },
    }


def _build_dashboard(rows, customer_id, complaint_rows=None, return_rows=None, cliche_rows=None):
    if not rows:
        return None

    first = rows[0]
    total_revenue = sum((_number(row["valor_bruto_faturado"]) for row in rows), Decimal("0"))
    total_weight = sum((_number(row["peso_bruto_faturado"]) for row in rows), Decimal("0"))
    total_quantity = sum((_number(row["quantidade_faturada"]) for row in rows), Decimal("0"))
    invoices = {str(row["nota_fiscal"]) for row in rows if row["nota_fiscal"] is not None}

    yearly = defaultdict(lambda: {"revenue": Decimal("0"), "weight": Decimal("0"), "orders": set()})
    product_totals = defaultdict(lambda: {"revenue": Decimal("0"), "weight": Decimal("0"), "quantity": Decimal("0")})
    orders = {}
    payment_conditions = defaultdict(int)
    representatives = defaultdict(int)

    for row in rows:
        generation_date = _date_value(row["data_geracao"])
        if generation_date:
            bucket = yearly[generation_date.year]
            bucket["revenue"] += _number(row["valor_bruto_faturado"])
            bucket["weight"] += _number(row["peso_bruto_faturado"])
            bucket["orders"].add(str(row["pedido"]))

        product_name = row["descricao_produto"] or f"Produto {row['codigo_produto']}"
        product = product_totals[product_name]
        product["revenue"] += _number(row["valor_bruto_faturado"])
        product["weight"] += _number(row["peso_bruto_faturado"])
        product["quantity"] += _number(row["quantidade_faturada"])

        order_key = str(row["chv_pedido"] or row["pedido"])
        if order_key not in orders:
            orders[order_key] = {
                "number": row["pedido"],
                "date": row["emissao_pedido"] or row["data_geracao"],
                "forecast": row["previsao_pedido"],
                "value": _number(row["valor_pedido"]),
                "net_value": _number(row["valor_liquido_pedido"]),
                "status": row["situacao_pedido"] or "-",
                "status_summary": row["situacao_resumida_pedido"] or "-",
                "weight": Decimal("0"),
            }
        orders[order_key]["weight"] += _number(row["peso_bruto_faturado"])

        if row["descricao_condicao_pagamento_pedido"]:
            payment_conditions[row["descricao_condicao_pagamento_pedido"]] += 1
        if row["representante_completo_pedido"]:
            representatives[row["representante_completo_pedido"]] += 1

    yearly_rows = []
    for year in sorted(yearly):
        values = yearly[year]
        yearly_rows.append(
            {
                "label": str(year),
                "revenue": float(values["revenue"]),
                "weight": float(values["weight"]),
                "orders": len(values["orders"]),
            }
        )

    product_rows = []
    sorted_products = sorted(product_totals.items(), key=lambda item: item[1]["revenue"], reverse=True)
    for name, values in sorted_products[:6]:
        share = (values["revenue"] / total_revenue * 100) if total_revenue else Decimal("0")
        product_rows.append(
            {
                "name": name,
                "revenue": _format_money(values["revenue"], compact=True),
                "weight": _format_weight(values["weight"]),
                "share": round(float(share), 1),
            }
        )

    sorted_orders = sorted(orders.values(), key=lambda item: _date_value(item["date"]) or date.min, reverse=True)
    order_rows = [
        {
            **order,
            "date_display": _display_date(order["date"]),
            "date_iso": _iso_date(order["date"]),
            "forecast_display": _display_date(order["forecast"]),
            "value_display": _format_money(order["value"]),
            "weight_display": _format_weight(order["weight"]),
        }
        for order in sorted_orders[:8]
    ]

    purchase_dates = [_date_value(order["date"]) for order in orders.values()]
    purchase_dates = sorted(value for value in purchase_dates if value)
    relationship_days = (date.today() - purchase_dates[0]).days if purchase_dates else 0
    last_purchase_days = (date.today() - purchase_dates[-1]).days if purchase_dates else None
    relationship_years = relationship_days // 365
    relationship_months = (relationship_days % 365) // 30
    average_ticket = total_revenue / len(orders) if orders else Decimal("0")
    average_value_kg = total_revenue / total_weight if total_weight else Decimal("0")
    top_payment = max(payment_conditions, key=payment_conditions.get) if payment_conditions else "Não informado"
    top_representative = max(representatives, key=representatives.get) if representatives else "Não informado"
    operational = _build_operational_data(
        complaint_rows or [],
        return_rows or [],
        cliche_rows or [],
        total_revenue,
    )

    return {
        "customer": {
            "code": customer_id,
            "name": first["nome_cliente"] or first["nome_cliente_agrupador"] or f"Cliente {customer_id}",
            "cnpj": str(first["cnpj_cliente"]) if first["cnpj_cliente"] is not None else "Não informado",
            "city": first["cidade_cliente"] or "Não informado",
            "state": first["sigla_estado_cliente"] or "-",
            "region": first["regiao_cliente"] or "Não definido",
            "market": first["tipo_mercado_cliente"] or "Não informado",
            "address": first["endereco_cliente"] or "Não informado",
            "representative": top_representative,
        },
        "metrics": {
            "revenue": _format_money(total_revenue, compact=True),
            "weight": _format_weight(total_weight),
            "average_value_kg": _format_money(average_value_kg),
            "average_ticket": _format_money(average_ticket, compact=True),
            "orders": len(orders),
            "invoices": len(invoices),
            "quantity": f"{float(total_quantity):,.0f}".replace(",", "."),
        },
        "relationship": {
            "last_purchase": _display_date(purchase_dates[-1]) if purchase_dates else "-",
            "last_purchase_days": last_purchase_days,
            "since": _display_date(purchase_dates[0]) if purchase_dates else "-",
            "duration": f"{relationship_years} anos e {relationship_months} meses",
        },
        "profile": {
            "main_product": product_rows[0]["name"] if product_rows else "Não informado",
            "frequency": round(len(orders) / max(1, len(yearly_rows) * 12), 1),
            "average_ticket": _format_money(average_ticket, compact=True),
            "payment_condition": top_payment,
            "products_count": len(product_totals),
        },
        "yearly": yearly_rows,
        "products": product_rows,
        "orders": order_rows,
        "operational": operational,
    }


def _percent_change(current, previous):
    current_value = _number(current)
    previous_value = _number(previous)
    if previous_value == 0:
        return 0.0 if current_value == 0 else None
    return round(float((current_value - previous_value) / previous_value * 100), 1)


def _format_percent(value):
    if value is None:
        return "n/d"
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.1f}%".replace(".", ",")


def _month_index(value):
    return value.year * 12 + value.month


def _shift_month(value, delta):
    index = value.year * 12 + value.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def _source_fingerprint(sources):
    def serialize(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    signatures = {}
    for source_name, rows in sorted(sources.items()):
        signatures[source_name] = sorted(
            (
                {key: serialize(value) for key, value in sorted(row.items())}
                for row in rows
            ),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
        )
    encoded = json.dumps(signatures, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_order_map(rows):
    orders = {}
    for row in rows:
        order_key = str(row.get("chv_pedido") or row.get("pedido") or "")
        if not order_key:
            continue
        if order_key not in orders:
            orders[order_key] = {
                "number": row.get("pedido"),
                "issue_date": _date_value(row.get("emissao_pedido")) or _date_value(row.get("data_geracao")),
                "forecast_date": _date_value(row.get("previsao_pedido")),
                "value": _number(row.get("valor_pedido")),
                "net_value": _number(row.get("valor_liquido_pedido")),
                "ordered_quantity": _number(row.get("quantidade_pedida")),
                "billed_quantity": Decimal("0"),
                "billed_revenue": Decimal("0"),
                "status": row.get("situacao_pedido") or "Outro",
                "status_summary": row.get("situacao_resumida_pedido") or "Aberto",
                "payment_condition": row.get("descricao_condicao_pagamento_pedido") or "Não informado",
                "representative": row.get("representante_completo_pedido") or "Não informado",
                "products": set(),
            }
        order = orders[order_key]
        order["billed_quantity"] += _number(row.get("quantidade_faturada"))
        order["billed_revenue"] += _number(row.get("valor_bruto_faturado"))
        if row.get("codigo_produto") is not None:
            order["products"].add(str(row["codigo_produto"]))
    return orders


def _build_product_map(rows):
    products = {}
    for row in rows:
        code = str(row.get("codigo_produto") or "Não informado")
        if code not in products:
            products[code] = {
                "code": code,
                "name": row.get("descricao_produto") or f"Produto {code}",
                "family_code": str(row.get("familia_codigo") or ""),
                "family_name": row.get("familia_descricao") or "Não informada",
                "revenue": Decimal("0"),
                "quantity": Decimal("0"),
                "weight": Decimal("0"),
                "orders": set(),
                "first_purchase": None,
                "last_purchase": None,
            }
        product = products[code]
        product["revenue"] += _number(row.get("valor_bruto_faturado"))
        product["quantity"] += _number(row.get("quantidade_faturada"))
        product["weight"] += _number(row.get("peso_bruto_faturado"))
        if row.get("pedido") is not None:
            product["orders"].add(str(row["pedido"]))
        generation_date = _date_value(row.get("data_geracao"))
        if generation_date:
            if product["first_purchase"] is None or generation_date < product["first_purchase"]:
                product["first_purchase"] = generation_date
            if product["last_purchase"] is None or generation_date > product["last_purchase"]:
                product["last_purchase"] = generation_date
    return products


def _summarize_invoice_period(rows, start_date, end_date):
    selected = []
    for row in rows:
        generation_date = _date_value(row.get("data_geracao"))
        if generation_date and start_date <= generation_date <= end_date:
            selected.append(row)
    return {
        "revenue": sum((_number(row.get("valor_bruto_faturado")) for row in selected), Decimal("0")),
        "quantity": sum((_number(row.get("quantidade_faturada")) for row in selected), Decimal("0")),
        "weight": sum((_number(row.get("peso_bruto_faturado")) for row in selected), Decimal("0")),
        "orders": len({str(row.get("pedido")) for row in selected if row.get("pedido") is not None}),
        "invoices": len({str(row.get("nota_fiscal")) for row in selected if row.get("nota_fiscal") is not None}),
    }


def _summarize_order_period(orders, start_date, end_date):
    selected = [order for order in orders.values() if order["issue_date"] and start_date <= order["issue_date"] <= end_date]
    return {
        "orders": len(selected),
        "value": sum((order["value"] for order in selected), Decimal("0")),
        "quantity": sum((order["ordered_quantity"] for order in selected), Decimal("0")),
        "average_ticket": (
            sum((order["value"] for order in selected), Decimal("0")) / len(selected)
            if selected
            else Decimal("0")
        ),
    }


def _comparison_block(current, previous, value_keys):
    block = {}
    for key in value_keys:
        block[key] = {
            "current": float(_number(current[key])),
            "previous": float(_number(previous[key])),
            "change_pct": _percent_change(current[key], previous[key]),
        }
    return block


def _event_summary(items, start_date, end_date, value_fields):
    selected = []
    for item in items:
        event_date = date.fromisoformat(item["date"]) if item.get("date") else None
        if event_date and start_date <= event_date <= end_date:
            selected.append(item)
    summary = {"count": len(selected)}
    for field in value_fields:
        summary[field] = sum((_number(item.get(field)) for item in selected), Decimal("0"))
    return summary


def _operational_comparisons(operational, current_start, current_end, previous_start, previous_end):
    definitions = {
        "complaints": (operational["complaints"]["items"], ()),
        "returns": (operational["returns"]["items"], ("value",)),
        "cliches": (operational["cliches"]["items"], ("value", "area")),
    }
    comparisons = {}
    for name, (items, fields) in definitions.items():
        current = _event_summary(items, current_start, current_end, fields)
        previous = _event_summary(items, previous_start, previous_end, fields)
        if name == "complaints":
            current_items = [item for item in items if item.get("date") and current_start <= date.fromisoformat(item["date"]) <= current_end and item.get("billed_volume")]
            previous_items = [item for item in items if item.get("date") and previous_start <= date.fromisoformat(item["date"]) <= previous_end and item.get("billed_volume")]
            current["average_incidence_pct"] = sum((_number(item["incidence_pct"]) for item in current_items), Decimal("0")) / len(current_items) if current_items else Decimal("0")
            previous["average_incidence_pct"] = sum((_number(item["incidence_pct"]) for item in previous_items), Decimal("0")) / len(previous_items) if previous_items else Decimal("0")
            fields = ("average_incidence_pct",)
        comparisons[name] = _comparison_block(current, previous, ("count",) + fields)
    return comparisons


def _build_intelligence(rows, dashboard):
    dated_rows = [row for row in rows if _date_value(row.get("data_geracao"))]
    if not dated_rows:
        return None

    period_start = min(_date_value(row["data_geracao"]) for row in dated_rows)
    period_end = max(_date_value(row["data_geracao"]) for row in dated_rows)
    orders = _build_order_map(rows)
    products = _build_product_map(rows)
    operational = dashboard["operational"]
    total_revenue = sum((_number(row.get("valor_bruto_faturado")) for row in rows), Decimal("0"))
    total_quantity = sum((_number(row.get("quantidade_faturada")) for row in rows), Decimal("0"))

    active_months = sorted({_date_value(row["data_geracao"]).replace(day=1) for row in dated_rows})
    longest_streak = 0
    current_streak = 0
    previous_index = None
    for month in active_months:
        month_index = _month_index(month)
        current_streak = current_streak + 1 if previous_index is not None and month_index == previous_index + 1 else 1
        longest_streak = max(longest_streak, current_streak)
        previous_index = month_index

    analysis_year = period_end.year
    cutoff_month = period_end.month
    current_ytd_start = date(analysis_year, 1, 1)
    previous_ytd_start = date(analysis_year - 1, 1, 1)
    previous_ytd_end = date(analysis_year - 1, cutoff_month, min(period_end.day, 28 if cutoff_month == 2 else 30 if cutoff_month in (4, 6, 9, 11) else 31))
    current_ytd = _summarize_invoice_period(rows, current_ytd_start, period_end)
    previous_ytd = _summarize_invoice_period(rows, previous_ytd_start, previous_ytd_end)

    rolling_current_start = _shift_month(period_end.replace(day=1), -11)
    rolling_previous_end = rolling_current_start - date.resolution
    rolling_previous_start = _shift_month(rolling_current_start, -12)
    rolling_current = _summarize_invoice_period(rows, rolling_current_start, period_end)
    rolling_previous = _summarize_invoice_period(rows, rolling_previous_start, rolling_previous_end)

    current_order_ytd = _summarize_order_period(orders, current_ytd_start, period_end)
    previous_order_ytd = _summarize_order_period(orders, previous_ytd_start, previous_ytd_end)
    operational_ytd = _operational_comparisons(
        operational,
        current_ytd_start,
        period_end,
        previous_ytd_start,
        previous_ytd_end,
    )

    ytd = _comparison_block(current_ytd, previous_ytd, ("revenue", "quantity", "weight", "orders", "invoices"))
    rolling = _comparison_block(rolling_current, rolling_previous, ("revenue", "quantity", "weight", "orders"))
    order_entry = _comparison_block(current_order_ytd, previous_order_ytd, ("orders", "value", "quantity", "average_ticket"))

    product_rows = []
    for product in sorted(products.values(), key=lambda item: item["revenue"], reverse=True):
        share = float(product["revenue"] / total_revenue * 100) if total_revenue else 0.0
        product_rows.append(
            {
                "code": product["code"],
                "name": product["name"],
                "family_code": product["family_code"],
                "family_name": product["family_name"],
                "revenue": float(product["revenue"]),
                "revenue_display": _format_money(product["revenue"]),
                "quantity": float(product["quantity"]),
                "weight": float(product["weight"]),
                "orders": len(product["orders"]),
                "share_pct": round(share, 1),
                "first_purchase": _iso_date(product["first_purchase"]),
                "last_purchase": _iso_date(product["last_purchase"]),
            }
        )
    top_two_share = round(sum(item["share_pct"] for item in product_rows[:2]), 1)
    top_four_share = round(sum(item["share_pct"] for item in product_rows[:4]), 1)
    newest_product = max(product_rows, key=lambda item: item["first_purchase"] or "", default=None)

    open_orders = []
    for order in sorted(orders.values(), key=lambda item: item["forecast_date"] or date.max):
        if order["status_summary"] != "Aberto" or order["status"] == "Suspenso":
            continue
        balance = max(order["ordered_quantity"] - order["billed_quantity"], Decimal("0"))
        service_pct = float(order["billed_quantity"] / order["ordered_quantity"] * 100) if order["ordered_quantity"] else 0.0
        open_orders.append(
            {
                "number": order["number"],
                "status": order["status"],
                "issue_date": _iso_date(order["issue_date"]),
                "issue_date_display": _display_date(order["issue_date"]),
                "forecast_date": _iso_date(order["forecast_date"]),
                "forecast_date_display": _display_date(order["forecast_date"]),
                "value": float(order["value"]),
                "value_display": _format_money(order["value"]),
                "ordered_quantity": float(order["ordered_quantity"]),
                "billed_quantity": float(order["billed_quantity"]),
                "balance_quantity": float(balance),
                "balance_display": _format_number(balance),
                "service_pct": round(service_pct, 1),
                "products": sorted(order["products"]),
            }
        )

    open_orders.sort(
        key=lambda item: (item["value"], item["forecast_date"] or ""),
        reverse=True,
    )

    status_counts = defaultdict(int)
    payment_counts = defaultdict(int)
    for order in orders.values():
        status_counts[order["status"]] += 1
        payment_counts[order["payment_condition"]] += 1
    status_distribution = [
        {"label": label, "count": count, "share_pct": round(count / len(orders) * 100, 1) if orders else 0.0}
        for label, count in sorted(status_counts.items(), key=lambda item: item[1], reverse=True)
    ]
    payment_distribution = [
        {"label": label, "count": count, "share_pct": round(count / len(orders) * 100, 1) if orders else 0.0}
        for label, count in sorted(payment_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    monthly = defaultdict(lambda: {"revenue": Decimal("0"), "quantity": Decimal("0"), "orders": set()})
    for row in dated_rows:
        month = _date_value(row["data_geracao"]).replace(day=1)
        monthly[month]["revenue"] += _number(row.get("valor_bruto_faturado"))
        monthly[month]["quantity"] += _number(row.get("quantidade_faturada"))
        if row.get("pedido") is not None:
            monthly[month]["orders"].add(str(row["pedido"]))
    monthly_series = [
        {
            "month": month.isoformat(),
            "label": month.strftime("%m/%Y"),
            "revenue": float(values["revenue"]),
            "quantity": float(values["quantity"]),
            "orders": len(values["orders"]),
            "value_per_unit": float(values["revenue"] / values["quantity"]) if values["quantity"] else 0.0,
        }
        for month, values in sorted(monthly.items())
    ]

    revenue_growth = ytd["revenue"]["change_pct"]
    quantity_growth = ytd["quantity"]["change_pct"]
    growth_gap = round(revenue_growth - quantity_growth, 1) if revenue_growth is not None and quantity_growth is not None else None
    if longest_streak >= 12 and revenue_growth is not None and revenue_growth >= 10:
        classification = "Estratégico, recorrente e em expansão"
    elif longest_streak >= 12 and revenue_growth is not None and revenue_growth < 0:
        classification = "Estratégico e recorrente, com atenção à retração"
    elif longest_streak >= 12:
        classification = "Recorrente e estável"
    else:
        classification = "Relacionamento em desenvolvimento"

    cards = [
        {
            "type": "classification",
            "tone": "green",
            "eyebrow": "Classificação sugerida",
            "title": classification,
            "summary": f"O cliente registrou faturamento em {len(active_months)} meses, com maior sequência de {longest_streak} meses consecutivos.",
            "evidence": [f"{len(active_months)} meses com faturamento", f"{len(orders)} pedidos únicos", _format_money(total_revenue)],
        },
        {
            "type": "opportunity",
            "tone": "blue",
            "eyebrow": "Principal oportunidade",
            "title": "Expandir o mix sem perder os produtos âncora",
            "summary": f"Os dois principais produtos concentram {_format_number(top_two_share)}% da receita. Há espaço para desenvolver itens complementares da mesma família.",
            "evidence": [f"Top 2: {_format_number(top_two_share)}%", f"Top 4: {_format_number(top_four_share)}%", f"{len(products)} SKUs no histórico"],
        },
        {
            "type": "attention",
            "tone": "amber" if growth_gap is None or growth_gap < 15 else "red",
            "eyebrow": "Principal atenção",
            "title": "Separar crescimento financeiro de crescimento físico",
            "summary": (
                f"No YTD, a receita variou {_format_percent(revenue_growth)} e a quantidade {_format_percent(quantity_growth)}. "
                f"A diferença entre os indicadores é {_format_percent(growth_gap)}."
            ),
            "evidence": [f"Receita YTD: {_format_percent(revenue_growth)}", f"Quantidade YTD: {_format_percent(quantity_growth)}", f"Gap: {_format_percent(growth_gap)}"],
        },
        {
            "type": "pipeline",
            "tone": "purple",
            "eyebrow": "Carteira atual",
            "title": f"{len(open_orders)} pedido(s) em aberto",
            "summary": f"O saldo físico identificado é de {_format_number(sum(item['balance_quantity'] for item in open_orders))} unidades e requer acompanhamento até a previsão de entrega.",
            "evidence": [f"Valor aberto: {_format_money(sum((_number(item['value']) for item in open_orders), Decimal('0')))}", f"Saldo: {_format_number(sum(item['balance_quantity'] for item in open_orders))} unidades"],
        },
    ]
    complaint_metrics = operational["complaints"]
    return_metrics = operational["returns"]
    cliche_metrics = operational["cliches"]
    complaint_volume_evidence = [
        f"{item['unit']}: {item['nonconforming_volume_display']}"
        for item in complaint_metrics["volume_by_unit"][:3]
    ]
    cards.extend(
        [
            {
                "type": "complaints",
                "tone": "red" if complaint_metrics["severe_count"] else "amber",
                "eyebrow": "Qualidade e reclamações",
                "title": f"{complaint_metrics['count']} reclamação(ões), {complaint_metrics['severe_count']} grave(s)",
                "summary": f"A incidência média por reclamação é de {_format_number(complaint_metrics['incidence_pct'], 2)}%, preservando separadamente as diferentes unidades de medida.",
                "evidence": complaint_volume_evidence + [
                    f"Graves: {_format_number(complaint_metrics['severe_share_pct'])}%",
                    f"YTD: {_format_percent(operational_ytd['complaints']['count']['change_pct'])}",
                ],
            },
            {
                "type": "returns",
                "tone": "red" if return_metrics["revenue_share_pct"] >= 1 else "amber",
                "eyebrow": "Devoluções",
                "title": f"{return_metrics['count']} devolução(ões) · {return_metrics['total_value_display']}",
                "summary": f"O total devolvido equivale a {_format_number(return_metrics['revenue_share_pct'], 2)}% do faturamento histórico elegível deste cliente.",
                "evidence": [
                    f"Valor total: {return_metrics['total_value_display']}",
                    f"YTD ocorrências: {_format_percent(operational_ytd['returns']['count']['change_pct'])}",
                    f"YTD valor: {_format_percent(operational_ytd['returns']['value']['change_pct'])}",
                ],
            },
            {
                "type": "cliches",
                "tone": "blue",
                "eyebrow": "Clichês e trocas",
                "title": f"{cliche_metrics['count']} lançamento(s) · {cliche_metrics['total_value_display']}",
                "summary": f"Os lançamentos somam {cliche_metrics['total_area_display']} de área, com {cliche_metrics['customer_cost_value_display']} atribuídos ao cliente.",
                "evidence": [
                    f"Custo Maxiplast: {cliche_metrics['maxiplast_cost_value_display']}",
                    f"Custo cliente: {cliche_metrics['customer_cost_value_display']}",
                    f"YTD lançamentos: {_format_percent(operational_ytd['cliches']['count']['change_pct'])}",
                ],
            },
        ]
    )

    actions = []
    if complaint_metrics["severe_count"]:
        top_problem = complaint_metrics["problem_distribution"][0]["label"] if complaint_metrics["problem_distribution"] else "não informado"
        actions.append({"title": "Tratar reclamações graves", "detail": f"Revisar causas, contenção e recorrência das reclamações graves, começando por {top_problem}.", "evidence": f"{complaint_metrics['severe_count']} ocorrência(s) grave(s)."})
    if return_metrics["count"]:
        top_sector = return_metrics["sector_distribution"][0]["label"] if return_metrics["sector_distribution"] else "não informado"
        actions.append({"title": "Reduzir devoluções recorrentes", "detail": f"Construir plano conjunto com o setor {top_sector} e acompanhar valor devolvido por causa.", "evidence": f"{return_metrics['count']} devolução(ões), total de {return_metrics['total_value_display']}."})
    if cliche_metrics["maxiplast_cost_value"]:
        actions.append({"title": "Controlar custos de clichês", "detail": "Revisar os lançamentos custeados pela Maxiplast e separar trocas comerciais de correções operacionais.", "evidence": f"Custo Maxiplast de {cliche_metrics['maxiplast_cost_value_display']}."})
    if open_orders:
        order_numbers = ", ".join(str(item["number"]) for item in open_orders[:4])
        actions.append({"title": "Garantir execução da carteira aberta", "detail": f"Acompanhar os pedidos {order_numbers} até o faturamento completo.", "evidence": f"{len(open_orders)} pedido(s) aberto(s)."})
    if growth_gap is not None and abs(growth_gap) >= 10:
        actions.append({"title": "Monitorar volume separado da receita", "detail": "Acompanhar mensalmente quantidade e valor por unidade para separar consumo físico de preço e mix.", "evidence": f"Diferença YTD de {_format_percent(growth_gap)}."})
    if top_two_share >= 60:
        actions.append({"title": "Defender os produtos âncora", "detail": "Monitorar disponibilidade, recorrência e participação dos dois principais SKUs.", "evidence": f"Top 2 representam {_format_number(top_two_share)}% da receita."})
    if newest_product:
        actions.append({"title": "Expandir o portfólio", "detail": f"Usar o SKU {newest_product['code']} como ponto de conversa para novas aplicações e itens complementares.", "evidence": f"Primeira compra em {newest_product['first_purchase'] or 'data não informada'}."})
    if len(payment_distribution) > 1:
        exception = payment_distribution[1]
        actions.append({"title": "Validar exceções de pagamento", "detail": f"Revisar a utilização da condição {exception['label']} e confirmar se foi negociação pontual.", "evidence": f"{exception['count']} pedido(s), {_format_number(exception['share_pct'])}% do histórico."})

    for priority, action in enumerate(actions, start=1):
        action["priority"] = priority

    source_dates = [period_start, period_end]
    for source in operational.values():
        source_dates.extend(date.fromisoformat(item["date"]) for item in source["items"] if item.get("date"))
    source_period_start = min(source_dates)
    source_period_end = max(source_dates)

    metrics = {
        "analysis_year": analysis_year,
        "cutoff_month": cutoff_month,
        "period": {"start": source_period_start.isoformat(), "end": source_period_end.isoformat(), "rows": len(rows)},
        "recurrence": {"active_months": len(active_months), "longest_consecutive_months": longest_streak},
        "ytd": ytd,
        "rolling_12_months": rolling,
        "order_entry_ytd": order_entry,
        "growth_quality": {"revenue_pct": revenue_growth, "quantity_pct": quantity_growth, "gap_pct": growth_gap},
        "mix": {"products_count": len(products), "top_two_share_pct": top_two_share, "top_four_share_pct": top_four_share, "products": product_rows},
        "open_orders": {"count": len(open_orders), "items": open_orders, "total_value": round(sum(item["value"] for item in open_orders), 2), "balance_quantity": round(sum(item["balance_quantity"] for item in open_orders), 3)},
        "commercial_signals": {"status_distribution": status_distribution, "payment_distribution": payment_distribution},
        "operational": {
            "complaints": complaint_metrics,
            "returns": return_metrics,
            "cliches": cliche_metrics,
            "ytd_comparison": operational_ytd,
        },
        "monthly_series": monthly_series,
        "classification": classification,
        "actions": actions,
        "historical": {"revenue": float(total_revenue), "quantity": float(total_quantity), "orders": len(orders)},
    }
    return {"metrics": metrics, "cards": cards, "period_start": source_period_start, "period_end": source_period_end}


AI_RESPONSE_SCHEMA = {
    "name": "customer_commercial_insights",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "executive_summary": {"type": "string"},
            "classification": {"type": "string"},
            "principal_opportunity": {"type": "string"},
            "principal_attention": {"type": "string"},
            "insights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "enum": ["positive", "attention", "opportunity", "operational"]},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "recommended_action": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["type", "title", "summary", "evidence", "recommended_action", "confidence"],
                },
            },
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["executive_summary", "classification", "principal_opportunity", "principal_attention", "insights", "recommended_actions"],
    },
}


def _build_ai_payload(dashboard, intelligence, fingerprint):
    deterministic_metrics = json.loads(json.dumps(intelligence["metrics"], ensure_ascii=False))
    source_records = {}
    for source_name in ("complaints", "returns", "cliches"):
        source_records[source_name] = deterministic_metrics["operational"][source_name].pop("items", [])
    return {
        "schema_version": "2.0",
        "request_type": "customer_commercial_intelligence",
        "source_system": "ConnectMX / ERP Senior",
        "source_fingerprint": fingerprint,
        "customer": dashboard["customer"],
        "source_period": intelligence["metrics"]["period"],
        "deterministic_metrics": deterministic_metrics,
        "deterministic_insight_cards": intelligence["cards"],
        "source_records": source_records,
        "analysis_instructions": [
            "Use somente os fatos e métricas fornecidos.",
            "Não invente causas para variações, suspensões, preços ou comportamento do cliente.",
            "Diferencie crescimento de receita, quantidade e entrada de pedidos.",
            "Analise reclamações por gravidade, problema, máquina, volume não conforme e evolução anual.",
            "Compare devoluções com faturamento, pedidos, reclamações, problemas e setores responsáveis.",
            "Analise clichês por valor, área, responsável pelo custo e relação com pedidos, devoluções e reclamações.",
            "Aponte correlações como hipóteses somente quando os dados não comprovarem causalidade.",
            "Inclua evidências numéricas em cada insight.",
            "Priorize recomendações comerciais específicas e acionáveis.",
            "Responda em português do Brasil.",
        ],
        "response_format": {"type": "json_schema", "json_schema": AI_RESPONSE_SCHEMA},
    }


def prepare_customer_insights(customer_id):
    sources = _load_customer_sources(customer_id)
    rows = sources["sales"]
    dashboard = _build_dashboard(
        rows,
        customer_id,
        complaint_rows=sources["complaints"],
        return_rows=sources["returns"],
        cliche_rows=sources["cliches"],
    )
    if dashboard is None:
        return None
    intelligence = _build_intelligence(rows, dashboard)
    fingerprint = _source_fingerprint(sources)
    return {
        "dashboard": dashboard,
        "metrics": intelligence["metrics"],
        "cards": intelligence["cards"],
        "period_start": intelligence["period_start"],
        "period_end": intelligence["period_end"],
        "source_fingerprint": fingerprint,
        "source_row_count": sum(len(source_rows) for source_rows in sources.values()),
        "ai_payload": _build_ai_payload(dashboard, intelligence, fingerprint),
    }


def load_customer_dna(customer_id):
    sources = _load_customer_sources(customer_id)
    return _build_dashboard(
        sources["sales"],
        customer_id,
        complaint_rows=sources["complaints"],
        return_rows=sources["returns"],
        cliche_rows=sources["cliches"],
    )
